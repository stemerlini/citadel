# Citadel — plan

> The maesters' library for an Omarchy scientist: drop a paper in, get it renamed,
> catalogued, cited, converted to Markdown, and readable by you, Obsidian and agents.

Status: Phase 1 done (2026-10-07): naming + citekeys, frontmatter records, SQLite/FTS5 index,
`reindex`/`list`/`search`/`get`; 73 tests. Library at `~/Cloud/Personal-OneDrive/Citadel`.

## Goals

1. **Ingest.** PDFs from manual downloads or AI research runs land in `_inbox/`.
   Citadel finds their metadata, renames them to one convention, files them by topic
   and converts them to Markdown.
2. **Browse.** A Textual TUI in the style of Toploader: a list of references,
   a category tree, search, and a **Space** preview of the PDF's first page or the Markdown.
3. **Cite.** BibTeX, BibLaTeX, CSL-JSON or a formatted reference string for any entry
   (and for a whole category or collection).
4. **Agent-native.** Every feature is available through a JSON CLI and an MCP server,
   so Claude Code and other agents can search, read, cite and add papers.
5. **Obsidian-friendly, not Obsidian-owned.** The library lives *outside* the vault.
   The vault collects artefacts (notes, reports, agent output) and links *to* Citadel
   references without containing them.
6. **No paid services.** Everything runs locally or uses free, open scholarly APIs.

## Decisions taken

