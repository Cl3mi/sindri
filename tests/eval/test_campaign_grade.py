"""Campaign metrics (C2 design §Metrics)."""
import math

from app.eval.campaign_grade import (IDLE_MS, group_metrics, px_to_mm,
                                     reweight, review_time_ms)


def test_px_to_mm_uses_render_scale():
    # 300 dpi: scale 300/72 px per pt; 300 px = 72 pt = 25.4 mm
    assert math.isclose(px_to_mm(300.0, 300 / 72.0), 25.4)


def test_review_time_sums_gaps_caps_idle_and_splits_sessions():
    ev = [{"t_ms": 0}, {"t_ms": 1000}, {"t_ms": 1000 + IDLE_MS + 1},  # idle gap ignored
          {"t_ms": 50}, {"t_ms": 2050}]                                 # reload: new session
    assert review_time_ms(ev) == 1000 + 2000


def test_review_time_skips_events_without_a_usable_t_ms():
    ev = [{"t_ms": 0}, {}, {"t_ms": "x"}, {"t_ms": None}, {"t_ms": True}, {"t_ms": 500}]
    assert review_time_ms(ev) == 500


def test_review_time_idle_gap_exactly_at_the_cap_counts_and_empty_is_zero():
    assert review_time_ms([{"t_ms": 0}, {"t_ms": IDLE_MS}]) == IDLE_MS
    assert review_time_ms([]) == 0


def test_review_time_reload_gap_is_never_counted():
    # t_ms restarts at the reload: 900 -> 10 must not add a negative or a
    # bridging gap, and 10 -> 20 is counted from the new session's start.
    assert review_time_ms([{"t_ms": 0}, {"t_ms": 900}, {"t_ms": 10}, {"t_ms": 20}]) == 910


def graded(grade, flagged=False, bubble_px=None, anchor_iou=None, moved=False, fields=(),
           reviewed=False):
    return {"grade": grade, "flagged": flagged, "fields": list(fields), "reason": None,
            "reviewed": reviewed,
            "bubble_px": bubble_px, "anchor_iou": anchor_iou, "anchor_moved": moved}


def test_group_metrics_counts_precision_recall_and_placement():
    rows = [graded("confirmed", bubble_px=0.0), graded("confirmed", flagged=True, bubble_px=60.0),
            graded("edited", fields=["nominal"], bubble_px=0.0),
            graded("phantom", flagged=True),
            graded("confirmed", anchor_iou=0.5, moved=True, bubble_px=0.0),
            graded("suggestion_left"), graded("suggestion_deleted")]
    m = group_metrics([{"rows": rows, "missed": [{"id": "m"}], "scale": 300 / 72.0,
                        "time_ms": 60000}])
    assert m["counts"]["confirmed"] == 3 and m["counts"]["missed"] == 1
    assert math.isclose(m["precision"], 3 / 5)
    assert math.isclose(m["unflagged_precision"], 2 / 3)
    assert math.isclose(m["flag_precision"], 1 / 2)         # 1 of 2 flagged was wrong
    assert math.isclose(m["recall_shipped_vs_client_known"], 4 / 5)  # 4 surviving, 1 missed
    assert "recall_lower_bound" not in m
    assert m["placement"]["bubble_moved"] == 1
    assert m["placement"]["anchor_nudged"] == 1 and m["placement"]["anchor_repointed"] == 0
    assert m["review_minutes_lower_bound_median"] == 1.0
    assert m["n_timed"] == 1 and m["time_not_measured"] == 0


def test_placement_counts_only_rows_with_a_bubble_measurement():
    # A deleted row / unconfirmed suggestion has bubble_px None: it is not
    # evidence that the bubble was left in place.
    rows = [graded("confirmed", bubble_px=0.0), graded("phantom", bubble_px=None),
            graded("suggestion_left", bubble_px=None)]
    m = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])
    assert m["placement"]["placed"] == 1


def test_anchor_unmeasurable_is_counted_not_treated_as_unmoved():
    rows = [graded("confirmed", anchor_iou=None, moved=False),      # unmeasurable
            graded("confirmed", anchor_iou=0.9, moved=False),       # measured, unmoved
            graded("confirmed", anchor_iou=0.1, moved=True),        # repointed
            graded("edited", anchor_iou=0.8, moved=True)]           # nudged
    m = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])
    p = m["placement"]
    assert p["anchor_unmeasurable"] == 1
    assert p["anchor_nudged"] == 1 and p["anchor_repointed"] == 1


