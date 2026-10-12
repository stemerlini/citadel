"""The library folder on disk: special folders, topic folders, and their health.

<library>/
├── _inbox/        drop anything here
├── _review/       low-confidence items wait here
├── _attachments/  files that are not references
├── topics.toml    the topic tree (folders below follow it)
└── <topic>/…/<reference folder>/
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from . import topics as topics_module
from .topics import Topic, TopicError

INBOX = "_inbox"
REVIEW = "_review"
ATTACHMENTS = "_attachments"
SPECIAL = (INBOX, REVIEW, ATTACHMENTS)
TOPICS_FILE = "topics.toml"
# Fingerprint of the default topics.toml the library's copy was made from: while the
# copy still matches it, the user has not edited it and it can follow new defaults.
TOPICS_ORIGIN = ".topics.default.sha256"


@dataclass
class InitReport:
    created: list[str] = field(default_factory=list)
    topics_file_created: bool = False
    topics_file_updated: bool = False
    topics_file_customised: bool = False  # edited by the user: left alone


class Library:
    def __init__(self, root: Path):
        self.root = root

    @property
    def inbox(self) -> Path:
        return self.root / INBOX

    @property
    def review(self) -> Path:
        return self.root / REVIEW

    @property
    def attachments(self) -> Path:
        return self.root / ATTACHMENTS

    @property
    def topics_file(self) -> Path:
        return self.root / TOPICS_FILE

    def exists(self) -> bool:
        return self.root.is_dir()

    def topics(self) -> list[Topic]:
        """The library's topic tree, or the default one before the library is set up."""
        file = self.topics_file if self.topics_file.exists() else None
        return topics_module.load(file)

    def init(self) -> InitReport:
        """Create whatever is missing. Never moves, renames or deletes anything."""
        report = InitReport()
        self.root.mkdir(parents=True, exist_ok=True)
        default = topics_module.DEFAULT_FILE.read_bytes()
        origin = self.root / TOPICS_ORIGIN
        if not self.topics_file.exists():
            self._install_topics(default)
            report.topics_file_created = True
        elif self.topics_file.read_bytes() != default:
            current = _sha(self.topics_file.read_bytes())
            if origin.exists() and origin.read_text().strip() == current:
                self._install_topics(default)  # unedited copy of an older default
                report.topics_file_updated = True
            else:
                report.topics_file_customised = True
        for name in (*SPECIAL, *(t.path for t in self.topics())):
            folder = self.root / name
            if not folder.is_dir():
                folder.mkdir(parents=True)
                report.created.append(name)
        return report

    def _install_topics(self, content: bytes) -> None:
        temporary = self.topics_file.with_name(f".{TOPICS_FILE}.tmp")
        temporary.write_bytes(content)
        temporary.replace(self.topics_file)
        (self.root / TOPICS_ORIGIN).write_text(_sha(content) + "\n")

    def check(self) -> list[str]:
        """Problems with the library layout, as readable sentences (empty = healthy)."""
        if not self.exists():
            return [f"library folder {self.root} does not exist (run `citadel init`)"]
        problems = []
        try:
            tree = self.topics()
        except (OSError, TopicError) as error:
            return [f"{TOPICS_FILE}: {error}"]
        for name in (*SPECIAL, *(t.path for t in tree)):
            if not (self.root / name).is_dir():
                problems.append(f"missing folder {name}/ (run `citadel init`)")
        return problems


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
