"""score --gentol-check: the CPU gate for fill_general_tolerance.

It prices the fill the only way that is exact -- re-scoring the reapplied
dumps with and without it -- and attributes every matched row's taxonomy
transition. The gate (docs/plans/2026-10-05-general-tolerance-registration.md
§4) is `fix >= 4 * Wilson-upper(break) AND fix >= 20`; a break costs +4 (flag 1
-> escaped 5) and a fix saves 1, so the 4 is the break-even ratio itself."""
import json

import pytest

from app.eval import gentol_check as gc
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.models import Characteristic, ExtractionResult, TitleField

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


# (x, gold kwargs, pred kwargs) -- one row per registered outcome, each far
# enough from the others that matching is unambiguous.
ROWS = [
    # FIX: general tolerance, read without one
    (100, dict(char_type="Distance", nominal="20", upper_tol="0,2",
               lower_tol="-0,2"),
          dict(char_type="Distance", nominal="20", raw_text="20")),
    # BREAK printed_missed: a printed tolerance the read dropped
    (200, dict(char_type="Distance", nominal="50", upper_tol="+0,05",
               lower_tol="-0,02"),
          dict(char_type="Distance", nominal="50", raw_text="50")),
    # BREAK other:field: tolerance right, nominal misread
    (300, dict(char_type="Distance", nominal="8", upper_tol="0,2",
               lower_tol="-0,2"),
          dict(char_type="Distance", nominal="9", raw_text="9")),
    # BREAK fit_notation: gold names the fit, the read did not show it
    (400, dict(char_type="Diameter", nominal="20 H7", upper_tol="+0,021",
               lower_tol="0"),
          dict(char_type="Diameter", nominal="20", raw_text="Ø20")),
    # BREAK other:gold_no_tolerance: nothing to fill, gold agrees
    (500, dict(char_type="Distance", nominal="12"),
          dict(char_type="Distance", nominal="12", raw_text="12")),
    # BREAK other:class_or_table: gold holds class f's value, drawing says m
    (600, dict(char_type="Distance", nominal="25", upper_tol="0,1",
               lower_tol="-0,1"),
          dict(char_type="Distance", nominal="25", raw_text="25")),
    # SKIP fit_guard: the read shows a fit
    (700, dict(char_type="Distance", nominal="20", upper_tol="0",
               lower_tol="-0,013"),
          dict(char_type="Distance", nominal="20", raw_text="20 h6")),
    # SKIP table_none: below ISO 2768's table
    (800, dict(char_type="Distance", nominal="0,3", upper_tol="0,05",
               lower_tol="-0,05"),
          dict(char_type="Distance", nominal="0,3", raw_text="0,3")),
    # NEUTRAL: filled correctly but still flagged for low confidence
    (900, dict(char_type="Distance", nominal="30", upper_tol="0,2",
               lower_tol="-0,2"),
          dict(char_type="Distance", nominal="30", raw_text="30",
               confidence=0.5)),
]


def _doc(doc_id="D", rows=ROWS, title="ISO 2768-mK"):
    gold = GoldDoc(doc_id=doc_id, pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=i + 1, position_pt=(x, 100), **g)
        for i, (x, g, _) in enumerate(rows)])
    chars = []
    for i, (x, _, p) in enumerate(rows):
        kw = dict(confidence=0.99)
        kw.update(p)
        chars.append(Characteristic(pos=i + 1, kind="dimension",
                                    target_region=_box(x, 100), **kw))
    dump = PredictionDump(
        doc_id=doc_id, config=RunConfig(model_id="s", dpi=300), scale=SCALE,
        page_rect=RECT, result=ExtractionResult(
            characteristics=chars,
            title_block=[TitleField(label="Tol", value=title)] if title else []))
    return dump, gold


def _report(docs=None):
    docs = docs or [_doc()]
    dumps = {d.doc_id: d for d, _ in docs}
    golds = {g.doc_id: g for _, g in docs}
    return gc.gentol_report(dumps, golds, list(dumps), ReviewCostWeights(),
                            MatchParams(), anonymizer=lambda s: f"h-{s}")


# --- the counts ------------------------------------------------------------------

def test_every_registered_outcome_is_counted_once():
    r = _report()
    assert r["would_fix"] == 1
    assert r["would_break"] == 5
    assert r["break_breakdown"] == {
        "printed_missed": 1, "fit_notation": 1, "other:field": 1,
        "other:gold_no_tolerance": 1, "other:class_or_table": 1}
    assert r["neutral"] == 1
    assert r["rows"]["fit_guard"] == 1
    assert r["rows"]["table_none"] == 1
    assert r["rows"]["filled"] == 7


def test_break_breakdown_rolls_up_to_the_three_registered_heads():
    r = _report()
    assert r["break_heads"] == {"printed_missed": 1, "fit_notation": 1,
                                "other": 3}


def test_transitions_are_reported_in_full():
    r = _report()
    t = r["transitions"]
    assert t["flagged_error->correct"] == 1
    assert t["flagged_error->escaped_error"] == 4
    assert t["flagged_correct->escaped_error"] == 1
    assert t["flagged_error->flagged_correct"] == 1
    assert sum(t.values()) == r["matched_filled"]


