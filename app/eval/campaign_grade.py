"""Grade a pulled review campaign (C2 design). The client's review is the
truth; gold is a cross-check that never flips a grade.

Every output that leaves the protected root is counts + salted hashes only."""
import math
import re
import statistics
from collections import Counter
from typing import Dict, List, Optional

from pydantic import ValidationError

from app.eval.dump import to_points
from app.eval.matching import Cand, match_candidates
from app.eval.models import GoldDoc, MatchParams
from app.eval.score import _compare_fields
from app.models import Characteristic
from app.review.grading import grade_session
from app.review.journal import DELETE_REASONS
from app.review.replay import note_of

# The first key of each output, and what makes an existing file replaceable.
REPORT_KIND = "campaign-grade-report"
DIGEST_KIND = "campaign-grade-digest"

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
            # Non-finite is no measurement either (a hand-built or pre-guard
            # row): it would put Infinity/NaN into the digest, which is not JSON.
            mm = (px_to_mm(r["bubble_px"], d["scale"])
                  if r.get("bubble_px") is not None else None)
            if mm is not None and math.isfinite(mm):
                placed += 1
                if mm > BUBBLE_MOVED_MM:
                    moved += 1
                    moves_mm.append(mm)
                    far += mm > FAR_MOVE_MM
            # Only surviving rows have a final anchor: counting deleted rows
            # or unconfirmed suggestions as "unmeasurable" would bury the real
            # ones. anchor_iou None on a survivor is unmeasurable, which is
            # different from "measured and unmoved".
            if g in SURVIVING_GRADES:
                if r.get("anchor_iou") is None or not math.isfinite(r["anchor_iou"]):
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
    # The counts behind every precision ratio, so each one can be recomputed
    # (and checked) from the digest alone. unflagged_* + flagged partition
    # the graded balloons; grade_campaign checks that.
    counts["flagged"], counts["flagged_wrong"] = flagged, flagged_wrong
    for g in VALUE_GRADES:
        counts[f"unflagged_{g}"] = unflagged[g]
    times = [d["time_ms"] / 60000 for d in drawings if d.get("time_ms")]
    # One shared denominator for both recalls: everything the client is known
    # to have wanted. These are UPPER bounds whenever the client's manual
    # additions are incomplete (the gold cross-check's 'neither found' covers
    # what they did not add).
    known = surviving + tray_found + counts["missed"]
    return {
        "n_drawings": len(drawings),
        "counts": counts,
        # precision / unflagged_precision include SILENT confirmations: approval
        # only requires the FLAGGED rows to be resolved, so an unflagged row
        # the client never looked at counts as confirmed. They are upper
        # bounds; the *_explicit_only values count only rows the client
        # explicitly accepted and are the matching lower bounds.
        "precision": _ratio(counts["confirmed"], graded_balloons),
        "precision_explicit_only": _ratio(counts["confirmed_explicit"], graded_balloons),
        "unflagged_precision": _ratio(unflagged["confirmed"], sum(unflagged.values())),
        "unflagged_precision_explicit_only": _ratio(
            counts["unflagged_confirmed_explicit"], sum(unflagged.values())),
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


def _report_page_rect(rect) -> Optional[List[float]]:
    """The campaign's page frame for the REPORT (the operator's deck turns px
    into points with it and checks the original is the same page), or None.
    Type-checked before _rect_ok: float() would take a bool or a digit
    string, and a guessed frame would put every crop somewhere else."""
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in rect)
            or not _rect_ok(rect)):
        return None
    return [float(v) for v in rect]


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
            "n_gold": len(gold_chars),
            # What the matrix must sum to; stated here so the caller can
            # check the partition instead of assuming it.
            "n_delivered": len(delivered)}

# --- ranked findings and the campaign grade ----------------------------------
# The digest is committed to docs/eval/, but almost every string the grader
# sees comes from files a client session wrote or a pull re-read: reasons,
# groups, run names, record keys. None of them is trusted to be a word. Each
# reaches the digest only through a closed vocabulary or a shape check, so a
# damaged or foreign record can at worst become "other"/"unknown", never quote
# a value.
_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
# Salted doc hashes and the two dir shapes pull.py packs under (nohash-<id8>,
# <hash>-<id8> on a collision).
_KEY = re.compile(r"\A(?:[0-9a-f]{8}|nohash-[0-9a-f]{8}|[0-9a-f]{8}-[0-9a-f]{8})\Z")


