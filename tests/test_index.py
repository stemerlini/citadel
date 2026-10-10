import os

import pytest

from citadel import record
from citadel.index import Index, fts_query, scan
from conftest import file_ref, make_ref


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    file_ref(root, make_ref(), "The Kolmogorov cascade and Alfvén waves in plasmas.")
    file_ref(
        root,
        make_ref(
            citekey="harding2009observation",
            title="Observation of Kelvin-Helmholtz instability",
            authors=["Harding, E. C."],
            year=2009,
            topic="plasma-physics/instabilities",
            tags=["omega"],
            doi="10.1103/PhysRevLett.103.045005",
            abstract="",
            type="article",
        ),
        pdf=False,
    )
    file_ref(
        root,
        make_ref(
            citekey="nrl2004formulary",
            title="NRL Plasma Formulary",
            authors=["NRL"],
            year=None,
            topic="plasma-physics",
            type="book",
            tags=[],
            abstract="",
            doi="",
        ),
    )
    return root


@pytest.fixture
def index(tmp_path, library):
    index = Index(tmp_path / "index.db", library)
    yield index
    index.close()


def test_scan_skips_special_and_figures(library):
    (library / "_inbox" / "x").mkdir(parents=True)
    (library / "_inbox" / "x" / "x.md").write_text("---\n")
    found = sorted(p.parent.name for p in scan(library))
    assert len(found) == 3 and "x" not in found


def test_reindex_and_list(index):
    report = index.reindex()
    assert sorted(report.added) == [
        "harding2009observation",
        "nrl2004formulary",
        "verma2004statistical",
    ]
    assert report.problems == []
    keys = [r["citekey"] for r in index.list()]
    assert keys == ["harding2009observation", "verma2004statistical", "nrl2004formulary"]
    assert [r["citekey"] for r in index.list(topic="plasma-physics/instabilities")] == [
        "harding2009observation"
    ]
    assert len(index.list(topic="plasma-physics")) == 3  # includes subtopics
    assert [r["citekey"] for r in index.list(tag="mhd")] == ["verma2004statistical"]
    assert [r["citekey"] for r in index.list(type="book")] == ["nrl2004formulary"]
    assert [r["citekey"] for r in index.list(sort="author")][0] == "harding2009observation"


def test_get(index):
    index.reindex()
    row = index.get("harding2009observation")
    assert row["has_pdf"] == 0 and row["tags"] == ["omega"]
    assert row["data"]["doi"] == "10.1103/PhysRevLett.103.045005"
    assert index.get("nope") is None
    assert index.find_doi("10.1103/physrevlett.103.045005") == "harding2009observation"


def test_search_ranks_and_handles_diacritics(index):
    index.reindex()
    assert [r["citekey"] for r in index.search("kelvin helm")] == ["harding2009observation"]
    hits = index.search("alfven")
    assert [r["citekey"] for r in hits] == ["verma2004statistical"]
    assert "[Alfvén]" in hits[0]["snippet"]
    assert index.search('"; DROP TABLE refs; --') == []
    assert index.search("   ") == []


def test_incremental_reindex(index, library):
    index.reindex()
    assert index.reindex().unchanged == 3

    path = next(p for p in scan(library) if p.parent.name.endswith("Harding"))
    ref, _ = record.read(path)
    ref.status = "read"
    record.update_metadata(path, ref)
    os.utime(path, (1, 1))  # make sure the mtime differs on coarse filesystems
    report = index.reindex()
    assert report.updated == ["harding2009observation"] and report.unchanged == 2
    assert index.get("harding2009observation")["status"] == "read"


def test_removed_and_moved(index, library):
    index.reindex()
    nrl = next(p for p in scan(library) if "Formulary" in p.name).parent
    for f in nrl.iterdir():
        f.unlink()
    nrl.rmdir()
    verma = next(p for p in scan(library) if "Verma" in p.name).parent
    target = library / "plasma-physics" / "mhd" / verma.name
    target.parent.mkdir(parents=True)
    verma.rename(target)

    report = index.reindex()
    assert report.removed == ["nrl2004formulary"]
    assert index.get("verma2004statistical")["topic"] == "plasma-physics/mhd"
    assert "frontmatter topic is" in report.problems[0]


def test_duplicate_citekeys_and_bad_files_are_reported(index, library):
    file_ref(library, make_ref(title="Another paper", topic="optics"))
    bad = library / "optics" / "broken"
    bad.mkdir(parents=True)
    (bad / "broken.md").write_text("not a record")
    report = index.reindex()
    assert any("already used" in p for p in report.problems)
    assert any("broken: no YAML frontmatter" in p for p in report.problems)
    assert len(report.added) == 3


def test_citekey_edited_by_hand(index, library):
    index.reindex()
    path = next(p for p in scan(library) if "Verma" in p.name)
    ref, _ = record.read(path)
    ref.citekey = "verma2004mhd"
    record.update_metadata(path, ref)
    os.utime(path, (2, 2))
    index.reindex()
    assert index.get("verma2004statistical") is None
    assert index.get("verma2004mhd") is not None


def test_stale_schema_is_rebuilt(tmp_path, library):
    file = tmp_path / "index.db"
    index = Index(file, library)
    index.conn.execute("PRAGMA user_version = 99")
    index.close()
    index = Index(file, library)
    assert index.reindex().added
    index.close()


def test_fts_query():
    assert fts_query("kelvin helm") == '"kelvin" "helm"*'
    assert fts_query("") == ""


def test_unreachable_library_keeps_the_index(tmp_path, library):
    index = Index(tmp_path / "index.db", library)
    index.reindex()
    library.rename(tmp_path / "unmounted")
    report = index.reindex()
    assert "not reachable" in report.problems[0] and report.removed == []
    assert len(index.list()) == 3
    index.close()
