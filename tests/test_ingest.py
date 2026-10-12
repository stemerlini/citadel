import json
import os
import time

import pytest

from citadel import ingest as ingest_module
from citadel import sources
from citadel.config import Config
from citadel.identify import Identification
from citadel.index import Index
from citadel.ingest import PROPOSAL_SUFFIX, Ingester
from citadel.library import Library
from citadel.pdf import PdfText
from citadel.sources import Metadata

GOOD = Metadata(
    title="A review of recent developments in schlieren and shadowgraph techniques",
    authors=["Settles, Gary S"],
    year=2017,
    doi="10.1088/1361-6501/aa5748",
    journal="Measurement Science and Technology",
    abstract="Schlieren and shadowgraphy and interferometry for plasma diagnostics.",
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    library = Library(tmp_path / "lib")
    library.init()
    index = Index(tmp_path / "index.db", library.root)
    config = Config()
    config.ingest.settle_seconds = 0
    ingester = Ingester(library, index, http=None, config=config)
    state = {"meta": GOOD, "confidence": 1.0}

    def fake_identify(pdf, http):
        meta = state["meta"]
        copy = Metadata(**meta.__dict__) if meta else None
        return Identification(copy, state["confidence"], "doi", ["fake"], pdf)

    monkeypatch.setattr(ingest_module, "identify_text", fake_identify)
    monkeypatch.setattr(
        ingest_module,
        "read_pdf",
        lambda path: PdfText(
            path=path, pages=10, has_text=True, first_pages="schlieren plasma " * 20
        ),
    )
    monkeypatch.setattr(sources, "enrich", lambda http, meta: meta)
    yield ingester, library, index, state
    index.close()


def drop(library, name="paper.pdf", content=b"%PDF-1.4 paper"):
    path = library.inbox / name
    path.write_bytes(content)
    return path


def test_confident_file_is_filed_and_indexed(setup):
    ingester, library, index, _ = setup
    source = drop(library)
    [result] = ingester.process_inbox()
    assert result.status == "filed", result.reason
    assert result.folder == (
        "plasma-physics/diagnostics/refractive/"
        "2017_ReviewRecentDevelopmentsSchlierenShadowgraph_Settles"
    )
    folder = library.root / result.folder
    assert (folder / f"{folder.name}.pdf").read_bytes() == b"%PDF-1.4 paper"
    assert (folder / f"{folder.name}.md").exists()
    assert not source.exists()
    row = index.get("settles2017review")
    assert row["has_pdf"] == 1 and row["data"]["source"] == "inbox"


def test_low_confidence_goes_to_review_with_a_proposal(setup):
    ingester, library, _, state = setup
    state["confidence"] = 0.5
    drop(library)
    [result] = ingester.process_inbox()
    assert result.status == "review" and "metadata confidence 0.50" in result.reason
    proposal = json.loads((library.review / f"paper.pdf{PROPOSAL_SUFFIX}").read_text())
    assert proposal["metadata"]["doi"] == GOOD.doi
    assert proposal["topic"] == "plasma-physics/diagnostics/refractive"
    assert (library.review / "paper.pdf").exists() and not (library.inbox / "paper.pdf").exists()


def test_accept_and_reject(setup):
    ingester, library, index, state = setup
    state["confidence"] = 0.5
    drop(library)
    drop(library, "other.pdf", b"%PDF other")
    ingester.process_inbox()

    result = ingester.accept("paper", topic="optics")
    assert result.status == "filed" and result.folder.startswith("optics/")
    assert not (library.review / f"paper.pdf{PROPOSAL_SUFFIX}").exists()
    assert index.get("settles2017review")["topic"] == "optics"

    result = ingester.reject("other")
    assert result.status == "kept"
    assert (library.attachments / "other.pdf").read_bytes() == b"%PDF other"
    assert ingester.review_items() == []


def test_exact_duplicates_are_caught_and_rejected_by_deleting(setup):
    ingester, library, _, _ = setup
    drop(library)
    ingester.process_inbox()
    drop(library, "again.pdf")
    [result] = ingester.process_inbox()
    assert result.status == "review" and result.reason == "identical to settles2017review"
    with pytest.raises(ValueError, match="reject it instead"):
        ingester.accept("again")
    assert ingester.reject("again").status == "deleted"
    assert not (library.review / "again.pdf").exists()


def test_same_doi_is_a_duplicate(setup):
    ingester, library, _, _ = setup
    drop(library)
    ingester.process_inbox()
    drop(library, "preprint.pdf", b"%PDF different bytes")
    [result] = ingester.process_inbox()
    assert result.status == "review" and "same DOI as settles2017review" in result.reason


def test_names_are_never_reused(setup):
    ingester, library, index, state = setup
    drop(library, "a.pdf", b"%PDF a")
    ingester.process_inbox()
    state["meta"] = Metadata(**{**GOOD.__dict__, "doi": "10.1088/other"})
    drop(library, "b.pdf", b"%PDF b")
    [result] = ingester.process_inbox()
    assert result.citekey == "settles2017reviewb"
    assert result.folder.endswith("_Settles-b")


def test_unsettled_hidden_and_temporary_files_wait(setup):
    ingester, library, _, _ = setup
    ingester.config.ingest.settle_seconds = 60
    fresh = drop(library, "fresh.pdf")
    old = drop(library, "old.pdf", b"%PDF old")
    past = time.time() - 600
    os.utime(old, (past, past))
    for name in (".hidden.pdf", "paper.pdf.crdownload", "~$lock.pdf"):
        path = drop(library, name)
        os.utime(path, (past, past))
    assert ingester.pending() == [old]
    assert fresh.exists()


def test_failed_copy_leaves_the_source_and_no_folder(setup, monkeypatch):
    ingester, library, _, _ = setup
    source = drop(library)

    def broken(src, dst, digest):
        raise OSError("disk full")

    monkeypatch.setattr(ingest_module, "_verified_copy", broken)
    [result] = ingester.process_inbox()
    assert result.status == "error" and "disk full" in result.reason
    assert source.exists()
    refractive = library.root / "plasma-physics/diagnostics/refractive"
    assert list(refractive.iterdir()) == []


def test_verified_copy_refuses_mismatch_and_overwrite(tmp_path):
    source = tmp_path / "a.pdf"
    source.write_bytes(b"abc")
    with pytest.raises(OSError, match="does not match"):
        ingest_module._verified_copy(source, tmp_path / "b.pdf", "0" * 64)
    assert not list(tmp_path.glob(".*part"))
    (tmp_path / "c.pdf").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        ingest_module._verified_copy(source, tmp_path / "c.pdf", "x")
    assert (tmp_path / "c.pdf").read_bytes() == b"keep"


def test_dry_run_changes_nothing(setup):
    ingester, library, _, _ = setup
    source = drop(library)
    [result] = ingester.process_inbox(dry_run=True)
    assert result.status == "dry-run" and result.reason == "would be filed"
    assert source.exists() and not any(library.review.iterdir())


def test_non_pdfs_wait_in_review(setup):
    ingester, library, _, _ = setup
    drop(library, "slides.pptx", b"PK")
    [result] = ingester.process_inbox()
    assert result.status == "review" and "only PDFs" in result.reason


def test_metadata_only_reference(setup):
    ingester, library, index, _ = setup
    ref = ingester.file_metadata_only(GOOD, "optics", "add")
    folder = library.root / ref.folder
    assert [p.suffix for p in folder.iterdir()] == [".md"]
    index.reindex()
    assert index.get(ref.citekey)["has_pdf"] == 0


def test_unknown_topic_is_refused(setup):
    ingester, _, _, _ = setup
    with pytest.raises(ValueError, match="unknown topic"):
        ingester.new_reference(GOOD, "not/a/topic", 1.0, "add")


def test_retry_files_items_that_now_pass(setup):
    ingester, library, index, state = setup
    state["confidence"] = 0.5
    drop(library)
    ingester.process_inbox()
    assert len(ingester.review_items()) == 1

    state["confidence"] = 1.0  # e.g. a better identifier
    [result] = ingester.retry()
    assert result.status == "filed" and result.file == "paper.pdf"
    assert ingester.review_items() == []
    assert not any(library.review.iterdir())
    assert index.get("settles2017review")["data"]["source"] == "inbox"


def test_retry_that_still_fails_stays_in_review(setup):
    ingester, library, _, state = setup
    state["confidence"] = 0.5
    drop(library)
    ingester.process_inbox()
    [result] = ingester.retry()
    assert result.status == "review"
    [item] = ingester.review_items()
    assert item.file == "paper.pdf"  # same name, not "paper (2).pdf"


def test_retry_puts_the_file_back_on_error(setup, monkeypatch):
    ingester, library, _, state = setup
    state["confidence"] = 0.5
    drop(library)
    ingester.process_inbox()
    before = (library.review / "paper.pdf").read_bytes()

    def broken(path, **kwargs):
        from citadel.ingest import Result

        return Result(str(path), "error", "boom")

    monkeypatch.setattr(ingester, "ingest", broken)
    [result] = ingester.retry()
    assert result.status == "error"
    assert (library.review / "paper.pdf").read_bytes() == before
    assert len(ingester.review_items()) == 1


def test_identical_files_in_one_batch_are_caught(setup):
    ingester, library, _, _ = setup
    drop(library, "a.pdf")
    drop(library, "a copy.pdf")  # same bytes
    first, second = ingester.process_inbox()
    assert first.status == "filed"
    assert second.status == "review" and second.reason == "identical to settles2017review"


def test_same_doi_in_one_batch_is_caught(setup):
    ingester, library, _, _ = setup
    drop(library, "a.pdf", b"%PDF published")
    drop(library, "b.pdf", b"%PDF preprint")  # other bytes, same metadata/DOI
    first, second = ingester.process_inbox()
    assert first.status == "filed"
    assert second.status == "review" and "same DOI" in second.reason
