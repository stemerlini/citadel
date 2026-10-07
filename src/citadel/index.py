"""The search index: a SQLite cache of every record in the library.

The Markdown files are the truth; this database can be deleted at any time and
rebuilt with `citadel reindex`. It lives in the app's data folder, never on the
OneDrive mount (SQLite is not safe on rclone's FUSE filesystem).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import record
from .record import RecordError, Reference

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    citekey      TEXT PRIMARY KEY,
    folder       TEXT NOT NULL,     -- relative to the library root
    title        TEXT NOT NULL,
    authors      TEXT NOT NULL,     -- "Surname, Given; Surname, Given"
    first_author TEXT NOT NULL,
    year         INTEGER,
    type         TEXT NOT NULL,
    topic        TEXT NOT NULL,     -- where the folder actually is
    status       TEXT NOT NULL,
    added        TEXT NOT NULL,
    doi          TEXT NOT NULL,
    arxiv        TEXT NOT NULL,
    has_pdf      INTEGER NOT NULL,
    has_fulltext INTEGER NOT NULL,
    md_mtime     REAL NOT NULL,
    data         TEXT NOT NULL      -- the whole frontmatter, as JSON
);
CREATE INDEX IF NOT EXISTS refs_topic ON refs(topic);
CREATE INDEX IF NOT EXISTS refs_doi ON refs(doi);
CREATE TABLE IF NOT EXISTS tags (
    citekey TEXT NOT NULL REFERENCES refs(citekey) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (citekey, tag)
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    citekey UNINDEXED, title, authors, abstract, tags, fulltext,
    tokenize = "porter unicode61 remove_diacritics 2"
);
"""

# bm25 column weights: citekey (unindexed), title, authors, abstract, tags, fulltext
WEIGHTS = (0.0, 10.0, 5.0, 3.0, 5.0, 1.0)
LIST_COLUMNS = (
    "citekey, folder, title, authors, first_author, year, type, topic, status, "
    "added, doi, arxiv, has_pdf, has_fulltext"
)


@dataclass
class ReindexReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "problems": self.problems,
        }


def scan(root: Path):
    """Every record file in the library: <topic…>/<name>/<name>.md.

    Special folders (_inbox, _review, …) and hidden folders are skipped.
    """
    for directory, subdirs, files in os.walk(root):
        here = Path(directory)
        subdirs[:] = sorted(d for d in subdirs if not d.startswith(("_", ".")) and d != "figures")
        if here != root and f"{here.name}.md" in files:
            yield here / f"{here.name}.md"
            subdirs[:] = []  # a reference folder holds no further references


