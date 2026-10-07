import pytest

from citadel import topics
from citadel.topics import TopicError


def test_default_tree_is_valid():
    tree = topics.load()
    paths = {t.path for t in tree}
    assert "plasma-physics/hedp/pulsed-power" in paths
    assert "plasma-physics/diagnostics/refractive" in paths
    assert all(t.depth <= 3 for t in tree)
    assert all(t.description for t in tree)


def test_topic_properties():
    topic = topics.Topic("plasma-physics/hedp/icf")
    assert (topic.parent, topic.depth, topic.name) == ("plasma-physics/hedp", 3, "icf")
    assert topics.Topic("optics").parent is None


@pytest.mark.parametrize(
    "text, message",
    [
        ('["a/b/c/d"]\n["a"]\n["a/b"]\n["a/b/c"]', "deeper than 3"),
        ('["Plasma Physics"]', "kebab-case"),
        ('["a/b"]', "parent a is not listed"),
        ('["a"]\nkeywords = "x"', "keywords must be a list"),
        ("a = 1", "must be a table"),
        ('["a"', "not valid TOML"),
    ],
)
def test_invalid_trees_are_rejected(text, message):
    with pytest.raises(TopicError, match=message):
        topics.parse(text)
