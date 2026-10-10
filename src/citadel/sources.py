"""Free scholarly metadata sources: Crossref, arXiv, OpenAlex, OpenLibrary.

Every source returns `Metadata` (or a list of them). Responses are cached on
disk, so identifying the same paper twice costs nothing, and requests to each
host are spaced out to stay polite (arXiv asks for 3 s between calls).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from . import paths

CROSSREF_TYPES = {
    "journal-article": "article",
    "posted-content": "preprint",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "book-chapter": "chapter",
    "book-section": "chapter",
    "book-part": "chapter",
    "proceedings-article": "conference",
    "report": "report",
    "dissertation": "thesis",
    "standard": "standard",
    "dataset": "dataset",
}
OPENALEX_TYPES = {
    "article": "article",
    "preprint": "preprint",
    "book": "book",
    "book-chapter": "chapter",
    "dissertation": "thesis",
    "report": "report",
    "standard": "standard",
    "dataset": "dataset",
    "review": "article",
    "letter": "article",
}
MIN_INTERVAL = {"export.arxiv.org": 3.0}  # seconds between requests; default below
DEFAULT_INTERVAL = 0.2


@dataclass
class Metadata:
    title: str
    authors: list[str] = field(default_factory=list)  # "Surname, Given"
    year: int | None = None
    type: str = "article"
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    doi: str = ""
    arxiv: str = ""
    isbn: str = ""
    url: str = ""
    abstract: str = ""
    source: str = ""  # crossref | arxiv | openalex | openlibrary | pdf


class SourceError(Exception):
    """A source could not be reached or answered with something unusable."""


class Http:
    """A small GET client with an on-disk cache and per-host spacing."""

    def __init__(
        self,
        contact_email: str = "",
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        agent = paths.USER_AGENT
        if contact_email:
            agent = agent.replace(")", f"; mailto:{contact_email})")
        self.contact_email = contact_email
        self.cache_dir = cache_dir or paths.CACHE_DIR / "http"
        self.client = httpx.Client(
            headers={"User-Agent": agent}, timeout=20, follow_redirects=True, transport=transport
        )
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self.client.close()

    def get(self, url: str) -> str | None:
        """Body of a 200 response, None for 404, SourceError for anything else."""
        file = self.cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
        try:
            cached = json.loads(file.read_text())
            return cached["body"]
        except (OSError, ValueError, KeyError):
            pass
        self._wait(urlparse(url).hostname or "")
        try:
            response = self.client.get(url)
        except httpx.HTTPError as error:
            raise SourceError(f"{urlparse(url).hostname}: {error}") from error
        if response.status_code == 404:
            body = None
        elif response.status_code == 200:
            body = response.text
        else:
            raise SourceError(f"{urlparse(url).hostname}: HTTP {response.status_code}")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"url": url, "body": body}))
        return body

    def download(self, url: str, target: Path) -> None:
        """Save a file (not cached: PDFs are kept by the library itself)."""
        self._wait(urlparse(url).hostname or "")
        try:
            with self.client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise SourceError(f"{urlparse(url).hostname}: HTTP {response.status_code}")
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as error:
            raise SourceError(f"{urlparse(url).hostname}: {error}") from error

    def _wait(self, host: str) -> None:
        with self._lock:
            interval = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
            delay = self._last.get(host, 0) + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


# -- Crossref --------------------------------------------------------------------


def crossref(http: Http, doi: str) -> Metadata | None:
    url = f"https://api.crossref.org/works/{quote(doi, safe='/')}"
    if http.contact_email:
        url += f"?mailto={quote(http.contact_email)}"
    body = http.get(url)
    if body is None:
        return None
    try:
        work = json.loads(body)["message"]
    except (ValueError, KeyError) as error:
        raise SourceError("crossref: unexpected response") from error
    return crossref_work(work)


def crossref_work(work: dict) -> Metadata:
    authors = []
    for person in work.get("author", []) or work.get("editor", []):
        if person.get("family"):
            given = person.get("given", "")
            authors.append(f"{person['family']}, {given}" if given else person["family"])
        elif person.get("name"):
            authors.append(person["name"])
    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (work.get(key) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    container = work.get("container-title") or []
    isbns = work.get("ISBN") or []
    return Metadata(
        title=clean_text(" ".join(work.get("title") or [])),
        authors=authors,
        year=year,
        type=CROSSREF_TYPES.get(work.get("type", ""), "misc"),
        journal=clean_text(container[0]) if container else "",
        volume=str(work.get("volume", "")),
        issue=str(work.get("issue", "")),
        # APS/AIP and others number articles instead of paging them (PRL 116, 105004).
        pages=str(work.get("page") or work.get("article-number") or "").replace("--", "-"),
        publisher=work.get("publisher", ""),
        doi=str(work.get("DOI", "")).lower(),
        isbn=isbns[0].replace("-", "") if isbns else "",
        url=work.get("URL", ""),
        abstract=clean_text(work.get("abstract", "")),
        source="crossref",
    )


def crossref_search(http: Http, title: str, author: str = "", limit: int = 5) -> list[Metadata]:
    """Crossref's bibliographic search: good with title + author, covers book chapters."""
    url = f"https://api.crossref.org/works?rows={limit}&query.bibliographic={quote(title[:300])}"
    if author:
        url += f"&query.author={quote(author[:100])}"
    if http.contact_email:
        url += f"&mailto={quote(http.contact_email)}"
    body = http.get(url)
    if body is None:
        return []
    try:
        items = json.loads(body)["message"]["items"]
    except (ValueError, KeyError) as error:
        raise SourceError("crossref: unexpected response") from error
    return [crossref_work(item) for item in items if item.get("title")]


