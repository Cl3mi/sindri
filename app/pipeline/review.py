"""The needs-review policy: one pure function mapping a row's observed extraction
facts to a flag + human-readable reasons. The single home for this policy so it
can be understood and tested in isolation."""
from typing import List, Optional, Set, Tuple

from app.models import Characteristic

# Measurement types that must carry a numeric nominal; a non-empty read that
# parses to no nominal for one of these is a garbled-value read worth flagging.
# Strings MUST match the char_type constants in parser.py exactly.
# Exempt by design: GD&T/Flatness/Position (parser forces nominal "0"), Note and
# Material (nominal holds text; an empty one is caught by the "empty read" rule),
# and Reference (parser only assigns it when a number was parsed, so it can never
# reach an empty nominal here).
DIMENSION_TYPES = {"Distance", "Diameter", "Radius", "Theoretical"}
LOW_CONF = 0.6


def review_flags(c: Characteristic, rotation_ambiguous: bool,
                 known_note_positions: Optional[Set[int]] = None) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a populated Characteristic.

    `known_note_positions`, when provided, is the set of top-level note pos
    values present in the parsed notes block. A note_ref Characteristic
    pointing outside that set is flagged 'unknown note reference'.

    Gating: an empty read is its own reason and does not also report
    'missing nominal' or 'low OCR confidence'."""
    reasons: List[str] = []
    text = (c.raw_text or "").strip()
    if not text:
        reasons.append("empty read")
    elif c.confidence < LOW_CONF:
        reasons.append("low OCR confidence")
    if text and c.char_type in DIMENSION_TYPES and not c.nominal:
        reasons.append("missing nominal")
    if rotation_ambiguous:
        reasons.append("rotation ambiguity")
    if c.subtype == "note_ref" and known_note_positions is not None:
        if c.note_ref_pos is None or c.note_ref_pos not in known_note_positions:
            reasons.append("unknown note reference")
    return bool(reasons), reasons