def test_anchor_moved_none_iou_is_never_nudged_or_repointed():
    rows = [graded("confirmed", anchor_iou=None, moved=True)]
    m = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])
    p = m["placement"]
    assert p["anchor_unmeasurable"] == 1
    assert p["anchor_nudged"] == 0 and p["anchor_repointed"] == 0


def test_recall_with_tray_counts_confirmed_and_edited_suggestions_as_found():
    rows = [graded("confirmed"), graded("edited"),
            graded("suggestion_confirmed"), graded("suggestion_edited"),
            graded("suggestion_deleted"), graded("suggestion_left")]
    m = group_metrics([{"rows": rows, "missed": [{"id": "a"}, {"id": "b"}],
                        "scale": 1.0, "time_ms": 0}])
    # one shared denominator: surviving 2 + tray found 2 + missed 2
    assert math.isclose(m["recall_shipped_vs_client_known"], 2 / 6)
    assert math.isclose(m["recall_with_tray_vs_client_known"], 4 / 6)


def test_flag_precision_ignores_suggestions():
    rows = [graded("confirmed", flagged=True), graded("suggestion_confirmed", flagged=True),
            graded("suggestion_deleted", flagged=True)]
    m = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])
    assert m["flag_precision"] == 0.0 and m["counts"]["suggestion_confirmed"] == 1


def test_zero_denominators_are_none_never_zero():
    m = group_metrics([{"rows": [], "missed": [], "scale": 1.0, "time_ms": 0}])
    for k in ("precision", "unflagged_precision", "flag_precision",
              "recall_shipped_vs_client_known", "recall_with_tray_vs_client_known",
              "review_minutes_lower_bound_median"):
        assert m[k] is None, k
    assert m["placement"]["bubble_moved_share"] is None
    assert m["placement"]["moved_bubble_mm_median"] is None
    assert group_metrics([])["precision"] is None


def test_reweight_to_the_pool_mix_and_refuse_empty_groups():
    assert math.isclose(reweight({"great": 0.9, "problem": 0.5},
                                 {"great": 30, "problem": 10}), 0.8)
    assert reweight({"great": 0.9, "problem": None}, {"great": 30, "problem": 10}) is None


def test_reweight_refuses_a_missing_or_zero_pool():
    assert reweight({"great": 0.9, "problem": 0.5}, {"great": 30}) is None
    assert reweight({"great": 0.9, "problem": 0.5}, {"great": 30, "problem": 0}) is None
    assert reweight({}, {}) is None


def test_anchor_figures_exclude_rows_that_have_no_final_anchor():
    # Phantoms (deleted) and unconfirmed suggestions have no final anchor by
    # construction; counting them would bury the real unmeasurable rows.
    rows = ([graded("phantom") for _ in range(30)] + [graded("suggestion_left")]
            + [graded("confirmed", anchor_iou=0.9), graded("edited", anchor_iou=0.1, moved=True),
               graded("confirmed", anchor_iou=None),
               graded("suggestion_confirmed", anchor_iou=0.5, moved=True)])
    p = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])["placement"]
    assert p["anchor_unmeasurable"] == 1 and p["anchor_measured"] == 3
    assert p["anchor_nudged"] == 1 and p["anchor_repointed"] == 1


def test_silent_confirmations_are_split_from_explicit_ones():
    rows = [graded("confirmed", reviewed=True), graded("confirmed", reviewed=False),
            graded("confirmed", flagged=True, reviewed=True)]
    c = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])["counts"]
    assert c["confirmed"] == 3 and c["confirmed_explicit"] == 2 and c["confirmed_silent"] == 1
    assert c["unflagged_confirmed_explicit"] == 1


def test_time_not_measured_counts_drawings_without_time():
    d = {"rows": [], "missed": [], "scale": 1.0}
    m = group_metrics([dict(d, time_ms=120000), dict(d, time_ms=0), dict(d, time_ms=60000)])
    assert m["n_timed"] == 2 and m["time_not_measured"] == 1
    assert m["review_minutes_lower_bound_median"] == 1.5


def test_reweight_refuses_missing_and_surprise_groups():
    assert reweight({"great": 0.9}, {"great": 30, "problem": 10}) is None
    assert reweight({"great": 0.9, "problem": 0.5, "x": 0.1},
                    {"great": 30, "problem": 10}) is None
    # a zero-weight pool group is not required
    assert math.isclose(reweight({"great": 0.9}, {"great": 30, "problem": 0}), 0.9)
