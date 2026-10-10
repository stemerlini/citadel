"""Entry point: `citadel` or `python -m citadel`.

    citadel                 open the app (Phase 5; for now prints a pointer)
    citadel init            create the config and the library folders that are missing
    citadel doctor          check paths, config, library layout and the converter
    citadel topics          list the topic tree
    citadel reindex         bring the search index in line with the library files
    citadel list            list references (filter by topic, tag, type, status)
    citadel search QUERY    full-text search
    citadel get CITEKEY     one reference, with its paths
    citadel identify PDF…   find a PDF's metadata (dry run: nothing is moved)
    citadel ingest          file everything waiting in the inbox (or send it to review)
    citadel watch           keep ingesting the inbox (for the systemd user service)
    citadel add X           add a PDF, doi:…, or arxiv:… to the library
    citadel review          list items waiting for review; accept or reject them
    citadel --version

Every command accepts --json, for agents and scripts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def emit(data: dict, as_json: bool, text: str) -> None:
    print(json.dumps(data, indent=2) if as_json else text)


def cmd_init(args: argparse.Namespace) -> int:
    from . import config as config_module
    from . import paths
    from .library import Library

    config_created = config_module.write_default(
        library=Path(args.library).expanduser() if args.library else None
    )
    config = config_module.Config.load()
    library = Library(config.library_path)
    report = library.init()

    data = {
        "config": str(paths.CONFIG_FILE),
        "config_created": config_created,
        "library": str(library.root),
        "topics_file_created": report.topics_file_created,
        "created": report.created,
    }
    lines = [
        f"Config:  {paths.CONFIG_FILE}" + (" (created)" if config_created else ""),
        f"Library: {library.root}",
    ]
    if report.topics_file_created:
        lines.append(f"  created {library.topics_file.name}")
    lines += [f"  created {name}/" for name in report.created]
    if not report.created and not report.topics_file_created:
        lines.append("  nothing to do: the library is already set up")
    emit(data, args.json, "\n".join(lines))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import __version__, paths
    from .config import Config
    from .library import Library

    config = Config.load()
    library = Library(config.library_path)
    problems = library.check()
    if not paths.CONFIG_FILE.exists():
        problems.insert(0, f"no config at {paths.CONFIG_FILE} (run `citadel init`)")
    converter = config.convert_python
    vault = config.vault_path
    data = {
        "version": __version__,
        "app_root": str(paths.ROOT),
        "config": str(paths.CONFIG_FILE),
        "index": str(paths.INDEX_FILE),
        "library": str(library.root),
        "library_ok": not problems,
        "problems": problems,
        "vault": str(vault) if vault else None,
        "converter": str(converter),
        "converter_installed": converter.exists(),
        "gpu": shutil.which("nvidia-smi") is not None,
    }
    status = "ok" if not problems else f"{len(problems)} problem(s)"
    lines = [
        f"citadel {__version__}",
        f"App data:  {paths.ROOT}",
        f"Config:    {paths.CONFIG_FILE}",
        f"Library:   {library.root} [{status}]",
        *(f"  - {p}" for p in problems),
        f"Vault:     {vault or 'not set (Obsidian links disabled)'}",
        f"Converter: {converter} "
        f"[{'installed' if converter.exists() else 'not installed yet (Phase 4)'}]",
        f"GPU:       {'nvidia-smi found' if data['gpu'] else 'none found (Marker will use CPU)'}",
    ]
    emit(data, args.json, "\n".join(lines))
    return 0 if not problems else 1


def cmd_topics(args: argparse.Namespace) -> int:
    from .config import Config
    from .library import Library

    tree = Library(Config.load().library_path).topics()
    data = {
        "topics": [
            {"path": t.path, "description": t.description, "keywords": list(t.keywords)}
            for t in tree
        ]
    }
    text = "\n".join(f"{'  ' * (t.depth - 1)}{t.name}/" for t in tree)
    emit(data, args.json, text)
    return 0


def open_index(refresh: bool = True):
    """The index, brought up to date with the files first (cheap: only changed files
    are read). Problems are reported on stderr so --json output stays clean."""
    from . import paths
    from .config import Config
    from .index import Index

    index = Index(paths.INDEX_FILE, Config.load().library_path)
    if refresh:
        for problem in index.reindex().problems:
            print(f"warning: {problem}", file=sys.stderr)
    return index


def ref_line(row: dict) -> str:
    year = row["year"] or "n.d."
    return f"{row['citekey']:<32} {year!s:<5} {row['first_author'][:18]:<18} {row['title']}"


def cmd_reindex(args: argparse.Namespace) -> int:
    from . import paths
    from .config import Config
    from .index import Index

    index = Index(paths.INDEX_FILE, Config.load().library_path)
    report = index.reindex(full=args.full)
    stats = index.stats()
    index.close()
    data = {**report.as_dict(), "references": stats["references"]}
    lines = [
        f"{stats['references']} references indexed: {len(report.added)} added, "
        f"{len(report.updated)} updated, {len(report.removed)} removed, "
        f"{report.unchanged} unchanged",
        *(f"  problem: {p}" for p in report.problems),
    ]
    emit(data, args.json, "\n".join(lines))
    return 0 if not report.problems else 1


def cmd_list(args: argparse.Namespace) -> int:
    index = open_index()
    rows = index.list(
        topic=args.topic,
        tag=args.tag,
        type=args.type,
        status=args.status,
        sort=args.sort,
        limit=args.limit,
    )
    index.close()
    emit({"references": rows}, args.json, "\n".join(map(ref_line, rows)) or "No references.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    index = open_index()
    rows = index.search(" ".join(args.query), limit=args.limit)
    index.close()
    text = "\n".join(
        f"{ref_line(r)}\n    {r['snippet']}" if r["snippet"] else ref_line(r) for r in rows
    )
    emit({"query": " ".join(args.query), "results": rows}, args.json, text or "No matches.")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    index = open_index()
    row = index.get(args.citekey)
    root = index.root
    index.close()
    if row is None:
        emit({"error": f"no reference {args.citekey}"}, args.json, f"No reference {args.citekey}.")
        return 1
    folder = root / row["folder"]
    name = folder.name
    row["paths"] = {
        "folder": str(folder),
        "pdf": str(folder / f"{name}.pdf") if row["has_pdf"] else None,
        "markdown": str(folder / f"{name}.md"),
    }
    data = row["data"]
    lines = [
        data["title"],
        "; ".join(data.get("authors", [])) or "(no authors)",
        f"{row['year'] or 'n.d.'} · {row['type']} · {row['topic'] or '(no topic)'} · "
        f"{row['status']}",
        *(
            f"{key}: {data[key]}"
            for key in ("journal", "doi", "arxiv", "isbn", "url")
            if data.get(key)
        ),
        f"tags: {', '.join(row['tags'])}" if row["tags"] else "",
        f"pdf: {row['paths']['pdf'] or '(none)'}",
        f"md:  {row['paths']['markdown']}",
    ]
    emit(row, args.json, "\n".join(line for line in lines if line))
    return 0


def cmd_identify(args: argparse.Namespace) -> int:
    from . import naming
    from .config import Config
    from .identify import identify
    from .sources import Http

    config = Config.load()
    http = Http(config.metadata.contact_email)
    results, lines = [], []
    try:
        for name in args.pdf:
            path = Path(name).expanduser()
            if not path.is_file():
                results.append({"file": str(path), "error": "not a file"})
                lines.append(f"{path}: not a file")
                continue
            found = identify(path, http)
            item = {"file": str(path), **found.as_dict()}
            meta = found.metadata
            if meta:
                item["basename"] = naming.basename(meta.year, meta.title, meta.authors)
                item["citekey"] = naming.citekey(meta.year, meta.title, meta.authors)
            results.append(item)
            lines.append(f"{path.name}")
            if meta:
                lines.append(
                    f"  -> {item['basename']}  [{found.method}, confidence {found.confidence:.2f}]"
                )
                lines.append(f"     {meta.title} ({meta.year or 'n.d.'})")
                if meta.doi:
                    lines.append(f"     doi {meta.doi}")
            else:
                lines.append("  -> not identified")
            if args.verbose:
                lines += [f"     · {e}" for e in found.evidence]
    finally:
        http.close()
    emit({"results": results}, args.json, "\n".join(lines))
    return 0


class Session:
    """Config, library, index, HTTP client and ingester, opened together."""

    def __init__(self):
        from . import paths
        from .config import Config
        from .index import Index
        from .ingest import Ingester
        from .library import Library
        from .sources import Http

        self.config = Config.load()
        self.library = Library(self.config.library_path)
        self.index = Index(paths.INDEX_FILE, self.library.root)
        self.index.reindex()
        self.http = Http(self.config.metadata.contact_email)
        self.ingester = Ingester(self.library, self.index, self.http, self.config)

    def close(self) -> None:
        self.http.close()
        self.index.close()


def result_line(result) -> str:
    name = result.file if result.file.startswith("doi:") else Path(result.file).name
    if result.status == "filed":
        return (
            f"filed   {result.folder}\n        {result.title[:90]}  "
            f"[metadata {result.confidence:.2f}, topic {result.topic_confidence:.2f}]"
        )
    if result.status in ("review", "dry-run"):
        what = "would file" if result.reason == "would be filed" else result.reason
        topic = f" → {result.topic} ({result.topic_confidence:.2f})" if result.topic else ""
        return f"{result.status:<7} {name}: {what}{topic}"
    return f"{result.status:<7} {name}: {result.reason}"


def cmd_ingest(args: argparse.Namespace) -> int:
    session = Session()
    try:
        if not session.library.exists():
            emit({"error": "no library"}, args.json, "No library: run `citadel init`.")
            return 1
        results = session.ingester.process_inbox(dry_run=args.dry_run)
    finally:
        session.close()
    text = "\n".join(map(result_line, results)) or "The inbox is empty."
    emit({"results": [r.as_dict() for r in results]}, args.json, text)
    return 1 if any(r.status == "error" for r in results) else 0


def cmd_watch(args: argparse.Namespace) -> int:
    import time

    session = Session()
    interval = args.interval or session.config.ingest.watch_interval
    print(f"Watching {session.library.inbox} every {interval}s (Ctrl+C to stop)", flush=True)
    try:
        while True:
            for result in session.ingester.process_inbox():
                stamp = time.strftime("%H:%M:%S")
                print(f"{stamp} {result_line(result)}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        session.close()


def parse_target(target: str) -> tuple[str, str]:
    """('pdf'|'doi'|'arxiv', value) for what `citadel add` was given."""
    from .identifiers import ARXIV_NEW, ARXIV_OLD, clean_doi, find_dois

    lowered = target.lower()
    if lowered.startswith("doi:") or (find_dois(target) and not Path(target).exists()):
        return "doi", clean_doi(target[4:] if lowered.startswith("doi:") else target)
    if lowered.startswith("arxiv:") or "arxiv.org/" in lowered:
        for pattern in (ARXIV_NEW, ARXIV_OLD):
            match = pattern.search(target if "arxiv" in lowered else f"arxiv:{target}")
            if match:
                return "arxiv", match.group(1)
    return "pdf", target


def cmd_add(args: argparse.Namespace) -> int:
    import tempfile

    from . import sources

    kind, value = parse_target(args.target)
    source = f"agent:{args.agent}" if args.agent else "add"
    session = Session()
    try:
        ingester = session.ingester
        if kind == "pdf":
            path = Path(value).expanduser()
            if not path.is_file():
                emit({"error": f"not a file: {path}"}, args.json, f"Not a file: {path}")
                return 1
            result = ingester.ingest(
                path, source=source, keep_source=not args.move, topic=args.topic
            )
        elif kind == "arxiv":
            meta = sources.arxiv(session.http, value)
            if meta is None:
                emit({"error": f"arXiv has no {value}"}, args.json, f"arXiv has no {value}.")
                return 1
            if meta.doi:  # the published version has richer metadata
                published = sources.crossref(session.http, meta.doi)
                if published:
                    published.arxiv = value
                    meta = published
            with tempfile.TemporaryDirectory(prefix="citadel-add-") as tmp:
                pdf = Path(tmp) / f"{value.replace('/', '_')}.pdf"
                session.http.download(f"https://arxiv.org/pdf/{value}", pdf)
                result = ingester.ingest(pdf, source=source, known=meta, topic=args.topic)
        else:
            result = add_doi(session, value, args.topic, source)
        if result.status == "filed":
            session.index.reindex()
    finally:
        session.close()
    emit(result.as_dict(), args.json, result_line(result))
    return 0 if result.status in ("filed", "review") else 1


def add_doi(session, doi: str, topic: str, source: str):
    """A reference from a DOI alone (no PDF): filed only when the topic is clear."""
    from . import sources
    from .ingest import Result

    label = f"doi:{doi}"
    meta = sources.crossref(session.http, doi)
    if meta is None:
        return Result(label, "error", "Crossref does not know this DOI")
    existing = session.index.find_doi(meta.doi)
    if existing:
        return Result(label, "error", f"already in the library as {existing}")
    sources.enrich(session.http, meta)
    confidence = 1.0
    if not topic:
        found = session.ingester.classifier.classify(meta.title, meta.abstract, meta.journal)
        topic, confidence = found.topic, found.confidence
        if not topic or confidence < session.config.ingest.topic_threshold:
            hint = f" (best guess: {topic}, {confidence:.2f})" if topic else ""
            return Result(
                doi,
                "error",
                f"topic unclear{hint}: add it again with --topic",
                title=meta.title,
                topic=topic,
                topic_confidence=confidence,
            )
    ref = session.ingester.file_metadata_only(meta, topic, source)
    return Result(
        doi,
        "filed",
        "metadata only (no PDF)",
        ref.citekey,
        ref.folder,
        ref.title,
        1.0,
        topic,
        confidence,
    )


def cmd_review(args: argparse.Namespace) -> int:
    session = Session()
    try:
        ingester = session.ingester
        if args.action == "list":
            items = ingester.review_items()
            data = {"items": [item.__dict__ for item in items]}
            lines = []
            for item in items:
                title = (item.metadata or {}).get("title", "")
                lines.append(f"{item.file}\n    {item.reason}")
                if title:
                    lines.append(f"    proposal: {title[:80]} [{item.confidence:.2f}]")
                if item.topic:
                    lines.append(f"    topic:    {item.topic} [{item.topic_confidence:.2f}]")
            emit(data, args.json, "\n".join(lines) or "Nothing to review.")
            return 0
        if not args.item:
            emit({"error": "which item?"}, args.json, "Name the item (file name or prefix).")
            return 1
        try:
            if args.action == "accept":
                result = ingester.accept(args.item, topic=args.topic, doi=args.doi, type=args.type)
            else:
                result = ingester.reject(args.item)
        except (LookupError, ValueError) as error:
            emit({"error": str(error)}, args.json, f"Error: {error}")
            return 1
    finally:
        session.close()
    emit(result.as_dict(), args.json, result_line(result))
    return 0


def main(argv: list[str] | None = None) -> None:
    from . import __version__

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print machine-readable JSON")

    parser = argparse.ArgumentParser(
        prog="citadel", description="Your reference library, in the terminal."
    )
    parser.add_argument("--version", action="version", version=f"citadel {__version__}")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser(
        "init", parents=[common], help="create the config and any missing library folders"
    )
    init.add_argument("--library", help="library folder (only used when creating the config)")
    init.set_defaults(func=cmd_init)
    sub.add_parser("doctor", parents=[common], help="check the setup").set_defaults(func=cmd_doctor)
    sub.add_parser("topics", parents=[common], help="list the topic tree").set_defaults(
        func=cmd_topics
    )

    reindex = sub.add_parser("reindex", parents=[common], help="update the search index")
    reindex.add_argument("--full", action="store_true", help="re-read every file")
    reindex.set_defaults(func=cmd_reindex)

    listing = sub.add_parser("list", parents=[common], help="list references")
    listing.add_argument("--topic", default="", help="topic folder (includes subtopics)")
    listing.add_argument("--tag", default="")
    listing.add_argument("--type", default="")
    listing.add_argument("--status", default="", choices=["", "unread", "reading", "read"])
    listing.add_argument("--sort", default="year", choices=["year", "author", "title", "added"])
    listing.add_argument("--limit", type=int)
    listing.set_defaults(func=cmd_list)

    search = sub.add_parser("search", parents=[common], help="full-text search")
    search.add_argument("query", nargs="+")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_search)

    get = sub.add_parser("get", parents=[common], help="show one reference")
    get.add_argument("citekey")
    get.set_defaults(func=cmd_get)

    ident = sub.add_parser(
        "identify", parents=[common], help="find a PDF's metadata (dry run: nothing is moved)"
    )
    ident.add_argument("pdf", nargs="+")
    ident.add_argument("-v", "--verbose", action="store_true", help="show what was tried")
    ident.set_defaults(func=cmd_identify)

    ingest = sub.add_parser("ingest", parents=[common], help="file what is in the inbox")
    ingest.add_argument("--dry-run", action="store_true", help="only say what would happen")
    ingest.set_defaults(func=cmd_ingest)

    watch = sub.add_parser("watch", help="keep ingesting the inbox")
    watch.add_argument("--interval", type=int, help="seconds between checks")
    watch.set_defaults(func=cmd_watch, json=False)

    add = sub.add_parser("add", parents=[common], help="add a PDF, doi:… or arxiv:…")
    add.add_argument("target", help="a PDF path, doi:10.…, arxiv:2101.12345 or an arXiv URL")
    add.add_argument("--topic", default="", help="topic folder (skips classification)")
    add.add_argument("--move", action="store_true", help="move the PDF instead of copying")
    add.add_argument("--agent", default="", help="name of the agent adding it (recorded)")
    add.set_defaults(func=cmd_add)

    review = sub.add_parser("review", parents=[common], help="settle items waiting in _review/")
    review.add_argument("action", nargs="?", default="list", choices=["list", "accept", "reject"])
    review.add_argument("item", nargs="?", help="file name (or a unique prefix)")
    review.add_argument("--topic", default="", help="accept: topic folder")
    review.add_argument("--doi", default="", help="accept: take the metadata from this DOI")
    review.add_argument("--type", default="", help="accept: document type (book, article…)")
    review.set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    if args.command is None:
        print("The Citadel TUI arrives in Phase 5. Try `citadel doctor` or `citadel --help`.")
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
