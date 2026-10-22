"""Which example rows the operator's drill-down shows per correction bucket
(C2 design, Operator drill-down).

PURE: it reads a report `grade_campaign` returned and nothing else. Its output
holds client values (proposed/final rows, notes) and so lives only in the
operator's deck inside the protected root -- never docs/eval."""
from typing import Dict, List, Optional

from app.eval.campaign_grade import (_phantom_reason, px_to_mm, ranked_findings,
                                     row_buckets)
from app.review.journal import EDIT_REASONS

DEFAULT_PER_BUCKET = 12


def _reason_word(grade, reason) -> str:
    """A closed-vocabulary word, never the record's own string: the sealed
    journal is read raw, so a reason is only shown when it is one of the
    journal's words. Edits and deletes have different vocabularies."""
    if grade in ("edited", "suggestion_edited", "suggestion_confirmed"):
        if reason is None:
            return "none"
        return reason if isinstance(reason, str) and reason in EDIT_REASONS else "other"
    return _phantom_reason(reason)


def _by_id(rows) -> Dict[object, dict]:
    return {r.get("id"): r for r in rows or [] if isinstance(r, dict)}


def examples(report: dict, per_bucket: int = DEFAULT_PER_BUCKET) -> dict:
    """-> {"buckets": {bucket: {"total": n, "examples": [...]}}, "order": [...]}.

    Buckets come from `row_buckets`, the same definition `ranked_findings`
    counts with, so each `total` equals the digest's count for that bucket
    and `order` is the digest's ranked order (value table, then placement).
    Examples are capped at `per_bucket`; the total says what the cap hid.

    Pick order is deterministic: placement by displacement (bubble, farthest
    first) or overlap (anchors, least first), value buckets by report order
    then row id. Ungradable and pending drawings carry no rows and are
    skipped."""
    drawings = [d for d in report.get("drawings", []) if d.get("rows") is not None]
    # {bucket: [(sort_key, example)]}
    found: Dict[str, List[tuple]] = {}

    def add(bucket, key, d, row_id, grade, reason, prop, fin, mm, verdict):
        found.setdefault(bucket, []).append((key, {
            "doc_hash": d.get("doc_hash"), "group": d.get("group"), "row_id": row_id,
            "grade": grade, "reason": reason, "proposed": prop, "final": fin,
            "mm": mm, "scale": d.get("scale"), "verdict": verdict,
            "note": d.get("note") or ""}))

    for di, d in enumerate(drawings):
        scale = d["scale"]
        proposal, final = _by_id(d.get("proposal")), _by_id(d.get("final"))
        cross = d.get("cross")
        verdicts = (cross.get("verdicts") if isinstance(cross, dict)
                    and cross.get("computed") is True else None) or {}

        def verdict(rid) -> Optional[str]:
            v = verdicts.get(rid)
            return v if isinstance(v, str) else None

        for r in d["rows"]:
            rid = r["id"]
            mm = (px_to_mm(r["bubble_px"], scale) if r.get("bubble_px") is not None
                  else None)
            for kind, bucket in row_buckets(r, scale):
                if bucket == "bubble_moved":
                    key = (-mm, di, str(rid))
                elif kind == "placement":
                    key = (r["anchor_iou"], di, str(rid))
                else:
                    key = (di, str(rid))
                add(bucket, key, d, rid, r["grade"], _reason_word(r["grade"], r.get("reason")),
                    proposal.get(rid), final.get(rid), mm, verdict(rid))
        for m in d["missed"]:
            rid = m.get("id")
            # Added by the client, so there is no proposal and no reason.
            add("missed", (di, str(rid)), d, rid, "missed", "none", None, m, None,
                verdict(rid))

    buckets = {}
    for bucket, items in found.items():
        items.sort(key=lambda kv: kv[0])
        buckets[bucket] = {"total": len(items),
                           "examples": [e for _, e in items[:per_bucket]]}
    ranked = ranked_findings(drawings)
    order = [r["bucket"] for kind in ("value", "placement") for r in ranked[kind]]
    return {"buckets": {b: buckets[b] for b in order}, "order": order}
