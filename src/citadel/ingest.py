"""Turning files into references: identify, classify, rename, file, or ask.

A file from the inbox (or `citadel add`) ends up in one of three places:

- filed:     <topic>/<basename>/<basename>.pdf + .md, when both the metadata and
             the topic are confident enough;
- _review/:  with a proposal next to it (<file>.citadel.json), when either is not,
             or when the file duplicates a reference already in the library;
- left where it is: files still downloading, temporary files, and anything that
             failed half-way (nothing is ever deleted before its copy is verified).

Review items are settled with `accept` (optionally correcting the topic, DOI or
type) or `reject` (an exact duplicate is deleted; anything else is kept in
_attachments/, as it may still be useful).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import naming, office, paths, record, sources, standards
from .classify import Classifier
from .config import Config
from .identifiers import ARXIV_NEW, ARXIV_OLD, clean_doi, find_dois
from .identify import Identification, identify_text, normal_words
from .index import Index
from .library import Library
from .pdf import PdfText, sha256
from .pdf import read as read_pdf
from .record import Reference
from .sources import Http, Metadata

PROPOSAL_SUFFIX = ".citadel.json"
ATTACHMENTS = "attachments"
# Words that make a file part of another reference rather than a reference itself.
COMPANION = re.compile(
    r"\b(annex|appendix|supplement(?:ary)?|attachment|addendum)\b", re.IGNORECASE
)
TEMPORARY_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial", ".opdownload")


EMPTY_PATIENCE = 15 * 60  # seconds an empty inbox file is given to fill in
EMPTY_REASON = (
    "the file is empty (0 bytes): its download or OneDrive sync did not finish; "
    "delete it and drop the PDF again"
)


@dataclass
class Proposal:
    """What Citadel would do with a file in review, and why it did not."""

    file: str  # file name inside _review/
    reason: str
    sha256: str
    metadata: dict | None = None  # sources.Metadata fields
    confidence: float = 0.0  # metadata confidence
    method: str = ""
    topic: str = ""
    topic_confidence: float = 0.0
    topic_explain: str = ""
    duplicate_of: str = ""
    evidence: list[str] = field(default_factory=list)
    original_path: str = ""
    added: str = ""
    source: str = "inbox"  # kept for the reference when the item is accepted
    original_name: str = ""  # the file's name when it arrived (main.pdf, 2103.00613.pdf…)

    def save(self, folder: Path) -> None:
        (folder / f"{self.file}{PROPOSAL_SUFFIX}").write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> Proposal:
        data = json.loads(path.read_text())
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Result:
    file: str  # the file that was processed (where it was found)
    status: str  # filed | review | skipped | error | dry-run
    reason: str = ""
    citekey: str = ""
    folder: str = ""  # library-relative: the reference folder, or _review/<file>
    title: str = ""
    confidence: float = 0.0
    topic: str = ""
    topic_confidence: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


class Ingester:
    def __init__(self, library: Library, index: Index, http: Http, config: Config):
        self.library = library
        self.index = index
        self.http = http
        self.config = config
        self.classifier = Classifier(library.topics())

    # -- the inbox ---------------------------------------------------------------

    def pending(self) -> list[Path]:
        """Inbox files ready to process: not hidden, not temporary, not still changing."""
        if not self.library.inbox.is_dir():
            return []
        now = time.time()
        ready = []
        for path in sorted(self.library.inbox.iterdir()):
            if not path.is_file() or path.name.startswith((".", "~$")):
                continue
            if path.name.lower().endswith(TEMPORARY_SUFFIXES):
                continue
            stat = path.stat()
            if now - stat.st_mtime < self.config.ingest.settle_seconds:
                continue
            # An empty file is usually a download or sync that has not finished.
            if stat.st_size == 0 and now - stat.st_mtime < EMPTY_PATIENCE:
                continue
            ready.append(path)
        return ready

    def process_inbox(self, dry_run: bool = False) -> list[Result]:
        results = [self.ingest(path, dry_run=dry_run) for path in self.pending()]
        if not dry_run and any(r.status == "filed" for r in results):
            self.index.reindex()
        return results

    # -- one file ------------------------------------------------------------------

    def ingest(
        self,
        path: Path,
        *,
        source: str = "inbox",
        keep_source: bool = False,
        known: Metadata | None = None,
        topic: str = "",
        dry_run: bool = False,
    ) -> Result:
        """Identify, classify and file one file (or send it to review).

        known: metadata already established (e.g. from arXiv), skipping identification.
        topic: a topic chosen by the caller, skipping classification.
        keep_source: copy instead of move (for files outside the library)."""
        try:
            return self._ingest(path, source, keep_source, known, topic, dry_run)
        except Exception as error:  # one bad file must not stop the whole inbox
            return Result(str(path), "error", f"{type(error).__name__}: {error}")

    def _ingest(self, path, source, keep_source, known, chosen_topic, dry_run) -> Result:
        if path.stat().st_size == 0:
            return self._review_or_report(
                path,
                Proposal(path.name, EMPTY_REASON, sha256(path)),
                keep_source,
                dry_run,
                source,
            )
        parent = self.parent_of(path)
        if parent:
            return self._attach(path, parent, keep_source, dry_run)
        if office.is_office(path):
            return self._ingest_office(path, source, keep_source, known, chosen_topic, dry_run)
        if path.suffix.lower() != ".pdf":
            return self._review_or_report(
                path,
                Proposal(
                    path.name, f"{path.suffix or 'this'} files are not handled yet", sha256(path)
                ),
                keep_source,
                dry_run,
                source,
            )
        return self._ingest_pdf(path, source, keep_source, known, chosen_topic, dry_run)

    def _ingest_office(self, path, source, keep_source, known, chosen_topic, dry_run) -> Result:
        """Convert to PDF, then file like any PDF; the original is kept next to it."""
        with tempfile.TemporaryDirectory(prefix="citadel-office-", dir=_scratch()) as tmp:
            pdf = office.to_pdf(path, Path(tmp))
            if pdf is None:
                return self._review_or_report(
                    path,
                    Proposal(path.name, "LibreOffice could not convert it to PDF", sha256(path)),
                    keep_source,
                    dry_run,
                    source,
                )
            return self._ingest_pdf(
                pdf, source, keep_source, known, chosen_topic, dry_run, original=path
            )

    def _ingest_pdf(
        self, path, source, keep_source, known, chosen_topic, dry_run, original: Path | None = None
    ) -> Result:
        """original: the office document a converted PDF came from. Review shows the
        original, and filing keeps it next to the PDF."""
        shown = original or path

        def review(proposal: Proposal) -> Result:
            if original:
                proposal.file, proposal.sha256 = original.name, sha256(original)
            return self._review_or_report(shown, proposal, keep_source, dry_run, source)

        digest = sha256(path)
        duplicate = self.index.find_sha256(digest)
        if duplicate:
            proposal = Proposal(
                path.name, f"identical to {duplicate}", digest, duplicate_of=duplicate
            )
            return review(proposal)

        pdf = read_pdf(path)
        if known:
            found = Identification(known, 1.0, "given", [f"given: {known.source}"], pdf)
        else:
            found = identify_text(pdf, self.http)
        meta = found.metadata
        if meta and pdf.pages >= 150 and meta.source == "pdf":
            meta.type = "book"
        if meta and not dry_run:
            sources.enrich(self.http, meta)

        matches = []
        if meta and meta.doi and (existing := self.index.find_doi(meta.doi)):
            matches.append((existing, f"same DOI as {existing}"))
        if meta and not matches and (existing := self._same_title(meta)):
            matches.append((existing, f"same title, year and author as {existing}"))
        for existing, reason in matches:
            if not self.index.get(existing)["has_pdf"]:
                # The PDF of a reference filed without one (metadata only): fill it in.
                if found.confidence >= self.config.ingest.review_threshold:
                    return self._complete(
                        shown, path, existing, digest, keep_source, dry_run, original
                    )
                reason = f"looks like {existing}, which has no PDF yet: accept to attach"
            proposal = self._proposal(path, digest, found, reason, pdf)
            proposal.duplicate_of = existing
            return review(proposal)

        if chosen_topic:
            topic, topic_confidence, explain = chosen_topic, 1.0, "chosen"
        else:
            topic, topic_confidence, explain = self._classify(meta, pdf)

        problems = []
        if meta is None:
            problems.append("could not identify it")
        elif found.confidence < self.config.ingest.review_threshold:
            problems.append(f"metadata confidence {found.confidence:.2f}")
        if not topic:
            problems.append("no topic matched")
        elif topic_confidence < self.config.ingest.topic_threshold:
            problems.append(f"topic confidence {topic_confidence:.2f}")
        if problems:
            proposal = self._proposal(path, digest, found, "; ".join(problems), pdf)
            proposal.topic, proposal.topic_confidence = topic, topic_confidence
            proposal.topic_explain = explain
            return review(proposal)

        if dry_run:
            return Result(
                str(shown),
                "dry-run",
                "would be filed",
                title=meta.title,
                confidence=found.confidence,
                topic=topic,
                topic_confidence=topic_confidence,
            )
        ref = self.file(
            path,
            meta,
            topic,
            found.confidence,
            source,
            digest,
            keep_source=keep_source or original is not None,
            extras=[original] if original else [],
        )
        if original and not keep_source:
            original.unlink()
        return Result(
            str(shown),
            "filed",
            explain,
            ref.citekey,
            ref.folder,
            ref.title,
            found.confidence,
            topic,
            topic_confidence,
        )

    def _complete(
        self,
        shown: Path,
        pdf: Path,
        citekey: str,
        digest: str,
        keep_source: bool,
        dry_run: bool,
        original: Path | None = None,
    ) -> Result:
        """Give a metadata-only reference its PDF (and the original office file, if any)."""
        row = self.index.get(citekey)
        if dry_run:
            return Result(
                str(shown),
                "dry-run",
                f"would add the PDF to {citekey}",
                citekey,
                row["folder"],
                row["title"],
            )
        folder = self.index.root / row["folder"]
        md = folder / f"{folder.name}.md"
        _verified_copy(pdf, folder / f"{folder.name}.pdf", digest)
        if original:
            _verified_copy(
                original, folder / f"{folder.name}{original.suffix.lower()}", sha256(original)
            )
        ref, _ = record.read(md)
        ref.pdf_sha256 = digest
        record.update_metadata(md, ref)
        self.index.add(md)
        if not keep_source:
            shown.unlink(missing_ok=True)
        return Result(
            str(shown),
            "filed",
            f"PDF added to {citekey}",
            citekey,
            row["folder"],
            row["title"],
            1.0,
            row["topic"],
            1.0,
        )

    def attach_pdf(self, citekey: str, path: Path, keep_source: bool = True) -> Result:
        """Add a PDF to a reference that has none, after checking it is that paper (its
        DOI or title must match, unless the reference has neither)."""
        row = self.index.get(citekey)
        if row is None:
            raise LookupError(f"no reference {citekey}")
        if row["has_pdf"]:
            raise ValueError(f"{citekey} already has a PDF")
        found = identify_text(read_pdf(path), self.http)
        data = row["data"]
        meta = found.metadata
        author = naming.surname(meta.authors[0]) if meta and meta.authors else ""
        wanted = title_key(row["title"], row["year"], row["first_author"])
        # A preprint often appears a year before the journal version.
        years = {meta.year, meta.year + 1, meta.year - 1} if meta and meta.year else {None}
        same = meta and (
            (data.get("doi") and meta.doi == data["doi"])
            or (wanted and wanted in {title_key(meta.title, y, author) for y in years})
            or (
                author.lower() == (row["first_author"] or "").lower()
                and row["year"] in years
                and similar_titles(meta.title, row["title"])
            )
        )
        if not same:
            seen = found.metadata.title[:70] if found.metadata else "nothing recognisable"
            raise ValueError(f"this PDF does not look like {citekey} (it reads as: {seen})")
        return self._complete(path, path, citekey, sha256(path), keep_source, False)

    def _same_title(self, meta: Metadata) -> str | None:
        author = naming.surname(meta.authors[0]) if meta.authors else ""
        key = title_key(meta.title, meta.year, author)
        if key is None:
            return None
        for row in self.index.list():
            if title_key(row["title"], row["year"], row["first_author"]) == key:
                return row["citekey"]
        return None

    def _classify(self, meta: Metadata | None, pdf: PdfText) -> tuple[str, float, str]:
        if meta is None and not pdf.has_text:
            return "", 0.0, "nothing to classify"
        result = self.classifier.classify(
            title=meta.title if meta else "",
            abstract=meta.abstract if meta else "",
            journal=meta.journal if meta else "",
            text=pdf.first_pages,
        )
        return result.topic, result.confidence, result.explain()

    def _proposal(
        self, path: Path, digest: str, found: Identification, reason: str, pdf: PdfText
    ) -> Proposal:
        return Proposal(
            path.name,
            reason,
            digest,
            metadata=asdict(found.metadata) if found.metadata else None,
            confidence=round(found.confidence, 3),
            method=found.method,
            evidence=found.evidence,
        )

    # -- filing --------------------------------------------------------------------

    def new_reference(
        self, meta: Metadata, topic: str, confidence: float, source: str, digest: str = ""
    ) -> Reference:
        """A Reference for new metadata, with a citekey and basename nobody uses yet."""
        if topic not in {t.path for t in self.library.topics()}:
            raise ValueError(f"unknown topic {topic!r}")
        taken_names = self.index.basenames() | _folder_names(self.library.root / topic)
        return Reference(
            citekey=naming.unique(
                naming.citekey(meta.year, meta.title, meta.authors), self.index.citekeys()
            ),
            title=meta.title,
            type=meta.type,
            authors=meta.authors,
            year=meta.year,
            journal=meta.journal,
            volume=meta.volume,
            issue=meta.issue,
            pages=meta.pages,
            publisher=meta.publisher,
            doi=meta.doi,
            arxiv=meta.arxiv,
            isbn=meta.isbn,
            url=meta.url,
            abstract=meta.abstract,
            topic=topic,
            added=dt.date.today().isoformat(),
            source=source,
            metadata_confidence=round(confidence, 3),
            pdf_sha256=digest,
            basename=naming.unique(
                naming.basename(meta.year, meta.title, meta.authors), taken_names, "-"
            ),
        )

    def file(
        self,
        path: Path,
        meta: Metadata,
        topic: str,
        confidence: float,
        source: str,
        digest: str,
        keep_source: bool = False,
        extras: list[Path] = (),
    ) -> Reference:
        """Create the reference folder with the PDF and its record. The source file is
        removed only after its copy has been verified (unless keep_source)."""
        ref = self.new_reference(meta, topic, confidence, source, digest)
        folder = self.library.root / ref.folder
        folder.mkdir(parents=True)  # never reuse an existing folder
        try:
            _verified_copy(path, folder / f"{ref.basename}.pdf", digest)
            for extra in extras:  # e.g. the .docx a PDF was made from
                _verified_copy(
                    extra, folder / f"{ref.basename}{extra.suffix.lower()}", sha256(extra)
                )
            record.write_new(folder / f"{ref.basename}.md", ref)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)  # only what was just created
            raise
        self.index.add(folder / f"{ref.basename}.md")
        if not keep_source:
            path.unlink()
        return ref

    def file_metadata_only(self, meta: Metadata, topic: str, source: str) -> Reference:
        """A reference without a PDF (e.g. `citadel add doi:…` for a paywalled paper)."""
        ref = self.new_reference(meta, topic, 1.0, source)
        folder = self.library.root / ref.folder
        folder.mkdir(parents=True)
        record.write_new(folder / f"{ref.basename}.md", ref)
        self.index.add(folder / f"{ref.basename}.md")
        return ref

    # -- companions: annexes and supplements of a filed reference ---------------------

    def parent_of(self, path: Path) -> str | None:
        """The citekey of the filed reference this file belongs to (an annex of a
        standard, a supplement…), or None. It must call itself an annex/appendix/
        supplement and name its parent's identifier."""
        doc_title = office.title(path) if office.is_office(path) else ""
        if not (COMPANION.search(path.stem.replace("_", " ")) or COMPANION.search(doc_title)):
            return None
        for text in (doc_title, re.sub(r"[-_]Rev\.?", " Rev.", path.stem)):
            for body in standards.BODIES:
                match = body.pattern.search(text)
                if not match:
                    continue
                identifier = standards.tidy(match.group(0))
                for row in self.index.list():
                    if row["title"] == identifier or row["title"].startswith(f"{identifier}:"):
                        return row["citekey"]
        return None

    def _attach(self, path: Path, citekey: str, keep_source: bool, dry_run: bool) -> Result:
        row = self.index.get(citekey)
        if dry_run:
            return Result(
                str(path),
                "dry-run",
                f"would be attached to {citekey}",
                citekey,
                row["folder"],
                row["title"],
            )
        folder = self.index.root / row["folder"]
        attachments = folder / ATTACHMENTS
        attachments.mkdir(exist_ok=True)
        name = _free_name(attachments, path.name)
        _verified_copy(path, attachments / name, sha256(path))
        md = folder / f"{folder.name}.md"
        ref, _ = record.read(md)
        ref.attachments = [*ref.attachments, name]
        record.update_metadata(md, ref)
        self.index.add(md)
        if not keep_source:
            path.unlink()
        return Result(
            str(path),
            "filed",
            f"attached to {citekey}",
            citekey,
            f"{row['folder']}/{ATTACHMENTS}/{name}",
            row["title"],
            1.0,
            row["topic"],
            1.0,
        )

    # -- adding by hand or by agents ---------------------------------------------

    def add(self, target: str, topic: str = "", source: str = "add", move: bool = False) -> Result:
        """Add a PDF path, a DOI (doi:… or bare) or an arXiv id/URL to the library.

        A PDF is copied (moved with move=True). An arXiv paper is downloaded. A DOI
        alone makes a metadata-only reference, filed only when the topic is clear."""
        kind, value = parse_target(target)
        if kind == "pdf":
            path = Path(value).expanduser()
            if not path.is_file():
                return Result(value, "error", "not a file")
            return self._done(self.ingest(path, source=source, keep_source=not move, topic=topic))
        if kind == "arxiv":
            meta = sources.arxiv(self.http, value)
            if meta is None:
                return Result(f"arxiv:{value}", "error", "arXiv does not know this id")
            if meta.doi:  # the published version has richer metadata
                published = sources.crossref(self.http, meta.doi)
                if published:
                    published.arxiv = value
                    meta = published
            with tempfile.TemporaryDirectory(prefix="citadel-add-") as tmp:
                pdf = Path(tmp) / f"{value.replace('/', '_')}.pdf"
                self.http.download(f"https://arxiv.org/pdf/{value}", pdf)
                result = self.ingest(pdf, source=source, known=meta, topic=topic)
            result.file = f"arxiv:{value}"
            return self._done(result)
        return self._done(self._add_doi(value, topic, source))

    def _add_doi(self, doi: str, topic: str, source: str) -> Result:
        """A DOI: with its open-access PDF when one exists, else metadata only."""
        from .graph import Graph, Work

        meta = sources.crossref(self.http, doi)
        if meta is None:
            return Result(f"doi:{doi}", "error", "Crossref does not know this DOI")
        work = Graph(self.http, self.index).lookup(doi=meta.doi) or Work("", meta)
        work.meta = meta  # Crossref's record, with OpenAlex's PDF link and ids
        return self.fetch(work, topic=topic, source=source)

    def fetch(self, work, topic: str = "", fallback_topic: str = "", source: str = "add") -> Result:
        """Bring a work found in the citation graph (graph.Work) into the library: its
        open-access PDF when there is one, else a metadata-only reference.

        fallback_topic: where to file it when the topic is unclear, typically the topic
        of the paper it was found in."""
        from .graph import Graph

        meta = work.meta
        label = f"doi:{meta.doi}" if meta.doi else meta.title[:60]
        existing = (
            work.citekey
            or (self.index.find_doi(meta.doi) if meta.doi else "")
            or self._same_title(meta)
        )
        if existing:
            return Result(label, "error", f"already in the library as {existing}", existing)
        if meta.doi and meta.source != "crossref":
            richer = sources.crossref(self.http, meta.doi)
            if richer:
                richer.arxiv = richer.arxiv or meta.arxiv
                meta = work.meta = richer
        sources.enrich(self.http, meta)
        if not topic:
            found = self.classifier.classify(meta.title, meta.abstract, meta.journal)
            if found.topic and found.confidence >= self.config.ingest.topic_threshold:
                topic = found.topic
            elif fallback_topic:
                topic = fallback_topic
        with tempfile.TemporaryDirectory(prefix="citadel-fetch-", dir=_scratch()) as tmp:
            pdf = Path(tmp) / f"{naming.basename(meta.year, meta.title, meta.authors)}.pdf"
            if Graph(self.http, self.index).download(work, pdf):
                result = self.ingest(pdf, source=source, known=meta, topic=topic)
                result.file = label
                return self._done(result)
        if not topic:
            return Result(
                label,
                "error",
                "no open-access PDF, and the topic is unclear: choose a topic",
                title=meta.title,
            )
        ref = self.file_metadata_only(meta, topic, source)
        return self._done(
            Result(
                label,
                "filed",
                "metadata only (no open-access PDF)",
                ref.citekey,
                ref.folder,
                ref.title,
                1.0,
                topic,
                1.0,
            )
        )

    def _done(self, result: Result) -> Result:
        if result.status == "filed":
            self.index.reindex()
        return result

    # -- review --------------------------------------------------------------------

    def _review_or_report(
        self,
        path: Path,
        proposal: Proposal,
        keep_source: bool,
        dry_run: bool,
        source: str = "inbox",
    ) -> Result:
        meta = proposal.metadata or {}
        result = Result(
            str(path),
            "dry-run" if dry_run else "review",
            proposal.reason,
            title=meta.get("title", ""),
            confidence=proposal.confidence,
            topic=proposal.topic,
            topic_confidence=proposal.topic_confidence,
        )
        if dry_run:
            return result
        review = self.library.review
        review.mkdir(exist_ok=True)
        name = _free_name(review, review_name(proposal, path.name))
        proposal.original_name = proposal.original_name or path.name
        proposal.file = name
        proposal.original_path = str(path)
        proposal.added = dt.date.today().isoformat()
        proposal.source = source
        _verified_copy(path, review / name, proposal.sha256)
        proposal.save(review)
        if not keep_source:
            path.unlink()
        result.folder = f"{review.name}/{name}"
        return result

    def review_items(self) -> list[Proposal]:
        review = self.library.review
        if not review.is_dir():
            return []
        items = []
        for file in sorted(review.glob(f"*{PROPOSAL_SUFFIX}")):
            proposal = Proposal.load(file)
            if (review / proposal.file).exists():
                items.append(proposal)
        return items

    def find_review(self, name: str) -> Proposal:
        """A review item by its file name or the name it arrived with (main.pdf), whole
        or a unique prefix."""
        items = self.review_items()
        exact = [p for p in items if name in (p.file, p.original_name)]
        matches = exact or [
            p for p in items if p.file.startswith(name) or (p.original_name or "").startswith(name)
        ]
        if len(matches) != 1:
            raise LookupError(f"{len(matches)} review items match {name!r}")
        return matches[0]

    def accept(
        self, name: str, topic: str = "", doi: str = "", type: str = "", force: bool = False
    ) -> Result:
        """File a review item, optionally correcting its topic, DOI (metadata) or type.
        force: file it even though it was flagged as a duplicate (the duplicates
        screen does this for the copy the user keeps)."""
        proposal = self.find_review(name)
        path = self.library.review / proposal.file
        if proposal.duplicate_of and not force:
            target = self.index.get(proposal.duplicate_of)
            if target and not target["has_pdf"] and path.suffix.lower() == ".pdf":
                result = self._complete(
                    path, path, proposal.duplicate_of, proposal.sha256, False, False
                )
                (self.library.review / f"{proposal.file}{PROPOSAL_SUFFIX}").unlink(missing_ok=True)
                return result
            raise ValueError(
                f"{proposal.file} duplicates {proposal.duplicate_of}: reject it instead"
            )
        if doi:
            meta = sources.crossref(self.http, doi.strip())
            if meta is None:
                raise LookupError(f"Crossref does not know {doi}")
            confidence = 1.0
        elif proposal.metadata:
            meta = Metadata(**proposal.metadata)
            confidence = proposal.confidence
        else:
            raise ValueError(f"{proposal.file} has no metadata: accept it with --doi")
        if type:
            meta.type = type
        topic = topic or proposal.topic
        if not topic:
            raise ValueError(f"{proposal.file} has no topic: accept it with --topic")
        if path.suffix.lower() != ".pdf" and not office.is_office(path):
            raise ValueError(f"{path.suffix} files cannot be filed yet")
        if office.is_office(path):
            with tempfile.TemporaryDirectory(prefix="citadel-office-", dir=_scratch()) as tmp:
                pdf = office.to_pdf(path, Path(tmp))
                if pdf is None:
                    raise ValueError("LibreOffice could not convert it to PDF")
                ref = self.file(
                    pdf,
                    meta,
                    topic,
                    confidence,
                    proposal.source,
                    sha256(pdf),
                    keep_source=True,
                    extras=[path],
                )
            path.unlink()
        else:
            ref = self.file(path, meta, topic, confidence, proposal.source, proposal.sha256)
        (self.library.review / f"{proposal.file}{PROPOSAL_SUFFIX}").unlink(missing_ok=True)
        self.index.reindex()
        return Result(
            str(path),
            "filed",
            "accepted",
            ref.citekey,
            ref.folder,
            ref.title,
            confidence,
            topic,
            1.0,
        )

    def retry(self, name: str = "") -> list[Result]:
        """Run review items through identification and classification again (after
        better rules, new topics or keywords). Each file is first copied to local
        scratch space and taken out of review; if anything fails it is put back."""
        items = [self.find_review(name)] if name else self.review_items()
        results = []
        scratch = paths.CACHE_DIR / "retry"
        scratch.mkdir(parents=True, exist_ok=True)
        filed = False
        for proposal in items:
            name = Path(proposal.file.lower())
            if proposal.duplicate_of or not (name.suffix == ".pdf" or office.is_office(name)):
                continue  # nothing new to learn about these
            path = self.library.review / proposal.file
            sidecar = self.library.review / f"{proposal.file}{PROPOSAL_SUFFIX}"
            working = scratch / proposal.file
            working.unlink(missing_ok=True)
            _verified_copy(path, working, proposal.sha256)
            path.unlink()
            sidecar.unlink(missing_ok=True)
            # Metadata corrected by hand is kept; only the topic is worked out again.
            known = (
                Metadata(**proposal.metadata)
                if proposal.method == "manual" and proposal.metadata
                else None
            )
            result = self.ingest(working, source=proposal.source, known=known)
            if result.status == "error":  # put it back as it was
                _verified_copy(working, path, proposal.sha256)
                proposal.save(self.library.review)
                working.unlink()
            elif result.status == "review":
                self._keep_original_name(result, proposal.original_name or proposal.file)
            result.file = proposal.original_name or proposal.file
            filed = filed or result.status == "filed"
            results.append(result)
        if filed:
            self.index.reindex()
        return results

    def edit_review(self, name: str, **changes) -> Proposal:
        """Correct a review item's metadata before it is filed (its file name and
        citekey are made from it, and never change afterwards). Hand-checked
        metadata counts as certain."""
        proposal = self.find_review(name)
        known = set(Metadata.__dataclass_fields__)
        unknown = set(changes) - known
        if unknown:
            raise ValueError(f"cannot edit {', '.join(sorted(unknown))}")
        base = proposal.metadata or {"title": Path(proposal.file).stem}
        meta = Metadata(**{k: v for k, v in base.items() if k in known})
        for key, value in changes.items():
            setattr(meta, key, value)
        meta.source = "manual"
        proposal.metadata = asdict(meta)
        proposal.confidence, proposal.method = 1.0, "manual"
        proposal.reason = "metadata corrected by hand"
        return self._rename_review(proposal)

    def _rename_review(self, proposal: Proposal) -> Proposal:
        """Give a review item the name its (new) metadata implies, and save it."""
        review = self.library.review
        old = proposal.file
        wanted = review_name(proposal, old)
        if wanted != old:
            new = _free_name(review, wanted)
            (review / old).rename(review / new)
            (review / f"{old}{PROPOSAL_SUFFIX}").unlink(missing_ok=True)
            proposal.original_name = proposal.original_name or old
            proposal.file = new
        proposal.save(review)
        return proposal

    def _keep_original_name(self, result: Result, original: str) -> None:
        name = result.folder.rsplit("/", 1)[-1]
        sidecar = self.library.review / f"{name}{PROPOSAL_SUFFIX}"
        if sidecar.exists():
            proposal = Proposal.load(sidecar)
            proposal.original_name = original
            proposal.save(self.library.review)

    def reject(self, name: str) -> Result:
        """Not a reference to keep: an exact duplicate is deleted, anything else goes to
        _attachments/ (it might still be useful)."""
        proposal = self.find_review(name)
        path = self.library.review / proposal.file
        sidecar = self.library.review / f"{proposal.file}{PROPOSAL_SUFFIX}"
        if proposal.duplicate_of and self.index.find_sha256(proposal.sha256):
            path.unlink()
            sidecar.unlink(missing_ok=True)
            return Result(str(path), "deleted", f"exact duplicate of {proposal.duplicate_of}")
        if path.stat().st_size == 0:  # nothing in it to keep
            path.unlink()
            sidecar.unlink(missing_ok=True)
            return Result(str(path), "deleted", "empty file")
        attachments = self.library.attachments
        attachments.mkdir(exist_ok=True)
        # Not a reference: it goes back to the name it arrived with.
        target = attachments / _free_name(attachments, proposal.original_name or proposal.file)
        _verified_copy(path, target, proposal.sha256)
        path.unlink()
        sidecar.unlink(missing_ok=True)
        return Result(
            str(path), "kept", "moved to attachments", folder=f"{attachments.name}/{target.name}"
        )


