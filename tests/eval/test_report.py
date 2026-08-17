import pytest

from app.eval.models import (DocScore, MatchParams, ReviewCostWeights,
                             RunConfig)
from app.eval.report import aggregate, compare_runs


def _doc(doc_id, cost, recall=1.0, escaped=0, gold_hash="g" + "0" * 15,
         n_gold=10):
    counts = {"correct": n_gold - escaped, "escaped_error": escaped}
    return DocScore(doc_id=doc_id, gold_hash=gold_hash, n_gold=n_gold,
                    n_pred=n_gold, counts=counts, review_cost=cost,
                    recall=recall, precision=1.0,
                    escaped_rate=escaped / n_gold)


def _run(name, costs, recall=1.0, escaped=0):
    scores = [_doc(f"D{i}", c, recall=recall, escaped=escaped)
              for i, c in enumerate(costs)]
    return aggregate(name, RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), scores)


def test_aggregate_computes_headline_numbers():
    r = _run("base", [10.0, 20.0], escaped=1)
    assert r.mean_review_cost == 15.0
    assert r.micro_recall == 1.0
    assert r.taxonomy["escaped_error"] == 2
    assert r.escaped_rate == pytest.approx(0.1)


def test_compare_paired_delta_and_significance():
    a = _run("a", [10.0, 12.0, 14.0, 16.0])
    b = _run("b", [8.0, 10.0, 12.0, 14.0])       # uniformly 2 better
    cmp = compare_runs(a, b, seed=13)
    assert cmp["mean_delta"] == -2.0
    assert cmp["ci95"][1] <= 0.0
    assert cmp["significant"] is True
    assert cmp["n_docs"] == 4


def test_compare_self_is_zero_and_not_significant():
    a = _run("a", [10.0, 12.0, 14.0, 16.0])
    cmp = compare_runs(a, a, seed=13)
    assert cmp["mean_delta"] == 0.0
    assert cmp["significant"] is False


def test_regression_guard_flags_recall_drop_on_improved_cost():
    a = _run("a", [10.0, 10.0, 10.0, 10.0], recall=0.95)
    b = _run("b", [8.0, 8.0, 8.0, 8.0], recall=0.90)
    cmp = compare_runs(a, b, seed=13)
    assert any("recall" in w for w in cmp["warnings"])


def _report_with_client_values():
    """A report shaped like a real one: pairs carry the actual gold/predicted
    values in field_errors, and doc_ids are client part numbers."""
    from app.eval.models import MatchedPair
    pair = MatchedPair(gold_balloon=2, pred_pos=2, distance_frac=0.001,
                       fields_correct=False,
                       field_errors=["nominal: '6,5'!='5,5'"],
                       flagged=False, taxonomy="escaped_error",
                       notes=["cause:misread"])
    d1 = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=10, n_pred=10,
                  pairs=[pair], counts={"correct": 9, "escaped_error": 1},
                  review_cost=5.0, recall=1.0, precision=1.0, escaped_rate=0.1)
    d2 = DocScore(doc_id="T1025206_D", gold_hash="g" * 16, n_gold=10, n_pred=10,
                  counts={"correct": 10}, review_cost=1.0, recall=1.0,
                  precision=1.0, escaped_rate=0.0)
    return aggregate("baseline", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d1, d2])


def test_summarize_is_value_free_and_anonymized():
    import json
    from app.eval.anon import Anonymizer
    from app.eval.report import summarize
    s = summarize(_report_with_client_values(), Anonymizer("salt"))
    blob = json.dumps(s, ensure_ascii=False)
    for leak in ("field_errors", "raw_text", "6,5", "5,5",
                 "T1025300_B", "T1025206_D"):
        assert leak not in blob, f"summary leaked {leak!r}"
    assert s["n_docs"] == 2
    assert s["taxonomy"]["escaped_error"] == 1
    assert s["mean_review_cost"] == 3.0
    assert s["config"]["model_id"] == "stub"


def test_summarize_ranks_worst_docs_by_hashed_id_for_triage():
    from app.eval.anon import Anonymizer
    from app.eval.report import summarize
    a = Anonymizer("salt")
    s = summarize(_report_with_client_values(), a)
    assert s["worst_docs"][0] == {"doc": a("T1025300_B"), "review_cost": 5.0}


def test_cost_can_be_recomputed_for_other_weights_without_rescoring():
    """DocScore keeps the taxonomy counts, so any weight vector's cost is
    derivable. That is what lets the client's real weights arrive late without
    invalidating a baseline."""
    from app.eval.report import recompute_cost
    counts = {"correct": 5, "missed": 2, "escaped_error": 1,
              "false_detection": 3, "flagged_correct": 4, "flagged_error": 1}
    assert recompute_cost(counts, ReviewCostWeights()) == (
        10 * 2 + 5 * 1 + 2 * 3 + 1 * 5)
    # all-ones: 2 missed + 1 escaped + 3 false + 5 flagged rows
    assert recompute_cost(counts, ReviewCostWeights(miss=1, escaped=1,
                                                    false=1, flag=1)) == 11


def test_weight_sweep_says_whether_a_win_survives_any_plausible_weights():
    from app.eval.report import compare_runs
    better = _run("b", [8.0, 8.0, 8.0, 8.0])
    worse = _run("a", [10.0, 10.0, 10.0, 10.0])
    # make the taxonomy differ in the way the cost implies: b misses less
    for d in worse.doc_scores:
        d.counts = {"correct": 8, "missed": 1, "escaped_error": 0,
                    "false_detection": 0}
    for d in better.doc_scores:
        d.counts = {"correct": 9, "missed": 0, "escaped_error": 0,
                    "false_detection": 1}
    cmp = compare_runs(worse, better, seed=13)
    sweep = cmp["weight_sensitivity"]
    assert sweep["n_weight_vectors"] >= 4
    # fewer misses at the cost of one phantom wins under every sane weighting
    assert sweep["b_better_fraction"] == 1.0
    assert sweep["robust"] is True


def test_weight_sweep_flags_a_verdict_that_depends_on_the_weights():
    from app.eval.report import compare_runs
    a = _run("a", [10.0, 10.0])
    b = _run("b", [10.0, 10.0])
    # b trades one miss for many flagged rows: good if misses are costly,
    # bad if reviewer time per flag dominates
    for d in a.doc_scores:
        d.counts = {"missed": 1, "flagged_correct": 0}
    for d in b.doc_scores:
        d.counts = {"missed": 0, "flagged_correct": 9}
    cmp = compare_runs(a, b, seed=13)
    assert cmp["weight_sensitivity"]["robust"] is False


def test_guards_refuse_incomparable_runs():
    a = _run("a", [10.0, 12.0])
    b = _run("b", [10.0])                                   # different doc set
    with pytest.raises(ValueError, match="doc set"):
        compare_runs(a, b)

    c = _run("c", [10.0, 12.0])
    c.weights = ReviewCostWeights(miss=99)                  # different weights
    with pytest.raises(ValueError, match="weights"):
        compare_runs(a, c)

    d = _run("d", [10.0, 12.0])
    d.doc_scores[0].gold_hash = "f" * 16                    # different gold
    with pytest.raises(ValueError, match="gold"):
        compare_runs(a, d)
