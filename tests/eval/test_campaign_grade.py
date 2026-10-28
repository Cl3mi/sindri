"""Campaign metrics (C2 design §Metrics)."""
import math

import pytest

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


def test_frame_check_compares_origin_too():
    # Same size, shifted origin: every gold position would sit 100 pt off, so
    # each delivered value would read as a gold_phantom. Refuse instead.
    gold = _gold([_g(1, (130, 120), "10")], page_rect=(100, 100, 942, 695))
    assert _check([], [], {}, gold)["reason"] == "frame_mismatch"


def test_client_manual_add_counts_as_found():
    gold = _gold([_g(1, (30, 20), "10"), _g(2, (230, 220), "7")])
    p = _prow("ok", (20, 10, 40, 30))
    added = _prow("m1", (220, 210, 240, 230), nominal="7", source="manual")
    out = _check([p], [p, added], {"ok": "confirmed"}, gold)
    assert out["matched_final"] == 2 and out["neither_found"] == 0
    assert out["verdicts"] == {"ok": "gold_correct"}      # a final-only row gets no verdict


def test_bad_render_scale_refuses():
    gold = _gold([_g(1, (30, 20), "10")])
    for s in (0, -1.0, float("nan"), True):
        out = gold_cross_check([], [], {}, gold, render_scale=s, page_rect=PAGE)
        assert out == {"computed": False, "reason": "bad_render_scale"}


def test_row_beside_only_a_note_gold_row_is_a_phantom():
    # Notes are outside score_kinds: the scorer would call this a false
    # detection, so the cross-check must too.
    gold = _gold([_g(1, (30, 20), "10", kind="note")])
    p = _prow("p", (20, 10, 40, 30))
    out = _check([p], [p], {"p": "confirmed"}, gold)
    assert out["verdicts"] == {"p": "gold_phantom"} and out["n_gold"] == 0


def test_duplicate_or_missing_row_ids_refuse():
    # Verdicts are keyed by id: two rows sharing one would merge silently.
    gold = _gold([_g(1, (30, 20), "10")])
    a = _prow("x", (20, 10, 40, 30))
    b = _prow("x", (700, 500, 720, 520))
    out = _check([a, b], [a], {"x": "confirmed"}, gold)
    assert out == {"computed": False, "reason": "duplicate_ids"}
    noid = {k: v for k, v in a.items() if k != "id"}
    assert _check([noid], [], {}, gold)["reason"] == "duplicate_ids"


def test_duplicate_gold_balloons_refuse():
    gold = _gold([_g(1, (30, 20), "10"), _g(1, (230, 220), "7")])
    assert _check([], [], {}, gold) == {"computed": False, "reason": "gold_invalid"}


def test_cross_check_reports_its_delivered_row_count():
    # The matrix must partition the delivered rows; the caller can only
    # check that against a count the cross-check states itself.
    gold = _gold([_g(1, (30, 20), "10")])
    sug = _prow("s", (20, 10, 40, 30), suggested=True)
    rows = [_prow("a", (20, 10, 40, 30)), _prow("b", (700, 500, 720, 520)), sug]
    out = _check(rows, rows, {"a": "confirmed", "b": "phantom"}, gold)
    assert out["n_delivered"] == 2 == sum(out["matrix"].values())


def test_reconciled_frames_are_unsupported():
    from app.eval.models import MatchParams
    gold = _gold([_g(1, (30, 20), "10")])
    out = _check([], [], {}, gold, params=MatchParams(reconcile_frames="scale"))
    assert out == {"computed": False, "reason": "unsupported_params"}


# --- ranked findings and the campaign grade (Task 7) -------------------------
# End to end through write_tar/read_records, so the grader is exercised on the
# exact record shapes a pull produces, never on hand-built DrawingRecords.
import json

import app.eval.campaign_grade as campaign_grade
from app.eval.campaign_grade import LEVERS, grade_campaign, ranked_findings
from app.eval.campaign_records import read_records
from tests.eval.campaign_fixtures import drawing, event, proposal_row, write_tar


def test_ranked_findings_by_kind_with_levers():
    per_drawing = [
        {"rows": [graded("edited", fields=["nominal"]), graded("edited", fields=["nominal"]),
                  {**graded("phantom"), "reason": "unballooned_real_dimension"}],
         "missed": [{}], "scale": 300 / 72.0},
        {"rows": [graded("edited", fields=["nominal", "upper_tol"]),
                  graded("confirmed", bubble_px=100.0)], "missed": [], "scale": 300 / 72.0}]
    f = ranked_findings(per_drawing)
    value = f["value"]
    assert value[0]["bucket"] == "fields:nominal" and value[0]["count"] == 2
    assert value[0]["drawings"] == 1 and math.isclose(value[0]["share"], 2 / 5)
    assert {b["bucket"] for b in value} == {"fields:nominal", "fields:nominal+upper_tol",
                                           "phantom:unballooned_real_dimension", "missed"}
    assert f["placement"][0]["bucket"] == "bubble_moved"
    assert all(b["lever"] and b["lever"] != "unmapped" for b in value + f["placement"])
    ph = next(b for b in value if b["bucket"].startswith("phantom:"))
    assert ph["lever"] == LEVERS[0][1]          # most specific entry wins


