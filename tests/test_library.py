from citadel.library import ATTACHMENTS, INBOX, REVIEW, Library


def test_init_creates_layout(tmp_path):
    library = Library(tmp_path / "Citadel")
    report = library.init()
    assert report.topics_file_created
    for name in (INBOX, REVIEW, ATTACHMENTS, "plasma-physics/hedp/icf", "optics/aero-optics"):
        assert (library.root / name).is_dir()
    assert library.check() == []


def test_init_is_idempotent_and_keeps_edits(tmp_path):
    library = Library(tmp_path / "Citadel")
    library.init()
    library.topics_file.write_text('["mine"]\ndescription = "my topic"\n')
    report = library.init()
    assert not report.topics_file_created
    assert report.created == ["mine"]
    assert [t.path for t in library.topics()] == ["mine"]


def test_init_never_removes_folders(tmp_path):
    library = Library(tmp_path / "Citadel")
    library.init()
    (library.root / "optics" / "keep.pdf").write_bytes(b"%PDF")
    library.topics_file.write_text('["other"]\n')
    library.init()
    assert (library.root / "optics" / "keep.pdf").exists()


def test_check_reports_problems(tmp_path):
    library = Library(tmp_path / "Citadel")
    assert "does not exist" in library.check()[0]
    library.init()
    (library.root / INBOX).rmdir()
    assert library.check() == [f"missing folder {INBOX}/ (run `citadel init`)"]
    library.topics_file.write_text('["Bad Name"]\n')
    assert "kebab-case" in library.check()[0]
