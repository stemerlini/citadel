"""Entry point: `citadel` or `python -m citadel`.

    citadel                 open the app (Phase 5; for now prints a pointer)
    citadel init            create the config and the library folders that are missing
    citadel doctor          check paths, config, library layout and the converter
    citadel topics          list the topic tree
    citadel reindex         bring the search index in line with the library files
    citadel list            list references (filter by topic, tag, type, status)
    citadel search QUERY    full-text search
    citadel get CITEKEY     one reference, with its paths
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

    args = parser.parse_args(argv)
    if args.command is None:
        print("The Citadel TUI arrives in Phase 5. Try `citadel doctor` or `citadel --help`.")
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
