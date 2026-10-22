"""Grade one sealed review against its proposal (C2 design §Grading).

Product code (no app.eval import), so HITL phase 3 can grade in-house
reviews with the same function. Pure: dicts in, dicts out.

The client's review is the truth here. A grade says what the reviewer did to
each proposed row, judged on the SEALED revision -- its rows and net_events --
because events.jsonl can hold unsealed or torn tail events (C1 state file,
'What's next' 1). Value comparison is exact string equality on the four
VALUE_FIELDS: every change arrives as an explicit edit_cell, so a difference
is the reviewer's act, and an edit undone by a later edit nets to unchanged."""
from typing import Dict, List, Optional

from app.review.journal import VALUE_FIELDS

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


def grade_session(proposal_rows: List[dict], sealed: dict) -> dict:
    final = {r["id"]: r for r in sealed.get("rows", [])}
    reasons = _last_reasons(sealed.get("net_events", []))
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
        graded.append(rec)
    proposed = {p["id"] for p in proposal_rows}
    missed = [r for i, r in final.items() if i not in proposed]
    return {"rows": graded, "missed": missed, "ungradable": None}
