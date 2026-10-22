"""Grade a pulled review campaign (C2 design). The client's review is the
truth; gold is a cross-check that never flips a grade.

Every output that leaves the protected root is counts + salted hashes only."""
import math
import statistics
from typing import Dict, List, Optional

MM_PER_PT = 25.4 / 72.0
IDLE_MS = 5 * 60 * 1000          # a gap longer than this is the reviewer away
BUBBLE_MOVED_MM = 0.5            # below this a "move" is a nudge of the mouse
FAR_MOVE_MM = 5.0
NUDGE_IOU = 0.3

VALUE_GRADES = ("confirmed", "edited", "phantom")
SUGGESTION_GRADES = ("suggestion_confirmed", "suggestion_edited",
                     "suggestion_deleted", "suggestion_left")


def px_to_mm(px: float, render_scale: float) -> float:
    """render_scale is page-image px per PDF point (campaign.json), and one
    PDF point is 25.4/72 mm, so px / scale is points and * MM_PER_PT is mm.
    Dividing by dpi instead would silently be wrong whenever the render was
    clamped below the nominal dpi."""
    return px / render_scale * MM_PER_PT


def review_time_ms(events: List[dict]) -> int:
    """Active review time: gaps between consecutive events within a page
    session. t_ms restarts at each page load (C1 state 'What's next' 5), so
    a decrease starts a new session and that gap is not counted; t_wall is
    the client's clock and is never used. Events without a numeric t_ms are
    skipped rather than raising: a torn or foreign record must not sink the
    drawing's timing."""
    total, prev = 0, None
    for e in events:
        t = e.get("t_ms")
        # bool is an int subclass; True would read as 1 ms.
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            continue
        if prev is not None and 0 <= t - prev <= IDLE_MS:
            total += t - prev
        prev = t
    return int(total)


def _ratio(n, d):
    # None, never 0.0: an empty denominator is "not measured", and a
    # plausible-looking zero would be quoted as a result (CLAUDE.md §4).
    return (n / d) if d else None


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def group_metrics(drawings: List[dict]) -> dict:
    """drawings: [{"rows": grade_session rows, "missed": [...], "scale": render_scale,
    "time_ms": int}] for ONE group, gradable drawings only."""
    counts: Dict[str, int] = {g: 0 for g in VALUE_GRADES + SUGGESTION_GRADES}
    counts["missed"] = 0
    flagged = flagged_wrong = 0
    unflagged = {g: 0 for g in VALUE_GRADES}
    moves_mm, moved, far, placed = [], 0, 0, 0
    nudged = repointed = anchor_unmeasurable = 0
    for d in drawings:
        counts["missed"] += len(d["missed"])
        for r in d["rows"]:
            g = r["grade"]
            counts[g] += 1
            # Value grades only: a suggestion was never shipped, so a flag on
            # it says nothing about whether the flag was right.
            if g in VALUE_GRADES:
                if r["flagged"]:
                    flagged += 1
                    flagged_wrong += g != "confirmed"
                else:
                    unflagged[g] += 1
            # bubble_px is None for a deleted row or an unconfirmed
            # suggestion: no final position exists, so it is not evidence
            # that the bubble stayed put and must not enter the denominator.
            if r.get("bubble_px") is not None:
                placed += 1
                mm = px_to_mm(r["bubble_px"], d["scale"])
                if mm > BUBBLE_MOVED_MM:
                    moved += 1
                    moves_mm.append(mm)
                    far += mm > FAR_MOVE_MM
            # anchor_iou None = no anchor to compare (unmeasurable), which is
            # different from "measured and unmoved"; report it so the digest
            # can say how many rows the anchor figures could not speak for.
            if r.get("anchor_iou") is None:
                anchor_unmeasurable += 1
            elif r.get("anchor_moved"):
                if r["anchor_iou"] >= NUDGE_IOU:
                    nudged += 1
                else:
                    repointed += 1
    graded_balloons = sum(counts[g] for g in VALUE_GRADES)
    surviving = counts["confirmed"] + counts["edited"]
    # Tray rows are values the pipeline found but did not ship; the client
    # recovering them is recall the shipped balloons alone understate.
    tray_found = counts["suggestion_confirmed"] + counts["suggestion_edited"]
    times = [d["time_ms"] / 60000 for d in drawings if d.get("time_ms")]
    return {
        "n_drawings": len(drawings),
        "counts": counts,
        "precision": _ratio(counts["confirmed"], graded_balloons),
        "unflagged_precision": _ratio(unflagged["confirmed"], sum(unflagged.values())),
        "flag_precision": _ratio(flagged_wrong, flagged),
        "recall_lower_bound": _ratio(surviving, surviving + counts["missed"]),
        "recall_with_tray": _ratio(surviving + tray_found,
                                   surviving + tray_found + counts["missed"]),
        "placement": {"placed": placed, "bubble_moved": moved,
                      "bubble_moved_share": _ratio(moved, placed),
                      "bubble_mm_median": statistics.median(moves_mm) if moves_mm else None,
                      "bubble_mm_p90": _pct(moves_mm, 0.9),
                      "bubble_far_share": _ratio(far, placed),
                      "anchor_nudged": nudged, "anchor_repointed": repointed,
                      "anchor_unmeasurable": anchor_unmeasurable},
        "review_minutes_median": statistics.median(times) if times else None,
    }


def reweight(by_group: Dict[str, Optional[float]], pool: Dict[str, int]) -> Optional[float]:
    """Pooled value at the selection POOL's great:problem mix (a 60/40 pick is
    not representative). None if any group lacks the value, or the pool has no
    weight for a group present: an absent group would silently re-weight the
    rest."""
    weights = {g: pool.get(g) for g in by_group}
    if (not by_group or any(v is None for v in by_group.values())
            or any(not w for w in weights.values())):
        return None
    total = sum(weights.values())
    return sum(v * weights[g] for g, v in by_group.items()) / total
