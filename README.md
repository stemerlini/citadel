<p align="center"><img src="assets/banner.png" alt="Citadel: a reference library for scientists, in the terminal" width="100%"></p>

# Citadel

A reference library for scientists, in the terminal: a keyboard-driven Mendeley for
[Omarchy](https://omarchy.org) whose library is plain files on your OneDrive.

- **Drop it in the inbox, it gets filed.** PDFs (and slides, standards, theses) are
  identified from their DOI, arXiv id or text through Crossref, OpenAlex and arXiv,
  renamed, and filed under a topic folder. Anything uncertain waits in a review queue.
- **Cite without leaving the keyboard.** Stable citekeys, formatted references, BibTeX
  and a `library.bib` for LaTeX and Pandoc.
- **Read it as text.** PDFs become Markdown with equations and figures (Marker, on the
  GPU), so search covers the full text and agents can quote it.
- **Follow the citations.** A paper's references and citing works, with open-access
  PDFs fetched in one keystroke; paywalled ones through your library's sign-in.
- **Built for agents.** An MCP server and an agent skill let Claude Code and Copilot
  search, read and cite your library.
- **Your files stay yours.** No paid services and no lock-in: every reference is a
  folder holding the PDF and a Markdown file with YAML metadata.

See [PLAN.md](PLAN.md) for the design and the phases.

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

## Install (another machine)

The library is shared through OneDrive; each machine keeps its own index. The inbox
watcher runs on one machine only, so two machines don't file the same PDF at once.

On another Omarchy machine, clone the repository and install without the watcher (later
runs of the installer keep it off; `--watch` turns it back on):

```sh
gh repo clone stemerlini/citadel ~/Projects/citadel
~/Projects/citadel/tools/install-omarchy --no-watch
citadel reindex   # builds this machine's index from the library
```

Elsewhere, with [uv](https://docs.astral.sh/uv/) and access to this repository:

```sh
uv tool install git+https://github.com/stemerlini/citadel   # `uv tool upgrade citadel` later
citadel init --library ~/OneDrive/Citadel                  # wherever OneDrive syncs it
citadel reindex   # builds this machine's index from the library
citadel           # the TUI; `citadel mcp` serves the library to agents
```

`init` writes the config to `~/.local/share/citadel/data/config.toml`. Opening PDFs
and pages uses `xdg-open` (Linux).

Fixing or changing Citadel from any machine: see [AGENTS.md](AGENTS.md).

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
| `f` | citation tree: references / cited by, step in (`Enter`) and back (`←`), `a` add | | |
| `w` | open the publisher page (DOI) | `g` | Google search for the paper |
| `L` | sign in to your library (paywalled papers) | `?` | about |

No PDF for a reference (MD column `—`)? Press `w` (or `g`), download it with your
institutional access, and drop it into `_inbox/`: Citadel recognises it and attaches it to
that reference. `citadel attach CITEKEY file.pdf` does the same by hand.

### Paywalled papers: library sign-in

Set `[access]` in the config (Imperial: `name = "imperial"`,
`login_url = "https://library-search.imperial.ac.uk/"`) and run `citadel login` (or `L`)
once to sign in. From then on `w`, `g` and `citadel web` open pages in Citadel's own
Chromium profile, signed in to your library, and every download there lands in
`_inbox/`. On a publisher's page, use "Access through your institution" the first time,
then click the PDF button: the watcher attaches the file to its reference. You do the
clicking and Citadel never sees your password; the session stays in
`data/browser/<name>/` on this machine. For a library that uses EZproxy, also set
`proxy` to its prefix and publisher links go through it.

Slides, posters, books, standards and reports stay filed under their topic and are also
gathered in the sidebar by kind. Slides and posters are recognised from their pages (landscape,
presentation software, few words) and described from the deck itself.

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
citadel refs CITEKEY [--cited-by] [--add N…]      # citation tree (OpenAlex); adds fetch open-access PDFs
citadel web CITEKEY [--google]                     # publisher page / Google search
citadel attach CITEKEY file.pdf                    # give a reference its PDF
citadel duplicates [keep|ignore CITEKEY-or-file]   # deleted copies go to OneDrive's recycle bin
```

Every command takes `--json`.

## Agents

`tools/install-omarchy` registers the Citadel MCP server with Claude Code (user scope) and
GitHub Copilot CLI (`~/.copilot/mcp-config.json`), and links the `citadel` skill into
`~/.claude/skills/` and `~/.copilot/skills/`, so any agent session can use your library:

| MCP tool | Does |
|---|---|
| `search` | full-text search (optionally within a topic) |
| `get` | metadata, file paths, and an outline of the Markdown full text |
| `read` | the full text by section (`"2.1"`, `"Results"`) or in chunks (`next_start`) |
| `cite` | formatted references, BibTeX or citekeys |
| `list_references`, `topics` | browse the library |
| `references` | the citation tree: what a paper cites or what cites it, with library and open-access marks |
| `add` | add a PDF, DOI or arXiv id (recorded as `agent:NAME`; a DOI brings its open-access PDF); the only tool that writes |

Other agents: run `citadel mcp` as a stdio MCP server. `library.bib` in the library root
holds every reference as BibTeX (kept current by the watcher; `citadel bib` on demand).

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
