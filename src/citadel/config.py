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

from . import paths


@dataclass
class ObsidianConfig:
    vault: str = ""  # empty until the vault exists; link/note explain instead of failing
    notes_folder: str = "Literature"


@dataclass
class IngestConfig:
    review_threshold: float = 0.8  # metadata confidence needed to file without review
    topic_threshold: float = 0.6  # topic confidence needed to file without review
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
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    convert: ConvertConfig = field(default_factory=ConvertConfig)

    @property
    def library_path(self) -> Path:
        return Path(os.environ.get("CITADEL_LIBRARY") or self.library).expanduser()

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
# Citadel settings. Edit freely: Citadel never rewrites this file.

# The library folder (PDFs, Markdown, figures). Syncs through OneDrive.
library = "{library}"

[obsidian]
# Path to your Obsidian vault. Leave empty until the vault exists.
vault = ""
# Vault folder where `citadel note` creates literature notes.
notes_folder = "Literature"

[ingest]
# Confidence needed to file a new item without asking: for its metadata, and for
# its topic folder. Below either, it waits in _review/ with a proposal.
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
