from citadel import config as config_module
from citadel.config import Config


def test_missing_file_gives_defaults(tmp_path):
    config = Config.load(tmp_path / "nope.toml")
    assert config.ingest.review_threshold == 0.8
    assert config.vault_path is None


def test_template_round_trips(tmp_path):
    file = tmp_path / "config.toml"
    assert config_module.write_default(file, library=tmp_path / "lib")
    config = Config.load(file)
    assert config.library == str(tmp_path / "lib")
    assert config.obsidian.notes_folder == "Literature"
    assert config.convert.device == "cuda"


def test_write_default_never_overwrites(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text("# mine\n")
    assert not config_module.write_default(file)
    assert file.read_text() == "# mine\n"


def test_wrong_types_and_unknown_keys_are_ignored():
    config = Config.from_dict(
        {
            "library": "~/lib",
            "ingest": {"review_threshold": "high", "watch_interval": 10, "bogus": 1},
            "obsidian": {"vault": "~/Vault"},
            "unknown": {"x": 1},
        }
    )
    assert config.ingest.review_threshold == 0.8
    assert config.ingest.watch_interval == 10
    assert config.vault_path.name == "Vault" and config.vault_path.is_absolute()


def test_integer_threshold_is_accepted():
    assert Config.from_dict({"ingest": {"review_threshold": 1}}).ingest.review_threshold == 1.0


def test_library_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("CITADEL_LIBRARY", str(tmp_path / "elsewhere"))
    assert Config.from_dict({"library": "/x"}).library_path == tmp_path / "elsewhere"


def test_relative_converter_is_inside_app_root():
    from citadel import paths

    assert Config().convert_python == paths.ROOT / ".venv-convert/bin/python"


def test_settings_from_the_app_keep_the_rest_of_the_file(tmp_path, monkeypatch):
    from citadel.config import save_access, save_libraries, write_default

    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    file = tmp_path / "config.toml"
    write_default(file)
    save_libraries(
        [{"name": "Personal", "path": str(tmp_path / "a")}, {"name": "Work", "path": "/w"}],
        file,
    )
    imperial = {"name": "imperial", "login_url": "https://library-search.imperial.ac.uk/"}
    save_access([imperial, {"name": "ieee", "login_url": "https://ieee.example/"}], "chrome", file)
    save_libraries([{"name": "Work", "path": "/w"}], file)
    text = file.read_text()
    assert "# Confidence needed to file a new item" in text
    config = Config.load(file)
    assert [(lib.name, str(lib.root)) for lib in config.libraries()] == [("Work", "/w")]
    assert [a["name"] for a in config.access.all()] == ["imperial", "ieee"]
    assert config.access.browser == "chrome"
