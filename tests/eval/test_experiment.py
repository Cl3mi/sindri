from app.eval.experiment import arm_row, verdict


def _digest(cost, recall, missed, contended, isolated, correct, flagged_correct,
            escaped, n_gold=477, n_pred=830, false_det=522, misplaced=80):
    return {
        "run": "exp-x-dev", "n_gold": n_gold, "n_pred": n_pred,
        "mean_review_cost": cost, "micro_recall": recall,
        "micro_precision": 0.37, "escaped_rate": escaped / n_gold,
        "taxonomy": {"missed": missed, "correct": correct,
                     "flagged_correct": flagged_correct, "escaped_error": escaped,
                     "flagged_error": n_gold - missed - correct - flagged_correct
                                      - escaped,
                     "false_detection": false_det},
        "misplaced_matches": misplaced,
        "missed_diagnosis": {"contended": contended, "isolated": isolated,
                             "unlocated": missed - contended - isolated},
        "config": {"extra": {"merge_max_lines": 2}},
    }


CONTROL = _digest(174.3, 0.6457, 169, 82, 74, 72, 40, 129)


def test_arm_row_derives_field_accuracy_on_matched_rows():
    """The number that catches recall bought by breaking correct pairs."""
    row = arm_row("control", CONTROL)
    assert row["matched"] == 477 - 169
    assert row["field_acc"] == round((72 + 40) / (477 - 169), 4)


def test_a_real_improvement_draws_no_objection_from_the_taxonomy_conditions():
    """Cost down, recall up, matched rows no less accurate: a genuine gain, and
    none of the taxonomy conditions should complain about it.

    Robustness is checked separately and is NOT satisfied here, because no
    vs-control comparison is supplied -- see
    test_a_robust_improvement_still_wins for the whole rule passing at once.
    This test exists to keep the taxonomy half from becoming unsatisfiable."""
    better = _digest(150.0, 0.70, 140, 55, 74, 95, 50, 130)
    why = verdict(arm_row("nomerge", better), arm_row("control", CONTROL))["why"]
    for taxonomy_objection in ("review cost", "field accuracy", "escaped-error",
                               "recall fell"):
        assert taxonomy_objection not in why, why
    assert "robustness unmeasured" in why


def test_recall_bought_by_breaking_correct_pairs_is_not_a_win():
    """The max_cardinality result, encoded so it cannot be repeated: 26 misses
    recovered, 27 correct pairings destroyed, field accuracy 36.4% -> 25.4%.
    Review cost fell, so a cost-only rule would have called it an improvement."""
    inflated = _digest(168.6, 0.7002, 143, 58, 74, 52, 33, 167,
                       false_det=496, misplaced=126)
    v = verdict(arm_row("maxcard", inflated), arm_row("control", CONTROL))
    assert v["win"] is False
    assert "field accuracy" in v["why"]


def test_a_rise_in_silent_errors_is_not_a_win_either():
    """handoff §6's regression guard: cost down but escaped errors up is a net
    review-time LOSS, because a silent wrong value reaches the customer."""
    leaky = _digest(170.0, 0.66, 160, 78, 74, 72, 40, 175)
    v = verdict(arm_row("leaky", leaky), arm_row("control", CONTROL))
    assert v["win"] is False
    assert "escaped" in v["why"]


def test_no_cost_improvement_is_not_a_win():
    worse = _digest(180.0, 0.60, 190, 90, 82, 70, 38, 129)
    v = verdict(arm_row("worse", worse), arm_row("control", CONTROL))
    assert v["win"] is False
    assert "review cost" in v["why"]


def test_verdict_names_the_direction_the_arm_confirms():
    """Direction-finding is the point: which miss bucket actually moved."""
    merge_win = _digest(150.0, 0.70, 140, 50, 74, 95, 50, 130)
    v = verdict(arm_row("nomerge", merge_win), arm_row("control", CONTROL))
    assert v["contended_delta"] == 50 - 82
    assert v["isolated_delta"] == 0


# The real detectbox numbers, 2026-08-27. Cost fell, field accuracy ROSE, and
# escaped_rate rose only +0.0147 -- inside experiment.py's 0.02 tolerance -- so
# the three original conditions all passed and the tool printed WIN plus
# "confirm it on the full corpus". Four things said otherwise, and two of them
# are checkable here.
_DETECTBOX = _digest(174.05, 0.631, 176, 87, 70, 72, 46, 136,
                     false_det=474, misplaced=80)
