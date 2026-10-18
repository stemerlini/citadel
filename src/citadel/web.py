"""Finding a paper online: its publisher page, or a web search.

For references without a PDF: the publisher page (through the DOI) is where an
institutional login usually unlocks the PDF; a Google search finds author copies and
repositories. Once downloaded, drop the PDF in the inbox: Citadel recognises it and
attaches it to the reference (ingest.Ingester._complete).
"""

from __future__ import annotations

import subprocess
from urllib.parse import quote_plus

from . import naming


def publisher_url(doi: str = "", arxiv: str = "") -> str:
    if doi:
        return f"https://doi.org/{doi}"
    if arxiv:
        return f"https://arxiv.org/abs/{arxiv}"
    return ""


def search_url(title: str, authors: list[str] | None = None, year: int | None = None) -> str:
    """A Google search for the paper's PDF: quoted title, first author, year."""
    parts = [f'"{title}"']
    if authors:
        parts.append(naming.surname(authors[0]))
    if year:
        parts.append(str(year))
    parts.append("pdf")
    return "https://www.google.com/search?q=" + quote_plus(" ".join(parts))


def best_url(data: dict, google: bool = False) -> str:
    """The publisher page when there is a DOI or arXiv id, else (or with google) a search."""
    if not google:
        url = publisher_url(data.get("doi", ""), data.get("arxiv", ""))
        if url:
            return url
    return search_url(data.get("title", ""), data.get("authors"), data.get("year"))


def open_url(url: str) -> bool:
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False
