# Citadel

A reference library for scientists, in the terminal. Drop papers in an inbox and
Citadel renames, catalogues and converts them, cites them, and serves them to
agents. See [PLAN.md](PLAN.md) for the design and the phases.

## Install (Omarchy)

```sh
tools/install-omarchy     # safe to re-run after pulling changes
```

This sets up the Python environment, the `citadel` command (`~/.local/bin`), an
apps-menu entry with icon, the inbox watcher (systemd user service
`citadel-watch`) and the desktop conversion widget (shell plugin
`smerlini.citadel`). `tools/uninstall-omarchy` removes all of it; the library is
never touched.

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
(installed by `tools/install-omarchy`).

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
| `i` | review queue (`a` accept, `t` other topic, `d` DOI, `x` reject, `v` mark, `R` retry) | `D` | duplicates: keep one copy |
| `?` | about | | |

The **MD** column: `✓` Markdown ready · `⟳` converting now · `·` not converted yet · `—` no PDF.

Office documents (`.docx`, `.odt`, `.pptx`, …) are converted to PDF with LibreOffice and
filed with the original kept next to the PDF; annexes and supplements of a filed
reference (e.g. `ECSS-…-Annex-A.docx`) are attached to it under `attachments/`.

## Daily use

```sh
citadel ingest              # file what is in <library>/_inbox (or send it to review)
citadel review              # what is waiting, with Citadel's proposal
citadel review accept NAME [--topic T] [--doi D]
citadel add arxiv:2101.12345 | doi:10.… | paper.pdf
citadel convert [CITEKEY…] [--all] [--force] [--ocr]
citadel search "kelvin helmholtz" ; citadel get CITEKEY
citadel cite CITEKEY… [--format reference|bibtex|key]
citadel duplicates [keep|ignore CITEKEY-or-file]   # deleted copies go to OneDrive's recycle bin
```

Every command takes `--json`.

## Agents

`tools/install-omarchy` registers Citadel with Claude Code (user scope) and links the
`citadel` skill into `~/.claude/skills/`, so any agent session can use your library:

| MCP tool | Does |
|---|---|
| `search` | full-text search (optionally within a topic) |
| `get` | metadata, file paths, and an outline of the Markdown full text |
| `read` | the full text by section (`"2.1"`, `"Results"`) or in chunks (`next_start`) |
| `cite` | formatted references, BibTeX or citekeys |
| `list_references`, `topics` | browse the library |
| `add` | add a PDF, DOI or arXiv id (recorded as `agent:NAME`); the only tool that writes |

Other agents: run `citadel mcp` as a stdio MCP server. `library.bib` in the library root
holds every reference as BibTeX (kept current by the watcher; `citadel bib` on demand).

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
