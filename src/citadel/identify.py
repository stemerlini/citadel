"""Work out what a PDF is: find its metadata and say how sure we are.

Order of attempts, best evidence first:
  0. Slides and posters (talks.py): described from themselves, never searched for, as
     a search only finds related papers.
     Standards and handbooks (ECSS, NASA, MIL, ESCC, JEDEC), by the identifier at
     the top of page 1 (see standards.py): they have no DOI and are not in Crossref
  1. DOIs in the PDF metadata, first pages and cover page -> Crossref
  2. arXiv ids -> arXiv (then Crossref, if the preprint has a journal DOI)
  3. ISBNs (first pages, then the copyright page) -> Crossref, then OpenLibrary
  4. Title search (titles guessed from the title page, or a usable Title field)
     -> Crossref bibliographic search (with the PDF's Author, if any), then OpenAlex

Every candidate is checked against the text of the first pages: a paper's
first page shows its own title and authors, while a DOI found there may
belong to a cited paper. The confidence reflects that check, not the source.
When two different works score almost the same (two editions of a book, a
paper and its conference version), the result is capped below the review
threshold: a person should choose.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import identifiers, naming, sources, standards, talks
from .pdf import PdfText
from .pdf import read as read_pdf
from .sources import Http, Metadata, SourceError

MAX_DOIS = 3  # DOIs on the first pages beyond the first few are almost always citations
GOOD_ENOUGH = 0.9  # stop searching once a candidate scores this
TRUSTED_TITLE = 0.95  # title match that makes a DOI/arXiv id found in the paper certain
DIRECT_MATCH = 0.92  # …the confidence it then gets
PLAUSIBLE = 0.5  # below this after the best title guess, try the other guesses
AMBIGUITY_MARGIN = 0.05  # a different work this close to the best makes it ambiguous
AMBIGUOUS_CAP = 0.75  # confidence of an ambiguous result (below the review threshold)
HEADER_CHARS = 2500  # the year is checked near the top of the paper, not in citations


@dataclass
class Identification:
    metadata: Metadata | None
    confidence: float  # 0..1
    method: str  # doi | arxiv | isbn | search | none
    evidence: list[str] = field(default_factory=list)  # what was tried, for review and agents
    pdf: PdfText | None = None

    def as_dict(self) -> dict:
        return {
            "metadata": self.metadata.__dict__ if self.metadata else None,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "evidence": self.evidence,
            "title_candidates": self.pdf.title_candidates if self.pdf else [],
            "has_text": self.pdf.has_text if self.pdf else False,
            "ocr": self.pdf.ocr if self.pdf else False,
        }


def identify(path: Path, http: Http) -> Identification:
    return identify_text(read_pdf(path), http)


class _Search:
    """Keeps the best candidate seen so far and a log of every attempt."""

    def __init__(self, pdf: PdfText):
        self.pdf = pdf
        self.best: Identification | None = None
        self.scored: list[tuple[float, Metadata]] = []
        self.evidence: list[str] = []

    @property
    def done(self) -> bool:
        return self.best is not None and self.best.confidence >= GOOD_ENOUGH

    def fetch(self, label: str, call: Callable):
        try:
            found = call()
        except SourceError as error:
            self.evidence.append(f"{label}: {error}")
            return None
        if not found:
            self.evidence.append(f"{label}: not found")
        return found

    def consider(self, candidate: Metadata | None, method: str, weight: float) -> None:
        if candidate is None or not candidate.title:
            return
        score, notes, title_score = check(candidate, self.pdf, copyright_page=method == "isbn")
        confidence = weight * score
        if method in ("doi", "arxiv") and title_score >= TRUSTED_TITLE:
            # An identifier printed in the paper, and the record's title on its page:
            # authors set in an unusual way must not hold this back.
            confidence = max(confidence, DIRECT_MATCH)
        key = candidate.doi or candidate.arxiv or candidate.isbn or "-"
        self.evidence.append(
            f"{method} {key}: '{candidate.title[:70]}' ({notes}) -> {confidence:.2f}"
        )
        self.scored.append((confidence, candidate))
        if self.best is None or confidence > self.best.confidence:
            self.best = Identification(candidate, confidence, method, self.evidence, self.pdf)

    def result(self) -> Identification:
        if self.best is None:
            if not self.pdf.has_text:
                self.evidence.append("no readable text, even with OCR")
            return Identification(fallback(self.pdf), 0.0, "none", self.evidence, self.pdf)
        best = self.best
        rivals = [
            (c, m)
            for c, m in self.scored
            if c >= best.confidence - AMBIGUITY_MARGIN and not same_work(m, best.metadata)
        ]
        if rivals and best.confidence > AMBIGUOUS_CAP:
            rival = max(rivals, key=lambda r: r[0])[1]
            self.evidence.append(
                f"ambiguous: '{rival.title[:50]}' ({rival.year}, {rival.doi or '-'}) scores "
                f"almost the same; confidence capped at {AMBIGUOUS_CAP}"
            )
            best.confidence = AMBIGUOUS_CAP
        return best


def identify_text(pdf: PdfText, http: Http) -> Identification:
    if pdf.layout in ("slides", "poster"):
        meta, confidence, evidence = talks.describe(pdf)
        return Identification(meta, confidence, pdf.layout, evidence, pdf)
    standard = standards.recognise(pdf)
    if standard:
        return Identification(
            standard, standards.CONFIDENCE, "standard", [f"standard: {standard.title}"], pdf
        )
    search = _Search(pdf)
    meta_text = " ".join(pdf.info.get(k, "") for k in ("Title", "Subject", "Keywords"))
    text = pdf.all_text

    dois = dict.fromkeys(identifiers.find_dois(meta_text) + identifiers.find_dois(text))
    for doi in list(dois)[:MAX_DOIS]:
        search.consider(
            search.fetch(f"crossref {doi}", lambda d=doi: sources.crossref(http, d)), "doi", 1.0
        )
        if search.done:
            return search.result()

    # The file name counts too: arXiv downloads are named after their id (2103.00613.pdf).
    name = pdf.path.stem if pdf.path else ""
    named = [m.group(1) for m in re.finditer(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", name)]
    for arxiv_id in list(dict.fromkeys(identifiers.find_arxiv(f"{meta_text}\n{text}") + named))[:2]:
        found = search.fetch(f"arxiv {arxiv_id}", lambda a=arxiv_id: sources.arxiv(http, a))
        if found and found.doi:  # the published version has richer metadata
            published = search.fetch(
                f"crossref {found.doi}", lambda d=found.doi: sources.crossref(http, d)
            )
            if published:
                published.arxiv = arxiv_id
                found = published
        search.consider(found, "arxiv", 1.0)
        if search.done:
            return search.result()

    isbns = identifiers.find_isbns(text) or identifiers.find_isbns(pdf.more_text())
    for isbn in isbns[:2]:
        search.consider(
            search.fetch(f"crossref isbn {isbn}", lambda i=isbn: sources.crossref_isbn(http, i)),
            "isbn",
            0.95,
        )
        if search.done:
            return search.result()
        search.consider(
            search.fetch(f"openlibrary {isbn}", lambda i=isbn: sources.openlibrary(http, i)),
            "isbn",
            0.95,
        )
        if search.done:
            return search.result()

    author = pdf.meta_author
    for query in dict.fromkeys(q for q in (*pdf.title_candidates, pdf.meta_title) if q):
        for found in (
            search.fetch(
                f"crossref search '{query[:50]}'",
                lambda q=query: sources.crossref_search(http, q, author),
            )
            or []
        ):
            search.consider(found, "search", 0.95)
        if search.done:
            break
        for found in (
            search.fetch(
                f"openalex '{query[:50]}'", lambda q=query: sources.openalex_search(http, q)
            )
            or []
        ):
            if found.doi and similarity(found.title, query) > 0.8:
                found = (
                    search.fetch(
                        f"crossref {found.doi}", lambda d=found.doi: sources.crossref(http, d)
                    )
                    or found
                )
            search.consider(found, "search", 0.95)
        if search.best and search.best.confidence >= PLAUSIBLE:
            break  # the other title guesses are smaller text: author lines, banners
    return search.result()


def fallback(pdf: PdfText) -> Metadata | None:
    """What the PDF says about itself, when no source knows it."""
    title = pdf.title_guess or pdf.meta_title
    if not title:
        return None
    authors = [a.strip() for a in re.split(r";|\band\b", pdf.meta_author) if a.strip()]
    year = re.search(r"\b(19\d\d|20\d\d)\b", pdf.info.get("CreationDate", ""))
    return Metadata(
        title=title,
        authors=[sources.comma_name(a) for a in authors],
        year=int(year.group(1)) if year else None,
        source="pdf",
    )


# -- checking a candidate against the PDF ----------------------------------------


def normal_words(text: str) -> list[str]:
    return [w.lower().replace("-", "") for w in naming.words(text)]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, " ".join(normal_words(a)), " ".join(normal_words(b))).ratio()


def title_in_text(title: str, text: str) -> float:
    """Share of the title's words that appear on the page next to their neighbour
    in the title. Scattered words do not count, so a short title cannot match a
    page just because its words occur somewhere on it."""
    wanted = normal_words(title)
    page = normal_words(text)
    if len(wanted) < 2:
        return float(bool(wanted) and wanted[0] in page)
    page_pairs = set(zip(page, page[1:], strict=False))
    covered = set()
    for index, pair in enumerate(zip(wanted, wanted[1:], strict=False)):
        if pair in page_pairs:
            covered.update((index, index + 1))
    return len(covered) / len(wanted)


def same_work(a: Metadata, b: Metadata) -> bool:
    """The same publication found twice (e.g. by DOI and by search)."""
    if a.doi and b.doi:
        return a.doi == b.doi
    return a.year == b.year and similarity(a.title, b.title) > 0.9


def author_in_text(author: str, text: str) -> bool:
    """The surname on the page, even with affiliation marks glued to it: digits
    (Settles1,3) or one or two early letters before a separator (Gautiera , Greeleyb ,).
    Only a-h: affiliation marks, not a longer name (Settle must not match Settles)."""
    surname = naming.ascii_fold(author.split(",")[0]).lower().strip()
    if not surname:
        return False
    pattern = (
        r"(?<![a-z])"
        + r"[\s-]*".join(map(re.escape, surname.split()))
        + r"(?:[a-h]{1,2}(?=[\s,;*†‡∗\d])|(?![a-z]))"
    )
    return re.search(pattern, naming.ascii_fold(text).lower()) is not None


def check(
    candidate: Metadata, pdf: PdfText, copyright_page: bool = False
) -> tuple[float, str, float]:
    """How well a candidate matches the PDF, 0..1, with a short explanation.

    Title (65%): the candidate's title on the page as phrases, or its similarity to
    the best title guess. A candidate whose title is much shorter than the guess
    (only part of the real title) is scaled down.
    First author's surname on the page (25%). Year near the top of the paper (10%).
    For a book found by the ISBN printed in it, the copyright page (pages 3-8)
    counts too: that is where authors and year are printed. For anything else it
    does not, as later pages are full of other works' years and authors.
    """
    text = pdf.all_text
    if not pdf.has_text:
        # Nothing to compare with: trust a direct identifier only moderately.
        return 0.6, "no text to verify", 0.0
    later = pdf.later_pages if copyright_page else ""
    text += "\n" + later
    title_score = title_in_text(candidate.title, text)
    resemblance = [(similarity(candidate.title, g), g) for g in pdf.title_candidates]
    similar, guess = max(resemblance, default=(0.0, ""))
    if guess and similar >= 0.5:
        # A candidate whose title is only part of the guessed one (a shorter, earlier
        # paper by the same authors) should not win on its words being on the page.
        ratio = len(normal_words(candidate.title)) / max(1, len(normal_words(guess)))
        if ratio < 0.75:
            title_score *= ratio / 0.75
    title_score = max(title_score, similar)
    author_ok = bool(candidate.authors) and author_in_text(candidate.authors[0], text)
    header = "\n".join(
        (pdf.first_pages[:HEADER_CHARS], pdf.cover, later, pdf.info.get("Subject", ""))
    )
    year_ok = candidate.year is not None and str(candidate.year) in header
    score = 0.65 * title_score + 0.25 * author_ok + 0.10 * year_ok
    notes = (
        f"title {title_score:.2f}, author {'yes' if author_ok else 'no'}, "
        f"year {'yes' if year_ok else 'no'}"
    )
    return score, notes, title_score
