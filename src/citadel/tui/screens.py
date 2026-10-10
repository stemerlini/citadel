"""Modal screens: preview, topic picker, edit, add, review queue, prompts, about."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image as PILImage
from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Input,
    LoadingIndicator,
    Markdown,
    OptionList,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option
from textual_image._terminal import get_cell_size
from textual_image.widget import Image

from .. import record
from ..record import TYPES, Reference
from . import logo
from .pages import page_image


def open_externally(path: Path) -> None:
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def details_markup(ref: Reference) -> str:
    """Bibliographic details as Rich markup, for the preview and the detail pane."""
    rows = [
        ("Authors", "; ".join(ref.authors)),
        (
            "Published",
            " · ".join(
                p
                for p in (
                    ref.journal,
                    ref.volume and f"vol. {ref.volume}",
                    ref.pages and f"p. {ref.pages}",
                    str(ref.year or ""),
                )
                if p
            ),
        ),
        ("Type", ref.type),
        ("Topic", ref.topic),
        ("DOI", ref.doi),
        ("arXiv", ref.arxiv),
        ("ISBN", ref.isbn),
        ("Tags", ", ".join(ref.tags)),
        ("Citekey", ref.citekey),
        ("Status", ref.status),
        ("Markdown", ref.md_engine or "not converted yet"),
    ]
    return "\n".join(f"[dim]{k:<10}[/] {escape(str(v))}" for k, v in rows if v)


# -- preview -----------------------------------------------------------------------


class PreviewScreen(ModalScreen[None]):
    """A page of the PDF next to the reference's details; Tab shows the full text."""

    BINDINGS = [
        Binding("space,escape,q", "dismiss", "Close"),
        Binding("left,h", "page(-1)", "◀ Page", key_display="←"),
        Binding("right,l", "page(1)", "Page ▶", key_display="→"),
        Binding("tab", "toggle_text", "Full text"),
        Binding("o,enter", "open_pdf", "Open PDF"),
    ]

    def __init__(
        self, ref: Reference, folder: Path, pages: int = 0, pdf: Path | None = None, body: str = ""
    ) -> None:
        super().__init__()
        self.ref = ref
        self.folder = folder
        self.pdf = pdf if pdf is not None else folder / f"{folder.name}.pdf"
        self.has_pdf = self.pdf.exists()
        self.pages = pages
        self.page = 1
        self.body = body
        self.image_px: tuple[int, int] | None = None
        self.showing_text = not self.has_pdf

    def compose(self) -> ComposeResult:
        with Vertical(id="preview"):
            with Horizontal(id="preview-main"):
                with Vertical(id="preview-image"):
                    yield LoadingIndicator()
                with VerticalScroll(id="preview-info"):
                    yield Static(escape(self.ref.title), classes="title")
                    yield Static(details_markup(self.ref), classes="details")
                    if self.ref.abstract:
                        yield Static("Abstract", classes="section")
                        yield Static(escape(self.ref.abstract), classes="abstract")
            with VerticalScroll(id="preview-text"):
                yield Markdown(id="fulltext")
            yield Static(id="preview-footer", classes="footnote")

    def on_mount(self) -> None:
        text = record.fulltext(self.body)
        self.query_one("#fulltext", Markdown).update(
            text or "_No Markdown yet: press **m** in the list to convert this PDF._"
        )
        self.apply_mode()
        if self.has_pdf:
            self.load_page()
        else:
            self.query_one("#preview-image").remove_children()

    def apply_mode(self) -> None:
        self.query_one("#preview-main").display = not self.showing_text
        self.query_one("#preview-text").display = self.showing_text
        where = (
            "full text"
            if self.showing_text
            else (f"page {self.page} of {self.pages}" if self.pages else f"page {self.page}")
        )
        keys = (
            "[b]Tab[/] page view" if self.showing_text else "[b]←/→[/] pages · [b]Tab[/] full text"
        )
        pdf = " · [b]o[/] open PDF" if self.has_pdf else ""
        self.query_one("#preview-footer", Static).update(
            f"[dim]{where} · {keys}{pdf} · [b]Space[/] close[/]"
        )

    def action_toggle_text(self) -> None:
        if not self.has_pdf:
            return
        self.showing_text = not self.showing_text
        self.apply_mode()

    def action_page(self, delta: int) -> None:
        if not self.has_pdf or self.showing_text:
            return
        target = self.page + delta
        if target < 1 or (self.pages and target > self.pages):
            return
        self.page = target
        self.apply_mode()
        self.load_page()

    def action_open_pdf(self) -> None:
        if self.has_pdf:
            open_externally(self.pdf)

    @work(exclusive=True, thread=True)
    def load_page(self) -> None:
        path = page_image(self.pdf, self.page)
        self.app.call_from_thread(self.show_page, path)

    async def show_page(self, path: Path | None) -> None:
        holder = self.query_one("#preview-image")
        await holder.remove_children()
        if path is None:
            await holder.mount(Static("This page could not be rendered.", classes="no-image"))
            return
        with PILImage.open(path) as img:
            self.image_px = img.size
        image = Image(path, id="page-image")
        image.display = False  # shown once sized, to avoid a stretched flash
        await holder.mount(image)
        self.call_after_refresh(self.fit_image)

    def on_resize(self) -> None:
        self.call_after_refresh(self.fit_image)

    def fit_image(self) -> None:
        """Size the page so it keeps its aspect ratio (terminal cells are not square)."""
        images = self.query("#page-image")
        if not images or not self.image_px:
            return
        image = images.first()
        box = self.query_one("#preview-main")
        cell = get_cell_size()
        img_w, img_h = self.image_px
        rows = max(box.content_size.height, 4)
        cols = round(rows * cell.height * img_w / (img_h * cell.width))
        max_cols = max(box.content_size.width * 3 // 5, 10)
        if cols > max_cols:
            cols = max_cols
            rows = round(cols * cell.width * img_h / (img_w * cell.height))
        self.query_one("#preview-image").styles.width = cols
        image.styles.width = cols
        image.styles.height = rows
        image.display = True


# -- small dialogs -----------------------------------------------------------------


class TopicPicker(ModalScreen[str | None]):
    """Choose a topic folder; type to filter."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, topics: list[str], current: str = "", prompt: str = "Choose a topic"):
        super().__init__()
        self.topics = topics
        self.current = current
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.prompt, classes="title")
            yield Input(placeholder="type to filter…", id="topic-filter")
            yield OptionList(id="topic-list")

    def on_mount(self) -> None:
        self.fill("")
        self.query_one("#topic-filter", Input).focus()

    def fill(self, needle: str) -> None:
        options = self.query_one("#topic-list", OptionList)
        options.clear_options()
        for topic in self.topics:
            if needle.casefold() in topic.casefold():
                depth = topic.count("/")
                label = f"{'  ' * depth}{topic.rsplit('/', 1)[-1]}  [dim]{topic}[/]"
                options.add_option(Option(label, id=topic))
        if self.current in self.topics and not needle:
            options.highlighted = options.get_option_index(self.current)
        elif options.option_count:
            options.highlighted = 0

    @on(Input.Changed, "#topic-filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self.fill(event.value)

    @on(Input.Submitted, "#topic-filter")
    def submit_filter(self) -> None:
        options = self.query_one("#topic-list", OptionList)
        if options.highlighted is not None:
            self.dismiss(options.get_option_at_index(options.highlighted).id)

    def on_key(self, event) -> None:
        if event.key in ("down", "up"):
            options = self.query_one("#topic-list", OptionList)
            options.action_cursor_down() if event.key == "down" else options.action_cursor_up()
            event.stop()

    @on(OptionList.OptionSelected, "#topic-list")
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class Prompt(ModalScreen[str | None]):
    """One line of text (a DOI, an arXiv id, a path…)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", hint: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.title_text, classes="title")
            if self.hint:
                yield Static(self.hint, classes="subtitle")
            yield Input(placeholder=self.placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class EditScreen(ModalScreen[dict | None]):
    """Correct a reference's metadata. Returns the changed fields."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel"), Binding("ctrl+s", "save", "Save")]

    FIELDS = [
        ("title", "Title"),
        ("authors", "Authors (Surname, Given; …)"),
        ("year", "Year"),
        ("journal", "Journal / book"),
        ("volume", "Volume"),
        ("pages", "Pages"),
        ("doi", "DOI"),
        ("tags", "Tags (comma-separated)"),
    ]

    def __init__(self, ref: Reference) -> None:
        super().__init__()
        self.ref = ref

    def compose(self) -> ComposeResult:
        ref = self.ref
        values = {
            "title": ref.title,
            "authors": "; ".join(ref.authors),
            "year": str(ref.year or ""),
            "journal": ref.journal,
            "volume": ref.volume,
            "pages": ref.pages,
            "doi": ref.doi,
            "tags": ", ".join(ref.tags),
        }
        with VerticalScroll(classes="dialog wide"):
            yield Static(f"Edit {ref.citekey}", classes="title")
            yield Static(
                "[dim]Ctrl+S save · Esc cancel · the citekey and file names never change[/]",
                classes="subtitle",
            )
            for key, label in self.FIELDS:
                yield Static(label, classes="field-label")
                yield Input(values[key], id=f"edit-{key}")
            yield Static("Type", classes="field-label")
            yield Select([(t, t) for t in TYPES], value=ref.type, allow_blank=False, id="edit-type")
            yield Static("Abstract", classes="field-label")
            yield TextArea(ref.abstract, id="edit-abstract", soft_wrap=True)

    def action_save(self) -> None:
        get = lambda key: self.query_one(f"#edit-{key}", Input).value.strip()  # noqa: E731
        year = get("year")
        if year and not year.isdigit():
            self.notify("The year must be a number.", severity="error")
            return
        changes = {
            "title": get("title"),
            "authors": [a.strip() for a in get("authors").split(";") if a.strip()],
            "year": int(year) if year else None,
            "journal": get("journal"),
            "volume": get("volume"),
            "pages": get("pages"),
            "doi": get("doi").lower(),
            "tags": [t.strip() for t in get("tags").split(",") if t.strip()],
            "type": self.query_one("#edit-type", Select).value,
            "abstract": self.query_one("#edit-abstract", TextArea).text.strip(),
        }
        if not changes["title"]:
            self.notify("A reference needs a title.", severity="error")
            return
        current = {k: getattr(self.ref, k) for k in changes}
        self.dismiss({k: v for k, v in changes.items() if v != current[k]})


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "dismiss(True)", "Yes"), Binding("n,escape", "dismiss(False)", "No")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.question)
            yield Static("[b]y[/] yes · [b]n[/] no", classes="footnote")


# -- review queue ------------------------------------------------------------------


class ReviewScreen(ModalScreen[None]):
    """Items waiting in _review/ with Citadel's proposal; settle them one by one."""

    BINDINGS = [
        Binding("escape,q,i", "dismiss", "Close"),
        Binding("a,enter", "accept", "Accept"),
        Binding("t", "accept_topic", "Other topic"),
        Binding("d", "accept_doi", "Use DOI"),
        Binding("x", "reject", "Reject"),
        Binding("space", "preview", "Preview"),
    ]

    def __init__(self, items, topics: list[str], settle) -> None:
        super().__init__()
        self.items = items  # list[ingest.Proposal]
        self.topics = topics
        self.settle = settle  # callable(action, name, **kwargs) run by the app

    def compose(self) -> ComposeResult:
        with Vertical(id="review"):
            yield Static(id="review-title", classes="title")
            with Horizontal():
                yield OptionList(id="review-list")
                with VerticalScroll(id="review-info"):
                    yield Static(id="review-details")
            yield Static(
                "[dim][b]a[/] accept · [b]t[/] accept with another topic · "
                "[b]d[/] accept with a DOI · [b]x[/] reject · [b]Space[/] preview · "
                "[b]Esc[/] close[/]",
                classes="footnote",
            )

    def on_mount(self) -> None:
        self.refresh_items(self.items)
        self.query_one("#review-list").focus()

    def refresh_items(self, items) -> None:
        self.items = items
        self.query_one("#review-title", Static).update(
            f"Review · {len(items)} waiting" if items else "Review · nothing waiting"
        )
        options = self.query_one("#review-list", OptionList)
        index = options.highlighted or 0
        options.clear_options()
        for item in items:
            options.add_option(Option(escape(item.file), id=item.file))
        if items:
            options.highlighted = min(index, len(items) - 1)
        self.show_details()

    @property
    def current(self):
        options = self.query_one("#review-list", OptionList)
        if options.highlighted is None or not self.items:
            return None
        return self.items[options.highlighted]

    @on(OptionList.OptionHighlighted, "#review-list")
    def show_details(self) -> None:
        item = self.current
        panel = self.query_one("#review-details", Static)
        if item is None:
            panel.update("[dim]The review queue is empty.[/]")
            return
        meta = item.metadata or {}
        lines = [f"[b $warning]{escape(item.reason)}[/]", ""]
        if meta:
            lines += [
                f"[b]{escape(meta.get('title', ''))}[/]",
                escape("; ".join(meta.get("authors", [])[:6])),
                f"{meta.get('year') or 'n.d.'} · {escape(meta.get('journal', ''))}",
                f"[dim]DOI[/] {escape(meta.get('doi', ''))}" if meta.get("doi") else "",
                f"[dim]metadata confidence[/] {item.confidence:.2f} ({item.method})",
            ]
        if item.topic:
            lines.append(f"[dim]topic[/] {item.topic} ({item.topic_confidence:.2f})")
        if item.duplicate_of:
            lines.append(f"[dim]duplicate of[/] {item.duplicate_of}")
        if item.evidence:
            lines += ["", "[dim]What Citadel tried:[/]"]
            lines += [f"[dim]· {escape(e[:110])}[/]" for e in item.evidence[-8:]]
        panel.update("\n".join(line for line in lines if line is not None))

    def action_accept(self) -> None:
        if self.current:
            self.settle("accept", self.current.file)

    def action_accept_topic(self) -> None:
        item = self.current
        if item:
            self.app.push_screen(
                TopicPicker(self.topics, item.topic, f"Topic for {item.file}"),
                lambda topic: topic and self.settle("accept", item.file, topic=topic),
            )

    def action_accept_doi(self) -> None:
        item = self.current
        if item:
            self.app.push_screen(
                Prompt("Accept with a DOI", "10.…", "Citadel takes the metadata from Crossref."),
                lambda doi: doi and self.settle("accept", item.file, doi=doi),
            )

    def action_reject(self) -> None:
        item = self.current
        if item:
            what = "deleted (exact duplicate)" if item.duplicate_of else "kept in _attachments/"
            self.app.push_screen(
                ConfirmScreen(f"Reject {item.file}? It will be {what}."),
                lambda yes: yes and self.settle("reject", item.file),
            )

    def action_preview(self) -> None:
        item = self.current
        if item:
            meta = item.metadata or {}
            ref = Reference(
                citekey="(in review)",
                title=meta.get("title") or item.file,
                authors=meta.get("authors", []),
                year=meta.get("year"),
                journal=meta.get("journal", ""),
                doi=meta.get("doi", ""),
                abstract=meta.get("abstract", ""),
                topic=item.topic,
            )
            pdf = self.app.library.review / item.file
            self.app.push_screen(PreviewScreen(ref, pdf.parent, pdf=pdf))


class AboutScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark,space,q", "dismiss", "Close")]

    def __init__(self, version: str, library: str) -> None:
        super().__init__()
        self.version = version
        self.library = library

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog about"):
            yield Static(logo.banner(), id="about-logo")
            yield Static(f"[b]Citadel {self.version}[/]  [dim]the maesters' library[/]")
            yield Static(
                "Drop papers in the inbox: Citadel names, files and converts them, cites "
                "them, and serves them to agents.\n\n"
                f"[dim]Library[/] {escape(self.library)}\n\n"
                "[b]Space[/] preview · [b]Enter[/] open PDF · [b]/[/] search · "
                "[b]c[/] copy reference · [b]b[/] BibTeX · [b]k[/] citekey · "
                "[b]r[/] read status · [b]e[/] edit · [b]t[/] move to topic · "
                "[b]m[/] convert to Markdown · [b]a[/] add · [b]i[/] review queue",
                classes="details",
            )
