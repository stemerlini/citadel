import json

import httpx
import pytest

from citadel import sources
from citadel.sources import Http, SourceError

CROSSREF_WORK = {
    "DOI": "10.1088/1361-6501/AA5748",
    "type": "journal-article",
    "title": ["A review of recent developments in <i>schlieren</i> and shadowgraph techniques"],
    "author": [{"family": "Settles", "given": "Gary S"}, {"family": "Hargather", "given": "M J"}],
    "published-online": {"date-parts": [[2017, 2, 1]]},
    "issued": {"date-parts": [[2017]]},
    "container-title": ["Measurement Science and Technology"],
    "volume": "28",
    "issue": "4",
    "page": "042001",
    "publisher": "IOP Publishing",
    "abstract": "<jats:title>Abstract</jats:title><jats:p>Schlieren &amp; shadowgraph.</jats:p>",
    "URL": "https://doi.org/10.1088/1361-6501/aa5748",
}

ARXIV_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2101.12345v2</id>
<published>2021-01-28T00:00:00Z</published>
<title>Magnetised  compressible
 turbulence</title>
<summary> An abstract. </summary>
<author><name>James R. Beattie</name></author><author><name>Ludwig van der Berg</name></author>
<arxiv:doi>10.1093/MNRAS/abc</arxiv:doi>
</entry></feed>"""

OPENALEX = {
    "results": [
        {
            "title": "Plasma Formulary",
            "publication_year": 2019,
            "type": "book",
            "doi": "https://doi.org/10.1/NRL",
            "authorships": [{"author": {"display_name": "J. D. Huba"}}],
            "biblio": {"volume": "1", "first_page": "1", "last_page": "71"},
            "primary_location": {"source": {"display_name": "NRL"}},
            "abstract_inverted_index": {"world": [1], "Hello": [0]},
        }
    ]
}

OPENLIBRARY = {
    "ISBN:9780750333078": {
        "title": "Energy from Nuclear Fusion",
        "subtitle": "An introduction",
        "authors": [{"name": "Mark Dunlap"}],
        "publish_date": "Sept 2021",
        "publishers": [{"name": "IOP"}],
        "url": "https://openlibrary.org/x",
    }
}


def make_http(tmp_path, routes, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        for fragment, (status, body) in routes.items():
            if fragment in str(request.url):
                text = body if isinstance(body, str) else json.dumps(body)
                return httpx.Response(status, text=text)
        return httpx.Response(404)

    return Http("me@example.org", tmp_path / "cache", httpx.MockTransport(handler))


def test_crossref(tmp_path):
    http = make_http(tmp_path, {"/works/10.1088": (200, {"message": CROSSREF_WORK})})
    meta = sources.crossref(http, "10.1088/1361-6501/aa5748")
    assert meta.title == ("A review of recent developments in schlieren and shadowgraph techniques")
    assert meta.authors == ["Settles, Gary S", "Hargather, M J"]
    assert (meta.year, meta.type, meta.doi) == (2017, "article", "10.1088/1361-6501/aa5748")
    assert meta.journal == "Measurement Science and Technology"
    assert meta.abstract == "Schlieren & shadowgraph."
    assert sources.crossref(http, "10.9/missing") is None


def test_crossref_search_and_isbn(tmp_path):
    chapter = {**CROSSREF_WORK, "type": "book-chapter"}
    book = {**CROSSREF_WORK, "type": "monograph", "ISBN": ["978-0-7503-3307-8"]}
    calls = []
    http = make_http(
        tmp_path,
        {
            "query.bibliographic": (200, {"message": {"items": [CROSSREF_WORK]}}),
            "filter=isbn": (200, {"message": {"items": [chapter, book]}}),
        },
        calls,
    )
    found = sources.crossref_search(http, "schlieren review", "Settles")
    assert [f.authors[0] for f in found] == ["Settles, Gary S"]
    assert "query.author=Settles" in calls[0] and "mailto=me%40example.org" in calls[0]
    assert sources.crossref_isbn(http, "9780750333078").type == "book"


def test_arxiv(tmp_path):
    http = make_http(tmp_path, {"export.arxiv.org": (200, ARXIV_FEED)})
    meta = sources.arxiv(http, "2101.12345")
    assert meta.title == "Magnetised compressible turbulence"
    assert meta.authors == ["Beattie, James R.", "van der Berg, Ludwig"]
    assert (meta.year, meta.type, meta.doi) == (2021, "preprint", "10.1093/mnras/abc")


def test_openalex_and_openlibrary(tmp_path):
    http = make_http(
        tmp_path, {"api.openalex.org": (200, OPENALEX), "openlibrary.org": (200, OPENLIBRARY)}
    )
    work = sources.openalex_search(http, "plasma formulary")[0]
    assert (work.authors, work.pages, work.doi, work.abstract) == (
        ["Huba, J. D."],
        "1-71",
        "10.1/nrl",
        "Hello world",
    )
    book = sources.openlibrary(http, "9780750333078")
    assert book.title == "Energy from Nuclear Fusion: An introduction"
    assert (book.authors, book.year, book.type) == (["Dunlap, Mark"], 2021, "book")
    assert sources.openlibrary(http, "9780000000002") is None


def test_responses_are_cached(tmp_path):
    calls = []
    http = make_http(tmp_path, {"/works/": (200, {"message": CROSSREF_WORK})}, calls)
    sources.crossref(http, "10.1/a")
    sources.crossref(http, "10.1/a")
    assert len(calls) == 1


def test_errors_are_not_cached(tmp_path):
    calls = []
    http = make_http(tmp_path, {"/works/": (503, "busy")}, calls)
    for _ in range(2):
        with pytest.raises(SourceError, match="HTTP 503"):
            sources.crossref(http, "10.1/a")
    assert len(calls) == 2


def test_user_agent_includes_contact(tmp_path):
    seen = {}

    def handler(request):
        seen["agent"] = request.headers["user-agent"]
        return httpx.Response(404)

    Http("me@example.org", tmp_path, httpx.MockTransport(handler)).get("https://x.org/a")
    assert "mailto:me@example.org" in seen["agent"]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Mahendra K. Verma", "Verma, Mahendra K."),
        ("Ludwig van der Berg", "van der Berg, Ludwig"),
        ("NRL", "NRL"),
        ("Settles, Gary", "Settles, Gary"),
    ],
)
def test_comma_name(name, expected):
    assert sources.comma_name(name) == expected