def _token(s, fallback=None):
    return s if isinstance(s, str) and _TOKEN.match(s) else fallback


def _safe_key(k) -> str:
    return k if isinstance(k, str) and _KEY.match(k) else "invalid_key"


def _phantom_reason(reason) -> str:
    # replay() does not validate reasons, and the seal is read raw: only the
    # journal's own closed vocabulary may name a bucket.
    if reason is None:
        return "none"
    return reason if isinstance(reason, str) and reason in DELETE_REASONS else "other"


# Correction bucket -> the lever that could move it and that lever's status
# (C1 design §6, CLAUDE.md §2-3). EXACT signatures first: a single-fault row
# is the one a single lever can fix. A mixed signature must not borrow one
# field's lever -- a fix only pays when it is the LAST fault on the row
# (CLAUDE.md §2, the theoretical-parser result) -- so it falls to the generic
# multi-field entry.
EXACT_LEVERS = {
    "fields:char_type": ("read stage (Ø Diameter↔Distance)", "open"),
    "fields:upper_tol": ("render_target fix + adapter retrain (tolerances)", "open"),
    "fields:lower_tol": ("render_target fix + adapter retrain (tolerances)", "open"),
    "fields:upper_tol+lower_tol": ("render_target fix + adapter retrain (tolerances)", "open"),
    "fields:nominal": ("read input / box framing (crop context)", "open"),
    "phantom:duplicate": ("stage-1 contained_duplicate drop", "shipped"),
    "phantom:wrong_region": ("box framing (detect_regions)", "open"),
    # The reader returned nothing: a non-text region was ballooned, which is a
    # detection fault, not a read one.
    "phantom:empty_extraction": ("non-text region detected (detect_regions)", "open"),
}
# Prefix match, FIRST wins, so most-specific first.
LEVERS = [
    ("phantom:unballooned_real_dimension",
     "ballooning-policy classifier learned on train gold", "open (verifier closed)"),
    ("phantom:", "phantom drops / ballooning policy", "stage-2 drops shipped"),
    ("fields:", "read input / box framing (multi-field: every fault must be fixed "
                "before the row pays)", "open"),
    ("missed", "detector weights", "open (Rung 3 moved isolated misses)"),
    ("suggestion_", "suggestion tray thresholds (drop stage 2)", "shipped; graded here"),
    ("bubble_moved", "whitespace balloon placement (fa1cb6f)", "new, unmeasured"),
    ("anchor_", "box framing (detect_regions)", "open; framing dominates read accuracy"),
]


def _lever(bucket):
    if bucket in EXACT_LEVERS:
        return EXACT_LEVERS[bucket]
    for prefix, lever, status in LEVERS:
        if bucket.startswith(prefix):
            return lever, status
    return "unmapped", "unmapped"


def _value_bucket(r) -> Optional[str]:
    g = r["grade"]
    if g == "edited":
        # Field names come from VALUE_FIELDS via grade_session, not the record.
        return "fields:" + "+".join(r["fields"])
    if g == "phantom":
        return "phantom:" + _phantom_reason(r.get("reason"))
    if g in ("suggestion_confirmed", "suggestion_edited"):
        return g          # a dropped value the client wanted back
    return None


def row_buckets(row, scale) -> List[tuple]:
    """The (kind, bucket) pairs ONE graded row falls in; kind is "value" or
    "placement". The single definition of what a bucket is: ranked_findings
    counts with it and the operator's example deck picks rows with it, so a
    bucket's examples can never disagree with its count. The missed bucket
    belongs to no graded row (the client added it), so callers add it.

    One row can sit in several buckets (an edit whose bubble also moved), and
    a deleted suggestion sits in none -- the client agreed with the drop."""
    out = []
    vb = _value_bucket(row)
    if vb:
        out.append(("value", vb))
    # Same thresholds as group_metrics, so the two never disagree on what
    # counts as a moved bubble or a nudged anchor.
    if (row.get("bubble_px") is not None
            and px_to_mm(row["bubble_px"], scale) > BUBBLE_MOVED_MM):
        out.append(("placement", "bubble_moved"))
    # anchor_moved with no IoU is unmeasurable, not a known move.
    if row.get("anchor_moved") and row.get("anchor_iou") is not None:
        out.append(("placement", "anchor_nudged" if row["anchor_iou"] >= NUDGE_IOU
                    else "anchor_repointed"))
    return out


