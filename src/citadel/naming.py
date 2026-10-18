"""Naming rules: folder/file basenames and citekeys.

Basename (folder and files share it): {year}_{ShortTitle}_{FirstAuthorSurname}
    2004_StatisticalTheoryMagnetohydrodynamicTurbulence_Verma

Citekey (stable, never changes once assigned): {surname}{year}{firstword}
    verma2004statistical
"""

from __future__ import annotations

import re
import string
import unicodedata

MAX_TITLE_WORDS = 6
MAX_TITLE_CHARS = 50

STOP_WORDS = frozenset(
    """a an and as at by for from in into of on or over the to under via with without
    its their this that these those using toward towards""".split()
)

# Letters NFKD does not split into base + accent.
EXTRA_FOLDS = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "Æ": "Ae",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ł": "l",
        "Ł": "L",
        "þ": "th",
        "Þ": "Th",
        "œ": "oe",
        "Œ": "Oe",
        "ı": "i",
    }
)


def ascii_fold(text: str) -> str:
    """Schäfer -> Schafer, Kelvin–Helmholtz -> Kelvin-Helmholtz."""
    text = text.translate(EXTRA_FOLDS).replace("–", "-").replace("—", "-")
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def words(text: str) -> list[str]:
    """ASCII words of a text. Hyphenated words stay one word (Kelvin-Helmholtz);
    apostrophes are dropped (O'Neil -> ONeil)."""
    text = ascii_fold(text)
    text = re.sub(r"\$[^$]*\$", " ", text)  # inline LaTeX in titles
    text = re.sub(r"<[^>]+>", " ", text)  # <i>…</i> tags from Crossref
    text = re.sub(r"(?<=\w)['’](?=\w)", "", text)
    return re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", text)


def pascal(word: str) -> str:
    """Capitalise each hyphen-separated part, keeping acronyms and mixed case as they are:
    high-power -> HighPower, PFRC-2 -> PFRC2, mHz -> mHz."""
    return "".join(
        part if any(c.isupper() for c in part[1:]) or part.isupper() else part.capitalize()
        for part in word.split("-")
    )


def main_title(title: str) -> str:
    """The title without its subtitle (text after ':' or ' - '), unless that leaves a
    single word."""
    head = re.split(r":|\s[-–—]\s", title, maxsplit=1)[0].strip()
    # A one-word head is a name (Weightfield2, AGILE, CREME96): the subtitle says what it is.
    return head if len([w for w in words(head) if w.lower() not in STOP_WORDS]) > 1 else title


def short_title(title: str) -> str:
    all_words = words(main_title(title))
    significant = [w for w in all_words if w.lower() not in STOP_WORDS] or all_words
    result = ""
    for word in significant[:MAX_TITLE_WORDS]:
        if result and len(result) + len(word) > MAX_TITLE_CHARS:
            break
        result += pascal(word)
    return result or "Untitled"


def surname(author: str) -> str:
    """Surname of an author written "Surname, Given", or an organisation's name.

    "van der Merwe, J." -> "VanDerMerwe", "Schäfer, J." -> "Schafer".
    Without a comma, a name with initials is a person ("Mahendra K. Verma" ->
    "Verma"); anything else is an organisation, kept whole ("Texas Instruments"
    -> "TexasInstruments", "NRL" -> "NRL").
    """
    author = author.strip()
    if "," in author:
        name = author.split(",", 1)[0]
    else:
        parts = author.split()
        person = len(parts) > 1 and any(re.fullmatch(r"[A-Z]\.(-[A-Z]\.)?", p) for p in parts)
        name = parts[-1] if person else author
    return "".join(pascal(w) for w in words(name)) or "Anon"


def basename(year: int | None, title: str, authors: list[str]) -> str:
    first = surname(authors[0]) if authors else "Anon"
    return f"{year if year else 'nd'}_{short_title(title)}_{first}"


def citekey(year: int | None, title: str, authors: list[str]) -> str:
    first = surname(authors[0]).lower() if authors else "anon"
    significant = [w for w in words(title) if w.lower() not in STOP_WORDS]
    word = significant[0].lower().replace("-", "") if significant else ""
    return f"{first}{year if year else 'nd'}{word}"


def unique(candidate: str, taken: set[str], separator: str = "") -> str:
    """candidate, or candidate + b, c, …, z, aa, ab, … (with an optional separator).

    Citekeys use no separator (verma2004statisticalb); basenames use '-'
    (2004_…_Verma-b), so a suffix can never be mistaken for part of the surname.
    """
    if candidate not in taken:
        return candidate
    for suffix in _suffixes():
        option = f"{candidate}{separator}{suffix}"
        if option not in taken:
            return option
    raise ValueError(f"too many references named {candidate}")


def _suffixes():
    yield from string.ascii_lowercase[1:]
    for first in string.ascii_lowercase:
        for second in string.ascii_lowercase:
            yield first + second
