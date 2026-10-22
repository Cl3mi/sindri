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


def _last_reasons(events: List[dict]) -> Dict[str, Dict[str, str]]:
    """The last reason given per row, per kind of act ('edit' / 'delete')."""
    out: Dict[str, Dict[str, str]] = {}
    for e in events:
        if e.get("reason") and e["type"] in ("edit_cell", "delete_row"):
            kind = "edit" if e["type"] == "edit_cell" else "delete"
            out.setdefault(e["id"], {})[kind] = e["reason"]
    return out


def grade_session(proposal_rows: List[dict], sealed: dict) -> dict:
    final = {r["id"]: r for r in sealed.get("rows", [])}
    reasons = _last_reasons(sealed.get("net_events", []))
    graded = []
    for p in proposal_rows:
        f = final.get(p["id"])
        r = reasons.get(p["id"], {})
        rec = {"id": p["id"], "flagged": bool(p.get("needs_review")),
               "suggested": bool(p.get("suggested")), "kind": p.get("kind") or "",
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
                rec["reason"] = r.get("edit") if rec["fields"] else None
        elif f is None:
            rec["grade"] = "phantom"
            rec["reason"] = r.get("delete")
        else:
            rec["fields"] = _changed_fields(p, f)
            rec["grade"] = "edited" if rec["fields"] else "confirmed"
            rec["reason"] = r.get("edit") if rec["fields"] else None
        graded.append(rec)
    proposed = {p["id"] for p in proposal_rows}
    missed = [r for i, r in final.items() if i not in proposed]
    return {"rows": graded, "missed": missed, "ungradable": None}