# -- helpers -----------------------------------------------------------------------


def _verified_copy(source: Path, target: Path, digest: str) -> None:
    """Copy through a temporary name and check the hash before it gets its real name."""
    if target.exists():
        raise FileExistsError(target)
    temporary = target.with_name(f".{target.name}.part")
    shutil.copyfile(source, temporary)
    if sha256(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise OSError(f"copy of {source.name} does not match the original")
    temporary.replace(target)


def _free_name(folder: Path, name: str) -> str:
    """name, or name with ' (2)', ' (3)'… before the suffix if it is taken."""
    candidate, stem, suffix, number = name, Path(name).stem, Path(name).suffix, 2
    while (folder / candidate).exists() or (folder / f"{candidate}{PROPOSAL_SUFFIX}").exists():
        candidate = f"{stem} ({number}){suffix}"
        number += 1
    return candidate


def _folder_names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir() if p.is_dir()} if folder.is_dir() else set()


def parse_target(target: str) -> tuple[str, str]:
    """('pdf'|'doi'|'arxiv', value) for what `add` was given."""
    lowered = target.lower()
    if lowered.startswith("doi:") or (find_dois(target) and not Path(target).exists()):
        return "doi", clean_doi(target[4:] if lowered.startswith("doi:") else target)
    if lowered.startswith("arxiv:") or "arxiv.org/" in lowered:
        for pattern in (ARXIV_NEW, ARXIV_OLD):
            match = pattern.search(target if "arxiv" in lowered else f"arxiv:{target}")
            if match:
                return "arxiv", match.group(1)
    return "pdf", target


