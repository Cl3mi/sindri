"""`score --gentol-check`: the CPU gate for fill_general_tolerance.

Prices the fill exactly by scoring each split's dumps twice under today's
post-read code -- `reapply_current_code(d)` (control) and
`reapply_current_code(d, fill=True)` (arm) -- and attributing every matched
row's taxonomy transition. The fill touches tolerances only and matching reads
nominal and position, so the pairs must be identical on both sides; that is
asserted, not assumed, because the attribution means nothing otherwise.

Registered in docs/plans/2026-10-05-general-tolerance-registration.md §4:

  would_fix   flagged_error -> correct           (-1 each)
  would_break flagged_*     -> escaped_error      (+4 each)
  gate        fix >= 4 * Wilson95-upper(break rate) * n  AND  fix >= MIN_FIX

The break categories are a closed set, assigned first-match-wins in the
registered order, from GOLD -- which this module may read and the pipeline
never does. Counts and salted ids only; never a value."""
import math
from typing import Callable, Dict, Optional, Sequence, Tuple

from app.eval.models import GoldDoc, MatchParams, PredictionDump, ReviewCostWeights
from app.eval.normalize import values_equal
from app.eval.policy_check import _better_under, _delta, _score_all, _stats
from app.eval.reapply import reapply_current_code
from app.eval.report import WEIGHT_GRID, recompute_cost
from app.pipeline.general_tolerance import (document_class, fill_row,
                                            has_fit_notation, iso2768, _fmt)

MIN_FIX = 20          # registered: ~-0.4/doc on train's 49 docs, below the
                      # smallest change ever shipped (Ø-zone, -0.67)
BREAK_TO_FIX = 4      # a break costs +4 (flag 1 -> escaped 5), a fix saves 1
Z95 = 1.96

# First match wins, in exactly this order (registration §4).
BREAK_ORDER = ("fit_notation", "other:field", "other:gold_no_tolerance",
               "other:class_or_table", "printed_missed")
_FLAGGED = ("flagged_error", "flagged_correct")


def wilson_upper(k: int, n: int, z: float = Z95) -> float:
    """Upper end of the Wilson score interval for k successes in n. 1.0 at
    n = 0: with no evidence the break rate is unbounded, which fails the gate
    rather than passing it on an empty sample."""
    if n == 0:
        return 1.0
    p = k / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre + margin) / (1 + z2 / n)


def gate(fix: int, brk: int) -> Dict:
    n = fix + brk
    upper = wilson_upper(brk, n)
    bound = upper * n
    return {"would_fix": fix, "would_break": brk, "n": n,
            "break_rate": round(brk / n, 4) if n else None,
            "break_rate_upper95": round(upper, 4),
            "would_break_upper95": round(bound, 2),
            "min_fix": MIN_FIX,
            "passes": fix >= BREAK_TO_FIX * bound and fix >= MIN_FIX}


def gate_without_worst(per_doc: Dict[str, Tuple[int, int]]) -> Dict:
    """The gate recomputed with the single document carrying most breaks
    removed (ties: fewer fixes is worse). One drawing whose crops clip every
    printed tolerance should not decide a corpus-wide rule either way."""
    if not per_doc:
        return {"removed": None, **gate(0, 0)}
    worst = min(per_doc, key=lambda d: (-per_doc[d][1], per_doc[d][0], d))
    fix = sum(f for d, (f, _) in per_doc.items() if d != worst)
    brk = sum(b for d, (_, b) in per_doc.items() if d != worst)
    return {"removed": worst, **gate(fix, brk)}


def _symmetric_equals(gold, v) -> bool:
    return (values_equal(gold.upper_tol, _fmt(v))
            and values_equal(gold.lower_tol, "-" + _fmt(v)))


def _break_category(gold, filled, doc_cls: str) -> str:
    if has_fit_notation(gold.nominal):
        return "fit_notation"
    if (values_equal(gold.upper_tol, filled.upper_tol)
            and values_equal(gold.lower_tol, filled.lower_tol)):
        return "other:field"
    if not gold.upper_tol and not gold.lower_tol:
        return "other:gold_no_tolerance"
    for cls in "fmcv":
        for ct in ("Distance", "Radius"):
            if (cls, ct == "Radius") == (doc_cls, filled.char_type == "Radius"):
                continue
            v = iso2768(cls, filled.nominal, ct)
            if v is not None and _symmetric_equals(gold, v):
                return "other:class_or_table"
    return "printed_missed"


