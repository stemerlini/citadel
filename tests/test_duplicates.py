import pytest

from citadel import duplicates
from citadel.config import Config
from citadel.index import Index
from citadel.ingest import Ingester, Proposal
from citadel.library import Library
from conftest import file_ref, make_ref


@pytest.fixture
def setup(tmp_path, monkeypatch):
    library = Library(tmp_path / "lib")
    library.init()
    index = Index(tmp_path / "index.db", library.root)
    yield library, index
    index.close()


def ref_with(library, **kw):
    return file_ref(library.root, make_ref(**kw))


def test_no_duplicates(setup):
    library, index = setup
    ref_with(library)
    ref_with(
        library,
        citekey="other2020x",
        title="Something else entirely here",
        doi="10.1/b",
        pdf_sha256="b" * 64,
    )
    index.reindex()
    assert duplicates.find(index, library) == []


def test_groups_by_hash_doi_and_title(setup):
    library, index = setup
    ref_with(library, pdf_sha256="a" * 64)
    ref_with(
        library,
        citekey="copy2004x",
        topic="optics",
        pdf_sha256="a" * 64,
        doi="",
        title="A copy with another title",
    )
    ref_with(
        library, citekey="preprint2004y", topic="plasma-physics/mhd", doi="", pdf_sha256="c" * 64
    )  # same title, year, author as verma2004statistical
    index.reindex()
    [group] = duplicates.find(index, library)
    assert {m.key for m in group.members} == {"verma2004statistical", "copy2004x", "preprint2004y"}
    assert "identical file" in group.reasons and "same title, year and author" in group.reasons


def test_review_items_join_groups(setup):
    library, index = setup
    ref_with(library, pdf_sha256="a" * 64)
    index.reindex()
    (library.review / "again.pdf").write_bytes(b"%PDF")
    Proposal(
        "again.pdf",
        "identical to verma2004statistical",
        "a" * 64,
        duplicate_of="verma2004statistical",
    ).save(library.review)
    [group] = duplicates.find(index, library)
    assert [m.kind for m in group.members] == ["reference", "review"]  # filed first


def test_keep_a_reference_deletes_the_others(setup):
    library, index = setup
    first = ref_with(library, pdf_sha256="a" * 64)
    second = ref_with(
        library, citekey="copy2004x", topic="optics", pdf_sha256="a" * 64, doi="", title="A copy"
    )
    index.reindex()
    (library.review / "again.pdf").write_bytes(b"%PDF")
    Proposal("again.pdf", "identical", "a" * 64, duplicate_of="verma2004statistical").save(
        library.review
    )
    [group] = duplicates.find(index, library)
    ingester = Ingester(library, index, None, Config())
    deleted = duplicates.keep(index, [ingester], group, "reference:copy2004x")
    assert sorted(deleted) == [
        "_review/again.pdf",
        "plasma-physics/turbulence-transport/" + first.name,
    ]
    assert not first.exists() and second.exists()
    assert not (library.review / "again.pdf.citadel.json").exists()
    assert [r["citekey"] for r in index.list()] == ["copy2004x"]


def test_keep_a_review_item_files_it(setup, monkeypatch):
    library, index = setup
    first = ref_with(library, pdf_sha256="a" * 64)
    index.reindex()
    (library.review / "better.pdf").write_bytes(b"%PDF better")
    import hashlib

    digest = hashlib.sha256(b"%PDF better").hexdigest()
    Proposal(
        "better.pdf",
        "same DOI as verma2004statistical",
        digest,
        metadata={
            **make_ref().__dict__["extra"],
            "title": "Statistical theory of magnetohydrodynamic turbulence: recent results",
            "authors": ["Verma, M."],
            "year": 2004,
            "doi": "10.1016/j.physrep.2004.07.007",
        },
        topic="optics",
        duplicate_of="verma2004statistical",
    ).save(library.review)
    [group] = duplicates.find(index, library)
    ingester = Ingester(library, index, None, Config())
    duplicates.keep(index, [ingester], group, "review:better.pdf")
    assert not first.exists()
    [row] = index.list()
    assert row["topic"] == "optics" and row["has_pdf"]


def test_ignored_groups_stay_hidden(setup):
    library, index = setup
    ref_with(library, pdf_sha256="a" * 64)
    ref_with(
        library, citekey="copy2004x", topic="optics", pdf_sha256="a" * 64, doi="", title="Another"
    )
    index.reindex()
    [group] = duplicates.find(index, library)
    duplicates.ignore(library, group)
    assert duplicates.find(index, library) == []


def test_notes_are_reported(setup):
    library, index = setup
    folder = ref_with(library, pdf_sha256="a" * 64)
    md = folder / f"{folder.name}.md"
    md.write_text(md.read_text() + "Important notes.\n")
    ref_with(
        library, citekey="copy2004x", topic="optics", pdf_sha256="a" * 64, doi="", title="Another"
    )
    index.reindex()
    [group] = duplicates.find(index, library)
    notes = {m.key: m.has_notes for m in group.members}
    assert notes == {"verma2004statistical": True, "copy2004x": False}
    assert group.members[0].key == "verma2004statistical"  # the copy with notes first
