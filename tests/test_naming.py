import pytest

from citadel import naming


@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "Statistical theory of magnetohydrodynamic turbulence: recent results",
            "StatisticalTheoryMagnetohydrodynamicTurbulence",
        ),
        (
            "Observation of the Kelvin–Helmholtz instability in a HED plasma on OMEGA",
            "ObservationKelvinHelmholtzInstabilityHEDPlasma",
        ),
        ("Construction of the PFRC-2 device", "ConstructionPFRC2Device"),
        ("On the <i>in situ</i> growth of $\\beta$-limited modes", "SituGrowthLimitedModes"),
        ("Schlieren für Plasmen", "SchlierenFurPlasmen"),
        (
            "Plasma turbulence imaging using high-power laser Thomson scattering",
            "PlasmaTurbulenceImagingHighPowerLaserThomson",
        ),
        (
            "Monoenergetic proton backlighter for measuring E and B fields and for radiographing",
            "MonoenergeticProtonBacklighterMeasuringEB",
        ),
        ("The", "The"),
        ("", "Untitled"),
    ],
)
def test_short_title(title, expected):
    assert naming.short_title(title) == expected


def test_short_title_is_capped():
    title = " ".join(f"word{i}" for i in range(20))
    result = naming.short_title(title)
    assert result.count("Word") == naming.MAX_TITLE_WORDS


def test_subtitle_kept_when_head_is_empty():
    assert naming.short_title(": Advances in fusion") == "AdvancesFusion"


@pytest.mark.parametrize(
    "author, expected",
    [
        ("Verma, Mahendra K.", "Verma"),
        ("Schäfer, J.", "Schafer"),
        ("van der Merwe, J.", "VanDerMerwe"),
        ("O'Neil, K.", "ONeil"),
        ("Mahendra K. Verma", "Verma"),
        ("NRL", "NRL"),
        ("ECSS", "ECSS"),
        ("Texas Instruments", "TexasInstruments"),
        ("Jean-Luc Picard", "JeanLucPicard"),  # no comma, no initials: read as an organisation
        ("Smørgrav, D.", "Smorgrav"),
        ("Ascoli-Bartoli, U.", "AscoliBartoli"),
        ("", "Anon"),
    ],
)
def test_surname(author, expected):
    assert naming.surname(author) == expected


def test_basename_and_citekey():
    title = "Statistical theory of magnetohydrodynamic turbulence"
    assert naming.basename(2004, title, ["Verma, M. K."]) == (
        "2004_StatisticalTheoryMagnetohydrodynamicTurbulence_Verma"
    )
    assert naming.citekey(2004, title, ["Verma, M. K."]) == "verma2004statistical"


def test_missing_year_and_authors():
    assert naming.basename(None, "Formulary", []) == "nd_Formulary_Anon"
    assert naming.citekey(None, "The formulary", []) == "anonndformulary"


def test_unique():
    assert naming.unique("a", set()) == "a"
    assert naming.unique("a", {"a"}) == "ab"
    assert naming.unique("a", {"a", "ab"}) == "ac"
    assert naming.unique("x_Verma", {"x_Verma"}, "-") == "x_Verma-b"
    taken = {"a"} | {f"a{c}" for c in "bcdefghijklmnopqrstuvwxyz"}
    assert naming.unique("a", taken) == "aaa"
