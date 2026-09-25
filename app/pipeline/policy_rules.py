"""Gold-free rules over a finished Characteristic: which extra rows to FLAG for
review, and which predictions to DROP as false detections.

Both decisions happen after the read, so a dump already on disk holds every
input they need -- which is why app/eval/policy_check.py can price a rule
EXACTLY, in CPU seconds, by calling these same functions. Keep it that way:
a rule that needs anything a dump does not store (the image, the notes block,
rotation scores) cannot be priced offline and does not belong here.

Two invariants the counterfactual depends on:
  * flag rules only ADD flags, so stored flags + rules == what the pipeline
    would have produced;
  * drop rules are ORDER-INDEPENDENT and evaluated against the original list,
    because the pipeline applies them before numbering and the dump stores the
    numbered order. Ties therefore keep both rows, never break by position.

Nothing is active by default. A rule becomes active only when the keep/revert
rule in docs/plans/2026-09-25-review-quality-arms-plan.md §1 keeps it."""
import math
import re
from typing import Callable, Dict, List, Sequence

from app.models import Characteristic
from app.pipeline.parser import _GDT_SYMBOLS

# Kinds whose rows read at 0.00-0.18 on dev (read_accuracy_by_kind, 2026-09-17)
# and ship silently wrong 3-5x more often per row than `dimension`.
NON_DIMENSION_KINDS = frozenset({"gdt", "theoretical", "surface", "note"})
# Render pixels. Boxes >= 80 px (6.8 mm at 300 dpi) read at 0.47 against 0.59
# for 40-80 px (crop-height diagnostic, 2026-09-16).
TALL_BOX_PX = 80.0
_DIAMETER_SIGNS = ("Ø", "⌀", "ø")
_DIGIT = re.compile(r"\d")
# A box at least this share inside a strictly larger one is its fragment.
CONTAINED_FRAC = 0.6
# Same nominal within this many box-heights (centre to centre) is one callout
# read twice, not a value the drawing repeats.
NEAR_HEIGHTS = 3.0


def _height(c: Characteristic) -> float:
    r = c.target_region
    return abs(r[3] - r[1]) if r else 0.0


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


# ---- flag rules: Characteristic -> bool -----------------------------------

def _nondim_kind(c):
    return (c.kind or "") in NON_DIMENSION_KINDS


def _gdt_guessed(c):
    # parser._gdt_type returns Flatness when it recognises no symbol, so the
    # char_type of such a row is a guess (0 of 8 char_type-only gdt rows on
    # dev held a known symbol, 2026-09-22).
    text = (c.raw_text or "").replace("\n", " ")
    return c.kind == "gdt" and not any(s in text for s in _GDT_SYMBOLS)


def _tall_box(c):
    return _height(c) >= TALL_BOX_PX


def _diameter_sign(c):
    # Diameter <-> Distance is confused 11 times each way over a leading Ø.
    return any(s in (c.raw_text or "") for s in _DIAMETER_SIGNS)


def _multiline(c):
    return "\n" in (c.raw_text or "").strip()


def _no_tolerance(c):
    return c.kind == "dimension" and bool(c.nominal) \
        and not c.upper_tol and not c.lower_tol


def _asymmetric_tol(c):
    if not (c.upper_tol and c.lower_tol):
        return False
    return c.upper_tol.lstrip("+").replace(",", ".") \
        != c.lower_tol.lstrip("-").replace(",", ".")


FLAG_RULES: Dict[str, Callable[[Characteristic], bool]] = {
    "nondim_kind": _nondim_kind,
    "gdt_guessed": _gdt_guessed,
    "tall_box": _tall_box,
    "diameter_sign": _diameter_sign,
    "multiline": _multiline,
    "no_tolerance": _no_tolerance,
    "asymmetric_tol": _asymmetric_tol,
}


# ---- drop rules: (Characteristic, all rows) -> bool -----------------------

def _empty_read(c, chars):
    return not (c.raw_text or "").strip()


def _contained_duplicate(c, chars):
    a = c.target_region
    if a is None or _area(a) == 0:
        return False
    for o in chars:
        b = o.target_region
        if o is c or b is None:
            continue
        # STRICTLY larger: equal boxes keep both (see module docstring).
        if _area(b) > _area(a) and _inter(a, b) / _area(a) >= CONTAINED_FRAC:
            return True
    return False


def _repeated_value_nearby(c, chars):
    a = c.target_region
    if a is None or not c.nominal:
        return False
    for o in chars:
        b = o.target_region
        if o is c or b is None or o.nominal != c.nominal:
            continue
        reach = NEAR_HEIGHTS * max(_height(c), _height(o), 1.0)
        if math.dist(_center(a), _center(b)) > reach:
            continue
        # Keep the more confident read; equal confidence keeps both.
        if o.confidence > c.confidence:
            return True
    return False


def _no_digit(c, chars):
    return c.kind == "dimension" and not _DIGIT.search(c.raw_text or "")


def _theoretical_no_nominal(c, chars):
    return c.kind == "theoretical" and not c.nominal


def _note_kind(c, chars):
    return c.kind == "note"


def _theoretical_kind(c, chars):
    return c.kind == "theoretical"


DROP_RULES: Dict[str, Callable[[Characteristic, Sequence[Characteristic]], bool]] = {
    "empty_read": _empty_read,
    "contained_duplicate": _contained_duplicate,
    "repeated_value_nearby": _repeated_value_nearby,
    "no_digit": _no_digit,
    "theoretical_no_nominal": _theoretical_no_nominal,
    "note_kind": _note_kind,
    "theoretical_kind": _theoretical_kind,
}

# Filled ONLY by the keep/revert rule. Empty means the pipeline behaves
# exactly as every dump on disk was produced.
ACTIVE_FLAG_RULES: tuple = ()
ACTIVE_DROP_RULES: tuple = ()


def apply_flag_rules(c: Characteristic, names: Sequence[str]) -> List[str]:
    """The review reasons the named rules add for `c`, in `names` order."""
    return [f"rule:{n}" for n in names if FLAG_RULES[n](c)]


def apply_drop_rules(chars: List[Characteristic],
                     names: Sequence[str]) -> List[Characteristic]:
    """`chars` minus every row any named rule drops, judged against the
    ORIGINAL list so the result does not depend on evaluation order."""
    rules = [DROP_RULES[n] for n in names]
    return [c for c in chars if not any(r(c, chars) for r in rules)]
