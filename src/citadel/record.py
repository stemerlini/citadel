"""A reference and its Markdown file.

The .md next to the PDF is the record: YAML frontmatter holds the metadata, the
body holds the converted full text between two markers, and anything outside
the markers (your notes) is never touched by Citadel.

    ---
    citekey: verma2004statistical
    …
    ---
    # Title
    <!-- citadel:fulltext-start -->
    …
    <!-- citadel:fulltext-end -->
    ## Notes
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import naming

FULLTEXT_START = "<!-- citadel:fulltext-start -->"
FULLTEXT_END = "<!-- citadel:fulltext-end -->"

TYPES = (
    "article",
    "preprint",
    "book",
    "chapter",
    "thesis",
    "report",
    "standard",
    "conference",
    "slides",
    "poster",
    "dataset",
    "misc",
)
STATUSES = ("unread", "reading", "read")


class RecordError(ValueError):
    """A Markdown file is not a valid Citadel record."""


@dataclass
class Reference:
    citekey: str
    title: str
    type: str = "article"
    authors: list[str] = field(default_factory=list)  # "Surname, Given"
    year: int | None = None
    journal: str = ""  # or book/proceedings title
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    doi: str = ""
    arxiv: str = ""
    isbn: str = ""
    url: str = ""
    abstract: str = ""
    topic: str = ""
    tags: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    status: str = "unread"
    rating: int | None = None
    added: str = ""  # ISO date
    source: str = ""  # inbox | migration | agent:<name> | doi | manual
    metadata_confidence: float | None = None
    md_engine: str = ""
    pdf_sha256: str = ""
    basename: str = ""  # folder and file name; fixed once filed
    extra: dict = field(default_factory=dict)  # unknown keys, kept as they are

    def __post_init__(self):
        if not self.basename:
            self.basename = naming.basename(self.year, self.title, self.authors)

    @property
    def folder(self) -> str:
        """Folder relative to the library root."""
        return f"{self.topic}/{self.basename}" if self.topic else self.basename

    @property
    def first_author(self) -> str:
        return naming.surname(self.authors[0]) if self.authors else ""

    # -- frontmatter ---------------------------------------------------------

    def to_dict(self) -> dict:
        data = {}
        for f in fields(self):
            if f.name == "extra":
                continue
            value = getattr(self, f.name)
            if f.name == "citekey" or f.name == "title" or value not in ("", None, []):
                data[f.name] = value
        data.update(self.extra)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Reference:
        if not data.get("citekey") or not data.get("title"):
            raise RecordError("frontmatter needs at least citekey and title")
        known = {f.name for f in fields(cls)} - {"extra"}
        values = {k: _clean(k, v) for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(**values, extra=extra)


def _clean(key: str, value):
    """Normalise what YAML hands back: dates as ISO strings, lists of strings, ints."""
    if isinstance(value, dt.date):
        return value.isoformat()
    if key in ("authors", "tags", "projects", "collections"):
        if value is None:
            return []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]
    if key in ("year", "rating"):
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
    if key == "metadata_confidence":
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return "" if value is None else str(value)


# -- Markdown files ---------------------------------------------------------------


def split(text: str) -> tuple[dict, str]:
    """(frontmatter, body) of a Markdown file."""
    if not text.startswith("---\n"):
        raise RecordError("no YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.rstrip().endswith("\n---"):
            end = len(text.rstrip()) - 4
        else:
            raise RecordError("frontmatter is not closed")
    try:
        data = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as error:
        raise RecordError(f"frontmatter is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise RecordError("frontmatter is not a mapping")
    return data, text[end + 5 :]


def dump_frontmatter(ref: Reference) -> str:
    body = yaml.safe_dump(ref.to_dict(), sort_keys=False, allow_unicode=True, width=100)
    return f"---\n{body}---\n"


def render(ref: Reference, fulltext: str = "", notes: str = "") -> str:
    """A complete new record file."""
    lines = [f"# {ref.title}", ""]
    if ref.abstract:
        lines += ["> [!abstract]", *(f"> {line}" for line in ref.abstract.splitlines()), ""]
    lines += [FULLTEXT_START, fulltext.strip(), FULLTEXT_END, "", "## Notes", ""]
    if notes:
        lines += [notes.strip(), ""]
    return dump_frontmatter(ref) + "\n".join(lines)


def read(path: Path) -> tuple[Reference, str]:
    data, body = split(path.read_text(encoding="utf-8"))
    return Reference.from_dict(data), body


def fulltext(body: str) -> str:
    start, end = body.find(FULLTEXT_START), body.find(FULLTEXT_END)
    if start == -1 or end == -1 or end < start:
        return ""
    return body[start + len(FULLTEXT_START) : end].strip()


def update_metadata(path: Path, ref: Reference) -> None:
    """Rewrite the frontmatter only; the body (full text and notes) stays as it is."""
    _, body = split(path.read_text(encoding="utf-8"))
    _write(path, dump_frontmatter(ref) + body)


def update_fulltext(path: Path, text: str) -> None:
    """Replace what is between the markers; everything else stays as it is."""
    content = path.read_text(encoding="utf-8")
    start, end = content.find(FULLTEXT_START), content.find(FULLTEXT_END)
    if start == -1 or end == -1 or end < start:
        raise RecordError(f"{path.name}: full-text markers are missing")
    new = content[: start + len(FULLTEXT_START)] + "\n" + text.strip() + "\n" + content[end:]
    _write(path, new)


def write_new(path: Path, ref: Reference, fulltext: str = "") -> None:
    if path.exists():
        raise FileExistsError(path)
    _write(path, render(ref, fulltext))


def _write(path: Path, content: str) -> None:
    """Write through a temporary file, so a crash never leaves a half-written record."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
