import json

import pytest

from citadel.__main__ import main


def run(capsys, *argv):
    with pytest.raises(SystemExit) as exit_info:
        main(list(argv))
    return exit_info.value.code, capsys.readouterr().out


def test_init_then_doctor(capsys, library_root, config_file):
    code, out = run(capsys, "init", "--json")
    data = json.loads(out)
    assert code == 0 and data["config_created"] and data["topics_file_created"]
    assert config_file.exists() and (library_root / "_inbox").is_dir()

    code, out = run(capsys, "doctor", "--json")
    data = json.loads(out)
    assert code == 0 and data["library_ok"] and data["problems"] == []
    assert data["vault"] is None


def test_init_twice_has_nothing_to_do(capsys, library_root, config_file):
    run(capsys, "init")
    code, out = run(capsys, "init")
    assert code == 0 and "nothing to do" in out


def test_doctor_fails_without_setup(capsys, library_root, config_file):
    code, out = run(capsys, "doctor", "--json")
    data = json.loads(out)
    assert code == 1 and not data["library_ok"]
    assert any("citadel init" in p for p in data["problems"])


def test_topics_json(capsys, library_root, config_file):
    code, out = run(capsys, "topics", "--json")
    paths = [t["path"] for t in json.loads(out)["topics"]]
    assert code == 0 and "plasma-physics/fusion/magnetic-confinement" in paths


def test_topics_text_is_indented(capsys, library_root, config_file):
    _, out = run(capsys, "topics")
    assert "\n    pulsed-power/" in out


def test_no_command_points_to_help(capsys):
    code, out = run(capsys)
    assert code == 0 and "citadel doctor" in out


def test_reindex_list_search_get(capsys, library_root, config_file, index_file):
    from conftest import file_ref, make_ref

    run(capsys, "init")
    file_ref(library_root, make_ref(), "Energy cascade in MHD.")

    code, out = run(capsys, "reindex", "--json")
    assert code == 0 and json.loads(out)["added"] == ["verma2004statistical"]

    code, out = run(capsys, "list", "--json", "--topic", "plasma-physics")
    assert [r["citekey"] for r in json.loads(out)["references"]] == ["verma2004statistical"]

    code, out = run(capsys, "search", "cascade")
    assert "verma2004statistical" in out and "[cascade]" in out

    code, out = run(capsys, "get", "verma2004statistical", "--json")
    data = json.loads(out)
    assert data["paths"]["pdf"].endswith("_Verma.pdf") and data["tags"] == ["mhd", "spectra"]

    code, out = run(capsys, "get", "nope")
    assert code == 1 and "No reference" in out


def test_reindex_reports_problems(capsys, library_root, config_file, index_file):
    run(capsys, "init")
    broken = library_root / "optics" / "broken"
    broken.mkdir()
    (broken / "broken.md").write_text("junk")
    code, out = run(capsys, "reindex")
    assert code == 1 and "problem: optics/broken" in out


def test_identify(capsys, monkeypatch, tmp_path, config_file):
    from citadel import identify
    from citadel.identify import Identification
    from citadel.sources import Metadata

    paper = tmp_path / "x.pdf"
    paper.write_bytes(b"%PDF")
    meta = Metadata(
        title="Sudden viscous dissipation of compressing turbulence",
        authors=["Davidovits, Seth"],
        year=2016,
        doi="10.1103/x",
    )
    monkeypatch.setattr(
        identify,
        "identify",
        lambda path, http: Identification(meta, 0.97, "doi", ["doi 10.1103/x"]),
    )
    code, out = run(capsys, "identify", "--json", str(paper), str(tmp_path / "missing.pdf"))
    first, second = json.loads(out)["results"]
    assert code == 0 and first["confidence"] == 0.97
    assert first["basename"] == "2016_SuddenViscousDissipationCompressingTurbulence_Davidovits"
    assert first["citekey"] == "davidovits2016sudden"
    assert second["error"] == "not a file"
    code, out = run(capsys, "identify", "-v", str(paper))
    assert "confidence 0.97" in out and "· doi 10.1103/x" in out


@pytest.mark.parametrize(
    "target, expected",
    [
        ("doi:10.1063/1.2009008", ("doi", "10.1063/1.2009008")),
        ("10.1063/1.2009008", ("doi", "10.1063/1.2009008")),
        ("https://doi.org/10.1063/1.2009008", ("doi", "10.1063/1.2009008")),
        ("arxiv:2101.12345", ("arxiv", "2101.12345")),
        ("https://arxiv.org/abs/2101.12345v2", ("arxiv", "2101.12345")),
        ("arxiv:nlin/0404043", ("arxiv", "nlin/0404043")),
        ("~/Downloads/paper.pdf", ("pdf", "~/Downloads/paper.pdf")),
    ],
)
def test_parse_target(target, expected):
    from citadel.__main__ import parse_target

    assert parse_target(target) == expected
