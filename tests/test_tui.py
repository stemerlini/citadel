"""The app, driven headlessly against a small temporary library."""

import pytest

from citadel import record
from citadel.config import Config
from citadel.index import Index
from citadel.library import Library
from citadel.tui import app as app_module
from citadel.tui.app import Citadel, table_layout
from citadel.tui.screens import EditScreen, PreviewScreen, TopicPicker
from conftest import file_ref, make_ref


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    root = tmp_path / "lib"
    Library(root).init()
    file_ref(root, make_ref(), "The Kolmogorov cascade in a plasma.")
    file_ref(
        root,
        make_ref(
            citekey="harding2009observation",
            title="Observation of a Kelvin-Helmholtz instability",
            authors=["Harding, E. C."],
            year=2009,
            topic="plasma-physics/instabilities",
            doi="10.1/x",
            abstract="",
            tags=[],
        ),
        pdf=False,
    )
    copied = []
    monkeypatch.setattr(app_module, "copy_text", lambda app, text: copied.append(text))
    config = Config(library=str(root))

    def build():
        index = Index(tmp_path / "index.db", root)
        return Citadel(config=config, index=index), copied

    return build


async def test_lists_references_and_views(make_app):
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert [r["citekey"] for r in app.rows] == [
            "harding2009observation",
            "verma2004statistical",
        ]
        assert "2 references" in str(app.query_one("#stats").render())
        app.view = ("topic", "plasma-physics/instabilities")
        app.refresh_table()
        assert [r["citekey"] for r in app.rows] == ["harding2009observation"]
        app.view = ("view", "no-md")
        app.refresh_table()
        assert [r["citekey"] for r in app.rows] == []  # the PDF one already has text


async def test_search_and_escape(make_app):
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("slash", *"kolmogorov")
        await pilot.pause()
        assert [r["citekey"] for r in app.rows] == ["verma2004statistical"]
        assert "kolmogorov" in app.snippets["verma2004statistical"].lower()
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.rows) == 2 and not app.query_one("#search").display


async def test_copy_and_status(make_app):
    app, copied = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("down")  # verma (second row)
        await pilot.press("b")
        assert copied[-1].startswith("@article{verma2004statistical,")
        await pilot.press("c")
        assert copied[-1].startswith("Verma, M. K. (2004).")
        await pilot.press("k")
        assert copied[-1] == "verma2004statistical"
        await pilot.press("r")
        await pilot.pause()
        assert app.index.get("verma2004statistical")["status"] == "reading"
        assert app.current_key() == "verma2004statistical"  # the cursor stayed put


async def test_preview_opens_with_details(make_app):
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("down", "space")
        await pilot.pause()
        assert isinstance(app.screen, PreviewScreen)
        assert app.screen.ref.citekey == "verma2004statistical"
        await pilot.press("tab")
        assert app.screen.showing_text
        await pilot.press("space")
        assert not isinstance(app.screen, PreviewScreen)


async def test_move_to_topic(make_app):
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("down", "t")
        await pilot.pause()
        assert isinstance(app.screen, TopicPicker)
        await pilot.press(*"aero-optics", "enter")
        await pilot.pause()
        assert app.index.get("verma2004statistical")["topic"] == "optics/aero-optics"


async def test_edit_saves_changes(make_app):
    app, _ = make_app()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("down", "e")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        tags = app.screen.query_one("#edit-tags")
        tags.value = "mhd, review"
        await pilot.press("ctrl+s")
        await pilot.pause()
        row = app.index.get("verma2004statistical")
        assert row["tags"] == ["mhd", "review"]
        ref, _ = record.read(
            app.index.root / row["folder"] / f"{row['folder'].rsplit('/', 1)[-1]}.md"
        )
        assert ref.tags == ["mhd", "review"]


def test_table_layout_drops_columns_when_narrow():
    wide = table_layout(160, show_topic=True)
    assert set(wide) == {"status", "year", "title", "author", "type", "topic", "md"}
    narrow = table_layout(70, show_topic=True)
    assert "topic" not in narrow and "type" not in narrow and narrow["title"] >= 24


async def test_review_marks_and_batch_topic(make_app):
    from citadel.ingest import Proposal
    from citadel.tui.screens import ReviewScreen

    app, _ = make_app()
    calls = []
    items = [
        Proposal(f"paper{i}.pdf", "topic confidence 0.30", "x" * 64, topic="optics")
        for i in range(3)
    ]
    async with app.run_test(size=(140, 40)) as pilot:
        app.push_screen(
            ReviewScreen(
                items,
                app.topic_paths(),
                lambda action, names, **kw: calls.append((action, names, kw)),
            )
        )
        await pilot.pause()
        await pilot.press("v", "v")  # marks paper0 and paper1 (the cursor moves down)
        assert app.screen.marked == {"paper0.pdf", "paper1.pdf"}
        await pilot.press("t")
        await pilot.pause()
        await pilot.press(*"aero-optics", "enter")
        await pilot.pause()
        assert calls[-1] == (
            "accept",
            ["paper0.pdf", "paper1.pdf"],
            {"topic": "optics/aero-optics"},
        )
        await pilot.press("R")
        assert calls[-1] == ("retry", [""], {})
        await pilot.press("V")
        assert len(app.screen.marked) == 3