def test_tray_outcomes_split_corrections_from_drops_that_were_right():
    """A confirmed or edited suggestion is a value the client wanted back: a
    correction. A deleted suggestion means the drop was RIGHT, so it must not
    rank as a finding; it is reported as its own count."""
    rows = [graded("suggestion_confirmed"), graded("suggestion_edited", fields=["nominal"]),
            graded("suggestion_deleted"), graded("suggestion_deleted"),
            graded("suggestion_left")]
    f = ranked_findings([{"rows": rows, "missed": [], "scale": 1.0}])
    assert {b["bucket"] for b in f["value"]} == {"suggestion_confirmed", "suggestion_edited"}
    assert f["tray_drop_right"] == 2


def test_anchor_buckets_need_a_measured_move_and_ties_sort_by_name():
    rows = [graded("confirmed", anchor_iou=0.8, moved=True),     # nudged
            graded("confirmed", anchor_iou=0.1, moved=True),     # repointed
            graded("confirmed", anchor_iou=None, moved=True),    # unmeasurable: no bucket
            graded("confirmed", anchor_iou=0.9, moved=False)]
    f = ranked_findings([{"rows": rows, "missed": [], "scale": 1.0}])
    assert [b["bucket"] for b in f["placement"]] == ["anchor_nudged", "anchor_repointed"]
    assert all(math.isclose(b["share"], 0.5) for b in f["placement"])
    assert f["value"] == []


def _manifest(roles, pool=None):
    return {"runs": [], "roles": roles, "pool": pool or {"great": 30, "problem": 20}}


NOTE = "PLANTED-CLIENT-NOTE-7"


def _campaign(tmp_path, extra=()):
    p = [proposal_row("a"), proposal_row("b")]
    return write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, [event(1, "set_note", text=NOTE)], group="great"),
        drawing("bbbbbbbb", p, [proposal_row("a")],
                [event(1, "delete_row", id="b", reason="duplicate")], group="problem"),
        drawing("cccccccc", p, p, [], group="problem", approved=False),
        *extra])


def test_grade_campaign_end_to_end_and_reconciles(tmp_path):
    # gold_by_hash=None: no cross-check asked for. {} would mean "asked, and
    # nothing joined", which is the gold_none_joined problem.
    report, digest = grade_campaign(read_records(_campaign(tmp_path)), gold_by_hash=None,
                                    manifest=_manifest({"aaaaaaaa": "great",
                                                        "bbbbbbbb": "problem",
                                                        "cccccccc": "problem",
                                                        "dddddddd": "great"}))
    assert digest["drawings"] == {"approved": 2, "pending": 1, "ungradable": 0,
                                  "ungradable_reasons": {}, "skipped": 0,
                                  "not_in_records": 1, "extra_in_records": 0}
    assert digest["groups"]["great"]["counts"]["confirmed"] == 2
    assert digest["groups"]["problem"]["counts"]["phantom"] == 1
    assert digest["reconciles"] is True and digest["reconcile_problems"] == []
    assert digest["pooled"]["precision"] is not None
    assert set(digest["pooled"]) == {"precision", "unflagged_precision", "flag_precision",
                                     "recall_shipped_vs_client_known",
                                     "recall_with_tray_vs_client_known"}
    assert digest["findings"]["value"][0]["bucket"] == "phantom:duplicate"
    assert digest["by_source_run"] == {"r5-control": 2}
    by = {d["doc_hash"]: d for d in report["drawings"]}
    assert by["cccccccc"]["status"] == "pending"
    assert by["aaaaaaaa"]["status"] == "approved" and by["aaaaaaaa"]["scale"] == 300 / 72.0


@pytest.mark.parametrize("rect,want", [
    ([0.0, 0.0, 842.0, 595.0], [0.0, 0.0, 842.0, 595.0]),
    ([0, 0, 842, 0], None), ([0, 0, float("nan"), 595], None), ([0, 0, 842], None),
    ("0 0 842 595", None), ([True, 0, 842, 595], None), (None, None)])
