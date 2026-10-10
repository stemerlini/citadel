"""Build a Textual theme from the active Omarchy theme, if there is one (as in Toploader)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from textual.theme import Theme

COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"


def omarchy_theme() -> Theme | None:
    try:
        c = tomllib.loads(COLORS.read_text())
    except (OSError, ValueError):
        return None
    try:
        return Theme(
            name="omarchy",
            dark=c.get("mode", "dark") != "light",
            primary=c.get("accent") or c["blue"],
            secondary=c.get("magenta"),
            accent=c.get("cyan"),
            warning=c.get("yellow"),
            error=c.get("red"),
            success=c.get("green"),
            foreground=c["foreground"],
            background=c["background"],
            surface=c.get("lighter_background") or c["background"],
            panel=c.get("dark_background") or c["background"],
        )
    except KeyError:
        return None
