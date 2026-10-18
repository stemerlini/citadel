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

# What a review item holding GOOD is called: its name once filed.
REVIEW_NAME = "2017_ReviewRecentDevelopmentsSchlierenShadowgraph_Settles"

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
    proposal = json.loads((library.review / f"{REVIEW_NAME}.pdf{PROPOSAL_SUFFIX}").read_text())
    assert proposal["original_name"] == "paper.pdf"  # named after its metadata
    assert proposal["metadata"]["doi"] == GOOD.doi
    assert proposal["topic"] == "plasma-physics/diagnostics/refractive"
    assert (library.review / f"{REVIEW_NAME}.pdf").exists() and not (
        library.inbox / "paper.pdf"
    ).exists()


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
    # A different paper whose short name collides: "…: part 2" (the subtitle is dropped).
    state["meta"] = Metadata(
        **{**GOOD.__dict__, "doi": "10.1088/other", "title": GOOD.title + ": part 2"}
    )
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


def test_unconvertible_office_files_wait_in_review(setup, monkeypatch):
    ingester, library, _, _ = setup
    monkeypatch.setattr(ingest_module.office, "to_pdf", lambda path, folder: None)
    drop(library, "slides.pptx", b"PK")
    [result] = ingester.process_inbox()
    assert result.status == "review" and "could not convert" in result.reason
    assert (library.review / "slides.pptx").exists()


def test_other_files_wait_in_review(setup):
    ingester, library, _, _ = setup
    drop(library, "data.h5", b"HDF")
    [result] = ingester.process_inbox()
    assert result.status == "review" and ".h5 files are not handled" in result.reason


def fake_conversion(monkeypatch):
    def to_pdf(path, folder):
        pdf = folder / f"{path.stem}.pdf"
        pdf.write_bytes(b"%PDF converted " + path.read_bytes())
        return pdf

    monkeypatch.setattr(ingest_module.office, "to_pdf", to_pdf)


def test_office_documents_are_converted_and_kept(setup, monkeypatch):
    ingester, library, _, _ = setup
    fake_conversion(monkeypatch)
    source = drop(library, "report.docx", b"PK word")
    [result] = ingester.process_inbox()
    assert result.status == "filed" and result.file.endswith("report.docx")
    folder = library.root / result.folder
    assert (folder / f"{folder.name}.pdf").read_bytes() == b"%PDF converted PK word"
    assert (folder / f"{folder.name}.docx").read_bytes() == b"PK word"
    assert not source.exists()


def test_office_documents_in_review_show_the_original(setup, monkeypatch):
    ingester, library, _, state = setup
    fake_conversion(monkeypatch)
    state["confidence"] = 0.5
    drop(library, "report.docx", b"PK word")
    [result] = ingester.process_inbox()
    assert result.status == "review"
    [item] = ingester.review_items()
    assert item.original_name == "report.docx" and item.file == f"{REVIEW_NAME}.docx"
    assert (library.review / item.file).exists()
    accepted = ingester.accept("report.docx")  # the name it arrived with still works
    folder = library.root / accepted.folder
    assert (folder / f"{folder.name}.docx").exists() and (folder / f"{folder.name}.pdf").exists()
    assert not any(library.review.iterdir())


def test_annexes_are_attached_to_their_standard(setup, monkeypatch):
    ingester, library, index, state = setup
    state["meta"] = Metadata(
        title="ECSS-Q-ST-60-15C Rev.1: Radiation hardness assurance - EEE components",
        authors=["ECSS"],
        year=2025,
        type="standard",
    )
    drop(library, "standard.pdf", b"%PDF the standard")
    [standard] = ingester.process_inbox()
    assert standard.status == "filed"

    annex = drop(library, "ECSS-Q-ST-60-15C-Rev.1(20March2025)-Annex-A.docx", b"PK annex")
    monkeypatch.setattr(ingest_module.office, "title", lambda path: "ECSS-Q-ST-60-15C Rev.1")
    [result] = ingester.process_inbox()
    assert result.status == "filed" and result.reason == f"attached to {standard.citekey}"
    folder = library.root / standard.folder
    assert (folder / "attachments" / annex.name).read_bytes() == b"PK annex"
    ref, _ = ingest_module.record.read(folder / f"{folder.name}.md")
    assert ref.attachments == [annex.name]
    assert not annex.exists()


