"""Grade a pulled review campaign (C2 design). The client's review is the
truth; gold is a cross-check that never flips a grade.

Every output that leaves the protected root is counts + salted hashes only."""
import math
import statistics
from typing import Dict, List, Optional

MM_PER_PT = 25.4 / 72.0
IDLE_MS = 5 * 60 * 1000          # a gap longer than this is the reviewer away
# Kept at 0.5: at fit zoom the UI's 3-screen-px dead zone is already ~1.3 mm,
# so this filters nothing there; at high zoom it filters sub-mm drags.
# Arrow-key nudges are deliberate small moves we WANT counted.
BUBBLE_MOVED_MM = 0.5
FAR_MOVE_MM = 5.0
NUDGE_IOU = 0.3

VALUE_GRADES = ("confirmed", "edited", "phantom")
SUGGESTION_GRADES = ("suggestion_confirmed", "suggestion_edited",
                     "suggestion_deleted", "suggestion_left")
# Grades whose row survives into the final revision, hence has a final anchor.
SURVIVING_GRADES = ("confirmed", "edited", "suggestion_confirmed", "suggestion_edited")


def px_to_mm(px: float, render_scale: float) -> float:
    """render_scale is page-image px per PDF point (campaign.json), and one
    PDF point is 25.4/72 mm, so px / scale is points and * MM_PER_PT is mm.
    Dividing by dpi instead would silently be wrong whenever the render was
    clamped below the nominal dpi."""
    return px / render_scale * MM_PER_PT


def review_time_ms(events: List[dict]) -> int:
    """Active review time: gaps between consecutive events within a page
    session. The input is the COMPACTED net events.jsonl: the seal drops
    retracted pairs and their timing, plus the lead-in before the first event
    and the tail before Approve, so the result is a LOWER bound, and a drawing
    with fewer than 2 net events has no time (0). t_ms restarts at each open of the drawing (journal.start/resume;
    C1 state 'What's next' 5), so a decrease starts a new session and that gap
    is not counted. A boundary is only detected when t_ms DECREASES: an open
    after a long pause with a higher t_ms inflates one gap by at most that
    session's lead-in; t_wall is
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
    counts["confirmed_explicit"] = counts["unflagged_confirmed_explicit"] = 0
    flagged = flagged_wrong = 0
    unflagged = {g: 0 for g in VALUE_GRADES}
    moves_mm, moved, far, placed = [], 0, 0, 0
    nudged = repointed = anchor_measured = anchor_unmeasurable = 0
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
            if g == "confirmed" and r.get("reviewed"):
                counts["confirmed_explicit"] += 1
                counts["unflagged_confirmed_explicit"] += not r["flagged"]
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
            # Only surviving rows have a final anchor: counting deleted rows
            # or unconfirmed suggestions as "unmeasurable" would bury the real
            # ones. anchor_iou None on a survivor is unmeasurable, which is
            # different from "measured and unmoved".
            if g in SURVIVING_GRADES:
                if r.get("anchor_iou") is None:
                    anchor_unmeasurable += 1
                else:
                    anchor_measured += 1
                    if r.get("anchor_moved"):
                        if r["anchor_iou"] >= NUDGE_IOU:
                            nudged += 1
                        else:
                            repointed += 1
    graded_balloons = sum(counts[g] for g in VALUE_GRADES)
    surviving = counts["confirmed"] + counts["edited"]
    # Tray rows are values the pipeline found but did not ship; the client
    # recovering them is recall the shipped balloons alone understate.
    tray_found = counts["suggestion_confirmed"] + counts["suggestion_edited"]
    counts["confirmed_silent"] = counts["confirmed"] - counts["confirmed_explicit"]
    times = [d["time_ms"] / 60000 for d in drawings if d.get("time_ms")]
    # One shared denominator for both recalls: everything the client is known
    # to have wanted. These are UPPER bounds whenever the client's manual
    # additions are incomplete (the gold cross-check's 'neither found' covers
    # what they did not add).
    known = surviving + tray_found + counts["missed"]
    return {
        "n_drawings": len(drawings),
        "counts": counts,
        # precision / unflagged_precision include SILENT confirmations (rows
        # the client never touched); the digest shows the explicit/silent split.
        "precision": _ratio(counts["confirmed"], graded_balloons),
        "unflagged_precision": _ratio(unflagged["confirmed"], sum(unflagged.values())),
        "flag_precision": _ratio(flagged_wrong, flagged),
        "recall_shipped_vs_client_known": _ratio(surviving, known),
        "recall_with_tray_vs_client_known": _ratio(surviving + tray_found, known),
        "placement": {"placed": placed, "bubble_moved": moved,
                      "bubble_moved_share": _ratio(moved, placed),
                      "moved_bubble_mm_median": statistics.median(moves_mm) if moves_mm else None,
                      "moved_bubble_mm_p90": _pct(moves_mm, 0.9),
                      # moved_* are over MOVED rows only; the far share's
                      # denominator is `placed`.
                      "bubble_far_share": _ratio(far, placed),
                      "anchor_nudged": nudged, "anchor_repointed": repointed,
                      "anchor_measured": anchor_measured,
                      "anchor_unmeasurable": anchor_unmeasurable},
        "n_timed": len(times), "time_not_measured": len(drawings) - len(times),
        "review_minutes_lower_bound_median": statistics.median(times) if times else None,
    }


def reweight(by_group: Dict[str, Optional[float]], pool: Dict[str, int]) -> Optional[float]:
    """Pooled value at the selection POOL's great:problem mix (a 60/40 pick is
    not representative). Iterates the POOL: None if by_group lacks a pool group
    with weight, has None for one, or carries a surprise group the pool does
    not weight, since a silently absent group would re-weight the rest.
    Weighting a per-row ratio by drawing counts is an approximation."""
    groups = [g for g, w in pool.items() if w]
    if (not groups or any(by_group.get(g) is None for g in groups)
            or any(g not in groups for g in by_group)):
        return None
    return sum(by_group[g] * pool[g] for g in groups) / sum(pool[g] for g in groups)
