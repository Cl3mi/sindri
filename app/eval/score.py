"""Score one prediction dump against one GoldDoc: match, compare fields, tag
the error taxonomy, and price the result in expected review effort (§4 of the
handoff). Pure CPU; imports nothing from the model stack."""
import math
import re
from typing import Dict, List

from app.eval.dump import to_points
from app.eval.matching import Cand, match_candidates
from app.eval.models import (DocScore, GoldDoc, MatchParams, MatchedPair,
                             PredictionDump, ReviewCostWeights)
from app.eval.normalize import canon_value, char_type_equal, values_equal

# Same numeric token shape as app/pipeline/parser.py's _NUM (kept local so the
# eval package never imports pipeline internals that may move under tuning).
_NUM_RE = re.compile(r"[+\-±]?\d+(?:[.,]\d+)?")

_FIELDS = ("nominal", "upper_tol", "lower_tol")

# Read-confidence buckets. 0.6 is a boundary on purpose: it is
# app.pipeline.review.LOW_CONF, the threshold that decides needs_review, so the
# joint histogram of bucket x taxonomy answers "how many rows would a threshold
# move flag, and how many of those are actually wrong?" without a GPU re-run.
# Duplicated rather than imported: eval must not import the pipeline module
# whose constant is under review, and a drifting copy would change only this
# diagnostic's bucket labels, not any scored result.
_CONF_EDGES = (0.2, 0.4, 0.6, 0.8)


def _conf_bucket(conf: float) -> str:
    """Label for the confidence band `conf` falls in, lower edge inclusive."""
    lo = 0.0
    for edge in _CONF_EDGES:
        if conf < edge:
            return f"<{edge:.1f}" if lo == 0.0 else f"{lo:.1f}-{edge:.1f}"
        lo = edge
    return f">={_CONF_EDGES[-1]:.1f}"


def _center_pt(char, dump):
    box = to_points(char.target_region, dump.scale, dump.page_rect)
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _compare_fields(pred, gold) -> List[str]:
    errors = []
    if gold.char_type and not char_type_equal(pred.char_type, gold.char_type):
        errors.append(f"char_type: {pred.char_type!r}!={gold.char_type!r}")
    for f in _FIELDS:
        pv, gv = getattr(pred, f), getattr(gold, f)
        if not values_equal(pv, gv):
            errors.append(f"{f}: {pv!r}!={gv!r}")
    return errors


def _failure_modes(pred, gold) -> List[str]:
    """Values-blind tags saying HOW each wrong field is wrong.

    `missing:<field>`  the pipeline produced nothing and gold has a value;
    `wrong:<field>`    both are non-empty and disagree;
    `spurious:<field>` the pipeline invented a value gold does not have.

    This is the routing decision for a read-prompt arm. A dropped tolerance is
    an instruction problem — the prompt never says "transcribe every number
    printed, including a zero" — while a disagreeing one is a perception
    problem, and they need different prompt text. field_errors already records
    which field, but the digest cannot read its values, so the distinction had
    nowhere to live.

    Only emptiness is inspected, never the value itself, which is what makes
    these safe for the values-blind digest. The predicates are exactly
    _compare_fields', so the two can never disagree on WHICH fields are wrong."""
    modes = []
    if gold.char_type and not char_type_equal(pred.char_type, gold.char_type):
        empty = not str(pred.char_type or "").strip()
        modes.append(f"{'missing' if empty else 'wrong'}:char_type")
    for f in _FIELDS:
        pv, gv = getattr(pred, f), getattr(gold, f)
        if values_equal(pv, gv):
            continue
        if not canon_value(pv):
            modes.append(f"missing:{f}")
        elif not canon_value(gv):
            modes.append(f"spurious:{f}")
        else:
            modes.append(f"wrong:{f}")
    return modes


