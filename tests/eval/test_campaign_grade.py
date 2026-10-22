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


# --- gold cross-check (design §Gold cross-check) -----------------------------
# Synthetic gold only: the cross-check is a second lens on the client's grade,
# so every test asserts the grades dict is read, never written.
from app.eval.campaign_grade import gold_cross_check
from app.eval.models import GoldCharacteristic, GoldDoc

SCALE = 300 / 72.0     # px per pt
PAGE = (0, 0, 842, 595)


def _gold(chars, page_rect=PAGE):
    return GoldDoc(doc_id="D1", pdf="x", excel="y", page_rect=page_rect,
                   characteristics=chars)


def _prow(id, box_pt, nominal="10", **kw):
    return {"id": id, "char_type": "Distance", "nominal": nominal, "upper_tol": "",
            "lower_tol": "", "target_region": [v * SCALE for v in box_pt], "pos": 1,
            "needs_review": False, "suggested": False, **kw}


def _g(balloon, pos, nominal, **kw):
    return GoldCharacteristic(balloon=balloon, position_pt=pos, char_type="Distance",
                              nominal=nominal, **kw)


def _check(proposal, final, grades, gold, **kw):
    before = dict(grades)
    out = gold_cross_check(proposal, final, grades, gold,
                           render_scale=SCALE, page_rect=kw.pop("page_rect", PAGE), **kw)
    assert grades == before          # gold never flips (or touches) a grade
    return out


def test_cross_check_classifies_without_flipping_grades():
    gold = _gold([_g(1, (30, 20), "10"), _g(2, (230, 220), "7"), _g(3, (500, 400), "3")])
    proposal = [_prow("ok", (20, 10, 40, 30)),                    # matches g1, correct
                _prow("bad", (220, 210, 240, 230), nominal="9"),  # matches g2, wrong
                _prow("ph", (700, 500, 720, 520))]                # no gold near: phantom
    final = proposal[:2]                                          # client deleted "ph"
    grades = {"ok": "confirmed", "bad": "confirmed", "ph": "phantom"}
    out = _check(proposal, final, grades, gold)
    assert out["computed"] is True
    assert out["verdicts"] == {"ok": "gold_correct", "bad": "gold_wrong", "ph": "gold_phantom"}
    assert out["matrix"]["gold_wrong&confirmed"] == 1          # client missed an error gold knows
    assert out["matrix"]["gold_phantom&phantom"] == 1
    assert out["neither_found"] == 1                           # g3: no final row near it
    assert out["matched_final"] == 2 and out["n_gold"] == 3


def test_suggestions_are_never_matched_on_either_side():
    # A tray row sits right on g1; it was never delivered, so it neither gets a
    # verdict nor counts as the client having found g1.
    gold = _gold([_g(1, (30, 20), "10")])
    sug = _prow("s", (20, 10, 40, 30), suggested=True)
    out = _check([sug], [sug], {"s": "suggestion_left"}, gold)
    assert out["verdicts"] == {} and out["matrix"] == {}
    assert out["neither_found"] == 1 and out["matched_final"] == 0


def test_frame_mismatch_refuses_to_compute():
    # Gold from the STAMPED sheet, campaign rendered from the ORIGINAL: if the
    # pages differ, every distance is measured in two frames at once (this once
    # silently cost 141 matches), so no number is better than a wrong one.
    gold = _gold([_g(1, (30, 20), "10")], page_rect=(0, 0, 1191, 842))
    out = _check([_prow("ok", (20, 10, 40, 30))], [], {"ok": "confirmed"}, gold)
    assert out == {"computed": False, "reason": "frame_mismatch"}
    # sub-point rounding between the two sources is not a mismatch
    gold2 = _gold([_g(1, (30, 20), "10")], page_rect=(0, 0, 842.4, 595.2))
    assert _check([], [], {}, gold2)["computed"] is True
    # a degenerate or non-finite campaign rect cannot be trusted either
    for bad in [(0, 0, float("nan"), 595), (842, 595, 0, 0)]:
        assert _check([], [], {}, _gold([]), page_rect=bad)["reason"] == "frame_mismatch"


def test_rows_without_a_usable_region_are_counted_unlocatable():
    gold = _gold([_g(1, (30, 20), "10")])
    proposal = [_prow("none", (0, 0, 0, 0), target_region=None),
                _prow("short", (0, 0, 0, 0), target_region=[1, 2]),
                _prow("nan", (0, 0, 0, 0), target_region=[1, 2, float("inf"), 4]),
                _prow("ok", (20, 10, 40, 30))]
    grades = {"none": "confirmed", "short": "phantom", "nan": "edited", "ok": "confirmed"}
    out = _check(proposal, proposal, grades, gold)
    assert out["unlocatable"] == 3
    assert out["verdicts"]["none"] == "gold_unlocatable"
    assert out["matrix"]["gold_unlocatable&confirmed"] == 1
    assert out["matrix"]["gold_unlocatable&phantom"] == 1
    assert out["matrix"]["gold_correct&confirmed"] == 1
    # the matrix partitions the non-suggestion proposal rows
    assert sum(out["matrix"].values()) == 4


def test_invalid_row_is_counted_never_raised_or_quoted():
    gold = _gold([_g(1, (30, 20), "10")])
    secret = "CLIENT-VALUE-42"
    # extras the UI carries (reviewed/source/...) are dropped, not fatal
    ok = _prow("ok", (20, 10, 40, 30), reviewed=True, source="auto", anchor_note="x")
    bad = _prow("bad", (20, 10, 40, 30), pos=secret)      # pos must be an int
    out = _check([ok, bad], [ok], {"ok": "confirmed", "bad": "edited"}, gold)
    assert out["verdicts"] == {"ok": "gold_correct", "bad": "row_invalid"}
    assert out["row_invalid"] == 1
    assert out["matrix"]["row_invalid&edited"] == 1
    assert secret not in repr(out)


def test_unlocated_gold_pairs_on_value_alone():
    # match_candidates falls back to value similarity for a gold row whose
    # balloon was never located (center None), whatever the distance: the
    # value is still printed on the drawing, so a correct read anywhere pairs.
    # A value below value_sim_min does not pair.
    gold = _gold([_g(1, None, "12.5"), _g(2, None, "40")])
    proposal = [_prow("far", (700, 500, 720, 520), nominal="12.5"),
                _prow("other", (100, 100, 120, 120), nominal="3")]
    out = _check(proposal, proposal[:1], {"far": "confirmed", "other": "phantom"}, gold)
    assert out["verdicts"] == {"far": "gold_correct", "other": "gold_phantom"}
    assert out["neither_found"] == 1 and out["matched_final"] == 1   # g2 found by nobody


def test_only_scored_kinds_count_and_output_carries_no_value_keys():
    gold = _gold([_g(1, (30, 20), "10"), _g(2, (300, 300), "5", kind="note")])
    p = _prow("ok", (20, 10, 40, 30))
    out = _check([p], [p], {"ok": "confirmed"}, gold)
    assert out["n_gold"] == 1 and out["neither_found"] == 0
    blob = repr(out)
    for k in ("upper_tol", "lower_tol", "characteristics", "position_pt"):
        assert k not in blob
