"""Choosing a topic folder for a reference, from the keywords in topics.toml.

Each topic's keywords are matched (as phrases, lightly stemmed) against the
title, abstract, journal and first-page text. A topic's score counts the
distinct keywords found, weighted by where they were found; scores roll up the
tree along the strongest path. The classifier picks the best discipline, then
descends while one child clearly dominates: an unclear paper stays at the parent topic rather than
being forced into a subfield.

Confidence is the narrowest win along the chosen path (1 = no competition,
0 = a tie), scaled down when there is little evidence at all.

A topic may claim documents outright: by type (`types = ["standard"]`) or by file
name (`files = ["TRAD_FOR_*"]`); a file-name claim beats a type claim, the deeper
topic beats its parent. A claimed document starts at that topic and only descends
by keywords; a claiming topic and its subtopics are reached only through the claim,
so a paper that merely mentions a standard stays with its subject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from . import naming
from .topics import Topic

# Distinct keyword matches count once per field, weighted by how telling the field is.
FIELD_WEIGHTS = {"title": 3.0, "abstract": 2.0, "journal": 1.5, "text": 0.5}
STRONG_EVIDENCE = 6.0  # this much score for the best discipline means full strength
DESCEND_MIN = 2.0  # a subtopic needs at least this much evidence to be chosen
MIN_MARGIN = 1 / 3  # …and must beat the runner-up by this margin (1.5x)
MAX_NGRAM = 4
# A discipline's own keywords ("plasma", "optics") are generic; a subtopic's are
# specific ("thruster", "schlieren"). Generic words must not outvote specific ones
# when a paper sits between disciplines (a "pulsed plasma thruster").
DEPTH_WEIGHTS = {1: 0.5}


@dataclass
class Classification:
    topic: str  # "" when nothing matched
    confidence: float
    ranking: list[tuple[str, float]] = field(default_factory=list)  # top disciplines
    matches: dict[str, list[str]] = field(default_factory=dict)  # topic -> keywords found
    claim: str = ""  # what put it under a claiming topic: "type standard", "file name"

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "confidence": round(self.confidence, 3),
            "ranking": [[t, round(s, 2)] for t, s in self.ranking],
            "matches": self.matches,
        }

    def explain(self) -> str:
        if self.claim:
            return f"topic {self.topic} (by {self.claim})"
        found = ", ".join(self.matches.get(self.topic, [])[:6])
        runners = "; ".join(f"{t} {s:.1f}" for t, s in self.ranking[1:3])
        return f"topic {self.topic or '-'} ({found or 'no keywords'}); next: {runners or '-'}"


def stem(word: str) -> str:
    """Very light English stemming: plurals only (instabilities -> instability)."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    """Lowercase ASCII stems; hyphenated words split (laser-plasma -> laser plasma)."""
    return [stem(part) for word in naming.words(text) for part in word.lower().split("-") if part]


def ngrams(text: str) -> set[tuple[str, ...]]:
    words = tokens(text)
    return {
        tuple(words[i : i + n]) for n in range(1, MAX_NGRAM + 1) for i in range(len(words) - n + 1)
    }


class Classifier:
    def __init__(self, topics: list[Topic]):
        self.topics = {t.path: t for t in topics}
        self.children: dict[str, list[str]] = {}
        for topic in topics:
            self.children.setdefault(topic.parent or "", []).append(topic.path)
        self.keywords = {
            t.path: [(k, tuple(tokens(k))) for k in t.keywords if tokens(k)] for t in topics
        }
        self.claiming = {t.path for t in topics if t.types or t.files}
        self.closed = {  # reached only through a claim
            p for p in self.topics if any(p == c or p.startswith(c + "/") for c in self.claiming)
        }

    def claimed(self, type: str = "", filename: str = "") -> tuple[str, str]:
        """The topic claiming a document, and why: its file name, else its type."""
        name = filename.lower()
        for test, why in (
            (lambda t: name and any(fnmatchcase(name, g.lower()) for g in t.files), "file name"),
            (lambda t: type and type in t.types, f"type {type}"),
        ):
            found = [p for p in self.claiming if test(self.topics[p])]
            if found:
                return max(found, key=lambda p: (self.topics[p].depth, p)), why
        return "", ""

    def classify(
        self,
        title: str = "",
        abstract: str = "",
        journal: str = "",
        text: str = "",
        type: str = "",
        filename: str = "",
    ) -> Classification:
        fields = {"title": title, "abstract": abstract, "journal": journal, "text": text}
        grams = {name: ngrams(value) for name, value in fields.items() if value}

        own: dict[str, float] = {}
        matches: dict[str, list[str]] = {}
        for path, keywords in self.keywords.items():
            score, found = 0.0, []
            for keyword, key in keywords:
                weight = max(
                    (FIELD_WEIGHTS[name] for name, g in grams.items() if key in g), default=0.0
                )
                if weight:
                    score += weight
                    found.append(keyword)
            own[path] = score * DEPTH_WEIGHTS.get(self.topics[path].depth, 1.0)
            if found:
                matches[path] = found

        claim, why = self.claimed(type, filename)
        # Without a claim, closed topics are out of reach; with one, the other claimers.
        blocked = (self.claiming - {claim}) if claim else self.closed
        path_score: dict[str, float] = {}

        def kids_of(path: str) -> list[str]:
            return [c for c in self.children.get(path, []) if c not in blocked]

        def score(path: str) -> float:
            """The topic's own evidence plus its best child's: the strongest path down.
            (Summing all children would let broad subtrees win on many weak matches.)"""
            if path not in path_score:
                path_score[path] = own[path] + max((score(c) for c in kids_of(path)), default=0.0)
            return path_score[path]

        if claim:
            topic, margins = self._descend(claim, kids_of, score)
            return Classification(topic, min(margins, default=1.0), [], matches, why)

        roots = sorted(kids_of(""), key=score, reverse=True)
        if not roots or score(roots[0]) == 0:
            return Classification("", 0.0, [], matches)

        topic, margins = self._descend(roots[0], kids_of, score)
        margins.insert(0, _margin(roots, score))
        strength = min(1.0, score(roots[0]) / STRONG_EVIDENCE)
        ranking = [(r, score(r)) for r in roots[:3]]
        return Classification(topic, strength * min(margins), ranking, matches)

    @staticmethod
    def _descend(topic: str, kids_of, score) -> tuple[str, list[float]]:
        """Go down while one child clearly dominates; the margins of each step."""
        margins = []
        while True:
            kids = sorted(kids_of(topic), key=score, reverse=True)
            if not kids or score(kids[0]) < DESCEND_MIN:
                break
            margin = _margin(kids, score)
            if margin < MIN_MARGIN:
                break  # two subtopics are about equally likely: stay at the parent
            margins.append(margin)
            topic = kids[0]
        return topic, margins


def _margin(ranked: list[str], score) -> float:
    """How clearly the first beats the second: 1 = no competition, 0 = a tie."""
    first = score(ranked[0])
    second = score(ranked[1]) if len(ranked) > 1 else 0.0
    return (first - second) / first if first else 0.0