| Topic | Decision |
|---|---|
| Name / command | **Citadel** / `citadel` |
| Library location | New folder `~/Cloud/Personal-OneDrive/Citadel/`; migrate the old `Library/` gradually |
| On-disk layout | One folder per reference |
| PDF → Markdown | [Marker](https://github.com/datalab-to/marker) on the local GPU (RTX 4060 Ti, 8 GB); Tesseract OCR for scans |
| Decisions (classify / verify) | Local only: rules + local embedding classifier (no Jev, no paid APIs) |
| Obsidian | Library outside the vault; the vault links to it (see *Obsidian integration*) |
| Zotero | Out of scope for now |
| Stack | Python + Textual + textual-image (same as Toploader), SQLite index, pytest, ruff |
| Environments | App: system Python 3.14 in `.venv`. Marker: its own Python 3.13 env (`.venv-convert`, via mise) run as a worker subprocess, because Marker pins `pillow<11` (no 3.14 wheel) and drags in PyTorch |

## Library layout

```
Citadel/                                  ← NOT an Obsidian vault
├── _inbox/                               ← drop anything here
├── _review/                              ← low-confidence items wait here for you
├── _attachments/                         ← non-reference files (.bib exports, etc.)
├── plasma-physics/
│   └── turbulence-transport/
│       └── 2004_StatisticalTheoryMHDTurbulence_Verma/
│           ├── 2004_StatisticalTheoryMHDTurbulence_Verma.pdf
│           ├── 2004_StatisticalTheoryMHDTurbulence_Verma.md   ← frontmatter + full text
│           └── figures/
└── …
```

### Naming convention (all file types)

`{year}_{ShortTitle}_{FirstAuthorSurname}`. This is the convention the old library
already uses, made strict:

- `year`: publication year, or `nd` if unknown.
- `ShortTitle`: the main title (subtitle after `:` dropped) in PascalCase, at most 6
  significant words and 50 characters, stop-words dropped, ASCII only
  (`Kelvin–Helmholtz` → `KelvinHelmholtz`, `high-power` → `HighPower`, acronyms kept).
- `FirstAuthorSurname`: ASCII-folded (`Schäfer` → `Schafer`); an organisation for
  standards (`ECSS`, `NRL`).
- Collisions get a suffix: `_Verma-b`.
- The **citekey** is separate and BibTeX-style: `verma2004statistical`. It is stable
  and never changes after it is assigned.

### Topic tree (v2, approved)

Folders are **topics only**: discipline → field → subfield, **at most three levels**,
lowercase kebab-case. A paper may be filed at any level (a broad plasma review sits
directly in `plasma-physics/`). Everything that is not a topic becomes metadata:

| Old folder | Becomes |
|---|---|
| Books, Posters, Slides | `type:` field (book, poster, slides, article, thesis, standard, report, chapter, preprint) |
| MAGPIE, HEDS course | `projects:` / `collections:` tags |
| Drafts, Peer Reviewed | Not references. Left out of Citadel (they stay in your own documents) |

```
plasma-physics/
├── fundamentals/            kinetic theory, transport coefficients, formularies (Braginskii, NRL)
├── mhd/                     equilibria, reconnection, waves
├── turbulence-transport/    MHD & plasma turbulence, anomalous transport, Lévy walks
├── instabilities/           Kelvin–Helmholtz, Rayleigh–Taylor, MRT, kink/sausage
├── hedp/
│   ├── pulsed-power/        z-pinches, wire arrays, MAGPIE
│   ├── laser-plasma/        laser–plasma interaction, laser-driven experiments
│   ├── icf/
│   └── laboratory-astrophysics/
├── diagnostics/
│   ├── refractive/          schlieren, shadowgraphy, interferometry, deflectometry
│   ├── scattering/          Thomson, collective scattering
│   ├── spectroscopy/
│   ├── imaging/             fast cameras, X-ray imaging, tomography
│   ├── particle-probes/     Langmuir, magnetic, neutron
│   └── synthetic/           synthetic diagnostics, forward models
├── computational/           PIC, MHD codes, numerical methods for plasmas
├── fusion/
│   ├── magnetic-confinement/  tokamaks, stellarators
│   └── alternative-concepts/  FRC, PFRC, z-pinch fusion, MIF
└── accelerators/            plasma wakefield, laser-driven
optics/
├── wave-optics/             propagation in inhomogeneous media
├── aero-optics/
└── imaging-systems/
fluid-dynamics/
├── turbulence/              (neutral-fluid turbulence, spectra)
└── hypersonics/
space-engineering/
├── propulsion/              electric & plasma thrusters
├── space-weather/
├── power/                   radioisotope power, laser power transmission
└── radiation-effects/       radiation testing, ECSS standards
data-science/
├── machine-learning/
└── statistics-methods/      fitting, power laws, inference
```

The topic list lives in `topics.toml` (name, description, keywords). The description
and keywords feed the local classifier, and adding a folder there is all it takes to
create a new topic.

The TUI also shows virtual views (by type, project, year, author, unread, recently
added), so a paper lives in one folder but can be found many ways.

## Reference record (the `.md` frontmatter)

```yaml
---
citekey: verma2004statistical
type: article
title: "Statistical theory of magnetohydrodynamic turbulence: recent results"
authors: ["Verma, Mahendra K."]
year: 2004
journal: Physics Reports
volume: 401
pages: "229-380"
doi: 10.1016/j.physrep.2004.07.007
arxiv: nlin/0404043
url:
abstract: "…"
topic: plasma-physics/turbulence
tags: [mhd, turbulence, spectra]
projects: []
collections: []
status: unread            # unread | reading | read
rating:
added: 2026-10-07
source: inbox             # inbox | migration | agent:<name> | doi
metadata_confidence: 0.97
md_engine: marker-1.x
pdf_sha256: …
---
# Statistical theory of magnetohydrodynamic turbulence: recent results
> [!abstract] …
<!-- citadel:fulltext-start -->
…Marker output, with LaTeX equations ($…$, $$…$$), tables and figure embeds…
<!-- citadel:fulltext-end -->
## Notes
(your notes go here; Citadel never touches anything outside the full-text markers)
```

- **The frontmatter is the source of truth.** It syncs through OneDrive.
- **The SQLite index is a rebuildable cache**, kept outside OneDrive in `~/Projects/citadel/data/`.
  SQLite on an rclone FUSE mount is unsafe. `citadel reindex` rebuilds it from the vault.
  It holds the FTS5 full-text index and, later, embeddings.

## Ingest pipeline

```
_inbox/file ─▶ 1 identify ─▶ 2 metadata ─▶ 3 decide ─▶ 4 file ─▶ 5 convert ─▶ 6 index
```

1. **Identify.** Hash the file (skip it if already in the library). Extract the text layer
   with pdftotext and the PDF metadata, then look for DOI, arXiv id and ISBN.
2. **Metadata.** Ask the sources in order: DOI → Crossref; arXiv → arXiv API;
   ISBN → OpenLibrary; otherwise search the title on OpenAlex / Semantic Scholar.
   All of these are free. Results are cached and requests are polite (User-Agent with
   contact, as in Toploader).
3. **Decide** (through the pluggable `Decider`):
   - Does this metadata match this PDF? (yes/no + p)
   - Document type? (enum)
   - Topic folder? (enum = the current topic tree)
   - Anything below a threshold (default 0.8) → `_review/`, never filed silently.
4. **File.** Create the reference folder, move the PDF in (copy and verify before deleting
   the inbox file) and write the frontmatter.
5. **Convert.** Run Marker on the GPU → Markdown with LaTeX equations and `figures/`
   (OCR with Tesseract when the PDF has no text layer).
   Then post-process: fix heading levels, drop repeated headers and footers, merge
   hyphenated words, turn references into a list, use relative image links `![](figures/…)`
   (portable CommonMark that renders in Obsidian, the TUI and on GitHub).
   Conversion runs in a background queue, so filing is never blocked by it.
6. **Index.** Update SQLite and FTS5.

**Watcher.** A systemd user service (`citadel watch`) that **polls** `_inbox/`.
inotify does not see changes that arrive through rclone from other devices.

**Other ways in:** `citadel add <pdf|doi|arxiv-id|url>`. Agents use the same call to add
papers they find.

### Classifier (local, free)

The interface is small (`decide(record, question) → answer + confidence`), so the
backend can be swapped later, but the only one planned is local:

1. **Rules:** document type from Crossref/arXiv type, page count, ISBN, and keywords
   ("slides", "ECSS-", "thesis").
2. **Embedding similarity:** a small local sentence-embedding model (CPU/GPU) compares
   the title + abstract with each topic's description/keywords and with the centroid
   of the papers already filed there. The more you file, the better it gets.
3. **Metadata match check:** fuzzy comparison of the found title/authors with the PDF's
   first-page text.

Paid decision services (TypeSafe Jev, the Claude API) are left out on purpose.

## TUI (Textual)

- **Left:** topic tree + virtual views. **Centre:** reference table (year, short title,
  first author, type, status). **Right / bottom:** details (abstract, tags, citation).
- **Space:** preview (first page of the PDF through textual-image / Sixel in foot, or the
  rendered Markdown, toggled with Tab).
- **Keys:** `/` search (full text), `o` open PDF (in the default viewer),
  `l` copy an Obsidian link to the reference, `n` create/open its literature note in the vault, `c` copy citation (cycle the format),
  `b` copy BibTeX, `e` edit metadata, `t` tags, `r` cycle read status, `i` inbox / review
  queue (approve or fix each decision), `?` help.
- A theme that follows Omarchy (as in Toploader), an About screen with a logo, a bar
  widget showing the inbox count, an apps-menu entry.

## Obsidian integration (library stays outside the vault)

**The vault is not set up yet.** This section is the target design: the vault path stays
unset in `config.toml` until then, and `link`/`note` print a helpful message instead of failing.

The vault is where you and agents put notes, reports and
other artefacts. Citadel never writes into it unless asked, and it never copies PDFs there.

- **Links into Citadel:** `citadel link <citekey>` (and `l` in the TUI) gives a Markdown
  link that Obsidian opens outside the vault, e.g.
  `[Verma 2004](file:///home/smerlini/Cloud/Personal-OneDrive/Citadel/plasma-physics/turbulence-transport/2004_…/2004_….pdf)`.
  Variants give the `.md` full text or the PDF at a page (`#page=12`).
- **Literature notes on demand:** `citadel note <citekey>` (and `n`) creates a small note
  in a vault folder of your choice (default `Literature/@verma2004statistical.md`) with
  the frontmatter (title, authors, year, DOI, citekey, links to the PDF and the full text)
  and the abstract. You or an agent then add your own thinking there. It is created only
  when you reference the paper, so the vault holds what you have worked with, not the
  whole library.
- **Citekeys everywhere:** agents writing reports into the vault cite as `[@verma2004statistical]`
  (Pandoc style). `citadel cite --resolve report.md` turns those into a formatted
  reference list, and `library.bib` makes the same keys work with Pandoc and LaTeX.
- **Optional, later:** a read-only Obsidian Base view or a small community-plugin
  bridge, if you find the links above are not enough.

## Agent interface

- **CLI**, with `--json` on every command:
  `search`, `get <citekey>`, `read <citekey> [--section]`, `cite <keys…> --format bibtex|csl|apa|…`,
  `add`, `list --topic/--tag/--type`, `tag`, `move`, `export <collection> --bib`.
- **MCP server** (`citadel mcp`, stdio): the same operations as tools, plus resources
  `citadel://ref/<citekey>` and `citadel://ref/<citekey>/fulltext`.
- **Claude Code skill** (`~/.claude/skills/citadel`): when and how agents should use it,
  e.g. "put every downloaded paper through `citadel add`, cite with citekeys".
- **Exports:** a live `library.bib` (Better-BibTeX style) for LaTeX and Overleaf
  workflows, and a CSL-JSON export.

## Migration from the old Library

`citadel import ~/Cloud/Personal-OneDrive/Library/<folder> --dry-run` shows a table:
old path → new path, topic, metadata found, confidence. Then `--apply` **copies**
(never moves) into Citadel. The old library stays untouched until you delete it
yourself. Run it one folder at a time (gradual). Old topic folders map to new topics
through an editable mapping file, and `.bib`/`.docx`/`.png`/`.mp4` files go to
`_attachments/` or are skipped.

## Findings so far

- **PDF Title metadata is unreliable.** In the old library it is often a bibcode
  (`1965plph.conf..287A`), a PostScript path or `doi:10.…`. Phase 2 must never use it
  as the title on its own: treat it as a hint, and mine it for a DOI.
- **Cold rclone listings are slow.** The first walk of freshly created folders took 18 s;
  warm walks take about 0.1 s (`--dir-cache-time 72h`). Commands refresh the index
  incrementally before answering. If that gets slow, the watcher service (Phase 3)
  can keep the index fresh instead.

## Phases

| # | Deliverable | Done when |
|---|---|---|
| 0 | Repo, pyproject, venv (Python 3.12 if Marker/torch lack 3.14 wheels), config, paths, tests skeleton | `pytest` green |
| 1 | Core model: naming + citekey rules, frontmatter read/write, SQLite index + FTS5, `reindex` | Round-trip tests on sample records |
| 2 | Metadata: DOI/arXiv/ISBN extraction, Crossref/arXiv/OpenAlex/OpenLibrary clients (cached, mocked in tests) | 20 real PDFs from the old library identified correctly |
| 3 | Ingest: `add`, inbox processing, local decider, `_review/`, polling watcher service | Drop a PDF → filed reference folder |
| 4 | Conversion: Marker queue + Markdown post-processor + figures + OCR | Equation-heavy papers (Braginskii, Verma) render well in Obsidian and the TUI |
| 5 | TUI: list, tree, details, Space preview, search, cite/copy, review queue | Daily use instead of a file manager |
| 6 | Agents: `--json` CLI, MCP server, Claude Code skill, `library.bib` | Claude Code can find, read and cite a paper end to end |
| 7 | Obsidian: `link`, `note`, `cite --resolve`, vault path config | A vault report cites a paper and opens its PDF |
| 8 | Migration tool + migrate the old library folder by folder | Old library retired |
| 9 | Semantic search (local embeddings), Omarchy integration (menu, bar widget), release | — |

## Open questions

- Vault path and literature-note folder: decide when the Obsidian vault is set up.

## Out of scope (for now)

- Zotero import (Imperial Zotero library).
- Paid services: TypeSafe Jev, Claude API, paid search.
