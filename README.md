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

Every command takes `--json`.

## Development

```sh
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