def test_the_report_carries_the_campaign_page_frame_and_the_digest_never(tmp_path,
                                                                        rect, want):
    """The operator's deck converts the report's px to points on the ORIGINAL
    drawing and must check it is the same page; both need the page frame the
    campaign rendered. A frame that is not 4 finite numbers spanning a real
    area is None, never a guess."""
    p = [proposal_row("a")]
    tar = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [], page_rect=rect)])
    report, digest = grade_campaign(read_records(tar), gold_by_hash={}, manifest=None)
    assert report["drawings"][0]["page_rect"] == want
    assert "page_rect" not in json.dumps(digest)


def test_notes_count_the_final_note_only_and_never_reach_the_digest(tmp_path):
    """The drill-down shows the client's note, so the protected report keeps
    it; the digest goes to docs/eval/ and may only count notes. A note typed
    and then cleared is no note: counting set_note events would say there is."""
    p = [proposal_row("a")]
    cleared = drawing("eeeeeeee", p, p, [event(1, "set_note", text="tmp"),
                                         event(2, "set_note", text="")])
    report, digest = grade_campaign(read_records(_campaign(tmp_path, [cleared])),
                                    gold_by_hash={}, manifest=None)
    assert digest["notes_written"] == 1
    by = {d["doc_hash"]: d for d in report["drawings"]}
    assert by["aaaaaaaa"]["note"] == NOTE and by["eeeeeeee"]["note"] == ""


def test_digest_carries_no_value_name_or_note(tmp_path):
    _, digest = grade_campaign(read_records(_campaign(tmp_path)), gold_by_hash={},
                               manifest=_manifest({"aaaaaaaa": "great"}))
    blob = json.dumps(digest)
    file_name = read_records(_campaign(tmp_path)).drawings["aaaaaaaa"] \
        .header["campaign"]["file_name"]
    for forbidden in ('"upper_tol"', '"lower_tol"', '"characteristics"', '"position_pt"',
                      file_name, NOTE):
        assert forbidden not in blob, forbidden


def test_damaged_pending_and_ungradable_drawings_are_counted_not_fatal(tmp_path):
    """One bad drawing must not sink the campaign. A record error keeps its
    type word; a crash inside grading becomes the fixed word grade_error (the
    exception text could quote a client value); a header without a usable
    render_scale cannot be put in mm and is ungradable too."""
    p = [proposal_row("a")]
    noid = [{k: v for k, v in proposal_row("SECRET-ID-VALUE").items() if k != "id"}]
    tar = _campaign(tmp_path, [
        drawing("dddddddd", p, p, [], header=False),
        drawing("eeeeeeee", noid, p, []),
        drawing("ffffffff", p, p, [], render_scale=None)])
    report, digest = grade_campaign(read_records(tar), gold_by_hash=None, manifest=None)
    d = digest["drawings"]
    assert d["approved"] == 2 and d["pending"] == 1 and d["ungradable"] == 3
    assert d["ungradable_reasons"] == {"header_missing": 1, "grade_error": 1,
                                       "no_render_scale": 1}
    assert d["not_in_records"] is None and d["extra_in_records"] is None
    assert digest["reconciles"] is True
    by = {r["doc_hash"]: r for r in report["drawings"]}
    assert by["eeeeeeee"]["reason"] == "grade_error"
    assert "SECRET" not in json.dumps(digest)


def test_group_differing_from_the_manifest_is_named(tmp_path):
    report, digest = grade_campaign(
        read_records(_campaign(tmp_path)), gold_by_hash={},
        manifest=_manifest({"aaaaaaaa": "problem", "bbbbbbbb": "problem",
                            "cccccccc": "problem", "zzzzzzzz": "great"}))
    assert "group_differs:aaaaaaaa" in digest["reconcile_problems"]
    assert digest["reconciles"] is False
    assert digest["drawings"]["not_in_records"] == 1


def test_records_outside_the_manifest_and_skipped_ones_are_accounted(tmp_path):
    p = [proposal_row("a")]
    tar = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])],
                    skipped=[{"id8": "deadbeef", "state": "ready", "reason": "no_proposal",
                              "doc_hash": "bbbbbbbb", "group": "great"}])
    _, digest = grade_campaign(read_records(tar), gold_by_hash={},
                               manifest=_manifest({"bbbbbbbb": "great"}))
    d = digest["drawings"]
    assert d["skipped"] == 1 and d["not_in_records"] == 0 and d["extra_in_records"] == 1


def test_missing_pool_leaves_pooled_values_unmeasured(tmp_path):
    _, digest = grade_campaign(read_records(_campaign(tmp_path)), gold_by_hash={},
                               manifest={"runs": [], "roles": {}})
    assert digest["pooled"] and all(v is None for v in digest["pooled"].values())


