"""Recognising standards and handbooks: ECSS, NASA, MIL, ESCC, JEDEC.

They have no DOI and Crossref does not know them, so identifying them by search
fails. But they announce themselves: the document identifier stands on its own
line at the top of the first page (and often in the PDF's Title), usually next
to an issue date. Only that position counts, so a paper that merely mentions a
standard is never taken for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .identifiers import is_junk_title
from .pdf import PdfText
from .sources import Metadata

HEADER_LINES = 15  # where a standard's identifier is printed
CONFIDENCE = 0.9


@dataclass(frozen=True)
class Body:
    pattern: re.Pattern
    author: str
    publisher: str


BODIES = [
    Body(
        re.compile(
            r"\bECSS-[EQMUS]-(?:ST|HB|AS|TM)-\d{2}(?:-\d{2}){0,2}[A-Z]?"
            r"(?:\s*Rev\.?\s*\d+)?",
            re.IGNORECASE,
        ),
        "ECSS",
        "ESA Requirements and Standards Division",
    ),
    Body(
        re.compile(
            r"\bNASA-(?:STD|HDBK|SPEC|TM|TP)-\d{3,5}(?:\.\d+)?[A-Z]?(?:-\d+)?", re.IGNORECASE
        ),
        "NASA",
        "NASA",
    ),
    Body(
        re.compile(r"\bMIL-(?:STD|HDBK|PRF|DTL|S)-\d{2,5}[A-Z]?", re.IGNORECASE),
        "DoD",
        "U.S. Department of Defense",
    ),
    Body(
        re.compile(r"\bESCC\s+(?:Basic\s+Specification\s+)?No\.?\s*\d{4,5}", re.IGNORECASE),
        "ESCC",
        "European Space Components Coordination",
    ),
    Body(
        re.compile(r"\bJESD\d+[A-Z]?(?:-\d+)?"), "JEDEC", "JEDEC Solid State Technology Association"
    ),
]

GENERIC_TITLES = re.compile(
    r"^(military handbook|military standard|nasa technical (handbook|standard)|"
    r"technical (handbook|standard)|space (engineering|product assurance)|metric/si)",
    re.IGNORECASE,
)
YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")


def recognise(pdf: PdfText) -> Metadata | None:
    """Metadata for a standard or handbook, or None if the PDF is not one."""
    header = [line.strip() for line in pdf.first_pages.splitlines() if line.strip()]
    header = header[:HEADER_LINES]
    candidates = [pdf.info.get("Title", "")] + header
    for body in BODIES:
        for line in candidates:
            match = body.pattern.search(line)
            # The identifier must be (almost) the whole line: "ECSS-Q-ST-60-15C Rev.1".
            if match and len(line) <= len(match.group(0)) + 12:
                return _metadata(body, tidy(match.group(0)), pdf, header)
    return None


def _metadata(body: Body, identifier: str, pdf: PdfText, header: list[str]) -> Metadata:
    title = _title(identifier, pdf, header)
    return Metadata(
        title=f"{identifier}: {title}" if title else identifier,
        authors=[body.author],
        year=_year(identifier, pdf, header),
        type="standard",
        publisher=body.publisher,
        source="standard",
    )


def _title(identifier: str, pdf: PdfText, header: list[str]) -> str:
    subject = pdf.info.get("Subject", "").strip()
    if subject and not is_junk_title(subject):
        return subject  # ECSS puts the document's title here
    for guess in pdf.title_candidates:
        if (
            identifier.lower() not in guess.lower()
            and not GENERIC_TITLES.match(guess)
            and len(guess) > 8
        ):
            return _sentence_case(guess)
    return ""


def _year(identifier: str, pdf: PdfText, header: list[str]) -> int | None:
    """The issue date printed with the identifier, else the PDF's creation year."""
    for line in header:
        if identifier.split()[0].lower() in line.lower():
            continue
        found = YEAR.search(line)
        if found:
            return int(found.group(1))
    found = YEAR.search(pdf.info.get("CreationDate", ""))
    return int(found.group(1)) if found else None


def tidy(identifier: str) -> str:
    identifier = re.sub(r"\s+", " ", identifier).strip()
    identifier = re.sub(r"Rev\.?\s*(\d+)", r"Rev.\1", identifier, flags=re.IGNORECASE)
    return identifier.upper().replace("REV.", "Rev.")


def _sentence_case(text: str) -> str:
    """'MITIGATING IN-SPACE CHARGING EFFECTS—A GUIDELINE' -> 'Mitigating in-space …'."""
    if text.isupper():
        text = text.lower()
        return text[:1].upper() + text[1:]
    return text
