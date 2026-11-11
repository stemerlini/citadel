"""Claiming an inbox file before filing it, so that one machine files it.

Every machine may run an inbox watcher on the same library. Before a watcher (or
`citadel ingest`) touches an inbox file, it moves it into
_inbox/.claimed/<host>/: the machine whose move succeeds files it, the others find
it gone and leave it.

How the move is made depends on where the library folder is mounted:

- On an rclone mount of a OneDrive remote (`rclone mount onedrive-personal: ~/...`)
  the move is made on OneDrive itself, guarded by the file's eTag (Microsoft Graph,
  with the sign-in rclone already keeps), so only one machine can win, whatever the
  mounts show. When the mount has a remote control (`--rc`), Citadel also asks it
  whether the file is still uploading and refreshes the folders after the move.
- Anywhere else it is a local rename: atomic on this machine, but two machines may
  both "win" before their folders catch up. Citadel warns once.

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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


@dataclass
class Mount:
    """An rclone mount: `rclone mount <remote>:<root> <mountpoint>`."""

    remote: str  # rclone remote name, e.g. onedrive-personal
    root: str  # the drive folder the mount shows, "" for the whole drive
    mountpoint: Path
    rc: str = ""  # the mount's remote control address, "" when it has none

    def drive_path(self, path: Path) -> str:
        """Where a file under the mount is on the drive, as /a/b."""
        rel = path.relative_to(self.mountpoint).as_posix()
        return "/" + "/".join(p for p in (self.root, "" if rel == "." else rel) if p)

    def fs(self) -> str:
        return f"{self.remote}:{self.root}"


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def rclone_mount(path: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> Mount | None:
    """The rclone mount that holds `path`, or None."""
    path = path.resolve()
    best = None
    try:
        lines = mountinfo.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        head, _, tail = line.partition(" - ")
        fields, extra = head.split(), tail.split()
        if len(fields) < 5 or len(extra) < 2 or extra[0] != "fuse.rclone":
            continue
        mountpoint = Path(_unescape(fields[4]))
        if path.is_relative_to(mountpoint) and (
            best is None or len(mountpoint.parts) > len(best[0].parts)
        ):
            best = (mountpoint, _unescape(extra[1]))
    if best is None:
        return None
    mountpoint, source = best
    remote, _, root = source.partition(":")
    return Mount(remote, root.strip("/"), mountpoint, _rc_address(source, mountpoint))


def _rc_address(source: str, mountpoint: Path, proc: Path = Path("/proc")) -> str:
    """The --rc-addr of the `rclone mount` process serving this mount, if it has --rc."""
    for cmdline in proc.glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
        if not args or Path(args[0]).name != "rclone" or "mount" not in args:
            continue
        rest = args[args.index("mount") + 1 :]
        positional = [a for a in rest if a and not a.startswith("-")][:2]
        if len(positional) < 2 or positional[0].rstrip("/") != source.rstrip("/"):
            continue
        if Path(positional[1]).expanduser().resolve() != mountpoint:
            continue
        address = ""
        for i, arg in enumerate(rest):
            if arg == "--rc-addr" and i + 1 < len(rest):
                address = rest[i + 1]
            elif arg.startswith("--rc-addr="):
                address = arg.split("=", 1)[1]
        if address or "--rc" in rest:
            return address or "localhost:5572"
        return ""
    return ""


def _remote_type(remote: str, run=subprocess.run) -> str:
    try:
        done = run(["rclone", "listremotes", "--long"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in done.stdout.splitlines():
        name, _, kind = line.partition(":")
        if name.strip() == remote:
            return kind.strip()
    return ""


def _backend(inbox: Path) -> tuple[str, Mount | None]:
    mount = rclone_mount(inbox)
    if mount and _remote_type(mount.remote) == "onedrive":
        return "onedrive", mount
    return "local", None


class Graph:
    """Microsoft Graph calls on the mount's drive, signed in with rclone's own token.
    The token is read from rclone's config and never logged."""

    def __init__(self, remote: str, run=subprocess.run):
        self.remote, self.run = remote, run
        self.token, self.drive = "", ""

    def _read(self) -> datetime | None:
        done = self.run(["rclone", "config", "dump"], capture_output=True, text=True, timeout=30)
        entry = json.loads(done.stdout or "{}").get(self.remote, {})
        token = json.loads(entry.get("token") or "{}")
        self.token, self.drive = token.get("access_token", ""), entry.get("drive_id", "")
        expiry = re.sub(r"(\.\d{6})\d+", r"\1", token.get("expiry", ""))
        try:
            return datetime.fromisoformat(expiry)
        except ValueError:
            return None

    def sign_in(self) -> None:
        expiry = self._read()
        if expiry is None or expiry < datetime.now(UTC) + timedelta(minutes=2):
            # Any call makes rclone refresh the token and save it.
            self.run(["rclone", "about", f"{self.remote}:"], capture_output=True, timeout=60)
            self._read()
        if not self.token or not self.drive:
            raise OSError(f"no OneDrive sign-in for {self.remote} in rclone")

    def call(self, verb, rel, query="", body=None, if_match=""):
        url = f"https://graph.microsoft.com/v1.0/drives/{self.drive}/{rel}"
        if query:
            url += "?" + query
        request = urllib.request.Request(url, method=verb)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        if if_match:
            request.add_header("If-Match", if_match)
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(request, data=data, timeout=30) as reply:
                return reply.status, json.loads(reply.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, {}


def _item(path: str) -> str:
    return "root" if path == "/" else "root:" + urllib.parse.quote(path) + ":"


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] or "/"


