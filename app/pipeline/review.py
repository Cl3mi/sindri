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
# 0.8, raised from the 0.6 inherited from the Tesseract-era UI. Measured on the
# dev split, not chosen: the 0.6-0.8 band held 18 matched pairs at a 100% error
# rate with ZERO correct rows, and all 24 pairs below 0.8 were wrong. Flagging
# that band converts 15 silent wrong values (w=5) into flagged ones (w=1) and
# flags no correct row -- -3.00 mean review cost for nothing given up.
# Not higher: 284 of 308 pairs sit at >=0.8 and 112 of those are correct, so the
# next band up would charge for 112 correct rows to catch 114 escaped ones.
# The VLM's confidence is saturated, so this is a two-band decision, not a curve.
LOW_CONF = 0.8


def active_review_policy() -> dict:
    """The review policy in effect, for `RunConfig.extra` at predict time.

    The same job `detect.active_knobs` does, and it exists for the same reason:
    two runs differing only in this threshold otherwise produce
    byte-indistinguishable RunConfigs. `r3-awqcontrol` and `baseline-dev` share
    model_id, dpi, prompt_sha256 and every detection knob while differing by a
    constant worth 3.00 review cost -- so `compare_runs` would not warn, and
    `_reusable_dump`, which compares the whole RunConfig, could skip documents
    as "already predicted" straight across the change. That exact failure has
    been paid for once here already, when detection knobs went unrecorded.

    A dump with no `review_low_conf` key PREDATES the field; it does not mean
    0.6. Same discipline as `DocScore.frame_origin_frac` being None rather than
    a plausible-looking 0.0."""
    return {"review_low_conf": LOW_CONF}


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
