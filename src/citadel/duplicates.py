"""Finding duplicates, and keeping the copy you choose.

Two items are duplicates when they are the same file (SHA-256), share a DOI, or
have the same title, year and first author. Items are filed references and files
waiting in review. A group is every item linked to another by any of those, so
a preprint, its published PDF and a copy of either end up together.

Resolving a group keeps one item and deletes the others. A kept review item is
filed first. Deleting on the OneDrive mount sends files to OneDrive's recycle
bin, where they stay for 30 days. Groups that are not really duplicates (two
volumes with the same title, say) can be marked as such and are not shown again.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import record
from .index import Index
from .ingest import PROPOSAL_SUFFIX, Ingester, title_key
from .library import Library

IGNORED_FILE = ".duplicates-ignored.json"  # in the library: synced with it


@dataclass
class Member:
    kind: str  # reference | review
    key: str  # citekey, or the file name in _review/
    title: str
    year: int | None
    first_author: str
    location: str  # library-relative folder or file
    has_pdf: bool = False
    has_markdown: bool = False
    has_notes: bool = False
    attachments: int = 0
    added: str = ""

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass
class Group:
    reasons: list[str]
    members: list[Member] = field(default_factory=list)

    @property
    def id(self) -> str:
        return "|".join(sorted(m.id for m in self.members))

    def as_dict(self) -> dict:
        return {"reasons": self.reasons, "members": [asdict(m) for m in self.members]}


def find(index: Index, library: Library) -> list[Group]:
    members: dict[str, Member] = {}
    keys: dict[str, list[tuple[str, str]]] = {}  # member id -> [(reason, key)]

    for row in index.list():
        data = index.get(row["citekey"])["data"]
        folder = library.root / row["folder"]
        member = Member(
            "reference",
            row["citekey"],
            row["title"],
            row["year"],
            row["first_author"],
            row["folder"],
            bool(row["has_pdf"]),
            bool(row["has_fulltext"]),
            _has_notes(folder),
            len(data.get("attachments") or []),
            row["added"],
        )
        members[member.id] = member
        keys[member.id] = _keys(
            data.get("pdf_sha256", ""), row["doi"], row["title"], row["year"], row["first_author"]
        )

    for proposal in Ingester(library, index, None, None).review_items():
        meta = proposal.metadata or {}
        authors = meta.get("authors") or []
        first = authors[0].split(",")[0].strip() if authors else ""
        member = Member(
            "review",
            proposal.file,
            meta.get("title") or proposal.file,
            meta.get("year"),
            first,
            f"{library.review.name}/{proposal.file}",
            proposal.file.lower().endswith(".pdf"),
            added=proposal.added,
        )
        members[member.id] = member
        found = _keys(
            proposal.sha256, meta.get("doi", ""), meta.get("title", ""), meta.get("year"), first
        )
        if proposal.duplicate_of:
            found.append(("flagged on arrival", f"ref:{proposal.duplicate_of}"))
        keys[member.id] = found

    # Union-find over shared keys; a "ref:" key links straight to that reference.
    parent = {m: m for m in members}

    def root(m: str) -> str:
        while parent[m] != m:
            parent[m] = parent[parent[m]]
            m = parent[m]
        return m

    reasons: dict[str, set[str]] = {}
    owner: dict[str, str] = {}
    for member_id, found in keys.items():
        for reason, key in found:
            target = f"reference:{key[4:]}" if key.startswith("ref:") else owner.get(key)
            if key.startswith("ref:") and target not in members:
                continue
            if target is None:
                owner[key] = member_id
                continue
            a, b = root(member_id), root(target)
            if a != b:
                parent[a] = b
            reasons.setdefault(member_id, set()).add(reason)
            reasons.setdefault(target, set()).add(reason)

    groups: dict[str, Group] = {}
    for member_id in members:
        group = groups.setdefault(root(member_id), Group([]))
        group.members.append(members[member_id])
        group.reasons = sorted(set(group.reasons) | reasons.get(member_id, set()))
    ignored = _ignored(library)
    found_groups = [g for g in groups.values() if len(g.members) > 1 and g.id not in ignored]
    for group in found_groups:  # filed copies first, the best-kept copy first among them
        group.members.sort(
            key=lambda m: (
                m.kind != "reference",
                not m.has_markdown,
                not m.has_notes,
                m.added or "9999",
            )
        )
    return sorted(found_groups, key=lambda g: g.members[0].title.casefold())


def keep(
    index: Index, library: Library, ingester: Ingester, group: Group, keep_id: str
) -> list[str]:
    """Keep one member, delete the rest. Returns what was deleted."""
    kept = next((m for m in group.members if m.id == keep_id), None)
    if kept is None:
        raise ValueError(f"{keep_id} is not in this group")
    if kept.kind == "review":
        ingester.accept(kept.key, force=True)  # the kept copy is filed
    deleted = []
    for member in group.members:
        if member is kept:
            continue
        if member.kind == "reference":
            shutil.rmtree(library.root / member.location)
        else:
            (library.review / member.key).unlink(missing_ok=True)
            (library.review / f"{member.key}{PROPOSAL_SUFFIX}").unlink(missing_ok=True)
        deleted.append(member.location)
    index.reindex()
    return deleted


def ignore(library: Library, group: Group) -> None:
    """Remember that this group is not a set of duplicates."""
    ignored = _ignored(library)
    ignored.add(group.id)
    (library.root / IGNORED_FILE).write_text(json.dumps(sorted(ignored), indent=1))


def _keys(sha: str, doi: str, title: str, year, author: str) -> list[tuple[str, str]]:
    found = []
    if sha:
        found.append(("identical file", f"sha:{sha}"))
    if doi:
        found.append(("same DOI", f"doi:{doi.lower()}"))
    key = title_key(title, year, author)
    if key:
        found.append(("same title, year and author", f"title:{key}"))
    return found


def _has_notes(folder: Path) -> bool:
    """Anything written under '## Notes' in the record."""
    try:
        _, body = record.split((folder / f"{folder.name}.md").read_text(encoding="utf-8"))
    except (OSError, record.RecordError):
        return False
    notes = body.split("## Notes", 1)
    return len(notes) == 2 and bool(notes[1].strip())


def _ignored(library: Library) -> set[str]:
    try:
        return set(json.loads((library.root / IGNORED_FILE).read_text()))
    except (OSError, ValueError):
        return set()
