import pytest

from citadel import identify, talks
from citadel.pdf import PdfText, _page_sizes


def deck(
    text="Tests of Ultra Fast Silicon Detectors\nNicola Minafra\nUniversity of Kansas",
    sizes=((960, 540),) * 3,
    words=60.0,
    pages=18,
    **info,
):
    info = {
        "Creator": "Microsoft® PowerPoint® 2016",
        "Title": "Tests of Ultra Fast Silicon Detectors",
        "Author": "Nicola Minafra",
        "CreationDate": "Tue Feb 20 2018",
        **info,
    }
    return PdfText(
        first_pages=text,
        has_text=True,
        info=info,
        pages=pages,
        page_sizes=list(sizes),
        words_per_page=words,
        title_candidates=[text.splitlines()[0]],
    )


@pytest.mark.parametrize(
    "sizes, words, pages, creator, layout",
    [
        (((960, 540),) * 3, 60, 18, "Microsoft PowerPoint", "slides"),  # 16:9 deck
        (((364, 273),) * 3, 300, 30, "LaTeX with Beamer class", "slides"),  # 4:3 Beamer, wordy
        (((842, 595),) * 3, 90, 12, "", "slides"),  # landscape, sparse
        (((842, 595),) * 3, 700, 12, "LaTeX", "document"),  # landscape paper
        (((595, 842),) * 3, 60, 18, "Microsoft PowerPoint", "document"),  # portrait handout
        (((2384, 3370),), 400, 1, "Adobe Illustrator", "poster"),  # A0 poster
        (((595, 842),), 400, 1, "", "document"),  # one A4 page
        ((), 0, 0, "", "document"),
    ],
)
def test_layout(sizes, words, pages, creator, layout):
    pdf = PdfText(
        pages=pages, page_sizes=list(sizes), words_per_page=words, info={"Creator": creator}
    )
    assert pdf.layout == layout


def test_page_sizes_from_pdfinfo():
    info = {
        "Page    1 size": "960 x 540 pts",
        "Page    2 size": "960 x 540 pts (A4)",
        "Pages": "18",
    }
    assert _page_sizes(info) == [(960.0, 540.0), (960.0, 540.0)]
    assert _page_sizes({"Page size": "595.276 x 841.89 pts (A4)"}) == [(595.276, 841.89)]


def test_a_confirmed_deck():
    meta, confidence, evidence = talks.describe(deck())
    assert (meta.title, meta.authors, meta.year, meta.type) == (
        "Tests of Ultra Fast Silicon Detectors",
        ["Minafra, Nicola"],
        2018,
        "slides",
    )
    assert confidence == talks.CONFIRMED and "author on the slide" in evidence[0]


def test_author_not_on_the_slide_lowers_confidence():
    _, confidence, _ = talks.describe(deck(Author="Someone Else"))
    assert confidence == talks.TITLE_ONLY


def test_junk_embedded_title_falls_back_to_the_slide():
    meta, _, _ = talks.describe(deck(Title="Presentation1"))
    assert meta.title == "Tests of Ultra Fast Silicon Detectors"


def test_decks_are_never_searched_for():
    class NoNetwork:
        def __getattr__(self, name):
            raise AssertionError("a deck must not be looked up")

    found = identify.identify_text(deck(), NoNetwork())
    assert found.method == "slides" and found.metadata.type == "slides"
    assert found.confidence == talks.CONFIRMED