_DETECTBOX_CMP_NOT_ROBUST = {
    "mean_delta": -0.25, "ci95": [-6.35, 6.0], "significant": False,
    "weight_sensitivity": {"n_weight_vectors": 6, "b_better_fraction": 4 / 6,
                           "robust": False,
                           "mean_delta_per_weighting": [-0.25, 4.3, -2.35,
                                                        -5.75, 2.5, -1.35]},
}


def test_a_recall_drop_is_not_a_win_even_when_cost_and_field_accuracy_improve():
    """detectbox converted 7 wrong rows into MISSES at w=10 rather than into
    correct ones, which lifts field accuracy by shrinking its denominator.
    compare_runs already warns on a recall drop at 0.005 while cost improves;
    experiment.py tolerated it silently, so the two tools disagreed about the
    same run and the looser one printed WIN."""
    v = verdict(arm_row("detectbox", _DETECTBOX), arm_row("control", CONTROL))
    assert v["win"] is False
    assert "recall" in v["why"]


def test_an_arm_that_is_not_robust_across_weightings_is_not_a_win():
    """A -0.25 mean on ci95 [-6.35, 6.00], better under 4 of 6 weightings, is
    indistinguishable from tightmerge's +0.35 -- which findings §4 records as a
    no-op, not a small gain. Adopting it would be reading noise as direction."""
    v = verdict(arm_row("detectbox", _DETECTBOX), arm_row("control", CONTROL),
                comparison=_DETECTBOX_CMP_NOT_ROBUST)
    assert v["win"] is False
    assert "robust" in v["why"] or "weighting" in v["why"]


def test_robustness_that_was_never_measured_does_not_count_as_passing():
    """House rule: an unmeasured field must not read as a pass. Without the
    vs-control comparison there is no evidence the arm beats control under any
    weighting but the default one."""
    good = _digest(150.0, 0.70, 140, 55, 74, 95, 50, 130)
    v = verdict(arm_row("x", good), arm_row("control", CONTROL), comparison=None)
    assert v["win"] is False
    assert "unmeasured" in v["why"] or "no comparison" in v["why"]


def test_a_robust_improvement_still_wins():
    """The guard must not make every arm unwinnable: cost down under all six
    weightings, recall up, accuracy up."""
    good = _digest(150.0, 0.70, 140, 55, 74, 95, 50, 130)
    cmp_robust = {
        "mean_delta": -24.3, "ci95": [-30.0, -18.0], "significant": True,
        "weight_sensitivity": {"n_weight_vectors": 6, "b_better_fraction": 1.0,
                               "robust": True,
                               "mean_delta_per_weighting": [-24.3] * 6},
    }
    v = verdict(arm_row("x", good), arm_row("control", CONTROL),
                comparison=cmp_robust)
    assert v["win"] is True, v["why"]


def test_the_spans_zero_clause_is_only_used_when_the_interval_spans_zero():
    """nomerge's ci95 is [1.15, 9.70] -- significantly WORSE, not a no-op. The
    first version of this message appended "an interval spanning zero is a
    no-op" to every non-robust arm, which was simply untrue for that one. An
    inaccurate diagnostic is what sent this campaign after the wrong lever in
    the first place."""
    worse = _digest(179.9, 0.6625, 161, 74, 74, 68, 43, 139)
    definitely_worse = {
        "mean_delta": 5.6, "ci95": [1.15, 9.7], "significant": True,
        "weight_sensitivity": {"n_weight_vectors": 6, "b_better_fraction": 0.0,
                               "robust": True},
    }
    why = verdict(arm_row("nomerge", worse), arm_row("control", CONTROL),
                  comparison=definitely_worse)["why"]
    assert "spanning zero" not in why
    assert "0 of 6 weightings" in why

    spans_zero = {
        "mean_delta": -0.25, "ci95": [-6.35, 6.0], "significant": False,
        "weight_sensitivity": {"n_weight_vectors": 6, "b_better_fraction": 4 / 6,
                               "robust": False},
    }
    why2 = verdict(arm_row("detectbox", _DETECTBOX), arm_row("control", CONTROL),
                   comparison=spans_zero)["why"]
    assert "spanning zero" in why2
