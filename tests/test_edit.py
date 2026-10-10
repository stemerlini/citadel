import pytest

from citadel import edit, record
from citadel.edit import EditError
from citadel.index import Index
from conftest import file_ref, make_ref


@pytest.fixture
def index(tmp_path):
    root = tmp_path / "lib"
    folder = file_ref(root, make_ref())
    (folder / f"{folder.name}.md").write_text(
        (folder / f"{folder.name}.md").read_text() + "My notes.\n"
    )
    index = Index(tmp_path / "index.db", root)
    index.reindex()
    yield index
    index.close()


def test_update_fields_keeps_notes(index):
    ref = edit.update(index, "verma2004statistical", tags=["mhd", "review"], rating=4)
    assert ref.tags == ["mhd", "review"]
    path = edit.record_path(index, "verma2004statistical")
    assert path.read_text().endswith("My notes.\n")
    assert index.get("verma2004statistical")["tags"] == ["mhd", "review"]


def test_cycle_status(index):
    assert edit.cycle_status(index, "verma2004statistical") == "reading"
    assert edit.cycle_status(index, "verma2004statistical") == "read"
    assert edit.cycle_status(index, "verma2004statistical") == "unread"


def test_move_to_another_topic(index):
    old = edit.record_path(index, "verma2004statistical").parent
    edit.update(
        index, "verma2004statistical", topics={"plasma-physics/mhd"}, topic="plasma-physics/mhd"
    )
    new = edit.record_path(index, "verma2004statistical")
    assert not old.exists()
    assert new.parent.parent == index.root / "plasma-physics/mhd"
    assert new.parent.name == old.name  # names never change
    ref, _ = record.read(new)
    assert ref.topic == "plasma-physics/mhd"
    assert (new.parent / f"{new.parent.name}.pdf").exists()
    assert index.get("verma2004statistical")["topic"] == "plasma-physics/mhd"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"status": "done"}, "status must be"),
        ({"type": "novel"}, "type must be"),
        ({"rating": 9}, "rating must be"),
        ({"basename": "x"}, "cannot edit basename"),
        ({"topic": "nowhere"}, "unknown topic"),
    ],
)
def test_invalid_changes(index, changes, message):
    with pytest.raises(EditError, match=message):
        edit.update(index, "verma2004statistical", topics={"optics"}, **changes)


def test_unknown_reference(index):
    with pytest.raises(EditError, match="no reference"):
        edit.load(index, "nobody2000x")
