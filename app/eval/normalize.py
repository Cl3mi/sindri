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
    key = " ".join(str(v or "").split()).casefold()
    return CHAR_TYPE_SYNONYMS.get(key, key)


def char_type_equal(a, b) -> bool:
    return _canon_char_type(a) == _canon_char_type(b)
