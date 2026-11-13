from citadel.classify import Classifier, ngrams, stem
from citadel.topics import parse

TREE = parse("""
["plasma-physics"]
keywords = ["plasma"]
["plasma-physics/diagnostics"]
keywords = ["plasma diagnostics"]
["plasma-physics/diagnostics/refractive"]
keywords = ["schlieren", "shadowgraphy", "interferometry"]
["plasma-physics/diagnostics/scattering"]
keywords = ["Thomson scattering"]
["plasma-physics/turbulence-transport"]
keywords = ["turbulence", "energy spectrum"]
["space-engineering"]
keywords = ["spacecraft"]
["space-engineering/propulsion"]
keywords = ["thruster", "propulsion", "specific impulse"]
["optics"]
keywords = ["optics"]
""")


def classify(**fields):
    return Classifier(TREE).classify(**fields)


def test_stemming_and_phrases():
    assert stem("instabilities") == "instability" and stem("thrusters") == "thruster"
    assert stem("plasmas") == "plasma" and stem("gas") == "gas" and stem("class") == "class"
    assert ("laser", "plasma") in ngrams("Laser-plasma interaction")
    assert ("thomson", "scattering") in ngrams("Thomson Scattering of lasers")


def test_clear_case_descends_to_the_leaf():
    found = classify(
        title="A review of schlieren and shadowgraphy",
        abstract="We review interferometry and schlieren in plasma.",
    )
    assert found.topic == "plasma-physics/diagnostics/refractive"
    assert found.confidence > 0.6
    assert "schlieren" in found.matches[found.topic]


def test_specific_evidence_beats_a_generic_discipline_word():
    found = classify(
        title="A plasmoid thruster for space propulsion",
        abstract="Plasma propulsion with high specific impulse.",
    )
    assert found.topic == "space-engineering/propulsion"


def test_unclear_subtopic_stays_at_the_parent():
    found = classify(title="Thomson scattering and schlieren measurements of plasma")
    assert found.topic == "plasma-physics/diagnostics"


def test_a_close_call_between_disciplines_has_low_confidence():
    found = classify(
        title="Turbulence in thruster plumes", abstract="energy spectrum of propulsion"
    )
    assert found.confidence < 0.5


def test_nothing_matches():
    found = classify(title="Recipes for sourdough bread")
    assert (found.topic, found.confidence) == ("", 0.0)


def test_title_counts_more_than_page_text():
    found = classify(title="Thruster design", text="turbulence turbulence energy spectrum")
    assert found.topic == "space-engineering/propulsion"


ROUTED = parse("""
["space-engineering"]
keywords = ["spacecraft"]
["space-engineering/radiation-effects"]
keywords = ["single event effects", "total ionizing dose"]
["standards"]
types = ["standard"]
["standards/ecss"]
keywords = ["ECSS"]
["standards/nasa"]
keywords = ["NASA-HDBK", "NASA-STD"]
["training"]
["training/trad-course"]
files = ["TRAD_FOR_*", "* - TRAD_FOR_*"]
["training/short-courses"]
keywords = ["short course"]
["slides"]
types = ["slides", "poster"]
""")


def test_topics_claim_document_types_and_file_names():
    classifier = Classifier(ROUTED)
    standard = classifier.classify(
        title="ECSS-Q-ST-60-15C Radiation hardness assurance", type="standard"
    )
    assert (standard.topic, standard.confidence) == ("standards/ecss", 1.0)
    # The file name wins over the type: course lectures are slides, but training.
    lecture = classifier.classify(
        title="Single event effects", type="slides", filename="4 - TRAD_FOR_RAD_PRES_SEE V1.pdf"
    )
    assert lecture.topic == "training/trad-course"
    talk = classifier.classify(title="Single event effects testing", type="slides")
    assert talk.topic == "slides"
    # A paper that only mentions a standard stays with its subject.
    paper = classifier.classify(
        title="Single event effects in ECSS qualified parts", abstract="total ionizing dose"
    )
    assert paper.topic == "space-engineering/radiation-effects"
    # Topics without a claim stay reachable by keywords.
    assert classifier.classify(title="RADECS short course").topic == "training/short-courses"