def _gold_at_fixture_row():
    # proposal_row's target_region [10,10,50,30] px at 300 dpi, in points.
    s = 300 / 72.0
    return GoldDoc(doc_id="D", pdf="x", excel="y", page_rect=(0.0, 0.0, 842.0, 595.0),
                   characteristics=[_g(1, (30 / s, 20 / s), "10"),
                                    _g(2, (400.0, 400.0), "99")])


def test_gold_cross_check_is_summed_as_counts_only(tmp_path):
    bad_frame = GoldDoc(doc_id="D", pdf="x", excel="y", page_rect=(0, 0, 1191, 842),
                        characteristics=[])
    report, digest = grade_campaign(
        read_records(_campaign(tmp_path)),
        gold_by_hash={"aaaaaaaa": _gold_at_fixture_row(), "bbbbbbbb": bad_frame},
        manifest=None)
    g = digest["gold_cross_check"]
    assert g["n_drawings_computed"] == 1
    assert g["not_computed"] == {"frame_mismatch": 1}
    assert sum(g["matrix"].values()) == 2            # both delivered rows of aaaaaaaa
    assert g["matched_final"] == 1 and g["neither_found"] == 1
    assert g["unlocatable"] == 0 and g["row_invalid"] == 0
    assert "verdicts" not in json.dumps(digest)
    by = {d["doc_hash"]: d for d in report["drawings"]}
    assert by["aaaaaaaa"]["cross"]["computed"] is True


