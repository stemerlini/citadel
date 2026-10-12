"""Office documents (.docx, .odt, .pptx, …): their title, and a PDF copy.

LibreOffice converts them, headless and with its own throw-away profile, so it
never collides with a LibreOffice window the user has open. The PDF goes through
the same pipeline as any other paper; the original is kept next to it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

SUFFIXES = {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods"}
TIMEOUT = 300  # seconds; a long report with many images can take a while


def is_office(path: Path) -> bool:
    return path.suffix.lower() in SUFFIXES


def title(path: Path) -> str:
    """The document's own title (Office core properties or ODF meta), if any."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for member, tag in (("docProps/core.xml", "dc:title"), ("meta.xml", "dc:title")):
                if member in names:
                    found = re.search(
                        rf"<{tag}>([^<]*)</{tag}>", archive.read(member).decode("utf-8", "replace")
                    )
                    if found and found.group(1).strip():
                        return found.group(1).strip()
    except (OSError, zipfile.BadZipFile, KeyError):
        pass
    return ""


def to_pdf(path: Path, folder: Path) -> Path | None:
    """Convert to <folder>/<stem>.pdf with LibreOffice; None if that is not possible."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="citadel-lo-") as profile:
        try:
            subprocess.run(
                [
                    office,
                    f"-env:UserInstallation=file://{profile}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(folder),
                    str(path),
                ],
                capture_output=True,
                timeout=TIMEOUT,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    pdf = folder / f"{path.stem}.pdf"
    return pdf if pdf.exists() and pdf.stat().st_size > 0 else None
