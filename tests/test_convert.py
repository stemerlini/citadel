"""The converter against a fake worker that speaks the real protocol."""

import sys
import textwrap

import pytest

from citadel import convert as convert_module
from citadel import record
from citadel.config import Config
from citadel.convert import Converter, ConvertError
from citadel.index import Index
from citadel.status import StatusWriter
from conftest import file_ref, make_ref

FAKE_WORKER = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    print(json.dumps({"event": "ready", "engine": "marker-test"}), flush=True)
    for line in sys.stdin:
        job = json.loads(line)
        print("a stray library print", flush=True)
        if "broken" in job["pdf"].lower():
            print(json.dumps({"event": "error", "id": job["id"], "error": "bad PDF"}),
                  flush=True)
            continue
        out = Path(job["out"]); out.mkdir(parents=True, exist_ok=True)
        (out / "fig.png").write_bytes(b"png")
        mode = "OCR" if job["ocr"] else "text"
        (out / "fulltext.md").write_text(
            "## Statistical theory of magnetohydrodynamic turbulence\\n\\n"
            f"Body via {mode}.\\n\\n$$E = k^2 \\\\quad (1)$$\\n\\n![](fig.png)\\n")
        print(json.dumps({"event": "done", "id": job["id"], "markdown": str(out / "fulltext.md"),
                          "images": ["fig.png"], "seconds": 1.5}), flush=True)
""")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    worker = tmp_path / "worker.py"
    worker.write_text(FAKE_WORKER)
    monkeypatch.setattr(convert_module, "WORKER", worker)
    monkeypatch.setattr(convert_module, "_pages", lambda folder: 7)
    monkeypatch.setattr(convert_module, "pdftotext", lambda path, options: "clean text")
    monkeypatch.setattr(convert_module.paths, "CACHE_DIR", tmp_path / "cache")
    root = tmp_path / "lib"
    file_ref(root, make_ref())
    file_ref(root, make_ref(citekey="broken2020x", title="Broken", topic="optics"))
    file_ref(root, make_ref(citekey="nopdf2020x", title="No PDF", topic="optics"), pdf=False)
    index = Index(tmp_path / "index.db", root)
    index.reindex()
    config = Config()
    config.convert.python = sys.executable
    writer = StatusWriter(tmp_path / "status.json", sampler=lambda: None)
    yield Converter(index, config, writer), index, root, writer
    index.close()


def test_pending_needs_a_pdf_and_no_fulltext(setup):
    converter, *_ = setup
    assert sorted(j.citekey for j in converter.pending()) == ["broken2020x", "verma2004statistical"]


def test_conversion_fills_the_record_and_figures(setup):
    converter, index, root, writer = setup
    [job] = converter.pending(["verma2004statistical"])
    [result] = converter.run([job])
    assert result.ok and result.figures == 1 and not result.ocr
    ref, body = record.read(job.markdown)
    text = record.fulltext(body)
    assert text.startswith("Body via text.")  # the duplicate title heading is gone
    assert "\\tag{1}" in text and "![](figures/fig.png)" in text
    assert body.rstrip().endswith("## Notes")
    assert ref.md_engine == "marker-test"
    assert (job.folder / "figures" / "fig.png").exists()
    assert not (job.folder / ".figures.new").exists()
    assert index.get("verma2004statistical")["has_fulltext"] == 1
    assert writer.status.state == "idle" and writer.status.done == 1
    assert index.search("body via text")


def test_garbled_text_layers_are_ocrd(setup, monkeypatch):
    converter, *_ = setup
    monkeypatch.setattr(convert_module, "pdftotext", lambda path, options: "ð Þ ¼ " * 5)
    [job] = converter.pending(["verma2004statistical"])
    [result] = converter.run([job])
    assert result.ocr
    assert "Body via OCR" in record.read(job.markdown)[1]
    assert record.read(job.markdown)[0].md_engine == "marker-test (ocr)"


def test_a_failure_leaves_the_record_untouched_and_the_run_continues(setup):
    converter, index, root, _ = setup
    jobs = converter.pending()
    broken = next(j for j in jobs if j.citekey == "broken2020x")
    before = broken.markdown.read_text()
    results = {r.citekey: r for r in converter.run(jobs)}
    assert not results["broken2020x"].ok and results["broken2020x"].error == "bad PDF"
    assert results["verma2004statistical"].ok
    assert broken.markdown.read_text() == before
    assert not (broken.folder / "figures").exists()


def test_reconverting_replaces_figures_and_keeps_notes(setup):
    converter, index, *_ = setup
    [job] = converter.pending(["verma2004statistical"])
    converter.run([job])
    job.markdown.write_text(job.markdown.read_text() + "My notes.\n")
    (job.folder / "figures" / "old.png").write_bytes(b"old")
    [job] = converter.pending(["verma2004statistical"], force=True)
    converter.run([job])
    assert sorted(p.name for p in (job.folder / "figures").iterdir()) == ["fig.png"]
    assert job.markdown.read_text().endswith("My notes.\n")


def test_missing_environment(setup):
    converter, *_ = setup
    converter.config.convert.python = "/nonexistent/python"
    with pytest.raises(ConvertError, match="environment is missing"):
        converter.run(converter.pending())
    assert converter.status.status.state == "idle"
