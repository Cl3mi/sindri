"""Aggregate DocScores into a RunReport; compare two RunReports with paired
per-document deltas + bootstrap CIs. compare_runs is the comparability
gatekeeper: it RAISES on any mismatch (doc set, per-doc gold hash, weights,
match params) instead of producing a quietly meaningless number."""
import random
from typing import Dict, List, Tuple

from app.eval.models import (DocScore, MatchParams, ReviewCostWeights,
                             RunConfig, RunReport, SCHEMA_VERSION)

N_BOOTSTRAP = 10_000

# How many clamped documents `summarize` lists in detail. Deliberately NOT
# summarize()'s `top`, which sizes the worst-docs triage list: a diagnostic list
# must not shorten because someone retuned an unrelated knob. The true count is
# always clamped_vs_unclamped.clamped.n, so truncation is visible by comparing
# len(clamped_docs) against it.
CLAMP_LIST_MAX = 25


def aggregate(run_name: str, config: RunConfig, weights: ReviewCostWeights,
              match_params: MatchParams, doc_scores: List[DocScore],
              splits_hash: str = "", split_used: str = "all") -> RunReport:
    n_gold = sum(d.n_gold for d in doc_scores)
    n_pred = sum(d.n_pred for d in doc_scores)
    matched_gold = sum(round(d.recall * d.n_gold) for d in doc_scores)
    matched_pred = sum(round(d.precision * d.n_pred) for d in doc_scores)
    taxonomy: Dict[str, int] = {}
    for d in doc_scores:
        for k, v in d.counts.items():
            taxonomy[k] = taxonomy.get(k, 0) + v
    escaped = taxonomy.get("escaped_error", 0)
    return RunReport(
        run_name=run_name, config=config, weights=weights,
        match_params=match_params, splits_hash=splits_hash,
        split_used=split_used,
        doc_scores=sorted(doc_scores, key=lambda d: d.doc_id),
        mean_review_cost=(sum(d.review_cost for d in doc_scores)
                          / len(doc_scores)) if doc_scores else 0.0,
        micro_recall=(matched_gold / n_gold) if n_gold else 1.0,
        micro_precision=(matched_pred / n_pred) if n_pred else 1.0,
        escaped_rate=(escaped / n_gold) if n_gold else 0.0,
        taxonomy=taxonomy,
    )


def _note_counts(report: RunReport) -> Tuple[Dict[str, int], int]:
    """Aggregate the tags scoring left on matched pairs.

    Reads ONLY the `cause:` and `misplaced` tokens — a fixed vocabulary written
    by score._cause. It never touches `field_errors`, which spells out client
    values, so the digest stays safe to commit and to show an agent."""
    causes: Dict[str, int] = {}
    misplaced = 0
    for d in report.doc_scores:
        for p in d.pairs:
            for note in p.notes:
                if note.startswith("cause:"):
                    key = note.split(":", 1)[1]
                    causes[key] = causes.get(key, 0) + 1
                elif note == "misplaced":
                    misplaced += 1
    return causes, misplaced


def _clamp_split(report: RunReport, anonymizer,
                 limit: int = CLAMP_LIST_MAX) -> Tuple[List, Dict]:
    """Clamped documents, and clamped-vs-unclamped macro means.

    render.py reduces dpi on sheets that would exceed the 80 MP budget, so those
    documents are extracted at up to a third of the requested resolution. Before
    anyone reads a low recall as a statement about the model, this says whether
    the misses concentrate there. Ids come from the local salt, so they join to
    worst_docs — unlike the predict log's, which are minted per container.

    THREE buckets, not two. effective_dpi is 0.0 in any DocScore written before
    the field existed, and folding those into "unclamped" would report "nothing
    was clamped" for a run where four documents were — a confident wrong answer
    from a view whose entire job is interpretability. Unknown gets its own
    bucket, so `unknown_dpi.n > 0` reads as "re-score before believing this"."""
    requested = report.config.dpi
    known = [d for d in report.doc_scores if d.effective_dpi > 0.0]
    unknown = [d for d in report.doc_scores if d.effective_dpi <= 0.0]
    clamped = sorted((d for d in known if d.effective_dpi < requested - 0.5),
                     key=lambda d: d.effective_dpi)
    clamped_ids = {d.doc_id for d in clamped}
    rest = [d for d in known if d.doc_id not in clamped_ids]

    def block(docs):
        n = len(docs)
        return {
            "n": n,
            # MACRO: unweighted mean over documents. The headline micro_recall
            # pools rows across the run, so the two are different statistics —
            # the name says so, because an unlabelled "mean_recall" next to a
            # micro headline is exactly the mis-read this plan exists to prevent.
            "macro_mean_recall": (round(sum(d.recall for d in docs) / n, 4)
                                  if n else None),
            "mean_review_cost": (round(sum(d.review_cost for d in docs) / n, 2)
                                 if n else None),
        }

    listed = [{"doc": anonymizer(d.doc_id),
               "effective_dpi": round(d.effective_dpi),
               "recall": round(d.recall, 4),
               "review_cost": d.review_cost} for d in clamped[:limit]]
    return listed, {"clamped": block(clamped), "unclamped": block(rest),
                    "unknown_dpi": block(unknown)}


