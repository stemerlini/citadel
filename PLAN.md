# Citadel — plan

> The maesters' library for an Omarchy scientist: drop a paper in, get it renamed,
> catalogued, cited, converted to Markdown, and readable by you, Obsidian and agents.

Status (2026-11-08): Phases 0-6 done, plus the citation tree, slides and posters, and the
library sign-in for paywalled papers (`access.py`); 278 tests. Maintained from several
Omarchy machines through pull requests on GitHub (`stemerlini/citadel`, private): see
[AGENTS.md](AGENTS.md). Next: migration (Phase 8), then Obsidian when the vault exists.

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
| Paywalled papers | Any number of sign-in accounts (university, company, publisher) sharing one Chromium profile of Citadel's own (`data/browser/citadel`), so a publisher sees every access; an EZproxy prefix for libraries such as BnL. The user clicks the PDF; Citadel never drives publisher sites (2026-11-08; accounts 2026-11-11) |
| Maintenance | GitHub, private; every change through a pull request; any number of inbox watchers, each inbox file claimed atomically on OneDrive, found from the library folder's rclone mount (2026-11-11; was one watcher, 2026-11-08) |
| Libraries | Several (e.g. Personal, SES on the work account), searched as one through one local index with a `library` column; files never cross libraries (2026-11-09) |
| Start-up | The list draws from the index at once; the library is re-read in a worker, references not confirmed yet greyed out and their file actions disabled. Duplicate detection runs in a worker and reads notes only for duplicates. The index uses WAL and commits each re-read record, so readers are never locked out (2026-11-11) |
| Library folders | Named `Citadel-<tag>`, lower case; the tag (and the TUI's one-letter mark) comes from the folder, not the drive; first letters must differ. Old `Citadel` folders are renamed in the app (on OneDrive via Graph, eTag-guarded); a config pointing at the old name follows the new folder (2026-11-11) |
| Setup | In the app: first run asks for a location and a tag (`personal`); Settings (`S`) adds named libraries and the optional sign-in, written to `config.toml` with tomlkit so comments survive. Without Omarchy the theme is Tokyo Night (2026-11-11) |
| Review | Optional `review_all`: inbox files always wait in `_review/` until accepted; `i` in the app reads the inbox first, so with the watcher off nothing is filed unseen (2026-11-11) |
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

**Claims (2026-11-09).** Before a file is ingested it is claimed: moved into
`_inbox/.claimed/<host>/`. When the library folder is on an rclone mount of a OneDrive
remote (read from `/proc/self/mountinfo`), Citadel itself makes the move on OneDrive,
conditional on the item's eTag (Microsoft Graph, with the token rclone keeps), so of
several watchers exactly one wins and the others skip the file (taken; or pending while
it is still uploading, asked of the mount's `--rc` when it has one). Citadel does not
depend on omadrive, which stays a file manager (2026-11-11). Elsewhere it is a local rename, safe on one machine only (a
warning says so). A host finishes its own leftovers on restart; another host's claims older than 24 h
are listed by `citadel doctor`, never taken over.

**Other ways in:** `citadel add <pdf|doi|arxiv-id|url>`. Agents use the same call to add
papers they find.

### Classifier (local, free)

The interface is small (`classify(title, abstract, journal, text) → topic + confidence`),
so the backend can be swapped. Paid decision services (TypeSafe Jev, the Claude API) are
left out on purpose.

1. **Keywords (done, Phase 3):** keywords from `topics.toml`, matched as lightly stemmed
   phrases, weighted by field (title 3, abstract 2, journal 1.5, page text 0.5).
   Discipline-level keywords count half (generic). Topics are scored along their strongest
   path down the tree; the classifier descends only while one subtopic beats the next by
   1.5x, so an unclear paper stays at the parent. Confidence = narrowest margin × evidence.
2. **Learned from the library (planned, after migration):** the old library's 714 hand-sorted
   papers are labelled examples. A nearest-neighbour vote over filed references (TF-IDF
   first, a small local embedding model later) adds "papers like this live in X" evidence,
   which keywords cannot give. Expected to raise most review items over the threshold.
3. **Document type:** from the metadata source (Crossref/arXiv/OpenLibrary); 150+ pages
   with no source means a book.

### Ingest rules (Phase 3)

- File automatically only when metadata ≥ `review_threshold` (0.8) **and** topic ≥
  `topic_threshold` (0.6). Otherwise → `_review/<file>` plus `<file>.citadel.json` holding
  the proposal, confidences and evidence.
- Exact duplicates (SHA-256) and same-DOI files go to review, never silently dropped.
- Copies are verified by hash before the source is removed; a failure leaves the source in
  place and removes only the half-made folder. Files changed in the last `settle_seconds`
  (60), hidden files and browser partial downloads are left alone.
- Abstracts missing from Crossref are fetched from OpenAlex (helps search and topics).
- `citadel review accept <item> [--topic T] [--doi D] [--type X]`, `citadel review reject
  <item>` (exact duplicate → deleted; anything else → `_attachments/`).
- `citadel add <pdf | doi:… | arxiv:…> [--topic T] [--agent NAME]`: arXiv downloads the PDF;
  a DOI alone makes a metadata-only reference (needs a clear topic or `--topic`).
- `citadel watch` + `omarchy/citadel-watch.service` (systemd user unit, after the OneDrive
  mount). If the mount is down, nothing is touched and the index is kept.

### Conversion (Phase 4)

- **Engine:** Marker 2.0 in `.venv-convert` (Python 3.13 via mise). Its OCR/layout model
  (`surya-ocr-2`, GGUF, ~1.4 GB) is served by **llama.cpp** (`llama-cpp` + `ggml-cuda`
  from the Arch repos), started and stopped by the worker, so the GPU (~4.4 GB) is held
  only while converting. Chosen over vLLM-in-Docker (needs the `docker` group, an AUR
  toolkit, a ~10 GB image) and Marker 1.x (pinned, older models).
- **Worker protocol:** `convert_worker.py` loads the models once per run (~5 s) and reads
  JSON-line jobs on stdin; Citadel reads events through one reader thread and a queue.
- **OCR only where needed:** many APS/AIP PDFs have text layers with mis-encoded maths
  fonts (`¼ ð Þ`), which garble inline maths. Those (detected from the text layer) are
  read with `force_ocr`, which yields clean inline LaTeX at ~6x the time. `convert.ocr`
  = auto | always | never.
- **Speed on the RTX 4060 Ti:** ~2.5 s/page (clean text layer, short paper) to ~13 s/page
  (OCR, or long figure-heavy reviews). The old library is 715 PDFs / 40,065 pages
  (72 books over 150 pages), i.e. days of GPU time: migration should convert in batches
  (overnight), papers before books.
- **Clean-up (`clean.py`):** page anchors and in-PDF links removed, citation brackets and
  equation references unescaped, equation numbers turned into `\tag{}`, stray one-symbol
  display maths made inline, figures linked under `figures/`, the duplicated title dropped,
  heading levels rebuilt from section numbers. Maths is never rewritten.
- **Safety:** figures are written to `.figures.new` and swapped in after the record is
  updated; a failed conversion leaves the reference untouched. Notes outside the full-text
  markers are never touched (re-conversion included).
- **Watcher:** after filing, newly filed papers are converted automatically (`convert.auto`).

### Desktop widget (Phase 4)

`omarchy/plugin/smerlini.citadel`: an Omarchy shell `panel` plugin on the layer-shell
**bottom** layer (above the wallpaper, under every window; no input), bottom-right. It
shows while converting and fades out a minute after: run progress, the paper being
converted, GPU load sparkline, VRAM, temperature, power, papers/min, s/page, ETA and how
much of the library is in Markdown. Data: `$XDG_RUNTIME_DIR/citadel/status.json`, written
about once a second by `status.py` (nvidia-smi samples). `omarchy-shell citadel state`
answers converting | done | hidden. Install by copying (Omarchy rejects symlinked plugin
folders), then `omarchy plugin enable smerlini.citadel`.

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

### As built (Phase 6)

- `citadel mcp` (official `mcp` SDK 2.x, `MCPServer`; FastMCP was renamed in 2.0): tools
  search, get, read, cite, list_references, topics (read-only) and add (writes, open-world).
  `read` serves the Markdown by section or in ≤12k-character chunks; `get` gives the outline.
  Server instructions state the conventions for any MCP client.
- Claude Code skill `omarchy/skill/citadel/SKILL.md`, linked into `~/.claude/skills/citadel`:
  library first, read from Markdown, web only for gaps (offer `add`), cite `[@citekey]`.
- `library.bib` in the library root (sorted, rewritten only on change; watcher + `citadel bib`).
  With several libraries each keeps its own `library.bib` (its own references only, so a
  work bibliography never lands in the personal OneDrive); `citadel bib --output FILE`
  writes one combined file.

### Several libraries (2026-11-09)

- Config: `[[libraries]]` entries (`name`, `path`); a plain `library = …` is one library
  named after its OneDrive folder (`OneDrive-Personal` → Personal). A library whose folder
  is missing (account not mounted) is skipped and reported by `doctor`.
- One index with a `library` column. Citekeys stay unique: on a clash the later library's
  reference is indexed as `<key>-<library>`, reported by `doctor`.
- Every view shows where a reference lives: the TUI tag column, `list`/`search`/`get`,
  `--json`, the MCP results, `cite --json`. `--library NAME` filters `list`/`search`.
- Each library has its own `_inbox/`, `_review/`, `topics.toml` and `library.bib`; a file
  is filed only within its own library, duplicate checks stay within it, and
  `duplicates` lists copies within one library only: a paper in two libraries is not a
  duplicate, and nothing is deleted from another library (2026-11-13).
- The TUI review (`i`) reads every library's inbox and review queue; each item shows its
  library and is accepted, re-topicked or rejected there (2026-11-13).
- Verified with a fresh headless Claude Code session: it searched Citadel unprompted, cited
  `tylka1997creme96`, and asked for conversion instead of guessing a section.

### Citation tree (2026-10-18)

- `graph.py`: OpenAlex for a work's references (in the paper's order) and the works citing
  it (most cited first); every work is matched against the library (DOI, then title + year
  + first author). Open-access PDFs from OpenAlex, then Unpaywall (contact email), then
  arXiv; a downloaded "PDF" that is really a web page is refused.
- `Ingester.fetch(work)`: open-access PDF → normal ingest with the known metadata; else a
  metadata-only reference. When the topic is unclear, the topic of the paper it was found
  in is used. `add doi:…` now goes through the same path (fetches the PDF when free).
- `citadel refs KEY [--cited-by] [--add N…]`, the `references` MCP tool, and the TUI
  explorer (`f`): step into a reference's own references, back with ←, Tab for cited-by.

### Slides and posters (2026-10-18)

- `PdfText.layout`: slides = mostly landscape pages (w/h ≥ 1.2) made by presentation
  software (PowerPoint, Keynote, Impress, Beamer…) or with < 160 words per page; poster = a
  single page ≥ 1500 pt. Checked on the whole library: the 4 decks detected, no paper
  mistaken. `.pptx`/`.odp` arrive through LibreOffice as landscape PDFs.
- `talks.describe`: never searched for (a search finds related papers: a deck was once
  proposed as a NIM-A article). Title from the embedded Title or the biggest text on slide 1,
  author from the embedded Author, year from the creation date; 0.9 when slide 1 confirms
  title and author, 0.75 title only.
- TUI sidebar: Slides & talks, Posters, Books, Standards, Reports (shown when present).

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

### As built (Phase 5)

- **Layout:** top bar (castle logo, live conversion progress from `status.json`, library
  stats with inbox/review counts); sidebar tree with views (All, Recently added, Unread,
  Reading, Without Markdown) and the topic tree with counts; reference table (status mark,
  year, title, first author, type, topic, PDF/Markdown icons; columns drop when narrow);
  detail pane (authors, venue, DOI, abstract or the search snippet, citekey, tags).
- **Keys:** Space preview (page image via `pdftoppm`, cached; ←/→ pages; Tab full text),
  Enter open PDF, `/` full-text search, `c`/`b`/`k` copy reference/BibTeX/citekey
  (wl-copy, OSC 52 fallback), `r` status, `e` edit, `t` move topic, `m` convert, `a` add,
  `i` review queue (accept / other topic / DOI / reject / preview), `?` about.
- Background work (add, accept, reject, convert) runs in worker threads with their own
  database connections; the UI reloads when they finish. Accepted or added papers are
  converted automatically when `convert.auto` is on.
- **Citations (`cite.py`, `citadel cite`):** APA-like references and BibTeX (types mapped,
  specials escaped, maths kept in titles, capitals protected, article numbers as pages).
- **Editing (`edit.py`):** metadata, status, tags, and moving a reference to another topic
  (the folder moves; basename and citekey never change).
- Obsidian keys (`l` link, `n` literature note) wait for the vault (Phase 7).

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

### Identification (Phase 2)

How `citadel identify` decides, and why (each rule came from a real failure on the old library):

- **Order:** DOI (PDF metadata, first pages, publisher cover) → arXiv → ISBN (first pages, then
  the copyright page; Crossref, then OpenLibrary) → title search (Crossref bibliographic search
  with the PDF's Author, then OpenAlex).
- **Confidence = how well the candidate matches the page**, not which source found it:
  title 65% (as phrases on the page or similarity to the guessed title), first author's
  surname 25%, year near the top 10%.
- **Title guess:** the biggest text block on the title page, measured by *median word height*
  (maths/superscripts stretch line boxes). Publisher cover pages (IOP, JSTOR, ResearchGate)
  and textless cover art are skipped. Scans are OCR'd (first two pages, Tesseract).
- **Guards against confident mistakes:** a candidate whose title is only part of the page's
  title is scaled down (an earlier, shorter paper by the same authors); the year only counts
  near the top (citations are full of years); when a *different* work scores within 0.05
  (two editions, conference vs journal version), confidence is capped at 0.75 → review.
- Every attempt is logged in `evidence`, which the review queue and agents can show.

- **First real batch (2026-10-12, radiation-testing material):** 32 of 34 files went to
  review. Causes: (1) the library's `topics.toml` was the Phase 0 copy, since `init` never
  replaced it, so the tuned keywords had never applied (now `init` updates an *unedited*
  copy, tracked by `.topics.default.sha256`, and leaves an edited one alone); (2) no topics
  for radiation environment, instruments, product assurance, radiation detection (added
  with the user's approval); (3) standards and handbooks have no DOI (now recognised by
  their identifier, `standards.py`); (4) a batch bug: the index was updated only after a
  whole batch, so identical files in one batch were both filed under the same citekey (now
  each filing is indexed at once). `citadel review retry` (and `R` in the review screen)
  re-runs review items with the current rules; `v` marks several items for one action.

## Phases

| # | Deliverable | Done when |
|---|---|---|
| 0 | Repo, pyproject, venv (Python 3.12 if Marker/torch lack 3.14 wheels), config, paths, tests skeleton | `pytest` green |
| 1 | Core model: naming + citekey rules, frontmatter read/write, SQLite index + FTS5, `reindex` | Round-trip tests on sample records |
| 2 | Metadata: DOI/arXiv/ISBN extraction, Crossref/arXiv/OpenAlex/OpenLibrary clients (cached, mocked in tests) | 20 real PDFs from the old library identified correctly |
| 3 | Ingest: `add`, inbox processing, local decider, `_review/`, polling watcher service | Drop a PDF → filed reference folder |
| 4 | Conversion: Marker worker + clean-up + figures + selective OCR; desktop GPU widget | Equation-heavy papers render well; widget shows live progress |
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
