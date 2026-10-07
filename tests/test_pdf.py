from pathlib import Path

from citadel import pdf


def bbox(lines, height=792):
    """A minimal pdftotext -bbox-layout page; lines are (y, size, text)."""
    body = []
    for y, size, text in lines:
        words, x = [], 50.0
        for word in text.split():
            words.append(
                f'<word xMin="{x}" yMin="{y}" xMax="{x + 30}" yMax="{y + size}">{word}</word>'
            )
            x += 35
        body.append(
            f'<line xMin="50" yMin="{y}" xMax="500" yMax="{y + size}">' + "".join(words) + "</line>"
        )
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><body><doc>'
        f'<page width="612" height="{height}"><flow><block>{"".join(body)}'
        "</block></flow></page></doc></body></html>"
    )


def test_title_is_the_largest_block_and_wraps():
    lines, height = pdf.bbox_lines(
        bbox(
            [
                (40, 8, "BRIEF REPORT | AUGUST 12 2005"),
                (80, 16, "Turbulent stress invariant analysis: Clarification of"),
                (98, 16, "existing terminology"),
                (130, 9, "A. J. Simonsen and P. Krogstad"),
                (700, 20, "Big footer text here that is low"),
            ]
        )
    )
    assert height == 792
    titles = pdf.guess_titles(lines, height)
    assert titles[0] == (
        "Turbulent stress invariant analysis: Clarification of existing terminology"
    )
    assert "Big footer text here that is low" not in titles


def test_superscripts_do_not_make_body_text_a_title():
    page = bbox(
        [
            (80, 14, "Sudden Viscous Dissipation of Compressing Turbulence"),
            (300, 10, "In an isotropic compression it grows as L"),
        ]
    )
    # A superscript stretches one word, and with it the whole line's box.
    stretched = page.replace(
        'xMin="50.0" yMin="300" xMax="80.0" yMax="310"',
        'xMin="50.0" yMin="285" xMax="80.0" yMax="335"',
    )
    stretched = stretched.replace(
        'yMin="300" xMax="500" yMax="310"', 'yMin="285" xMax="500" yMax="335"'
    )
    assert stretched.count('yMax="335"') == 2  # the test really changed the page
    lines, height = pdf.bbox_lines(stretched)
    assert pdf.guess_titles(lines, height)[0].startswith("Sudden Viscous")


def test_single_line_title_page_uses_real_page_height():
    lines, height = pdf.bbox_lines(bbox([(300, 24, "Energy from Nuclear Fusion")], 720))
    assert pdf.guess_titles(lines, height) == ["Energy from Nuclear Fusion"]


def test_banners_chapter_prefix_and_cover_text_are_skipped():
    lines, height = pdf.bbox_lines(
        bbox(
            [
                (40, 30, "Topical Review"),
                (90, 20, "Chapter 12 Experimental Astrophysics"),
                (150, 22, "This content has been downloaded from IOPscience"),
            ]
        )
    )
    assert pdf.guess_titles(lines, height) == ["Experimental Astrophysics"]


def test_bad_xml_gives_nothing():
    assert pdf.bbox_lines("<not xml") == ([], 0.0)
    assert pdf.guess_titles([]) == []


def test_tsv_lines():
    header = "level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "1\t1\t0\t0\t0\t0\t0\t0\t1700\t2200\t-1\t",
        "5\t1\t1\t1\t1\t1\t100\t200\t300\t60\t95\tIntroduction",
        "5\t1\t1\t1\t1\t2\t420\t205\t100\t50\t91\tto",
        "5\t1\t1\t1\t1\t3\t540\t200\t300\t60\t12\tjunk",
        "5\t1\t2\t1\t1\t1\t100\t900\t300\t20\t90\tsmall",
    ]
    lines, height = pdf.tsv_lines("\n".join([header, *rows]))
    assert height == 2200
    assert lines[0] == (60.0, 200.0, 260.0, "Introduction to")
    assert lines[1][3] == "small"


def test_read_skips_cover_and_textless_pages(monkeypatch):
    cover = "This content has been downloaded from IOPscience. " + "word " * 30
    pages = [cover, "", "Energy from Nuclear Fusion " + "text " * 30, "more"]
    calls = []

    def fake_pdftotext(path, options):
        calls.append(options)
        if "-bbox-layout" in options:
            return bbox([(300, 24, "Energy from Nuclear Fusion")])
        return "\f".join(pages)

    monkeypatch.setattr(pdf, "pdftotext", fake_pdftotext)
    monkeypatch.setattr(pdf, "_pdfinfo", lambda path: {"Pages": "4", "Title": "book_test"})
    result = pdf.read(Path("x.pdf"))
    assert result.cover == cover and result.has_text and not result.ocr
    assert result.first_pages.startswith("Energy from Nuclear Fusion")
    assert ["-f", "3", "-l", "3", "-bbox-layout"] in calls  # page 3 is the title page
    assert result.title_guess == "Energy from Nuclear Fusion"
    assert result.meta_title == ""  # junk


def test_scans_fall_back_to_ocr(monkeypatch):
    monkeypatch.setattr(pdf, "pdftotext", lambda path, options: "\f\f")
    monkeypatch.setattr(pdf, "_pdfinfo", lambda path: {"Pages": "190"})
    ocr_text = "Introduction to Plasma Physics " + "words " * 30
    monkeypatch.setattr(
        pdf,
        "ocr_first_pages",
        lambda path: ([(60.0, 100.0, 160.0, "Introduction to Plasma Physics")], 2200.0, ocr_text),
    )
    result = pdf.read(Path("scan.pdf"))
    assert result.ocr and result.has_text
    assert result.title_guess == "Introduction to Plasma Physics"
    assert result.more_text() == ""  # no text layer to read later pages from


def test_sha256(tmp_path):
    file = tmp_path / "a.pdf"
    file.write_bytes(b"abc")
    assert pdf.sha256(file).startswith("ba7816bf")
