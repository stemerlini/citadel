"""The Citadel app: topics on the left, references in the middle, details below."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Input, Static, Tree

from .. import __version__, cite, edit, paths, record
from .. import status as status_module
from ..config import Config
from ..index import Index
from ..library import Library
from . import logo
from .screens import (
    AboutScreen,
    DuplicatesScreen,
    EditScreen,
    PreviewScreen,
    Prompt,
    ReviewScreen,
    TopicPicker,
    open_externally,
)
from .theme import omarchy_theme

VIEWS = [
    ("all", "All references"),
    ("recent", "Recently added"),
    ("unread", "Unread"),
    ("reading", "Reading"),
    ("no-md", "Without Markdown"),
]
STATUS_MARKS = {"unread": ("●", "accent"), "reading": ("◐", "warning"), "read": ("✓", "")}
# Markdown column: converted, being converted right now, waiting, nothing to convert.
MD_MARKS = {
    "done": ("✓", "success"),
    "converting": ("⟳", "accent"),
    "waiting": ("·", ""),
    "no-pdf": ("—", ""),
}
CITE_FORMATS = ("reference", "bibtex", "key")

# (key, header, width, drop order): None width shares the spare room; higher drop
# orders disappear first when the window is narrow.
COLUMNS = [
    ("status", "", 1, 0),
    ("year", "Year", 4, 0),
    ("title", "Title", None, 0),
    ("author", "Author", 16, 1),
    ("type", "Type", 10, 3),
    ("topic", "Topic", 24, 2),
    ("md", "MD", 2, 0),
]
TITLE_MIN = 24


def table_layout(width: int, show_topic: bool) -> dict[str, int]:
    columns = [c for c in COLUMNS if show_topic or c[0] != "topic"]
    while True:
        fixed = sum(w for _, _, w, _ in columns if w) + 2 * len(columns)
        droppable = [c for c in columns if c[3]]
        if fixed + TITLE_MIN <= width or not droppable:
            break
        columns.remove(max(droppable, key=lambda c: c[3]))
    spare = max(width - fixed, TITLE_MIN)
    return {k: (w if w else spare) for k, _, w, _ in columns}


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 1)] + "…"


def highlight(snippet: str, style: str) -> Text:
    """An FTS snippet ('…the [fluctuation] [dynamo]…') with the matches styled
    (the index already puts the ellipses in)."""
    text = Text()
    for i, piece in enumerate(re.split(r"\[([^\]]*)\]", snippet)):
        text.append(piece, style=style if i % 2 else "dim")
    return text


def copy_text(app: App, text: str) -> None:
    """To the Wayland clipboard (wl-copy), or through the terminal (OSC 52)."""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy"], input=text.encode(), timeout=5, check=True)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    app.copy_to_clipboard(text)


class Citadel(App):
    TITLE = "Citadel"
    CSS_PATH = Path(__file__).with_name("app.tcss")

    BINDINGS = [
        Binding("space", "preview", "Preview"),
        Binding("enter", "open_pdf", "Open PDF", show=False),
        Binding("slash", "search", "Search", key_display="/"),
        Binding("c", "copy('reference')", "Cite"),
        Binding("b", "copy('bibtex')", "BibTeX"),
        Binding("k", "copy('key')", "Key", show=False),
        Binding("r", "cycle_status", "Read"),
        Binding("e", "edit", "Edit"),
        Binding("t", "move", "Topic"),
        Binding("m", "convert", "Markdown"),
        Binding("a", "add", "Add"),
        Binding("i", "review", "Review"),
        Binding("D", "duplicates", "Duplicates", show=False),
        Binding("tab", "focus_next", "Switch pane", show=False),
        Binding("question_mark", "about", "About", key_display="?"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config | None = None, index: Index | None = None) -> None:
        super().__init__()
        self.config = config or Config.load()
        self.library = Library(self.config.library_path)
        self.index = index or Index(paths.INDEX_FILE, self.library.root)
        self.view = ("view", "all")
        self.query_text = ""
        self.rows: list[dict] = []
        self.snippets: dict[str, str] = {}
        self.busy = ""  # what a background task is doing, for the top bar
        self.ingest_lock = threading.Lock()
        self.converting_key = ""  # the reference being converted right now
        self.duplicate_groups = []

    # -- layout ------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(logo.small(), id="brand-logo")
            with Vertical(id="brand"):
                yield Static("Citadel", id="brand-name")
                yield Static("reference library", id="brand-tagline")
            yield Static(id="activity")
            yield Static(id="stats")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("LIBRARY", classes="pane-title")
                yield Tree("Library", id="topics")
            with Vertical(id="main"):
                yield Static(id="list-title", classes="pane-title")
                yield Input(
                    placeholder="Search titles, authors, abstracts and full text…  (Esc to clear)",
                    id="search",
                )
                yield DataTable(id="refs", cursor_type="row", zebra_stripes=True)
                yield Static(id="empty")
                yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        theme = omarchy_theme()
        if theme:
            self.register_theme(theme)
            self.theme = theme.name
        self.query_one("#search").display = False
        variables = self.get_css_variables()
        self.tones = {name: variables.get(name, "") for name in ("accent", "warning", "success")}
        tree = self.query_one("#topics", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        self.reload()
        self.query_one("#refs", DataTable).focus()
        self.set_interval(1.5, self.refresh_activity)

    # -- data -> widgets ---------------------------------------------------------------

    def reload(self, keep: str | None = None) -> None:
        if self.library.exists():
            self.index.reindex()
        self.all_rows = self.index.list()
        self.duplicate_groups = self.find_duplicates()
        self.refresh_stats()
        self.refresh_tree()
        self.refresh_table(keep)

    def refresh_stats(self) -> None:
        total = len(self.all_rows)
        markdown = sum(r["has_fulltext"] for r in self.all_rows)
        inbox = (
            len([p for p in self.library.inbox.iterdir() if p.is_file()])
            if self.library.inbox.is_dir()
            else 0
        )
        review = (
            len(list(self.library.review.glob("*.citadel.json")))
            if self.library.review.is_dir()
            else 0
        )
        parts = [f"[b]{total}[/] references", f"[b]{markdown}[/] in Markdown"]
        if inbox:
            parts.append(f"[b $warning]{inbox}[/] in inbox")
        if review:
            parts.append(f"[b $warning]{review}[/] to review [dim](i)[/]")
        if self.duplicate_groups:
            parts.append(
                f"[b $error]{len(self.duplicate_groups)}[/] duplicate"
                f"{'s' if len(self.duplicate_groups) > 1 else ''} [dim](D)[/]"
            )
        self.query_one("#stats", Static).update(" · ".join(parts))

    def refresh_activity(self) -> None:
        """Conversion progress (from the status file the widget also reads), or a task."""
        live = status_module.read()
        converting = (
            live.current_citekey
            if live and live.state == "converting" and time.time() - live.updated < 30
            else ""
        )
        if converting != self.converting_key:
            finished = self.converting_key
            self.converting_key = converting
            if finished:
                self.reload(keep=self.current_key())  # its Markdown is ready: show ✓
            else:
                self.refresh_table()
        text = ""
        if live and live.state == "converting" and time.time() - live.updated < 30:
            gpu = f" · GPU {live.gpu['load']:.0f}%" if live.gpu else ""
            current = clip(live.current_title, 40) if live.current_title else ""
            text = (
                f"[b $accent]⚙ converting[/] {live.done}/{live.total}{gpu}"
                f"\n[dim]{escape(current)}[/]"
            )
        elif self.busy:
            text = f"[b $accent]⚙[/] {escape(self.busy)}"
        self.query_one("#activity", Static).update(text)

    def refresh_tree(self) -> None:
        tree = self.query_one("#topics", Tree)
        counts: Counter[str] = Counter()
        for row in self.all_rows:
            topic = row["topic"]
            while topic:
                counts[topic] += 1
                topic = topic.rsplit("/", 1)[0] if "/" in topic else ""
        tree.clear()
        view_counts = {
            "all": len(self.all_rows),
            "recent": min(len(self.all_rows), 30),
            "unread": sum(r["status"] == "unread" for r in self.all_rows),
            "reading": sum(r["status"] == "reading" for r in self.all_rows),
            "no-md": sum(r["has_pdf"] and not r["has_fulltext"] for r in self.all_rows),
        }
        for key, name in VIEWS:
            label = Text(name)
            label.append(f"  {view_counts[key]}", style="dim")
            tree.root.add_leaf(label, data=("view", key))
        tree.root.add_leaf(Text("─" * 20, style="dim"), data=None)
        nodes = {"": tree.root}
        for topic in sorted(t.path for t in self.library.topics()):
            parent = topic.rsplit("/", 1)[0] if "/" in topic else ""
            label = Text(topic.rsplit("/", 1)[-1])
            if counts[topic]:
                label.append(f"  {counts[topic]}", style="dim")
            else:
                label.stylize("dim")
            node = nodes.get(parent, tree.root).add(
                label, data=("topic", topic), expand=topic.count("/") == 0
            )
            nodes[topic] = node
        for node in list(nodes.values())[1:]:
            if not node.children:
                node.allow_expand = False

    def visible_rows(self) -> list[dict]:
        if self.query_text:
            return self.rows_from_search()
        kind, key = self.view
        rows = self.all_rows
        if kind == "topic":
            rows = [r for r in rows if r["topic"] == key or r["topic"].startswith(key + "/")]
        elif key == "recent":
            rows = sorted(rows, key=lambda r: r["added"], reverse=True)[:30]
        elif key in ("unread", "reading"):
            rows = [r for r in rows if r["status"] == key]
        elif key == "no-md":
            rows = [r for r in rows if r["has_pdf"] and not r["has_fulltext"]]
        return rows

    def rows_from_search(self) -> list[dict]:
        found = self.index.search(self.query_text, limit=200)
        self.snippets = {r["citekey"]: r["snippet"] for r in found}
        return found

    def on_resize(self) -> None:
        self.call_after_refresh(self.refresh_table)

    def refresh_table(self, keep: str | None = None) -> None:
        table = self.query_one("#refs", DataTable)
        if keep is None and table.row_count:
            keep = self.current_key()
        self.rows = self.visible_rows()
        show_topic = self.view[0] != "topic" or bool(self.query_text)
        width = (self.query_one("#main").content_size.width or self.size.width - 34) - 2
        layout = table_layout(width, show_topic)
        if layout != getattr(self, "_layout", None):
            self._layout = layout
            table.clear(columns=True)
            headers = {k: h for k, h, _, _ in COLUMNS}
            for key, column_width in layout.items():
                table.add_column(headers[key], key=key, width=column_width)
        else:
            table.clear()
        for row in self.rows:
            mark, color = STATUS_MARKS.get(row["status"], (" ", ""))
            cells = {
                "status": Text(mark, style=f"bold {self.tones[color]}" if color else "dim"),
                "year": str(row["year"] or ""),
                "title": Text(clip(row["title"], layout["title"])),
                "author": clip(row["first_author"], layout.get("author", 16)),
                "type": row["type"],
                "topic": Text(clip(row["topic"], layout.get("topic", 24)), style="dim"),
                "md": self.markdown_mark(row),
            }
            table.add_row(*(cells[k] for k in layout), key=row["citekey"])
        title, empty = self.list_title()
        self.query_one("#list-title", Static).update(title)
        self.query_one("#empty", Static).update(empty)
        self.query_one("#empty").display = not self.rows
        table.display = bool(self.rows)
        if keep and keep in {r["citekey"] for r in self.rows}:
            table.move_cursor(row=table.get_row_index(keep))
        self.refresh_details()

    def markdown_state(self, row: dict) -> str:
        if row["has_fulltext"]:
            return "done"
        if row["citekey"] == self.converting_key:
            return "converting"
        return "waiting" if row["has_pdf"] else "no-pdf"

    def markdown_mark(self, row: dict) -> Text:
        mark, tone = MD_MARKS[self.markdown_state(row)]
        return Text(f" {mark}", style=f"bold {self.tones[tone]}" if tone else "dim")

    def list_title(self) -> tuple[str, str]:
        count = len(self.rows)
        if self.query_text:
            return (
                f"SEARCH “{escape(self.query_text)}”  [dim]{count} found · Esc to clear[/]",
                "Nothing matches. Search looks at titles, authors, abstracts, tags and "
                "the converted full text.",
            )
        kind, key = self.view
        name = key if kind == "topic" else dict(VIEWS)[key]
        if not self.all_rows:
            return (
                name.upper(),
                "The library is empty.\n\nDrop PDFs in the inbox, or press "
                "[b]a[/] to add a PDF, a DOI or an arXiv id.",
            )
        return (f"{escape(name.upper())}  [dim]{count}[/]", "Nothing here yet.")

    def current_key(self) -> str | None:
        table = self.query_one("#refs", DataTable)
        if not self.rows or table.cursor_row is None or table.cursor_row >= len(self.rows):
            return None
        return self.rows[table.cursor_row]["citekey"]

    def current_row(self) -> dict | None:
        key = self.current_key()
        return next((r for r in self.rows if r["citekey"] == key), None)

    def refresh_details(self) -> None:
        panel = self.query_one("#details", Static)
        row = self.current_row()
        if row is None:
            panel.update("")
            return
        data = self.index.get(row["citekey"])["data"]
        text = Text()
        text.append(data["title"], style="bold")
        text.append("\n" + "; ".join(data.get("authors", [])[:8]), style="dim")
        where = " · ".join(
            str(p)
            for p in (
                data.get("journal"),
                data.get("year"),
                data.get("doi") and f"doi:{data['doi']}",
            )
            if p
        )
        if where:
            text.append("\n" + where)
        snippet = self.snippets.get(row["citekey"]) if self.query_text else ""
        if snippet:
            text.append("\n")
            text.append_text(highlight(snippet, f"bold {self.tones['accent']}"))
        elif data.get("abstract"):
            text.append("\n" + clip(data["abstract"], 400), style="dim")
        text.append("\n" + row["citekey"], style="dim")
        for tag in data.get("tags") or []:
            text.append("  ")
            text.append(f" {tag} ", style="reverse")
        panel.update(text)

    @on(DataTable.RowHighlighted, "#refs")
    def row_changed(self) -> None:
        self.refresh_details()

    @on(Tree.NodeHighlighted, "#topics")
    def topic_changed(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data and not self.query_text:
            self.view = event.node.data
            self.refresh_table()

    @on(DataTable.RowSelected, "#refs")
    def row_selected(self) -> None:
        self.action_open_pdf()

    # -- search ------------------------------------------------------------------------

    def action_search(self) -> None:
        box = self.query_one("#search", Input)
        box.display = True
        box.focus()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        text = event.value.strip()
        self.query_text = text if len(text) >= 2 else ""
        self.refresh_table()

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.query_one("#refs", DataTable).focus()

    def on_key(self, event) -> None:
        box = self.query_one("#search", Input)
        if event.key == "escape" and box.display:
            box.value = ""
            box.display = False
            self.query_text = ""
            self.refresh_table()
            self.query_one("#refs", DataTable).focus()
            event.stop()

    # -- actions on the selected reference ---------------------------------------------

    def check_action(self, action: str, parameters) -> bool | None:
        needs_row = {"preview", "open_pdf", "copy", "cycle_status", "edit", "move", "convert"}
        if action in needs_row and self.current_key() is None:
            return False
        return True

    def selected(self):
        """(Reference, record path, body) of the highlighted row."""
        key = self.current_key()
        ref, path = edit.load(self.index, key)
        _, body = record.split(path.read_text(encoding="utf-8"))
        return ref, path, body

    def action_preview(self) -> None:
        ref, path, body = self.selected()
        from ..convert import _pages

        self.push_screen(PreviewScreen(ref, path.parent, _pages(path.parent), body=body))

    def action_open_pdf(self) -> None:
        ref, path, _ = self.selected()
        pdf = path.with_suffix(".pdf")
        if pdf.exists():
            open_externally(pdf)
        else:
            self.notify("This reference has no PDF.", severity="warning")

    def action_copy(self, style: str) -> None:
        ref, _, _ = self.selected()
        copy_text(self, cite.format_ref(ref, style))
        what = {"reference": "Reference", "bibtex": "BibTeX entry", "key": "Citekey"}[style]
        self.notify(f"{what} copied: {ref.citekey}", timeout=3)

    def action_cycle_status(self) -> None:
        key = self.current_key()
        new = edit.cycle_status(self.index, key)
        self.reload(keep=key)
        self.notify(f"{key}: {new}", timeout=2)

    def action_edit(self) -> None:
        ref, _, _ = self.selected()

        def save(changes: dict | None) -> None:
            if changes:
                edit.update(self.index, ref.citekey, **changes)
                self.reload(keep=ref.citekey)
                self.notify(f"Saved {ref.citekey}.", timeout=3)

        self.push_screen(EditScreen(ref), save)

    def topic_paths(self) -> list[str]:
        return sorted(t.path for t in self.library.topics())

    def action_move(self) -> None:
        ref, _, _ = self.selected()

        def move(topic: str | None) -> None:
            if topic and topic != ref.topic:
                try:
                    edit.update(
                        self.index, ref.citekey, topics=set(self.topic_paths()), topic=topic
                    )
                except edit.EditError as error:
                    self.notify(str(error), severity="error")
                    return
                self.reload(keep=ref.citekey)
                self.notify(f"Moved to {topic}.", timeout=3)

        self.push_screen(
            TopicPicker(self.topic_paths(), ref.topic, f"Move {ref.citekey} to…"), move
        )

    def action_convert(self) -> None:
        row = self.current_row()
        if not row["has_pdf"]:
            self.notify("This reference has no PDF to convert.", severity="warning")
            return
        self.convert([row["citekey"]], force=bool(row["has_fulltext"]))

    @work(thread=True, exclusive=True, group="convert")
    def convert(self, citekeys: list[str], force: bool = False) -> None:
        from ..convert import Converter, ConvertError

        index = Index(paths.INDEX_FILE, self.library.root)  # this thread's own connection
        try:
            converter = Converter(index, self.config)
            jobs = converter.pending(citekeys, force=force)
            results = converter.run(jobs)
        except ConvertError as error:
            self.call_from_thread(self.notify, str(error), severity="error")
            return
        finally:
            index.close()
        for result in results:
            message = (
                f"{result.citekey}: Markdown ready ({result.pages} pages, {result.seconds:.0f}s)"
                if result.ok
                else f"{result.citekey}: {result.error}"
            )
            self.call_from_thread(
                self.notify, message, severity="information" if result.ok else "error"
            )
        self.call_from_thread(self.reload)

    # -- adding and reviewing ----------------------------------------------------------

    def action_add(self) -> None:
        self.push_screen(
            Prompt(
                "Add to the library",
                "a PDF path, doi:10.…, or arxiv:2101.12345",
                "PDFs are copied, arXiv papers downloaded; a DOI alone makes a "
                "reference without a PDF.",
            ),
            lambda target: target and self.run_ingester("add", [target]),
        )

    def action_review(self) -> None:
        self.push_screen(
            ReviewScreen(
                self.review_items(),
                self.topic_paths(),
                lambda action, names, **kw: self.run_ingester(action, names, **kw),
            )
        )

    def review_items(self):
        from ..ingest import Ingester

        return Ingester(self.library, self.index, None, self.config).review_items()

    @work(thread=True, group="ingest")
    def run_ingester(self, action: str, targets: list[str], **kwargs) -> None:
        """add / accept / reject / retry, one target after the other, in a worker thread
        with its own connections. The lock keeps two batches from racing on files."""
        from ..ingest import Ingester
        from ..sources import Http, SourceError

        with self.ingest_lock:
            index = Index(paths.INDEX_FILE, self.library.root)
            http = Http(self.config.metadata.contact_email)
            verb = {
                "add": "adding",
                "accept": "filing",
                "reject": "rejecting",
                "retry": "retrying",
                "edit": "correcting",
            }[action]
            filed: list[str] = []
            failures = 0
            try:
                ingester = Ingester(self.library, index, http, self.config)
                for number, target in enumerate(targets, 1):
                    self.busy = f"{verb} {number}/{len(targets)}: {target or 'all'}"
                    try:
                        if action == "add":
                            results = [ingester.add(target, **kwargs)]
                        elif action == "accept":
                            results = [ingester.accept(target, **kwargs)]
                        elif action == "retry":
                            results = ingester.retry(target)
                        elif action == "edit":
                            ingester.edit_review(target, **kwargs)
                            results = []
                        else:
                            results = [ingester.reject(target)]
                    except (LookupError, ValueError, OSError, SourceError) as error:
                        failures += 1
                        self.call_from_thread(self.notify, f"{target}: {error}", severity="error")
                        continue
                    for result in results:
                        if result.status == "filed":
                            filed.append(result.citekey)
                        elif result.status == "error":
                            failures += 1
                items = ingester.review_items()
            finally:
                self.busy = ""
                http.close()
                index.close()
        summary = f"{verb.capitalize()} done: {len(filed)} filed"
        if action == "retry":
            summary += f", {len(items)} still in review"
        if failures:
            summary += f", {failures} failed"
        self.call_from_thread(
            self.notify, summary, severity="warning" if failures else "information"
        )
        self.call_from_thread(self.after_ingest, items, filed[-1] if filed else "")
        if filed and self.config.convert.auto:
            self.call_from_thread(self.convert, filed)

    def after_ingest(self, items, citekey: str) -> None:
        self.reload(keep=citekey or None)
        if isinstance(self.screen, ReviewScreen):
            self.screen.refresh_items(items)

    def find_duplicates(self):
        from .. import duplicates

        try:
            return duplicates.find(self.index, self.library) if self.library.exists() else []
        except OSError:
            return []

    def action_duplicates(self) -> None:
        self.push_screen(DuplicatesScreen(self.duplicate_groups, self.resolve_duplicates))

    @work(thread=True, group="ingest")
    def resolve_duplicates(self, action: str, group, keep_id: str) -> None:
        from .. import duplicates
        from ..ingest import Ingester

        with self.ingest_lock:
            index = Index(paths.INDEX_FILE, self.library.root)
            try:
                if action == "ignore":
                    duplicates.ignore(self.library, group)
                    message = "Marked as not duplicates."
                else:
                    ingester = Ingester(self.library, index, None, self.config)
                    deleted = duplicates.keep(index, self.library, ingester, group, keep_id)
                    message = (
                        f"Kept {keep_id.split(':', 1)[1]}; deleted {len(deleted)} "
                        "(OneDrive recycle bin)"
                    )
            except (OSError, ValueError, LookupError) as error:
                self.call_from_thread(self.notify, str(error), severity="error")
                return
            finally:
                index.close()
        self.call_from_thread(self.notify, message)
        self.call_from_thread(self.after_duplicates)

    def after_duplicates(self) -> None:
        self.reload()
        if isinstance(self.screen, DuplicatesScreen):
            self.screen.refresh_groups(self.duplicate_groups)

    def action_about(self) -> None:
        self.push_screen(AboutScreen(__version__, str(self.library.root)))