async def test_markdown_column_shows_each_state(make_app):
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        rows = {r["citekey"]: r for r in app.rows}
        assert app.markdown_state(rows["verma2004statistical"]) == "done"
        assert app.markdown_state(rows["harding2009observation"]) == "no-pdf"
        waiting = dict(rows["verma2004statistical"], has_fulltext=0)
        assert app.markdown_state(waiting) == "waiting"
        app.converting_key = "verma2004statistical"
        assert app.markdown_state(waiting) == "converting"
        assert app.markdown_mark(waiting).plain == " ⟳"


async def test_duplicates_screen_keep_and_ignore(make_app):
    from citadel.duplicates import Group, Member
    from citadel.tui.screens import ConfirmScreen, DuplicatesScreen

    app, _ = make_app()
    group = Group(
        ["identical file"],
        [
            Member("reference", "a2020x", "A paper", 2020, "Smith", "optics/a", True, True, True),
            Member("review", "a copy.pdf", "A paper", None, "", "_review/a copy.pdf", True),
        ],
    )
    calls = []
    async with app.run_test(size=(140, 40)) as pilot:
        app.push_screen(DuplicatesScreen([group], lambda *args: calls.append(args)))
        await pilot.pause()
        await pilot.press("down", "enter")  # keep the copy in review
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "notes or Markdown" in str(app.screen.question)  # warns about losing them
        await pilot.press("y")
        assert calls[-1] == ("keep", group, "review:a copy.pdf")
        await pilot.press("n", "y")
        assert calls[-1] == ("ignore", group, "")


async def test_review_edit_sends_corrections(make_app):
    from citadel.ingest import Proposal
    from citadel.tui.screens import EditScreen, ReviewScreen

    app, _ = make_app()
    calls = []
    item = Proposal(
        "slides.pdf",
        "metadata confidence 0.30",
        "x" * 64,
        metadata={"title": "Wrong title", "authors": ["Hall, G."], "year": 1994},
    )
    async with app.run_test(size=(140, 50)) as pilot:
        app.push_screen(
            ReviewScreen(
                [item],
                app.topic_paths(),
                lambda action, names, **kw: calls.append((action, names, kw)),
            )
        )
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EditScreen)
        app.screen.query_one("#edit-title").value = "Detector physics"
        app.screen.query_one("#edit-authors").value = "Bindi, Marcello"
        app.screen.query_one("#edit-year").value = "2021"
        await pilot.press("ctrl+s")
        await pilot.pause()
        action, names, changes = calls[-1]
        assert (action, names) == ("edit", ["slides.pdf"])
        assert changes["title"] == "Detector physics" and changes["year"] == 2021
        assert changes["authors"] == ["Bindi, Marcello"] and "tags" not in changes


async def test_reference_tree_explore_back_and_add(make_app, monkeypatch):
    from contextlib import contextmanager

    from citadel.graph import Work
    from citadel.sources import Metadata
    from citadel.tui import references as refs_module
    from citadel.tui.references import ReferencesScreen

    def w(id, title, refs=(), citekey="", pdf=""):
        work = Work(
            id,
            Metadata(title=title, authors=["Smith, A."], year=2001),
            pdf_url=pdf,
            references=list(refs),
        )
        work.citekey = citekey
        return work

    tree = {
        "W1": w("W1", "Root paper", ("W2", "W3"), citekey="verma2004statistical"),
        "W2": w("W2", "First reference", ("W4",), pdf="https://x/a.pdf"),
        "W3": w("W3", "Second reference"),
        "W4": w("W4", "Deeper reference"),
    }

    class FakeGraph:
        def __init__(self, http, index):
            pass

        def work_for(self, citekey):
            return tree["W1"]

        def references(self, work):
            return [tree[r] for r in work.references]

        def cited_by(self, work, limit=50):
            return [tree["W4"]]

    @contextmanager
    def no_network():
        yield None, None

    monkeypatch.setattr(refs_module, "Graph", FakeGraph)
    monkeypatch.setattr(refs_module, "_connections", no_network)
    app, _ = make_app()
    fetched = []
    async with app.run_test(size=(140, 44)) as pilot:
        app.fetch_works = lambda works, topic, parent: fetched.append(
            ([x.id for x in works], topic, parent)
        )
        await pilot.press("down", "f")  # verma2004statistical is the second row
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, ReferencesScreen)
        assert [x.id for x in screen.works] == ["W2", "W3"]
        await pilot.press("right")
        await pilot.pause(0.3)
        assert [x.id for x in screen.trail] == ["W1", "W2"]
        assert [x.id for x in screen.works] == ["W4"]
        await pilot.press("left")
        await pilot.pause(0.3)
        assert [x.id for x in screen.trail] == ["W1"]
        await pilot.press("v", "v", "a")  # mark both references, add them
        assert fetched == [
            (["W2", "W3"], "plasma-physics/turbulence-transport", "verma2004statistical")
        ]
        await pilot.press("tab")
        await pilot.pause(0.3)
        assert screen.direction == "cited_by" and [x.id for x in screen.works] == ["W4"]


async def test_type_views(make_app, tmp_path):
    from conftest import file_ref, make_ref

    app, _ = make_app()
    file_ref(
        app.library.root,
        make_ref(
            citekey="minafra2018tests",
            title="Tests of Ultra Fast Silicon Detectors",
            type="slides",
            topic="space-engineering/instruments",
            doi="",
        ),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        views = [n.data for n in app.query_one("#topics").root.children if n.data]
        assert ("type", "slides") in views and ("type", "book") not in views  # none yet
        app.view = ("type", "slides")
        app.refresh_table()
        assert [r["citekey"] for r in app.rows] == ["minafra2018tests"]
        assert "SLIDES & TALKS" in str(app.query_one("#list-title").render())
