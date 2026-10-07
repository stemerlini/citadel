"""Shared test setup.

Everything runs offline and in temporary folders: the app's data folder and the
library both point at tmp directories, so tests never touch OneDrive.
"""

import os
import tempfile

# Must be set before citadel is imported: paths.py reads it at import time.
os.environ["CITADEL_HOME"] = tempfile.mkdtemp(prefix="citadel-tests-")

import pytest  # noqa: E402


@pytest.fixture
def library_root(tmp_path, monkeypatch):
    root = tmp_path / "Citadel"
    monkeypatch.setenv("CITADEL_LIBRARY", str(root))
    return root


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    from citadel import paths

    file = tmp_path / "data" / "config.toml"
    monkeypatch.setattr(paths, "CONFIG_FILE", file)
    return file