def _kind_totals(report: RunReport) -> Tuple[Dict[str, int], Dict[str, int],
                                             Dict[str, int]]:
    """Predictions, unmatched predictions, and MATCHED predictions by detector
    kind, summed over the run. Kind names are a fixed detector vocabulary, never
    client text.

    The three reconcile per kind: preds[k] == matched[k] + false[k], because a
    prediction either paired with in-scope gold or it did not. That identity is
    what turns "N false detections are out-of-scope kinds" from a quoted number
    into a checked one."""
    preds: Dict[str, int] = {}
    false: Dict[str, int] = {}
    matched: Dict[str, int] = {}
    for d in report.doc_scores:
        for k, v in d.pred_kinds.items():
            preds[k] = preds.get(k, 0) + v
        for k, v in d.false_kinds.items():
            false[k] = false.get(k, 0) + v
        for k, v in d.matched_kinds.items():
            matched[k] = matched.get(k, 0) + v
    return preds, false, matched


def summarize(report: RunReport, anonymizer, top: int = 10) -> Dict:
    """Privacy-safe digest of a run: aggregate metrics only, doc ids hashed.

    A RunReport embeds client values — `DocScore.pairs[].field_errors` spells out
    gold vs predicted (e.g. "nominal: '6,5'!='5,5'"). This function is the only
    sanctioned way to look at a run: it reads none of that, so the result can be
    shown to an AI agent, committed, or pasted into a ticket."""
    causes, misplaced = _note_counts(report)
    clamped_docs, clamp_split = _clamp_split(report, anonymizer)
    pred_kinds, false_kinds, matched_kinds = _kind_totals(report)
    worst = sorted(report.doc_scores, key=lambda d: (-d.review_cost, d.doc_id))
    return {
        "run": report.run_name,
        "split": report.split_used,
        "splits_hash": report.splits_hash,
        "n_docs": len(report.doc_scores),
        "n_gold": sum(d.n_gold for d in report.doc_scores),
        "n_pred": sum(d.n_pred for d in report.doc_scores),
        "mean_review_cost": report.mean_review_cost,
        "micro_recall": report.micro_recall,
        "micro_precision": report.micro_precision,
        "escaped_rate": report.escaped_rate,
        "taxonomy": dict(report.taxonomy),
        # Handoff §6 routing: misparse -> parser hardening (Rung 1),
        # misread -> prompts (Rung 2) then LoRA (Rung 3).
        "error_causes": causes,
        # Matched, but further from its gold balloon than misplaced_frac — a
        # geometry-quality signal that is not an error in its own right.
        "misplaced_matches": misplaced,
        "clamped_docs": clamped_docs,
        "clamped_vs_unclamped": clamp_split,
        # Gold is filtered to match_params.score_kinds; predictions are not. A
        # non-dimension kind here is a detection the metric cannot credit.
        "pred_kinds": pred_kinds,
        "false_detections_by_kind": false_kinds,
        # Kinds that DID match in-scope gold. A non-"dimension" entry here is a
        # match that filtering predictions to score_kinds would destroy, turning
        # a w=2 false detection into a w=10 miss — so this is the number that
        # says whether that filter helps or hurts.
        "matched_by_pred_kind": matched_kinds,
        "config": report.config.model_dump(),
        "weights": report.weights.model_dump(),
        "match_params": report.match_params.model_dump(),
        "worst_docs": [{"doc": anonymizer(d.doc_id),
                        "review_cost": d.review_cost} for d in worst[:top]],
    }


def recompute_cost(counts: Dict[str, int], weights: ReviewCostWeights) -> float:
    """Review cost for any weight vector, straight from the taxonomy counts.

    Scoring already recorded what happened; only the price tag changes. This is
    what lets the client's real weights arrive late without re-running anything
    — and lets a verdict be checked against every plausible weighting."""
    flagged = counts.get("flagged_correct", 0) + counts.get("flagged_error", 0)
    return (weights.miss * counts.get("missed", 0)
            + weights.escaped * counts.get("escaped_error", 0)
            + weights.false * counts.get("false_detection", 0)
            + weights.flag * flagged)


