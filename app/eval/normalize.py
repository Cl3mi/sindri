"""Canonical value comparison for gold vs prediction. The ONLY place equality
is defined — matching, scoring, and taxonomy all import from here, so a policy
change (Task 13, after inspecting real Excel conventions) is one edit.

Policy defaults:
- numbers compare numerically: '1,20' == '1.2' == 1.2 (Excel float cell)
- empty != '0' (an absent tolerance is not a zero tolerance)
- non-numbers compare casefolded + whitespace-collapsed
- char_type compares through a synonym map (German gold labels -> parser
  constants); unknown labels compare as plain strings
"""
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

# Gold-sheet label -> parser.py char_type constant. Extend in Task 13 from the
# real Excel vocabulary; keys and values are matched casefolded.
CHAR_TYPE_SYNONYMS: Dict[str, str] = {
    "durchmesser": "Diameter",
    "diameter": "Diameter",
    "radius": "Radius",
    "mass": "Distance",
    "maß": "Distance",
    "abstand": "Distance",
    "distance": "Distance",
    "länge": "Distance",
    "ebenheit": "Flatness",
    "flatness": "Flatness",
    "position": "Position",  # parser._gdt_type emits this for ⊕/⌖
    "werkstoff": "Material",
    "material": "Material",
    "note": "Note",
    "hinweis": "Note",
    "theoretical": "Theoretical",
    "theoretisch": "Theoretical",
    "reference": "Reference",
    "klammermass": "Reference",
    "klammermaß": "Reference",
    # Named linear dimensions. The same category as maß/abstand/länge above,
    # which were mapped from the start -- these entries were simply missing.
    # They are not cosmetic: the parser emits Distance for any bare number, and
    # a callout printing "20" carries nothing that says whether that 20 is a
    # width or a length. That distinction lives in the drawing's geometry, not
    # in the crop the read stage sees, so scoring these as char_type errors
    # charged the pipeline for information no model or prompt could recover.
    "breite": "Distance", "width": "Distance",
    "höhe": "Distance", "hoehe": "Distance", "height": "Distance",
    "tiefe": "Distance", "depth": "Distance",
    "dicke": "Distance", "thickness": "Distance",
    # Geometric tolerances. parser._GDT_SYMBOLS emits English constants while
    # gold labels this corpus in German, so the two could never compare equal.
    # The English constant must be a key too, or the two sides canonicalise
    # differently: _canon_char_type returns the mapped VALUE verbatim, so an
    # unmapped "Circularity" stays casefolded as "circularity" and never equals
    # the "Circularity" that "rundheit" maps to. Every value below is also a key
    # for exactly this reason -- see test_every_synonym_value_is_also_a_key.
    "circularity": "Circularity",
    "rundheit": "Circularity", "roundness": "Circularity",
    "parallelism": "Parallelism",
    "parallelität": "Parallelism", "parallelitaet": "Parallelism",
    # DELIBERATELY NOT MAPPED, and each for a reason worth keeping:
    #   symmetrie/symmetry, rundlauf/runout, profil/profile, oberfläche/surface
    #     -- the parser has no such char_type constant, so mapping the gold label
    #        would point it at a value the pipeline can never emit. Fixing these
    #        means extending parser._GDT_SYMBOLS, not this map.
    #   winkel/angle/winkligkeit
    #     -- ambiguous (Winkligkeit is angularity, Rechtwinkligkeit is
    #        perpendicularity) AND the parser has no Angle constant, so any
    #        mapping here would be scoring policy inventing a win.
    #   gewinde/thread, fase/chamfer
    #     -- genuinely different characteristics, not distance synonyms.
    # Adding a key that is NOT already in _DIMENSION_WORDS below would change
    # which gold rows are scored at all, moving n_gold and breaking comparability
    # with every earlier report. tests/eval/test_normalize.py guards that.
}


