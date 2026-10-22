"""Grade one sealed review against its proposal (C2 design §Grading).

Product code (no app.eval import), so HITL phase 3 can grade in-house
reviews with the same function. Pure: dicts in, dicts out.

The client's review is the truth here. A grade says what the reviewer did to
each proposed row, judged on the SEALED revision -- its rows and net_events --
because events.jsonl can hold unsealed or torn tail events (C1 state file,
'What's next' 1). Value comparison is exact string equality on the four
VALUE_FIELDS: every change arrives as an explicit edit_cell, so a difference
is the reviewer's act, and an edit undone by a later edit nets to unchanged."""
import math
from typing import Dict, List, Optional

from app.review.journal import JournalError, VALUE_FIELDS
from app.review.replay import mismatches, replay

GRADES = ("confirmed", "edited", "phantom", "suggestion_confirmed",
          "suggestion_edited", "suggestion_deleted", "suggestion_left")


def _changed_fields(a: dict, b: dict) -> List[str]:
    return [f for f in VALUE_FIELDS if (a.get(f) or "") != (b.get(f) or "")]


def _last_reasons(events: List[dict]) -> Dict[str, dict]:
    """Per row: the delete reason, and per edited FIELD the (seq, reason) of
    its last edit. A reason belongs to the field it was given on, and a later
    edit of that field without one clears it: the net edit is then no longer
    the one that was explained (10 -> 12 'misread_digit' -> 10, then a
    tolerance edit, must not read as a digit misread)."""
    out: Dict[str, dict] = {}
    for e in events:
        if e["type"] == "delete_row" and e.get("reason"):
            out.setdefault(e["id"], {})["delete"] = e["reason"]
        elif e["type"] == "edit_cell":
            edits = out.setdefault(e["id"], {}).setdefault("edit", {})
            if e.get("reason"):
                edits[e["field"]] = (e["seq"], e["reason"])
            else:
                edits.pop(e["field"], None)
    return out


def _edit_reason(r: dict, fields: List[str]) -> Optional[str]:
    """Reason of the last explained edit among the fields that net changed."""
    cands = [v for f, v in r.get("edit", {}).items() if f in fields]
    return max(cands)[1] if cands else None


# Must sit above settleBox's 0.1 px rounding (the UI rounds a journaled box to
# 0.1), so a drag settled back on its start never reads as a move, yet below
# the smallest drag the UI can produce at high zoom.
ANCHOR_MOVED_PX = 0.15


def iou(a, b) -> Optional[float]:
    if a is None or b is None:
        return None
    if a[2] <= a[0] or a[3] <= a[1] or b[2] <= b[0] or b[3] <= b[1]:
        return None  # degenerate or inverted: no meaningful overlap
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else None


def _placement(p: dict, f: Optional[dict]) -> dict:
    """Proposal -> sealed-final placement. Events carry only the NEW position
    (C1 decision 8), so the old one can only come from the proposal. Pixels in
    the page image the proposal was drawn in; campaign_grade turns them into
    mm with the drawing's render_scale. A deleted row has no placement."""
    # A move on a suggestion the client never confirmed is not a correction to
    # a delivered balloon, so it carries no placement at all.
    if f is None or (p.get("suggested") and f.get("suggested")):
        return {"bubble_px": None, "anchor_iou": None, "anchor_moved": False}
    a, b = p.get("balloon_xy"), f.get("balloon_xy")
    bubble = (round(math.dist(a, b), 3) if a is not None and b is not None else None)
    pa, fa = p.get("target_region"), f.get("target_region")
    moved = (pa is not None and fa is not None
             and any(abs(x - y) > ANCHOR_MOVED_PX for x, y in zip(pa, fa)))
    return {"bubble_px": bubble, "anchor_iou": iou(pa, fa), "anchor_moved": moved}


def _ungradable(proposal_rows: List[dict], sealed: dict) -> Optional[str]:
    """Why this record cannot be graded, or None. A record whose rows cannot be
    reproduced from its own journal is reported and never graded (design
    §Reconciliation). Only an error TYPE is ever reported, as a fixed word:
    exception text can quote a client value."""
    stored = bool(sealed.get("replay_mismatch_ids"))
    # Replayed even when a mismatch is already recorded: the recorded cause
    # stays the reported one, but a malformed journal must still be known so
    # later steps do not trip over it.
    try:
        bad = mismatches(replay(proposal_rows, sealed.get("net_events", [])),
                         sealed.get("rows", []), sealed.get("reviewed_ids", []))
    except (JournalError, KeyError, TypeError, ValueError):
        # Sealed net_events are already net, so a retract or a keyless event
        # means a malformed session; one bad drawing must not crash the grade.
        return "replay_mismatch" if stored else "replay_error"
    if stored:
        return "replay_mismatch"
    return "replay_disagrees" if bad else None


def grade_session(proposal_rows: List[dict], sealed: dict) -> dict:
    """`missed` is meaningless when `ungradable` is set (the sealed rows could
    not be trusted or even read); callers exclude such sessions. `rows` always
    has exactly one record per proposal row so counts reconcile. A corrupt
    PROPOSAL is the server's own file and is not handled here."""
    ungradable = _ungradable(proposal_rows, sealed)
    # The same events/rows replay may have refused can also break these two
    # extractions (a reason without seq, a field-less edit on an id replay
    # skips, a row without id): degrade to empty rather than crash, keeping a
    # stored replay_mismatch as the reported cause.
    try:
        final = {r["id"]: r for r in sealed.get("rows", [])}
    except (KeyError, TypeError):
        final, ungradable = {}, ungradable or "replay_error"
    try:
        reasons = _last_reasons(sealed.get("net_events", []))
    except (KeyError, TypeError):
        reasons, ungradable = {}, ungradable or "replay_error"
    reviewed = set(sealed.get("reviewed_ids", []))
    graded = []
    for p in proposal_rows:
        f = final.get(p["id"])
        r = reasons.get(p["id"], {})
        rec = {"id": p["id"], "flagged": bool(p.get("needs_review")),
               "suggested": bool(p.get("suggested")),
               "reviewed": p["id"] in reviewed, "kind": p.get("kind") or "",
               "fields": [], "reason": None}
        if p.get("suggested"):
            if f is None:
                rec["grade"] = "suggestion_deleted"
                rec["reason"] = r.get("delete")
            elif f.get("suggested"):
                rec["grade"] = "suggestion_left"
            else:
                rec["fields"] = _changed_fields(p, f)
                rec["grade"] = "suggestion_edited" if rec["fields"] else "suggestion_confirmed"
                rec["reason"] = _edit_reason(r, rec["fields"])
        elif f is None:
            rec["grade"] = "phantom"
            rec["reason"] = r.get("delete")
        else:
            rec["fields"] = _changed_fields(p, f)
            rec["grade"] = "edited" if rec["fields"] else "confirmed"
            rec["reason"] = _edit_reason(r, rec["fields"])
        rec.update(_placement(p, f))
        graded.append(rec)
    proposed = {p["id"] for p in proposal_rows}
    missed = [r for i, r in final.items() if i not in proposed]
    return {"rows": graded, "missed": missed, "ungradable": ungradable}
