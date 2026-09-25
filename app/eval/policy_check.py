"""Price a flag rule or a drop rule from dumps already on disk — no GPU.

Both are post-read decisions over a finished Characteristic, so applying the
rule to a stored dump reproduces exactly what the pipeline would have emitted
with the rule active, and re-scoring that dump prices it exactly -- matching
included, since dropping a prediction can let a neighbour pair. The rules come
from app.pipeline.policy_rules, the second deliberate pipeline import in the
eval package after reparse's `parser`: pricing a COPY of a rule would price
something the pipeline does not run.

Counts and deltas only; never a value. The keep conditions are those of
docs/plans/2026-09-25-review-quality-arms-plan.md §1, and the tolerances are
experiment.py's, so there is one threshold per question."""
from typing import Dict, Optional, Sequence, Tuple

from app.eval.experiment import (AUTO_ACCEPT_TOLERANCE, FIELD_ACC_TOLERANCE,
                                 RECALL_TOLERANCE)
from app.eval.models import GoldDoc, MatchParams, PredictionDump, ReviewCostWeights
from app.eval.report import WEIGHT_GRID, auto_accept_precision, recompute_cost
from app.eval.score import score_doc
from app.pipeline.policy_rules import (DROP_RULES, FLAG_RULES,
                                       apply_drop_rules, apply_flag_rules)

# The live threshold, copied for the base normalisation below. Dumps predicted
# before 2026-09-02 were flagged at 0.6; pricing a rule on top of them would
# credit it with part of the 0.6 -> 0.8 move. A test pins this to
# app.pipeline.review.LOW_CONF so the copy cannot drift.
_LOW_CONF = 0.8


def _normalise_base(dump: PredictionDump) -> Tuple[PredictionDump, int]:
    """Today's LOW_CONF on every row, so all splits share one base policy.
    Returns (dump copy, rows newly flagged). On current-code dumps the count
    must be 0 -- that is this function's gate."""
    d = dump.model_copy(deep=True)
    n = 0
    for c in d.result.characteristics:
        if (c.raw_text or "").strip() and c.confidence < _LOW_CONF \
                and not c.needs_review:
            c.needs_review = True
            c.review_reasons = [*c.review_reasons, "low OCR confidence"]
            n += 1
    return d, n


def _with_flags(dump: PredictionDump, names: Sequence[str]) -> PredictionDump:
    d = dump.model_copy(deep=True)
    for c in d.result.characteristics:
        extra = apply_flag_rules(c, names)
        if extra:
            c.needs_review = True
            c.review_reasons = [*c.review_reasons, *extra]
    return d


def _with_drops(dump: PredictionDump, names: Sequence[str]) -> PredictionDump:
    d = dump.model_copy(deep=True)
    d.result.characteristics = apply_drop_rules(d.result.characteristics, names)
    return d


def _score_all(dumps: Dict[str, PredictionDump], golds: Dict[str, GoldDoc],
               doc_ids: Sequence[str], weights: ReviewCostWeights,
               params: MatchParams):
    return [score_doc(dumps[i], golds[i], weights, params) for i in doc_ids]


def _stats(scores) -> Dict:
    counts: Dict[str, int] = {}
    n_gold = 0
    for s in scores:
        n_gold += s.n_gold
        for k, v in s.counts.items():
            counts[k] = counts.get(k, 0) + v
    matched = n_gold - counts.get("missed", 0)
    correct, escaped = counts.get("correct", 0), counts.get("escaped_error", 0)
    right = correct + counts.get("flagged_correct", 0)
    return {
        "cost": round(sum(s.review_cost for s in scores) / len(scores), 4),
        "counts": counts,
        "recall": round(matched / n_gold, 4) if n_gold else 0.0,
        "field_acc": round(right / matched, 4) if matched else 0.0,
        "escaped_rate": round(escaped / n_gold, 4) if n_gold else 0.0,
        # None (never 0.0) when nothing is unflagged -- report.py's own
        # formula, reused rather than re-derived so the two callers can never
        # disagree about what "unmeasured" means. Coercing this to 0.0 used to
        # let a flag rule that empties the unflagged set entirely score a
        # cost-free delta of exactly 0 against a base that was ALSO 0.0 (a
        # coincidence, not a measurement), and let a drop rule that re-pairs
        # onto an empty base show a fabricated +1.0. _delta below propagates
        # the None instead.
        "auto_accept_precision": auto_accept_precision(counts),
        "auto_accept_rate": round(correct / n_gold, 4) if n_gold else 0.0,
    }


_DELTA_KEYS = ("cost", "recall", "field_acc", "escaped_rate",
               "auto_accept_precision", "auto_accept_rate")


