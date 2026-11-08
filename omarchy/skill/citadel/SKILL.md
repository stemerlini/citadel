---
name: citadel
description: Literature from the user's own reference library (Citadel). Use when a task needs papers, standards or books - finding prior work, reading or quoting a paper, citing in a report or LaTeX/Markdown document, building a bibliography - or when you download a paper worth keeping.
---

# Citadel

Citadel is the user's curated reference library: papers, books, standards and slides,
each with checked metadata, a topic, the PDF, and Markdown full text (LaTeX maths).
Reach it through the `citadel` MCP tools; the `citadel` CLI is the fallback (below).

## Literature work

1. **Library first.** `search` Citadel for the subject before any web search; try two or
   three phrasings (the method, the instrument, the author). The step is done when you know
   which of the user's references bear on the task.
2. **Read from the Markdown.** `get` a reference for its metadata and outline, then `read`
   the sections you need (by heading or number), paging with `next_start`. When a reference
   has no Markdown yet, work from its abstract and tell the user `citadel convert` would make
   the full text available.
3. **Go wider only for gaps.** Follow the citation tree first: `references` lists what a
   library paper cites (or, with direction "cited_by", what cites it), marking what the
   user already has and what has an open-access PDF. Then search the web. For each paper
   worth keeping, offer to `add` it (DOI, arXiv id or a downloaded PDF path; a DOI brings
   its open-access PDF when one exists) with `agent` set to your name.
4. **Cite every claim** that rests on literature with its citekey, Pandoc style:
   `[@tylka1997creme96]`, `[@verma2004statistical, p. 12]`. Done when each such claim
   carries a citekey from Citadel, or a web source the user has been offered to add.

## Writing documents

- Markdown/Pandoc and LaTeX documents cite by citekey. Put the entries beside the document:
  `citadel cite KEY… --format bibtex > references.bib` (or the `cite` tool, style "bibtex").
- A reference list in prose comes from the `cite` tool, style "reference".
- Quote equations as they appear in the Markdown full text: they are already LaTeX.

## The library is Citadel's

Change it only through Citadel's tools and CLI (`add`, the user's review queue): Citadel
keeps file names, citekeys, records and its index consistent. Read files directly when that
helps - `get` returns the paths of the PDF, the Markdown and the folder.
Papers Citadel cannot place wait in the user's review queue; `add` reports that. Settling
review is the user's call.

## CLI fallback (no MCP connection)

```sh
citadel search "query words" --json        # best matches, with snippets
citadel get CITEKEY --json                  # metadata and file paths
citadel cite KEY… --format reference|bibtex|key
citadel list --topic TOPIC --json ; citadel topics --json
citadel add <pdf | doi:10.… | arxiv:ID> --agent NAME [--topic TOPIC] --json
citadel refs CITEKEY [--cited-by] --json    # the citation tree; --add N… fetches
```

The full text is the record's `.md` file (path from `get`), between
`<!-- citadel:fulltext-start -->` and `<!-- citadel:fulltext-end -->`.

## When Citadel itself misbehaves

A failing command, wrong metadata Citadel keeps producing, or a crash is a bug in the
user's own app: tell the user what you saw, and fix it through the `citadel-dev` skill
when they want it fixed.