class Claimer:
    def __init__(
        self,
        inbox: Path,
        host: str = "",
        backend: str = "",
        mount: Mount | None = None,
        graph: Graph | None = None,
        run=subprocess.run,
    ):
        self.inbox = inbox
        self.host = host or hostname()
        self.folder = inbox / CLAIMED / self.host
        if not backend:
            backend, mount = _backend(inbox)
        self.backend, self.mount, self.run = backend, mount, run
        self.graph = graph or (Graph(mount.remote, run) if mount else None)

    def claim(self, path: Path) -> Claim:
        """Move an inbox file into this host's claimed folder, if nobody else has."""
        target = self.folder / _free_name(self.folder, path.name)
        if self.backend == "onedrive":
            try:
                return self._onedrive(path, target)
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                return Claim("error", reason=f"OneDrive: {error}")
        global _warned
        if not _warned:
            _warned = True
            log.warning(
                "%s is not on an rclone mount of OneDrive: inbox files are claimed with "
                "a local rename, which is safe on this machine only. Mount the library "
                "folder with rclone on every machine for cross-machine safety.",
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

    def _onedrive(self, path: Path, target: Path) -> Claim:
        """Move the file on OneDrive, only if nobody moved it since we looked (eTag)."""
        mount = self.mount
        source, dest = mount.drive_path(path), mount.drive_path(target)
        if self._uploading(path):
            return Claim("pending", reason="still uploading to OneDrive")
        self.graph.sign_in()
        status, item = self.graph.call("GET", _item(source), "$select=id,eTag")
        if status == 404:
            self._refresh(path)
            if mount.rc:  # not uploading, so somebody moved it
                return Claim("taken", reason="gone from OneDrive: another machine claimed it")
            return Claim("pending", reason="not on OneDrive: still uploading or claimed elsewhere")
        if status != 200:
            return Claim("error", reason=f"OneDrive answered {status} for {source}")
        if self.graph.call("GET", _item(dest), "$select=id")[0] != 404:
            return Claim("error", reason=f"{dest} already exists on OneDrive")
        self.run(
            ["rclone", "mkdir", f"{mount.remote}:{_parent(dest)}"],
            capture_output=True,
            timeout=60,
        )
        status, folder = self.graph.call("GET", _item(_parent(dest)), "$select=id")
        if status != 200:
            return Claim("error", reason=f"could not make {_parent(dest)} on OneDrive")
        status, _ = self.graph.call(
            "PATCH",
            f"items/{item['id']}",
            "@microsoft.graph.conflictBehavior=fail",
            {"parentReference": {"id": folder["id"]}, "name": target.name},
            if_match=item["eTag"],
        )
        if status in (404, 412):
            return Claim("taken", reason="another machine claimed it first")
        if status not in (200, 201):
            return Claim("error", reason=f"OneDrive answered {status} moving {source}")
        self._refresh(path)
        self._refresh(target)
        deadline = time.monotonic() + (5 if mount.rc else 90)
        while not target.exists() and time.monotonic() < deadline:
            time.sleep(2)
        if target.exists():
            return Claim("claimed", target)
        # Ours on OneDrive; the next pass finds it among this host's leftovers.
        return Claim("pending", reason="claimed; waiting for the mount to show it")

    def _rc(self, command: str, **params) -> dict | None:
        """Ask the mount's remote control, if it has one."""
        address = self.mount.rc
        if not address:
            return None
        if address.startswith("unix://"):
            via = ["--unix-socket", address.removeprefix("unix://")]
        else:
            via = ["--url", address if "://" in address else f"http://{address}"]
        args = [f"{key}={value}" for key, value in params.items()]
        try:
            done = self.run(
                ["rclone", "rc", *via, command, *args], capture_output=True, text=True, timeout=60
            )
            return json.loads(done.stdout or "{}") if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def _uploading(self, path: Path) -> bool:
        name = path.relative_to(self.mount.mountpoint).as_posix()
        queue = (self._rc("vfs/queue", fs=self.mount.fs()) or {}).get("queue") or []
        return any(entry.get("name") == name for entry in queue)

    def _refresh(self, path: Path) -> None:
        folder = path.parent.relative_to(self.mount.mountpoint).as_posix()
        folder = "" if folder == "." else folder
        self._rc("vfs/forget", fs=self.mount.fs(), dir=folder)
        self._rc("vfs/refresh", fs=self.mount.fs(), dir=folder)

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


def rename_folder(folder: Path, name: str, claimer: Claimer | None = None) -> Path:
    """Rename a library folder in place (Citadel to Citadel-personal). On an rclone mount
    of OneDrive the rename is made on OneDrive, guarded by the folder's eTag and failing
    if the name is taken, then the mount is refreshed; elsewhere it is a local rename.
    Refuses while files in the folder are still uploading."""
    target = folder.with_name(name)
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    claimer = claimer or Claimer(folder / "_inbox")
    if claimer.backend != "onedrive":
        os.rename(folder, target)
        return target
    mount = claimer.mount
    inside = folder.relative_to(mount.mountpoint).as_posix() + "/"
    queue = (claimer._rc("vfs/queue", fs=mount.fs()) or {}).get("queue") or []
    if any(str(entry.get("name", "")).startswith(inside) for entry in queue):
        raise OSError("files in it are still uploading to OneDrive: try again in a minute")
    claimer.graph.sign_in()
    status, item = claimer.graph.call("GET", _item(mount.drive_path(folder)), "$select=id,eTag")
    if status != 200:
        raise OSError(f"OneDrive answered {status} for {mount.drive_path(folder)}")
    status, _ = claimer.graph.call(
        "PATCH",
        f"items/{item['id']}",
        "@microsoft.graph.conflictBehavior=fail",
        {"name": name},
        if_match=item["eTag"],
    )
    if status == 412:
        raise OSError("it changed on OneDrive meanwhile: try again")
    if status not in (200, 201):
        raise OSError(f"OneDrive answered {status} renaming {mount.drive_path(folder)}")
    claimer._refresh(folder)
    deadline = time.monotonic() + (30 if mount.rc else 120)
    while not target.is_dir() and time.monotonic() < deadline:
        time.sleep(2)
    if not target.is_dir():
        raise OSError(f"renamed on OneDrive; {target} will appear once the mount refreshes")
    return target
