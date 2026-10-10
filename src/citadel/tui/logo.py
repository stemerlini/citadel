"""The Citadel logo: a pixel-art fortress and a pixel wordmark.

Sprites are text grids, one character per pixel ("." is transparent), drawn
with half blocks (two pixels per cell), as in Toploader.
"""

from __future__ import annotations

import os

from rich.style import Style
from rich.text import Text

PALETTE = {
    "S": "#b9bdc9",  # stone
    "s": "#8a8f9e",  # stone in shadow
    "d": "#3b3d4a",  # gate
    "W": "#f6c177",  # lit window
    "P": "#8a8f9e",  # flag pole
    "A": "#e0565b",  # banner
}

SMALL = """
.......PAA.....
.......PA......
.......P.......
S.S..SSSSS..S.S
SSS..SWSWS..SSS
SSSSSSSSSSSSSSs
SSSSSSdddSSSSSs
SSSSSSdddSSSSSs
"""

GLYPHS = {
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
}


def sprite(art: str) -> list[str]:
    rows = art.strip().splitlines()
    assert len({len(r) for r in rows}) == 1, "sprite rows must have equal length"
    return rows


def wordmark(word: str = "CITADEL") -> list[str]:
    """The name in the pixel font: stone, with the banner colour on the top rows."""
    return [
        ".".join(GLYPHS[ch][y] for ch in word).replace("#", "A" if y < 2 else "S") for y in range(7)
    ]


def half_blocks_ok() -> bool:
    return os.environ.get("TERM_PROGRAM") != "Apple_Terminal"


def to_text(rows: list[str]) -> Text:
    """Two stacked pixels per cell with half blocks."""
    if len(rows) % 2:
        rows = rows + ["." * len(rows[0])]
    text = Text(no_wrap=True)
    for y in range(0, len(rows), 2):
        for top, bottom in zip(rows[y], rows[y + 1], strict=True):
            top_c, bottom_c = PALETTE.get(top), PALETTE.get(bottom)
            if not half_blocks_ok():
                color = top_c or bottom_c
                text.append(" ", Style(bgcolor=color) if color else None)
            elif top_c and bottom_c:
                text.append("▀", Style(color=top_c, bgcolor=bottom_c))
            elif top_c:
                text.append("▀", Style(color=top_c))
            elif bottom_c:
                text.append("▄", Style(color=bottom_c))
            else:
                text.append(" ")
        if y + 2 < len(rows):
            text.append("\n")
    return text


def small() -> Text:
    """The top bar's logo: four cells tall."""
    return to_text(sprite(SMALL))


def banner() -> Text:
    """Fortress next to the wordmark, for the About and empty screens."""
    castle = to_text(sprite(SMALL)).split("\n")
    word = to_text(["." * 41] + wordmark()).split("\n")
    out = Text(no_wrap=True)
    for i in range(max(len(castle), len(word))):
        if i < len(castle):
            out.append_text(castle[i])
        out.append("   ")
        if i < len(word):
            out.append_text(word[i])
        if i + 1 < max(len(castle), len(word)):
            out.append("\n")
    return out


def save_png(rows: list[str], path, size: int = 256) -> None:
    """The sprite as a square PNG with crisp pixels (for the apps-menu icon)."""
    from PIL import Image, ImageColor

    side = max(len(rows), len(rows[0])) + 2  # one pixel of margin all round
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    top = (side - len(rows)) // 2
    left = (side - len(rows[0])) // 2
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in PALETTE:
                image.putpixel((left + x, top + y), ImageColor.getrgb(PALETTE[ch]))
    image.resize((size, size), Image.NEAREST).save(path)
