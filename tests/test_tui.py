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
    assert set(wide) == {"status", "year", "title", "author", "type", "topic", "files"}
    narrow = table_layout(70, show_topic=True)
    assert "topic" not in narrow and "type" not in narrow and narrow["title"] >= 24
