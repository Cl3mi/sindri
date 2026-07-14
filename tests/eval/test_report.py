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
