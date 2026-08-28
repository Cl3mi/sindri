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


def test_comparability_errors_never_name_a_real_document():
    """The gold-differs guard interpolated the raw doc_id, so `runner compare`
    printed a client part number to the terminal -- the one thing every other
    line in this module routes through an Anonymizer first. Found by running a
    legitimate compare across two golds: the message read "gold differs for
    <a real part number from the live corpus>". The id is genuinely useful for
    triage, so hash it rather than drop it.

    The real id is deliberately NOT quoted here. Writing it into a test docstring
    would commit to the repository exactly what this test exists to keep out of
    it -- which is what the first version of this docstring did."""
    a = _run("a", [10.0, 12.0])
    b = _run("b", [10.0, 12.0])
    for d in (a.doc_scores[0], b.doc_scores[0]):
        d.doc_id = "T1025300_B"
    b.doc_scores[0].gold_hash = "f" * 16

    with pytest.raises(ValueError) as err:
        compare_runs(a, b)
    assert "T1025300_B" not in str(err.value), "comparability error leaked a part number"
    assert "gold differs" in str(err.value)

    # and with an anonymizer supplied it still identifies WHICH document
    from app.eval.anon import Anonymizer
    anon = Anonymizer("salt")
    with pytest.raises(ValueError) as err2:
        compare_runs(a, b, anonymizer=anon)
    assert anon("T1025300_B") in str(err2.value)
    assert "T1025300_B" not in str(err2.value)


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
    # The whole entry, not just a value fragment: nothing may forward the text
    # score._compare_fields wrote.
    assert "nominal: " not in blob
    assert "!=" not in blob
    # The bare field NAME is allowed, and _field_failure_counts deliberately
    # reports it: it comes from a closed vocabulary (_FIELD_NAMES) written by
    # this repo, not from client data, and without it field_acc's four-way
    # conjunction cannot be aimed at a prompt. Anything outside that vocabulary
    # is bucketed as "other" -- see
    # test_unrecognised_field_name_is_bucketed_rather_than_passed_through.
    assert json.loads(blob)["field_failures"] == {"field:nominal": 1}


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
    # Per-document undetected-miss count: sizes a per-sheet fix (raise the
    # budget vs true tiling) instead of averaging the four sheets together.
    assert "missed_isolated" in digest["clamped_docs"][0]
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


def test_summary_crosstabs_which_kinds_matched_in_scope_gold():
    """Settles the false_detection question without changing the metric: a
    non-"dimension" kind under matched_by_pred_kind is a prediction that
    filtering to score_kinds would demote from a match to a miss."""
    def doc(doc_id, matched, false):
        pred = {k: matched.get(k, 0) + false.get(k, 0)
                for k in set(matched) | set(false)}
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15,
                        n_gold=sum(matched.values()) + 2,
                        n_pred=sum(pred.values()),
                        counts={"correct": sum(matched.values()), "missed": 2,
                                "false_detection": sum(false.values())},
                        review_cost=10.0, recall=1.0, precision=1.0,
                        escaped_rate=0.0, pred_kinds=pred,
                        false_kinds=false, matched_kinds=matched)

    docs = [doc("D1", {"dimension": 12, "gdt": 3}, {"theoretical": 7}),
            doc("D2", {"dimension": 9, "surface": 2}, {"theoretical": 4,
                                                       "gdt": 1})]
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), docs)

    digest = summarize(report, lambda d: "hashed")

    assert digest["matched_by_pred_kind"] == {"dimension": 21, "gdt": 3,
                                             "surface": 2}
    # Per-kind conservation against two aggregates that already exist. This is
    # the identity that makes "filtering would cost N matches" a checked claim.
    for k, total in digest["pred_kinds"].items():
        assert total == (digest["matched_by_pred_kind"].get(k, 0)
                         + digest["false_detections_by_kind"].get(k, 0))
    assert (sum(digest["matched_by_pred_kind"].values())
            == digest["n_gold"] - digest["taxonomy"]["missed"])


