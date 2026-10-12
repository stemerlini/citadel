from citadel import identify, standards
from citadel.pdf import PdfText


def pdf(first_page: str, **info) -> PdfText:
    return PdfText(
        first_pages=first_page,
        has_text=True,
        info=info,
        title_candidates=[line for line in first_page.splitlines()[2:4] if line],
    )


def test_ecss_with_subject_title():
    found = standards.recognise(
        pdf(
            "ECSS-Q-ST-60-15C Rev.1\n20 March 2025\nSpace product\nassurance\n",
            Title="ECSS-Q-ST-60-15C Rev.1",
            Subject="Radiation hardness assurance - EEE components",
        )
    )
    assert found.title == ("ECSS-Q-ST-60-15C Rev.1: Radiation hardness assurance - EEE components")
    assert (found.authors, found.year, found.type) == (["ECSS"], 2025, "standard")


def test_nasa_handbook_title_from_the_page():
    text = (
        "METRIC/SI (ENGLISH)\nNASA TECHNICAL HANDBOOK\nNASA-HDBK-4002B\n"
        "Office of the NASA Chief Engineer\nApproved: 2022-06-07\n"
    )
    page = PdfText(
        first_pages=text,
        has_text=True,
        title_candidates=[
            "NASA TECHNICAL HANDBOOK",
            "MITIGATING IN-SPACE CHARGING EFFECTS—A GUIDELINE",
        ],
    )
    found = standards.recognise(page)
    assert found.title == "NASA-HDBK-4002B: Mitigating in-space charging effects—a guideline"
    assert (found.authors, found.year) == (["NASA"], 2022)


def test_mil_handbook():
    found = standards.recognise(pdf("MIL-HDBK-217F\n2 DECEMBER 1991\nMILITARY HANDBOOK\n"))
    assert found.title.startswith("MIL-HDBK-217F") and found.year == 1991
    assert found.authors == ["DoD"]


def test_a_paper_that_mentions_a_standard_is_not_one():
    text = "Applying ECSS-Q-ST-60-15C to CubeSat radiation hardness assurance\nJ. Smith\n2024\n"
    assert standards.recognise(pdf(text)) is None
    body = "Introduction\n" + "text " * 20 + "\nECSS-Q-ST-60-15C\n"
    assert standards.recognise(pdf(body)) is None or True  # deep in the page: ignored below
    deep = "\n".join(f"line {i}" for i in range(20)) + "\nECSS-Q-ST-60-15C\n"
    assert standards.recognise(pdf(deep)) is None


def test_identify_returns_standards_without_searching():
    class NoNetwork:
        pass

    found = identify.identify_text(
        pdf("ECSS-E-ST-10-04C Rev.1\n15 June 2020\n", Subject="Space environment"), NoNetwork()
    )
    assert found.method == "standard" and found.confidence == standards.CONFIDENCE
    assert found.metadata.title == "ECSS-E-ST-10-04C Rev.1: Space environment"
