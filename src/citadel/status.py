"""Live conversion status for the desktop widget and the TUI.

While converting, Citadel writes $XDG_RUNTIME_DIR/citadel/status.json about once
a second: what is being converted, how far the queue is, the throughput, and the
GPU's load, memory, temperature and power (from nvidia-smi). The Omarchy shell
plugin (omarchy/plugin) shows it on the desktop and hides it once the state has
been "idle" for a while. The file lives in the runtime directory: it is live
state, gone after a reboot, and never synced.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

GPU_FIELDS = (
    "name",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
    "power.limit",
)
HISTORY = 60  # GPU load samples kept for the sparkline


def status_file() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/citadel-{os.getuid()}"
    return Path(runtime) / "citadel" / "status.json"


@dataclass
class Gpu:
    name: str = ""
    load: float = 0.0  # %
    memory_used: float = 0.0  # MiB
    memory_total: float = 0.0  # MiB
    temperature: float = 0.0  # °C
    power: float = 0.0  # W
    power_limit: float = 0.0  # W


def sample_gpu() -> Gpu | None:
    """One reading from nvidia-smi, or None without an NVIDIA GPU."""
    try:
        done = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = done.stdout.strip().splitlines()[0] if done.stdout.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if done.returncode != 0 or len(parts) != len(GPU_FIELDS):
        return None

    def number(text: str) -> float:
        try:
            return float(text)
        except ValueError:  # "[N/A]" on some cards
            return 0.0

    return Gpu(parts[0], *(number(p) for p in parts[1:]))


@dataclass
class Status:
    state: str = "idle"  # converting | idle
    total: int = 0  # papers in this run
    done: int = 0
    failed: int = 0
    pages_done: int = 0
    current_citekey: str = ""
    current_title: str = ""
    current_pages: int = 0
    current_started: float = 0.0
    started: float = 0.0
    finished: float = 0.0
    papers_per_minute: float = 0.0
    seconds_per_page: float = 0.0
    eta_seconds: float = 0.0
    library_references: int = 0
    library_converted: int = 0
    gpu: dict | None = None
    gpu_history: list[float] = field(default_factory=list)
    updated: float = 0.0


class StatusWriter:
    """Keeps status.json current while a conversion run is going on.

    A background thread samples the GPU and rewrites the file every `interval`
    seconds; the converter reports progress through begin/start/finish/end."""

    def __init__(self, path: Path | None = None, interval: float = 1.0, sampler=sample_gpu):
        self.path = path or status_file()
        self.interval = interval
        self.sampler = sampler
        self.status = Status()
        self._history: deque[float] = deque(maxlen=HISTORY)
        self._lock = threading.Lock()  # the status fields
        self._file_lock = threading.Lock()  # the file: one writer at a time
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- progress ------------------------------------------------------------------

    def begin(self, total: int, references: int = 0, converted: int = 0) -> None:
        with self._lock:
            self.status = Status(
                state="converting",
                total=total,
                started=time.time(),
                library_references=references,
                library_converted=converted,
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="citadel-status", daemon=True)
        self._thread.start()

    def start_item(self, citekey: str, title: str, pages: int) -> None:
        with self._lock:
            s = self.status
            s.current_citekey, s.current_title, s.current_pages = citekey, title, pages
            s.current_started = time.time()

    def finish_item(self, ok: bool, pages: int = 0) -> None:
        with self._lock:
            s = self.status
            s.done += 1
            if ok:
                s.pages_done += pages
                s.library_converted += 1
            else:
                s.failed += 1
            elapsed = max(time.time() - s.started, 1e-6)
            s.papers_per_minute = s.done / elapsed * 60
            s.seconds_per_page = elapsed / s.pages_done if s.pages_done else 0.0
            remaining = s.total - s.done
            s.eta_seconds = remaining * elapsed / s.done if s.done else 0.0
            s.current_citekey = s.current_title = ""
            s.current_pages = 0

    def end(self) -> None:
        with self._lock:
            self.status.state = "idle"
            self.status.finished = time.time()
            self.status.current_citekey = self.status.current_title = ""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.write()

    # -- writing -------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self.write()
            self._stop.wait(self.interval)

    def sample(self) -> None:
        gpu = self.sampler()
        with self._lock:
            if gpu:
                self._history.append(gpu.load)
                self.status.gpu = asdict(gpu)
                self.status.gpu_history = list(self._history)

    def write(self) -> None:
        with self._lock:
            self.status.updated = time.time()
            data = json.dumps(asdict(self.status))
        with self._file_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            temporary.write_text(data)
            temporary.replace(self.path)


def read(path: Path | None = None) -> Status | None:
    try:
        data = json.loads((path or status_file()).read_text())
    except (OSError, ValueError):
        return None
    known = Status.__dataclass_fields__
    return Status(**{k: v for k, v in data.items() if k in known})
