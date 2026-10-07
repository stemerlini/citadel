"""Finding DOIs, arXiv ids and ISBNs in text, and spotting junk titles."""

from __future__ import annotations

import re
from urllib.parse import unquote

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.IGNORECASE)
ARXIV_NEW = re.compile(
    r"(?:arxiv\s*[:.]?\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)
ARXIV_OLD = re.compile(
    r"(?:arxiv\s*[:.]?\s*|arxiv\.org/(?:abs|pdf)/)([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
ISBN = re.compile(r"ISBN(?:-1[03])?\s*:?\s*((?:[0-9][\s-]?){9}[0-9Xx](?:[\s-]?[0-9]){0,3})")


def clean_doi(doi: str) -> str:
    """Normalise a DOI: no URL or 'doi:' prefix, no trailing punctuation, lowercase."""
    doi = unquote(doi.strip())
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = doi.rstrip(".,;:'\"")
    # A closing bracket belongs to the DOI only if it also opens inside it,
    # as in 10.1002/(SICI)1097-0207; otherwise it closes the surrounding sentence.
    for opening, closing in ("()", "[]"):
        while doi.endswith(closing) and doi.count(closing) > doi.count(opening):
            doi = doi[:-1].rstrip(".,;:")
    return doi.lower()


def find_dois(text: str) -> list[str]:
    """DOIs in order of first appearance, without duplicates."""
    seen: dict[str, None] = {}
    for match in DOI.finditer(text):
        doi = clean_doi(match.group(1))
        if "/" in doi and len(doi.split("/", 1)[1]) >= 2:
            seen.setdefault(doi)
    return list(seen)


def find_arxiv(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for pattern in (ARXIV_NEW, ARXIV_OLD):
        for match in pattern.finditer(text):
            seen.setdefault(match.group(1))
    return list(seen)


ISBN13_BARE = re.compile(r"\b(97[89](?:[\s-]?\d){10})\b")


def find_isbns(text: str) -> list[str]:
    """ISBNs after an 'ISBN' label, then bare ISBN-13s (978/979…) in text that mentions
    ISBN somewhere (copyright pages often print labels and numbers apart)."""
    seen: dict[str, None] = {}
    for match in ISBN.finditer(text):
        digits = re.sub(r"[\s-]", "", match.group(1)).upper()
        for candidate in (digits, digits[:10], digits[:13]):
            if valid_isbn(candidate):
                seen.setdefault(candidate)
                break
    if re.search(r"\bISBN\b", text):
        for match in ISBN13_BARE.finditer(text):
            digits = re.sub(r"[\s-]", "", match.group(1))
            if valid_isbn(digits):
                seen.setdefault(digits)
    return list(seen)


def valid_isbn(isbn: str) -> bool:
    if len(isbn) == 10 and re.fullmatch(r"\d{9}[\dX]", isbn):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn))
        return total % 11 == 0
    if len(isbn) == 13 and isbn.isdigit():
        total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(isbn))
        return total % 10 == 0
    return False


JUNK_TITLE = re.compile(
    r"""^\s*$
    | ^\d{4}[A-Za-z&.]{5}                       # ADS bibcode: 1965plph.conf..287A
    | [/\\]                                     # a file path
    | \.(ps|dvi|pdf|docx?|tex|indd|qxd)\b       # a file name
    | ^doi\s*:|^10\.\d{4,9}/                    # a DOI
    | ^(untitled|title|document\d*|microsoft\ word|slide\s*\d+|paper|article|manuscript|
         pii:|s\d{4}-\d{3}|untitled\ document)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_junk_title(title: str) -> bool:
    """True for PDF Title metadata that is not a real title."""
    title = title.strip()
    if JUNK_TITLE.search(title):
        return True
    if " " not in title:  # one token: a file or placeholder name (book_test, MS_draft)
        return True
    words = re.findall(r"[A-Za-z]{2,}", title)
    return len(words) < 2
