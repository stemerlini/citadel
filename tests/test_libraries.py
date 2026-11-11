from pathlib import Path

from citadel import ingest as ingest_module
from citadel import sources
from citadel.config import Config
from citadel.identify import Identification
from citadel.index import Index
from citadel.ingest import Ingester
from citadel.library import Library
from citadel.pdf import PdfText
from conftest import file_ref, make_ref


def test_libraries_from_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    legacy = Config.from_dict({"library": "/home/me/OneDrive-Personal/Citadel"})
    [only] = legacy.libraries()
    assert (only.name, only.root) == ("Personal", Path("/home/me/OneDrive-Personal/Citadel"))

    (tmp_path / "p").mkdir()
    config = Config.from_dict(
        {
            "library": "/ignored",
            "libraries": [
                {"name": "Personal", "path": str(tmp_path / "p")},
                {"name": "SES", "path": str(tmp_path / "ses")},  # not mounted yet
            ],
        }
    )
    assert [(lib.name, lib.tag) for lib in config.libraries()] == [
        ("Personal", "p"),
        ("SES", "s"),
    ]
    assert [lib.name for lib in config.available_libraries()] == ["Personal"]
    assert config.library_path == tmp_path / "p"


def test_one_index_over_libraries_keeps_citekeys_unique(tmp_path):
    personal, ses = Library(tmp_path / "p", "Personal"), Library(tmp_path / "s", "SES")
    file_ref(personal.root, make_ref())
    file_ref(ses.root, make_ref())  # the same citekey in the other library
    file_ref(ses.root, make_ref(citekey="harding2009observation", title="Kelvin-Helmholtz"))
    index = Index(tmp_path / "index.db", [personal, ses])

    report = index.reindex()
    assert any("verma2004statistical-ses" in p for p in report.problems)
    assert index.get("verma2004statistical")["library"] == "Personal"
    clash = index.get("verma2004statistical-ses")
    assert clash["library"] == "SES" and index.path(clash).is_relative_to(ses.root)
    assert {r["citekey"] for r in index.list(library="SES")} == {
        "verma2004statistical-ses",
        "harding2009observation",
    }
    assert [r["library"] for r in index.search("kelvin")] == ["SES"]
    assert index.reindex().problems == report.problems  # stable from one run to the next
    index.close()


def test_inbox_files_stay_in_their_own_library(tmp_path, monkeypatch):
    personal, ses = Library(tmp_path / "p", "Personal"), Library(tmp_path / "s", "SES")
    personal.init()
    ses.init()
    index = Index(tmp_path / "index.db", [personal, ses])
    config = Config()
    config.ingest.settle_seconds = 0
    meta = sources.Metadata(
        title="A review of recent developments in schlieren and shadowgraph techniques",
        authors=["Settles, Gary S"],
        year=2017,
        doi="10.1088/1361-6501/aa5748",
        abstract="Schlieren and shadowgraphy and interferometry for plasma diagnostics.",
    )
    monkeypatch.setattr(
        ingest_module,
        "identify_text",
        lambda pdf, http: Identification(sources.Metadata(**meta.__dict__), 1.0, "doi", [], pdf),
    )
    monkeypatch.setattr(
        ingest_module,
        "read_pdf",
        lambda path: PdfText(path=path, pages=10, has_text=True, first_pages="schlieren " * 20),
    )
    monkeypatch.setattr(sources, "enrich", lambda http, meta: meta)

    for library in (personal, ses):  # the same paper dropped in both inboxes
        (library.inbox / "paper.pdf").write_bytes(b"%PDF-1.4 paper")
        [result] = Ingester(library, index, None, config).process_inbox()
        assert result.status == "filed" and result.library == library.name, result.reason
        assert (library.root / result.folder).is_dir()
    assert sorted(r["library"] for r in index.list()) == ["Personal", "SES"]
    index.close()


def test_init_leaves_an_unmounted_onedrive_alone(tmp_path, config_file, monkeypatch, capsys):
    import pytest

    from citadel.__main__ import main

    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    mountpoint = tmp_path / "OneDrive-SES"  # its mount folder, empty while not mounted
    mountpoint.mkdir()
    with pytest.raises(SystemExit, match="0"):
        main(["init", "--library", str(mountpoint / "Citadel")])
    assert "not mounted" in capsys.readouterr().out
    assert not any(mountpoint.iterdir())


def test_a_library_is_named_by_its_folder_and_followed_when_renamed(tmp_path, monkeypatch):
    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    (tmp_path / "OneDrive-Personal" / "Citadel-work").mkdir(parents=True)
    config = Config.from_dict(
        {
            "libraries": [
                # renamed to Citadel-personal on another computer
                {"path": str(tmp_path / "OneDrive-Personal" / "Citadel")},
                {"name": "ignored", "path": str(tmp_path / "OneDrive-Personal" / "Citadel-work")},
            ]
        }
    )
    (tmp_path / "OneDrive-Personal" / "Citadel-personal").mkdir()
    libraries = config.libraries()
    assert [(lib.name, lib.tag, lib.root.name) for lib in libraries] == [
        ("personal", "p", "Citadel-personal"),
        ("work", "w", "Citadel-work"),
    ]