def test_a_failing_cross_check_keeps_the_client_grade(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("SECRET-GOLD-VALUE")
    monkeypatch.setattr(campaign_grade, "gold_cross_check", boom)
    report, digest = grade_campaign(read_records(_campaign(tmp_path)),
                                    gold_by_hash={"aaaaaaaa": _gold_at_fixture_row()},
                                    manifest=None)
    assert digest["drawings"]["approved"] == 2
    # bbbbbbbb has no gold at all: said so, so coverage is never overstated
    assert digest["gold_cross_check"]["not_computed"] == {"cross_check_error": 1,
                                                          "no_gold": 1}
    by = {d["doc_hash"]: d for d in report["drawings"]}
    assert by["aaaaaaaa"]["cross"] == {"computed": False, "reason": "cross_check_error"}
    assert "SECRET" not in json.dumps(report)


# --- review of f63f900: no client text in the digest, real reconciliation ----
import pytest

LEAK = "Ø12 H7 LEAK"


def _tar(tmp_path, drawings, **kw):
    return read_records(write_tar(tmp_path / "x.tar", drawings, **kw))


def test_an_out_of_vocabulary_delete_reason_never_reaches_the_digest(tmp_path):
    """replay does not validate reasons and the grader reads the raw seal, so
    a reason can be any JSON a damaged or foreign journal holds. Only the
    closed vocabulary may name a bucket; anything else is `other`."""
    p = [proposal_row("a"), proposal_row("b"), proposal_row("c")]
    sealed_events = [event(1, "delete_row", id="a", reason=LEAK),
                     event(2, "delete_row", id="b", reason={"x": LEAK}),
                     event(3, "delete_row", id="c")]
    _, digest = grade_campaign(_tar(tmp_path, [drawing("aaaaaaaa", p, [], sealed_events)]))
    buckets = {b["bucket"]: b["count"] for b in digest["findings"]["value"]}
    assert buckets == {"phantom:other": 2, "phantom:none": 1}
    assert "LEAK" not in json.dumps(digest)


def test_free_form_group_source_run_and_campaign_are_replaced(tmp_path):
    p = [proposal_row("a")]
    d = drawing("aaaaaaaa", p, p, [], group=LEAK, source_run="part-4711 LEAK")
    # drawing() puts the group in both the header and the index entry
    rec = _tar(tmp_path, [d], campaign="pilot LEAK")
    _, digest = grade_campaign(rec)
    blob = json.dumps(digest)
    assert "LEAK" not in blob
    assert digest["campaign"] == "invalid"
    assert set(digest["groups"]) == {"unknown"}
    assert digest["by_source_run"] == {"unknown": 1}


def test_record_keys_that_are_not_hash_shaped_are_never_quoted(tmp_path):
    p = [proposal_row("a")]
    rec = _tar(tmp_path, [drawing(None, p, p, [], dir="LEAK dir")])
    report, digest = grade_campaign(rec, manifest=_manifest({"bbbbbbbb": "great"}))
    assert "LEAK" not in json.dumps(digest)
    assert [d["doc_hash"] for d in report["drawings"]] == ["invalid_key"]


def test_group_drawing_counts_must_add_up_to_approved(tmp_path, monkeypatch):
    real = campaign_grade.group_metrics
    monkeypatch.setattr(campaign_grade, "group_metrics",
                        lambda ds: {**real(ds), "n_drawings": real(ds)["n_drawings"] + 1})
    _, digest = grade_campaign(read_records(_campaign(tmp_path)))
    assert "group_drawings" in digest["reconcile_problems"]


def test_gold_coverage_must_add_up_to_approved(tmp_path, monkeypatch):
    real = campaign_grade._gold_section
    def lossy(crosses):
        s = real(crosses)
        s["not_computed"] = {}
        return s
    monkeypatch.setattr(campaign_grade, "_gold_section", lossy)
    _, digest = grade_campaign(read_records(_campaign(tmp_path)))
    assert "gold_coverage" in digest["reconcile_problems"]


def test_a_cross_check_matrix_that_loses_a_row_is_named(tmp_path, monkeypatch):
    real = campaign_grade.gold_cross_check
    def lossy(*a, **k):
        out = real(*a, **k)
        k0 = next(iter(out["matrix"]))
        out["matrix"][k0] -= 1
        out["matched_final"] += 1
        return out
    monkeypatch.setattr(campaign_grade, "gold_cross_check", lossy)
    _, digest = grade_campaign(read_records(_campaign(tmp_path)),
                               gold_by_hash={"aaaaaaaa": _gold_at_fixture_row()})
    assert "cross_matrix:aaaaaaaa" in digest["reconcile_problems"]
    assert "cross_gold:aaaaaaaa" in digest["reconcile_problems"]


def test_a_hash_both_recorded_and_skipped_is_named(tmp_path):
    p = [proposal_row("a")]
    rec = _tar(tmp_path, [drawing("aaaaaaaa", p, p, [])],
               skipped=[{"id8": "deadbeef", "reason": "no_proposal", "doc_hash": "aaaaaaaa"},
                        {"id8": "deadbee0", "reason": "no_proposal", "doc_hash": [LEAK]}])
    _, digest = grade_campaign(rec)
    assert "skipped_and_recorded:aaaaaaaa" in digest["reconcile_problems"]
    assert "LEAK" not in json.dumps(digest)


def test_pooled_values_say_why_they_are_unmeasured(tmp_path):
    p = [proposal_row("a")]
    rec = _tar(tmp_path, [drawing("aaaaaaaa", p, p, [], group="great"),
                          drawing("bbbbbbbb", p, p, [], group="odd")])
    # "odd" is a word the manifest's roles use, so it is allowed through
    _, digest = grade_campaign(rec, manifest=_manifest({"bbbbbbbb": "odd"}))
    assert digest["pooled_unmeasured"] == {"unpooled_groups": ["odd"],
                                           "pool_groups_missing": ["problem"]}
    assert digest["pooled"]["precision"] is None


def test_word_shaped_group_and_run_must_be_on_an_allowlist(tmp_path):
    """A part number passes any word-shape check. Group and source run reach
    the digest only if the manifest (or, failing that, our own run naming)
    vouches for them."""
    p = [proposal_row("a")]
    d = drawing("aaaaaaaa", p, p, [], group="PartNo-4711-LEAK",
                source_run="Bracket_LEAK_rev3")
    _, digest = grade_campaign(_tar(tmp_path, [d]))
    blob = json.dumps(digest)
    assert "LEAK" not in blob
    assert set(digest["groups"]) == {"unknown"}
    assert digest["by_source_run"] == {"unknown": 1}


def test_manifest_runs_are_the_source_run_allowlist(tmp_path):
    p = [proposal_row("a")]
    ds = [drawing("aaaaaaaa", p, p, [], source_run="r5-control"),
          drawing("bbbbbbbb", p, p, [], source_run="r4-controltest")]
    man = {"runs": [{"run": "r4-controltest", "hashes": ["bbbbbbbb"]}], "roles": {}}
    _, digest = grade_campaign(_tar(tmp_path, ds), manifest=man)
    assert digest["by_source_run"] == {"r4-controltest": 1, "unknown": 1}
    _, digest = grade_campaign(_tar(tmp_path, ds))
    assert digest["by_source_run"] == {"r4-controltest": 1, "r5-control": 1}


@pytest.mark.parametrize("net_events", [{"x": LEAK}, 4711])
def test_a_non_list_net_events_is_ungradable_without_leaking(tmp_path, net_events):
    p = [proposal_row("a")]
    d = drawing("aaaaaaaa", p, p, net_events, events_lines=[])
    _, digest = grade_campaign(_tar(tmp_path, [d]))
    assert digest["drawings"]["ungradable_reasons"] == {"replay_error": 1}
    assert "LEAK" not in json.dumps(digest)


def test_exact_levers_beat_prefixes_and_mixed_signatures_stay_generic():
    rows = [graded("edited", fields=["char_type"]),
            graded("edited", fields=["char_type", "nominal"]),
            graded("edited", fields=["upper_tol", "lower_tol"]),
            {**graded("phantom"), "reason": "duplicate"}]
    f = {b["bucket"]: b for b in
         ranked_findings([{"rows": rows, "missed": [], "scale": 1.0}])["value"]}
    assert f["fields:char_type"]["lever"].startswith("read stage")
    assert not f["fields:char_type+nominal"]["lever"].startswith("read stage")
    assert "multi-field" in f["fields:char_type+nominal"]["lever"]
    assert f["fields:upper_tol+lower_tol"]["lever"].startswith("render_target")
    assert f["phantom:duplicate"]["lever_status"] == "shipped"


def test_a_collided_hash_is_flagged_and_joined_once(tmp_path):
    p = [proposal_row("a"), proposal_row("b")]
    rec = _tar(tmp_path, [drawing("aaaaaaaa", p, p, []),
                          drawing("aaaaaaaa", p, p, [], dir="aaaaaaaa-1234abcd")])
    _, digest = grade_campaign(rec, gold_by_hash={"aaaaaaaa": _gold_at_fixture_row()},
                               manifest=_manifest({"aaaaaaaa": "problem"}))
    probs = digest["reconcile_problems"]
    assert "duplicate_doc_hash:aaaaaaaa" in probs
    assert probs.count("group_differs:aaaaaaaa") == 1
    g = digest["gold_cross_check"]
    assert g["n_drawings_computed"] == 1 and g["not_computed"] == {"duplicate_doc_hash": 1}


def test_malformed_record_shapes_never_crash_the_grade(tmp_path):
    p = [proposal_row("a")]
    ds = [drawing(h, p, p, []) for h in ("aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd")]
    ds[0]["header"]["campaign"] = [1, 2]
    ds[1]["header"]["campaign"] = "pilot"
    ds[2]["header"]["campaign"]["group"] = ["great"]
    ds[3]["header"]["campaign"]["source_run"] = {"r": 1}
    report, digest = grade_campaign(_tar(tmp_path, ds), manifest=_manifest({}))
    d = digest["drawings"]
    assert d["ungradable_reasons"] == {"no_render_scale": 2} and d["approved"] == 2
    json.dumps(digest)


@pytest.mark.parametrize("manifest", [
    [1], {"roles": ["aaaaaaaa"]}, {"roles": {"aaaaaaaa": ["great"]}},
    {"roles": {}, "pool": [30]}, {"roles": {}, "pool": {"great": -1}},
    {"roles": {}, "pool": {"great": float("nan")}}, {"roles": {}, "pool": {"great": "30"}},
    {"roles": {}, "pool": {"great": True}}])
def test_a_malformed_manifest_is_refused_with_fixed_words(tmp_path, manifest):
    with pytest.raises(ValueError) as ei:
        grade_campaign(read_records(_campaign(tmp_path)), manifest=manifest)
    assert str(ei.value) == "manifest: bad roles/pool"


def test_an_unreadable_note_costs_the_note_not_the_grade(tmp_path):
    p = [proposal_row("a")]
    rec = _tar(tmp_path, [drawing("aaaaaaaa", p, p, [event(1, "set_note")]),
                          drawing("bbbbbbbb", p, p, [event(1, "set_note", text={"x": 1})])])
    report, digest = grade_campaign(rec)
    assert digest["drawings"]["approved"] == 2
    assert digest["notes_unreadable"] == 2 and digest["notes_written"] == 0
    assert all(d["note"] == "" for d in report["drawings"])


# --- final review (C2): gold that joins nothing is a problem, not a silence ---

def test_gold_asked_for_but_joined_to_nothing_is_a_problem(tmp_path):
    """A wrong salt or a wrong --gold dir leaves every drawing 'no_gold' and
    would otherwise reconcile cleanly: the cross-check silently vanishes."""
    _, digest = grade_campaign(read_records(_campaign(tmp_path)), gold_by_hash={},
                               manifest=None)
    assert "gold_none_joined" in digest["reconcile_problems"]
    assert digest["reconciles"] is False
    # None = no cross-check requested at all: nothing to complain about.
    _, digest = grade_campaign(read_records(_campaign(tmp_path)), gold_by_hash=None)
    assert "gold_none_joined" not in digest["reconcile_problems"]


def test_a_frame_mismatch_on_every_drawing_is_gold_none_joined(tmp_path):
    bad = GoldDoc(doc_id="D", pdf="x", excel="y", page_rect=(0, 0, 1191, 842))
    _, digest = grade_campaign(read_records(_campaign(tmp_path)),
                               gold_by_hash={"aaaaaaaa": bad, "bbbbbbbb": bad})
    assert digest["gold_cross_check"]["not_computed"] == {"frame_mismatch": 2}
    assert "gold_none_joined" in digest["reconcile_problems"]


def test_a_partial_gold_join_is_not_a_problem(tmp_path):
    _, digest = grade_campaign(read_records(_campaign(tmp_path)),
                               gold_by_hash={"aaaaaaaa": _gold_at_fixture_row()})
    assert digest["gold_cross_check"]["n_drawings_computed"] == 1
    assert digest["reconcile_problems"] == []


def test_no_approved_drawing_needs_no_gold(tmp_path):
    p = [proposal_row("a")]
    rec = _tar(tmp_path, [drawing("aaaaaaaa", p, p, [], approved=False)])
    _, digest = grade_campaign(rec, gold_by_hash={})
    assert digest["reconcile_problems"] == []


# --- final review (C2): the precision ratios are checkable from the digest ---

def test_flag_split_counts_and_explicit_lower_bounds_are_emitted():
    """precision / unflagged_precision count SILENT confirmations (approval
    only needs flagged rows resolved), so the digest must carry the counts
    behind them and the explicit-only lower bounds."""
    rows = [graded("confirmed", reviewed=True),                  # unflagged, explicit
            graded("confirmed"),                                 # unflagged, silent
            graded("edited"), graded("phantom"),                 # unflagged, wrong
            graded("confirmed", flagged=True, reviewed=True),    # flagged, right
            graded("phantom", flagged=True),                     # flagged, wrong
            graded("suggestion_left")]
    m = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])
    c = m["counts"]
    assert (c["flagged"], c["flagged_wrong"]) == (2, 1)
    assert (c["unflagged_confirmed"], c["unflagged_edited"], c["unflagged_phantom"]) == (2, 1, 1)
    assert c["unflagged_confirmed"] + c["unflagged_edited"] + c["unflagged_phantom"] \
        + c["flagged"] == 6
    assert math.isclose(m["unflagged_precision"], 2 / 4)
    assert math.isclose(m["unflagged_precision_explicit_only"], 1 / 4)
    assert math.isclose(m["precision"], 3 / 6)
    assert math.isclose(m["precision_explicit_only"], 2 / 6)
    assert math.isclose(m["flag_precision"], c["flagged_wrong"] / c["flagged"])