def test_a_lookalike_without_annex_words_is_not_attached(setup):
    ingester, library, _, state = setup
    state["meta"] = Metadata(
        title="ECSS-Q-ST-60-15C Rev.1: Radiation hardness",
        year=2025,
        authors=["ECSS"],
        type="standard",
    )
    drop(library, "standard.pdf", b"%PDF the standard")
    ingester.process_inbox()
    assert ingester.parent_of(library.inbox / "ECSS-Q-ST-60-15C-Rev.1 notes.pdf") is None


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
    assert item.file == f"{REVIEW_NAME}.pdf"  # the same name again, not "… (2).pdf"
    assert item.original_name == "paper.pdf"


def test_retry_puts_the_file_back_on_error(setup, monkeypatch):
    ingester, library, _, state = setup
    state["confidence"] = 0.5
    drop(library)
    ingester.process_inbox()
    before = (library.review / f"{REVIEW_NAME}.pdf").read_bytes()

    def broken(path, **kwargs):
        from citadel.ingest import Result

        return Result(str(path), "error", "boom")

    monkeypatch.setattr(ingester, "ingest", broken)
    [result] = ingester.retry()
    assert result.status == "error"
    assert (library.review / f"{REVIEW_NAME}.pdf").read_bytes() == before
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


def test_same_title_year_author_is_flagged_on_arrival(setup):
    ingester, library, _, state = setup
    drop(library, "a.pdf", b"%PDF journal")
    ingester.process_inbox()
    state["meta"] = Metadata(**{**GOOD.__dict__, "doi": ""})  # e.g. an author's own copy
    drop(library, "b.pdf", b"%PDF other bytes")
    [result] = ingester.process_inbox()
    assert result.status == "review"
    assert result.reason == "same title, year and author as settles2017review"


def test_metadata_can_be_corrected_before_filing(setup):
    ingester, library, index, state = setup
    state["confidence"] = 0.4
    drop(library, "slides.pdf", b"%PDF slides")
    ingester.process_inbox()
    proposal = ingester.edit_review(
        "slides.pdf",
        title="Detector physics: from particle interactions to tracking concepts",
        authors=["Bindi, Marcello"],
        year=2021,
        type="slides",
    )
    assert proposal.confidence == 1.0 and proposal.method == "manual"
    result = ingester.accept("slides.pdf", topic="optics")
    assert result.citekey == "bindi2021detector"
    assert result.folder == "optics/2021_DetectorPhysics_Bindi"
    assert index.get("bindi2021detector")["type"] == "slides"


def test_unknown_fields_are_refused(setup):
    ingester, library, _, state = setup
    state["confidence"] = 0.4
    drop(library)
    ingester.process_inbox()
    with pytest.raises(ValueError, match="cannot edit"):
        ingester.edit_review("paper.pdf", citekey="x")


def test_editing_a_review_item_renames_it(setup):
    ingester, library, _, state = setup
    state["confidence"] = 0.4
    drop(library, "main.pdf", b"%PDF main")
    ingester.process_inbox()
    proposal = ingester.edit_review(
        "main.pdf",
        title="Weightfield2: a fast simulator for silicon and diamond solid state detector",
        authors=["Cenna, Francesca"],
        year=2015,
    )
    assert proposal.file == "2015_Weightfield2FastSimulatorSiliconDiamondSolid_Cenna.pdf"
    assert proposal.original_name == "main.pdf"
    assert sorted(p.name for p in library.review.iterdir()) == [
        proposal.file,
        proposal.file + PROPOSAL_SUFFIX,
    ]


def test_retry_keeps_metadata_corrected_by_hand(setup):
    ingester, library, index, state = setup
    state["confidence"] = 0.4
    drop(library, "main.pdf", b"%PDF main")
    ingester.process_inbox()
    ingester.edit_review(
        "main.pdf", title="The right title of this paper", year=2015, authors=["Cenna, Francesca"]
    )
    state["meta"] = Metadata(title="A wrong match", authors=["Other, A."], year=1999)
    [result] = ingester.retry()
    assert result.file == "main.pdf" and result.status == "filed"
    assert result.title == "The right title of this paper"  # not the wrong match
    assert index.get(result.citekey)["year"] == 2015


