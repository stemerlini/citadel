import pytest

from citadel import record
from citadel.record import RecordError, Reference
from conftest import make_ref


def test_round_trip(tmp_path):
    ref = make_ref(extra={"zotero_key": "ABC"})
    path = tmp_path / "x.md"
    record.write_new(path, ref, "Full $E=mc^2$ text.")
    back, body = record.read(path)
    assert back == ref
    assert record.fulltext(body) == "Full $E=mc^2$ text."
    assert "> [!abstract]" in body and "## Notes" in body


def test_basename_defaults_from_metadata_and_then_sticks():
    ref = make_ref()
    assert ref.basename == "2004_StatisticalTheoryMagnetohydrodynamicTurbulence_Verma"
    assert ref.folder == f"plasma-physics/turbulence-transport/{ref.basename}"
    ref2 = Reference.from_dict({**ref.to_dict(), "title": "A changed title"})
    assert ref2.basename == ref.basename


def test_empty_fields_are_left_out_of_frontmatter():
    data = make_ref().to_dict()
    assert "volume" not in data and "rating" not in data
    assert list(data)[:2] == ["citekey", "title"]


def test_yaml_values_are_normalised():
    text = (
        "---\ncitekey: k\ntitle: T\nyear: '2001'\nadded: 2026-10-07\n"
        "authors: Solo, H.\ntags:\nrating: bad\n---\nbody"
    )
    data, body = record.split(text)
    ref = Reference.from_dict(data)
    assert (ref.year, ref.added, ref.authors, ref.tags, ref.rating) == (
        2001,
        "2026-10-07",
        ["Solo, H."],
        [],
        None,
    )
    assert body == "body"


@pytest.mark.parametrize(
    "text, message",
    [
        ("no frontmatter", "no YAML frontmatter"),
        ("---\ncitekey: k\n", "not closed"),
        ("---\n[unclosed\n---\n", "not valid YAML"),
        ("---\n- a\n---\n", "not a mapping"),
    ],
)
def test_bad_files(text, message):
    with pytest.raises(RecordError, match=message):
        record.split(text)


def test_needs_citekey_and_title():
    with pytest.raises(RecordError):
        Reference.from_dict({"title": "T"})


def test_updates_never_touch_notes(tmp_path):
    path = tmp_path / "x.md"
    record.write_new(path, make_ref(), "old text")
    path.write_text(path.read_text() + "My precious notes with $\\alpha$.\n")

    record.update_fulltext(path, "new text")
    ref = make_ref(status="read", tags=["a"])
    record.update_metadata(path, ref)

    back, body = record.read(path)
    assert back.status == "read" and back.tags == ["a"]
    assert record.fulltext(body) == "new text"
    assert body.endswith("My precious notes with $\\alpha$.\n")
    assert not list(tmp_path.glob(".*.tmp"))


def test_update_fulltext_needs_markers(tmp_path):
    path = tmp_path / "x.md"
    path.write_text(record.dump_frontmatter(make_ref()) + "no markers")
    with pytest.raises(RecordError, match="markers"):
        record.update_fulltext(path, "x")


def test_write_new_refuses_to_overwrite(tmp_path):
    path = tmp_path / "x.md"
    path.write_text("keep")
    with pytest.raises(FileExistsError):
        record.write_new(path, make_ref())
    assert path.read_text() == "keep"
