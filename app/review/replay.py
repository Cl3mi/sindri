"""Replay net review operations on the proposal (design §2, §3).

The semantics mirror app/static/js/state.js operation by operation; a change
to an op there must change its branch here, or seal starts reporting replay
mismatches on healthy sessions."""
from typing import Dict, Iterable, List

# VALUE_FIELDS lives in journal.py: validate_event is the append-time gate
# that must refuse a field edit_cell was never meant to touch, so the list
# has to exist there first; re-exported here so existing callers of
# replay.VALUE_FIELDS keep working unchanged.
from app.review.journal import JournalError, VALUE_FIELDS

__all__ = ["replay", "mismatches", "note_of", "VALUE_FIELDS"]


def replay(proposal_rows: Iterable[dict], events: Iterable[dict]) -> Dict[str, dict]:
    rows = {r["id"]: {**r, "reviewed": False} for r in proposal_rows}
    for e in events:
        t = e["type"]
        if t == "add_row":
            r = e["row"]
            rows[r["id"]] = {**r, "reviewed": bool(r.get("reviewed", False))}
        elif t == "delete_row":
            rows.pop(e["id"], None)
        elif t == "move_row":
            if e["id"] in rows:
                rows[e["id"]]["balloon_xy"] = list(e["xy"])
        elif t == "edit_cell":
            if e["id"] in rows:
                rows[e["id"]][e["field"]] = e["new"]
        elif t in ("accept", "unaccept"):
            for i in e["ids"]:
                if i in rows:
                    rows[i]["reviewed"] = t == "accept"
        elif t == "flag":
            # Move to Review (review UX plan task 2): the reviewer's own flag,
            # as persistent as a pipeline one -- it blocks Approve until the
            # row is resolved. Mirrors opFlagRows in state.js: balloons only
            # (a suggestion is not one), "manual" appended once.
            for i in e["ids"]:
                r = rows.get(i)
                if r is not None and not r.get("suggested"):
                    r["needs_review"] = True
                    reasons = list(r.get("review_reasons") or [])
                    if "manual" not in reasons:
                        reasons.append("manual")
                    r["review_reasons"] = reasons
                    r["reviewed"] = False
        elif t == "confirm_suggestions":
            for i in e["ids"]:
                r = rows.get(i)
                if r is not None and r.get("suggested"):
                    r["suggested"] = False
                    r["reviewed"] = True
        elif t == "move_target":
            if e["id"] in rows:
                rows[e["id"]]["target_region"] = list(e["box"])
        elif t == "set_note":
            pass   # a drawing-level fact, not a row's: read with note_of()
        else:
            # A retract here means the caller skipped net_events(): replaying
            # it would grade an operation the reviewer undid.
            raise JournalError(f"cannot replay event type {t!r}")
    return rows


def _xy(r):
    xy = r.get("balloon_xy")
    return None if xy is None else tuple(round(float(v), 3) for v in xy)


def _box(r):
    b = r.get("target_region")
    return None if b is None else tuple(round(float(v), 3) for v in b)


def note_of(events: Iterable[dict]) -> str:
    """The drawing's note after NET events (latest set_note wins; "" if none)."""
    note = ""
    for e in events:
        if e["type"] == "retract":
            # Same refusal as replay(): the caller skipped net_events(), and
            # the note it would return may be one the reviewer undid.
            raise JournalError("cannot read a note from a raw journal: retract present")
        if e["type"] == "set_note":
            note = e["text"]
    return note


def mismatches(replayed: Dict[str, dict], final_rows: Iterable[dict],
               reviewed_ids: Iterable[str]) -> List[str]:
    """Ids whose replayed state disagrees with the exported final. `pos` is
    not compared: renumbering is derived, not an operation."""
    final = {r["id"]: r for r in final_rows}
    reviewed = set(reviewed_ids)
    bad = set(replayed) ^ set(final)
    for i in set(replayed) & set(final):
        a, b = replayed[i], final[i]
        if (any((a.get(f) or "") != (b.get(f) or "") for f in VALUE_FIELDS)
                or bool(a.get("suggested")) != bool(b.get("suggested"))
                or bool(a.get("needs_review")) != bool(b.get("needs_review"))
                or _xy(a) != _xy(b)
                or _box(a) != _box(b)
                or a["reviewed"] != (i in reviewed)):
            bad.add(i)
    return sorted(bad)
