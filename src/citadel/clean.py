"""Tidying Marker's Markdown so it reads well in Obsidian, the TUI and on GitHub.

Marker's output is faithful but noisy: page anchors, links into pages that do
not exist outside the PDF, equation numbers left outside their equations, short
symbols promoted to display maths, and a heading that repeats the title the
record already shows. Maths itself is never rewritten, only moved or tagged.
"""

from __future__ import annotations

import re
import unicodedata

from .naming import main_title, words

# A text layer written with mis-encoded maths fonts (APS/AIP "MathPi" and the like):
# ¼ for =, ð/Þ for parentheses… or glyphs with no Unicode mapping at all.
MISENCODED = re.compile(r"[ðÞ¼½Š]|\(cid:\d+\)|�")
GARBLED_THRESHOLD = 8  # hits in the first pages before the text layer is distrusted


def text_layer_is_garbled(text: str) -> bool:
    """True when the PDF's own text cannot be trusted for maths: convert with OCR."""
    return len(MISENCODED.findall(text)) >= GARBLED_THRESHOLD


DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
PAGE_ANCHOR = re.compile(r'<span id="page-[^"]*"></span>')
PAGE_LINK = re.compile(r"\[((?:\\.|[^\]\\])*)\]\(#page-[^)]*\)")
NUMBER_INSIDE = re.compile(r"(?:\\quad|\\qquad|\\;|\s)*\(\s*([0-9]+[a-z]?)\s*\)\s*$")
NUMBER_AFTER = re.compile(
    r"\$\$(.+?)\$\$[ \t]*\n?[ \t]*[,.;]?[ \t]*\(([0-9]+[a-z]?)\)[ \t]*[,.;]?", re.DOTALL
)
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def clean(markdown: str, title: str = "", figures_dir: str = "figures") -> str:
    text = markdown.replace("\r\n", "\n")
    text = _join_accents(text)
    text = PAGE_ANCHOR.sub("", text)
    text = PAGE_LINK.sub(lambda m: m.group(1), text)
    text = _unescape_brackets(text)
    text = _equation_numbers(text)
    text = _inline_short_display_math(text)
    text = IMAGE.sub(lambda m: _image(m, figures_dir), text)
    text = _drop_title_heading(text, title)
    text = _heading_levels(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# Spacing accents that LaTeX-made PDFs often store before the letter they belong to:
# "f¨ur" is "für". (The backtick is left alone: in Markdown it is code.)
SPACING_ACCENTS = {
    "¨": "\u0308",
    "´": "\u0301",
    "ˆ": "\u0302",
    "˜": "\u0303",
    "¸": "\u0327",
    "˚": "\u030a",
    "ˇ": "\u030c",
    "˝": "\u030b",
}
LOOSE_ACCENT = re.compile(r"([¨´ˆ˜¸˚ˇ˝]) ?([A-Za-z])")


def _join_accents(text: str) -> str:
    def join(match: re.Match) -> str:
        combined = unicodedata.normalize("NFC", match.group(2) + SPACING_ACCENTS[match.group(1)])
        return combined if len(combined) == 1 else match.group(0)

    return _outside_math(text, lambda s: LOOSE_ACCENT.sub(join, s))


def _outside_math(text: str, change) -> str:
    """Apply change() to the text between maths spans only."""
    parts = re.split(r"(\$\$.+?\$\$|\$[^$\n]+?\$)", text, flags=re.DOTALL)
    return "".join(p if i % 2 else change(p) for i, p in enumerate(parts))


def _unescape_brackets(text: str) -> str:
    """Marker escapes brackets around citations and equation references:
    \\[7,11\\] -> [7,11], Eq. \\(2c\\) -> Eq. (2c). Escaped parentheses are only
    unescaped around short labels, as \\( … \\) can also delimit real maths."""

    def unescape(s: str) -> str:
        s = s.replace("\\[", "[").replace("\\]", "]")
        return re.sub(r"\\\(([\w.,–-]{1,8})\\\)", r"(\1)", s)

    return _outside_math(text, unescape)


def _equation_numbers(text: str) -> str:
    """Equation numbers become \\tag{…}, whether inside the block or just after it."""

    def after(match: re.Match) -> str:
        body = match.group(1).rstrip()
        if "\\tag{" in body:
            return match.group(0)
        return f"$${body} \\tag{{{match.group(2)}}}$$"

    text = NUMBER_AFTER.sub(after, text)

    def inside(match: re.Match) -> str:
        body = match.group(1)
        if "\\tag{" in body:
            return match.group(0)
        tagged = NUMBER_INSIDE.sub(lambda n: f" \\tag{{{n.group(1)}}}", body.rstrip())
        return f"$${tagged}$$"

    return DISPLAY_MATH.sub(inside, text)


def _inline_short_display_math(text: str) -> str:
    """'with\\n$$\\rho_0$$\\n' is a symbol in a sentence, not an equation."""

    def inline(match: re.Match) -> str:
        body = match.group(2).strip()
        return f"{match.group(1)} ${body}$ "

    return re.sub(r"([a-z,])[ \t]*\n\$\$([^$\n]{1,24})\$\$[ \t]*\n(?=[a-z(,.;])", inline, text)


def _image(match: re.Match, figures_dir: str) -> str:
    alt, target = match.groups()
    if "://" in target or target.startswith(f"{figures_dir}/"):
        return match.group(0)
    return f"![{alt}]({figures_dir}/{target})"


HEADING = re.compile(r"^(#{1,6}) +(.+?)[ \t]*$", re.MULTILINE)
SECTION_NUMBER = re.compile(r"^(?:[IVX]+|\d+|[A-Z])((?:\.\d+)*)\.?\s")


def _heading_levels(text: str) -> str:
    """Marker guesses heading levels from font sizes, page by page. Section numbers
    say the real depth: '2' is a section (##), '2.2' a subsection (###), and so on.
    Unnumbered headings (Abstract, References…) are sections. The record's own H1
    is the title, so nothing in the full text is H1."""

    def level(match: re.Match) -> str:
        heading = match.group(2)
        numbered = SECTION_NUMBER.match(heading)
        if numbered:
            depth = 2 + numbered.group(1).count(".")
        else:
            depth = 2
        return f"{'#' * min(depth, 6)} {heading}"

    return _outside_math(text, lambda s: HEADING.sub(level, s))


def _drop_title_heading(text: str, title: str) -> str:
    """The record already shows the title as its H1; drop Marker's copy of it."""
    if not title:
        return text
    match = re.match(r"\s*#{1,3} +([^\n]+)\n", text)
    heading = words(match.group(1).lower()) if match else None
    if heading and heading in (words(title.lower()), words(main_title(title).lower())):
        return text[match.end() :]
    return text