def _delta(a: Dict, b: Dict) -> Dict:
    """None on EITHER side yields a None delta, never a value computed against
    a stand-in 0.0 -- only `auto_accept_precision` can be None, and an
    unmeasured precision on one side makes the delta itself unmeasured, not
    zero and not the other side's raw number."""
    out = {}
    for k in _DELTA_KEYS:
        av, bv = a[k], b[k]
        out[k] = None if av is None or bv is None else round(bv - av, 4)
    return out


def _better_under(base_counts, new_counts) -> int:
    return sum(1 for w in WEIGHT_GRID
               if recompute_cost(new_counts, w) < recompute_cost(base_counts, w))


def _passes_flag(d: Dict, better: int) -> bool:
    # Flags cannot move matching, so recall and field_acc must be untouched;
    # a non-zero delta there means the offline reconstruction is broken.
    if d["recall"] != 0 or d["field_acc"] != 0:
        raise AssertionError(f"a flag rule moved matching: {d}")
    aap = d["auto_accept_precision"]
    # Unmeasured -- either side's unflagged set was empty -- is never a pass.
    # A rule that flags the whole unflagged set away is not "infinitely
    # precise"; it produced nothing to be confident about.
    if aap is None:
        return False
    return (d["cost"] < 0 and better == len(WEIGHT_GRID)
            and aap > 0
            and d["auto_accept_rate"] >= -AUTO_ACCEPT_TOLERANCE)


def _passes_drop(d: Dict, better: int) -> bool:
    aap = d["auto_accept_precision"]
    if aap is None:
        return False
    return (d["cost"] < 0 and better == len(WEIGHT_GRID)
            and d["recall"] >= -RECALL_TOLERANCE
            and d["field_acc"] >= -FIELD_ACC_TOLERANCE
            and d["escaped_rate"] <= 0
            and aap >= 0
            and d["auto_accept_rate"] >= -AUTO_ACCEPT_TOLERANCE)


def _base_taxonomy(base_scores, doc_ids) -> Dict[str, Dict[int, str]]:
    """Per doc, {pred_pos: taxonomy} over the base's matched pairs -- what a
    row's taxonomy was BEFORE the rule under test touched it."""
    return {doc_id: {p.pred_pos: p.taxonomy for p in s.pairs}
            for doc_id, s in zip(doc_ids, base_scores)}


def _base_false_positions(base_scores, doc_ids) -> Dict[str, set]:
    return {doc_id: set(s.false_positions) for doc_id, s in zip(doc_ids, base_scores)}


def _flagged_predicate(base_pair_tax, base_false, doc_id):
    def _pred(base_by_pos, new_by_pos):
        for pos, base_c in base_by_pos.items():
            new_c = new_by_pos.get(pos)
            if new_c is None or base_c.needs_review or not new_c.needs_review:
                continue
            key = base_pair_tax[doc_id].get(pos)
            # A flagged row need not have paired with gold at all -- a flag
            # rule fires on any Characteristic, matched or not. Without this,
            # a newly-flagged false detection has no taxonomy entry, `key` is
            # None, and the caller's `if key:` guard silently drops it from
            # the histogram instead of counting it, for the same reason the
            # drop histogram below names it explicitly.
            if key is None and pos in base_false[doc_id]:
                key = "false_detection"
            yield pos, key
    return _pred


def _dropped_predicate(base_pair_tax, base_false, doc_id):
    def _pred(base_by_pos, new_by_pos):
        for pos, base_c in base_by_pos.items():
            if pos in new_by_pos or base_c.target_region is None:
                continue
            if pos in base_false[doc_id]:
                key = "false_detection"
            else:
                key = base_pair_tax[doc_id].get(pos, "unscored")
            yield pos, key
    return _pred


def _by_pos(dump: PredictionDump) -> Dict[int, object]:
    # Regionless rows can never match or be scored as a false detection
    # (score_doc filters them the same way), so they must not leak into a
    # flag/drop histogram either -- keeping them would let a rule's touch on
    # an unscoreable row masquerade as a taxonomy-bearing one.
    return {c.pos: c for c in dump.result.characteristics
            if c.target_region is not None}


def _price_flag_rule(name: str, base_dumps, golds, doc_ids, weights, params,
                     base_stats, base_pair_tax, base_false) -> Dict:
    new_dumps = {i: _with_flags(base_dumps[i], (name,)) for i in doc_ids}
    hist: Dict[str, int] = {}
    for doc_id in doc_ids:
        for pos, key in _flagged_predicate(base_pair_tax, base_false, doc_id)(
                _by_pos(base_dumps[doc_id]), _by_pos(new_dumps[doc_id])):
            if key:
                hist[key] = hist.get(key, 0) + 1
    scores = _score_all(new_dumps, golds, doc_ids, weights, params)
    stats = _stats(scores)
    d = _delta(base_stats, stats)
    better = _better_under(base_stats["counts"], stats["counts"])
    return {"newly_flagged": hist, "delta": d, "better_under": better,
            "precision_unmeasured": d["auto_accept_precision"] is None,
            "passes": _passes_flag(d, better)}


