"""PDF pages as images for the preview, rendered once by pdftoppm and cached."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .. import paths

DPI = 110  # sharp enough for Sixel in a large terminal, small enough to be quick


def page_image(pdf: Path, page: int) -> Path | None:
    """PNG of one page (1-based), or None if it cannot be rendered."""
    try:
        stat = pdf.stat()
    except OSError:
        return None
    key = hashlib.sha1(f"{pdf}:{stat.st_size}:{stat.st_mtime}".encode()).hexdigest()[:16]
    folder = paths.CACHE_DIR / "pages"
    target = folder / f"{key}-{page}.png"
    if target.exists():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f".{key}-{page}"
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(DPI),
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                str(pdf),
                str(stem),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    rendered = stem.with_name(stem.name + ".png")
    if not rendered.exists():
        return None
    rendered.replace(target)
    return target