def ranked_findings(per_drawing: List[dict]) -> dict:
    """One table per kind (value / placement), each bucket with its count,
    share within its kind, distinct drawings and lever. `per_drawing`:
    [{"rows", "missed", "scale"}] for gradable drawings only.

    A placement share is a share of correction EVENTS, not of rows: one row
    can both have its bubble moved and its anchor nudged, and counts in both.

    A deleted suggestion is NOT a correction -- the client agreed with the
    drop -- so it is kept out of the value table as `tray_drop_right`;
    ranking it would make a working drop rule look like a problem."""
    tables: Dict[str, Dict[str, dict]] = {"value": {}, "placement": {}}
    drop_right = 0

    def add(kind, bucket, di):
        b = tables[kind].setdefault(bucket, {"count": 0, "drawings": set()})
        b["count"] += 1
        b["drawings"].add(di)

    for di, d in enumerate(per_drawing):
        for r in d["rows"]:
            for kind, bucket in row_buckets(r, d["scale"]):
                add(kind, bucket, di)
            drop_right += r["grade"] == "suggestion_deleted"
        for _ in d["missed"]:
            add("value", "missed", di)
    out: Dict[str, object] = {}
    for kind, t in tables.items():
        total = sum(b["count"] for b in t.values())
        rows = []
        for bucket, b in t.items():
            lever, status = _lever(bucket)
            rows.append({"bucket": bucket, "count": b["count"],
                         "share": _ratio(b["count"], total),
                         "drawings": len(b["drawings"]), "lever": lever,
                         "lever_status": status})
        out[kind] = sorted(rows, key=lambda r: (-r["count"], r["bucket"]))
    out["tray_drop_right"] = drop_right
    return out


POOLED_METRICS = ("precision", "unflagged_precision", "flag_precision",
                  "recall_shipped_vs_client_known", "recall_with_tray_vs_client_known")


def _scale_ok(s) -> bool:
    return (not isinstance(s, bool) and isinstance(s, (int, float))
            and math.isfinite(s) and s > 0)


def _num_ok(w) -> bool:
    return (not isinstance(w, bool) and isinstance(w, (int, float))
            and math.isfinite(w) and w >= 0)


def _manifest_parts(manifest):
    """(roles, pool, runs), refusing any shape the grader cannot use. The
    manifest is the operator's own file, so a bad one is an operator error to
    fix, not a drawing to skip -- but the message is still fixed words."""
    if manifest is None:
        return {}, {}, set()
    try:
        if not isinstance(manifest, dict):
            raise TypeError
        roles = manifest.get("roles", {}) or {}
        pool = manifest.get("pool", {}) or {}
        runs = manifest.get("runs", []) or []
        if not (isinstance(roles, dict) and isinstance(pool, dict)
                and isinstance(runs, list)):
            raise TypeError
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in roles.items()):
            raise TypeError
        if not all(_token(k) and _num_ok(w) for k, w in pool.items()):
            raise TypeError
        if not all(isinstance(r, dict) and isinstance(r.get("run"), str) for r in runs):
            raise TypeError
    except TypeError:
        raise ValueError("manifest: bad roles/pool") from None
    # A run name is copied into by_source_run, so only plain tokens may join
    # the allowlist; anything else falls to "unknown" like an unlisted run.
    return roles, pool, {r["run"] for r in runs if _token(r["run"])}


