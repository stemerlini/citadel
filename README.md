# Citadel

A reference library for scientists, in the terminal. Drop papers in an inbox and
Citadel renames, catalogues and converts them, cites them, and serves them to
agents. See [PLAN.md](PLAN.md) for the design and the phases.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/citadel init      # writes data/config.toml and creates the library folders
.venv/bin/citadel doctor    # checks the setup
```

The library defaults to `~/Cloud/Personal-OneDrive/Citadel`; change `library` in
`data/config.toml` (or set `CITADEL_LIBRARY`). The topic tree lives in the
library's `topics.toml`.

## PDF → Markdown (Marker, on the GPU)

Marker runs in its own environment, and its OCR model is served by llama.cpp:

```sh
omarchy pkg add llama-cpp ggml-cuda                  # official Arch packages
mise install python@3.13
$(mise where python@3.13)/bin/python3 -m venv .venv-convert
.venv-convert/bin/pip install marker-pdf
citadel convert --all                                # models download on the first run
```

While converting, a card on the desktop shows the progress and the GPU at work
(an Omarchy shell plugin):

```sh
cp -r omarchy/plugin/smerlini.citadel ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins && omarchy plugin enable smerlini.citadel
```

## The app

```sh
citadel          # opens the TUI
```

| Key | Action | Key | Action |
|---|---|---|---|
| `Space` | preview: PDF page + details (`←/→` pages, `Tab` full text) | `Enter` | open the PDF |
| `/` | search titles, authors, abstracts, tags and full text | `Esc` | clear the search |
| `c` / `b` / `k` | copy the reference / BibTeX / citekey | `r` | unread → reading → read |
| `e` | edit metadata (`Ctrl+S` saves) | `t` | move to another topic |
| `m` | convert to Markdown (GPU) | `a` | add a PDF, `doi:…` or `arxiv:…` |
| `i` | review queue (`a` accept, `t` other topic, `d` DOI, `x` reject) | `?` | about |

## Daily use

```sh
citadel ingest              # file what is in <library>/_inbox (or send it to review)
citadel review              # what is waiting, with Citadel's proposal
citadel review accept NAME [--topic T] [--doi D]
citadel add arxiv:2101.12345 | doi:10.… | paper.pdf
citadel convert [CITEKEY…] [--all] [--force] [--ocr]
citadel search "kelvin helmholtz" ; citadel get CITEKEY
citadel cite CITEKEY… [--format reference|bibtex|key]
```

To file the inbox automatically, install the watcher (a systemd user service):

```sh
cp omarchy/citadel-watch.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now citadel-watch.service
```

Every command takes `--json`.

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
