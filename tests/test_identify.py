from citadel import identify
from citadel.pdf import PdfText
from citadel.sources import Metadata

PAGE = """A review of recent developments in schlieren and shadowgraph techniques
Gary S Settles1,3 and Michael J Hargather2
Measurement Science and Technology 28 (2017) 042001
doi 10.1088/1361-6501/aa5748
Recent developments in schlieren and shadowgraphy were reviewed in [3] (2010)."""

REAL = Metadata(
    title="A review of recent developments in schlieren and shadowgraph techniques",
    authors=["Settles, Gary S"],
    year=2017,
    doi="10.1088/1361-6501/aa5748",
)
EARLIER = Metadata(
    title="Recent Developments in Schlieren and Shadowgraphy",
    authors=["Hargather, Michael"],
    year=2010,
    doi="10.2514/6.2010-4206",
)


def page(**overrides):
    values = dict(
        first_pages=PAGE, has_text=True, title_candidates=[REAL.title], info={"Pages": "20"}
    )
    values.update(overrides)
    return PdfText(**values)


class FakeHttp:
    """Stands in for sources.Http; identify only passes it through to sources."""


def patch_sources(monkeypatch, crossref=None, search=(), isbn=None, openalex=(), arxiv=None):
    from citadel import sources

    monkeypatch.setattr(sources, "crossref", lambda http, doi: (crossref or {}).get(doi))
    monkeypatch.setattr(sources, "crossref_search", lambda http, q, author="": list(search))
    monkeypatch.setattr(sources, "crossref_isbn", lambda http, i: isbn)
    monkeypatch.setattr(sources, "openlibrary", lambda http, i: None)
    monkeypatch.setattr(sources, "openalex_search", lambda http, q: list(openalex))
    monkeypatch.setattr(sources, "arxiv", lambda http, a: arxiv)


def test_doi_on_the_page_wins(monkeypatch):
    patch_sources(monkeypatch, crossref={REAL.doi: REAL})
    found = identify.identify_text(page(), FakeHttp())
    assert found.method == "doi" and found.metadata is REAL
    assert found.confidence >= 0.95


def test_shorter_earlier_paper_does_not_beat_the_real_one(monkeypatch):
    patch_sources(monkeypatch, search=[EARLIER, REAL])
    found = identify.identify_text(page(first_pages=PAGE.replace("doi 10.1088", "")), FakeHttp())
    assert found.metadata is REAL


def test_two_editions_are_ambiguous(monkeypatch):
    first = Metadata(
        title="Experimental Astrophysics",
        authors=["Drake, R. Paul"],
        year=2006,
        doi="10.1007/3-540-29315-9_10",
    )
    second = Metadata(
        title="Experimental Astrophysics",
        authors=["Drake, R. Paul"],
        year=2018,
        doi="10.1007/978-3-319-67711-8_12",
    )
    patch_sources(monkeypatch, search=[first, second])
    pdf = page(
        first_pages="Chapter 12\nExperimental Astrophysics\nR. Paul Drake\n" + "x " * 50,
        title_candidates=["Experimental Astrophysics"],
    )
    found = identify.identify_text(pdf, FakeHttp())
    assert found.confidence == identify.AMBIGUOUS_CAP
    assert any("ambiguous" in e for e in found.evidence)


def test_same_work_found_twice_is_not_ambiguous(monkeypatch):
    patch_sources(monkeypatch, search=[REAL, Metadata(**{**REAL.__dict__})])
    found = identify.identify_text(page(first_pages=PAGE.replace("doi 10.1088", "")), FakeHttp())
    assert found.confidence > identify.AMBIGUOUS_CAP


def test_isbn_book_uses_copyright_page(monkeypatch):
    book = Metadata(
        title="Energy from Nuclear Fusion",
        authors=["Dunlap, Mark"],
        year=2021,
        type="book",
        isbn="9780750333078",
    )
    patch_sources(monkeypatch, isbn=book)
    pdf = page(
        first_pages="Energy from Nuclear Fusion\n" + "x " * 30,
        title_candidates=["Energy from Nuclear Fusion"],
        later_pages="Mark Dunlap © 2021\nISBN 978-0-7503-3307-8 (ebook)",
    )
    found = identify.identify_text(pdf, FakeHttp())
    assert found.method == "isbn" and found.confidence >= 0.9


def test_nothing_found_falls_back_to_the_pdf(monkeypatch):
    patch_sources(monkeypatch)
    pdf = page(
        first_pages="Some internal report " + "x " * 30,
        title_candidates=["Some internal report"],
        info={"Author": "Jane Doe", "CreationDate": "Mon Nov 22 2010"},
    )
    found = identify.identify_text(pdf, FakeHttp())
    assert (found.method, found.confidence) == ("none", 0.0)
    assert found.metadata.title == "Some internal report"
    assert found.metadata.authors == ["Doe, Jane"] and found.metadata.year == 2010
    assert any("not found" in e for e in found.evidence)


def test_title_in_text_needs_phrases():
    text = "radiative cooling ... processes of ... in astrophysics"
    assert identify.title_in_text("Radiative Processes in Astrophysics", text) < 0.6
    assert (
        identify.title_in_text(
            "Radiative Processes in Astrophysics", "the radiative processes in astrophysics book"
        )
        == 1.0
    )


def test_author_in_text_tolerates_affiliation_marks():
    assert identify.author_in_text("Settles, Gary", "Gary S Settles1,3 and")
    assert identify.author_in_text("Schäfer, J.", "J. Schafer*")
    assert not identify.author_in_text("Settle, G.", "Gary S Settles")
    assert identify.author_in_text("van der Merwe, J.", "J. van der Merwe")
