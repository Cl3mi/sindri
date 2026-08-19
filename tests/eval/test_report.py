import json

import pytest

from app.eval.models import (DocScore, MatchedPair, MatchParams,
                             ReviewCostWeights, RunConfig)
from app.eval.report import aggregate, compare_runs, summarize


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


def _pair(balloon, taxonomy, notes, correct=False, flagged=False):
    return MatchedPair(gold_balloon=balloon, pred_pos=balloon,
                       distance_frac=0.01, fields_correct=correct,
                       flagged=flagged, taxonomy=taxonomy, notes=notes)


def test_summary_aggregates_error_causes_and_misplaced_matches():
    """Handoff §6 routes on the cause split: misparse -> parser hardening,
    misread -> Rung 2/3 perception. It is written into MatchedPair.notes and was
    never aggregated, so the decision had no number behind it."""
    pairs = [
        _pair(1, "escaped_error", ["cause:misread"]),
        _pair(2, "flagged_error", ["misplaced", "cause:misparse"], flagged=True),
        _pair(3, "escaped_error", ["cause:misread"]),
        _pair(4, "correct", [], correct=True),
    ]
    doc = DocScore(doc_id="D1", gold_hash="g" + "0" * 15, n_gold=4, n_pred=4,
                   pairs=pairs, counts={"escaped_error": 2, "flagged_error": 1,
                                        "correct": 1},
                   review_cost=11.0, recall=1.0, precision=1.0, escaped_rate=0.5)
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [doc])

    digest = summarize(report, lambda d: "hashed")

    assert digest["error_causes"] == {"misread": 2, "misparse": 1}
    assert digest["misplaced_matches"] == 1


def test_summary_cause_aggregation_never_reads_client_values():
    """field_errors spells out gold vs predicted ("nominal: '6,5'!='5,5'").
    summarize() is the one sanctioned view of a run; it must stay values-blind."""
    pairs = [_pair(1, "escaped_error", ["cause:misread"])]
    pairs[0].field_errors = ["nominal: '6,5'!='5,5'"]
    doc = DocScore(doc_id="D1", gold_hash="g" + "0" * 15, n_gold=1, n_pred=1,
                   pairs=pairs, counts={"escaped_error": 1}, review_cost=5.0,
                   recall=1.0, precision=1.0, escaped_rate=1.0)
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [doc])

    blob = json.dumps(summarize(report, lambda d: "hashed"))

    assert "6,5" not in blob and "5,5" not in blob
    assert "nominal" not in blob


def _dpi_doc(doc_id, dpi, recall, cost):
    return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=10, n_pred=10,
                    counts={"correct": 10}, review_cost=cost, recall=recall,
                    precision=1.0, escaped_rate=0.0, effective_dpi=dpi)


def test_summary_separates_clamped_documents_from_the_rest():
    """The run log's clamped ids came from a throwaway container salt and cannot
    be joined to a locally-scored report. The dumps carry the real scale, so the
    comparison is recoverable here — with local-salt ids that DO join."""
    docs = [_dpi_doc("D1", 300.0, 0.60, 100.0),
            _dpi_doc("D2", 300.0, 0.50, 140.0),
            _dpi_doc("D3", 109.0, 0.20, 400.0),
            _dpi_doc("D4", 225.0, 0.30, 300.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")
    split = digest["clamped_vs_unclamped"]

    assert [c["doc"] for c in digest["clamped_docs"]] == ["hash-D3", "hash-D4"]
    assert digest["clamped_docs"][0]["effective_dpi"] == 109
    assert split["clamped"]["n"] == 2
    assert split["unclamped"]["n"] == 2
    assert split["unknown_dpi"]["n"] == 0
    # Macro means: unweighted over documents. NOT comparable to the headline
    # micro_recall, which pools rows — hence the name.
    assert split["clamped"]["macro_mean_recall"] == 0.25
    assert split["unclamped"]["macro_mean_recall"] == 0.55
    # The three buckets partition the corpus, so nothing can be double-counted
    # or silently dropped.
    assert (split["clamped"]["n"] + split["unclamped"]["n"]
            + split["unknown_dpi"]["n"]) == digest["n_docs"]


def test_summary_reports_no_clamped_documents_when_none_were_clamped():
    docs = [_dpi_doc("D1", 300.0, 0.6, 100.0), _dpi_doc("D2", 300.0, 0.5, 140.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")

    assert digest["clamped_docs"] == []
    assert digest["clamped_vs_unclamped"]["clamped"]["n"] == 0
    assert digest["clamped_vs_unclamped"]["clamped"]["macro_mean_recall"] is None


def test_summary_puts_documents_without_a_recorded_dpi_in_their_own_bucket():
    """effective_dpi is 0.0 in every DocScore written before the field existed —
    i.e. in the report this plan exists to interpret, until Task 5 re-scores it.
    Calling those "unclamped" would report "nothing was clamped" for a run where
    four documents were, which is worse than reporting nothing. So: third bucket,
    and clamped/unclamped stay empty until the run is actually re-scored."""
    docs = [_dpi_doc("D1", 0.0, 0.60, 100.0), _dpi_doc("D2", 0.0, 0.50, 140.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")
    split = digest["clamped_vs_unclamped"]

    assert digest["clamped_docs"] == []
    assert split["unknown_dpi"]["n"] == 2
    assert split["clamped"]["n"] == 0
    assert split["unclamped"]["n"] == 0          # NOT 2 — this is the whole point
    assert split["unclamped"]["macro_mean_recall"] is None


def test_summary_aggregates_prediction_kinds_across_documents():
    def doc(doc_id, pred_kinds, false_kinds):
        # counts is derived from false_kinds, never stated independently: a
        # fixture that contradicts its own taxonomy cannot test a conservation
        # identity.
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=5,
                        n_pred=sum(pred_kinds.values()),
                        counts={"correct": 5,
                                "false_detection": sum(false_kinds.values())},
                        review_cost=10.0, recall=1.0, precision=1.0,
                        escaped_rate=0.0, pred_kinds=pred_kinds,
                        false_kinds=false_kinds)

    docs = [doc("D1", {"dimension": 20, "note": 5}, {"note": 5}),
            doc("D2", {"dimension": 18, "surface": 3}, {"surface": 3,
                                                        "dimension": 2})]
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), docs)

    digest = summarize(report, lambda d: "hashed")

    assert digest["pred_kinds"] == {"dimension": 38, "note": 5, "surface": 3}
    assert digest["false_detections_by_kind"] == {"note": 5, "surface": 3,
                                                  "dimension": 2}
    # Same conservation identity at run level. Task 5 reports "N of 663 false
    # detections are kinds the metric removed from gold"; this is what makes 663
    # a checked denominator rather than a quoted one.
    assert sum(digest["pred_kinds"].values()) == digest["n_pred"]
    assert (sum(digest["false_detections_by_kind"].values())
            == digest["taxonomy"]["false_detection"])
