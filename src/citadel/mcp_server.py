"""Citadel as an MCP server (stdio), for Claude Code and other agents.

    citadel mcp          # started by the agent, not by hand

Tools are small and typed: the read-only ones (search, get, read, cite,
list_references, topics) can be allowed once, while `add` changes the library
and reaches the network, so clients can keep asking before it runs. Full text is
served in bounded chunks or by section, so a 200-page standard never floods an
agent's context. stdout carries the protocol: nothing here may print to it.
"""

from __future__ import annotations

import re
import time
from collections import Counter

import mcp.types as types
from mcp.server.mcpserver import MCPServer

from . import __version__, cite, edit, paths, record
from .config import Config
from .index import Index
from .library import Library

INSTRUCTIONS = """\
Citadel is the user's own reference library (papers, books, standards, slides), with
metadata, topic folders, PDFs and Markdown full text.
- Before searching the web for literature, search Citadel: the user may already have it.
- Cite with citekeys, Pandoc style: [@tylka1997creme96]. `cite` gives formatted
  references or BibTeX for them.
- Read papers through `get` (metadata + outline) and then `read` (a section or a chunk);
  do not open the PDF when Markdown exists.
- When you find or download a paper the user will want to keep, offer to `add` it
  (PDF path, DOI or arXiv id): it is then identified, named and filed.
"""

READ_LIMIT = 12_000  # characters per `read` call unless asked otherwise
REFRESH_SECONDS = 20  # how often the index is brought in line with the files


class State:
    """The library and its index, refreshed when they may have changed."""

    def __init__(self) -> None:
        self.config = Config.load()
        self.library = Library(self.config.library_path)
        self.index = Index(paths.INDEX_FILE, self.library.root)
        self.refreshed = 0.0

    def fresh(self) -> Index:
        if time.monotonic() - self.refreshed > REFRESH_SECONDS:
            self.index.reindex()
            self.refreshed = time.monotonic()
        return self.index


def build_server() -> MCPServer:
    state = State()
    server = MCPServer("citadel", title="Citadel", version=__version__, instructions=INSTRUCTIONS)
    read_only = types.ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(annotations=read_only)
    def search(query: str, limit: int = 10, topic: str = "") -> dict:
        """Full-text search of the library (titles, authors, abstracts, tags and the
        converted text), best matches first. `topic` limits it to a topic folder and its
        subtopics, e.g. "space-engineering/radiation-effects"."""
        index = state.fresh()
        rows = index.search(query, limit=max(limit * 3, 30) if topic else limit)
        if topic:
            rows = [r for r in rows if r["topic"] == topic or r["topic"].startswith(f"{topic}/")][
                :limit
            ]
        return {"query": query, "results": [_summary(r) | {"snippet": r["snippet"]} for r in rows]}

    @server.tool(annotations=read_only)
    def get(citekey: str) -> dict:
        """Everything about one reference: metadata, abstract, files, and an outline of
        its Markdown full text (section headings with their sizes) to choose what to
        `read`."""
        index = state.fresh()
        row = index.get(citekey)
        if row is None:
            return {"error": f"no reference {citekey}; use search"}
        folder = index.root / row["folder"]
        data = row["data"]
        body = _body(folder)
        text = record.fulltext(body)
        return {
            **{
                k: data[k]
                for k in (
                    "citekey",
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
                    "topic",
                    "tags",
                    "status",
                    "attachments",
                )
                if data.get(k)
            },
            "files": {
                "folder": str(folder),
                "pdf": str(folder / f"{folder.name}.pdf") if row["has_pdf"] else None,
                "markdown": str(folder / f"{folder.name}.md"),
            },
            "markdown": {
                "available": bool(text),
                "characters": len(text),
                "outline": _outline(text),
            }
            if text
            else {
                "available": False,
                "note": "not converted yet (the user can run `citadel convert`)",
            },
        }

    @server.tool(annotations=read_only)
    def read(citekey: str, section: str = "", start: int = 0, max_chars: int = READ_LIMIT) -> dict:
        """Read a reference's Markdown full text. Give `section` (a heading from `get`'s
        outline, e.g. "3 Results" or just "3") to read that section, or `start` to page
        through the text; `next_start` tells where the following chunk begins. Equations
        are LaTeX; figures are referenced as figures/… images."""
        index = state.fresh()
        row = index.get(citekey)
        if row is None:
            return {"error": f"no reference {citekey}; use search"}
        text = record.fulltext(_body(index.root / row["folder"]))
        if not text:
            return {
                "error": f"{citekey} has no Markdown yet",
                "abstract": row["data"].get("abstract", ""),
            }
        max_chars = max(1000, min(max_chars, 50_000))
        if section:
            span = _section_span(text, section)
            if span is None:
                return {"error": f"no section {section!r}", "outline": _outline(text)}
            begin, end = span
            begin += start
        else:
            begin, end = start, len(text)
        chunk_end = min(end, begin + max_chars)
        return {
            "citekey": citekey,
            "section": section or None,
            "start": begin,
            "end": chunk_end,
            "total": len(text),
            "text": text[begin:chunk_end],
            "next_start": (chunk_end - (span[0] if section else 0)) if chunk_end < end else None,
        }

    @server.tool(name="cite", annotations=read_only)
    def cite_references(citekeys: list[str], style: str = "reference") -> dict:
        """Citations for references: style "reference" (formatted, APA-like), "bibtex"
        (entries for LaTeX/Pandoc), or "key" (the citekeys themselves)."""
        if style not in cite.FORMATS:
            return {"error": f"style must be one of {', '.join(cite.FORMATS)}"}
        index = state.fresh()
        found, missing = [], []
        for key in citekeys:
            try:
                ref, _ = edit.load(index, key)
            except edit.EditError:
                missing.append(key)
                continue
            found.append(cite.format_ref(ref, style).rstrip("\n"))
        return {"style": style, "citations": found, "missing": missing}

    @server.tool(annotations=read_only)
    def list_references(
        topic: str = "",
        tag: str = "",
        type: str = "",
        status: str = "",
        sort: str = "added",
        limit: int = 50,
    ) -> dict:
        """List references, filtered by topic folder (subtopics included), tag, type
        (article, book, standard, slides…) or reading status (unread, reading, read).
        sort: added (newest first), year, author or title."""
        index = state.fresh()
        try:
            rows = index.list(
                topic=topic, tag=tag, type=type, status=status, sort=sort, limit=limit
            )
        except ValueError as error:
            return {"error": str(error)}
        return {"count": len(rows), "references": [_summary(r) for r in rows]}

    @server.tool(annotations=read_only)
    def topics() -> dict:
        """The library's topic folders with descriptions and how many references each
        holds (subtopics included): useful to browse, or to pick a topic for `add`."""
        index = state.fresh()
        counts: Counter[str] = Counter()
        for row in index.list():
            topic = row["topic"]
            while topic:
                counts[topic] += 1
                topic = topic.rsplit("/", 1)[0] if "/" in topic else ""
        return {
            "topics": [
                {"path": t.path, "description": t.description, "references": counts[t.path]}
                for t in state.library.topics()
            ]
        }

    @server.tool(
        annotations=types.ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        )
    )
    def add(target: str, topic: str = "", agent: str = "claude") -> dict:
        """Add a paper to the library: a local PDF/office file path (copied, never moved),
        a DOI ("doi:10.…" or bare), or an arXiv id ("arxiv:2101.12345", downloaded).
        Citadel identifies, names and files it; if unsure it waits in the user's review
        queue. Give `topic` (see `topics`) when you know it. Duplicates are refused."""
        from .ingest import Ingester
        from .sources import Http, SourceError

        index = state.fresh()
        http = Http(state.config.metadata.contact_email)
        try:
            result = Ingester(state.library, index, http, state.config).add(
                target, topic=topic, source=f"agent:{agent}"
            )
        except (OSError, ValueError, SourceError) as error:
            return {"status": "error", "reason": str(error)}
        finally:
            http.close()
        state.refreshed = 0.0
        return {k: v for k, v in result.as_dict().items() if v not in ("", 0.0, None)}

    return server


