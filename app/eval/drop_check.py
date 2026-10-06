"""`score --drop-check`: price the registered phantom-drop configurations
(docs/plans/2026-10-06-phantom-drops-registration.md).

Control = today's post-read code on the stored dumps (`reapply_current_code`:
re-parse, flags, active drops). Arm = that control with the configuration's
drops applied on top, then RE-SCORED, matching included, because dropping a
matched row can let a neighbour pair. The metric is delivered precision, i.e.
correct / everything shipped unflagged, phantoms included. The client ranks it
first at any recall, so recall, cost and the six weightings are reported here
and decide nothing.

Counts only; never a value."""
from typing import Dict, Optional, Sequence

from app.eval.models import GoldDoc, MatchParams, PredictionDump, ReviewCostWeights
from app.eval.policy_check import _better_under, _score_all
from app.eval.reapply import reapply_current_code
from app.eval.report import WEIGHT_GRID, auto_accept_precision, recompute_cost
from app.pipeline.policy_rules import DROP_RULES, apply_drop_rules

# The policy the phantom drops were registered and priced against
# (2026-10-06): stage 1 only. Pinned, not read from ACTIVE_DROP_STAGES -- once
# the selected configuration shipped, "today's code" already contains it, and
# pricing it against itself would show a delta of exactly zero.
BASE_DROP_STAGES = (("contained_duplicate",),)

_DOSES = ("conf_below_090", "conf_below_095", "conf_below_099")
# Registered order -- it is also the final tie-break, so it must not be sorted.
CONFIGS: Dict[str, tuple] = {
    **{d: (d,) for d in _DOSES},
    "material_kind": ("material_kind",),
    "tight_cluster": ("tight_cluster",),
}
for _d in _DOSES:
    CONFIGS[f"{_d}+material_kind"] = (_d, "material_kind")
    CONFIGS[f"{_d}+material_kind+tight_cluster"] = (_d, "material_kind",
                                                    "tight_cluster")


def _stats(scores, n_docs: int) -> Dict:
    counts: Dict[str, int] = {}
    n_gold = phantom = 0
    for s in scores:
        n_gold += s.n_gold
        phantom += s.false_unflagged
        for k, v in s.counts.items():
            counts[k] = counts.get(k, 0) + v
    correct, escaped = counts.get("correct", 0), counts.get("escaped_error", 0)
    delivered = correct + escaped + phantom
    return {
        "correct": correct, "escaped": escaped, "phantom": phantom,
        "delivered": delivered,
        "delivered_precision": (round(correct / delivered, 4)
                                if delivered else None),
        "matched_precision": auto_accept_precision(counts),
        "recall": (round((n_gold - counts.get("missed", 0)) / n_gold, 4)
                   if n_gold else None),
        "cost": round(sum(s.review_cost for s in scores) / n_docs, 4),
        "cost_per_weighting": [round(recompute_cost(counts, w) / n_docs, 4)
                               for w in WEIGHT_GRID],
        "counts": counts,
    }


def passes(control: Dict, arm: Dict) -> bool:
    """The registered keep rule. An empty delivered set has no precision and
    never passes -- that is the only guard against 'deliver nothing'."""
    if not arm["delivered"] or arm["delivered_precision"] is None:
        return False
    if control["delivered_precision"] is not None and \
            arm["delivered_precision"] <= control["delivered_precision"]:
        return False
    cm, am = control["matched_precision"], arm["matched_precision"]
    if cm is not None and (am is None or am < cm):
        return False
    return True


def drop_report(dumps: Dict[str, PredictionDump], golds: Dict[str, GoldDoc],
                doc_ids: Sequence[str], weights: ReviewCostWeights,
                params: MatchParams,
                configs: Optional[Sequence[str]] = None) -> Dict:
    names = list(configs) if configs is not None else list(CONFIGS)
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise ValueError(f"unknown drop configuration(s): {unknown}; "
                         f"registered: {list(CONFIGS)}")
    if not doc_ids:
        raise ValueError("drop_report: doc_ids is empty -- nothing to price")
    for rules in CONFIGS.values():
        assert all(r in DROP_RULES for r in rules), rules

    control = {i: reapply_current_code(dumps[i], drop_stages=BASE_DROP_STAGES)
               for i in doc_ids}
    n_docs = len(doc_ids)
    c_scores = _score_all(control, golds, doc_ids, weights, params)
    c_stats = _stats(c_scores, n_docs)

    out_configs: Dict[str, Dict] = {}
    for name in names:
        arm = {}
        for i in doc_ids:
            d = control[i].model_copy(deep=True)
            d.result.characteristics = apply_drop_rules(
                d.result.characteristics, CONFIGS[name])
            arm[i] = d
        a_scores = _score_all(arm, golds, doc_ids, weights, params)
        a_stats = _stats(a_scores, n_docs)
        out_configs[name] = {
            "arm": a_stats,
            "correct_lost": c_stats["correct"] - a_stats["correct"],
            "delta_delivered_precision": (
                None if None in (a_stats["delivered_precision"],
                                 c_stats["delivered_precision"])
                else round(a_stats["delivered_precision"]
                           - c_stats["delivered_precision"], 4)),
            "better_under": _better_under(c_stats["counts"], a_stats["counts"]),
            "passes": passes(c_stats, a_stats),
        }

    passing = [n for n in names if out_configs[n]["passes"]]
    selected = None
    if passing:
        order = {n: k for k, n in enumerate(CONFIGS)}
        selected = min(passing, key=lambda n: (
            -out_configs[n]["arm"]["delivered_precision"],
            -out_configs[n]["arm"]["correct"], order[n]))
    return {"n_docs": n_docs, "control": c_stats, "configs": out_configs,
            "selected": selected}
