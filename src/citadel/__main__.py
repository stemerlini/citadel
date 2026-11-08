"""Entry point: `citadel` or `python -m citadel`.

    citadel                 open the app
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
    citadel review          list items waiting for review; accept, reject or retry them
    citadel convert         convert filed PDFs to Markdown on the GPU (Marker)
    citadel cite KEY…       formatted references, BibTeX entries or citekeys
    citadel duplicates      list duplicates; keep one copy (the rest is deleted)
    citadel mcp             serve the library to agents (MCP over stdio)
    citadel refs KEY        a paper's references (or --cited-by); --add N… fetches them
    citadel web KEY         open the paper online (publisher page, or --google search)
    citadel login           sign in to your library for paywalled papers
    citadel attach KEY PDF  give a reference without a PDF its PDF
    citadel bib             write library.bib (all references, BibTeX) in the library
    citadel --version

Every command accepts --json, for agents and scripts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def run_app() -> None:
    # textual-image must probe the terminal before Textual takes over stdin.
    import textual_image.widget  # noqa: F401

    from .tui.app import Citadel

    Citadel().run()


def emit(data: dict, as_json: bool, text: str) -> None:
    print(json.dumps(data, indent=2) if as_json else text)


def topics_module_default() -> str:
    from .topics import DEFAULT_FILE

    return str(DEFAULT_FILE)


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
        "topics_file_updated": report.topics_file_updated,
        "topics_file_customised": report.topics_file_customised,
        "created": report.created,
    }
    lines = [
        f"Config:  {paths.CONFIG_FILE}" + (" (created)" if config_created else ""),
        f"Library: {library.root}",
    ]
    if report.topics_file_created:
        lines.append(f"  created {library.topics_file.name}")
    if report.topics_file_updated:
        lines.append(f"  updated {library.topics_file.name} to Citadel's new default")
    if report.topics_file_customised:
        lines.append(
            f"  {library.topics_file.name} has your own edits: left as it is "
            f"(compare with {topics_module_default()})"
        )
    lines += [f"  created {name}/" for name in report.created]
    if not (
        report.created
        or report.topics_file_created
        or report.topics_file_updated
        or report.topics_file_customised
    ):
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
        "access": config.access.name or None,
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
        f"Sign-in:   {config.access.name or 'not set (pages open in your usual browser)'}",
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


def converted_line(result) -> str:
    if result.ok:
        mode = " with OCR" if result.ocr else ""
        return (
            f"md      {result.citekey}: {result.pages} pages in {result.seconds:.0f}s{mode}"
            f", {result.figures} figures"
        )
    return f"error   {result.citekey}: {result.error}"


def cmd_watch(args: argparse.Namespace) -> int:
    import time

    stop_on_sigterm()
    from .cite import export_bib
    from .classify import Classifier
    from .convert import Converter, ConvertError

    session = Session()
    interval = args.interval or session.config.ingest.watch_interval
    print(f"Watching {session.library.inbox} every {interval}s (Ctrl+C to stop)", flush=True)

    def log(line: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)

    try:
        while True:
            filed = []
            # Topics and keywords may have changed since the last pass (citadel init).
            session.ingester.classifier = Classifier(session.library.topics())
            for result in session.ingester.process_inbox():
                log(result_line(result))
                if result.status == "filed":
                    filed.append(result.citekey)
            try:
                export_bib(session.index)  # rewritten only when something changed
            except OSError as error:
                log(f"error   library.bib: {error}")
            if filed and session.config.convert.auto:
                converter = Converter(session.index, session.config)
                try:
                    converter.run(
                        converter.pending(filed), on_result=lambda r: log(converted_line(r))
                    )
                except ConvertError as error:
                    log(f"error   conversion: {error}")
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        session.close()


def cmd_add(args: argparse.Namespace) -> int:
    source = f"agent:{args.agent}" if args.agent else "add"
    session = Session()
    try:
        result = session.ingester.add(args.target, topic=args.topic, source=source, move=args.move)
    finally:
        session.close()
    emit(result.as_dict(), args.json, result_line(result))
    return 0 if result.status in ("filed", "review") else 1


def cmd_convert(args: argparse.Namespace) -> int:
    stop_on_sigterm()
    from .convert import Converter, ConvertError

    session = Session()
    try:
        converter = Converter(session.index, session.config)
        if args.ocr:
            session.config.convert.ocr = "always"
        jobs = converter.pending(args.citekeys or None, force=args.force, limit=args.limit)
        if not jobs:
            emit({"results": []}, args.json, "Nothing to convert.")
            return 0
        if not args.citekeys and not args.all:
            pages = sum(j.pages for j in jobs)
            emit(
                {"pending": [j.citekey for j in jobs], "pages": pages},
                args.json,
                f"{len(jobs)} references ({pages} pages) have no Markdown yet.\n"
                "Run `citadel convert --all` to convert them, or name citekeys.",
            )
            return 0
        if not args.json:
            print(
                f"Converting {len(jobs)} references ({sum(j.pages for j in jobs)} pages)…",
                flush=True,
            )
        try:
            results = converter.run(
                jobs,
                on_result=None if args.json else lambda r: print(converted_line(r), flush=True),
            )
        except ConvertError as error:
            emit({"error": str(error)}, args.json, f"Error: {error}")
            return 1
    finally:
        session.close()
    if args.json:
        emit({"results": [r.as_dict() for r in results]}, True, "")
    return 0 if all(r.ok for r in results) else 1


def cmd_cite(args: argparse.Namespace) -> int:
    from . import cite, edit

    index = open_index()
    entries, missing = [], []
    try:
        for key in args.citekeys:
            try:
                ref, _ = edit.load(index, key)
            except edit.EditError:
                missing.append(key)
                continue
            entries.append({"citekey": key, "text": cite.format_ref(ref, args.format)})
    finally:
        index.close()
    joiner = "\n" if args.format == "bibtex" else "\n\n" if args.format == "reference" else "\n"
    text = joiner.join(e["text"].rstrip("\n") for e in entries)
    if missing:
        text += ("\n" if text else "") + f"Unknown: {', '.join(missing)}"
    emit({"format": args.format, "entries": entries, "missing": missing}, args.json, text)
    return 1 if missing else 0


def cmd_duplicates(args: argparse.Namespace) -> int:
    from . import duplicates

    session = Session()
    try:
        groups = duplicates.find(session.index, session.library)
        if args.action == "list":
            lines = []
            for number, group in enumerate(groups, 1):
                lines.append(f"{number}. {', '.join(group.reasons)}")
                for m in group.members:
                    badges = " ".join(
                        b for b, on in (("md", m.has_markdown), ("notes", m.has_notes)) if on
                    )
                    lines.append(
                        f"   {m.id:<48} {m.year or 'n.d.'}  {m.title[:50]}"
                        + (f"  [{badges}]" if badges else "")
                    )
            emit(
                {"groups": [g.as_dict() for g in groups]},
                args.json,
                "\n".join(lines) or "No duplicates.",
            )
            return 0
        member = args.member
        group = next(
            (g for g in groups if any(m.id == member or m.key == member for m in g.members)), None
        )
        if group is None:
            emit(
                {"error": f"{member} is not in any duplicate group"},
                args.json,
                f"{member} is not in any duplicate group.",
            )
            return 1
        if args.action == "ignore":
            duplicates.ignore(session.library, group)
            emit({"ignored": group.id}, args.json, "Marked as not duplicates.")
            return 0
        keep_id = next(m.id for m in group.members if m.id == member or m.key == member)
        deleted = duplicates.keep(session.index, session.library, session.ingester, group, keep_id)
    finally:
        session.close()
    emit(
        {"kept": keep_id, "deleted": deleted},
        args.json,
        f"Kept {keep_id}; deleted (to OneDrive's recycle bin): " + ", ".join(deleted),
    )
    return 0


def cmd_bib(args: argparse.Namespace) -> int:
    from .cite import export_bib

    index = open_index()
    try:
        path, changed, count = export_bib(
            index, Path(args.output).expanduser() if args.output else None
        )
    finally:
        index.close()
    emit(
        {"path": str(path), "changed": changed, "entries": count},
        args.json,
        f"{path}: {count} entries" + ("" if changed else " (already up to date)"),
    )
    return 0


def cmd_refs(args: argparse.Namespace) -> int:
    from .graph import Graph

    session = Session()
    try:
        graph = Graph(session.http, session.index)
        try:
            work = graph.work_for(args.citekey)
        except LookupError as error:
            emit({"error": str(error)}, args.json, f"Error: {error}")
            return 1
        if work is None:
            emit(
                {"error": "not found in OpenAlex"},
                args.json,
                f"OpenAlex does not know {args.citekey}, so its references are unknown.",
            )
            return 1
        works = graph.cited_by(work, args.limit) if args.cited_by else graph.references(work)
        if args.add:
            parent = session.index.get(args.citekey)["topic"]
            results = []
            for number in args.add:
                if not 1 <= number <= len(works):
                    results.append({"number": number, "status": "error", "reason": "no such"})
                    continue
                result = session.ingester.fetch(
                    works[number - 1],
                    topic=args.topic,
                    fallback_topic=parent,
                    source=f"refs:{args.citekey}",
                )
                results.append({"number": number, **result.as_dict()})
            text = "\n".join(
                f"{r['number']:>3}. {r['status']:<7} {r.get('folder') or r.get('reason')}"
                for r in results
            )
            emit({"results": results}, args.json, text)
            return 0 if all(r["status"] in ("filed", "review") for r in results) else 1
    finally:
        session.close()
    what = "cited by" if args.cited_by else "references"
    lines = [
        f"{work.label}: {work.meta.title[:80]}",
        f"{len(works)} {what} · ✓ in library · ⬇ open-access PDF · · metadata only",
        "",
    ]
    for number, w in enumerate(works, 1):
        mark = "✓" if w.citekey else "⬇" if (w.pdf_url or w.meta.arxiv) else "·"
        lines.append(
            f"{number:>3}. {mark} {w.label:<22} {w.meta.title[:70]}"
            + (f"  [{w.citekey}]" if w.citekey else "")
        )
    lines.append("")
    lines.append(
        f"Add with: citadel refs {args.citekey}"
        + (" --cited-by" if args.cited_by else "")
        + " --add N [N…]"
    )
    emit(
        {"paper": work.as_dict(), "direction": what, "works": [w.as_dict() for w in works]},
        args.json,
        "\n".join(lines),
    )
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from . import access, web
    from .config import Config
    from .library import Library

    index = open_index()
    row = index.get(args.citekey)
    index.close()
    if row is None:
        emit({"error": f"no reference {args.citekey}"}, args.json, f"No reference {args.citekey}.")
        return 1
    config = Config.load()
    url = web.best_url(row["data"], google=args.google)
    inbox = Library(config.library_path).inbox
    opened = not args.print and access.open_page(
        config.access, inbox, url, publisher=not args.google
    )
    emit({"url": url, "opened": opened}, args.json, url)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    from . import access
    from .config import Config
    from .library import Library

    config = Config.load()
    if not access.enabled(config.access):
        message = "No library sign-in set: fill in [access] in the config (see `citadel doctor`)."
        emit({"error": message}, args.json, message)
        return 1
    opened = access.login(config.access, Library(config.library_path).inbox)
    data = {
        "library": config.access.name,
        "profile": str(access.profile_dir(config.access)),
        "opened": opened,
    }
    line = (
        f"Sign in to {config.access.name} in the window that opened; Citadel keeps the "
        "session.\nPDFs you download there go into the inbox and are attached to their "
        "reference."
        if opened
        else f"Could not start {config.access.browser}."
    )
    emit(data, args.json, line)
    return 0 if opened else 1


def cmd_attach(args: argparse.Namespace) -> int:
    session = Session()
    try:
        result = session.ingester.attach_pdf(
            args.citekey, Path(args.pdf).expanduser(), keep_source=not args.move
        )
    except (LookupError, ValueError, OSError) as error:
        emit({"error": str(error)}, args.json, f"Error: {error}")
        return 1
    finally:
        session.close()
    emit(result.as_dict(), args.json, result_line(result))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as serve

    serve()
    return 0


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
        if args.action == "retry":
            results = ingester.retry(args.item or "")
            text = "\n".join(map(result_line, results)) or "Nothing to retry."
            emit({"results": [r.as_dict() for r in results]}, args.json, text)
            return 0
        if not args.item:
            emit({"error": "which item?"}, args.json, "Name the item (file name or prefix).")
            return 1
        try:
            if args.action == "accept":
                corrections = {
                    k: v
                    for k, v in (
                        ("title", args.title),
                        ("authors", args.author),
                        ("year", args.year),
                    )
                    if v
                }
                item = args.item
                if corrections:  # the item is renamed after its corrected metadata
                    item = ingester.edit_review(args.item, **corrections).file
                result = ingester.accept(item, topic=args.topic, doi=args.doi, type=args.type)
            else:
                result = ingester.reject(args.item)
        except (LookupError, ValueError) as error:
            emit({"error": str(error)}, args.json, f"Error: {error}")
            return 1
    finally:
        session.close()
    emit(result.as_dict(), args.json, result_line(result))
    return 0


def stop_on_sigterm() -> None:
    """systemctl stop sends SIGTERM: turn it into a normal exit, so `finally` blocks run
    (the Marker worker and llama-server are stopped, the status file says idle)."""
    import signal

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)


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
    review.add_argument(
        "action", nargs="?", default="list", choices=["list", "accept", "reject", "retry"]
    )
    review.add_argument("item", nargs="?", help="file name (or a unique prefix)")
    review.add_argument("--topic", default="", help="accept: topic folder")
    review.add_argument("--doi", default="", help="accept: take the metadata from this DOI")
    review.add_argument("--type", default="", help="accept: document type (book, article…)")
    review.add_argument("--title", default="", help="accept: the correct title")
    review.add_argument(
        "--author",
        action="append",
        default=[],
        help='accept: an author, "Surname, Given" (repeat for more)',
    )
    review.add_argument("--year", type=int, help="accept: publication year")
    review.set_defaults(func=cmd_review)

    convert = sub.add_parser(
        "convert", parents=[common], help="convert filed PDFs to Markdown (Marker, GPU)"
    )
    convert.add_argument("citekeys", nargs="*", help="only these references")
    convert.add_argument("--all", action="store_true", help="every reference without Markdown")
    convert.add_argument("--force", action="store_true", help="also redo converted ones")
    convert.add_argument("--ocr", action="store_true", help="read every page with OCR")
    convert.add_argument("--limit", type=int, help="at most this many references")
    convert.set_defaults(func=cmd_convert)

    cite_parser = sub.add_parser("cite", parents=[common], help="cite references")
    cite_parser.add_argument("citekeys", nargs="+")
    cite_parser.add_argument(
        "--format", default="reference", choices=["reference", "bibtex", "key"]
    )
    cite_parser.set_defaults(func=cmd_cite)

    dupes = sub.add_parser("duplicates", parents=[common], help="find and resolve duplicates")
    dupes.add_argument("action", nargs="?", default="list", choices=["list", "keep", "ignore"])
    dupes.add_argument("member", nargs="?", help="keep/ignore: a citekey or review file name")
    dupes.set_defaults(func=cmd_duplicates)

    bib = sub.add_parser("bib", parents=[common], help="write library.bib")
    bib.add_argument("--output", help="somewhere else than <library>/library.bib")
    bib.set_defaults(func=cmd_bib)

    refs = sub.add_parser(
        "refs", parents=[common], help="a paper's references or citations; fetch them"
    )
    refs.add_argument("citekey")
    refs.add_argument("--cited-by", action="store_true", help="works citing it instead")
    refs.add_argument(
        "--add",
        type=int,
        nargs="+",
        metavar="N",
        help="add these numbers from the list to the library",
    )
    refs.add_argument("--topic", default="", help="--add: topic folder for them")
    refs.add_argument("--limit", type=int, default=50, help="--cited-by: how many")
    refs.set_defaults(func=cmd_refs)

    web_parser = sub.add_parser("web", parents=[common], help="open a reference online")
    web_parser.add_argument("citekey")
    web_parser.add_argument("--google", action="store_true", help="a Google search instead")
    web_parser.add_argument("--print", action="store_true", help="only print the address")
    web_parser.set_defaults(func=cmd_web)

    sub.add_parser(
        "login", parents=[common], help="sign in to your library for paywalled papers"
    ).set_defaults(func=cmd_login)

    attach = sub.add_parser("attach", parents=[common], help="add a reference's PDF")
    attach.add_argument("citekey")
    attach.add_argument("pdf")
    attach.add_argument("--move", action="store_true", help="move the PDF instead of copying")
    attach.set_defaults(func=cmd_attach)

    sub.add_parser("mcp", help="serve the library to agents (MCP, stdio)").set_defaults(
        func=cmd_mcp, json=False
    )

    args = parser.parse_args(argv)
    if args.command is None:
        run_app()
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
