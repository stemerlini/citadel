"""Paywalled papers through your sign-in accounts (a library, a company, a publisher).

Citadel keeps a Chromium profile of its own (data/browser/citadel, on this machine
only, never on OneDrive). You sign in there once to each account you have (a
university, a company, a publisher account: `citadel login [name]`, or 🔑 in the app);
they share the profile, so publisher pages Citadel opens load with every access you
have. The profile saves every download into the library inbox without asking, and PDFs
open as downloads rather than in the viewer, so clicking a paper's PDF button is all it
takes: the inbox watcher recognises the file and attaches it to its reference
(ingest.Ingester._complete).

You do the clicking. Citadel opens pages in an ordinary browser window and never
drives the publisher's site, so their access terms and bot checks are respected.
Citadel never sees your password: the session lives in the browser profile, which
reopens where it left off so that sign-in sessions survive closing the window.

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

PROFILE = "citadel"


def enabled(access: AccessConfig) -> bool:
    return bool(access.all())


def account(access: AccessConfig, name: str = "") -> dict | None:
    """The account called name (any case), or the first one."""
    accounts = access.all()
    if not name:
        return accounts[0] if accounts else None
    return next((a for a in accounts if a["name"].casefold() == name.casefold()), None)


def profile_dir(access: AccessConfig | None = None) -> Path:
    """The browser profile every account shares. One made when each sign-in had its own
    profile (data/browser/<name>) is taken over, so its sign-in is kept."""
    base = paths.DATA_DIR / "browser"
    profile = base / PROFILE
    if not profile.exists() and base.is_dir():
        older = [p for p in base.iterdir() if p.is_dir() and (p / "Default").is_dir()]
        if len(older) == 1 and not running(older[0]):
            older[0].rename(profile)
    return profile


def proxied(url: str, access: AccessConfig) -> str:
    """Publisher links go through the first account with an EZproxy prefix, if any."""
    proxy = next((a["proxy"] for a in access.all() if a["proxy"]), "")
    return proxy + url if proxy and url else url


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
    # "Continue where you left off": the only way Chromium keeps session cookies when the
    # window closes, and library and publisher sign-ins are mostly session cookies.
    prefs.setdefault("session", {})["restore_on_startup"] = 1
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


def login(access: AccessConfig, inbox: Path, name: str = "") -> bool:
    """Open an account's sign-in page (the first account's without a name)."""
    chosen = account(access, name)
    return launch(access, inbox, (chosen or {}).get("login_url") or "about:blank")


def open_page(access: AccessConfig, inbox: Path, url: str, publisher: bool = True) -> bool:
    """A paper's page: through the library sign-in when there is one, else the usual
    browser. Publisher links go through the proxy; searches do not."""
    if not enabled(access):
        return web.open_url(url)
    return launch(access, inbox, proxied(url, access) if publisher else url)
