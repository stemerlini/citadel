"""Entry point: `citadel` or `python -m citadel`.

    citadel                 open the app (Phase 5; for now prints a pointer)
    citadel init            create the config and the library folders that are missing
    citadel doctor          check paths, config, library layout and the converter
    citadel topics          list the topic tree
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

    args = parser.parse_args(argv)
    if args.command is None:
        print("The Citadel TUI arrives in Phase 5. Try `citadel doctor` or `citadel --help`.")
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
