import json
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


def test_omadrive_claim_asks_omadrive(tmp_path):
    inbox = tmp_path / "_inbox"
    inbox.mkdir()
    (inbox / "paper.pdf").write_bytes(b"%PDF")
    calls, replies = (
        [],
        [
            {"ok": True, "path": str(inbox / ".claimed" / "here" / "paper.pdf")},
            {"ok": False, "reason": "taken"},
            {"ok": False, "reason": "pending"},
        ],
    )

    def run(command, **kwargs):
        calls.append(command)
        reply = replies.pop(0)
        return subprocess.CompletedProcess(command, 0 if reply["ok"] else 1, json.dumps(reply), "")

    claimer = Claimer(inbox, host="here", backend="omadrive", run=run)
    assert claimer.claim(inbox / "paper.pdf").path == inbox / ".claimed" / "here" / "paper.pdf"
    assert calls[0] == [
        "omadrive",
        "agent",
        "claim",
        str(inbox / "paper.pdf"),
        "--as",
        str(inbox / ".claimed" / "here" / "paper.pdf"),
    ]
    assert claimer.claim(inbox / "paper.pdf").status == "taken"
    assert claimer.claim(inbox / "paper.pdf").status == "pending"


def test_backend_follows_omadrive_mounts(tmp_path, monkeypatch):
    mount = tmp_path / "OneDrive-Personal"
    (mount / "Citadel" / "_inbox").mkdir(parents=True)
    monkeypatch.setattr(claim, "_omadrive_mounts", lambda: [mount.resolve()])
    assert Claimer(mount / "Citadel" / "_inbox").backend == "omadrive"
    assert Claimer(tmp_path).backend == "local"
