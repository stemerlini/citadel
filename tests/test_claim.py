import os
import subprocess
import time

from citadel import claim
from citadel.claim import Claimer


def local_claimer(inbox, host="here"):
    return Claimer(inbox, host=host, backend="local")


def test_local_claim_wins_once_and_leftovers_are_this_hosts(tmp_path):
    inbox = tmp_path / "_inbox"
    inbox.mkdir()
    (inbox / "paper.pdf").write_bytes(b"%PDF")
    here = local_claimer(inbox)
    there = local_claimer(inbox, host="desktop")

    won = here.claim(inbox / "paper.pdf")
    assert won.status == "claimed" and won.path == inbox / ".claimed" / "here" / "paper.pdf"
    assert there.claim(inbox / "paper.pdf").status == "taken"
    # A crashed watcher's file is picked up again by the same host only.
    assert here.leftovers() == [won.path] and there.leftovers() == []

    old = time.time() - 25 * 3600
    os.utime(won.path, (old, old))
    assert there.stale() == [won.path] and here.stale() == []


class FakeGraph:
    """OneDrive's side of a claim: the file moves only while its eTag is current."""

    def __init__(self, files):
        self.files, self.calls = files, []

    def sign_in(self):
        pass

    def call(self, verb, rel, query="", body=None, if_match=""):
        self.calls.append((verb, rel, if_match))
        if verb == "GET":
            path = rel.removeprefix("root:").removesuffix(":")
            if path in self.files:
                return 200, {"id": path, "eTag": self.files[path]}
            if path.endswith("/.claimed/here"):
                return 200, {"id": path}
            return 404, {}
        if if_match != self.files.get(rel.removeprefix("items/")):
            return 412, {}
        del self.files[rel.removeprefix("items/")]
        return 200, {}


def test_onedrive_claim_moves_on_onedrive_with_the_etag(tmp_path):
    mount = claim.Mount("onedrive-personal", "", tmp_path, rc="unix:///run/rc.sock")
    inbox = tmp_path / "Citadel" / "_inbox"
    (inbox / ".claimed" / "here").mkdir(parents=True)
    graph = FakeGraph({"/Citadel/_inbox/paper.pdf": "v1"})
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["rclone", "mkdir"]:  # the mount shows the moved file
            (inbox / ".claimed" / "here" / "paper.pdf").write_bytes(b"%PDF")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    claimer = Claimer(inbox, host="here", backend="onedrive", mount=mount, graph=graph, run=run)
    won = claimer.claim(inbox / "paper.pdf")
    assert won.status == "claimed" and won.path == inbox / ".claimed" / "here" / "paper.pdf"
    assert ("PATCH", "items//Citadel/_inbox/paper.pdf", "v1") in graph.calls
    assert ["rclone", "mkdir", "onedrive-personal:/Citadel/_inbox/.claimed/here"] in commands
    # Gone from OneDrive and not uploading: another machine has it.
    assert claimer.claim(inbox / "paper.pdf").status == "taken"
    # Another machine moved it between our look and our move: the eTag no longer matches.
    graph.files["/Citadel/_inbox/late.pdf"] = "v1"
    graph.call = lambda *a, _call=graph.call, **k: (412, {}) if a[0] == "PATCH" else _call(*a, **k)
    assert claimer.claim(inbox / "late.pdf").status == "taken"


def test_backend_follows_the_rclone_mount_of_the_library(tmp_path, monkeypatch):
    mount = tmp_path / "One Drive"
    (mount / "Citadel" / "_inbox").mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    escaped = str(mount.resolve()).replace(" ", "\\040")
    mountinfo.write_text(
        "22 1 0:21 / / rw - ext4 /dev/sda1 rw\n"
        f"715 58 0:88 / {escaped} rw,nosuid - fuse.rclone onedrive-personal: rw\n"
    )
    found = claim.rclone_mount(mount / "Citadel" / "_inbox", mountinfo)
    assert (found.remote, found.root, found.mountpoint) == (
        "onedrive-personal",
        "",
        mount.resolve(),
    )
    assert found.drive_path(mount.resolve() / "Citadel" / "_inbox") == "/Citadel/_inbox"
    assert claim.rclone_mount(tmp_path, mountinfo) is None

    monkeypatch.setattr(claim, "rclone_mount", lambda path: found)
    monkeypatch.setattr(claim, "_remote_type", lambda remote: "onedrive")
    assert Claimer(mount / "Citadel" / "_inbox").backend == "onedrive"
    monkeypatch.setattr(claim, "_remote_type", lambda remote: "drive")
    assert Claimer(mount / "Citadel" / "_inbox").backend == "local"


def test_rename_folder_on_onedrive_waits_for_uploads_and_guards_the_etag(tmp_path):
    import json

    import pytest

    mount = claim.Mount("onedrive-personal", "", tmp_path, rc="unix:///run/rc.sock")
    folder = tmp_path / "Citadel"
    (folder / "_inbox").mkdir(parents=True)
    queue = [{"name": "Citadel/papers/x.pdf"}]
    calls = []

    def run(command, **kwargs):
        if "vfs/queue" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({"queue": queue}), "")
        if "vfs/refresh" in command:  # the mount shows OneDrive's new name
            folder.rename(folder.with_name("Citadel-personal"))
        return subprocess.CompletedProcess(command, 0, "{}", "")

    class Graph:
        def sign_in(self):
            pass

        def call(self, verb, rel, query="", body=None, if_match=""):
            calls.append((verb, rel, query, body, if_match))
            return (200, {"id": "abc", "eTag": "v1"}) if verb == "GET" else (200, {})

    claimer = Claimer(folder / "_inbox", backend="onedrive", mount=mount, graph=Graph(), run=run)
    with pytest.raises(OSError, match="uploading"):
        claim.rename_folder(folder, "Citadel-personal", claimer)
    assert calls == [] and folder.is_dir()

    queue.clear()
    target = claim.rename_folder(folder, "Citadel-personal", claimer)
    assert target == tmp_path / "Citadel-personal" and target.is_dir()
    assert calls[-1] == (
        "PATCH",
        "items/abc",
        "@microsoft.graph.conflictBehavior=fail",
        {"name": "Citadel-personal"},
        "v1",
    )