# Assumes nominals/tolerances < 1000: locale thousands separators ("1.234,56") are not parsed as numbers.
# Words that mark a MEASURABLE characteristic. Confirmed against the real
# corpus (2026-08-17), where sheets mix dimensions with verbal requirements
# ("SCHNITTKANTEN BLANK ZULAESSIG") that were never ballooned. Scoring the
# latter as missed callouts would let note text dominate the review-cost metric.
_DIMENSION_WORDS = frozenset(
    list(CHAR_TYPE_SYNONYMS) + [
        "perpendicularity", "parallelism", "concentricity", "cylindricity",
        "angularity", "circularity", "roundness", "symmetry", "sym", "sym.",
        "profile", "runout", "rundheit", "rundlauf", "symmetrie", "winkel",
        "winkligkeit", "parallelität", "parallelitaet", "profil",
        "surface", "oberfläche", "oberflaeche", "shape", "form",
        "breite", "höhe", "hoehe", "tiefe", "dicke", "width", "height",
        "depth", "thickness", "angle", "chamfer", "fase", "gewinde", "thread",
    ])


def char_type_kind(label) -> str:
    """'dimension' for a measurable characteristic, 'note' for a verbal
    requirement, 'unknown' for a blank.

    Word-containment rather than exact match, so 'Diameter MIN' and
    'Sym. 0,05 zu C' classify as dimensions while 'STANZGRATSEITE' does not.
    Anything unrecognised is a note: an unrecognised label in this corpus is a
    German requirement sentence, and the tail of them is long."""
    text = " ".join(str(label or "").split())
    if not text:
        return "unknown"
    words = {w.strip(".,;:()[]").casefold() for w in text.split()}
    if words & _DIMENSION_WORDS:
        return "dimension"
    return "note"


def _try_decimal(s: str) -> Optional[Decimal]:
    t = s.strip().replace(",", ".").lstrip("+")
    if not t:
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    # Infinity/NaN parse as Decimal but are data garbage in this domain
    # (tolerance sheets); treat them as plain strings, never as numbers.
    return d if d.is_finite() else None


def canon_value(v) -> str:
    """Canonical string form: numeric values via Decimal (trailing zeros
    stripped, comma/period unified), everything else casefolded/stripped."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v)
    d = _try_decimal(s)
    if d is not None:
        d = d.normalize()
        # Decimal('20').normalize() -> '2E+1'; re-quantize integers
        if d == d.to_integral_value():
            d = d.quantize(Decimal(1))
        return str(d)
    return " ".join(s.split()).casefold()


def values_equal(a, b) -> bool:
    return canon_value(a) == canon_value(b)


def _canon_char_type(v) -> str:
    """Gold label or parser constant -> one canonical characteristic name.

    Exact match first, then word CONTAINMENT — because gold labels carry
    qualifiers and datum references ("Diameter MIN", "Ebenheit 0,05 zu C") that
    an exact match can never resolve. char_type_kind above already reads labels
    that way, and its docstring already argues for it; matching on the whole
    string here meant the two functions read the same label two different ways.
    Measured on dev: 68 of the 115 char_type disagreements were gold labels
    this function could not resolve at all.

    A unique answer is REQUIRED. If a label's words reach two different
    characteristic names there is no principled choice, so it stays unresolved
    rather than being credited to whichever came first — a scoring relaxation
    with no compare_runs fingerprint must not guess in the pipeline's favour.

    Monotone by construction: exact matches still match, so no row that
    compared equal before can compare unequal now."""
    text = " ".join(str(v or "").split())
    key = text.casefold()
    if key in CHAR_TYPE_SYNONYMS:
        return CHAR_TYPE_SYNONYMS[key]
    words = {w.strip(".,;:()[]").casefold() for w in text.split()}
    reachable = {CHAR_TYPE_SYNONYMS[w] for w in words if w in CHAR_TYPE_SYNONYMS}
    return reachable.pop() if len(reachable) == 1 else key


def char_type_equal(a, b) -> bool:
    return _canon_char_type(a) == _canon_char_type(b)
