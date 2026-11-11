import json

from citadel import access
from citadel.config import AccessConfig, Config


def test_config_section_round_trips():
    config = Config.from_dict(
        {"access": {"name": "imperial", "login_url": "https://login.example/", "x": 1}}
    )
    assert config.access.name == "imperial"
    assert config.access.login_url == "https://login.example/"
    assert config.access.browser == "chromium"
    assert not access.enabled(Config().access)


def test_proxy_prefixes_publisher_links_only(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(access, "launch", lambda a, inbox, url: opened.append(url) or True)
    bnl = AccessConfig(name="bnl", proxy="https://proxy.example/login?url=")
    access.open_page(bnl, tmp_path, "https://doi.org/10.1/x")
    access.open_page(bnl, tmp_path, "https://www.google.com/search?q=x", publisher=False)
    assert opened == [
        "https://proxy.example/login?url=https://doi.org/10.1/x",
        "https://www.google.com/search?q=x",
    ]


def test_without_sign_in_pages_open_in_the_usual_browser(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(access.web, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(access, "launch", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert access.open_page(AccessConfig(), tmp_path, "https://doi.org/10.1/x")
    assert opened == ["https://doi.org/10.1/x"]


def test_prepare_sends_downloads_to_the_inbox_and_keeps_other_prefs(tmp_path):
    profile, inbox = tmp_path / "profile", tmp_path / "_inbox"
    prefs_file = profile / "Default" / "Preferences"
    prefs_file.parent.mkdir(parents=True)
    prefs_file.write_text(json.dumps({"download": {"prompt_for_download": True}, "keep": 1}))
    access.prepare(profile, inbox)
    prefs = json.loads(prefs_file.read_text())
    assert prefs["download"]["default_directory"] == str(inbox)
    assert prefs["download"]["prompt_for_download"] is False
    assert prefs["plugins"]["always_open_pdf_externally"] is True
    assert prefs["session"]["restore_on_startup"] == 1  # keeps the sign-in's session cookies
    assert prefs["keep"] == 1


def test_prepare_leaves_a_running_profile_alone(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("host-1234")
    access.prepare(profile, tmp_path / "_inbox")
    assert not (profile / "Default").exists()


def test_launch_uses_the_library_profile(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(access.paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(access.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    imperial = AccessConfig(name="imperial", login_url="https://library.example/")
    assert access.login(imperial, tmp_path / "_inbox")
    argv = calls[0]
    assert argv[0] == "chromium"
    assert f"--user-data-dir={tmp_path / 'data' / 'browser' / 'imperial'}" in argv
    assert argv[-1] == "https://library.example/"