def _price_drop_rule(name: str, base_dumps, golds, doc_ids, weights, params,
                     base_stats, base_pair_tax, base_false) -> Dict:
    new_dumps = {i: _with_drops(base_dumps[i], (name,)) for i in doc_ids}
    hist: Dict[str, int] = {}
    for doc_id in doc_ids:
        for pos, key in _dropped_predicate(base_pair_tax, base_false, doc_id)(
                _by_pos(base_dumps[doc_id]), _by_pos(new_dumps[doc_id])):
            if key:
                hist[key] = hist.get(key, 0) + 1
    scores = _score_all(new_dumps, golds, doc_ids, weights, params)
    stats = _stats(scores)
    d = _delta(base_stats, stats)
    better = _better_under(base_stats["counts"], stats["counts"])
    return {"dropped": hist, "delta": d, "better_under": better,
            "precision_unmeasured": d["auto_accept_precision"] is None,
            "passes": _passes_drop(d, better)}


def policy_report(dumps: Dict[str, PredictionDump], golds: Dict[str, GoldDoc],
                  doc_ids: Sequence[str], weights: ReviewCostWeights,
                  params: MatchParams,
                  flag_rules: Sequence[str] = tuple(FLAG_RULES),
                  drop_rules: Sequence[str] = tuple(DROP_RULES),
                  joint: Optional[Tuple[Sequence[str], Sequence[str]]] = None
                  ) -> Dict:
    """Price every named flag/drop rule (and, if given, a joint set) against a
    common, LOW_CONF-normalised base. Never mutates `dumps`."""
    if not doc_ids:
        raise ValueError("policy_report: doc_ids is empty -- nothing to price")
    seen, dupes = set(), set()
    for i in doc_ids:
        (dupes if i in seen else seen).add(i)
    if dupes:
        raise ValueError(f"policy_report: doc_ids has duplicates: {sorted(dupes)}")

    base_reflagged = 0
    n_docs_pre_low_conf = 0
    base_dumps: Dict[str, PredictionDump] = {}
    for doc_id in doc_ids:
        orig = dumps[doc_id]
        d, n = _normalise_base(orig)
        base_dumps[doc_id] = d
        base_reflagged += n
        # extra.review_low_conf == _LOW_CONF says this dump was ALREADY
        # produced under today's threshold -- the mirror of review.py's own
        # flagging, so a row there still needing a reflag means this copy and
        # the pipeline's have diverged. That is a bug in this module, not a
        # fact about the dump, and must raise rather than be quietly
        # "corrected" the way a genuinely pre-2026-09-02 dump is.
        if orig.config.extra.get("review_low_conf") == _LOW_CONF:
            if n:
                raise ValueError(
                    f"doc {doc_id!r} is on current review policy "
                    f"(review_low_conf={_LOW_CONF}) but base normalisation "
                    f"still reflagged {n} row(s) -- _LOW_CONF and "
                    f"review.LOW_CONF have diverged")
        else:
            n_docs_pre_low_conf += 1

    base_scores = _score_all(base_dumps, golds, doc_ids, weights, params)
    base_stats = _stats(base_scores)
    base_pair_tax = _base_taxonomy(base_scores, doc_ids)
    base_false = _base_false_positions(base_scores, doc_ids)

    out: Dict = {
        "n_docs": len(doc_ids),
        "base": base_stats,
        "base_low_conf_reflagged": base_reflagged,
        "n_docs_pre_low_conf": n_docs_pre_low_conf,
        "flag_rules": {
            name: _price_flag_rule(name, base_dumps, golds, doc_ids, weights,
                                   params, base_stats, base_pair_tax, base_false)
            for name in flag_rules
        },
        "drop_rules": {
            name: _price_drop_rule(name, base_dumps, golds, doc_ids, weights,
                                   params, base_stats, base_pair_tax, base_false)
            for name in drop_rules
        },
    }

    if joint is not None:
        flags, drops = joint
        joint_dumps = {i: _with_flags(_with_drops(base_dumps[i], drops), flags)
                       for i in doc_ids}
        scores = _score_all(joint_dumps, golds, doc_ids, weights, params)
        stats = _stats(scores)
        d = _delta(base_stats, stats)
        better = _better_under(base_stats["counts"], stats["counts"])
        aap = d["auto_accept_precision"]
        passes = _passes_drop(d, better) and (not flags or aap is not None
                                              and aap > 0)
        out["joint"] = {
            "flag_rules": list(flags), "drop_rules": list(drops),
            "after": stats, "delta": d, "better_under": better,
            "precision_unmeasured": aap is None,
            "passes": passes,
        }

    return out
