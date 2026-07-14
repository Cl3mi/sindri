"""Score one prediction dump against one GoldDoc: match, compare fields, tag
the error taxonomy, and price the result in expected review effort (§4 of the
handoff). Pure CPU; imports nothing from the model stack."""
import math
import re
from typing import List

from app.eval.dump import to_points
from app.eval.matching import Cand, match_candidates
from app.eval.models import (DocScore, GoldDoc, MatchParams, MatchedPair,
                             PredictionDump, ReviewCostWeights)
from app.eval.normalize import canon_value, char_type_equal, values_equal

# Same numeric token shape as app/pipeline/parser.py's _NUM (kept local so the
# eval package never imports pipeline internals that may move under tuning).
_NUM_RE = re.compile(r"[+\-±]?\d+(?:[.,]\d+)?")

_FIELDS = ("nominal", "upper_tol", "lower_tol")


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


def _cause(pred, gold) -> str:
    """misparse: the raw transcription contains the gold nominal (reader saw the
    right glyphs; parsing/structuring lost them). misread otherwise. A heuristic
    — good enough to steer Rung 1 (parser) vs Rung 2/3 (perception) effort."""
    gold_nom = canon_value(gold.nominal)
    raw_nums = {canon_value(t) for t in _NUM_RE.findall(pred.raw_text or "")}
    return "misparse" if gold_nom and gold_nom in raw_nums else "misread"


def score_doc(dump: PredictionDump, gold: GoldDoc,
              weights: ReviewCostWeights, params: MatchParams) -> DocScore:
    preds = [c for c in dump.result.characteristics if c.target_region is not None]
    pred_by_pos = {c.pos: c for c in preds}
    gold_by_num = {g.balloon: g for g in gold.characteristics}

    diag = math.dist(gold.page_rect[:2], gold.page_rect[2:])
    pairs_raw = match_candidates(
        [Cand(key=c.pos, center_pt=_center_pt(c, dump), nominal=c.nominal)
         for c in preds],
        [Cand(key=g.balloon, center_pt=g.position_pt, nominal=g.nominal)
         for g in gold.characteristics],
        diag, params)

    pairs, counts = [], {}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    for pk, gk, dist in pairs_raw:
        p, g = pred_by_pos[pk], gold_by_num[gk]
        errors = _compare_fields(p, g)
        notes = []
        if dist > params.misplaced_frac:
            notes.append("misplaced")
        if errors:
            taxonomy = "flagged_error" if p.needs_review else "escaped_error"
            notes.append(f"cause:{_cause(p, g)}")
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

    n_gold, n_pred = len(gold_by_num), len(pred_by_pos)
    return DocScore(
        doc_id=gold.doc_id, gold_hash=gold.gold_hash(),
        n_gold=n_gold, n_pred=n_pred, pairs=pairs,
        missed_balloons=missed, false_positions=false, counts=counts,
        review_cost=cost,
        recall=(len(matched_g) / n_gold) if n_gold else 1.0,
        precision=(len(matched_p) / n_pred) if n_pred else 1.0,
        escaped_rate=(escaped / n_gold) if n_gold else 0.0,
    )
