"""Grade a pulled review campaign (C2 design). The client's review is the
truth; gold is a cross-check that never flips a grade.

Every output that leaves the protected root is counts + salted hashes only."""
import math
import statistics
from typing import Dict, List, Optional

from pydantic import ValidationError

from app.eval.dump import to_points
from app.eval.matching import Cand, match_candidates
from app.eval.models import GoldDoc, MatchParams
from app.eval.score import _compare_fields
from app.models import Characteristic

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


# Two sources' page sizes may differ by rounding (PyMuPDF vs the ingest's
# rect), never by more than this; anything larger is a different sheet.
FRAME_TOL_PT = 1.0


def _rect_ok(rect) -> bool:
    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(v) for v in (x0, y0, x1, y1)) and x1 > x0 and y1 > y0


def _same_frame(a, b) -> bool:
    """Gold must be the `--originals` ingest, whose page_rect is the clean
    ORIGINAL's, i.e. the page the campaign rendered. When the two rects
    differ, every distance mixes two frames; that once silently cost 141
    matches, so a mismatch refuses the whole cross-check instead of producing
    a number that looks like a result. All four coordinates are compared: a
    shifted origin moves every gold position just as a resize does.
    Blind spot: gold ingested WITHOUT --originals from a stamped sheet of the
    same size passes any rect check, so the caller (Task 7 / runner help) must
    insist on the --originals gold dir."""
    if not (_rect_ok(a) and _rect_ok(b)):
        return False
    return all(abs(a[k] - b[k]) <= FRAME_TOL_PT for k in range(4))


def _center_pt(row, render_scale, page_rect):
    """Centre of a row's target_region in PDF points, or None when the region
    is unusable (missing, wrong arity, non-numeric, non-finite). Same
    conversion as score._center_pt applies to a dump's characteristics."""
    box = row.get("target_region")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) for v in box):
        return None
    b = to_points(box, render_scale, page_rect)
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _as_characteristic(row) -> Optional[Characteristic]:
    # Rows carry UI extras (reviewed, ...); only the model's own fields are
    # kept. A row that still refuses is reported as such, and the exception is
    # dropped unread: pydantic's message quotes the offending client value.
    try:
        return Characteristic.model_validate(
            {k: v for k, v in row.items() if k in Characteristic.model_fields})
    except (ValidationError, TypeError, ValueError):
        return None


def _match(located, gold_chars, diag, params) -> Dict[int, int]:
    """located: [(index, center_pt, nominal)]. Keys are list INDICES, not row
    ids, so a duplicate or missing id can never make match_candidates raise.
    Returns {index: gold balloon}."""
    # The scorer keys by pos, so an EXACT cost tie may break differently here;
    # acceptable, since ties need identical distances and value bonuses.
    pairs = match_candidates(
        [Cand(key=i, center_pt=c, nominal=n) for i, c, n in located],
        [Cand(key=g.balloon, center_pt=g.position_pt, nominal=g.nominal)
         for g in gold_chars], diag, params)
    return {pk: gk for pk, gk, _ in pairs}


def gold_cross_check(proposal: List[dict], final: List[dict], grades: Dict[str, str],
                     gold: GoldDoc, *, render_scale: float, page_rect,
                     params: Optional[MatchParams] = None) -> dict:
    """Gold as a second lens (design §Gold cross-check). Never changes a grade.

    Proposal balloons -> gold verdict, crossed with the client's grade in
    `matrix` as "<verdict>&<grade>":
      gold_correct / gold_wrong  matched a scored gold row; fields agree or not
      gold_phantom               no scored gold row pairs with it
      gold_unlocatable           no usable target_region, so it cannot be matched
      row_invalid                not a valid Characteristic, so not comparable
    Suggestions are skipped on both sides: they were never delivered, so gold
    has nothing to say about them and they cannot count as found.
    Final rows -> `matched_final` and `neither_found` (scored gold rows no
    final row pairs with). Matching is the scorer's own, in PDF points, so
    'phantom' means exactly what it means in a score report.

    Output is counts, fixed words and the per-row verdict keyed by row id;
    nothing in it is a client value."""
    params = params or MatchParams()
    # Reconciling frames would put gold in a third frame this function never
    # converts rows into; the frame check already requires the pages to agree.
    if params.reconcile_frames != "none":
        return {"computed": False, "reason": "unsupported_params"}
    if not _same_frame(gold.page_rect, page_rect):
        return {"computed": False, "reason": "frame_mismatch"}
    if (isinstance(render_scale, bool) or not isinstance(render_scale, (int, float))
            or not math.isfinite(render_scale) or render_scale <= 0):
        return {"computed": False, "reason": "bad_render_scale"}
    gold_chars = [g for g in gold.characteristics if g.kind in params.score_kinds]
    # match_candidates would raise on a repeated balloon number, and by_num
    # would silently keep only one of them.
    if len({g.balloon for g in gold_chars}) != len(gold_chars):
        return {"computed": False, "reason": "gold_invalid"}
    by_num = {g.balloon: g for g in gold_chars}
    # The gold page's diagonal, as score() uses when frames are not reconciled;
    # _same_frame has just established the two agree.
    diag = math.dist(gold.page_rect[:2], gold.page_rect[2:])

    delivered = [r for r in proposal if not r.get("suggested")]
    # Verdicts and grades are keyed by id: two rows sharing one (or rows with
    # none) would merge in the matrix without a trace, so refuse instead.
    ids = [r.get("id") for r in delivered]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        return {"computed": False, "reason": "duplicate_ids"}
    verdicts: Dict[str, str] = {}
    located, chars = [], {}
    unlocatable = invalid = 0
    for i, r in enumerate(delivered):
        c = _center_pt(r, render_scale, page_rect)
        if c is None:
            verdicts[r.get("id")] = "gold_unlocatable"
            unlocatable += 1
            continue
        ch = _as_characteristic(r)
        if ch is None:
            verdicts[r.get("id")] = "row_invalid"
            invalid += 1
            continue
        chars[i] = ch
        located.append((i, c, ch.nominal))
    pm = _match(located, gold_chars, diag, params)
    for i, _c, _n in located:
        gk = pm.get(i)
        if gk is None:
            v = "gold_phantom"
        else:
            v = "gold_wrong" if _compare_fields(chars[i], by_num[gk]) else "gold_correct"
        verdicts[delivered[i].get("id")] = v
    matrix: Dict[str, int] = {}
    for r in delivered:
        # A row the grader did not grade is said so, not dropped: the matrix
        # must keep partitioning the delivered proposal rows.
        key = f"{verdicts[r.get('id')]}&{grades.get(r.get('id'), 'ungraded')}"
        matrix[key] = matrix.get(key, 0) + 1

    final_located, final_unlocatable = [], 0
    for i, r in enumerate(f for f in final if not f.get("suggested")):
        c = _center_pt(r, render_scale, page_rect)
        if c is None:
            final_unlocatable += 1
            continue
        n = r.get("nominal")
        # canon_value expects text; a non-string nominal only loses the value
        # bonus / value fallback, it does not make the row unmatchable.
        final_located.append((i, c, n if isinstance(n, str) else ""))
    fm = _match(final_located, gold_chars, diag, params)
    found = set(fm.values())
    return {"computed": True, "verdicts": verdicts, "matrix": matrix,
            "unlocatable": unlocatable, "row_invalid": invalid,
            "final_unlocatable": final_unlocatable,
            "matched_final": len(found),
            "neither_found": len(set(by_num) - found),
            "n_gold": len(gold_chars)}