def _scratch() -> Path:
    folder = paths.CACHE_DIR / "ingest"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def title_key(title: str, year, author: str) -> str | None:
    """Same title, year and first author: a duplicate even with other bytes and no DOI."""
    words = normal_words(title)
    if len(words) < 3 or not author:
        return None
    return f"{' '.join(words)}|{year or ''}|{author.lower()}"


def similar_titles(a: str, b: str, threshold: float = 0.8) -> bool:
    """Nearly the same words (a preprint and its journal version differ in spelling,
    punctuation or LaTeX: magnetised/magnetized, "--"/"-", ${\\cal M}_s$/Sonic Mach)."""

    def words(title: str) -> set[str]:
        plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
        return {
            re.sub(r"is(e|ed|es|ing|ation)$", r"iz\1", w) for w in re.findall(r"[a-z0-9]+", plain)
        }

    first, second = words(a), words(b)
    if len(first) < 3 or len(second) < 3:
        return False
    return len(first & second) / len(first | second) >= threshold


def review_name(proposal: Proposal, fallback: str) -> str:
    """A review item's file name: what it will be called once filed, when its metadata
    is known (2015_Weightfield2FastSimulatorSiliconDiamond_Cenna.pdf), else its own."""
    meta = proposal.metadata or {}
    if not meta.get("title"):
        return fallback
    return (
        naming.basename(meta.get("year"), meta["title"], meta.get("authors") or [])
        + Path(fallback).suffix.lower()
    )