def _abs(v):
    from app.eval.normalize import _try_decimal
    d = _try_decimal(str(v or ""))
    return None if d is None else abs(d)


def printed_shape(gold, v) -> str:
    """For a `printed_missed` row: is gold's tolerance really something other
    than the general one, or the general MAGNITUDE written in another shape
    (an unsigned lower bound, say)? The second would make the category a
    measurement artefact, so it is counted rather than assumed away."""
    u, lo = _abs(gold.upper_tol), _abs(gold.lower_tol)
    if u is None or lo is None:
        return "one_sided"
    if u == lo == v:
        return "table_magnitude"
    return "symmetric_other" if u == lo else "asymmetric"


def _head(cat: str) -> str:
    return "other" if cat.startswith("other:") else cat


def _bump(d: Dict, k, n: int = 1) -> None:
    d[k] = d.get(k, 0) + n


def _slice() -> Dict:
    return {"would_fix": 0, "would_break": 0, "neutral": 0,
            "break_breakdown": {}}


def _attribute(control: Dict[str, PredictionDump], arm: Dict[str, PredictionDump],
               golds: Dict[str, GoldDoc], doc_ids: Sequence[str],
               weights: ReviewCostWeights, params: MatchParams,
               anonymizer: Callable[[str], str] = str) -> Dict:
    sc = _score_all(control, golds, doc_ids, weights, params)
    sa = _score_all(arm, golds, doc_ids, weights, params)

    coverage = {"docs": len(doc_ids), "by_source": {}, "class_histogram": {}}
    rows, matched_rows, transitions = {}, {}, {}
    breakdown, neutral_n, fix_n, brk_n, filled_false = {}, 0, 0, 0, 0
    shapes: Dict[str, int] = {}
    slices = {"by_class": {}, "by_kind": {}, "by_source": {}}
    per_doc: Dict[str, Tuple[int, int]] = {}

    for doc_id, s_c, s_a in zip(doc_ids, sc, sa):
        pairs_c = {(p.gold_balloon, p.pred_pos): p for p in s_c.pairs}
        pairs_a = {(p.gold_balloon, p.pred_pos): p for p in s_a.pairs}
        if set(pairs_c) != set(pairs_a):
            raise AssertionError(
                f"doc #{list(doc_ids).index(doc_id)}: the fill moved matching "
                f"({len(set(pairs_c) ^ set(pairs_a))} pair(s) differ) -- the "
                f"per-row attribution is meaningless; fix the arm, not this")

        dc = document_class(control[doc_id].result)
        src = ("conflict" if dc.conflict else dc.source) or "none"
        _bump(coverage["by_source"], src)
        if dc.cls:
            _bump(coverage["class_histogram"], dc.cls)

        ctl_by_pos = {c.pos: c for c in control[doc_id].result.characteristics}
        arm_by_pos = {c.pos: c for c in arm[doc_id].result.characteristics}
        outcome = {}
        for pos, c in ctl_by_pos.items():
            outcome[pos] = ("conflict" if dc.conflict
                            else fill_row(c.model_copy(deep=True), dc.cls))
            _bump(rows, outcome[pos])
        for pos in s_a.false_positions:
            if arm_by_pos[pos].tol_source == "general":
                filled_false += 1

        gold_by_num = {g.balloon: g for g in golds[doc_id].characteristics}
        d_fix = d_brk = 0
        for key, p_c in pairs_c.items():
            _bump(matched_rows, outcome[p_c.pred_pos])
            filled = arm_by_pos[p_c.pred_pos]
            if filled.tol_source != "general":
                continue
            t_c, t_a = p_c.taxonomy, pairs_a[key].taxonomy
            _bump(transitions, f"{t_c}->{t_a}")
            axes = {"by_class": dc.cls, "by_kind": filled.char_type,
                    "by_source": src}
            for axis, val in axes.items():
                slices[axis].setdefault(val, _slice())
            if t_c == "flagged_error" and t_a == "correct":
                fix_n += 1
                d_fix += 1
                for axis, val in axes.items():
                    slices[axis][val]["would_fix"] += 1
            elif t_c in _FLAGGED and t_a == "escaped_error":
                cat = _break_category(gold_by_num[p_c.gold_balloon], filled,
                                      dc.cls)
                brk_n += 1
                d_brk += 1
                _bump(breakdown, cat)
                if cat == "printed_missed":
                    _bump(shapes, printed_shape(
                        gold_by_num[p_c.gold_balloon],
                        iso2768(dc.cls, filled.nominal, filled.char_type)))
                for axis, val in axes.items():
                    slices[axis][val]["would_break"] += 1
                    _bump(slices[axis][val]["break_breakdown"], cat)
            else:
                neutral_n += 1
                for axis, val in axes.items():
                    slices[axis][val]["neutral"] += 1
        per_doc[anonymizer(doc_id)] = (d_fix, d_brk)

    stats_c, stats_a = _stats(sc), _stats(sa)
    delta = _delta(stats_c, stats_a)
    ordered = {k: breakdown[k] for k in BREAK_ORDER if k in breakdown}
    heads: Dict[str, int] = {}
    for k, v in ordered.items():
        _bump(heads, _head(k), v)

    # The registered exactness check: re-scored cost must move by exactly
    # what the attributed transitions imply (-1 per fix, +4 per break, and
    # whatever the neutral moves cost), because the fill cannot move matching
    # or touch a false detection's price. Anything else means a path is wrong.
    n_docs = len(doc_ids)
    price = {"correct": 0.0, "flagged_correct": weights.flag,
             "flagged_error": weights.flag, "escaped_error": weights.escaped}
    expected = sum(n * (price[t.split("->")[1]] - price[t.split("->")[0]])
                   for t, n in transitions.items()) / n_docs
    reconciles = math.isclose(delta["cost"], expected, abs_tol=1e-3)

    total_brk = sum(b for _, b in per_doc.values())
    g = gate(fix_n, brk_n)
    return {
        "n_docs": n_docs,
        "coverage": coverage,
        "rows": rows,
        "matched_rows": matched_rows,
        "matched_filled": sum(transitions.values()),
        "filled_false_detections": filled_false,
        "transitions": transitions,
        "would_fix": fix_n,
        "would_break": brk_n,
        "break_breakdown": ordered,
        "break_heads": heads,
        "printed_missed_shape": shapes,
        "neutral": neutral_n,
        "gate": g,
        "verdict": "PASS" if g["passes"] else "FAIL",
        "concentration": {
            "docs": [{"doc": d, "would_fix": f, "would_break": b}
                     for d, (f, b) in sorted(per_doc.items(),
                                             key=lambda kv: (-kv[1][1], kv[0]))
                     if f or b],
            "max_doc_break_share": (round(max(b for _, b in per_doc.values())
                                          / total_brk, 4)
                                    if total_brk else None),
            "gate_without_worst": gate_without_worst(per_doc),
        },
        "slices": slices,
        "control": stats_c,
        "arm": stats_a,
        "delta": delta,
        "better_under": _better_under(stats_c["counts"], stats_a["counts"]),
        "cost_per_weighting": [
            {"weights": w.model_dump(),
             "control": round(recompute_cost(stats_c["counts"], w) / n_docs, 4),
             "arm": round(recompute_cost(stats_a["counts"], w) / n_docs, 4)}
            for w in WEIGHT_GRID],
        "reconciles": reconciles,
    }


def gentol_report(dumps: Dict[str, PredictionDump], golds: Dict[str, GoldDoc],
                  doc_ids: Sequence[str], weights: ReviewCostWeights,
                  params: MatchParams,
                  anonymizer: Optional[Callable[[str], str]] = None) -> Dict:
    """Control and arm from the ORIGINAL dumps, both through today's post-read
    code, so the only difference between them is the fill. Never mutates
    `dumps`."""
    if not doc_ids:
        raise ValueError("gentol_report: doc_ids is empty -- nothing to price")
    control = {i: reapply_current_code(dumps[i]) for i in doc_ids}
    arm = {i: reapply_current_code(dumps[i], fill=True) for i in doc_ids}
    return _attribute(control, arm, golds, doc_ids, weights, params,
                      anonymizer or str)