# Our own predict-run naming (r5-control, r4-controltest, r3-trainpredict):
# the fallback allowlist for a source run when no manifest names its runs.
_RUN_NAME = re.compile(r"\Ar\d+-[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# The share_select roles; any further group must be vouched for by a manifest.
_BASE_GROUPS = frozenset({"great", "problem"})


def _allowed(s, allow) -> Optional[str]:
    """A part number ('PartNo-4711') is word-shaped, so a shape check cannot
    keep client text out of the digest; group and source run must instead be
    words WE chose, i.e. on an allowlist. isinstance first: a list or dict is
    unhashable and would raise on the membership test."""
    return s if isinstance(s, str) and s in allow else None


def _grade_one(d, facts) -> dict:
    """The per-drawing work that can trip over a damaged record. Raises on
    anything unexpected; the caller turns that into a fixed word."""
    scale = facts.get("render_scale")
    if not _scale_ok(scale):
        return {"status": "ungradable", "reason": "no_render_scale"}
    # The session's OWN proposal (suggestions included): it is what the
    # journal was replayed on, and any other list disagrees on every drawing.
    g = grade_session(d.proposal_rows, d.sealed)
    if g["ungradable"]:
        return {"status": "ungradable", "reason": g["ungradable"]}
    return {"status": "approved", "reason": None, "rows": g["rows"], "missed": g["missed"],
            "scale": scale, "time_ms": review_time_ms(d.events)}


def _note(sealed) -> Optional[str]:
    """The FINAL note, or None when it cannot be read. Separate from grading:
    a malformed note must cost the note, never a valid grade."""
    try:
        note = note_of(sealed.get("net_events", []))
    except Exception:  # noqa: BLE001 -- note_of raises KeyError/JournalError on bad events
        return None
    return note if isinstance(note, str) else None


def _gold_section(crosses: List[dict]) -> dict:
    """Digest view of every approved drawing's cross-check: counts only, the
    per-row verdicts stay in the report."""
    done = [c for c in crosses if c.get("computed") is True]
    not_computed: Dict[str, int] = {}
    for c in crosses:
        if c.get("computed") is not True:
            word = _token(c.get("reason"), "unknown")
            not_computed[word] = not_computed.get(word, 0) + 1
    matrix: Dict[str, int] = {}
    for c in done:
        for k, v in c["matrix"].items():
            matrix[k] = matrix.get(k, 0) + v
    return {"n_drawings_computed": len(done),
            "not_computed": dict(sorted(not_computed.items())),
            "matrix": dict(sorted(matrix.items())),
            **{k: sum(c[k] for c in done)
               for k in ("n_gold", "neither_found", "matched_final", "unlocatable",
                         "row_invalid")}}


def grade_campaign(records, gold_by_hash: Optional[Dict[str, GoldDoc]] = None,
                   manifest: Optional[dict] = None, *,
                   params: Optional[MatchParams] = None, name=None):
    """-> (report, digest).

    `report` holds per-row grades, proposed/final values and notes: it goes
    ONLY inside the protected root. `digest` is counts, rates, fixed words and
    salted doc hashes, for docs/eval/.

    `gold_by_hash` maps doc_hash -> GoldDoc and MUST come from the
    `--originals` gold ingest: the campaign renders the clean original, and a
    stamped-sheet gold of the same page size passes the frame check while
    every position is wrong (see _same_frame). None means "no cross-check
    requested"; a dict (even an empty one) means gold was asked for, and if no
    approved drawing joins it that is the reconcile problem gold_none_joined.

    `name` is the run's --name, kept only if it is a plain token.

    A drawing is ungradable (with a fixed word, never exception text, which
    can quote client values) when its record is damaged, its replay does not
    reproduce its seal, it has no usable render_scale, or grading raised
    (`grade_error`). A cross-check failure alone keeps the client's grade.
    Raises ValueError only for a malformed manifest."""
    roles, pool, manifest_runs = _manifest_parts(manifest)
    # Roles and pool are the operator's words; _token still guards them because
    # the group also keys the digest and the pooled_unmeasured lists.
    group_allow = _BASE_GROUPS | {g for g in set(pool) | set(roles.values()) if _token(g)}

    def source_run(s) -> str:
        if manifest_runs:
            return _allowed(s, manifest_runs) or "unknown"
        return s if isinstance(s, str) and _RUN_NAME.match(s) else "unknown"
    # The operator's --name lands in the digest, so only a plain token is kept.
    run_name = _token(name, "unnamed")
    gold_requested = gold_by_hash is not None
    gold_by_hash = gold_by_hash or {}
    status = {"approved": 0, "pending": 0, "ungradable": 0}
    reasons: Dict[str, int] = {}
    by_group: Dict[str, List[dict]] = {}
    report_docs, problems = [], []
    notes_unreadable = revision_mismatch = 0

    def doc_hash(d):
        h = d.index.get("doc_hash")
        return h if isinstance(h, str) and h else None

    # Two records under one hash (pull.py's collision dirs) are flagged, and
    # only the first is joined to roles and gold: the second would otherwise
    # be counted against the same manifest role and gold twice.
    hash_counts = Counter(h for h in map(doc_hash, records.drawings.values()) if h)
    for h, n in sorted(hash_counts.items()):
        if n > 1:
            problems.append(f"duplicate_doc_hash:{_safe_key(h)}")
    joined = set()
    for key, d in sorted(records.drawings.items()):
        skey = _safe_key(key)
        h = doc_hash(d)
        primary = h is None or h not in joined
        if h:
            joined.add(h)
        header = d.header if isinstance(d.header, dict) else {}
        facts = header.get("campaign")
        facts = facts if isinstance(facts, dict) else {}
        group = (_allowed(facts.get("group"), group_allow)
                 or _allowed(d.index.get("group"), group_allow) or "unknown")
        if primary and h in roles and roles[h] != group:
            problems.append(f"group_differs:{skey}")
        revision_mismatch += bool(d.revision_mismatch)
        doc = {"doc_hash": skey, "group": group,
               "source_run": source_run(facts.get("source_run")),
               "status": None, "reason": None, "rows": None, "missed": None,
               "scale": None, "time_ms": None, "proposal": None, "final": None,
               "cross": None, "note": None,
               # Report only: the digest is built from counts and never sees it.
               "page_rect": _report_page_rect(facts.get("page_rect"))}
        if d.error:
            res = {"status": "ungradable", "reason": _token(d.error, "unknown")}
        elif d.sealed is None:
            res = {"status": "pending", "reason": None}
        else:
            try:
                res = _grade_one(d, facts)
            except Exception:  # noqa: BLE001 -- one bad drawing must not sink the run
                res = {"status": "ungradable", "reason": "grade_error"}
        doc.update(res)
        status[doc["status"]] += 1
        if doc["status"] == "ungradable":
            reasons[doc["reason"]] = reasons.get(doc["reason"], 0) + 1
        if doc["status"] != "approved":
            report_docs.append(doc)
            continue
        note = _note(d.sealed)
        notes_unreadable += note is None
        doc["note"] = note or ""
        if len(doc["rows"]) != len(d.proposal_rows):
            problems.append(f"partition:{skey}")
        doc["proposal"], doc["final"] = d.proposal_rows, d.sealed.get("rows", [])
        by_group.setdefault(group, []).append(doc)
        gold = gold_by_hash.get(h) if h else None
        if not primary:
            cross = {"computed": False, "reason": "duplicate_doc_hash"}
        elif gold is None:
            cross = {"computed": False, "reason": "no_gold"}
        else:
            try:
                cross = gold_cross_check(
                    d.proposal_rows, doc["final"], {r["id"]: r["grade"] for r in doc["rows"]},
                    gold, render_scale=doc["scale"], page_rect=facts.get("page_rect"),
                    params=params)
            except Exception:  # noqa: BLE001 -- gold is a second lens; keep the grade
                cross = {"computed": False, "reason": "cross_check_error"}
        doc["cross"] = cross
        if cross.get("computed") is True:
            # The matrix must partition the delivered rows and the gold rows
            # must be either found or not -- checked against the counts the
            # cross-check states, never assumed.
            if sum(cross["matrix"].values()) != cross.get("n_delivered"):
                problems.append(f"cross_matrix:{skey}")
            if cross["matched_final"] + cross["neither_found"] != cross["n_gold"]:
                problems.append(f"cross_gold:{skey}")
        report_docs.append(doc)

    groups = {grp: group_metrics(ds) for grp, ds in sorted(by_group.items())}
    # STRUCTURAL GUARD, not reconciliation: group_metrics counts the same rows
    # it is checked against, so no input can make this fire today. It only
    # catches group_metrics drifting from grade_session's GRADES (a new grade
    # it does not count). Do not cite it as evidence that the digest adds up;
    # group_drawings, gold_coverage and the cross_* checks are the real ones.
    for grp, ds in sorted(by_group.items()):
        c = groups[grp]["counts"]
        if (sum(c[g] for g in VALUE_GRADES + SUGGESTION_GRADES)
                != sum(len(e["rows"]) for e in ds)):
            problems.append(f"group_counts:{grp}")
        # The counts behind the precision ratios must add up: flagged plus
        # the unflagged per-grade counts partition the graded balloons, and
        # an explicit confirmation is a confirmation. Both are checked against
        # counts the digest carries, so a reader can redo them.
        graded = sum(c[g] for g in VALUE_GRADES)
        if sum(c[f"unflagged_{g}"] for g in VALUE_GRADES) + c["flagged"] != graded:
            problems.append(f"group_flag_partition:{grp}")
        if not (c["unflagged_confirmed_explicit"] <= c["confirmed_explicit"] <= c["confirmed"]):
            problems.append(f"group_explicit:{grp}")
    if sum(m["n_drawings"] for m in groups.values()) != status["approved"]:
        problems.append("group_drawings")
    if sum(status.values()) != len(records.drawings):
        problems.append("status_counts")
    approved_docs = [e for ds in by_group.values() for e in ds]
    gold_section = _gold_section([e["cross"] for e in approved_docs])
    if (gold_section["n_drawings_computed"] + sum(gold_section["not_computed"].values())
            != status["approved"]):
        problems.append("gold_coverage")
    # Gold was asked for (a dict, even empty) yet no approved drawing joined
    # it: a wrong salt or a wrong --gold dir. Every drawing would otherwise
    # read no_gold and the run would reconcile cleanly with its cross-check
    # silently gone. None means no cross-check was requested at all.
    if (gold_requested and status["approved"]
            and gold_section["n_drawings_computed"] == 0):
        problems.append("gold_none_joined")

    in_records ={h for h in map(doc_hash, records.drawings.values()) if h}
    in_skipped = {s.get("doc_hash") for s in records.skipped
                  if isinstance(s, dict) and isinstance(s.get("doc_hash"), str)}
    for h in sorted(in_records & in_skipped):
        problems.append(f"skipped_and_recorded:{_safe_key(h)}")
    not_in_records = extra_in_records = None
    if roles:
        not_in_records = len([h for h in roles if h not in in_records | in_skipped])
        # A hashless record cannot be in the manifest either.
        extra_in_records = len([d for d in records.drawings.values()
                                if doc_hash(d) not in roles])
        # Coverage needs no check of its own: not_in_records is DEFINED as the
        # roles found nowhere, so "recorded + skipped + not_in_records ==
        # roles" is arithmetic, not evidence (a former manifest_coverage check
        # could never fire and was removed). The falsifiable half is
        # skipped_and_recorded above.

    # No pool -> nothing to reweight to: None, never a guessed mix; and say
    # which groups made a pooled value None, since reweight() cannot.
    pooled = {k: (reweight({grp: m[k] for grp, m in groups.items()}, pool) if pool else None)
              for k in POOLED_METRICS}
    runs = sorted({e["source_run"] for e in approved_docs})
    campaign = _token(records.campaign, "invalid")
    # "kind" goes FIRST in both: an agent picks the output paths, so a later
    # run only overwrites a file whose opening bytes say it is one of ours
    # (review.check_report_path / check_digest_path), without parsing a
    # report that may be large.
    digest = {
        "kind": DIGEST_KIND,
        "name": run_name,
        "campaign": campaign,
        "drawings": {**status, "ungradable_reasons": dict(sorted(reasons.items())),
                     "skipped": len(records.skipped),
                     "not_in_records": not_in_records,
                     "extra_in_records": extra_in_records},
        "revision_mismatch": revision_mismatch,
        "groups": groups,
        "pooled": pooled,
        "pooled_unmeasured": {
            "unpooled_groups": sorted(set(groups) - set(pool)),
            "pool_groups_missing": sorted(g for g, w in pool.items()
                                          if w and g not in groups)},
        "pool": pool,
        "gold_cross_check": gold_section,
        "findings": ranked_findings(approved_docs),
        # The FINAL note only (a cleared note is no note), and only as a count:
        # note text is client prose.
        "notes_written": sum(1 for e in approved_docs if e["note"]),
        "notes_unreadable": notes_unreadable,
        "by_source_run": {r: sum(1 for e in approved_docs if e["source_run"] == r)
                          for r in runs},
        "reconcile_problems": problems,
        "reconciles": not problems,
    }
    report = {"kind": REPORT_KIND, "name": run_name, "campaign": campaign,
              "drawings": report_docs, "digest": digest}
    return report, digest
