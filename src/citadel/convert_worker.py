"""Marker worker: converts PDFs to Markdown on the GPU.

Runs in Marker's own environment (.venv-convert, Python 3.13) and imports nothing
from Citadel, so it is started as a script:

    .venv-convert/bin/python convert_worker.py [--device cuda|cpu]

Protocol: one JSON object per line.
  stdin:  {"id": "...", "pdf": "/path/in.pdf", "out": "/path/outdir", "ocr": false}
  stdout: {"event": "ready", "engine": "marker-2.0.0"}   models loaded, send jobs
          {"event": "start", "id": ...}
          {"event": "done", "id": ..., "markdown": "/outdir/fulltext.md",
           "images": ["fig.png", ...], "seconds": 12.3}
          {"event": "error", "id": ..., "error": "..."}
The models are loaded once, so a whole queue pays the start-up cost once.
"ocr": true reads every page with the OCR model instead of the PDF's text layer
(slower; for PDFs whose text layer garbles maths).
Marker's own logging goes to stderr.

Marker 2's OCR/layout model (surya-ocr-2) is served by llama.cpp's llama-server
(Arch packages llama-cpp + ggml-cuda), which this worker starts with the models
and always stops on the way out, so the GPU memory is held only while converting.
"""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path


def emit(**event) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    os.environ.setdefault("TORCH_DEVICE", args.device)

    # Imported here so that `--help` and protocol errors do not load torch.
    from importlib.metadata import version

    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered
    from surya.inference import SuryaInferenceManager

    manager = SuryaInferenceManager(method="llamacpp")
    try:
        models = create_model_dict(device=args.device, inference_manager=manager)
        converters = {
            ocr: PdfConverter(
                artifact_dict=models,
                config={"output_format": "markdown", "use_llm": False, "force_ocr": ocr},
            )
            for ocr in (False, True)
        }
        emit(event="ready", engine=f"marker-{version('marker-pdf')}")
        serve(converters, text_from_rendered)
    finally:
        manager.stop()


def serve(converters, text_from_rendered) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            job = json.loads(line)
        except ValueError:
            emit(event="error", id=None, error="not JSON")
            continue
        emit(event="start", id=job.get("id"))
        started = time.monotonic()
        try:
            out = Path(job["out"])
            out.mkdir(parents=True, exist_ok=True)
            rendered = converters[bool(job.get("ocr"))](job["pdf"])
            text, _, images = text_from_rendered(rendered)
            names = []
            for name, image in images.items():
                image.save(out / name)
                names.append(name)
            markdown = out / "fulltext.md"
            markdown.write_text(text, encoding="utf-8")
            emit(
                event="done",
                id=job.get("id"),
                markdown=str(markdown),
                images=names,
                seconds=round(time.monotonic() - started, 2),
            )
        except Exception as error:  # one broken PDF must not end the run
            traceback.print_exc(file=sys.stderr)
            emit(event="error", id=job.get("id"), error=f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