def test_summary_aggregates_why_the_misses_happened():
    """missed=310 is 63% of the review cost and the single biggest Rung 1 target,
    but the three causes route to different work: contended -> merge/dedupe,
    isolated -> tile size and overlap, unlocated -> balloon recovery, which is
    not a detection problem at all."""
    def doc(doc_id, contended, isolated, unlocated):
        total = contended + isolated + unlocated
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15,
                        n_gold=total + 5, n_pred=5,
                        counts={"correct": 5, "missed": total},
                        review_cost=10.0 * total, recall=0.5, precision=1.0,
                        escaped_rate=0.0, missed_contended=contended,
                        missed_isolated=isolated, missed_unlocated=unlocated)

    docs = [doc("D1", 3, 7, 1), doc("D2", 2, 4, 0)]
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), docs)

    digest = summarize(report, lambda d: "hashed")
    diag = digest["missed_diagnosis"]

    assert diag == {"contended": 5, "isolated": 11, "unlocated": 1}
    # The three buckets partition the misses, so no cause is double-counted and
    # none is silently dropped.
    assert sum(diag.values()) == digest["taxonomy"]["missed"]


def test_summary_names_documents_whose_gold_and_dump_frames_disagree():
    """An unguarded seam: predictions are placed with dump.page_rect, the match
    gate with gold.page_rect, and nothing compares them. A document scoring
    recall 0.0 while still emitting predictions is the signature, so the digest
    has to be able to say whether the frames are the cause."""
    def doc(doc_id, origin, extent, n_gold, missed):
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=n_gold,
                        n_pred=10, counts={"missed": missed}, review_cost=100.0,
                        recall=round((n_gold - missed) / n_gold, 4), precision=0.0,
                        escaped_rate=0.0, frame_origin_frac=origin,
                        frame_extent_frac=extent)

    docs = [
        doc("D1", 0.0, 0.0, 10, 2),          # frames agree: 8/10
        doc("D2", 0.2742, 0.0, 10, 10),      # origin differs: 0/10
        doc("D3", 0.0, 4e-05, 10, 2),        # float noise only -> counts as AGREE
        doc("D4", 0.0, 1.5, 40, 36),         # extent differs: 4/40
        doc("D5", 0.0, 3.0, 1, 0),           # one-row doc at recall 1.0
    ]
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")
    fm = digest["frame_mismatch"]

    assert fm["n_docs_affected"] == 3        # D2, D4, D5 -- NOT the 4e-05 one
    assert fm["n_docs_frames_agree"] == 2
    assert fm["max_frac"] == 3.0
    # Micro, so the single-row D5 cannot outvote the 40-row D4:
    # agree 16/20 = 0.8 ; differ (0 + 4 + 1)/(10 + 40 + 1) = 5/51
    assert fm["micro_recall_frames_agree"] == 0.8
    assert fm["micro_recall_frames_differ"] == round(5 / 51, 4)
    assert fm["docs"][0] == {"doc": "hash-D5", "origin": 0.0, "extent": 3.0,
                             "recall": 1.0}


def test_summary_reports_no_frame_mismatch_when_every_frame_agrees():
    docs = [DocScore(doc_id="D1", gold_hash="g" + "0" * 15, n_gold=5, n_pred=5,
                     counts={"correct": 5}, review_cost=0.0, recall=1.0,
                     precision=1.0, escaped_rate=0.0,
                     frame_origin_frac=0.0, frame_extent_frac=0.0)]
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), docs)

    fm = summarize(report, lambda d: "hashed")["frame_mismatch"]

    assert fm == {"n_docs_affected": 0, "n_docs_frames_agree": 1,
                  "n_docs_not_measured": 0, "max_frac": 0.0,
                  "micro_recall_frames_agree": 1.0,
                  "micro_recall_frames_differ": None, "docs": []}