class Index:
    def __init__(self, file: Path, library_root: Path):
        self.file = file
        self.root = library_root
        file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self._drop()  # a cache: rebuild instead of migrating
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.conn.close()

    def _drop(self) -> None:
        self.conn.executescript(
            "DROP TABLE IF EXISTS tags; DROP TABLE IF EXISTS fts; DROP TABLE IF EXISTS refs;"
        )

    # -- building ------------------------------------------------------------

    def reindex(self, full: bool = False) -> ReindexReport:
        """Bring the index in line with the files. Only changed files are re-read,
        unless full is set."""
        report = ReindexReport()
        known = {
            row["citekey"]: (row["folder"], row["md_mtime"])
            for row in self.conn.execute("SELECT citekey, folder, md_mtime FROM refs")
        }
        by_folder = {folder: key for key, (folder, _) in known.items()}
        seen: dict[str, str] = {}  # citekey -> folder, this run

        with self.conn:
            for path in scan(self.root):
                folder = path.parent.relative_to(self.root).as_posix()
                mtime = path.stat().st_mtime
                old_key = by_folder.get(folder)
                if not full and old_key and known[old_key][1] == mtime:
                    seen[old_key] = folder
                    report.unchanged += 1
                    continue
                try:
                    ref, body = record.read(path)
                except (OSError, UnicodeDecodeError, RecordError) as error:
                    report.problems.append(f"{folder}: {error}")
                    continue
                if ref.citekey in seen:
                    report.problems.append(
                        f"{folder}: citekey {ref.citekey} is already used by {seen[ref.citekey]}"
                    )
                    continue
                seen[ref.citekey] = folder
                topic = path.parent.parent.relative_to(self.root).as_posix()
                topic = "" if topic == "." else topic
                if ref.topic != topic:
                    report.problems.append(
                        f"{folder}: frontmatter topic is '{ref.topic}' but the folder is in "
                        f"'{topic}' (indexed where it is)"
                    )
                if old_key and old_key != ref.citekey:
                    self._delete(old_key)  # citekey edited by hand
                self._store(ref, body, folder, topic, mtime)
                (report.updated if ref.citekey in known else report.added).append(ref.citekey)

            for key in known.keys() - seen.keys():
                self._delete(key)
                report.removed.append(key)
        return report

    def _store(self, ref: Reference, body: str, folder: str, topic: str, mtime: float) -> None:
        directory = self.root / folder
        text = record.fulltext(body)
        self._delete(ref.citekey)
        self.conn.execute(
            f"INSERT INTO refs ({LIST_COLUMNS}, md_mtime, data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ref.citekey,
                folder,
                ref.title,
                "; ".join(ref.authors),
                ref.first_author,
                ref.year,
                ref.type,
                topic,
                ref.status,
                ref.added,
                ref.doi.lower(),
                ref.arxiv,
                int((directory / f"{directory.name}.pdf").exists()),
                int(bool(text)),
                mtime,
                json.dumps(ref.to_dict(), ensure_ascii=False, default=str),
            ),
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO tags VALUES (?, ?)", [(ref.citekey, tag) for tag in ref.tags]
        )
        self.conn.execute(
            "INSERT INTO fts VALUES (?,?,?,?,?,?)",
            (
                ref.citekey,
                ref.title,
                " ; ".join(ref.authors),
                ref.abstract,
                " ".join(ref.tags),
                text,
            ),
        )

    def _delete(self, citekey: str) -> None:
        self.conn.execute("DELETE FROM refs WHERE citekey = ?", (citekey,))
        self.conn.execute("DELETE FROM fts WHERE citekey = ?", (citekey,))

    # -- reading -------------------------------------------------------------

    def get(self, citekey: str) -> dict | None:
        row = self.conn.execute(
            f"SELECT {LIST_COLUMNS}, data FROM refs WHERE citekey = ?", (citekey,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["data"] = json.loads(result["data"])
        result["tags"] = self._tags(citekey)
        return result

    def find_doi(self, doi: str) -> str | None:
        row = self.conn.execute("SELECT citekey FROM refs WHERE doi = ?", (doi.lower(),)).fetchone()
        return row["citekey"] if row else None

    def citekeys(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT citekey FROM refs")}

    def list(
        self,
        topic: str = "",
        tag: str = "",
        type: str = "",
        status: str = "",
        sort: str = "year",
        limit: int | None = None,
    ) -> list[dict]:
        """References filtered by topic (including subtopics), tag, type and status."""
        where, params = [], []
        if topic:
            where.append("(topic = ? OR topic LIKE ? ESCAPE '\\')")
            params += [topic, _like_prefix(topic) + "/%"]
        if tag:
            where.append("citekey IN (SELECT citekey FROM tags WHERE tag = ?)")
            params.append(tag)
        if type:
            where.append("type = ?")
            params.append(type)
        if status:
            where.append("status = ?")
            params.append(status)
        order = {
            "year": "year IS NULL, year DESC, first_author COLLATE NOCASE",
            "author": "first_author COLLATE NOCASE, year",
            "title": "title COLLATE NOCASE",
            "added": "added DESC, citekey",
        }.get(sort)
        if order is None:
            raise ValueError(f"unknown sort {sort!r}")
        sql = f"SELECT {LIST_COLUMNS} FROM refs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in self.conn.execute(sql, params)]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Full-text search, best matches first. Words are ANDed; the last one is a prefix."""
        match = fts_query(query)
        if not match:
            return []
        weights = ", ".join(str(w) for w in WEIGHTS)
        rows = self.conn.execute(
            f"SELECT r.citekey, r.folder, r.title, r.authors, r.first_author, r.year, r.type, "
            f"r.topic, r.status, r.added, r.doi, r.arxiv, r.has_pdf, r.has_fulltext, "
            f"snippet(fts, 5, '[', ']', '…', 12) AS snippet, bm25(fts, {weights}) AS score "
            f"FROM fts JOIN refs r ON r.citekey = fts.citekey "
            f"WHERE fts MATCH ? ORDER BY score LIMIT ?",
            (match, limit),
        )
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        count = self.conn.execute("SELECT count(*) FROM refs").fetchone()[0]
        topics = {
            row[0]: row[1]
            for row in self.conn.execute(
                "SELECT topic, count(*) FROM refs GROUP BY topic ORDER BY topic"
            )
        }
        return {"references": count, "by_topic": topics}

    def _tags(self, citekey: str) -> list[str]:
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT tag FROM tags WHERE citekey = ? ORDER BY tag", (citekey,)
            )
        ]


def fts_query(query: str) -> str:
    """Turn what a person types into a safe FTS5 query: every word must match,
    the last word may be a prefix (so results appear while typing)."""
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        return ""
    quoted = [f'"{t}"' for t in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
