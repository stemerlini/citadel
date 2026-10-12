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


def test_unedited_topics_follow_new_defaults(tmp_path, monkeypatch):
    from citadel import topics

    old = tmp_path / "old.toml"
    old.write_text('["optics"]\ndescription = "old"\n')
    new = tmp_path / "new.toml"
    new.write_text('["optics"]\ndescription = "new"\n["nuclear"]\n')
    library = Library(tmp_path / "Citadel")

    monkeypatch.setattr(topics, "DEFAULT_FILE", old)
    library.init()
    monkeypatch.setattr(topics, "DEFAULT_FILE", new)
    report = library.init()
    assert report.topics_file_updated and "nuclear" in library.topics_file.read_text()
    assert (library.root / "nuclear").is_dir()


def test_edited_topics_are_left_alone(tmp_path, monkeypatch):
    from citadel import topics

    old = tmp_path / "old.toml"
    old.write_text('["optics"]\n')
    new = tmp_path / "new.toml"
    new.write_text('["nuclear"]\n')
    library = Library(tmp_path / "Citadel")
    monkeypatch.setattr(topics, "DEFAULT_FILE", old)
    library.init()
    library.topics_file.write_text('["optics"]\n["mine"]\n')
    monkeypatch.setattr(topics, "DEFAULT_FILE", new)
    report = library.init()
    assert report.topics_file_customised and not report.topics_file_updated
    assert "mine" in library.topics_file.read_text()
