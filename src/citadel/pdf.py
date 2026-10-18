"""What can be read from a PDF without converting it: metadata, text, title guesses.

Uses poppler's pdfinfo/pdftotext/pdftoppm and Tesseract (all installed with
Omarchy), so nothing heavy is loaded. Full conversion to Markdown is Marker's
job (Phase 4); OCR here only covers the first pages, to identify scans.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .identifiers import is_junk_title

TIMEOUT = 60  # seconds per external call; a broken PDF must not hang ingest
SIZE_PAGES = 5  # pages whose size decides the layout
LANDSCAPE = 1.2  # width/height above which a page is landscape (4:3 is 1.33, 16:9 1.78)
SLIDE_WORDS = 160  # fewer words per landscape page than this reads as slides
POSTER_SIDE = 1500  # points (A2 and up): a single page this big is a poster
PRESENTATION = re.compile(
    r"powerpoint|keynote|impress|beamer|google slides|prezi|canva", re.IGNORECASE
)
PAGE_SIZE = re.compile(r"^Page\s+\d+\s+size$")
MAX_TITLE_CANDIDATES = 3

# Pages publishers and repositories put in front of the paper itself.
COVER_MARKERS = re.compile(
    r"this content has been downloaded from iopscience|this content was downloaded from|"
    r"this content downloaded from|see discussions, stats, and author profiles|"
    r"jstor is a not-for-profit|downloaded from https?://\S+ on|terms and conditions apply",
    re.IGNORECASE,
)

Line = tuple[float, float, float, str]  # (height, top, bottom, text)


@dataclass
class PdfText:
    path: Path | None = None
    pages: int = 0
    info: dict[str, str] = field(default_factory=dict)  # pdfinfo fields (Title, Author, …)
    first_pages: str = ""  # the paper's first pages (after any cover page)
    cover: str = ""  # text of a publisher cover page, if there was one
    title_candidates: list[str] = field(default_factory=list)  # best first
    has_text: bool = False  # False for scans without a text layer
    ocr: bool = False  # True when first_pages came from Tesseract
    later_pages: str = ""  # pages 3-8, read only when needed (see more_text)
    page_sizes: list[tuple[float, float]] = field(default_factory=list)  # first pages, pts
    words_per_page: float = 0.0  # over the first pages

    @property
    def layout(self) -> str:
        """document | slides | poster, from the page geometry and the producing software.

        Slides: landscape pages made by presentation software, or landscape pages with
        few words. Poster: a single, very large page."""
        if not self.page_sizes:
            return "document"
        width, height = self.page_sizes[0]
        if self.pages == 1 and max(width, height) >= POSTER_SIDE:
            return "poster"
        landscape = sum(w >= LANDSCAPE * h for w, h in self.page_sizes)
        if landscape * 2 > len(self.page_sizes):
            software = f"{self.info.get('Creator', '')} {self.info.get('Producer', '')}"
            if PRESENTATION.search(software) or self.words_per_page < SLIDE_WORDS:
                return "slides"
        return "document"

    @property
    def title_guess(self) -> str:
        return self.title_candidates[0] if self.title_candidates else ""

    @property
    def meta_title(self) -> str:
        """The PDF's Title metadata, or "" when it is junk (a bibcode, a path, a DOI…)."""
        title = self.info.get("Title", "")
        return "" if is_junk_title(title) else title.strip()

    @property
    def meta_author(self) -> str:
        author = self.info.get("Author", "").strip()
        return "" if is_junk_title(author + " x") else author

    @property
    def all_text(self) -> str:
        """First pages and cover together: where a paper's own identifiers are printed."""
        return f"{self.first_pages}\n{self.cover}"

    def more_text(self) -> str:
        """Text of pages 3-8, read once (books print ISBN, authors and year on the
        copyright page)."""
        if not self.later_pages and self.path is not None and not self.ocr:
            self.later_pages = pdftotext(self.path, ["-f", "3", "-l", "8"])
        return self.later_pages


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path: Path, ocr: bool = True) -> PdfText:
    result = PdfText(path=path, info=_pdfinfo(path))
    result.pages = int(result.info.get("Pages", "0") or 0)
    result.page_sizes = _page_sizes(result.info)
    pages = pdftotext(path, ["-l", "3"]).split("\f")
    counted = [len(p.split()) for p in pages if p.strip()]
    result.words_per_page = sum(counted) / len(counted) if counted else 0.0
    has_text = len(re.findall(r"[A-Za-z]{3,}", " ".join(pages))) >= 20
    title_page = 1
    if has_text and pages and COVER_MARKERS.search(pages[0]):
        result.cover = pages[0]
        pages = pages[1:]
        title_page = 2
    # Skip pages without words (cover artwork, blank pages) to reach the title page.
    while len(pages) > 1 and len(re.findall(r"[A-Za-z]{2,}", pages[0])) < 2:
        pages = pages[1:]
        title_page += 1
    if has_text:
        result.has_text = True
        result.first_pages = "\n".join(pages[:2])
        layout = pdftotext(path, ["-f", str(title_page), "-l", str(title_page), "-bbox-layout"])
        result.title_candidates = guess_titles(*bbox_lines(layout))
    elif ocr:
        lines, page_height, text = ocr_first_pages(path)
        result.first_pages = text
        result.has_text = result.ocr = len(re.findall(r"[A-Za-z]{3,}", text)) >= 20
        result.title_candidates = guess_titles(lines, page_height)
    return result


