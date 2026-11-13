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
        index.reindex()  # the app opens on what the index already knows
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
        app.index.reindex = None  # the UI thread never rereads the whole library
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
    assert set(wide) == {"status", "library", "year", "title", "author", "type", "topic", "md"}
    narrow = table_layout(70, show_topic=True)
    assert "topic" not in narrow and "type" not in narrow and narrow["title"] >= 24


async def test_review_key_reads_the_inbox_first(make_app, monkeypatch):
    from citadel.ingest import Ingester, Result
    from citadel.tui.screens import ReviewScreen

    app, _ = make_app()
    read = []

    def fake_process(self, dry_run=False, settle=None):
        for path in self.library.inbox.iterdir():
            read.append(path.name)
            path.unlink()
        return [Result(name, "review", "ready to file: accept it") for name in read]

    monkeypatch.setattr(Ingester, "process_inbox", fake_process)
    async with app.run_test(size=(140, 40)) as pilot:
        (app.library.inbox / "new.pdf").write_bytes(b"%PDF")
        await pilot.press("i")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert read == ["new.pdf"]
        assert isinstance(app.screen, ReviewScreen)


async def test_review_covers_every_library(tmp_path, monkeypatch):
    """i reads every library's inbox and review queue, and settles an item in its own
    library (found with the SES library's papers waiting while Personal was empty)."""
    from citadel.ingest import Ingester, Proposal, Result
    from citadel.tui.screens import ReviewScreen

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    personal, ses = Library(tmp_path / "p", "Personal"), Library(tmp_path / "s", "SES")
    personal.init()
    ses.init()
    (ses.review / "waiting.pdf").write_bytes(b"%PDF")
    Proposal("waiting.pdf", "ready to file: accept it", "a" * 64).save(ses.review)
    read, accepted = [], []

    def fake_process(self, dry_run=False, settle=None):
        for path in self.library.inbox.iterdir():
            read.append((self.library.name, path.name))
            path.unlink()
        return [Result(name, "review", "") for _, name in read]

    def fake_accept(self, name, **kw):
        accepted.append((self.library.name, name))
        return Result(name, "review")

    monkeypatch.setattr(Ingester, "process_inbox", fake_process)
    monkeypatch.setattr(Ingester, "accept", fake_accept)
    config = Config(
        library_entries=[
            {"name": "Personal", "path": str(personal.root)},
            {"name": "SES", "path": str(ses.root)},
        ]
    )
    app = Citadel(config=config, index=Index(tmp_path / "index.db", [personal, ses]))
    async with app.run_test(size=(140, 40)) as pilot:
        (ses.inbox / "new.pdf").write_bytes(b"%PDF")
        await pilot.press("i")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert read == [("SES", "new.pdf")]
        assert isinstance(app.screen, ReviewScreen)
        assert [(i.library, i.file) for i in app.screen.items] == [("SES", "waiting.pdf")]
        await pilot.press("a")
        await app.workers.wait_for_complete()
        assert accepted == [("SES", "waiting.pdf")]


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
                app.topic_paths,
                lambda action, names, **kw: calls.append((action, names, kw)),
            )
        )
        await pilot.pause()
        await pilot.press("v", "v")  # marks paper0 and paper1 (the cursor moves down)
        assert app.screen.marked == {("", "paper0.pdf"), ("", "paper1.pdf")}
        await pilot.press("t")
        await pilot.pause()
        await pilot.press(*"aero-optics", "enter")
        await pilot.pause()
        assert calls[-1] == (
            "accept",
            ["paper0.pdf", "paper1.pdf"],
            {"library": "", "topic": "optics/aero-optics"},
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
        for _ in range(3):  # the start-up reindex, then its duplicate check
            await app.workers.wait_for_complete()
            await pilot.pause()
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
        app.fetch_works = lambda works, topic, parent, library="": fetched.append(
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


async def test_w_and_g_open_the_paper_online(make_app, monkeypatch):
    from citadel import web

    opened = []
    monkeypatch.setattr(web, "open_url", lambda url: opened.append(url) or True)
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("w")  # harding2009observation: a DOI, no PDF
        assert opened[-1] == "https://doi.org/10.1/x"
        await pilot.press("g")
        assert opened[-1].startswith("https://www.google.com/search?q=")
        assert "No PDF yet" in str(app.query_one("#details").render())


async def test_first_run_asks_where_and_makes_citadel_personal(tmp_path, config_file, monkeypatch):
    from textual.widgets import Input

    from citadel.tui.settings import LibraryForm

    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    (tmp_path / "OneDrive").mkdir()
    (tmp_path / "OneDrive" / "notes.txt").write_text("synced")
    app = Citadel(index=Index(tmp_path / "index.db", tmp_path / "none"), setup=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        form = app.screen
        assert isinstance(form, LibraryForm)
        form.query_one("#lib-location", Input).value = str(tmp_path / "OneDrive")
        form.submit()
        await pilot.pause()
        assert (app.library.name, app.library.tag) == ("personal", "p")
        assert (tmp_path / "OneDrive" / "Citadel-personal" / "_inbox").is_dir()
    assert "Citadel-personal" in config_file.read_text()


async def test_settings_add_a_work_library_and_the_sign_in(
    make_app, tmp_path, config_file, monkeypatch
):
    from textual.widgets import Input

    from citadel.tui.settings import AccountForm, AccountsScreen, LibraryForm, SettingsScreen

    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "x").write_text("")
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("S", "a")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, LibraryForm)
        form.query_one("#lib-location", Input).value = str(tmp_path / "work")
        form.query_one("#lib-tag", Input).value = "Library"  # "l": the first one's letter
        form.submit()
        await pilot.pause()
        assert app.screen is form  # refused
        form.query_one("#lib-tag", Input).value = "Work"
        form.submit()
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        assert [(lib.name, lib.tag) for lib in app.libraries][1] == ("work", "w")
        assert (tmp_path / "work" / "Citadel-work" / "_inbox").is_dir()
        await pilot.press("escape")
        await pilot.click("#signin")  # no accounts: the list opens on a new one
        await pilot.pause()
        assert isinstance(app.screen, AccountForm)
        app.screen.query_one("#account-name", Input).value = "imperial"
        app.screen.query_one("#account-login_url", Input).value = "https://library.example/"
        app.screen.action_save()
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)
        assert [a["name"] for a in app.config.access.all()] == ["imperial"]