def _cause(pred, gold) -> str:
    """misparse: the raw transcription contains the gold nominal (reader saw the
    right glyphs; parsing/structuring lost them). misread otherwise. A heuristic
    — good enough to steer Rung 1 (parser) vs Rung 2/3 (perception) effort."""
    gold_nom = canon_value(gold.nominal)
    raw_nums = {canon_value(t) for t in _NUM_RE.findall(pred.raw_text or "")}
    return "misparse" if gold_nom and gold_nom in raw_nums else "misread"


def _reconcile_pos(pos, gold_rect, dump_rect, mode: str):
    """Map a gold balloon position from the gold page's space into the dump's.

    Gold geometry comes from the stamped drawing, predictions from the clean
    original, and nothing has ever reconciled the two. "scale" treats the stamped
    sheet as a scaled-to-fit export of the same drawing; "center" treats it as the
    same drawing with different margins. Which one recovers recall is the evidence
    for which relationship actually holds."""
    if mode == "none" or pos is None:
        return pos
    gw, gh = gold_rect[2] - gold_rect[0], gold_rect[3] - gold_rect[1]
    dw, dh = dump_rect[2] - dump_rect[0], dump_rect[3] - dump_rect[1]
    if gw <= 0 or gh <= 0:
        return pos
    if mode == "scale":
        return (dump_rect[0] + (pos[0] - gold_rect[0]) * dw / gw,
                dump_rect[1] + (pos[1] - gold_rect[1]) * dh / gh)
    if mode == "center":
        return (pos[0] + (dump_rect[0] - gold_rect[0]) + (dw - gw) / 2.0,
                pos[1] + (dump_rect[1] - gold_rect[1]) + (dh - gh) / 2.0)
    raise ValueError(f"unknown reconcile_frames mode {mode!r} "
                     f"(expected none|scale|center)")