def crossref_isbn(http: Http, isbn: str) -> Metadata | None:
    """A book by ISBN from Crossref (covers most scientific publishers' books)."""
    url = f"https://api.crossref.org/works?rows=10&filter=isbn:{quote(isbn)}"
    if http.contact_email:
        url += f"&mailto={quote(http.contact_email)}"
    body = http.get(url)
    if body is None:
        return None
    try:
        items = json.loads(body)["message"]["items"]
    except (ValueError, KeyError) as error:
        raise SourceError("crossref: unexpected response") from error
    books = [i for i in items if CROSSREF_TYPES.get(i.get("type", "")) == "book"]
    if not books:
        return None
    book = crossref_work(books[0])
    book.isbn = isbn
    return book


# -- arXiv -----------------------------------------------------------------------

ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv(http: Http, arxiv_id: str) -> Metadata | None:
    body = http.get(f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id, safe='/')}")
    if body is None:
        return None
    try:
        feed = ET.fromstring(body)
    except ET.ParseError as error:
        raise SourceError("arxiv: unexpected response") from error
    entry = feed.find("a:entry", ATOM)
    if entry is None or entry.find("a:title", ATOM) is None:
        return None
    if (entry.findtext("a:id", "", ATOM)).endswith("/api/errors"):
        return None
    authors = [comma_name(a.findtext("a:name", "", ATOM)) for a in entry.findall("a:author", ATOM)]
    published = entry.findtext("a:published", "", ATOM)
    return Metadata(
        title=clean_text(entry.findtext("a:title", "", ATOM)),
        authors=authors,
        year=int(published[:4]) if published[:4].isdigit() else None,
        type="preprint",
        journal=clean_text(entry.findtext("arxiv:journal_ref", "", ATOM)),
        doi=entry.findtext("arxiv:doi", "", ATOM).strip().lower(),
        arxiv=arxiv_id,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        abstract=clean_text(entry.findtext("a:summary", "", ATOM)),
        source="arxiv",
    )


# -- OpenAlex --------------------------------------------------------------------


def openalex_doi(http: Http, doi: str) -> Metadata | None:
    url = f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}"
    if http.contact_email:
        url += f"?mailto={quote(http.contact_email)}"
    body = http.get(url)
    if body is None:
        return None
    try:
        return openalex_work(json.loads(body))
    except ValueError as error:
        raise SourceError("openalex: unexpected response") from error


