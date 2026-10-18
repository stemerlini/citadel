"""Slides and posters: described from themselves, not looked up.

A talk has no DOI and is not in Crossref; searching for its title only finds
related papers, which is how a deck once got proposed as a NIM-A article. So a
deck or poster (see PdfText.layout) is described by its own PDF metadata and
first page: the title (embedded Title, else the biggest text on slide 1), the
author (embedded Author), the year it was made. Confidence reflects how much of
that the first slide confirms.
"""

from __future__ import annotations

import re

from . import naming
from .identifiers import is_junk_title
from .pdf import PdfText
from .sources import Metadata, comma_name

CONFIRMED = 0.9  # title and author both on the first slide
TITLE_ONLY = 0.75  # only the title confirmed: review, the author may be wrong
UNCONFIRMED = 0.5


def describe(pdf: PdfText) -> tuple[Metadata, float, list[str]]:
    kind = "poster" if pdf.layout == "poster" else "slides"
    first_page = pdf.first_pages.split("\f")[0] if pdf.first_pages else ""
    first_page = first_page or pdf.first_pages
    page_words = set(_words(first_page))

    title, title_ok = "", False
    embedded = pdf.info.get("Title", "").strip()
    if embedded and not is_junk_title(embedded):
        title, title_ok = embedded, _covered(embedded, page_words)
    if not title_ok and pdf.title_guess:
        title, title_ok = pdf.title_guess, True  # it *is* the biggest text on the slide

    authors = [
        comma_name(a.strip())
        for a in re.split(r";|\band\b|,(?=\s*[A-Z][a-z]+ [A-Z])", pdf.meta_author)
        if a.strip()
    ]
    author_ok = bool(authors) and _covered(authors[0].split(",")[0], page_words)
    year = re.search(r"\b(19[89]\d|20\d\d)\b", pdf.info.get("CreationDate", ""))

    confidence = CONFIRMED if title_ok and author_ok else TITLE_ONLY if title_ok else UNCONFIRMED
    evidence = [
        f"{kind}: {pdf.layout} layout, "
        f"{pdf.info.get('Creator') or pdf.info.get('Producer') or 'unknown software'}; "
        f"title {'on the slide' if title_ok else 'not confirmed'}, "
        f"author {'on the slide' if author_ok else 'not confirmed'}"
    ]
    meta = Metadata(
        title=title or "Untitled presentation",
        authors=authors,
        year=int(year.group(1)) if year else None,
        type=kind,
        source=kind,
    )
    return meta, confidence, evidence


def _words(text: str) -> list[str]:
    return [w.lower().replace("-", "") for w in naming.words(text)]


def _covered(text: str, page_words: set[str]) -> bool:
    """Most of the words of text appear on the page."""
    wanted = [w for w in _words(text) if len(w) > 2]
    return bool(wanted) and sum(w in page_words for w in wanted) >= 0.8 * len(wanted)