def score_doc(dump: PredictionDump, gold: GoldDoc,
              weights: ReviewCostWeights, params: MatchParams) -> DocScore:
    # Regionless rows can't be matched or counted as false detections; today
    # every VLM and manual row carries target_region, so nothing is dropped.
    # Score only the kinds in scope. A verbal requirement was never ballooned,
    # so it cannot be "missed" by a ballooning pipeline; counting it would swamp
    # the metric. The excluded count is recorded, never hidden.
    scored_gold = [g for g in gold.characteristics
                   if getattr(g, "kind", "dimension") in params.score_kinds]
    excluded_by_kind = len(gold.characteristics) - len(scored_gold)

    preds = [c for c in dump.result.characteristics if c.target_region is not None]
    # Cand list below is built from `preds`, NOT this dict — so duplicate pos
    # values reach match_candidates and fail loudly there. Keep it that way.
    pred_by_pos = {c.pos: c for c in preds}
    gold_by_num = {g.balloon: g for g in scored_gold}

    def gold_pos(g):
        return _reconcile_pos(g.position_pt, gold.page_rect, dump.page_rect,
                              params.reconcile_frames)

    # When gold has been mapped into the dump's page space, the gate has to be
    # normalised by THAT page's diagonal too -- everything is in the dump's frame
    # now. Unreconciled scoring keeps the gold diagonal it has always used.
    rect_for_diag = (gold.page_rect if params.reconcile_frames == "none"
                     else dump.page_rect)
    diag = math.dist(rect_for_diag[:2], rect_for_diag[2:])
    pairs_raw = match_candidates(
        [Cand(key=c.pos, center_pt=_center_pt(c, dump), nominal=c.nominal)
         for c in preds],
        [Cand(key=g.balloon, center_pt=gold_pos(g), nominal=g.nominal)
         for g in scored_gold],
        diag, params)

    pairs, counts = [], {}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    for pk, gk, dist in pairs_raw:
        p, g = pred_by_pos[pk], gold_by_num[gk]
        errors = _compare_fields(p, g)
        notes = [f"conf:{_conf_bucket(p.confidence)}"]
        if dist > params.misplaced_frac:
            notes.append("misplaced")
        if errors:
            taxonomy = "flagged_error" if p.needs_review else "escaped_error"
            notes.append(f"cause:{_cause(p, g)}")
            notes.extend(_failure_modes(p, g))
        else:
            taxonomy = "flagged_correct" if p.needs_review else "correct"
        bump(taxonomy)
        pairs.append(MatchedPair(
            gold_balloon=gk, pred_pos=pk, distance_frac=round(dist, 5),
            fields_correct=not errors, field_errors=errors,
            flagged=p.needs_review, taxonomy=taxonomy, notes=notes))

    matched_g = {gk for _, gk, _ in pairs_raw}
    matched_p = {pk for pk, _, _ in pairs_raw}
    missed = sorted(set(gold_by_num) - matched_g)
    false = sorted(set(pred_by_pos) - matched_p)
    for _ in missed:
        bump("missed")
    for _ in false:
        bump("false_detection")

    flagged_rows = counts.get("flagged_correct", 0) + counts.get("flagged_error", 0)
    escaped = counts.get("escaped_error", 0)
    cost = (weights.miss * len(missed) + weights.escaped * escaped
            + weights.false * len(false) + weights.flag * flagged_rows)

    def _kind(c) -> str:
        return c.kind or "unset"

    pred_kinds: Dict[str, int] = {}
    for c in preds:
        pred_kinds[_kind(c)] = pred_kinds.get(_kind(c), 0) + 1
    false_kinds: Dict[str, int] = {}
    for pk in false:
        k = _kind(pred_by_pos[pk])
        false_kinds[k] = false_kinds.get(k, 0) + 1
    matched_kinds: Dict[str, int] = {}
    for pk in matched_p:
        k = _kind(pred_by_pos[pk])
        matched_kinds[k] = matched_kinds.get(k, 0) + 1

    # Why the misses happened. Read-only over geometry already computed above;
    # it does not influence a single pairing. A gold row with no recovered
    # position can never be matched by any detection change, so it is separated
    # from the two that a detection knob could actually move.
    # Do the two page frames even agree? Reported, not enforced: raising here
    # would refuse to score runs the harness has been scoring all along, and the
    # size of the disagreement is the diagnostic. Expressed as fractions of the
    # diagonal so they carry no page dimensions.
    g, d = gold.page_rect, dump.page_rect
    frame_origin = max(abs(g[0] - d[0]), abs(g[1] - d[1])) / diag if diag else 0.0
    frame_extent = (max(abs((g[2] - g[0]) - (d[2] - d[0])),
                        abs((g[3] - g[1]) - (d[3] - d[1]))) / diag
                    if diag else 0.0)

    pred_centers = [_center_pt(c, dump) for c in preds]
    contended = isolated = unlocated = 0
    for b in missed:
        pos = gold_pos(gold_by_num[b])
        if pos is None:
            unlocated += 1
        elif any(math.dist(pos, pc) / diag <= params.max_geo_frac
                 for pc in pred_centers):
            contended += 1
        else:
            isolated += 1

    n_gold, n_pred = len(gold_by_num), len(pred_by_pos)
    return DocScore(
        doc_id=gold.doc_id, gold_hash=gold.gold_hash(),
        n_gold=n_gold, n_pred=n_pred, pairs=pairs,
        missed_balloons=missed, false_positions=false, counts=counts,
        excluded_by_kind=excluded_by_kind,
        effective_dpi=dump.scale * 72.0,
        pred_kinds=pred_kinds, false_kinds=false_kinds,
        matched_kinds=matched_kinds,
        missed_contended=contended, missed_isolated=isolated,
        missed_unlocated=unlocated,
        frame_origin_frac=round(frame_origin, 6),
        frame_extent_frac=round(frame_extent, 6),
        review_cost=cost,
        recall=(len(matched_g) / n_gold) if n_gold else 1.0,
        precision=(len(matched_p) / n_pred) if n_pred else 1.0,
        escaped_rate=(escaped / n_gold) if n_gold else 0.0,
    )