def test_explicit_only_ratios_are_none_without_a_denominator():
    m = group_metrics([{"rows": [graded("suggestion_left")], "missed": [], "scale": 1.0,
                        "time_ms": 0}])
    assert m["precision_explicit_only"] is None
    assert m["unflagged_precision_explicit_only"] is None


@pytest.mark.parametrize("break_it, problem", [
    (lambda c: c.__setitem__("flagged", c["flagged"] + 1), "group_flag_partition:great"),
    (lambda c: c.__setitem__("confirmed_explicit", c["confirmed"] + 1),
     "group_explicit:great"),
])
def test_the_flag_partition_and_explicit_bound_are_checked(tmp_path, monkeypatch,
                                                           break_it, problem):
    real = campaign_grade.group_metrics

    def drifted(ds):
        m = real(ds)
        break_it(m["counts"])
        return m
    monkeypatch.setattr(campaign_grade, "group_metrics", drifted)
    _, digest = grade_campaign(read_records(_campaign(tmp_path)))
    assert problem in digest["reconcile_problems"]


# --- final review (C2): odd inputs ------------------------------------------

def test_a_non_finite_placement_never_makes_the_digest_invalid_json(tmp_path):
    """A journal move of 1e308 -> -1e308 overflows math.dist to inf, and an
    anchor of that size makes the IoU nan; json.dumps would then write
    Infinity/NaN, which is not JSON."""
    big = 1e308
    p = [proposal_row("a", balloon_xy=[-big, -big],
                      target_region=[-big, -big, big, big])]
    final = [proposal_row("a", balloon_xy=[big, big], target_region=[-big, -big, big, 1.0])]
    d = drawing("aaaaaaaa", p, final,
                [event(1, "move_row", id="a", xy=[big, big]),
                 event(2, "move_target", id="a", box=[-big, -big, big, 1.0])])
    report, digest = grade_campaign(_tar(tmp_path, [d]))
    assert digest["drawings"]["approved"] == 1
    row = report["drawings"][0]["rows"][0]
    assert row["bubble_px"] is None and row["anchor_iou"] is None
    json.dumps(digest, allow_nan=False)
    json.dumps(report, allow_nan=False)