def enrich(http: Http, meta: Metadata) -> Metadata:
    """Fill a missing abstract from OpenAlex (Crossref has none for many publishers).
    Best effort: a source being down never blocks filing."""
    if meta.doi and not meta.abstract:
        try:
            other = openalex_doi(http, meta.doi)
        except SourceError:
            other = None
        if other and other.abstract:
            meta.abstract = other.abstract
    return meta


def openalex_search(http: Http, title: str, limit: int = 5) -> list[Metadata]:
    query = quote(re.sub(r"[^\w\s-]", " ", title)[:300])
    url = f"https://api.openalex.org/works?search={query}&per-page={limit}"
    if http.contact_email:
        url += f"&mailto={quote(http.contact_email)}"
    body = http.get(url)
    if body is None:
        return []
    try:
        results = json.loads(body)["results"]
    except (ValueError, KeyError) as error:
        raise SourceError("openalex: unexpected response") from error
    return [openalex_work(work) for work in results if work.get("title")]


def openalex_work(work: dict) -> Metadata:
    biblio = work.get("biblio") or {}
    pages = biblio.get("first_page") or ""
    if pages and biblio.get("last_page") and biblio["last_page"] != pages:
        pages += f"-{biblio['last_page']}"
    source = (work.get("primary_location") or {}).get("source") or {}
    doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
    return Metadata(
        title=clean_text(work.get("title") or ""),
        authors=[
            comma_name((a.get("author") or {}).get("display_name", ""))
            for a in work.get("authorships") or []
        ],
        year=work.get("publication_year"),
        type=OPENALEX_TYPES.get(work.get("type", ""), "misc"),
        journal=source.get("display_name") or "",
        volume=biblio.get("volume") or "",
        issue=biblio.get("issue") or "",
        pages=pages,
        doi=doi,
        url=f"https://doi.org/{doi}" if doi else work.get("id", ""),
        abstract=_inverted_abstract(work.get("abstract_inverted_index")),
        source="openalex",
    )


def _inverted_abstract(index: dict | None) -> str:
    if not index:
        return ""
    words = sorted((position, word) for word, positions in index.items() for position in positions)
    return " ".join(word for _, word in words)


# -- OpenLibrary -----------------------------------------------------------------


def openlibrary(http: Http, isbn: str) -> Metadata | None:
    body = http.get(f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data")
    if body is None:
        return None
    try:
        book = json.loads(body).get(f"ISBN:{isbn}")
    except ValueError as error:
        raise SourceError("openlibrary: unexpected response") from error
    if not book:
        return None
    year = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", book.get("publish_date", ""))
    title = book.get("title", "")
    if book.get("subtitle"):
        title += f": {book['subtitle']}"
    return Metadata(
        title=clean_text(title),
        authors=[comma_name(a.get("name", "")) for a in book.get("authors", [])],
        year=int(year.group(1)) if year else None,
        type="book",
        publisher=", ".join(p.get("name", "") for p in book.get("publishers", [])),
        isbn=isbn,
        url=book.get("url", ""),
        source="openlibrary",
    )


# -- helpers ---------------------------------------------------------------------


def clean_text(text: str) -> str:
    """Strip JATS/HTML tags and squeeze whitespace (Crossref abstracts, arXiv titles)."""
    text = re.sub(r"<jats:title>[^<]*</jats:title>", " ", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def comma_name(name: str) -> str:
    """ "Mahendra K. Verma" -> "Verma, Mahendra K."; organisations stay as they are."""
    name = re.sub(r"\s+", " ", name).strip()
    if not name or "," in name or " " not in name:
        return name
    parts = name.split(" ")
    particles = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "le", "la", "du"}
    start = len(parts) - 1
    while start > 1 and parts[start - 1].lower() in particles:
        start -= 1
    return f"{' '.join(parts[start:])}, {' '.join(parts[:start])}"