def test_summary_never_calls_an_unmeasured_frame_an_agreeing_one():
    """A DocScore written before these fields existed carries None, not 0.0.
    Reporting it as "agrees" gave this author a clean bill of health for a run
    where 14 of 20 documents disagreed -- read off a report that had simply been
    summarised without being re-scored. n_docs_not_measured is the tell, exactly
    as unknown_dpi is for the clamp split."""
    stale = DocScore(doc_id="D1", gold_hash="g" + "0" * 15, n_gold=5, n_pred=5,
                     counts={"correct": 5}, review_cost=0.0, recall=1.0,
                     precision=1.0, escaped_rate=0.0)       # fields absent
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [stale])

    fm = summarize(report, lambda d: "hashed")["frame_mismatch"]

    assert fm["n_docs_not_measured"] == 1
    assert fm["n_docs_frames_agree"] == 0        # NOT 1 -- the whole point
    assert fm["n_docs_affected"] == 0
    assert fm["micro_recall_frames_agree"] is None


def test_summary_says_whether_undetected_misses_sit_on_the_clamped_sheets():
    """Decides WHICH coverage fix: if the isolated misses concentrate on the
    documents whose render dpi was clamped, the answer is tiled rendering for
    oversized sheets. If they are spread evenly, the detector is under-covering
    at full resolution and the answer is tile size / overlap."""
    def doc(doc_id, dpi, isolated):
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=20,
                        n_pred=10, counts={"correct": 10, "missed": isolated},
                        review_cost=10.0 * isolated, recall=0.5, precision=1.0,
                        escaped_rate=0.0, effective_dpi=dpi,
                        missed_isolated=isolated)

    docs = [doc("D1", 300.0, 2), doc("D2", 300.0, 3),
            doc("D3", 109.0, 40), doc("D4", 225.0, 30)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    split = summarize(report, lambda d: f"hash-{d}")["clamped_vs_unclamped"]

    assert split["clamped"]["missed_isolated"] == 70
    assert split["unclamped"]["missed_isolated"] == 5
    # Still reconciles: the buckets partition the corpus, so their isolated
    # counts must add up to the run total.
    assert (split["clamped"]["missed_isolated"]
            + split["unclamped"]["missed_isolated"]
            + split["unknown_dpi"]["missed_isolated"]
            == summarize(report, lambda d: "h")["missed_diagnosis"]["isolated"])


def _wrong_row_report(field_errors, taxonomy="escaped_error", notes=None):
    """One matched-but-wrong pair carrying real-looking client values, so every
    aggregate over it can be checked for leakage as well as for arithmetic."""
    pair = MatchedPair(gold_balloon=1, pred_pos=1, distance_frac=0.001,
                       fields_correct=False, field_errors=field_errors,
                       flagged=taxonomy.startswith("flagged"),
                       taxonomy=taxonomy, notes=notes or [])
    d = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=1, n_pred=1,
                 pairs=[pair], counts={taxonomy: 1}, review_cost=5.0,
                 recall=1.0, precision=1.0, escaped_rate=1.0)
    return aggregate("diag", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d])


def test_field_failures_name_the_field_and_never_the_value():
    digest = summarize(_wrong_row_report(["nominal: '6,5'!='5,5'"]),
                       lambda d: "hashed")
    assert digest["field_failures"] == {"field:nominal": 1}
    blob = json.dumps(digest, ensure_ascii=False)
    for leak in ("6,5", "5,5"):
        assert leak not in blob, f"field-failure aggregate leaked {leak!r}"


def test_field_failure_signature_records_the_combination_not_just_the_count():
    """'both tolerances wrong' and 'nominal wrong' are different fixes, and a
    per-field histogram alone cannot tell them apart."""
    digest = summarize(_wrong_row_report(
        ["upper_tol: '0,1'!='0,2'", "lower_tol: ''!='-0,2'"]),
        lambda d: "hashed")
    assert digest["field_failure_signatures"] == {
        "fields:upper_tol+lower_tol": 1}
    assert digest["field_failures"] == {"field:upper_tol": 1,
                                       "field:lower_tol": 1}


def test_field_failure_signatures_reconcile_with_the_error_taxonomy():
    """House rule: every aggregate must cross-check against a count that already
    exists. Each wrong row contributes exactly one signature, so the signatures
    must sum to escaped_error + flagged_error."""
    digest = summarize(_wrong_row_report(["nominal: '1'!='2'"],
                                         taxonomy="flagged_error"),
                       lambda d: "hashed")
    t = digest["taxonomy"]
    assert sum(digest["field_failure_signatures"].values()) == (
        t.get("escaped_error", 0) + t.get("flagged_error", 0))


