"""policy_check prices a rule by applying it to dumps and RE-SCORING, so
matching changes (a dropped duplicate letting its neighbour pair) are counted,
not assumed."""
import json

import pytest

from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.policy_check import _LOW_CONF, policy_report
from app.models import Characteristic, ExtractionResult
from app.pipeline.policy_rules import FLAG_RULES

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _setup():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Distance", nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(400, 100),
                           char_type="Distance", nominal="30"),
    ])
    chars = [
        # correct, unflagged
        Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99,
                       target_region=_box(100, 100)),
        # wrong, unflagged, gdt kind -> escaped; nondim_kind would flag it
        Characteristic(pos=2, kind="gdt", char_type="Flatness", nominal="0",
                       raw_text="0,05", confidence=0.99,
                       target_region=_box(400, 100)),
        # phantom with an empty read -> false detection; empty_read drops it
        Characteristic(pos=3, kind="dimension", raw_text="", confidence=0.0,
                       needs_review=True, review_reasons=["empty read"],
                       target_region=_box(800, 600)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    return {"D": dump}, {"D": gold}


def _report(**kw):
    dumps, golds = _setup()
    return policy_report(dumps, golds, ["D"], ReviewCostWeights(),
                         MatchParams(), **kw)


def test_flag_rule_converts_an_escaped_row_and_raises_auto_accept():
    fr = _report(flag_rules=("nondim_kind",),
                 drop_rules=())["flag_rules"]["nondim_kind"]
    assert fr["newly_flagged"] == {"escaped_error": 1}
    assert fr["delta"]["cost"] == -4.0          # 5 -> 1 on one doc
    assert fr["delta"]["auto_accept_precision"] > 0
    assert fr["delta"]["auto_accept_rate"] == 0
    assert fr["better_under"] == 6
    assert fr["passes"] is True


def test_drop_rule_removes_a_false_detection_and_is_repriced():
    dr = _report(flag_rules=(),
                 drop_rules=("empty_read",))["drop_rules"]["empty_read"]
    assert dr["dropped"] == {"false_detection": 1}
    assert dr["delta"]["cost"] == -2.0
    assert dr["passes"] is True


def test_a_flag_rule_that_only_hits_correct_rows_fails():
    """no_tolerance fires on the correct dimension row and on nothing escaped:
    +1 cost, and the unflagged set shrinks without getting more trustworthy."""
    fr = _report(flag_rules=("no_tolerance",),
                 drop_rules=())["flag_rules"]["no_tolerance"]
    assert fr["newly_flagged"] == {"correct": 1}
    assert fr["delta"]["cost"] == 1.0
    assert fr["passes"] is False


def test_base_gate_reports_no_reflag_on_current_code_dumps():
    assert _report(flag_rules=(), drop_rules=())["base_low_conf_reflagged"] == 0


def test_base_normalises_an_old_threshold_dump():
    """A row at 0.7 confidence, unflagged, is what a 0.6-era dump holds;
    today's pipeline would flag it, so the base must too before any rule is
    priced on top."""
    dumps, golds = _setup()
    dumps["D"].result.characteristics[1].confidence = 0.7
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=(), drop_rules=())
    assert r["base_low_conf_reflagged"] == 1
    assert r["base"]["counts"].get("escaped_error", 0) == 0


def test_joint_set_is_priced_together():
    r = _report(flag_rules=("nondim_kind",), drop_rules=("empty_read",),
                joint=(("nondim_kind",), ("empty_read",)))
    assert r["joint"]["delta"]["cost"] == -6.0
    assert r["joint"]["passes"] is True


def test_output_is_values_blind():
    blob = json.dumps(_report(flag_rules=tuple(FLAG_RULES), drop_rules=()))
    for value in ("0,05", "Flatness", '"20"', '"30"'):
        assert value not in blob
    # The repo's pre-commit hook rejects JSON carrying these as quoted tokens.
    assert "upper_tol" not in blob and "lower_tol" not in blob


def test_input_dumps_are_not_mutated():
    dumps, golds = _setup()
    before = dumps["D"].model_dump_json()
    policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                  joint=(("nondim_kind",), ("empty_read",)))
    assert dumps["D"].model_dump_json() == before


def test_low_conf_copy_matches_the_pipeline():
    from app.eval.policy_check import _LOW_CONF
    from app.pipeline.review import LOW_CONF
    assert _LOW_CONF == LOW_CONF


