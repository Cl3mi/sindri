"""The general tolerance a drawing names (ISO 2768-1), applied to dimensions
whose read carries no tolerance of its own.

Gold carries the general tolerance on nearly every row, so a dimension read
without one is wrong by construction -- `no_tolerance` exists to flag exactly
that, and on dev it fires on ~21 rows that are wrong ONLY for this reason. The
value is not missing from the drawing, it is printed once, in the title block
or notes, as "ISO 2768-m": no reader looking at a callout crop can ever see it.
So it is filled here, after the read, from the class the drawing names.

Every pattern and table cell here is FROZEN by
docs/plans/2026-10-05-general-tolerance-registration.md, priced on train before
dev and test were looked at. Changing one after a split's numbers were seen
makes that split ineligible -- edit the registration first, and re-price.

Two traps the registration names:
  * A printed tolerance the crop clipped looks exactly like "no tolerance". The
    fill then ships the general value silently, +4 per row (flag 1 -> escaped
    5). That is what the gate prices; nothing here can see it.
  * A fit (Ø20 H7) is a tolerance ISO 286 defines, not ISO 2768, and the parser
    reads it as no tolerance. `has_fit_notation` skips those on the READ text.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from app.models import Characteristic, ExtractionResult

# First class letter only; the geometric letter (H/K/L, ISO 2768-2) is
# accepted and ignored. No DIN 7168: dropped by operator decision.
GENTOL_RE = re.compile(
    r"(?<!\d)2768(?:\s*-\s*1)?\s*[-–]?\s*([fmcvFMCV])[hklHKL]?(?![A-Za-z])")

# A number then an ISO 286 fundamental deviation (I, L, O, Q, W do not exist)
# and an IT grade 01-18. Also catches "3x5" and "M8x1" -- x is a shaft letter
# -- which the registration accepts: threads and multiplicities are not
# general-tolerance rows either.
FIT_RE = re.compile(
    r"\d\s?(?:CD|EF|FG|JS|Z[ABC]|cd|ef|fg|js|z[abc]"
    r"|[A-HJKMNPR-VXYZ]|[a-hjkmnpr-vxyz])(?:01|1[0-8]|[0-9])(?![0-9])")

_D = Decimal
# (upper bound inclusive, {class: deviation or None}); the first range starts
# at 0.5 inclusive. ISO 2768-1 Table 1, linear dimensions.
_LINEAR = (
    (_D("3"), {"f": _D("0.05"), "m": _D("0.1"), "c": _D("0.2"), "v": None}),
    (_D("6"), {"f": _D("0.05"), "m": _D("0.1"), "c": _D("0.3"), "v": _D("0.5")}),
    (_D("30"), {"f": _D("0.1"), "m": _D("0.2"), "c": _D("0.5"), "v": _D("1")}),
    (_D("120"), {"f": _D("0.15"), "m": _D("0.3"), "c": _D("0.8"), "v": _D("1.5")}),
    (_D("400"), {"f": _D("0.2"), "m": _D("0.5"), "c": _D("1.2"), "v": _D("2.5")}),
    (_D("1000"), {"f": _D("0.3"), "m": _D("0.8"), "c": _D("2"), "v": _D("4")}),
    (_D("2000"), {"f": _D("0.5"), "m": _D("1.2"), "c": _D("3"), "v": _D("6")}),
    (_D("4000"), {"f": None, "m": _D("2"), "c": _D("4"), "v": _D("8")}),
)
# ISO 2768-1 Table 2, external radii and chamfer heights. Open-ended above 6.
_RADIUS = (
    (_D("3"), {"f": _D("0.2"), "m": _D("0.2"), "c": _D("0.4"), "v": _D("0.4")}),
    (_D("6"), {"f": _D("0.5"), "m": _D("0.5"), "c": _D("1"), "v": _D("1")}),
    (None, {"f": _D("1"), "m": _D("1"), "c": _D("2"), "v": _D("2")}),
)
_TABLE_FOR = {"Distance": _LINEAR, "Diameter": _LINEAR, "Radius": _RADIUS}
_MIN_NOMINAL = _D("0.5")


def _decimal(nominal) -> Optional[Decimal]:
    try:
        d = Decimal(str(nominal or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def iso2768(cls: str, nominal, char_type: str) -> Optional[Decimal]:
    """The symmetric ± deviation ISO 2768-1 assigns, or None where the table
    has no cell -- including every char_type it does not cover (angles,
    Reference, Theoretical, unknown). None is never a guess: a row it returns
    None for is left unfilled and stays flagged."""
    table = _TABLE_FOR.get(char_type)
    n = _decimal(nominal)
    if table is None or n is None or n < _MIN_NOMINAL:
        return None
    for upper, cells in table:
        if upper is None or n <= upper:
            return cells.get(cls)
    return None


def find_class(text) -> Optional[str]:
    """The first ISO 2768 class letter named in `text`, lower-cased."""
    m = GENTOL_RE.search(text or "")
    return m.group(1).lower() if m else None


def has_fit_notation(raw_text) -> bool:
    return bool(FIT_RE.search(raw_text or ""))


@dataclass(frozen=True)
class DocumentClass:
    cls: Optional[str]          # None when absent OR conflicting
    source: Optional[str]       # "title" | "notes" | "loose" | None
    conflict: bool


# What title_block.read_title_block adds to a grid cell with no caption.
# loose_text never sets it, so it is the one stored mark that tells a
# caption-less grid cell from free text.
_GRID_ONLY_REASON = "missing caption"


def _texts(result: ExtractionResult) -> List[Tuple[str, str]]:
    """(source, text) for every place a general tolerance can be stated, in
    precedence order: title grid, then notes, then loose text."""
    title, loose = [], []
    for f in result.title_block:
        is_loose = not f.label and _GRID_ONLY_REASON not in f.review_reasons
        (loose if is_loose else title).append(f"{f.label} {f.value}")
    notes = [t for n in (result.notes.notes if result.notes else ())
             for t in (n.text_en, n.text_de, n.raw_text) if t]
    return ([("title", t) for t in title] + [("notes", t) for t in notes]
            + [("loose", t) for t in loose])


def document_class(result: ExtractionResult) -> DocumentClass:
    """The one ISO 2768 class this drawing names, where it was found, and
    whether two different classes were named. A conflict yields no class:
    which of two printed classes applies is not decidable from text, and a
    guess here would be auto-accepted."""
    found, source = set(), None
    for src, text in _texts(result):
        letters = {m.group(1).lower() for m in GENTOL_RE.finditer(text)}
        if letters and source is None:
            source = src
        found |= letters
    if len(found) > 1:
        return DocumentClass(None, source, True)
    return DocumentClass(next(iter(found), None), source, False)


def _fmt(d: Decimal) -> str:
    # parse_value emits comma decimals with no trailing zeros ("0,15", "2");
    # a filled row must be indistinguishable from a printed "±" read.
    s = format(d.normalize(), "f")
    return s.replace(".", ",")


def fill_row(c: Characteristic, cls: Optional[str]) -> str:
    """Fill `c`'s tolerance from class `cls` in place when eligible, and say
    why when not. Never touches needs_review/review_reasons: the flag pass
    runs after this and decides those -- a row flagged for low confidence
    must stay flagged whatever tolerance it was given."""
    if cls is None:
        return "no_class"
    if c.kind != "dimension":
        return "not_dimension"
    if c.char_type == "Reference":
        return "reference"
    if not c.nominal:
        return "no_nominal"
    if c.upper_tol or c.lower_tol:
        return "has_tolerance"
    if has_fit_notation(c.raw_text):
        return "fit_guard"
    v = iso2768(cls, c.nominal, c.char_type)
    if v is None:
        return "table_none"
    c.upper_tol, c.lower_tol = _fmt(v), "-" + _fmt(v)
    c.tol_source = "general"
    return "filled"


def apply_general_tolerance(result: ExtractionResult) -> Dict[int, str]:
    """Fill every eligible row of one drawing in place; {pos: outcome}."""
    dc = document_class(result)
    if dc.conflict:
        return {c.pos: "conflict" for c in result.characteristics}
    return {c.pos: fill_row(c, dc.cls) for c in result.characteristics}
