"""Aggregate DocScores into a RunReport; compare two RunReports with paired
per-document deltas + bootstrap CIs. compare_runs is the comparability
gatekeeper: it RAISES on any mismatch (doc set, per-doc gold hash, weights,
match params) instead of producing a quietly meaningless number."""
import random
from typing import Dict, List

from app.eval.models import (DocScore, MatchParams, ReviewCostWeights,
                             RunConfig, RunReport, SCHEMA_VERSION)

N_BOOTSTRAP = 10_000


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
    warnings. Deterministic for fixed seed."""
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
        "warnings": warnings,
    }