def test_fetch_with_an_open_access_pdf(setup, monkeypatch):
    from citadel.graph import Graph, Work

    ingester, library, index, _ = setup

    def download(self, work, target):
        target.write_bytes(b"%PDF-1.4 open")
        return True

    monkeypatch.setattr(Graph, "download", download)
    monkeypatch.setattr(sources, "crossref", lambda http, doi: None)
    result = ingester.fetch(Work("W1", Metadata(**GOOD.__dict__)), fallback_topic="optics")
    assert result.status == "filed" and result.file == f"doi:{GOOD.doi}"
    folder = library.root / result.folder
    assert (folder / f"{folder.name}.pdf").read_bytes() == b"%PDF-1.4 open"


def test_fetch_without_pdf_uses_the_fallback_topic(setup, monkeypatch):
    from citadel.graph import Graph, Work

    ingester, library, index, _ = setup
    monkeypatch.setattr(Graph, "download", lambda self, work, target: False)
    monkeypatch.setattr(sources, "crossref", lambda http, doi: None)
    vague = Metadata(
        title="Something rather general about things",
        authors=["Doe, J."],
        year=1999,
        doi="10.1/vague",
    )
    result = ingester.fetch(Work("W2", vague), fallback_topic="optics/wave-optics")
    assert result.status == "filed" and result.reason == "metadata only (no open-access PDF)"
    assert result.folder.startswith("optics/wave-optics/")
    again = ingester.fetch(Work("W2", vague))
    assert again.status == "error" and "already in the library" in again.reason
    unclear = ingester.fetch(
        Work("W3", Metadata(title="Vague words only here", year=2000, authors=["Roe, R."]))
    )
    assert unclear.status == "error" and "topic is unclear" in unclear.reason


def test_a_pdf_for_a_reference_without_one_is_attached(setup):
    ingester, library, index, _ = setup
    ref = ingester.file_metadata_only(Metadata(**GOOD.__dict__), "optics", "refs:x")
    folder = library.root / ref.folder
    assert not (folder / f"{folder.name}.pdf").exists()

    source = drop(library, "downloaded.pdf", b"%PDF from the publisher")
    [result] = ingester.process_inbox()
    assert result.status == "filed" and result.reason == f"PDF added to {ref.citekey}"
    assert (folder / f"{folder.name}.pdf").read_bytes() == b"%PDF from the publisher"
    assert index.get(ref.citekey)["has_pdf"] == 1 and not source.exists()
    assert len(index.list()) == 1  # no second reference


def test_an_uncertain_match_asks_and_accept_attaches(setup):
    ingester, library, index, state = setup
    ref = ingester.file_metadata_only(Metadata(**GOOD.__dict__), "optics", "refs:x")
    state["confidence"] = 0.6
    drop(library, "maybe.pdf", b"%PDF maybe")
    [result] = ingester.process_inbox()
    assert result.status == "review" and "which has no PDF yet" in result.reason
    accepted = ingester.accept("maybe.pdf")
    assert accepted.reason == f"PDF added to {ref.citekey}"
    assert index.get(ref.citekey)["has_pdf"] == 1 and ingester.review_items() == []


def test_attach_checks_it_is_the_same_paper(setup, tmp_path):
    ingester, library, index, state = setup
    ref = ingester.file_metadata_only(Metadata(**GOOD.__dict__), "optics", "refs:x")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF the paper")
    state["meta"] = Metadata(title="Something else entirely", authors=["Doe, J."], year=2001)
    with pytest.raises(ValueError, match="does not look like"):
        ingester.attach_pdf(ref.citekey, pdf)
    state["meta"] = GOOD
    result = ingester.attach_pdf(ref.citekey, pdf)
    assert result.status == "filed" and pdf.exists()  # copied, not moved
    with pytest.raises(ValueError, match="already has a PDF"):
        ingester.attach_pdf(ref.citekey, pdf)
