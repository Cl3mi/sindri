"""Re-derive what today's post-read pipeline would have produced for a
prediction dump already on disk, without a GPU.

`app/pipeline/extract.py`'s read loop does four things to a stored dump's
raw material after the actual read: parse `raw_text` (`parser.parse_value`),
derive `needs_review`/`review_reasons` (`review.review_flags`), then flag and
drop rows under the ACTIVE policy rules (`extract._apply_active_policy`).
Every one of those steps is a pure function of fields a dump already stores,
so replaying them offline reproduces exactly what a fresh predict run on
current code would have written for that document -- a DERIVED number, exact
for these post-read changes, that a later GPU run must still confirm for
anything the read stage itself might have done differently.

This is the SECOND place `app.eval` deliberately imports pipeline internals
that move under tuning (the first is `app/eval/reparse.py`, which prices a
parser change alone). Unlike `reparse.py`, this module imports
`app.pipeline.extract` directly rather than duplicating its `_HINTS` map --
`extract` is already on the import path here because `_apply_active_policy`
lives there too, so there is nothing left to protect by keeping a second copy
in sync by hand. `score --reparse-check`'s CPU-only path is unaffected: it
still imports only `parser`, and this module is only ever imported lazily,
from `--reapply-policy`."""
import copy

from app.eval.models import PredictionDump
from app.pipeline.extract import _apply_active_policy, _HINTS
from app.pipeline.parser import parse_value
from app.pipeline.review import review_flags

# Fields the read loop sets OUTSIDE parse_value -- i.e. never touched by a
# reparse, and must survive it unchanged. `kind`/`subtype` are the FINAL
# detector kind/subtype extract.py settled on (including the theoretical ->
# note_ref relabel), which is also what `_HINTS.get(c.kind)` must be keyed on
# to reproduce the hint extract used -- see the module docstring on note_ref.
_RESTORE_FIELDS = ("pos", "id", "kind", "subtype", "source", "confidence",
                   "target_region", "balloon_xy", "note_ref_pos")


def _known_note_positions(result):
    """Mirrors extract.py's `known_positions`: top-level note `pos` values, or
    None when there is no notes block at all (never an empty set -- the two
    mean different things to `review_flags`'s "unknown note reference" rule)."""
    if result.notes is None:
        return None
    return {n.pos for n in result.notes.notes if n.parent_pos is None}


def reapply_current_code(dump: PredictionDump) -> PredictionDump:
    """A copy of `dump` as today's post-read code would have emitted it.

    Never mutates `dump`. For each characteristic: re-parse `raw_text` with
    the hint its (already final) `kind` implies, restore the fields
    `parse_value` never sets, re-derive `needs_review`/`review_reasons` with
    `review_flags` (since those depend on the parse -- a stale flag would
    silently misdescribe a row the parser now reads differently), and finally
    apply today's ACTIVE flag/drop rules the same way `extract.py` does."""
    dump = copy.deepcopy(dump)
    known_positions = _known_note_positions(dump.result)
    new_chars = []
    for c in dump.result.characteristics:
        # "rotation ambiguity" is the one review reason `review_flags` cannot
        # derive from the Characteristic alone -- it comes from a read-time
        # signal extract.py passes in directly -- so it is read back off the
        # stored reasons rather than recomputed.
        rotation_ambiguous = "rotation ambiguity" in c.review_reasons
        fresh = parse_value(c.raw_text or "", hint=_HINTS.get(c.kind or "", ""))
        for field in _RESTORE_FIELDS:
            setattr(fresh, field, getattr(c, field))
        fresh.needs_review, fresh.review_reasons = review_flags(
            fresh, rotation_ambiguous, known_note_positions=known_positions)
        new_chars.append(fresh)
    dump.result.characteristics = _apply_active_policy(new_chars)
    return dump
