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
  * drop rules are ORDER-INDEPENDENT and evaluated against the original list.
    `pos` is assigned by `place.number_characteristics` only AFTER the rules
    would run, and `extract` discards that call's return value, so the list a
    rule sees is in raw detection order, not reading order -- neither the
    pipeline nor a dump reload can guarantee the same order twice. Rules must
    therefore depend on neither list position nor `pos`, and ties keep both
    rows rather than break by either.

A rule becomes active only when the keep/revert rule in
docs/plans/2026-09-25-review-quality-arms-plan.md §1 keeps it -- see
ACTIVE_FLAG_RULES / ACTIVE_DROP_RULES below for what is currently kept."""
import math
import re
from typing import Callable, Dict, List, Sequence, Tuple

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
    # Also true of note_ref rows: extract.py rewrites a theoretical box whose
    # text is a bare note number to kind "note" (subtype "note_ref"), and
    # those inherit the same low read reliability as any other note. Leave
    # that as is -- it is not a mislabel, it is the same bucket.
    return (c.kind or "") in NON_DIMENSION_KINDS


def _gdt_guessed(c):
    # parser._gdt_type recognises a symbol OR infers Position from a leading
    # Ø-zone sign (2026-09-25); with neither present it falls back to
    # Flatness. Both the Ø-inference and the Flatness default are GUESSES --
    # only a row whose text holds a symbol _GDT_SYMBOLS knows is a real read
    # (0 of 8 char_type-only gdt rows on dev held one, 2026-09-22).
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
    # Reference (Klammermaß) dimensions carry no tolerance by definition --
    # informational only, so a missing tolerance there is not a fault.
    return c.kind == "dimension" and c.char_type != "Reference" \
        and bool(c.nominal) and not c.upper_tol and not c.lower_tol


def _asymmetric_tol(c):
    # GD&T rows are shaped upper_tol=<zone>, lower_tol="0" by parser
    # construction -- a zone width, not a +/- pair, so this rule means
    # nothing off `dimension`. Compare NUMERICALLY: "+0,1"/"-0,10" is
    # formatting, not asymmetry, and a string compare flagged both that and
    # every GD&T row (upper_tol never string-equals "0" reversed).
    if c.kind != "dimension" or not (c.upper_tol and c.lower_tol):
        return False
    try:
        upper = float(c.upper_tol.lstrip("+").replace(",", "."))
        lower = float(c.lower_tol.replace(",", "."))
    except ValueError:
        return False
    return upper != -lower


FLAG_RULES: Dict[str, Callable[[Characteristic], bool]] = {
    "nondim_kind": _nondim_kind,
    "gdt_guessed": _gdt_guessed,
    "tall_box": _tall_box,
    "diameter_sign": _diameter_sign,
    "multiline": _multiline,
    "no_tolerance": _no_tolerance,
    "asymmetric_tol": _asymmetric_tol,
}

# review_reasons reaches a reviewer as a hover tooltip (app/static/js/table.js,
# viewer.js) -- `rule:no_tolerance` is jargon a reviewer never signed up to
# learn. One human-readable string per FLAG_RULES key, checked 1:1 by
# tests/test_policy_rules.py so a new rule can never ship without one.
FLAG_REASONS: Dict[str, str] = {
    "nondim_kind": "GD&T / surface / note / boxed callout — read often "
                   "wrong, check",
    "gdt_guessed": "GD&T symbol not recognised — characteristic type guessed",
    "tall_box": "tall box — may span several lines",
    "diameter_sign": "Ø present — check diameter vs distance",
    "multiline": "multi-line read",
    "no_tolerance": "no tolerance read — apply the general tolerance from "
                    "the title block",
    "asymmetric_tol": "asymmetric tolerance — check both limits",
}


# ---- drop rules: (Characteristic, all rows) -> bool -----------------------

def _empty_read(c, chars):
    return not (c.raw_text or "").strip()


def _content_related(small, big):
    """Containment alone is not a duplicate: a single oversized box (a stray
    detection artefact, a title-block frame, a note's boilerplate) would
    otherwise swallow every real callout that happens to fall inside it.

    Two different kinds are never the same read -- a note's text and a
    dimension's are unrelated even when one contains the other's digits by
    coincidence ("...SEE NOTE 5" holding a "5" that is really its own row).

    The text check is a WHOLE-TOKEN match (bounded by neither digit nor
    separator), not a bare substring: a plain `in` check let "5" match
    inside "25" or "2768", i.e. any number that happens to embed the small
    box's digits, which is not the same value at all."""
    if small.kind != big.kind:
        return False
    text = (small.raw_text or "").strip()
    if text and re.search(rf"(?<![\d,.]){re.escape(text)}(?![\d,.])",
                          big.raw_text or ""):
        return True
    return bool(small.nominal) and small.nominal == big.nominal


def _contained_duplicate(c, chars):
    a = c.target_region
    if a is None or _area(a) == 0:
        return False
    for o in chars:
        b = o.target_region
        if o is c or b is None:
            continue
        # STRICTLY larger: equal boxes keep both (see module docstring --
        # that "ties keep both" is about AREA ties, a different tie from the
        # one below). Also defer to confidence like `repeated_value_nearby`
        # does: if the CONTAINED row is the more confident read, containment
        # must not drop it -- otherwise the two rules together can delete a
        # callout entirely (this one drops the smaller box, the other drops
        # the bigger one for being less confident, and nothing survives).
        # `not (c.confidence > o.confidence)` means a CONFIDENCE tie drops
        # the contained box, the opposite choice from the area tie above --
        # known residual risk, not a bug: a merged box reading both a
        # dimension and its neighbour ("20 35") can equal-confidence-drop a
        # clean inner "20" read separately. Rare (6 drops on dev, measured
        # 2026-09-25) and cheaper than flipping the tie the other way, which
        # would instead let genuine duplicates survive at equal confidence.
        if _area(b) > _area(a) and _inter(a, b) / _area(a) >= CONTAINED_FRAC \
                and _content_related(c, o) and not (c.confidence > o.confidence):
            return True
    return False


def _same_read(c, o):
    """Two rows are "the same read, seen twice" only if kind, char_type,
    nominal and both tolerances all agree -- GD&T rows all carry nominal "0"
    by parser construction, so nominal alone collides two different frames
    stacked on the same drawing."""
    return bool(c.nominal) and (c.kind, c.char_type, c.nominal,
                                c.upper_tol, c.lower_tol) == \
        (o.kind, o.char_type, o.nominal, o.upper_tol, o.lower_tol)


def _is_local_max(o, chars):
    """o has no same-read neighbour within o's OWN reach that is strictly
    more confident. This is what makes `_repeated_value_nearby` non-
    transitive: a chain of three rows must not let the middle one's
    confidence "carry" a drop onto a row it never actually competes with."""
    a = o.target_region
    if a is None:
        return True
    for p in chars:
        if p is o or not _same_read(o, p):
            continue
        b = p.target_region
        if b is None:
            continue
        reach = NEAR_HEIGHTS * max(_height(o), _height(p), 1.0)
        if math.dist(_center(a), _center(b)) <= reach and p.confidence > o.confidence:
            return False
    return True


def _repeated_value_nearby(c, chars):
    a = c.target_region
    if a is None or not c.nominal:
        return False
    for o in chars:
        b = o.target_region
        if o is c or b is None or not _same_read(c, o):
            continue
        reach = NEAR_HEIGHTS * max(_height(c), _height(o), 1.0)
        if math.dist(_center(a), _center(b)) > reach:
            continue
        # Keep the more confident read; equal confidence keeps both. The
        # neighbour must be a LOCAL MAXIMUM, not merely more confident than
        # `c` -- otherwise a drop chains transitively through a row that is
        # itself about to be dropped, and the survivor of a stack of three
        # can end up deferring to nobody.
        if o.confidence > c.confidence and _is_local_max(o, chars):
            return True
    return False


def _no_digit(c, chars):
    return c.kind == "dimension" and not _DIGIT.search(c.raw_text or "")


def _theoretical_no_nominal(c, chars):
    return c.kind == "theoretical" and not c.nominal


def _note_kind(c, chars):
    # Also catches note_ref rows (extract.py rewrites a theoretical box whose
    # text is a bare note number to kind "note") -- intentional, not a gap:
    # a note reference is exactly as unscoreable as any other note row.
    return c.kind == "note"


def _theoretical_kind(c, chars):
    return c.kind == "theoretical"


# --- phantom drops (2026-10-06, docs/plans/2026-10-06-phantom-drops-registration.md)
# The client ranks precision over recall at ANY recall, and a third of what the
# product delivers unflagged on dev is phantoms. These fire ONLY on a row that
# would ship unflagged: drops run after the flag pass, so needs_review is
# final, and a flagged row is not delivered -- dropping it could only cost
# recall, never buy precision.

def _conf_below(threshold: float):
    def rule(c, chars):
        return not c.needs_review and c.confidence < threshold
    return rule


def _material_kind(c, chars):
    return not c.needs_review and c.kind == "material"


# A drop rule sees the characteristics, not the page, so the profile's "< 1%
# of the page diagonal" is registered as a fixed 50 px: ~1% at 300 dpi, between
# A4 (~43 px) and A3 (~61 px).
TIGHT_CLUSTER_PX = 50.0


def _centre(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _tight_cluster(c, chars):
    if c.needs_review or c.target_region is None:
        return False
    cx, cy = _centre(c.target_region)
    return any(o is not c and o.target_region is not None
               and math.dist((cx, cy), _centre(o.target_region))
               < TIGHT_CLUSTER_PX
               for o in chars)


DROP_RULES: Dict[str, Callable[[Characteristic, Sequence[Characteristic]], bool]] = {
    "conf_below_090": _conf_below(0.90),
    "conf_below_095": _conf_below(0.95),
    "conf_below_099": _conf_below(0.99),
    "material_kind": _material_kind,
    "tight_cluster": _tight_cluster,
    "empty_read": _empty_read,
    "contained_duplicate": _contained_duplicate,
    "repeated_value_nearby": _repeated_value_nearby,
    "no_digit": _no_digit,
    "theoretical_no_nominal": _theoretical_no_nominal,
    "note_kind": _note_kind,
    "theoretical_kind": _theoretical_kind,
}

# Kept 2026-09-25 (docs/plans/2026-09-25-policy-arms-result.md): each passed
# train, dev and test on every guard, individually and jointly. Joint, derived
# exactly from stored dumps: dev 131.87 -> 118.73, test 165.18 -> 149.73,
# auto-accept precision 0.53 -> 0.80 (dev). r4-control must reproduce it.
ACTIVE_FLAG_RULES: Tuple[str, ...] = ("nondim_kind", "no_tolerance")
ACTIVE_DROP_RULES: Tuple[str, ...] = ("contained_duplicate",)


def apply_flag_rules(c: Characteristic, names: Sequence[str]) -> List[str]:
    """The review reasons the named rules add for `c`, in `names` order --
    FLAG_REASONS' human text, not the bare rule name (see its docstring)."""
    return [FLAG_REASONS[n] for n in names if FLAG_RULES[n](c)]


def apply_drop_rules(chars: List[Characteristic],
                     names: Sequence[str]) -> List[Characteristic]:
    """`chars` minus every row any named rule drops, judged against the
    ORIGINAL list so the result does not depend on evaluation order."""
    rules = [DROP_RULES[n] for n in names]
    return [c for c in chars if not any(r(c, chars) for r in rules)]