def test_unrecognised_field_name_is_bucketed_rather_than_passed_through():
    """If score._compare_fields ever changes format, the digest must degrade to
    'other' rather than forward an unvetted string into a values-blind file."""
    digest = summarize(_wrong_row_report(["SOMETHING_NEW: 'a'!='b'"]),
                       lambda d: "hashed")
    assert digest["field_failures"] == {"field:other": 1}
    assert "SOMETHING_NEW" not in json.dumps(digest)


def _crosstab_report(rows):
    """rows: (cause, misplaced, taxonomy) triples, one matched-but-wrong pair
    each, all on one document."""
    pairs = []
    for i, (cause, misplaced, taxonomy) in enumerate(rows, start=1):
        notes = [f"cause:{cause}"] + (["misplaced"] if misplaced else [])
        pairs.append(MatchedPair(
            gold_balloon=i, pred_pos=i, distance_frac=0.09 if misplaced else 0.001,
            fields_correct=False, field_errors=["nominal: '1'!='2'"],
            flagged=taxonomy.startswith("flagged"), taxonomy=taxonomy,
            notes=notes))
    counts = {}
    for _, _, taxonomy in rows:
        counts[taxonomy] = counts.get(taxonomy, 0) + 1
    d = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=len(rows),
                 n_pred=len(rows), pairs=pairs, counts=counts,
                 review_cost=float(len(rows)), recall=1.0, precision=1.0,
                 escaped_rate=0.0)
    return aggregate("diag", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d])


def test_crosstab_splits_misread_by_whether_the_pair_was_misplaced():
    """A misplaced pair may have read a DIFFERENT callout correctly. No prompt
    edit can move those, so they must be separable before an arm is costed."""
    digest = summarize(_crosstab_report([
        ("misread", True, "escaped_error"),
        ("misread", False, "escaped_error"),
        ("misparse", False, "flagged_error"),
    ]), lambda d: "hashed")
    ct = digest["error_cause_crosstab"]
    assert ct["misread"]["misplaced"] == 1
    assert ct["misread"]["on_target"] == 1
    assert ct["misparse"]["misplaced"] == 0


def test_crosstab_rows_reconcile_on_both_axes():
    digest = summarize(_crosstab_report([
        ("misread", True, "escaped_error"),
        ("misread", False, "flagged_error"),
    ]), lambda d: "hashed")
    row = digest["error_cause_crosstab"]["misread"]
    assert row["misplaced"] + row["on_target"] == row["total"]
    assert row["escaped"] + row["flagged"] == row["total"]


def test_crosstab_totals_match_the_existing_error_causes_histogram():
    digest = summarize(_crosstab_report([
        ("misread", True, "escaped_error"),
        ("misparse", False, "escaped_error"),
    ]), lambda d: "hashed")
    ct = digest["error_cause_crosstab"]
    assert {k: v["total"] for k, v in ct.items()} == digest["error_causes"]


def test_digest_never_contains_the_tokens_the_commit_hook_screens_for():
    """.git/hooks/pre-commit blocks any staged .json carrying "field_errors",
    "raw_text", "characteristics", "position_pt", "upper_tol" or "lower_tol" as
    a quoted token, because that is the shape of a gold record or a prediction
    dump — it cannot tell a COUNT keyed by a field name from a VALUE stored
    under one.

    Digests ARE committed, so an aggregate that keys on a bare field name makes
    every future digest commit need the SINDRI_ALLOW_DATA_COMMIT bypass. That
    trades a permanent hole in a data guard for six characters of key naming,
    which is why both field_failures and field_failure_signatures namespace
    their keys. This test fails the moment an aggregate reintroduces one."""
    for field_errors in (
            # every field wrong: the multi-field signature key
            ["upper_tol: '0,1'!='0,2'", "lower_tol: ''!='-0,2'",
             "nominal: '6,5'!='5,5'", "char_type: 'Radius'!='Distance'"],
            # ONE field wrong: the signature key is that field alone, which
            # is the shape that emitted a bare "upper_tol" and slipped past
            # the first version of this test
            ["upper_tol: '0,1'!='0,2'"],
            ["lower_tol: ''!='-0,2'"]):
        _assert_no_blocked_tokens(summarize(
            _wrong_row_report(field_errors,
                              notes=["cause:misread", "misplaced"]),
            lambda d: "hashed"))


