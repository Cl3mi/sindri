"""A closed-vocabulary probe over client text: report only PUBLIC list members.

Built for the gdt parser arm (2026-09-25, docs/plans/2026-09-25-gdt-review-
result.md). The operator review found 4 profile-tolerance rows a parser change
would free; the change needs to know which glyph the reader wrote for the frame
and which label word tells profile of a line from profile of a surface. The
operator chose to learn that without any client text reaching an agent.

So this module tests membership of FIXED lists -- ISO 1101 glyphs with the
look-alikes a vision model plausibly emits, and ISO 1101 terms in German and
English -- and returns only members of those lists. Nothing outside them can
appear in its output. The single thing it says about the rest is whether a
row holds a symbol that is NOT listed (`has_unlisted_symbol`), so a probe that
missed is visibly inconclusive instead of silently empty.
"""
import unicodedata
from typing import Iterable, Set

# characteristic -> glyphs a transcription may use for it. Look-alikes are the
# nearest shapes in common fonts; listing them is the point of the probe.
_GLYPHS_BY_CHARACTERISTIC = {
    "Profile of a line": "⌒◠⁀︵⌢∩",
    "Profile of a surface": "⌓◓◒⏜",
    "Straightness": "⏤—–",
    "Flatness": "⏥▱◇▭□▢",
    "Circularity": "○◯",
    "Cylindricity": "⌭",
    "Parallelism": "∥‖",
    "Perpendicularity": "⊥⟂",
    "Angularity": "∠∡∢⦣",
    "Position": "⌖⊕⨁",
    "Concentricity": "◎⊙",
    "Symmetry": "⌯≡",
    "Runout": "↗⬈",
    "Total runout": "⌰⇗",
}
GLYPHS = {c for cs in _GLYPHS_BY_CHARACTERISTIC.values() for c in cs}

# Characters that belong to a callout's VALUES, never to its characteristic.
_VALUE_CHARS = set("+-±=<>×÷°~Ø⌀")

# ISO 1101 terms (German spellings with and without umlauts, and English) plus
# the generic words a label might carry instead.
LABEL_WORDS = frozenset({
    "profil", "profile", "profilform", "profiltoleranz", "kontur", "contour",
    "linienform", "linienprofil", "linie", "line",
    "flächenform", "flaechenform", "flächenprofil", "flaechenprofil",
    "fläche", "flaeche", "surface", "oberfläche", "oberflaeche",
    "form", "formtoleranz", "shape",
    "ebenheit", "flatness", "geradheit", "straightness", "rundheit",
    "roundness", "circularity", "zylinderform", "cylindricity",
    "parallelität", "parallelitaet", "parallelism", "rechtwinkligkeit",
    "perpendicularity", "winkligkeit", "neigung", "angularity",
    "position", "positionstoleranz", "lage", "koaxialität", "koaxialitaet",
    "konzentrizität", "konzentrizitaet", "concentricity", "symmetrie",
    "symmetry", "rundlauf", "planlauf", "runout", "gesamtlauf",
    "gesamtrundlauf", "total",
})
TRANSCRIPTION_WORDS = LABEL_WORDS


def _key(c: str) -> str:
    return f"U+{ord(c):04X} {unicodedata.name(c, '?')}"


def glyph_set(text: str) -> Set[str]:
    return {_key(c) for c in set(text or "") if c in GLYPHS}


def word_set(text: str, vocab: Iterable[str]) -> Set[str]:
    words = {w.strip(".,;:()[]{}/\\\"'").casefold()
             for w in str(text or "").split()}
    return words & set(vocab)


def has_unlisted_symbol(text: str) -> bool:
    for c in set(text or ""):
        if c in GLYPHS or c in _VALUE_CHARS:
            continue
        if unicodedata.category(c) in ("So", "Sm"):
            return True
    return False


def signature(items: Set[str]) -> str:
    """A row's set of list members as one key, so co-occurrence survives
    aggregation (which words appear TOGETHER on a label)."""
    return "+".join(sorted(items)) if items else "(none listed)"