def _summary(row: dict) -> dict:
    authors = row["authors"].split("; ") if row["authors"] else []
    return {
        "citekey": row["citekey"],
        "title": row["title"],
        "year": row["year"],
        "authors": authors[:3] + (["et al."] if len(authors) > 3 else []),
        "type": row["type"],
        "topic": row["topic"],
        "has_markdown": bool(row["has_fulltext"]),
        "has_pdf": bool(row["has_pdf"]),
    }


def _body(folder) -> str:
    try:
        return record.split((folder / f"{folder.name}.md").read_text(encoding="utf-8"))[1]
    except (OSError, record.RecordError):
        return ""


HEADING = re.compile(r"^(#{1,6}) +(.+?)[ \t]*$", re.MULTILINE)


def _headings(text: str) -> list[tuple[int, int, str]]:
    """(position, level, heading) of every heading outside display maths."""
    maths = [(m.start(), m.end()) for m in re.finditer(r"\$\$.+?\$\$", text, re.DOTALL)]
    return [
        (m.start(), len(m.group(1)), m.group(2).strip())
        for m in HEADING.finditer(text)
        if not any(a <= m.start() < b for a, b in maths)
    ]


def _outline(text: str) -> list[dict]:
    heads = _headings(text)
    outline = []
    for i, (pos, level, heading) in enumerate(heads):
        end = next((p for p, lv, _ in heads[i + 1 :] if lv <= level), len(text))
        outline.append({"section": heading, "level": level, "characters": end - pos})
    return outline


def _section_span(text: str, wanted: str) -> tuple[int, int] | None:
    """Where a section starts and ends (its subsections included). Matches the whole
    heading or its number ("3", "2.1") or a case-insensitive prefix of its title."""
    wanted_l = wanted.strip().lower().rstrip(".")
    heads = _headings(text)
    for i, (pos, level, heading) in enumerate(heads):
        h = heading.lower()
        number = h.split()[0].rstrip(".") if h.split() else ""
        title = h[len(number) :].strip() if number[:1].isdigit() else h
        if wanted_l in (h, number) or (len(wanted_l) > 3 and title.startswith(wanted_l)):
            end = next((p for p, lv, _ in heads[i + 1 :] if lv <= level), len(text))
            return pos, end
    return None


def main() -> None:
    build_server().run("stdio")