def test_precision_unmeasured_when_flagging_empties_the_unflagged_set():
    """A flag rule that flags the only currently-unflagged matched row (here
    the escaped-error gdt row) leaves no unflagged set to have a precision at
    all. Coercing that to 0.0 would score a delta of exactly 0 against the
    base's real 0.0 and silently mask what happened; None must propagate all
    the way to `passes`, which must treat it as a fail, not a wash."""
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Distance", nominal="30"),
    ])
    chars = [
        Characteristic(pos=1, kind="gdt", char_type="Flatness", nominal="0",
                       raw_text="0,05", confidence=0.99,
                       target_region=_box(100, 100)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    r = policy_report({"D": dump}, {"D": gold}, ["D"], ReviewCostWeights(),
                      MatchParams(), flag_rules=("nondim_kind",), drop_rules=())
    fr = r["flag_rules"]["nondim_kind"]
    assert fr["delta"]["auto_accept_precision"] is None
    assert fr["precision_unmeasured"] is True
    assert fr["passes"] is False


def test_newly_flagged_histogram_counts_false_detections_too():
    """A flag rule fires just as easily on a row with no gold match at all --
    e.g. nondim_kind on a phantom GD&T detection. The base pair taxonomy has
    no entry for an unmatched row, so before this fix the lookup returned
    None and the histogram silently dropped it -- undercounting exactly the
    rows a reviewer would see get flagged."""
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Distance", nominal="20"),
    ])
    chars = [
        Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99,
                       target_region=_box(100, 100)),
        Characteristic(pos=2, kind="gdt", char_type="Flatness", nominal="0",
                       raw_text="0,05", confidence=0.99,
                       target_region=_box(800, 600)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    r = policy_report({"D": dump}, {"D": gold}, ["D"], ReviewCostWeights(),
                      MatchParams(), flag_rules=("nondim_kind",), drop_rules=())
    assert r["flag_rules"]["nondim_kind"]["newly_flagged"] == {"false_detection": 1}


def test_base_gate_raises_if_a_current_config_dump_needs_reflagging():
    """extra.review_low_conf == _LOW_CONF says this dump was already produced
    under today's threshold. A row there still needing a reflag means
    review.LOW_CONF and this copy have diverged -- that must be loud, not a
    silent re-normalisation that hides the drift."""
    dumps, golds = _setup()
    dumps["D"].config.extra = {"review_low_conf": _LOW_CONF}
    dumps["D"].result.characteristics[1].confidence = 0.7
    with pytest.raises(ValueError):
        policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=(), drop_rules=())


def test_base_gate_counts_pre_low_conf_docs():
    """No `review_low_conf` key at all is what every dump predates the
    threshold move looks like -- this must reflag (as the existing base test
    already checks) and must be COUNTED as pre-LOW_CONF, not silently folded
    into the current-config bucket the raise above guards."""
    dumps, golds = _setup()
    dumps["D"].result.characteristics[1].confidence = 0.7
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=(), drop_rules=())
    assert r["n_docs_pre_low_conf"] == 1
    assert r["base_low_conf_reflagged"] == 1


def test_empty_doc_ids_raises():
    dumps, golds = _setup()
    with pytest.raises(ValueError):
        policy_report(dumps, golds, [], ReviewCostWeights(), MatchParams())


def test_duplicate_doc_ids_raises():
    dumps, golds = _setup()
    with pytest.raises(ValueError):
        policy_report(dumps, golds, ["D", "D"], ReviewCostWeights(), MatchParams())


def test_drop_rule_re_pairing_can_lower_cost_while_lowering_quality():
    """note_kind drops the correctly-matched, unflagged row (recast here as
    kind "note"); the only other candidate near its gold balloon is a WORSE,
    already-flagged read that then re-pairs onto it. Cost falls -- one
    escaped-flavoured toggle away from what the taxonomy tally alone would
    reward -- while field_acc falls just as hard. This is exactly why cost
    down is never sufficient on its own: the guards, not cost, must decide,
    and matching is RE-SCORED here, never assumed."""
    dumps, golds = _setup()
    dumps["D"].result.characteristics[0].kind = "note"
    dumps["D"].result.characteristics.append(Characteristic(
        pos=4, kind="dimension", char_type="Distance", nominal="21",
        raw_text="21", confidence=0.5, needs_review=True,
        target_region=_box(102, 100)))
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=(), drop_rules=("note_kind",))
    dr = r["drop_rules"]["note_kind"]
    assert dr["dropped"] == {"correct": 1}
    assert dr["delta"]["cost"] == -1.0
    assert dr["delta"]["field_acc"] == -0.5
    assert dr["better_under"] == 4
    assert dr["passes"] is False
