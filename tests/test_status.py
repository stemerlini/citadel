import json

from citadel import status
from citadel.status import Gpu, StatusWriter


def test_a_run_from_start_to_end(tmp_path):
    loads = iter([10.0, 90.0, 95.0, 97.0] * 10)
    writer = StatusWriter(
        tmp_path / "status.json",
        interval=60,
        sampler=lambda: Gpu("RTX", next(loads), 4000, 8000, 70, 120, 165),
    )
    writer.begin(3, references=10, converted=4)
    writer.start_item("a2020x", "A paper", 12)
    writer.sample()
    writer.write()
    live = status.read(tmp_path / "status.json")
    assert live.state == "converting" and live.current_title == "A paper"
    assert live.gpu["memory_total"] == 8000 and live.gpu_history

    writer.finish_item(True, 12)
    writer.start_item("b2021y", "B", 5)
    writer.finish_item(False)
    writer.end()
    done = json.loads((tmp_path / "status.json").read_text())
    assert done["state"] == "idle" and done["finished"] > 0
    assert (done["done"], done["failed"], done["pages_done"]) == (2, 1, 12)
    assert done["library_converted"] == 5 and done["current_title"] == ""
    assert done["papers_per_minute"] > 0 and done["eta_seconds"] > 0


def test_history_is_bounded(tmp_path):
    writer = StatusWriter(tmp_path / "s.json", sampler=lambda: Gpu(load=50))
    for _ in range(status.HISTORY + 20):
        writer.sample()
    assert len(writer.status.gpu_history) == status.HISTORY


def test_no_gpu(tmp_path):
    writer = StatusWriter(tmp_path / "s.json", sampler=lambda: None)
    writer.sample()
    assert writer.status.gpu is None


def test_parsing_nvidia_smi(monkeypatch):
    class Done:
        returncode = 0
        stdout = "NVIDIA GeForce RTX 4060 Ti, 97, 4409, 8188, 71, 142.5, [N/A]\n"

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Done())
    gpu = status.sample_gpu()
    assert (gpu.name, gpu.load, gpu.memory_used, gpu.power, gpu.power_limit) == (
        "NVIDIA GeForce RTX 4060 Ti",
        97.0,
        4409.0,
        142.5,
        0.0,
    )


def test_missing_or_broken_file(tmp_path):
    assert status.read(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{")
    assert status.read(tmp_path / "bad.json") is None
