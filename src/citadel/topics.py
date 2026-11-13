"""The topic tree: one table in topics.toml per folder in the library.

The library's own copy (<library>/topics.toml) is the one that counts once the
library exists; the copy shipped with Citadel is only the starting point.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("topics.toml")
MAX_DEPTH = 3
SEGMENT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class TopicError(ValueError):
    """The topic tree file is not valid."""


@dataclass(frozen=True)
class Topic:
    path: str
    description: str = ""
    keywords: tuple[str, ...] = field(default=())
    # Documents this topic claims whatever their words: by type (standard, slides…)
    # or by file name (globs, case-insensitive). See classify.py.
    types: tuple[str, ...] = field(default=())
    files: tuple[str, ...] = field(default=())

    @property
    def parent(self) -> str | None:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else None

    @property
    def depth(self) -> int:
        return self.path.count("/") + 1

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def parse(text: str) -> list[Topic]:
    """Read and validate a topics.toml. Raises TopicError with every problem found."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise TopicError(f"not valid TOML: {error}") from error

    topics, problems = [], []
    for path, table in data.items():
        if not isinstance(table, dict):
            problems.append(f"{path}: must be a table")
            continue
        segments = path.split("/")
        if len(segments) > MAX_DEPTH:
            problems.append(f"{path}: deeper than {MAX_DEPTH} levels")
        bad = [s for s in segments if not SEGMENT.match(s)]
        if bad:
            problems.append(f"{path}: use lowercase kebab-case ({', '.join(bad)})")
        lists = {}
        for key in ("keywords", "types", "files"):
            value = table.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f"{path}: {key} must be a list of strings")
                value = []
            lists[key] = tuple(value)
        topics.append(Topic(path, str(table.get("description", "")), **lists))

    known = {t.path for t in topics}
    for topic in topics:
        if topic.parent and topic.parent not in known:
            problems.append(f"{topic.path}: parent {topic.parent} is not listed")
    if problems:
        raise TopicError("; ".join(problems))
    return sorted(topics, key=lambda t: t.path)


def load(file: Path | None = None) -> list[Topic]:
    return parse((file or DEFAULT_FILE).read_text())
