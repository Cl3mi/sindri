"""Pure rules for the review journal (design §2).

An event is one reviewer operation, numbered by the UI (`seq` from 1) and
self-contained enough to replay without the browser. An undo is not a deletion
but a `retract` of the event it undid, so the server can stay append-only and
a crashed tab loses nothing; only at seal are retracted pairs compacted away,
which is the "net operations only" retention the operator chose."""
from typing import Iterable, List, Set

EVENT_TYPES = frozenset({
    "add_row", "delete_row", "move_row", "edit_cell",
    "accept", "unaccept", "confirm_suggestions", "retract",
})

# The cells an edit_cell may touch. Defined here (not in replay.py) because
# validate_event is the gate that must refuse a forbidden field BEFORE it
# reaches disk -- replay.py imports this name so the two stay one list.
# A field like "reviewed"/"suggested"/"id" must never appear here: a cell
# edit is a VALUE change, and letting it also rewrite review state would let
# a replayed edit silently flip what the reviewer did or did not confirm.
VALUE_FIELDS = ("char_type", "nominal", "upper_tol", "lower_tol")


class JournalError(ValueError):
    """A journal that cannot be trusted for grading."""


def _is_str(v) -> bool:
    return isinstance(v, str)


def _is_number(v) -> bool:
    # bool is an int subclass; a coordinate must never silently accept one.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_event(e) -> None:
    if not isinstance(e, dict):
        raise JournalError("event is not an object")
    seq = e.get("seq")
    # bool is an int subclass; True would silently become seq 1.
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise JournalError(f"bad seq {seq!r}")
    type_ = e.get("type")
    if type_ not in EVENT_TYPES:
        raise JournalError(f"unknown event type {type_!r}")
    # validate_event is the gate the store runs on every incoming batch, so a
    # structurally broken event must be refused at append time -- where it
    # can be traced to one request -- not discovered at seal time against the
    # whole journal (a KeyError/TypeError out of replay(), or worse, a silent
    # rewrite of review state through a field edit_cell was never meant to
    # touch).
    if type_ == "retract":
        target = e.get("target")
        if (not isinstance(target, int) or isinstance(target, bool)
                or not (1 <= target < seq)):
            raise JournalError("retract needs a target seq below its own")
    elif type_ == "edit_cell":
        if not _is_str(e.get("id")):
            raise JournalError("edit_cell needs a string id")
        if e.get("field") not in VALUE_FIELDS:
            raise JournalError(f"edit_cell cannot touch field {e.get('field')!r}")
        if not _is_str(e.get("old")) or not _is_str(e.get("new")):
            raise JournalError("edit_cell needs string old/new")
    elif type_ == "move_row":
        if not _is_str(e.get("id")):
            raise JournalError("move_row needs a string id")
        xy = e.get("xy")
        if (not isinstance(xy, list) or len(xy) != 2
                or not all(_is_number(v) for v in xy)):
            raise JournalError("move_row needs xy as a 2-element number list")
    elif type_ == "delete_row":
        if not _is_str(e.get("id")):
            raise JournalError("delete_row needs a string id")
    elif type_ == "add_row":
        row = e.get("row")
        if not isinstance(row, dict) or not _is_str(row.get("id")):
            raise JournalError("add_row needs a row object with a string id")
    elif type_ in ("accept", "unaccept", "confirm_suggestions"):
        ids = e.get("ids")
        if not isinstance(ids, list) or not all(_is_str(i) for i in ids):
            raise JournalError(f"{type_} needs ids as a list of strings")


def contiguous_seq(seqs: Set[int], start: int = 0) -> int:
    """Highest n such that every seq in (start, n] is present. `start` is the
    last sealed seq: compaction leaves holes below it that are not gaps."""
    n = start
    while n + 1 in seqs:
        n += 1
    return n


def net_events(events: Iterable[dict]) -> List[dict]:
    """The operations that survive their undos, in seq order. Undo is LIFO, so
    removing a retracted event and its retraction leaves a sequence whose
    replay equals the state the reviewer actually ended in.

    Every event must already have passed validate_event (the store validates
    on append); net_events checks only cross-event consistency."""
    live = {}
    for e in sorted(events, key=lambda x: x["seq"]):
        if e["type"] == "retract":
            target = e.get("target")
            if target not in live:
                raise JournalError(f"retract of unknown or already retracted seq {target!r}")
            del live[target]
        else:
            live[e["seq"]] = e
    return list(live.values())