def _run(args: list[str]) -> str:
    try:
        done = subprocess.run(args, capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode("utf-8", "replace")


def _pdfinfo(path: Path) -> dict[str, str]:
    """pdfinfo's fields, with the sizes of the first pages ("Page    1 size", …)."""
    info = {}
    for line in _run(
        ["pdfinfo", "-enc", "UTF-8", "-f", "1", "-l", str(SIZE_PAGES), str(path)]
    ).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            info[key.strip()] = value.strip()
    return info


def _page_sizes(info: dict[str, str]) -> list[tuple[float, float]]:
    sizes = []
    for key, value in info.items():
        found = re.match(r"([\d.]+) x ([\d.]+) pts", value)
        if (PAGE_SIZE.match(key) or key == "Page size") and found:
            sizes.append((float(found.group(1)), float(found.group(2))))
    return sizes[:SIZE_PAGES] if any(PAGE_SIZE.match(k) for k in info) else sizes[:1]


def pdftotext(path: Path, options: list[str]) -> str:
    return _run(["pdftotext", "-q", "-enc", "UTF-8", *options, str(path), "-"])


# -- OCR (scans) ------------------------------------------------------------------


def ocr_first_pages(path: Path, pages: int = 2) -> tuple[list[Line], float, str]:
    """Tesseract on the first pages: lines of page 1 (with heights), its page height,
    and the text of all pages."""
    with tempfile.TemporaryDirectory(prefix="citadel-ocr-") as tmp:
        _run(
            [
                "pdftoppm",
                "-r",
                "200",
                "-gray",
                "-png",
                "-f",
                "1",
                "-l",
                str(pages),
                str(path),
                f"{tmp}/page",
            ]
        )
        images = sorted(Path(tmp).glob("page*.png"))
        texts, first_lines, first_height = [], [], 0.0
        for number, image in enumerate(images):
            lines, height = tsv_lines(_run(["tesseract", str(image), "-", "tsv"]))
            if number == 0:
                first_lines, first_height = lines, height
            texts.append("\n".join(text for *_, text in lines))
    return first_lines, first_height, "\n".join(texts)


def tsv_lines(tsv: str) -> tuple[list[Line], float]:
    """Lines (with heights) and the page height, from Tesseract's TSV output."""
    words: dict[tuple[str, str, str], list[tuple[int, int, str]]] = {}
    page_height = 0.0
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) >= 10 and cols[0] == "1":
            page_height = float(cols[9]) if cols[9].isdigit() else 0.0
            continue
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        try:
            if float(cols[10]) < 30:  # word confidence
                continue
            top, height = int(cols[7]), int(cols[9])
        except ValueError:
            continue
        words.setdefault((cols[2], cols[3], cols[4]), []).append((top, height, cols[11]))
    lines = []
    for items in words.values():
        top = min(t for t, _, _ in items)
        bottom = max(t + h for t, h, _ in items)
        heights = sorted(h for _, h, _ in items)
        lines.append(
            (
                float(heights[len(heights) // 2]),
                float(top),
                float(bottom),
                " ".join(w for _, _, w in items),
            )
        )
    return sorted(lines, key=lambda line: line[1]), page_height


# -- title guesses ----------------------------------------------------------------

NOT_TITLE = re.compile(
    r"^(article|letter|review|brief report|research article|original paper|open access|"
    r"contents lists available|journal of|physics of|physical review|view online|"
    r"available online|received|accepted|published|copyright|©|doi|https?://|www\.|"
    r"this content|download details|ip address|you may also like|please note|"
    r"the table of contents|unclassified|abstract|keywords|citation|to cite this|"
    r"topical review|review article|editorial|invited paper|special issue)\b",
    re.IGNORECASE,
)
CHAPTER_PREFIX = re.compile(r"^(chapter|part|section)\s+[\dIVXLC]+[.:]?\s+", re.IGNORECASE)


def guess_titles(lines: list[Line], page_height: float = 0.0) -> list[str]:
    """Title candidates from the lines of a title page, most likely first.

    Lines of similar height that follow each other form a block (titles wrap).
    Blocks in the top 60% of the page that read like a title are ranked by
    text height: the title is usually the biggest text on its page."""
    if not lines:
        return []
    page_height = page_height or max(bottom for _, _, bottom, _ in lines) or 1
    blocks: list[dict] = []
    for height, top, bottom, text in lines:
        last = blocks[-1] if blocks else None
        if (
            last
            and abs(height - last["height"]) <= 0.15 * last["height"]
            and top - last["bottom"] <= 1.2 * height
        ):
            last["texts"].append(text)
            last["bottom"] = bottom
        else:
            blocks.append({"height": height, "top": top, "bottom": bottom, "texts": [text]})
    candidates = []
    for block in blocks:
        text = CHAPTER_PREFIX.sub("", re.sub(r"\s+", " ", " ".join(block["texts"])).strip())
        real_words = len(re.findall(r"[A-Za-z]{3,}", text))
        if (
            block["top"] < 0.6 * page_height
            and 2 <= real_words <= 40
            and not NOT_TITLE.match(text)
            and not COVER_MARKERS.search(text)
        ):
            candidates.append((block["height"], block["top"], text))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    return list(dict.fromkeys(text for _, _, text in candidates))[:MAX_TITLE_CANDIDATES]


def bbox_lines(bbox_xhtml: str) -> tuple[list[Line], float]:
    """Lines (with heights), top to bottom, and the page height, from
    pdftotext -bbox-layout."""
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", bbox_xhtml)
    xml = re.sub(r'\sxmlns="[^"]*"', "", xml, count=1)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], 0.0
    page = root.find(".//page")
    page_height = float(page.get("height", 0)) if page is not None else 0.0
    lines = []
    for line in root.iter("line"):
        words = [w for w in line.iter("word") if w.text and w.text.strip()]
        if not words:
            continue
        # The median word height, not the line's box: superscripts and maths stretch
        # the box, and would make a line of body text look like a title.
        heights = sorted(float(w.get("yMax", 0)) - float(w.get("yMin", 0)) for w in words)
        top, bottom = float(line.get("yMin", 0)), float(line.get("yMax", 0))
        text = " ".join(w.text.strip() for w in words)
        lines.append((heights[len(heights) // 2], top, bottom, text))
    return sorted(lines, key=lambda line: line[1]), page_height