def test_coverage_by_source_and_class():
    r = _report([_doc("A"), _doc("B", title=None),
                 _doc("C", title="ISO 2768-m / ISO 2768-c")])
    cov = r["coverage"]
    assert cov["docs"] == 3
    assert cov["by_source"] == {"title": 1, "none": 1, "conflict": 1}
    assert cov["class_histogram"] == {"m": 1}


def test_a_document_with_no_class_fills_nothing():
    r = _report([_doc(title=None)])
    assert r["would_fix"] == r["would_break"] == 0
    assert r["rows"]["no_class"] == len(ROWS)


def test_slices_by_class_kind_and_source_sum_to_the_totals():
    r = _report()
    for axis in ("by_class", "by_kind", "by_source"):
        sl = r["slices"][axis]
        assert sum(v["would_fix"] for v in sl.values()) == r["would_fix"]
        assert sum(v["would_break"] for v in sl.values()) == r["would_break"]
    assert r["slices"]["by_class"]["m"]["would_break"] == 5
    assert r["slices"]["by_kind"]["Diameter"]["break_breakdown"] == {
        "fit_notation": 1}
    assert r["slices"]["by_source"]["title"]["would_fix"] == 1


# --- the cost path the counts must reconcile with --------------------------------

def test_counts_reconcile_with_the_rescored_cost():
    """The exactness check the registration pins: -1 per fix, +4 per break,
    nothing else, because the fill cannot move matching."""
    r = _report()
    assert r["reconciles"] is True
    assert r["delta"]["cost"] == pytest.approx((-1 * 1 + 4 * 5) / 1)


def test_arm_and_control_stats_are_reported_with_all_six_weightings():
    r = _report()
    assert set(r["control"]) >= {"cost", "auto_accept_precision",
                                 "auto_accept_rate", "counts"}
    assert len(r["cost_per_weighting"]) == 6
    assert 0 <= r["better_under"] <= 6


def test_matching_must_not_move():
    """If a fill ever changed which prediction pairs with which gold row, the
    per-row attribution would be meaningless. It raises rather than report."""
    dump, gold = _doc()
    control = {"D": dump}
    arm = {"D": dump.model_copy(deep=True)}
    arm["D"].result.characteristics[0].target_region = _box(1000, 700)
    with pytest.raises(AssertionError, match="matching"):
        gc._attribute(control, arm, {"D": gold}, ["D"], ReviewCostWeights(),
                      MatchParams())


# --- the gate ----------------------------------------------------------------------

def test_wilson_upper_matches_the_closed_form():
    assert gc.wilson_upper(0, 25) == pytest.approx(3.8416 / 28.8416, rel=1e-4)
    assert gc.wilson_upper(5, 35) == pytest.approx(0.2938, abs=1e-3)
    assert gc.wilson_upper(0, 0) == 1.0


@pytest.mark.parametrize("fix,brk,passes", [
    (100, 3, True),     # n=103, U~0.083 -> 4Un~34
    (30, 5, False),     # n=35,  U~0.30  -> 4Un~41
    (19, 0, False),     # Wilson clears, MIN_FIX does not
    (20, 0, True),
    (0, 0, False),
])
def test_gate(fix, brk, passes):
    assert gc.gate(fix, brk)["passes"] is passes


def test_min_fix_is_the_registered_twenty():
    assert gc.MIN_FIX == 20


def test_gate_without_the_worst_document():
    per_doc = {"a": (30, 0), "b": (30, 1), "c": (10, 6)}
    g = gc.gate_without_worst(per_doc)
    assert g["removed"] == "c"
    assert (g["would_fix"], g["would_break"]) == (60, 1)
    assert g["passes"] is True


def test_concentration_lists_salted_ids_only():
    r = _report([_doc("A"), _doc("B")])
    conc = r["concentration"]
    assert {d["doc"] for d in conc["docs"]} == {"h-A", "h-B"}
    assert conc["max_doc_break_share"] == pytest.approx(0.5)
    assert "gate_without_worst" in conc


def test_report_states_pass_or_fail():
    r = _report()
    assert r["gate"]["passes"] is False
    assert r["verdict"] == "FAIL"


def test_output_is_values_blind():
    blob = json.dumps(_report())
    for value in ("H7", "0,021", "+0,05", "0,013", '"20"', "ISO 2768"):
        assert value not in blob


def test_input_dumps_are_not_mutated():
    dump, gold = _doc()
    before = dump.model_dump_json()
    gc.gentol_report({"D": dump}, {"D": gold}, ["D"], ReviewCostWeights(),
                     MatchParams(), anonymizer=str)
    assert dump.model_dump_json() == before


def test_reconciliation_catches_a_cost_move_the_fill_did_not_cause():
    """Unflag a row the fill never touched: the cost moves, no attributed
    transition explains it, and reconciles must say so."""
    from app.eval.reapply import reapply_current_code
    dump, gold = _doc()
    control = {"D": reapply_current_code(dump)}
    arm = {"D": reapply_current_code(dump, fill=True)}
    guarded = next(c for c in arm["D"].result.characteristics
                   if c.raw_text == "20 h6")
    guarded.needs_review, guarded.review_reasons = False, []
    r = gc._attribute(control, arm, {"D": gold}, ["D"], ReviewCostWeights(),
                      MatchParams())
    assert r["reconciles"] is False
