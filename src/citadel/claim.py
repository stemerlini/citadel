"""Claiming an inbox file before filing it, so that one machine files it.

Every machine may run an inbox watcher on the same library. Before a watcher (or
`citadel ingest`) touches an inbox file, it moves it into
_inbox/.claimed/<host>/: the machine whose move succeeds files it, the others find
it gone and leave it.

- In a OneDrive folder that omadrive knows (an rclone mount it manages) the move is
  `omadrive agent claim`: a move on OneDrive itself guarded by the file's eTag, so
  only one machine can win, whatever their mounts show.
- Elsewhere (a plain folder, a mount omadrive does not know) it is a local rename:
  atomic on this machine, but two machines may both "win" before their mounts catch
  up. Citadel warns once.

A file left in this host's claimed folder (the watcher stopped half-way) is picked up
again by the same host. Other hosts' files are never taken; `citadel doctor` reports
the ones older than a day.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CLAIMED = ".claimed"
STALE_SECONDS = 24 * 3600

log = logging.getLogger("citadel.claim")
_warned = False


@dataclass
class Claim:
    status: str  # claimed | taken | pending | error
    path: Path | None = None  # where the claimed file is now
    reason: str = ""


def hostname() -> str:
    return re.sub(r"[^\w.-]+", "-", socket.gethostname()) or "host"


def _omadrive_mounts(run=subprocess.run) -> list[Path]:
    """The OneDrive mount folders omadrive manages, or [] without omadrive."""
    try:
        done = run(["omadrive", "agent", "accounts"], capture_output=True, text=True, timeout=30)
        accounts = json.loads(done.stdout or "{}").get("accounts", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [
        Path(a["mountpoint"]).resolve()
        for a in accounts
        if a.get("mounted") and a.get("mountpoint")
    ]


def _backend(inbox: Path) -> str:
    inbox = inbox.resolve()
    if any(inbox.is_relative_to(mount) for mount in _omadrive_mounts()):
        return "omadrive"
    return "local"


class Claimer:
    def __init__(self, inbox: Path, host: str = "", backend: str = "", run=subprocess.run):
        self.inbox = inbox
        self.host = host or hostname()
        self.folder = inbox / CLAIMED / self.host
        self.backend = backend or _backend(inbox)
        self.run = run

    def claim(self, path: Path) -> Claim:
        """Move an inbox file into this host's claimed folder, if nobody else has."""
        target = self.folder / _free_name(self.folder, path.name)
        if self.backend == "omadrive":
            return self._omadrive(path, target)
        global _warned
        if not _warned:
            _warned = True
            log.warning(
                "%s is not in a OneDrive folder omadrive knows: inbox files are claimed "
                "with a local rename, which is safe on this machine only. Mount the "
                "library through omadrive (rclone) on every machine for cross-machine "
                "safety.",
                self.inbox,
            )
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            if target.exists():
                return Claim("taken", reason=f"{target.name} is already claimed")
            os.rename(path, target)
        except FileNotFoundError:
            return Claim("taken", reason="gone: another machine claimed it")
        except OSError as error:
            return Claim("error", reason=str(error))
        return Claim("claimed", target)

    def _omadrive(self, path: Path, target: Path) -> Claim:
        command = ["omadrive", "agent", "claim", str(path), "--as", str(target)]
        try:
            done = self.run(command, capture_output=True, text=True, timeout=120)
            reply = json.loads(done.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            return Claim("error", reason=f"omadrive: {error}")
        if reply.get("ok"):
            return Claim("claimed", Path(reply.get("path") or target))
        reason = reply.get("reason", "")
        if reason in ("taken", "pending"):
            return Claim(reason, reason=reply.get("error", ""))
        return Claim("error", reason=f"omadrive: {reply.get('error') or reason or 'failed'}")

    def leftovers(self) -> list[Path]:
        """Files this host claimed but did not finish (a crash, a reboot)."""
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.iterdir() if p.is_file())

    def stale(self, seconds: float = STALE_SECONDS) -> list[Path]:
        """Other hosts' claimed files untouched for longer than `seconds`."""
        claimed = self.inbox / CLAIMED
        if not claimed.is_dir():
            return []
        now = time.time()
        return sorted(
            file
            for host in claimed.iterdir()
            if host.is_dir() and host.name != self.host
            for file in host.iterdir()
            if file.is_file() and now - file.stat().st_mtime > seconds
        )


def _free_name(folder: Path, name: str) -> str:
    candidate, stem, suffix, number = name, Path(name).stem, Path(name).suffix, 2
    while (folder / candidate).exists():
        candidate = f"{stem} ({number}){suffix}"
        number += 1
    return candidate
