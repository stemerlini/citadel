import json

import httpx
import pytest

from citadel.graph import Graph, Work
from citadel.index import Index
from citadel.sources import Http, Metadata
from conftest import file_ref, make_ref


def oa(id, title, year, doi="", first="Smith", pdf="", arxiv="", refs=()):
    return {
        "id": f"https://openalex.org/{id}",
        "title": title,
        "display_name": title,
        "publication_year": year,
        "doi": f"https://doi.org/{doi}" if doi else None,
        "authorships": [{"author": {"display_name": f"Ann {first}"}}],
        "best_oa_location": {"pdf_url": pdf} if pdf else None,
        "ids": {"arxiv": f"https://arxiv.org/abs/{arxiv}"} if arxiv else {},
        "cited_by_count": 7,
        "type": "article",
        "referenced_works": [f"https://openalex.org/{r}" for r in refs],
    }


ROOT = oa(
    "W1",
    "Statistical theory of magnetohydrodynamic turbulence",
    2004,
    doi="10.1016/j.physrep.2004.07.007",
    first="Verma",
    refs=("W3", "W2", "W4"),
)
WORKS = {
    "W2": oa("W2", "A paper with an open PDF", 2001, doi="10.1/open", pdf="https://x.org/a.pdf"),
    "W3": oa(
        "W3", "Observation of a Kelvin-Helmholtz instability", 2009, doi="10.1/kh", first="Harding"
    ),
    "W4": oa("W4", "A preprint only", 2020, arxiv="2001.00001"),
}


@pytest.fixture
def graph(tmp_path):
    root = tmp_path / "lib"
    file_ref(root, make_ref())
    file_ref(
        root,
        make_ref(
            citekey="harding2009observation",
            doi="",
            title="Observation of a Kelvin-Helmholtz instability",
            authors=["Harding, E. C."],
            year=2009,
            topic="plasma-physics/instabilities",
        ),
        pdf=False,
    )
    index = Index(tmp_path / "index.db", root)
    index.reindex()
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append(url)
        if "/works/doi:10.1016" in url:
            return httpx.Response(200, json=ROOT)
        if "filter=openalex_id" in url:
            wanted = (
                url.split("openalex_id:")[1].split("&")[0].split("%7C" if "%7C" in url else "|")
            )
            return httpx.Response(200, json={"results": [WORKS[w] for w in wanted if w in WORKS]})
        if "filter=cites:W1" in url:
            return httpx.Response(200, json={"results": [WORKS["W4"]]})
        if "unpaywall" in url:
            return httpx.Response(200, json={"best_oa_location": None})
        if url.endswith("a.pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 real")
        if "arxiv.org/pdf" in url:
            return httpx.Response(200, content=b"<html>landing</html>")
        return httpx.Response(404)

    http = Http("me@example.org", tmp_path / "cache", httpx.MockTransport(handler))
    yield Graph(http, index), calls
    index.close()


def test_references_in_order_and_marked(graph):
    graph, _ = graph
    root = graph.work_for("verma2004statistical")
    assert root.citekey == "verma2004statistical" and root.references == ["W3", "W2", "W4"]
    works = graph.references(root)
    assert [w.id for w in works] == ["W3", "W2", "W4"]  # the paper's own order
    assert works[0].citekey == "harding2009observation"  # matched by title, year, author
    assert works[1].pdf_url == "https://x.org/a.pdf" and not works[1].citekey
    assert works[2].meta.arxiv == "2001.00001"


def test_cited_by(graph):
    graph, calls = graph
    root = graph.work_for("verma2004statistical")
    assert [w.id for w in graph.cited_by(root)] == ["W4"]
    assert "sort=cited_by_count:desc" in calls[-1]


def test_downloads_only_real_pdfs(graph, tmp_path):
    graph, _ = graph
    works = graph.references(graph.work_for("verma2004statistical"))
    target = tmp_path / "a.pdf"
    assert graph.download(works[1], target) and target.read_bytes().startswith(b"%PDF")
    landing = tmp_path / "b.pdf"
    assert not graph.download(works[2], landing) and not landing.exists()  # HTML, not PDF
    assert not graph.download(
        Work("W9", Metadata(title="Closed", doi="10.1/closed")), tmp_path / "c.pdf"
    )


def test_unknown_reference(graph):
    graph, _ = graph
    with pytest.raises(LookupError):
        graph.work_for("nobody2000x")


def test_as_dict_for_agents(graph):
    graph, _ = graph
    work = graph.references(graph.work_for("verma2004statistical"))[0]
    data = work.as_dict()
    assert data["in_library"] == "harding2009observation" and data["open_access_pdf"] is False
    json.dumps(data)
