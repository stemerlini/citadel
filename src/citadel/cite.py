"""Citations: BibTeX entries and formatted references.

    bibtex(ref)      @article{verma2004statistical, author = {Verma, Mahendra K.}, …}
    reference(ref)   Verma, M. K. (2004). Statistical theory of … Physics Reports, 401, 229–380.
                     https://doi.org/10.1016/j.physrep.2004.07.007

The reference style is APA-like, the usual default in physics and engineering
reports; LaTeX users take the BibTeX and let their own style decide.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import record
from .record import Reference

BIB_FILE = "library.bib"  # in the library root, for LaTeX, Overleaf and Pandoc

BIBTEX_TYPES = {
    "article": "article",
    "book": "book",
    "chapter": "incollection",
    "conference": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "preprint": "misc",
    "standard": "misc",
    "slides": "misc",
    "poster": "misc",
    "dataset": "misc",
    "misc": "misc",
}
CONTAINER_FIELD = {"article": "journal", "chapter": "booktitle", "conference": "booktitle"}
MAX_LISTED_AUTHORS = 5  # in formatted references; more become "et al."
FORMATS = ("reference", "bibtex", "key")


def bibtex(ref: Reference) -> str:
    kind = BIBTEX_TYPES.get(ref.type, "misc")
    fields: list[tuple[str, str]] = [
        # Organisations (no comma) are braced so BibTeX does not split them into names.
        (
            "author",
            " and ".join(_escape(a) if "," in a else "{" + _escape(a) + "}" for a in ref.authors),
        ),
        # Double braces keep capitals (MHD, Kelvin-Helmholtz) as written.
        ("title", "{" + _escape(ref.title, keep_math=True) + "}"),
    ]
    container = CONTAINER_FIELD.get(ref.type)
    if container and ref.journal:
        fields.append((container, _escape(ref.journal)))
    elif ref.journal and kind == "misc" and ref.type != "preprint":
        fields.append(("howpublished", _escape(ref.journal)))
    if ref.type == "report" and ref.publisher:
        fields.append(("institution", _escape(ref.publisher)))
    elif ref.publisher and kind in ("book", "incollection", "inproceedings"):
        fields.append(("publisher", _escape(ref.publisher)))
    fields += [
        ("year", str(ref.year) if ref.year else ""),
        ("volume", ref.volume),
        ("number", ref.issue),
        (
            "pages",
            ref.pages.replace("-", "--")
            if "-" in ref.pages and "--" not in ref.pages
            else ref.pages,
        ),
        ("doi", ref.doi),
        ("isbn", ref.isbn),
    ]
    if ref.arxiv:
        fields += [("eprint", ref.arxiv), ("archiveprefix", "arXiv")]
    if ref.url and not ref.doi:
        fields.append(("url", ref.url))
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
    return f"@{kind}{{{ref.citekey},\n{body}\n}}\n"


def reference(ref: Reference) -> str:
    """One formatted reference (APA-like), as plain text."""
    parts = [f"{_author_list(ref.authors)} ({ref.year or 'n.d.'})."]
    title = _sentence_end(_plain_math(ref.title))
    if ref.type in ("book", "thesis", "report", "standard"):
        parts.append(title)  # stand-alone works: the title is the container
        if ref.publisher:
            parts.append(_sentence_end(ref.publisher))
    else:
        parts.append(title)
        container = ref.journal
        if container:
            if ref.type == "chapter":
                container = f"In {container}"
            details = ", ".join(
                p
                for p in (
                    ref.volume + (f"({ref.issue})" if ref.issue else ""),
                    ref.pages.replace("--", "–").replace("-", "–"),
                )
                if p
            )
            parts.append(_sentence_end(f"{container}, {details}" if details else container))
        elif ref.type == "preprint" and ref.arxiv:
            parts.append(f"arXiv:{ref.arxiv}.")
    if ref.doi:
        parts.append(f"https://doi.org/{ref.doi}")
    elif ref.url:
        parts.append(ref.url)
    return " ".join(parts)


def format_ref(ref: Reference, style: str) -> str:
    if style == "bibtex":
        return bibtex(ref)
    if style == "key":
        return ref.citekey
    return reference(ref)


# -- helpers -----------------------------------------------------------------------


def _initials(given: str) -> str:
    """'Mahendra K.' -> 'M. K.', 'Jean-Luc' -> 'J.-L.'"""
    out = []
    for part in given.replace(".", " ").split():
        out.append("-".join(f"{p[0]}." for p in part.split("-") if p))
    return " ".join(out)


def _apa_name(author: str) -> str:
    if "," not in author:
        return author  # an organisation
    family, given = (s.strip() for s in author.split(",", 1))
    initials = _initials(given)
    return f"{family}, {initials}" if initials else family


def _author_list(authors: list[str]) -> str:
    if not authors:
        return "Anonymous"
    names = [_apa_name(a) for a in authors]
    if len(names) > MAX_LISTED_AUTHORS:
        return f"{', '.join(names[:3])}, et al."
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])}, & {names[-1]}"


def _sentence_end(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "?", "!")) else f"{text}."


def _plain_math(text: str) -> str:
    """$-1/2$ -> -1/2 in plain-text references."""
    return re.sub(r"\$([^$]*)\$", r"\1", text)


BIBTEX_SPECIAL = {"&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_"}


def _escape(text: str, keep_math: bool = False) -> str:
    """Escape BibTeX/LaTeX specials; maths in titles ($…$) is kept as written."""

    def escape_plain(part: str) -> str:
        part = "".join(BIBTEX_SPECIAL.get(c, c) for c in part)
        return part if keep_math else part.replace("$", r"\$")

    if not keep_math:
        return escape_plain(text)
    pieces = re.split(r"(\$[^$]*\$)", text)
    return "".join(p if i % 2 else escape_plain(p) for i, p in enumerate(pieces))


def export_bib(
    index, target: Path | None = None, library: str | None = None
) -> tuple[Path, bool, int]:
    """Write references as BibTeX, sorted by citekey. The file is only rewritten when
    its content changes (no needless OneDrive sync). Returns (path, changed, count).

    library: only that library's references, by default into its own library.bib
    (so a work library's entries stay in the work OneDrive); None: every library, by
    default the default library. Entries carry their index citekey, unique across
    libraries."""
    name = library if library is not None else ""
    target = target or index.roots.get(name, index.root) / BIB_FILE
    entries = []
    for row in sorted(index.list(library=name), key=lambda r: r["citekey"]):
        folder = index.path(row)
        try:
            ref, _ = record.read(folder / f"{folder.name}.md")
        except (OSError, record.RecordError):
            continue
        ref.citekey = row["citekey"]
        entries.append(bibtex(ref))
    content = (
        "% Generated by Citadel from the library's records: edit the references in "
        "Citadel, not here.\n\n" + "\n".join(entries)
    )
    try:
        if target.read_text(encoding="utf-8") == content:
            return target, False, len(entries)
    except OSError:
        pass
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return target, True, len(entries)
