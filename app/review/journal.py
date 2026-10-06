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


class JournalError(ValueError):
    """A journal that cannot be trusted for grading."""


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
    # structurally broken retract must be refused at append time -- where it
    # can be traced to one request -- not discovered at seal time against the
    # whole journal.
    if type_ == "retract":
        target = e.get("target")
        if (not isinstance(target, int) or isinstance(target, bool)
                or not (1 <= target < seq)):
            raise JournalError("retract needs a target seq below its own")


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
