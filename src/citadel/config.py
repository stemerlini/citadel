"""User settings, kept in data/config.toml.

The file is meant to be edited by hand. Citadel only writes it when it does not
exist yet (`citadel init`), so comments and ordering you add are never lost.
Missing keys fall back to the defaults below; unknown keys are ignored.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from . import paths
from .library import Library, standard_folder


@dataclass
class ObsidianConfig:
    vault: str = ""  # empty until the vault exists; link/note explain instead of failing
    notes_folder: str = "Literature"


@dataclass
class IngestConfig:
    review_threshold: float = 0.8  # metadata confidence needed to file without review
    topic_threshold: float = 0.6  # topic confidence needed to file without review
    review_all: bool = False  # inbox files always wait in review, however confident
    settle_seconds: int = 60  # leave files this recently changed alone (still downloading)
    watch_interval: int = 30  # seconds between inbox polls (inotify misses rclone changes)


@dataclass
class MetadataConfig:
    # Crossref and OpenAlex serve requests faster when they include a contact address.
    # Empty means no address is sent.
    contact_email: str = ""


@dataclass
class AccessConfig:
    # Your library's sign-in for paywalled papers (see access.py). Empty name: pages open
    # in your usual browser instead.
    name: str = ""
    login_url: str = ""
    proxy: str = ""  # EZproxy-style prefix put before publisher links; empty for none
    browser: str = "chromium"


@dataclass
class ConvertConfig:
    python: str = ".venv-convert/bin/python"  # Marker's own environment, relative to the app root
    device: str = "cuda"  # cuda | cpu
    ocr: str = "auto"  # auto (only PDFs whose text layer garbles maths) | always | never
    auto: bool = True  # the inbox watcher converts newly filed papers by itself


@dataclass
class Config:
    library: str = str(paths.DEFAULT_LIBRARY)
    # [[libraries]] tables (name, path, optional tag); when present, `library` is unused.
    library_entries: list[dict] = field(default_factory=list)
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    convert: ConvertConfig = field(default_factory=ConvertConfig)

    def libraries(self) -> list[Library]:
        """Every configured library, the default one (for `add`) first. CITADEL_LIBRARY
        replaces them all with one. Missing folders are included: see available_libraries."""
        override = os.environ.get("CITADEL_LIBRARY")
        if override:
            return [Library(Path(override).expanduser())]
        entries = self.library_entries or [{"path": self.library}]
        found: list[Library] = []
        for entry in entries:
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                continue
            root = Path(path).expanduser()
            library = Library(root, str(entry.get("name") or ""))
            renamed = standard_folder(root, library.name)
            if not root.exists() and renamed.is_dir():
                # Renamed to Citadel-<tag> on another machine: follow it.
                library = Library(renamed)
            if any(other.name.casefold() == library.name.casefold() for other in found):
                continue  # names identify libraries in the index: the first one wins
            if isinstance(entry.get("tag"), str) and entry["tag"]:
                library.tag = entry["tag"]
            found.append(library)
        tags = [library.tag for library in found]
        for library in found:  # two libraries starting with the same letter
            if tags.count(library.tag) > 1:
                library.tag = library.name
        return found or [Library(paths.DEFAULT_LIBRARY)]

    def available_libraries(self) -> list[Library]:
        """The libraries whose folder is there (a work OneDrive may not be mounted)."""
        return [library for library in self.libraries() if library.exists()]

    def library_named(self, name: str) -> Library:
        """A library by name or tag (any case); "" is the default library."""
        libraries = self.libraries()
        if not name:
            return libraries[0]
        for library in libraries:
            if name.casefold() in (library.name.casefold(), library.tag.casefold()):
                return library
        known = ", ".join(library.name for library in libraries)
        raise LookupError(f"no library {name!r} (configured: {known})")

    @property
    def library_path(self) -> Path:
        """The default library's folder."""
        return self.libraries()[0].root

    @property
    def vault_path(self) -> Path | None:
        return Path(self.obsidian.vault).expanduser() if self.obsidian.vault else None

    @property
    def convert_python(self) -> Path:
        python = Path(self.convert.python).expanduser()
        return python if python.is_absolute() else paths.ROOT / python

    @classmethod
    def load(cls, file: Path | None = None) -> Config:
        file = file or paths.CONFIG_FILE
        try:
            data = tomllib.loads(file.read_text())
        except OSError:
            return cls()
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        config = cls()
        if isinstance(data.get("library"), str):
            config.library = data["library"]
        if isinstance(data.get("libraries"), list):
            config.library_entries = [e for e in data["libraries"] if isinstance(e, dict)]
        for name in ("obsidian", "ingest", "metadata", "access", "convert"):
            section = getattr(config, name)
            values = data.get(name)
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                default = getattr(section, key, None)
                if default is not None and isinstance(value, type(default)):
                    setattr(section, key, value)
                elif isinstance(default, float) and isinstance(value, int):
                    setattr(section, key, float(value))
        return config


