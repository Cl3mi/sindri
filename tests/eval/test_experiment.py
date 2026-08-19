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


def test_a_real_improvement_is_called_a_win():
    """Cost down, and matched rows no less accurate: a genuine gain."""
    better = _digest(150.0, 0.70, 140, 55, 74, 95, 50, 130)
    assert verdict(arm_row("nomerge", better), arm_row("control", CONTROL))["win"]


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
