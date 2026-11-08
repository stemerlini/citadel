"""Exploring the citation tree from a paper, and fetching what you find.

f            on a reference in the list opens this screen
Enter / →    step into the highlighted work: its own references
←            back up the trail
Tab          references ⇄ cited by
a            add the highlighted (or marked) works to the library
v            mark; o open the DOI page; Esc close
"""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, LoadingIndicator, Static

from .. import paths
from ..graph import Graph, Work
from ..index import Index
from ..sources import Http

MARKS = {"library": "✓", "pdf": "⬇", "none": "·"}


def mark_of(work: Work) -> str:
    if work.citekey:
        return "library"
    return "pdf" if (work.pdf_url or work.meta.arxiv) else "none"


class ReferencesScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
        # The table uses the arrows for its own cursor: these must win.
        Binding("right,l", "explore", "Step into", priority=True),
        Binding("left,h,backspace", "back", "Back", priority=True),
        Binding("tab", "switch", "Refs ⇄ cited by", priority=True),
        Binding("a", "add", "Add"),
        Binding("v", "mark", "Mark"),
        Binding("o", "open_doi", "Open DOI"),
        Binding("g", "google", "Google"),
    ]

    def __init__(self, citekey: str, topic: str, tones: dict[str, str]) -> None:
        super().__init__()
        self.citekey = citekey
        self.topic = topic  # where fetched works go when their topic is unclear
        self.tones = tones
        self.trail: list[Work] = []  # the works stepped into, root first
        self.direction = "references"
        self.works: list[Work] = []
        self.marked: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="refs-screen"):
            yield Static(id="refs-trail", classes="title")
            yield Static(id="refs-summary", classes="subtitle")
            yield LoadingIndicator(id="refs-loading")
            yield DataTable(id="refs-table", cursor_type="row", zebra_stripes=True)
            yield Static(id="refs-details")
            yield Static(
                "[dim][b]Enter[/] step into · [b]←[/] back · [b]Tab[/] references ⇄ "
                "cited by · [b]a[/] add (marked or highlighted) · [b]v[/] mark · "
                "[b]o[/] open DOI · [b]g[/] Google · [b]Esc[/] close\n"
                "✓ in your library · ⬇ open-access PDF · · metadata only[/]",
                classes="footnote",
            )

    def on_mount(self) -> None:
        table = self.query_one("#refs-table", DataTable)
        for key, label, width in (
            ("mark", "", 2),
            ("year", "Year", 4),
            ("author", "Author", 16),
            ("title", "Title", None),
            ("cited", "Cited", 6),
        ):
            table.add_column(label, key=key, width=width)
        self.load_root()

    # -- loading (network, in a worker thread) ----------------------------------------

    def busy(self, on: bool) -> None:
        self.query_one("#refs-loading").display = on
        self.query_one("#refs-table").display = not on

    @work(thread=True, exclusive=True, group="refs")
    def load_root(self) -> None:
        self.app.call_from_thread(self.busy, True)
        with _connections() as (http, index):
            graph = Graph(http, index)
            root = graph.work_for(self.citekey)
            works = self._fetch(graph, root) if root else []
        if root is None:
            self.app.call_from_thread(self.not_found)
            return
        self.app.call_from_thread(self.show, [root], works)

    @work(thread=True, exclusive=True, group="refs")
    def load(self, trail: list[Work]) -> None:
        self.app.call_from_thread(self.busy, True)
        with _connections() as (http, index):
            graph = Graph(http, index)
            works = self._fetch(graph, trail[-1])
        self.app.call_from_thread(self.show, trail, works)

    def _fetch(self, graph: Graph, work: Work) -> list[Work]:
        if self.direction == "cited_by":
            return graph.cited_by(work, 100)
        return graph.references(work)

    def not_found(self) -> None:
        self.busy(False)
        self.query_one("#refs-trail", Static).update(
            f"{self.citekey}: OpenAlex does not know this paper, so its references are unknown."
        )

    def show(self, trail: list[Work], works: list[Work]) -> None:
        self.trail, self.works = trail, works
        self.busy(False)
        crumbs = " › ".join(w.label for w in trail)
        self.query_one("#refs-trail", Static).update(
            f"{crumbs}  [dim]{_clip(trail[-1].meta.title, 70)}[/]"
        )
        what = "cited by" if self.direction == "cited_by" else "references"
        have = sum(bool(w.citekey) for w in works)
        free = sum(mark_of(w) == "pdf" for w in works)
        self.query_one("#refs-summary", Static).update(
            f"{len(works)} {what} · {have} in your library · {free} with an open-access PDF"
        )
        table = self.query_one("#refs-table", DataTable)
        table.clear()
        # Title gets what the table has left after the fixed columns, their padding
        # (2 cells each) and the scrollbar: the citation count stays in view.
        table_width = table.size.width or int((self.size.width or 120) * 0.9)
        width = max(20, table_width - (2 + 4 + 16 + 6) - 2 * 5 - 2)
        for w in works:
            kind = mark_of(w)
            tone = {
                "library": self.tones.get("success", ""),
                "pdf": self.tones.get("accent", ""),
            }.get(kind, "")
            prefix = "● " if w.id in self.marked else ""
            table.add_row(
                Text(MARKS[kind], style=f"bold {tone}" if tone else "dim"),
                str(w.meta.year or ""),
                _clip(w.label.rsplit(" ", 1)[0], 16),
                Text(prefix + _clip(w.meta.title, width), style="dim" if w.citekey else ""),
                str(w.cited_by or ""),
                key=w.id,
            )
        table.focus()
        self.show_details()

    # -- the highlighted work ------------------------------------------------------------

    @property
    def current(self) -> Work | None:
        table = self.query_one("#refs-table", DataTable)
        if not self.works or table.cursor_row is None or table.cursor_row >= len(self.works):
            return None
        return self.works[table.cursor_row]

    @on(DataTable.RowHighlighted, "#refs-table")
    def show_details(self) -> None:
        w = self.current
        panel = self.query_one("#refs-details", Static)
        if w is None:
            panel.update("")
            return
        text = Text()
        text.append(w.meta.title, style="bold")
        text.append(
            "\n" + "; ".join(w.meta.authors[:6]) + (" et al." if len(w.meta.authors) > 6 else ""),
            style="dim",
        )
        venue = " · ".join(
            str(p) for p in (w.meta.journal, w.meta.year, w.meta.doi and f"doi:{w.meta.doi}") if p
        )
        if venue:
            text.append("\n" + venue)
        if w.citekey:
            text.append(f"\nIn your library: {w.citekey}", style=self.tones.get("success", ""))
        if w.meta.abstract:
            text.append("\n" + _clip(w.meta.abstract, 360), style="dim")
        panel.update(text)

    @on(DataTable.RowSelected, "#refs-table")
    def selected(self) -> None:
        self.action_explore()

    # -- actions ---------------------------------------------------------------------

    def action_explore(self) -> None:
        w = self.current
        if w is not None:
            self.load([*self.trail, w])

    def action_back(self) -> None:
        if len(self.trail) > 1:
            self.load(self.trail[:-1])

    def action_switch(self) -> None:
        if self.trail:
            self.direction = "cited_by" if self.direction == "references" else "references"
            self.load(self.trail)

    def action_mark(self) -> None:
        w = self.current
        if w is None:
            return
        self.marked ^= {w.id}
        row = self.query_one("#refs-table", DataTable).cursor_row
        self.show(self.trail, self.works)
        table = self.query_one("#refs-table", DataTable)
        table.move_cursor(row=min(row + 1, len(self.works) - 1))

    def action_add(self) -> None:
        chosen = [w for w in self.works if w.id in self.marked] or (
            [self.current] if self.current else []
        )
        chosen = [w for w in chosen if not w.citekey]
        if not chosen:
            self.notify("Already in your library.", timeout=3)
            return
        self.marked.clear()
        self.app.fetch_works(chosen, self.topic, self.citekey)

    def action_open_doi(self) -> None:
        from .. import access, web

        w = self.current
        if w and (w.meta.doi or w.meta.arxiv):
            url = web.publisher_url(w.meta.doi, w.meta.arxiv)
            access.open_page(self.app.config.access, self.app.library.inbox, url)

    def action_google(self) -> None:
        from .. import access, web

        w = self.current
        if w:
            url = web.search_url(w.meta.title, w.meta.authors, w.meta.year)
            access.open_page(self.app.config.access, self.app.library.inbox, url, publisher=False)

    def refresh_marks(self) -> None:
        """After adding: the fetched works now show as in the library."""
        if self.trail:
            self.load(self.trail)


class _connections:
    """An HTTP client and this thread's own index connection."""

    def __enter__(self):
        from ..config import Config

        config = Config.load()
        self.http = Http(config.metadata.contact_email)
        self.index = Index(paths.INDEX_FILE, config.library_path)
        return self.http, self.index

    def __exit__(self, *exc) -> None:
        self.http.close()
        self.index.close()


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 1)] + "…"