async def test_an_old_library_folder_is_renamed_citadel_tag(tmp_path, config_file, monkeypatch):
    from citadel.config import save_libraries
    from citadel.tui.screens import ConfirmScreen

    monkeypatch.delenv("CITADEL_LIBRARY", raising=False)
    old = tmp_path / "OneDrive-Personal" / "Citadel"
    Library(old).init()
    file_ref(old, make_ref(), "The Kolmogorov cascade in a plasma.")
    save_libraries([{"path": str(old)}])
    app = Citadel(index=Index(tmp_path / "index.db", Config.load().libraries()), setup=False)
    app.config = Config.load()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not old.exists() and (old.parent / "Citadel-personal" / "topics.toml").is_file()
        assert (app.library.name, app.library.tag) == ("personal", "p")
        assert [r["citekey"] for r in app.rows] == ["verma2004statistical"]
    assert "Citadel-personal" in config_file.read_text()


async def test_opens_at_once_and_catches_up_with_the_library_in_the_background(make_app, tmp_path):
    app, _ = make_app()
    file_ref(tmp_path / "lib", make_ref(citekey="late2024arrival", title="A late arrival"))
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "late2024arrival" in {row["citekey"] for row in app.rows}


async def test_references_still_loading_are_greyed_out_until_read(make_app, monkeypatch):
    import threading

    from citadel.index import Index
    from citadel.tui.app import LOADING_STYLE

    release, real = threading.Event(), Index.reindex

    def slow(self, full=False, progress=None):  # a OneDrive that takes its time
        if threading.current_thread() is not threading.main_thread():
            release.wait(10)
        return real(self, full, progress)

    monkeypatch.setattr(Index, "reindex", slow)
    app, _ = make_app()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.8)
        table = app.query_one("#refs")
        title = list(app._layout).index("title")
        assert app.loading and table.get_row_at(0)[title].style == LOADING_STYLE
        assert app.check_action("open_pdf", ()) is None  # disabled while loading
        assert "Not available yet" in str(app.query_one("#details").render())
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.loading and table.get_row_at(0)[title].style != LOADING_STYLE
        assert app.check_action("open_pdf", ()) is True


def test_a_page_rendered_twice_at_once_does_not_crash(tmp_path, monkeypatch):
    import threading

    from citadel.tui import pages

    monkeypatch.setattr(pages.paths, "CACHE_DIR", tmp_path / "cache")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF")
    both = threading.Barrier(2)

    def pdftoppm(argv, **kw):  # both renders write their file before either moves it
        with open(argv[-1] + ".png", "wb") as png:
            png.write(b"png")
        both.wait(timeout=5)

    monkeypatch.setattr(pages.subprocess, "run", pdftoppm)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(pages.page_image(pdf, 1))) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2 and all(r and r.exists() for r in results)