# Plausible reviewer economics, spanning the range the client's real numbers
# could land in: a miss always costs most, a flag always least, but how much
# more varies. If a verdict holds across all of these, the exact numbers do not
# change the decision.
WEIGHT_GRID = (
    ReviewCostWeights(miss=10, escaped=5, false=2, flag=1),    # documented default
    ReviewCostWeights(miss=20, escaped=8, false=2, flag=1),    # misses dominate
    ReviewCostWeights(miss=5, escaped=4, false=2, flag=1),     # flatter
    ReviewCostWeights(miss=10, escaped=5, false=4, flag=2),    # phantoms costly
    ReviewCostWeights(miss=8, escaped=8, false=1, flag=1),     # silent errors as bad
    ReviewCostWeights(miss=3, escaped=2, false=1, flag=1),     # nearly flat
)


def _weight_sensitivity(a: RunReport, b: RunReport) -> Dict:
    """How often B beats A across the plausible weightings."""
    wins, deltas = 0, []
    for weights in WEIGHT_GRID:
        cost_a = sum(recompute_cost(d.counts, weights) for d in a.doc_scores)
        cost_b = sum(recompute_cost(d.counts, weights) for d in b.doc_scores)
        n = max(1, len(a.doc_scores))
        deltas.append(round((cost_b - cost_a) / n, 4))
        if cost_b < cost_a:
            wins += 1
    fraction = wins / len(WEIGHT_GRID)
    return {
        "n_weight_vectors": len(WEIGHT_GRID),
        "b_better_fraction": fraction,
        "mean_delta_per_weighting": deltas,
        # A verdict that flips with the weights needs the client's real numbers;
        # one that holds everywhere does not.
        "robust": fraction in (0.0, 1.0),
    }


def _check_comparable(a: RunReport, b: RunReport) -> None:
    ids_a = [d.doc_id for d in a.doc_scores]
    ids_b = [d.doc_id for d in b.doc_scores]
    if ids_a != ids_b:
        raise ValueError(f"doc set differs: {len(ids_a)} vs {len(ids_b)} docs "
                         f"(runs are only comparable on the identical corpus)")
    for da, db in zip(a.doc_scores, b.doc_scores):
        if da.gold_hash != db.gold_hash:
            raise ValueError(f"gold differs for {da.doc_id}: scored against "
                             f"different gold data — re-score both runs")
    if a.weights != b.weights:
        raise ValueError("weights differ between runs — re-score with one set")
    if a.match_params != b.match_params:
        raise ValueError("match params differ between runs — re-score with one set")
    if a.splits_hash and b.splits_hash and a.splits_hash != b.splits_hash:
        raise ValueError("splits differ between runs")


def compare_runs(a: RunReport, b: RunReport, seed: int = 13,
                 n_boot: int = N_BOOTSTRAP) -> Dict:
    """Paired comparison: delta = b - a per document (negative = b better).
    Returns headline deltas, a bootstrap CI on the mean delta, and regression
    warnings. Deterministic for fixed seed.

    The bootstrap CI is uninformative for very small doc sets (n < ~10): with
    n=1 any nonzero delta is reported significant."""
    _check_comparable(a, b)
    deltas = [db.review_cost - da.review_cost
              for da, db in zip(a.doc_scores, b.doc_scores)]
    n = len(deltas)
    mean_delta = sum(deltas) / n if n else 0.0

    rng = random.Random(seed)
    boot_means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(n_boot)) if n else [0.0]
    ci95 = (boot_means[int(0.025 * len(boot_means))],
            boot_means[int(0.975 * len(boot_means)) - 1])
    significant = bool(deltas) and (ci95[1] < 0.0 or ci95[0] > 0.0)

    warnings = []
    improved = mean_delta < 0
    if improved and b.micro_recall < a.micro_recall - 0.005:
        warnings.append(
            f"review-cost improved but recall dropped "
            f"{a.micro_recall:.3f} -> {b.micro_recall:.3f} — likely a net "
            f"review-time LOSS on missed callouts (handoff §6 regression guard)")
    if improved and b.escaped_rate > a.escaped_rate + 0.005:
        warnings.append(
            f"review-cost improved but escaped-error rate rose "
            f"{a.escaped_rate:.3f} -> {b.escaped_rate:.3f} — silent errors up")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_a": a.run_name, "run_b": b.run_name, "n_docs": n,
        "mean_delta": round(mean_delta, 4),
        "ci95": [round(ci95[0], 4), round(ci95[1], 4)],
        "significant": significant,
        "per_doc_deltas": {da.doc_id: round(d, 4)
                           for da, d in zip(a.doc_scores, deltas)},
        "headline": {
            a.run_name: {"mean_review_cost": a.mean_review_cost,
                         "recall": a.micro_recall,
                         "escaped_rate": a.escaped_rate},
            b.run_name: {"mean_review_cost": b.mean_review_cost,
                         "recall": b.micro_recall,
                         "escaped_rate": b.escaped_rate},
        },
        "weight_sensitivity": _weight_sensitivity(a, b),
        "warnings": warnings,
    }
