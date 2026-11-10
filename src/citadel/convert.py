"""Converting filed PDFs to Markdown with the Marker worker.

For each reference with a PDF but no full text, the worker (convert_worker.py,
in Marker's own environment) writes Markdown and figures to a scratch folder.
Citadel then cleans the Markdown (clean.py), puts it between the full-text
markers of the reference's record, swaps in the figures/ folder, and notes the
engine in the frontmatter. Nothing in the reference folder changes until a
conversion has fully succeeded.

Progress goes to status.json for the desktop widget (status.py).
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths, record
from .clean import clean, text_layer_is_garbled
from .config import Config
from .index import Index
from .pdf import pdftotext
from .status import StatusWriter

WORKER = Path(__file__).with_name("convert_worker.py")
READY_TIMEOUT = 900  # seconds: the first run downloads the models (~1.5 GB)
FIGURES = "figures"


class ConvertError(Exception):
    """The worker could not be started or stopped answering."""


@dataclass
class Converted:
    citekey: str
    ok: bool
    seconds: float = 0.0
    pages: int = 0
    ocr: bool = False
    figures: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Job:
    citekey: str
    title: str
    folder: Path  # the reference folder
    pages: int

    @property
    def pdf(self) -> Path:
        return self.folder / f"{self.folder.name}.pdf"

    @property
    def markdown(self) -> Path:
        return self.folder / f"{self.folder.name}.md"


class Converter:
    def __init__(self, index: Index, config: Config, status: StatusWriter | None = None):
        self.index = index
        self.config = config
        self.status = status or StatusWriter()

    # -- what to convert --------------------------------------------------------------

    def pending(
        self, citekeys: list[str] | None = None, force: bool = False, limit: int | None = None
    ) -> list[Job]:
        """References that have a PDF and (unless force) no full text yet."""
        rows = (
            [r for key in citekeys if (r := self.index.get(key))]
            if citekeys
            else self.index.list(sort="added")
        )
        jobs = []
        for row in rows:
            if not row["has_pdf"] or (row["has_fulltext"] and not force):
                continue
            folder = self.index.path(row)
            jobs.append(Job(row["citekey"], row["title"], folder, _pages(folder)))
            if limit and len(jobs) >= limit:
                break
        return jobs

    def needs_ocr(self, job: Job) -> bool:
        mode = self.config.convert.ocr
        if mode in ("always", "never"):
            return mode == "always"
        return text_layer_is_garbled(pdftotext(job.pdf, ["-l", "3"]))

    # -- running ----------------------------------------------------------------------

    def run(self, jobs: list[Job], on_result=None) -> list[Converted]:
        """Convert the jobs in one worker session. on_result(Converted) after each."""
        if not jobs:
            return []
        stats = self.index.stats()
        converted = sum(1 for r in self.index.list() if r["has_fulltext"])
        self.status.begin(len(jobs), stats["references"], converted)
        results: list[Converted] = []
        worker = None
        try:
            worker = _Worker(self.config)
            for job in jobs:
                result = self._convert(worker, job)
                results.append(result)
                self.status.finish_item(result.ok, job.pages)
                if on_result:
                    on_result(result)
        finally:
            if worker:
                worker.close()
            self.status.end()
            if any(r.ok for r in results):
                self.index.reindex()
        return results

    def _convert(self, worker: _Worker, job: Job) -> Converted:
        ocr = self.needs_ocr(job)
        self.status.start_item(job.citekey, job.title, job.pages)
        with tempfile.TemporaryDirectory(prefix="citadel-convert-", dir=_scratch_dir()) as tmp:
            event = worker.convert(job.citekey, job.pdf, Path(tmp), ocr)
            if event.get("event") != "done":
                return Converted(
                    job.citekey,
                    False,
                    pages=job.pages,
                    ocr=ocr,
                    error=event.get("error", "no answer from the worker"),
                )
            try:
                figures = self._store(
                    job,
                    Path(event["markdown"]),
                    event.get("images", []),
                    f"{worker.engine}{' (ocr)' if ocr else ''}",
                )
            except (OSError, record.RecordError) as error:
                return Converted(
                    job.citekey,
                    False,
                    pages=job.pages,
                    ocr=ocr,
                    error=f"{type(error).__name__}: {error}",
                )
        return Converted(job.citekey, True, event.get("seconds", 0.0), job.pages, ocr, figures)

    def _store(self, job: Job, markdown: Path, images: list[str], engine: str) -> int:
        """Put a finished conversion into the reference folder."""
        ref, _ = record.read(job.markdown)
        text = clean(markdown.read_text(encoding="utf-8"), ref.title, FIGURES)

        # Figures first, under a temporary name: the record only points at them once
        # they are all in place, and an older figures/ folder is replaced as a whole.
        fresh = job.folder / f".{FIGURES}.new"
        shutil.rmtree(fresh, ignore_errors=True)
        if images:
            fresh.mkdir()
            for name in images:
                shutil.copyfile(markdown.parent / name, fresh / name)
        record.update_fulltext(job.markdown, text)
        ref.md_engine = engine
        record.update_metadata(job.markdown, ref)
        old = job.folder / FIGURES
        if old.exists():
            shutil.rmtree(old)
        if images:
            fresh.rename(old)
        return len(images)


class _Worker:
    """The Marker worker process: started once per run, models loaded once."""

    def __init__(self, config: Config):
        python = config.convert_python
        if not python.exists():
            raise ConvertError(f"Marker's environment is missing ({python}); see README")
        self.process = subprocess.Popen(
            [str(python), str(WORKER), "--device", config.convert.device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(_log_file(), "ab"),
            text=True,
            bufsize=1,
        )
        # One reader thread for the worker's whole life: lines go into a queue, so
        # waiting with a timeout never leaves a second reader on the same pipe.
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        ready = self._next_event(READY_TIMEOUT)
        if ready.get("event") != "ready":
            self.close()
            raise ConvertError(f"the Marker worker did not start (see {_log_file()})")
        self.engine = ready.get("engine", "marker")

    def convert(self, job_id: str, pdf: Path, out: Path, ocr: bool) -> dict:
        self.process.stdin.write(
            json.dumps({"id": job_id, "pdf": str(pdf), "out": str(out), "ocr": ocr}) + "\n"
        )
        self.process.stdin.flush()
        while True:
            # A very long book can take a while; the worker reports nothing mid-document.
            event = self._next_event(timeout=3600)
            if event.get("id") == job_id and event.get("event") in ("done", "error"):
                return event
            if not event:
                return {"event": "error", "error": "the worker stopped"}

    def _read(self) -> None:
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put("")  # end of output: the worker exited

    def _next_event(self, timeout: float) -> dict:
        """The next JSON event from the worker, or {} on timeout or exit."""
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                return {}
            if not line:
                return {}
            try:
                return json.loads(line)
            except ValueError:
                continue  # not a protocol line (a stray print from a library)

    def close(self) -> None:
        """Stop the worker; it stops llama-server and frees the GPU on the way out."""
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.process.kill()


def _pages(folder: Path) -> int:
    from .pdf import _pdfinfo

    try:
        return int(_pdfinfo(folder / f"{folder.name}.pdf").get("Pages", "0") or 0)
    except ValueError:
        return 0


def _scratch_dir() -> Path:
    """Local scratch space (never the OneDrive mount)."""
    folder = paths.CACHE_DIR / "convert"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _log_file() -> Path:
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return paths.CACHE_DIR / "convert-worker.log"