TEMPLATE = """\
# Citadel settings. Edit freely: the app changes only what you set in its Settings
# (S): the libraries and the sign-in. Comments are kept.

# The library folder (PDFs, Markdown, figures). Syncs through OneDrive.
library = "{library}"
# Several libraries (say a personal one and a work one, each in its own OneDrive) are
# searched as one; files are never copied between them. Use these instead of `library`
# (the first is where `citadel add` files by default). A library folder is called
# Citadel-<tag>, and the tag (personal, work…) names it; Settings (S) in the app does this:
# [[libraries]]
# path = "~/OneDrive-Personal/Citadel-personal"
# [[libraries]]
# path = "~/OneDrive-Work/Citadel-work"

[obsidian]
# Path to your Obsidian vault. Leave empty until the vault exists.
vault = ""
# Vault folder where `citadel note` creates literature notes.
notes_folder = "Literature"

[ingest]
# Confidence needed to file a new item without asking: for its metadata, and for
# its topic folder. Below either, it waits in _review/ with a proposal.
# review_all = true: every inbox file waits in review, however confident; you file it
# by accepting it (i in the app, or `citadel accept`).
review_all = false
review_threshold = 0.8
topic_threshold = 0.6
# Files changed less than this many seconds ago are left alone (still downloading).
settle_seconds = 60
# Seconds between inbox checks.
watch_interval = 30

[metadata]
# Optional contact address sent to Crossref/OpenAlex for faster, polite access.
contact_email = ""

[access]
# Sign-in for paywalled papers through your library (`citadel login`, or L in the app).
# Citadel opens publisher pages in its own Chromium profile, where you sign in once; a
# PDF you download there goes straight into the inbox and is attached to its reference.
# Empty name: pages open in your usual browser.
# Imperial:   name = "imperial", login_url = "https://library-search.imperial.ac.uk/"
# EZproxy library: also set proxy, e.g. "https://proxy.example.org/login?url="
name = ""
login_url = ""
proxy = ""
browser = "chromium"

[convert]
# Python of Marker's own environment (relative paths are inside the app folder).
python = ".venv-convert/bin/python"
# cuda or cpu
device = "cuda"
# Read pages with OCR instead of the PDF's text layer: "auto" does it only for PDFs
# whose text layer garbles maths (many APS/AIP papers); "always" is ~6x slower.
ocr = "auto"
# Let the inbox watcher convert newly filed papers to Markdown on its own.
auto = true
"""


def write_default(file: Path | None = None, library: Path | None = None) -> bool:
    """Create the config file from the template. Returns False if it already exists."""
    file = file or paths.CONFIG_FILE
    if file.exists():
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(TEMPLATE.format(library=library or paths.DEFAULT_LIBRARY))
    return True


def _document(file: Path) -> tomlkit.TOMLDocument:
    if file.exists():
        return tomlkit.parse(file.read_text())
    return tomlkit.parse(TEMPLATE.format(library=paths.DEFAULT_LIBRARY))


def _save(document: tomlkit.TOMLDocument, file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f".{file.name}.tmp")
    temporary.write_text(tomlkit.dumps(document))
    temporary.replace(file)


def _home_relative(path: str) -> str:
    resolved = Path(path).expanduser()
    try:
        return "~/" + resolved.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(resolved)


def save_libraries(entries: list[dict], file: Path | None = None) -> None:
    """Write the libraries chosen in the app as [[libraries]], in order: the first is the
    default. A Citadel-<tag> folder names itself, so only its path is written. The rest of
    the file, comments included, is kept."""
    file = file or paths.CONFIG_FILE
    document = _document(file)
    tables = tomlkit.aot()
    for entry in entries:
        table = tomlkit.table()
        if entry.get("name"):
            table["name"] = entry["name"]
        table["path"] = _home_relative(entry["path"])
        tables.append(table)
    if entries:
        document["library"] = _home_relative(entries[0]["path"])
    document["libraries"] = tables
    _save(document, file)


def save_access(
    name: str, login_url: str, proxy: str = "", browser: str = "", file: Path | None = None
) -> None:
    """Write the library sign-in set in the app; an empty name turns it off."""
    file = file or paths.CONFIG_FILE
    document = _document(file)
    if "access" not in document:
        document["access"] = tomlkit.table()
    section = document["access"]
    section["name"], section["login_url"], section["proxy"] = name, login_url, proxy
    if browser:
        section["browser"] = browser
    _save(document, file)
