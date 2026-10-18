"""The citation graph around a paper: what it cites, what cites it, and fetching those.

OpenAlex (free) gives a work's reference list and the works citing it, with DOIs,
arXiv ids and open-access PDF links; Unpaywall (free, needs the contact email)
fills in legal open-access PDFs it knows better. Every work is checked against
the library (by DOI, arXiv id, or title + year + first author), so a list shows at
a glance what you already have.

Fetching a work downloads its open-access PDF when there is one and ingests it like
any other paper; without a PDF it becomes a metadata-only reference.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from . import naming
from .index import Index
from .sources import Http, Metadata, SourceError, openalex_work

OPENALEX = "https://api.openalex.org"
FIELDS = (
    "id,doi,title,display_name,publication_year,authorships,primary_location,biblio,"
    "type,abstract_inverted_index,best_oa_location,open_access,ids,cited_by_count,"
    "referenced_works"
)
BATCH = 50  # OpenAlex ids per filter request


@dataclass
class Work:
    id: str  # OpenAlex id (W…)
    meta: Metadata
    pdf_url: str = ""  # an open-access PDF, if OpenAlex knows one
    cited_by: int = 0
    references: list[str] = field(default_factory=list)  # OpenAlex ids it cites
    citekey: str = ""  # set when the work is already in the library

    @property
    def label(self) -> str:
        first = naming.surname(self.meta.authors[0]) if self.meta.authors else "Anon"
        return f"{first} {self.meta.year or 'n.d.'}"

    def as_dict(self) -> dict:
        meta = self.meta
        return {
            "openalex": self.id,
            "title": meta.title,
            "year": meta.year,
            "authors": meta.authors[:3] + (["et al."] if len(meta.authors) > 3 else []),
            "journal": meta.journal,
            "doi": meta.doi,
            "arxiv": meta.arxiv,
            "type": meta.type,
            "cited_by": self.cited_by,
            "in_library": self.citekey or None,
            "open_access_pdf": bool(self.pdf_url or meta.arxiv),
        }


class Graph:
    def __init__(self, http: Http, index: Index):
        self.http = http
        self.index = index
        self._titles: dict[str, str] | None = None

    # -- finding works ---------------------------------------------------------------

    def work_for(self, citekey: str) -> Work | None:
        """The OpenAlex work of a library reference (by DOI, arXiv id, then title)."""
        row = self.index.get(citekey)
        if row is None:
            raise LookupError(f"no reference {citekey}")
        data = row["data"]
        work = self.lookup(
            doi=data.get("doi", ""),
            arxiv=data.get("arxiv", ""),
            title=data.get("title", ""),
            year=data.get("year"),
        )
        if work:
            work.citekey = citekey
        return work

    def lookup(
        self, doi: str = "", arxiv: str = "", title: str = "", year: int | None = None
    ) -> Work | None:
        mail = self._mail("?")
        if doi:
            raw = self._get(f"{OPENALEX}/works/doi:{quote(doi, safe='/')}{mail}")
            if raw:
                return self._work(raw)
        if arxiv:  # arXiv registers DataCite DOIs for every preprint
            raw = self._get(f"{OPENALEX}/works/doi:10.48550/arXiv.{quote(arxiv, safe='/')}{mail}")
            if raw:
                return self._work(raw)
        if title:
            query = quote(re.sub(r"[^\w\s-]", " ", title)[:300])
            found = self._get(
                f"{OPENALEX}/works?search={query}&per-page=5&select={FIELDS}{self._mail('&')}"
            )
            for raw in (found or {}).get("results", []):
                work = self._work(raw)
                same = naming.words(work.meta.title.lower()) == naming.words(title.lower())
                if same and (year is None or work.meta.year in (year, year - 1, year + 1)):
                    return work
        return None

    def references(self, work: Work) -> list[Work]:
        """What the work cites, in the order OpenAlex lists it."""
        return self._many(work.references)

    def cited_by(self, work: Work, limit: int = 50) -> list[Work]:
        """Works citing it, most cited first."""
        found = self._get(
            f"{OPENALEX}/works?filter=cites:{work.id}&sort=cited_by_count:desc"
            f"&per-page={min(limit, 200)}&select={FIELDS}{self._mail('&')}"
        )
        return [self._mark(self._work(raw)) for raw in (found or {}).get("results", [])]

    def pdf_url(self, work: Work) -> str:
        """A legal open-access PDF: OpenAlex's, Unpaywall's, or arXiv's."""
        if work.pdf_url:
            return work.pdf_url
        if work.meta.doi and self.http.contact_email:
            raw = self._get(
                f"https://api.unpaywall.org/v2/{quote(work.meta.doi, safe='/')}"
                f"?email={quote(self.http.contact_email)}"
            )
            url = ((raw or {}).get("best_oa_location") or {}).get("url_for_pdf")
            if url:
                return url
        if work.meta.arxiv:
            return f"https://arxiv.org/pdf/{work.meta.arxiv}"
        return ""

    def download(self, work: Work, target: Path) -> bool:
        """Fetch the open-access PDF to target; False when there is none (or the link
        leads to a web page instead of a PDF)."""
        url = self.pdf_url(work)
        if not url:
            return False
        try:
            self.http.download(url, target)
        except SourceError:
            return False
        with target.open("rb") as handle:
            if handle.read(5) != b"%PDF-":  # a landing page or a paywall
                target.unlink()
                return False
        return True

    # -- helpers ---------------------------------------------------------------------

    def _many(self, ids: list[str]) -> list[Work]:
        short = [i.rsplit("/", 1)[-1] for i in ids]
        by_id: dict[str, Work] = {}
        for start in range(0, len(short), BATCH):
            chunk = "|".join(short[start : start + BATCH])
            found = self._get(
                f"{OPENALEX}/works?filter=openalex_id:{chunk}&per-page={BATCH}"
                f"&select={FIELDS}{self._mail('&')}"
            )
            for raw in (found or {}).get("results", []):
                work = self._mark(self._work(raw))
                by_id[work.id] = work
        return [by_id[i] for i in short if i in by_id]

    def _work(self, raw: dict) -> Work:
        meta = openalex_work(raw)
        arxiv = ((raw.get("ids") or {}).get("arxiv") or "").rsplit("/", 1)[-1]
        if arxiv and not meta.arxiv:
            meta.arxiv = re.sub(r"v\d+$", "", arxiv)
        best = raw.get("best_oa_location") or {}
        return Work(
            id=(raw.get("id") or "").rsplit("/", 1)[-1],
            meta=meta,
            pdf_url=best.get("pdf_url") or "",
            cited_by=raw.get("cited_by_count") or 0,
            references=[r.rsplit("/", 1)[-1] for r in raw.get("referenced_works") or []],
        )

    def _mark(self, work: Work) -> Work:
        """Note the citekey when the library already has this work."""
        from .ingest import title_key

        if work.meta.doi:
            work.citekey = self.index.find_doi(work.meta.doi) or ""
        if not work.citekey:
            if self._titles is None:
                self._titles = {}
                for row in self.index.list():
                    key = title_key(row["title"], row["year"], row["first_author"])
                    if key:
                        self._titles[key] = row["citekey"]
            first = naming.surname(work.meta.authors[0]) if work.meta.authors else ""
            key = title_key(work.meta.title, work.meta.year, first)
            work.citekey = self._titles.get(key, "") if key else ""
        return work

    def _get(self, url: str) -> dict | None:
        try:
            body = self.http.get(url)
        except SourceError:
            return None
        if body is None:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    def _mail(self, separator: str) -> str:
        email = self.http.contact_email
        return f"{separator}mailto={quote(email)}" if email else ""
