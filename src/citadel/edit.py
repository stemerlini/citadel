"""Changing a filed reference: metadata, status, tags, and its topic folder.

Every change goes through the record (the .md frontmatter), then the index is
refreshed. Moving to another topic moves the whole reference folder. The
basename (folder and file names) and the citekey never change: links to them,
in Obsidian notes or LaTeX documents, keep working.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from . import record
from .index import Index
from .record import STATUSES, TYPES, Reference

EDITABLE = {
    "title",
    "type",
    "authors",
    "year",
    "journal",
    "volume",
    "issue",
    "pages",
    "publisher",
    "doi",
    "arxiv",
    "isbn",
    "url",
    "abstract",
    "tags",
    "projects",
    "collections",
    "status",
    "rating",
}


class EditError(ValueError):
    """A change that cannot be made (unknown reference, topic, or value)."""


def record_path(index: Index, citekey: str) -> Path:
    row = index.get(citekey)
    if row is None:
        raise EditError(f"no reference {citekey}")
    folder = index.root / row["folder"]
    return folder / f"{folder.name}.md"


def load(index: Index, citekey: str) -> tuple[Reference, Path]:
    path = record_path(index, citekey)
    ref, _ = record.read(path)
    return ref, path


def update(index: Index, citekey: str, topics: set[str] | None = None, **changes) -> Reference:
    """Apply field changes (and a topic move, with topic=…). Returns the new reference."""
    ref, path = load(index, citekey)
    unknown = set(changes) - EDITABLE - {"topic"}
    if unknown:
        raise EditError(f"cannot edit {', '.join(sorted(unknown))}")
    if "status" in changes and changes["status"] not in STATUSES:
        raise EditError(f"status must be one of {', '.join(STATUSES)}")
    if "type" in changes and changes["type"] not in TYPES:
        raise EditError(f"type must be one of {', '.join(TYPES)}")
    if "rating" in changes and changes["rating"] not in (None, 1, 2, 3, 4, 5):
        raise EditError("rating must be 1-5 (or empty)")

    new_topic = changes.pop("topic", ref.topic)
    known = {f.name for f in fields(Reference)}
    for name, value in changes.items():
        if name in known:
            setattr(ref, name, value)

    if new_topic != ref.topic:
        if topics is not None and new_topic not in topics:
            raise EditError(f"unknown topic {new_topic!r}")
        path = _move(index, path, new_topic)
        ref.topic = new_topic
    record.update_metadata(path, ref)
    index.reindex()
    return ref


def cycle_status(index: Index, citekey: str) -> str:
    ref, _ = load(index, citekey)
    following = (
        STATUSES[(STATUSES.index(ref.status) + 1) % len(STATUSES)]
        if ref.status in STATUSES
        else STATUSES[0]
    )
    update(index, citekey, status=following)
    return following


def _move(index: Index, md_path: Path, topic: str) -> Path:
    folder = md_path.parent
    target = index.root / topic / folder.name
    if target.exists():
        raise EditError(f"{topic}/{folder.name} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(target)  # same filesystem: one rename, also on the OneDrive mount
    return target / md_path.name
