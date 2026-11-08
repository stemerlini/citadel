# Maintaining Citadel

Citadel is maintained by its owner from several Omarchy machines, each with its own
agents. GitHub (`stemerlini/citadel`, private) is where the machines meet: every change
reaches `main` through a pull request, so any agent on any machine can pick up from it.

- [README.md](README.md): what Citadel does, for the user.
- [PLAN.md](PLAN.md): design, decisions taken, phases and status. Read the
  *Decisions taken* table and the *Status* line before changing behaviour.

## Setting up a machine

```sh
gh repo clone stemerlini/citadel ~/Projects/citadel && cd ~/Projects/citadel
tools/install-omarchy             # main desktop: runs the inbox watcher
tools/install-omarchy --no-watch  # every other machine
.venv/bin/pip install -q -e '.[dev]'   # pytest, ruff
```

The library itself arrives through the OneDrive mount; each machine builds its own
index (`citadel reindex`) and keeps its own sign-in (`citadel login`).

## The change loop

1. **Sync.** `git switch main && git pull --ff-only`, then a branch: `fix/<topic>` or
   `feat/<topic>`. Done when the tree is clean on the new branch.
2. **Red.** Reproduce the problem as a failing test in `tests/` (offline, temporary
   folders: see `tests/conftest.py`). Behaviour a test cannot reach (the TUI by hand, the
   desktop widget) is tried on a scratch library with **both** variables set:
   `CITADEL_LIBRARY=$(mktemp -d)/Citadel CITADEL_HOME=$(mktemp -d) citadel …`.
   Done when the test fails for the reason the user saw.
3. **Green.** Fix it. Done when all three pass:
   `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`,
   `.venv/bin/ruff format --check src tests`.
4. **Record.** User-visible behaviour goes in README.md; a design decision or a phase
   step goes in PLAN.md, dated; a rule for maintainers goes here.
5. **Pull request.** Push the branch and `gh pr create`: what broke (or what the user
   asked for), why, how it was verified, and which machine it was found on. The owner
   reviews and merges; merge only when asked.
6. **Roll out.** After a merge, on each machine: `git switch main && git pull` and
   `tools/install-omarchy`. Re-running the installer is how new code reaches the
   watcher, the desktop widget, the skill and the MCP server.

## Guardrails: the library is the user's data

- The library on OneDrive (`~/Cloud/Personal-OneDrive/Citadel`) is real. Change it only
  through Citadel's own code paths, and never from tests or trials (step 2).
- The old `Library/` folder is migrated by copying; it is never moved or edited.
- Deletions go to the OneDrive recycle bin, as `duplicates.py` does; keep it that way.
- The SQLite index stays off OneDrive: SQLite is not safe on the rclone FUSE mount.
- Credentials stay out of output, logs and commits: `~/.config/rclone/rclone.conf`,
  the Overleaf and GitHub tokens, the browser profiles in `data/browser/`.
- Free, open services only (Crossref, OpenAlex, arXiv, Unpaywall, OpenLibrary): no paid
  APIs, including LLM APIs for classification.
- Publisher sites: Citadel opens pages for the user to click (`access.py`). It never
  drives a publisher's site or gets around its bot checks.
- The topic tree (`src/citadel/topics.toml`) changes only with the owner's approval.
- One inbox watcher across all machines (`--no-watch` everywhere else).

## Gotchas

- Two Python environments: the app in `.venv` (system Python 3.14); Marker in
  `.venv-convert` (Python 3.13 via mise), because it pins `pillow<11`.
- Omarchy rejects symlinked shell plugins, so the installer copies
  `omarchy/plugin/smerlini.citadel/`: re-run it after editing the widget.
- Text inputs in the TUI use `tui/fields.py` (`Field`, `Notes`), where Ctrl+A selects
  all; Textual's own `Input` binds it to Home.
- The MCP server uses the mcp SDK 2.x API (`MCPServer`, formerly `FastMCP`).
- `data/` (config, index, browser profiles, the `no-watch` marker) is per machine and
  never committed.
