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


@pytest.fixture
def index_file(tmp_path, monkeypatch):
    from citadel import paths

    file = tmp_path / "data" / "index.db"
    monkeypatch.setattr(paths, "INDEX_FILE", file)
    return file


def make_ref(**overrides):
    """A small valid Reference; override any field."""
    from citadel.record import Reference

    values = dict(
        citekey="verma2004statistical",
        title="Statistical theory of magnetohydrodynamic turbulence: recent results",
        authors=["Verma, Mahendra K."],
        year=2004,
        journal="Physics Reports",
        doi="10.1016/j.physrep.2004.07.007",
        abstract="We review the statistical theory of MHD turbulence.",
        topic="plasma-physics/turbulence-transport",
        tags=["mhd", "spectra"],
        added="2026-10-07",
    )
    values.update(overrides)
    return Reference(**values)


def file_ref(root, ref, fulltext="", pdf=True):
    """Write a reference folder into a library root, as ingest will."""
    from citadel import record

    folder = root / ref.folder
    folder.mkdir(parents=True, exist_ok=True)
    record.write_new(folder / f"{ref.basename}.md", ref, fulltext)
    if pdf:
        (folder / f"{ref.basename}.pdf").write_bytes(b"%PDF-1.4\n")
    return folder
