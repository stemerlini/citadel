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

## Daily use

```sh
citadel ingest              # file what is in <library>/_inbox (or send it to review)
citadel review              # what is waiting, with Citadel's proposal
citadel review accept NAME [--topic T] [--doi D]
citadel add arxiv:2101.12345 | doi:10.… | paper.pdf
citadel search "kelvin helmholtz" ; citadel get CITEKEY
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