def test_group_metrics_skip_a_non_finite_distance():
    rows = [graded("confirmed", bubble_px=float("inf")), graded("confirmed", bubble_px=60.0)]
    m = group_metrics([{"rows": rows, "missed": [], "scale": 1.0, "time_ms": 0}])
    assert m["placement"]["placed"] == 1
    json.dumps(m, allow_nan=False)


def test_manifest_run_names_must_be_tokens(tmp_path):
    """A run name is copied into the digest's by_source_run, so a name that
    is not a plain token never joins the allowlist."""
    p = [proposal_row("a")]
    ds = [drawing("aaaaaaaa", p, p, [], source_run="PART 4711/x")]
    man = {"runs": [{"run": "PART 4711/x"}], "roles": {}}
    _, digest = grade_campaign(_tar(tmp_path, ds), manifest=man)
    assert digest["by_source_run"] == {"unknown": 1}


def test_the_name_is_recorded_after_the_kind(tmp_path):
    report, digest = grade_campaign(read_records(_campaign(tmp_path)), name="pilot-1")
    assert list(digest)[:2] == ["kind", "name"] and digest["name"] == "pilot-1"
    assert list(report)[:2] == ["kind", "name"] and report["name"] == "pilot-1"
    _, digest = grade_campaign(read_records(_campaign(tmp_path)), name="PART 4711/x")
    assert digest["name"] == "unnamed"
    _, digest = grade_campaign(read_records(_campaign(tmp_path)))
    assert digest["name"] == "unnamed"


