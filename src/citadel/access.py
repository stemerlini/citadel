"""Paywalled papers through your library's sign-in.

Citadel keeps its own Chromium profile per library (data/browser/<name>, on this
machine only, never on OneDrive). You sign in there once (`citadel login`); publisher
pages Citadel opens then load with your library's access. The profile saves every
download into the library inbox without asking, and PDFs open as downloads rather than
in the viewer, so clicking a paper's PDF button is all it takes: the inbox watcher
recognises the file and attaches it to its reference (ingest.Ingester._complete).

You do the clicking. Citadel opens pages in an ordinary browser window and never
drives the publisher's site, so their access terms and bot checks are respected.
Citadel never sees your password: the session lives in the browser profile.

Two kinds of library sign-in work this way:
- Single sign-on (Imperial, UK Federation): sign in to the library once; on a
  publisher's site, "Access through your institution" then passes straight through.
- EZproxy (e.g. a national library): set `proxy` to the library's prefix and every
  publisher link is opened through it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import paths, web
from .config import AccessConfig


def enabled(access: AccessConfig) -> bool:
    return bool(access.name)


def profile_dir(access: AccessConfig) -> Path:
    return paths.DATA_DIR / "browser" / access.name


def proxied(url: str, access: AccessConfig) -> str:
    return access.proxy + url if access.proxy and url else url


def running(profile: Path) -> bool:
    """Chromium holds this lock (a symlink) while a window uses the profile."""
    return (profile / "SingletonLock").is_symlink()


def prepare(profile: Path, inbox: Path) -> None:
    """Point the profile's downloads at the inbox, without asking, PDFs included.
    Chromium reads its preferences at start, so this is left alone while it runs."""
    if running(profile):
        return
    file = profile / "Default" / "Preferences"
    try:
        prefs = json.loads(file.read_text())
    except (OSError, ValueError):
        prefs = {}
    download = prefs.setdefault("download", {})
    download.update(default_directory=str(inbox), prompt_for_download=False, directory_upgrade=True)
    prefs.setdefault("savefile", {})["default_directory"] = str(inbox)
    prefs.setdefault("plugins", {})["always_open_pdf_externally"] = True
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(prefs))


def launch(access: AccessConfig, inbox: Path, url: str) -> bool:
    """Open url in the library profile: a new window, or a tab in the open one."""
    profile = profile_dir(access)
    prepare(profile, inbox)
    try:
        subprocess.Popen(
            [
                access.browser,
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def login(access: AccessConfig, inbox: Path) -> bool:
    return launch(access, inbox, access.login_url or "about:blank")


def open_page(access: AccessConfig, inbox: Path, url: str, publisher: bool = True) -> bool:
    """A paper's page: through the library sign-in when there is one, else the usual
    browser. Publisher links go through the proxy; searches do not."""
    if not enabled(access):
        return web.open_url(url)
    return launch(access, inbox, proxied(url, access) if publisher else url)
