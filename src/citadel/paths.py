"""Where Citadel keeps things.

There are two places, kept apart on purpose:

- The app's own data (config, the SQLite index, caches). Run from the project
  folder (./citadel, or an editable install), it lives inside the project; a
  regular install uses ~/.local/share/citadel. Set CITADEL_HOME to choose any folder.
  The index is a rebuildable cache and must not live on the OneDrive mount:
  SQLite is not safe on an rclone FUSE filesystem.
- The library itself (PDFs, Markdown, figures), which syncs through OneDrive.
  Its path comes from the config (see config.py); CITADEL_LIBRARY overrides it.
"""

import os
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def default_root() -> Path:
    if (SOURCE_ROOT / "pyproject.toml").exists():
        return SOURCE_ROOT
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "citadel"


ROOT = Path(os.environ.get("CITADEL_HOME") or default_root())

DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
CONFIG_FILE = DATA_DIR / "config.toml"
INDEX_FILE = DATA_DIR / "index.db"

DEFAULT_LIBRARY = Path.home() / "Cloud" / "Personal-OneDrive" / "Citadel"

USER_AGENT = "citadel/0.1 (personal reference library)"
