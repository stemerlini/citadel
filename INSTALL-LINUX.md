# Installing Citadel on Ubuntu (or another Linux without Omarchy)

Written for the agent doing the install (Copilot CLI, Claude Code…): follow the steps
in order, check each "Done when", and ask the user where a step says so. On Omarchy use
`tools/install-omarchy` instead (see README.md).

What works the same as on Omarchy: the TUI, search, cite, the inbox and review queue,
claims on an rclone mount of OneDrive, the library sign-in, the MCP server and the
agent skill. What is Omarchy-only and skipped here: the desktop conversion widget, the
Omarchy theme colours (the app uses its own theme), and `omarchy-launch-or-focus-tui`.

Checked on 2026-11-11: the test suite passes on Python 3.12 (Ubuntu 24.04's version), and
`uv tool install`, `citadel init`, `reindex` and `doctor` work in an empty home folder.

## Rules while installing

- The user's library is their data: never move, delete or edit files in it by hand.
- Each person has their own library and their own OneDrive. Never point Citadel at
  another person's library or copy files out of one.
- Keep credentials out of output: `~/.config/rclone/rclone.conf`, the GitHub token, and
  `~/.local/share/citadel/data/browser/`.

## 1. Prerequisites

```sh
sudo apt update
sudo apt install -y git curl wl-clipboard   # wl-clipboard: copying citations on Wayland
curl -LsSf https://astral.sh/uv/install.sh | sh    # puts uv in ~/.local/bin
```

Python: uv fetches Python 3.12 or newer by itself when the system's is older
(Ubuntu 22.04 has 3.10), so there is no need for a system Python upgrade.

Optional, depending on what the user wants:

| Feature | Package |
|---|---|
| Slides and Word documents in the inbox | `libreoffice` |
| OCR of scanned PDFs | `tesseract-ocr` |
| Library sign-in for paywalled papers | Google Chrome from google.com (`.deb`); see step 5 |
| Repository access | `gh` (GitHub CLI) |

Done when `uv --version` and `git --version` both print a version.

## 2. Get the code

The repository `stemerlini/citadel` is **private**. The user needs read access from
the owner (Stefano Merlini). Ask the user whether they have it. Then:

```sh
sudo apt install -y gh && gh auth login && gh auth setup-git
gh repo clone stemerlini/citadel ~/Projects/citadel
uv tool install --python 3.12 ~/Projects/citadel
```

Done when `citadel --help` runs. If `citadel` is not found, add `~/.local/bin` to PATH
(`uv tool update-shell`, then open a new terminal).

Updating later: `cd ~/Projects/citadel && git pull && uv tool install --reinstall --python 3.12 .`

## 3. The library folder

Ask the user where the library should live. Citadel needs one plain folder. To sync
it between machines, put it on OneDrive through an **rclone mount**: then several
machines can each run the inbox watcher safely (see README, "Install (another machine)").

- **Personal OneDrive:** install rclone from rclone.org, because Ubuntu's package is
  too old: `sudo -v && curl https://rclone.org/install.sh | sudo bash`. Then let the
  user run `rclone config` themselves to create a `onedrive` remote: it opens a browser
  sign-in. Mount it as a systemd user service:

  ```ini
  # ~/.config/systemd/user/rclone-onedrive.service
  [Unit]
  Description=rclone mount of OneDrive
  After=network-online.target

  [Service]
  Type=notify
  ExecStartPre=/usr/bin/mkdir -p %h/OneDrive
  ExecStart=/usr/bin/rclone mount onedrive: %h/OneDrive --vfs-cache-mode full --dir-cache-time 1h --poll-interval 1m --rc --rc-addr unix://%t/rclone-onedrive.sock --rc-no-auth
  ExecStop=/bin/fusermount3 -u %h/OneDrive
  Restart=on-failure

  [Install]
  WantedBy=default.target
  ```

  `systemctl --user enable --now rclone-onedrive`. The `--rc` flags let Citadel see what
  is still uploading and claim inbox files on OneDrive. Library: `~/OneDrive/Citadel`.
- **Work (company) OneDrive:** the tenant's IT may block rclone's sign-in ("needs admin
  approval"). Do not work around it. Use a local folder, or ask IT to approve rclone.
- **No OneDrive:** any local folder, e.g. `~/Citadel`.

```sh
citadel init --library ~/OneDrive/Citadel   # creates the folders and config; never overwrites
citadel reindex
citadel doctor
```

Done when `citadel doctor` shows the library as `[ok]`. On an rclone mount it should
not say "claims are local renames". The config is
`~/.local/share/citadel/data/config.toml`.

## 4. Launcher entry (optional)

```sh
mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/256x256/apps
cp ~/Projects/citadel/assets/icon-256.png ~/.local/share/icons/hicolor/256x256/apps/citadel.png
cat > ~/.local/share/applications/citadel.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Citadel
Comment=Reference library: search, preview, cite
Exec=$HOME/.local/bin/citadel
Icon=citadel
Terminal=true
Categories=Office;
EOF
```

PDF page previews need a terminal with image support (Ghostty, kitty, WezTerm, foot).
Other terminals (GNOME Terminal) fall back to coarse block previews.

## 5. Library sign-in for paywalled papers (optional)

The user adds their accounts in the app: 🔑 in the top bar (or `L`), then `a` for each
(a university library, a company, a publisher account) with its sign-in page, and Enter
signs in. Without the app: add `[[access.accounts]]` entries (`name`, `login_url`, and
`proxy` for an EZproxy prefix) to the config and run `citadel login [name]`.
The user signs in themselves. Citadel only opens pages: it never types credentials or
drives publisher sites.

Ubuntu's `chromium` is a snap, and a snap cannot use a profile in the hidden
`~/.local/share` folder. Install Google Chrome (`.deb`) and set
`browser = "google-chrome"` under `[access]`.

## 6. Inbox watcher (optional)

Without a watcher, the user presses `i` in the app (or runs `citadel ingest`) to read
the inbox. To file new inbox PDFs automatically:

```sh
sed "s|^ExecStart=.*|ExecStart=$HOME/.local/bin/citadel watch|" \
  ~/Projects/citadel/omarchy/citadel-watch.service > ~/.config/systemd/user/citadel-watch.service
systemctl --user daemon-reload && systemctl --user enable --now citadel-watch
```

On a library that is not on an rclone mount of OneDrive, run one watcher only.

## 7. Agents (optional)

```sh
mkdir -p ~/.copilot/skills && ln -sfn ~/Projects/citadel/omarchy/skill/citadel ~/.copilot/skills/citadel
copilot mcp add citadel -- ~/.local/bin/citadel mcp        # Copilot CLI
# Claude Code: ~/.claude/skills, and: claude mcp add --scope user citadel -- ~/.local/bin/citadel mcp
```

## 8. PDF → Markdown (optional, advanced)

This needs an NVIDIA GPU, a Python 3.13 environment with `marker-pdf`, and llama.cpp's
`llama-server`, which Ubuntu does not package. Follow README "PDF → Markdown",
installing Python 3.13 with `uv python install 3.13`. Skip it unless the user asks:
everything else works without it.

## Check the install

```sh
citadel doctor
citadel search test      # runs; it is empty for a new library
citadel                  # the TUI opens; q quits
```

Report to the user what was installed, the library path, and which optional parts were
skipped.
