"""The MCP server, called through the MCP layer as an agent would."""

import json

import pytest

from citadel.library import Library
from citadel.mcp_server import _outline, _section_span, build_server
from conftest import file_ref, make_ref

FULLTEXT = """## Abstract

We review MHD turbulence.

## 1 Introduction

Kolmogorov cascades.

## 2 Results

### 2.1 Spectra

The spectral index is $-3/2$.

$$
# not a heading inside maths
$$

### 2.2 Anisotropy

Goldreich and Sridhar.

## References

[1] Someone."""


@pytest.fixture
def server(library_root, config_file, index_file):
    Library(library_root).init()
    file_ref(library_root, make_ref(), FULLTEXT)
    file_ref(
        library_root,
        make_ref(
            citekey="harding2009observation",
            title="Observation of a Kelvin-Helmholtz instability",
            authors=["Harding, E. C."],
            year=2009,
            topic="plasma-physics/instabilities",
            doi="10.1/x",
            tags=["omega"],
        ),
        pdf=False,
    )
    return build_server()


async def call(server, tool, **arguments):
    result = await server.call_tool(tool, arguments)
    assert not result.is_error, result
    return json.loads(result.content[0].text)


async def test_tools_and_annotations(server):
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == {"search", "get", "read", "cite", "list_references", "topics", "add"}
    assert all(tools[n].annotations.read_only_hint for n in tools if n != "add")
    assert tools["add"].annotations.read_only_hint is False


async def test_search_with_topic(server):
    found = await call(server, "search", query="kolmogorov")
    assert [r["citekey"] for r in found["results"]] == ["verma2004statistical"]
    assert found["results"][0]["has_markdown"] and "[Kolmogorov]" in found["results"][0]["snippet"]
    none = await call(server, "search", query="kolmogorov", topic="plasma-physics/instabilities")
    assert none["results"] == []


async def test_get_has_outline_and_files(server):
    ref = await call(server, "get", citekey="verma2004statistical")
    assert ref["title"].startswith("Statistical theory") and ref["doi"]
    assert ref["files"]["pdf"].endswith("_Verma.pdf")
    sections = [s["section"] for s in ref["markdown"]["outline"]]
    assert sections == [
        "Abstract",
        "1 Introduction",
        "2 Results",
        "2.1 Spectra",
        "2.2 Anisotropy",
        "References",
    ]
    other = await call(server, "get", citekey="harding2009observation")
    assert other["files"]["pdf"] is None and other["markdown"]["available"] is False
    missing = await call(server, "get", citekey="nobody")
    assert "error" in missing


async def test_read_by_section_and_in_chunks(server):
    results = await call(server, "read", citekey="verma2004statistical", section="2")
    assert results["text"].startswith("## 2 Results") and "Goldreich" in results["text"]
    assert "References" not in results["text"]
    spectra = await call(server, "read", citekey="verma2004statistical", section="spectra")
    assert spectra["text"].startswith("### 2.1 Spectra") and "Goldreich" not in spectra["text"]
    first = await call(server, "read", citekey="verma2004statistical", max_chars=1000)
    assert first["start"] == 0 and first["end"] == len(FULLTEXT) and first["next_start"] is None
    bad = await call(server, "read", citekey="verma2004statistical", section="9")
    assert "no section" in bad["error"] and bad["outline"]
    unconverted = await call(server, "read", citekey="harding2009observation")
    assert "no Markdown" in unconverted["error"]


async def test_cite_and_list_and_topics(server):
    cited = await call(server, "cite", citekeys=["verma2004statistical", "nobody"], style="bibtex")
    assert cited["citations"][0].startswith("@article{verma2004statistical,")
    assert cited["missing"] == ["nobody"]
    listed = await call(server, "list_references", topic="plasma-physics", sort="year")
    assert [r["citekey"] for r in listed["references"]] == [
        "harding2009observation",
        "verma2004statistical",
    ]
    tree = await call(server, "topics")
    counts = {t["path"]: t["references"] for t in tree["topics"]}
    assert counts["plasma-physics"] == 2 and counts["optics"] == 0


async def test_add_reports_errors_cleanly(server, tmp_path):
    result = await call(server, "add", target=str(tmp_path / "missing.pdf"))
    assert result["status"] == "error" and result["reason"] == "not a file"


def test_outline_ignores_maths():
    assert [o["section"] for o in _outline(FULLTEXT)].count("not a heading inside maths") == 0
    begin, end = _section_span(FULLTEXT, "2.2")
    assert FULLTEXT[begin:end].startswith("### 2.2 Anisotropy")
