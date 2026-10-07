import pytest

from citadel import identifiers as ids


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://doi.org/10.1063/1.2009008", "10.1063/1.2009008"),
        ("doi:10.1103/PhysRevLett.116.105004.", "10.1103/physrevlett.116.105004"),
        ("10.1002/(SICI)1097-0207(19960415)39:7", "10.1002/(sici)1097-0207(19960415)39:7"),
        ("10.1088/1361-6501/aa5748)", "10.1088/1361-6501/aa5748"),
        ("10.1016/j.jqsrt.2003.12.014];", "10.1016/j.jqsrt.2003.12.014"),
        ("https://dx.doi.org/10.1000%2Fabc", "10.1000/abc"),
    ],
)
def test_clean_doi(raw, expected):
    assert ids.clean_doi(raw) == expected


def test_find_dois_in_order_without_duplicates():
    text = "see (doi: 10.1234/aa) and https://doi.org/10.5678/bb, again 10.1234/AA."
    assert ids.find_dois(text) == ["10.1234/aa", "10.5678/bb"]
    assert ids.find_dois("version 10.1/x") == []  # registrants have 4-9 digits


def test_find_arxiv():
    text = "arXiv:2101.12345v2 [physics.plasm-ph] and https://arxiv.org/abs/nlin/0404043"
    assert ids.find_arxiv(text) == ["2101.12345", "nlin/0404043"]
    assert ids.find_arxiv("version 2101.12345 of something") == []


def test_find_isbns_labelled_and_bare():
    assert ids.find_isbns("ISBN 978-0-7503-3307-8 (ebook)") == ["9780750333078"]
    assert ids.find_isbns("ISBN-10: 0-306-40615-2") == ["0306406152"]
    columns = "ISBN\nISBN\n\n978-0-7503-3307-8 (ebook)\n978-0-7503-3305-4 (print)"
    assert ids.find_isbns(columns) == ["9780750333078", "9780750333054"]
    assert ids.find_isbns("978-0-7503-3307-8 without the label") == []
    assert ids.find_isbns("ISBN 978-0-7503-3307-9") == []  # bad check digit


@pytest.mark.parametrize(
    "title",
    [
        "1965plph.conf..287A",
        "/Users/tsutomu/jjap/99-6r2/98R08037.ps",
        "doi:10.1016/j.x",
        "Microsoft Word - paper.docx",
        "untitled",
        "book_test",
        "",
        "PII: S0022-4073(03)00",
    ],
)
def test_junk_titles(title):
    assert ids.is_junk_title(title)


def test_real_titles():
    assert not ids.is_junk_title("Plasma turbulence imaging using high-power laser scattering")