def _assert_no_blocked_tokens(digest):
    blob = json.dumps(digest, ensure_ascii=False)
    for token in ("field_errors", "raw_text", "characteristics", "position_pt",
                  "upper_tol", "lower_tol"):
        assert f'"{token}"' not in blob, (
            f'digest carries the quoted token "{token}", which the pre-commit '
            f"client-data guard blocks")


def test_failure_modes_are_aggregated_by_mode_and_field():
    digest = summarize(_wrong_row_report(
        ["upper_tol: ''!='0,1'", "nominal: '21'!='20'"],
        notes=["cause:misread", "missing:upper_tol", "wrong:nominal"]),
        lambda d: "hashed")
    assert digest["field_failure_modes"] == {"missing:upper_tol": 1,
                                            "wrong:nominal": 1}


def test_failure_modes_reconcile_against_the_per_field_histogram():
    """Both count one entry per wrong field per pair, so their totals must
    agree. A mismatch means one of the two is reading the report wrongly."""
    digest = summarize(_wrong_row_report(
        ["upper_tol: ''!='0,1'", "lower_tol: ''!='-0,1'"],
        notes=["cause:misread", "missing:upper_tol", "missing:lower_tol"]),
        lambda d: "hashed")
    assert (sum(digest["field_failure_modes"].values())
            == sum(digest["field_failures"].values()))
    assert digest["field_failure_modes_not_measured"] == 0


def test_a_report_written_before_the_tags_existed_says_not_measured():
    """An empty dict would read as 'no failures'. A stale report once reported
    'all 20 frames agree' for a run where 14 disagreed and cost a full analysis
    cycle; the same mistake is not available here."""
    digest = summarize(_wrong_row_report(["nominal: '21'!='20'"],
                                         notes=["cause:misread"]),
                       lambda d: "hashed")
    assert digest["field_failure_modes"] == {}
    assert digest["field_failure_modes_not_measured"] == 1


def _conf_report(rows):
    """rows: (conf_bucket, taxonomy) pairs, one matched pair each."""
    pairs, counts = [], {}
    for i, (bucket, taxonomy) in enumerate(rows, start=1):
        wrong = taxonomy.endswith("error")
        pairs.append(MatchedPair(
            gold_balloon=i, pred_pos=i, distance_frac=0.001,
            fields_correct=not wrong,
            field_errors=["nominal: '1'!='2'"] if wrong else [],
            flagged=taxonomy.startswith("flagged"), taxonomy=taxonomy,
            notes=[f"conf:{bucket}"]))
        counts[taxonomy] = counts.get(taxonomy, 0) + 1
    d = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=len(rows),
                 n_pred=len(rows), pairs=pairs, counts=counts,
                 review_cost=float(len(rows)), recall=1.0, precision=1.0,
                 escaped_rate=0.0)
    return aggregate("diag", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d])


def test_confidence_is_crossed_with_taxonomy_so_a_threshold_move_is_priceable():
    digest = summarize(_conf_report([("0.6-0.8", "escaped_error"),
                                     ("0.6-0.8", "correct"),
                                     (">=0.8", "correct")]),
                       lambda d: "hashed")
    assert digest["confidence_by_taxonomy"]["0.6-0.8"] == {"escaped_error": 1,
                                                           "correct": 1}
    assert digest["confidence_by_taxonomy"][">=0.8"] == {"correct": 1}


def test_confidence_histogram_covers_every_matched_pair():
    digest = summarize(_conf_report([("<0.2", "flagged_error"),
                                     (">=0.8", "correct")]),
                       lambda d: "hashed")
    total = sum(sum(v.values())
                for v in digest["confidence_by_taxonomy"].values())
    assert total == digest["n_gold"] - digest["taxonomy"].get("missed", 0)
    assert digest["confidence_not_measured"] == 0


