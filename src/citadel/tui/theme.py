"""The app's colours: the active Omarchy theme when there is one (as in Toploader),
else Citadel's own, in the colours of Omarchy's default theme (Tokyo Night)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from textual.theme import Theme

COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"


def omarchy_theme() -> Theme | None:
    try:
        colors = tomllib.loads(COLORS.read_text())
    except (OSError, ValueError):
        return None
    return _theme("omarchy", colors)


def _theme(name: str, c: dict) -> Theme | None:
    try:
        return Theme(
            name=name,
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


# Omarchy's default theme, Tokyo Night, for machines without Omarchy (Ubuntu…).
DEFAULT_COLORS = {
    "mode": "dark",
    "accent": "#7aa2f7",
    "magenta": "#ad8ee6",
    "cyan": "#449dab",
    "yellow": "#e0af68",
    "red": "#f7768e",
    "green": "#9ece6a",
    "foreground": "#a9b1d6",
    "background": "#1a1b26",
    "lighter_background": "#24283b",
    "dark_background": "#13141c",
}


def app_theme() -> Theme:
    return omarchy_theme() or _theme("citadel", DEFAULT_COLORS)