def test_an_empty_extraction_delete_is_its_own_phantom_bucket_with_a_lever():
    """A box the reader returned nothing for is not "other": the reviewer named
    it, and the fix lives in detection (a non-text region was ballooned), not
    in the read."""
    f = ranked_findings([{"rows": [{**graded("phantom"), "reason": "empty_extraction"}],
                          "missed": [], "scale": 300 / 72.0}])
    [b] = f["value"]
    assert b["bucket"] == "phantom:empty_extraction"
    assert b["lever"] != "unmapped" and "detect" in b["lever"]


def test_reviewer_flags_are_counted_by_what_became_of_the_row():
    """The reviewer moving a row to Review is signal about the rows they
    doubted: how many, and whether the doubt was right (edited/deleted) or not
    (confirmed). The three outcomes partition the flagged rows."""
    rows = [{**graded("confirmed"), "manual_flag": True},
            {**graded("edited", fields=["nominal"]), "manual_flag": True},
            {**graded("phantom"), "manual_flag": True},
            {**graded("confirmed"), "manual_flag": False},
            graded("confirmed")]       # a row graded before the field existed
    c = group_metrics([{"rows": rows, "missed": [], "scale": 300 / 72.0}])["counts"]
    assert (c["manual_flagged"], c["manual_flagged_confirmed"],
            c["manual_flagged_edited"], c["manual_flagged_phantom"]) == (3, 1, 1, 1)


def _scan(outcome, empty=False, fields=(), reason=None, moved=False, ms=100):
    return {"row_id": "x", "outcome": outcome, "empty": empty, "fields": list(fields),
            "reason": reason, "box_iou": 0.5 if moved else 1.0, "box_moved": moved, "ms": ms}


def test_scan_outcomes_are_counted_per_group_and_partition_the_attempts():
    scans = [_scan("untouched"), _scan("edited", fields=["nominal"], moved=True, ms=300),
             _scan("deleted", empty=True, reason="empty_extraction", ms=500),
             _scan("undone", empty=True), _scan("failed", empty=True, ms=900)]
    d = {"rows": [], "missed": [{"via": "scan"}, {"via": "scan"}, {"via": "typed"}],
         "scale": 300 / 72.0, "scans": scans}
    m = group_metrics([d])
    s = m["scans"]
    assert (s["attempts"], s["untouched"], s["edited"], s["deleted"], s["undone"],
            s["failed"]) == (5, 1, 1, 1, 1, 1)
    assert s["empty"] == 3 and s["box_moved"] == 1
    assert s["edits"] == {"fields:nominal": 1}
    assert s["deleted_by_reason"] == {"empty_extraction": 1}
    assert (s["ms_median"], s["ms_max"]) == (300, 900)
    c = m["counts"]
    assert (c["missed"], c["missed_scan"], c["missed_typed"], c["missed_via_unknown"]) == (3, 2, 1, 0)


def test_a_drawing_with_no_scans_reports_zero_attempts_not_a_missing_section():
    m = group_metrics([{"rows": [], "missed": [{}], "scale": 300 / 72.0, "scans": []}])
    assert m["scans"]["attempts"] == 0 and m["scans"]["ms_median"] is None
    # A missed row graded before `via` existed is unknown, never typed.
    assert m["counts"]["missed_via_unknown"] == 1 and m["counts"]["missed_typed"] == 0