def test_a_report_written_before_the_conf_tag_says_not_measured():
    report = _wrong_row_report(["nominal: '1'!='2'"], notes=["cause:misread"])
    digest = summarize(report, lambda d: "hashed")
    assert digest["confidence_by_taxonomy"] == {}
    assert digest["confidence_not_measured"] == 1


def _tol_doc(doc_id, rows, distinct, n_gold=10):
    return DocScore(doc_id=doc_id, gold_hash="g" * 16, n_gold=n_gold,
                    n_pred=n_gold, counts={"correct": n_gold},
                    review_cost=1.0, recall=1.0, precision=1.0,
                    escaped_rate=0.0, dropped_tol_rows=rows,
                    dropped_tol_distinct=distinct)


def test_dropped_tolerances_report_rows_and_distinct_per_document():
    """rows/distinct per document, never pooled: a general tolerance is one
    value repeated within ONE drawing, so pooling across drawings would turn 20
    documents' worth of separate ISO 2768 defaults into 20 'distinct' values and
    destroy the signal."""
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(),
                       [_tol_doc("T1025300_B", 30, 2),
                        _tol_doc("T1025206_D", 12, 11)])
    dt = summarize(report, lambda d: f"hash-{d}")["dropped_tolerances"]
    assert dt["rows"] == 42
    assert dt["not_measured"] == 0
    by_doc = {d["doc"]: (d["rows"], d["distinct"]) for d in dt["docs"]}
    assert by_doc["hash-T1025300_B"] == (30, 2)
    assert by_doc["hash-T1025206_D"] == (12, 11)


def test_dropped_tolerances_omit_documents_with_none_dropped():
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [_tol_doc("D1", 0, 0),
                                       _tol_doc("D2", 5, 1)])
    dt = summarize(report, lambda d: "hashed")["dropped_tolerances"]
    assert dt["rows"] == 5
    assert len(dt["docs"]) == 1


def test_dropped_tolerances_say_not_measured_for_an_older_report():
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [_doc("D1", 10.0)])
    dt = summarize(report, lambda d: "hashed")["dropped_tolerances"]
    assert dt["not_measured"] == 1
    assert dt["rows"] == 0


def test_dropped_tolerances_carry_no_tolerance_value():
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [_tol_doc("T1025300_B", 30, 2)])
    blob = json.dumps(summarize(report, lambda d: "hashed"), ensure_ascii=False)
    for token in ("upper_tol", "lower_tol", "0,1", "-0,1"):
        assert f'"{token}"' not in blob, f"leaked {token!r}"


def test_a_comparison_across_base_models_warns_loudly():
    """_check_comparable guards the doc set, gold hashes, weights, match_params
    and splits_hash -- but not RunConfig. So a run on a different base compares
    against the frozen baseline without complaint, and the swap gets credited to
    whatever the arm was actually testing. Rung 3 does exactly that twice: the
    baseline is ...-72B-Instruct-AWQ while both new runs are ...-72B-Instruct
    quantised to NF4.

    A warning, not a refusal: the cross-base comparison IS the experiment."""
    a = _run("a", [10.0, 12.0])
    b = _run("b", [9.0, 11.0])
    a.config = RunConfig(model_id="Qwen/Qwen2.5-VL-72B-Instruct-AWQ")
    b.config = RunConfig(model_id="Qwen/Qwen2.5-VL-72B-Instruct")

    cmp = compare_runs(a, b, seed=13)

    assert any("base model" in w for w in cmp["warnings"]), cmp["warnings"]
    assert any("AWQ" in w for w in cmp["warnings"])


def test_same_base_model_produces_no_model_warning():
    """lora72b vs base72bnf4 is same-model by construction, and that is what
    makes it the clean measurement of the fine-tune."""
    a = _run("a", [10.0, 12.0])
    b = _run("b", [9.0, 11.0])
    for r in (a, b):
        r.config = RunConfig(model_id="Qwen/Qwen2.5-VL-72B-Instruct")
    assert not any("base model" in w
                   for w in compare_runs(a, b, seed=13)["warnings"])
