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
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import naming, record, sources
from .classify import Classifier
from .config import Config
from .identify import Identification, identify_text
from .index import Index
from .library import Library
from .pdf import PdfText, sha256
from .pdf import read as read_pdf
from .record import Reference
from .sources import Http, Metadata

PROPOSAL_SUFFIX = ".citadel.json"
TEMPORARY_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial", ".opdownload")


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
            if now - path.stat().st_mtime < self.config.ingest.settle_seconds:
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
        if path.suffix.lower() != ".pdf":
            return self._review_or_report(
                path,
                Proposal(path.name, "only PDFs are identified so far", sha256(path)),
                keep_source,
                dry_run,
                source,
            )

        digest = sha256(path)
        duplicate = self.index.find_sha256(digest)
        if duplicate:
            proposal = Proposal(
                path.name, f"identical to {duplicate}", digest, duplicate_of=duplicate
            )
            return self._review_or_report(path, proposal, keep_source, dry_run, source)

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

        if meta and meta.doi:
            existing = self.index.find_doi(meta.doi)
            if existing:
                proposal = self._proposal(path, digest, found, f"same DOI as {existing}", pdf)
                proposal.duplicate_of = existing
                return self._review_or_report(path, proposal, keep_source, dry_run, source)

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
            return self._review_or_report(path, proposal, keep_source, dry_run, source)

        if dry_run:
            return Result(
                str(path),
                "dry-run",
                "would be filed",
                title=meta.title,
                confidence=found.confidence,
                topic=topic,
                topic_confidence=topic_confidence,
            )
        ref = self.file(path, meta, topic, found.confidence, source, digest, keep_source)
        return Result(
            str(path),
            "filed",
            explain,
            ref.citekey,
            ref.folder,
            ref.title,
            found.confidence,
            topic,
            topic_confidence,
        )

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
    ) -> Reference:
        """Create the reference folder with the PDF and its record. The source file is
        removed only after its copy has been verified (unless keep_source)."""
        ref = self.new_reference(meta, topic, confidence, source, digest)
        folder = self.library.root / ref.folder
        folder.mkdir(parents=True)  # never reuse an existing folder
        try:
            _verified_copy(path, folder / f"{ref.basename}.pdf", digest)
            record.write_new(folder / f"{ref.basename}.md", ref)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)  # only what was just created
            raise
        if not keep_source:
            path.unlink()
        return ref

    def file_metadata_only(self, meta: Metadata, topic: str, source: str) -> Reference:
        """A reference without a PDF (e.g. `citadel add doi:…` for a paywalled paper)."""
        ref = self.new_reference(meta, topic, 1.0, source)
        folder = self.library.root / ref.folder
        folder.mkdir(parents=True)
        record.write_new(folder / f"{ref.basename}.md", ref)
        return ref

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
        name = _free_name(review, path.name)
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
        """A review item by file name or unique prefix."""
        items = self.review_items()
        exact = [p for p in items if p.file == name]
        matches = exact or [p for p in items if p.file.startswith(name)]
        if len(matches) != 1:
            raise LookupError(f"{len(matches)} review items match {name!r}")
        return matches[0]

    def accept(self, name: str, topic: str = "", doi: str = "", type: str = "") -> Result:
        """File a review item, optionally correcting its topic, DOI (metadata) or type."""
        proposal = self.find_review(name)
        path = self.library.review / proposal.file
        if proposal.duplicate_of:
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
        if path.suffix.lower() != ".pdf":
            raise ValueError("only PDFs can be filed so far")
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
        attachments = self.library.attachments
        attachments.mkdir(exist_ok=True)
        target = attachments / _free_name(attachments, proposal.file)
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
