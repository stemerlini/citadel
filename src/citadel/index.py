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
from .library import Library
from .record import RecordError, Reference

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    citekey      TEXT PRIMARY KEY,  -- unique across libraries (see reindex)
    library      TEXT NOT NULL,     -- the library's name
    folder       TEXT NOT NULL,     -- relative to that library's root
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
    pdf_sha256   TEXT NOT NULL,     -- for spotting duplicates on ingest
    md_mtime     REAL NOT NULL,
    data         TEXT NOT NULL      -- the whole frontmatter, as JSON
);
CREATE INDEX IF NOT EXISTS refs_topic ON refs(topic);
CREATE INDEX IF NOT EXISTS refs_doi ON refs(doi);
CREATE INDEX IF NOT EXISTS refs_sha ON refs(pdf_sha256);
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
    "citekey, library, folder, title, authors, first_author, year, type, topic, status, "
    "added, doi, arxiv, has_pdf, has_fulltext"
)


@dataclass
class ReindexReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    problems: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)  # libraries left as they were

    def as_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "problems": self.problems,
            "unreachable": self.unreachable,
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
    """One index over every library. `libraries` is a folder (one library) or a list
    of Library, the default one first."""

    def __init__(self, file: Path, libraries: Path | Library | list[Library]):
        if isinstance(libraries, Path):
            libraries = [Library(libraries)]
        elif isinstance(libraries, Library):
            libraries = [libraries]
        self.libraries = list(libraries)
        self.file = file
        self.root = self.libraries[0].root  # the default library
        self.roots = {library.name: library.root for library in self.libraries}
        file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(file, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        # Readers (the app, MCP, the CLI) are never locked out by a long reindex.
        self.conn.execute("PRAGMA journal_mode = WAL")
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

    def reindex(self, full: bool = False, progress=None) -> ReindexReport:
        """Bring the index in line with the files of every library. Only changed files
        are re-read, unless full is set. progress(citekey) is called as each reference
        is confirmed, and each re-read one is committed at once.

        A citekey is unique across libraries: when two libraries use the same one, the
        library listed first keeps it and the other reference is indexed as
        <citekey>-<library> (its file is left as it is, and the clash reported)."""
        report = ReindexReport()
        reachable = [library for library in self.libraries if library.root.is_dir()]
        if not reachable:
            # The OneDrive mount is down, or the library moved: that is not the same
            # as every reference having been deleted, so keep the index as it is.
            report.problems += [
                f"library folder {library.root} is not reachable" for library in self.libraries
            ]
            return report
        known = {
            row["citekey"]: (row["library"], row["folder"], row["md_mtime"], row["own"])
            for row in self.conn.execute(
                "SELECT citekey, library, folder, md_mtime, "
                "json_extract(data, '$.citekey') AS own FROM refs"
            )
        }
        by_place = {(lib, folder): key for key, (lib, folder, _, _) in known.items()}
        seen: dict[str, tuple[str, str]] = {}  # citekey -> (library, folder), this run

        with self.conn:
            for library in self.libraries:
                if library not in reachable:
                    report.unreachable.append(f"{library.name}: {library.root} is not reachable")
                    for key, (lib, folder, _, _) in known.items():
                        if lib == library.name:  # kept until the library is back
                            seen.setdefault(key, (lib, folder))
                            if progress:
                                progress(key)
                    continue
                self._reindex_library(library, known, by_place, seen, full, report, progress)
            for key in known.keys() - seen.keys():
                self._delete(key)
                report.removed.append(key)
        return report

    def _reindex_library(self, library, known, by_place, seen, full, report, progress) -> None:
        root, name = library.root, library.name
        for path in scan(root):
            folder = path.parent.relative_to(root).as_posix()
            where = folder if name == self.libraries[0].name else f"{name}:{folder}"
            mtime = path.stat().st_mtime
            old_key = by_place.get((name, folder))
            if (
                not full
                and old_key
                and old_key not in seen
                and known[old_key][2] == mtime
                and (old_key == known[old_key][3] or known[old_key][3] in seen)
            ):
                seen[old_key] = (name, folder)
                report.unchanged += 1
                if progress:
                    progress(old_key)
                if old_key != known[old_key][3]:
                    report.problems.append(_clash(where, known[old_key][3], seen, old_key))
                continue
            try:
                ref, body = record.read(path)
            except (OSError, UnicodeDecodeError, RecordError) as error:
                report.problems.append(f"{where}: {error}")
                continue
            key = ref.citekey
            if key in seen:
                other, other_folder = seen[key]
                if other == name:
                    report.problems.append(
                        f"{where}: citekey {key} is already used by {other_folder}"
                    )
                    continue
                key = f"{ref.citekey}-{_slug(name)}"
                report.problems.append(_clash(where, ref.citekey, seen, key))
                if key in seen:
                    continue
            seen[key] = (name, folder)
            topic = path.parent.parent.relative_to(root).as_posix()
            topic = "" if topic == "." else topic
            if ref.topic != topic:
                report.problems.append(
                    f"{where}: frontmatter topic is '{ref.topic}' but the folder is in "
                    f"'{topic}' (indexed where it is)"
                )
            if old_key and old_key != key and old_key not in seen:
                self._delete(old_key)  # citekey edited by hand
            self._store(ref, body, name, folder, topic, mtime, key)
            (report.updated if key in known else report.added).append(key)
            if progress:
                self.conn.commit()
                progress(key)

    def add(self, md_path: Path) -> str:
        """Index one record right away (just filed), so the next file in the same batch
        sees it: duplicates are caught and its names are taken."""
        ref, body = record.read(md_path)
        library = self.library_of(md_path)
        folder = md_path.parent.relative_to(library.root).as_posix()
        topic = md_path.parent.parent.relative_to(library.root).as_posix()
        with self.conn:
            self._store(
                ref,
                body,
                library.name,
                folder,
                "" if topic == "." else topic,
                md_path.stat().st_mtime,
            )
        return ref.citekey

    def library_of(self, path: Path) -> Library:
        """The library a file or folder is in."""
        for library in sorted(self.libraries, key=lambda lib: len(lib.root.parts), reverse=True):
            if path.is_relative_to(library.root):
                return library
        raise ValueError(f"{path} is in none of the libraries")

    def path(self, row: dict) -> Path:
        """Where a reference's folder is (row: from get, list or search)."""
        return self.roots.get(row["library"], self.root) / row["folder"]

    def tags(self) -> dict[str, str]:
        """Library name -> its short tag, for lists."""
        return {library.name: library.tag for library in self.libraries}

    def _store(
        self,
        ref: Reference,
        body: str,
        library: str,
        folder: str,
        topic: str,
        mtime: float,
        key: str = "",
    ) -> None:
        """key: the citekey to index it under, when it is not its own (a clash)."""
        key = key or ref.citekey
        directory = self.roots[library] / folder
        text = record.fulltext(body)
        self._delete(key)
        self.conn.execute(
            f"INSERT INTO refs ({LIST_COLUMNS}, pdf_sha256, md_mtime, data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                library,
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
                ref.pdf_sha256,
                mtime,
                json.dumps(ref.to_dict(), ensure_ascii=False, default=str),
            ),
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO tags VALUES (?, ?)", [(key, tag) for tag in ref.tags]
        )
        self.conn.execute(
            "INSERT INTO fts VALUES (?,?,?,?,?,?)",
            (
                key,
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

    def find_doi(self, doi: str, library: str = "") -> str | None:
        """The reference with this DOI (in that library only, when given)."""
        row = self.conn.execute(
            "SELECT citekey FROM refs WHERE doi = ? AND (? = '' OR library = ?)",
            (doi.lower(), library, library),
        ).fetchone()
        return row["citekey"] if row else None

    def find_sha256(self, sha256: str, library: str = "") -> str | None:
        row = self.conn.execute(
            "SELECT citekey FROM refs WHERE pdf_sha256 = ? AND pdf_sha256 != '' "
            "AND (? = '' OR library = ?)",
            (sha256, library, library),
        ).fetchone()
        return row["citekey"] if row else None

    def clashes(self) -> list[dict]:
        """References indexed under another citekey than their own (see reindex)."""
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT citekey, library, folder, json_extract(data, '$.citekey') AS own "
                "FROM refs WHERE citekey != json_extract(data, '$.citekey') ORDER BY citekey"
            )
        ]

    def citekeys(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT citekey FROM refs")}

    def basenames(self) -> set[str]:
        """Folder names in use (a basename is unique across the whole library)."""
        return {row[0].rsplit("/", 1)[-1] for row in self.conn.execute("SELECT folder FROM refs")}

    def list(
        self,
        topic: str = "",
        tag: str = "",
        type: str = "",
        status: str = "",
        sort: str = "year",
        limit: int | None = None,
        library: str = "",
    ) -> list[dict]:
        """References filtered by topic (including subtopics), tag, type, status and
        library (its name)."""
        where, params = [], []
        if library:
            where.append("library = ?")
            params.append(library)
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

    def search(self, query: str, limit: int = 50, library: str = "") -> list[dict]:
        """Full-text search, best matches first. Words are ANDed; the last one is a prefix."""
        match = fts_query(query)
        if not match:
            return []
        weights = ", ".join(str(w) for w in WEIGHTS)
        rows = self.conn.execute(
            f"SELECT r.citekey, r.library, r.folder, r.title, r.authors, r.first_author, "
            f"r.year, r.type, r.topic, r.status, r.added, r.doi, r.arxiv, r.has_pdf, "
            f"r.has_fulltext, "
            f"snippet(fts, 5, '[', ']', '…', 12) AS snippet, bm25(fts, {weights}) AS score "
            f"FROM fts JOIN refs r ON r.citekey = fts.citekey "
            f"WHERE fts MATCH ? AND (? = '' OR r.library = ?) ORDER BY score LIMIT ?",
            (match, library, library, limit),
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


def _clash(where: str, citekey: str, seen: dict, key: str) -> str:
    return (
        f"{where}: citekey {citekey} is also used in library {seen[citekey][0]}; "
        f"indexed as {key} (give one of them another citekey)"
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "library"


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
