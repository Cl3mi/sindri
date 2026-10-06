"""score --drop-check: prices the registered phantom-drop configurations exactly,
re-scoring with matching, against today's reapplied code
(docs/plans/2026-10-06-phantom-drops-registration.md).

Fixture (from the phantom profile): delivered = a correct row at confidence
0.99, a wrong row at 0.93, a phantom at 0.96; two more rows are flagged and
not delivered."""
import json

import pytest

from app.eval import drop_check as dc
from app.eval.models import MatchParams, ReviewCostWeights
from tests.eval.test_phantom_profile import _setup


def _report(configs=None):
    dumps, golds = _setup()
    return dc.drop_report(dumps, golds, ["D"], ReviewCostWeights(),
                          MatchParams(), configs=configs)


def test_the_registered_configurations_are_exactly_eleven():
    assert list(dc.CONFIGS) == [
        "conf_below_090", "conf_below_095", "conf_below_099",
        "material_kind", "tight_cluster",
        "conf_below_090+material_kind",
        "conf_below_090+material_kind+tight_cluster",
        "conf_below_095+material_kind",
        "conf_below_095+material_kind+tight_cluster",
        "conf_below_099+material_kind",
        "conf_below_099+material_kind+tight_cluster",
    ]


def test_control_is_the_delivered_set_today():
    c = _report()["control"]
    assert (c["correct"], c["escaped"], c["phantom"]) == (1, 1, 1)
    assert c["delivered_precision"] == pytest.approx(1 / 3, abs=1e-4)


def test_a_dose_removes_exactly_the_rows_below_it():
    r = _report()["configs"]
    a = r["conf_below_095"]["arm"]
    assert (a["correct"], a["escaped"], a["phantom"]) == (1, 0, 1)
    a = r["conf_below_099"]["arm"]
    assert (a["correct"], a["escaped"], a["phantom"]) == (1, 0, 0)
    assert r["conf_below_099"]["correct_lost"] == 0


def test_keep_rule_needs_a_strict_rise():
    r = _report()["configs"]
    assert r["conf_below_090"]["passes"] is False     # removes nothing
    assert r["conf_below_095"]["passes"] is True
    assert r["conf_below_099"]["passes"] is True


def test_an_empty_delivered_set_never_passes():
    """The registered guard against the 'deliver nothing' extreme: precision
    of an empty set is unmeasured, never a perfect 1.0."""
    assert dc.passes({"delivered_precision": 0.5, "matched_precision": 0.5,
                      "delivered": 3},
                     {"delivered_precision": None, "matched_precision": None,
                      "delivered": 0}) is False


def test_matched_precision_may_not_fall():
    ctl = {"delivered_precision": 0.4, "matched_precision": 0.8, "delivered": 10}
    arm = {"delivered_precision": 0.6, "matched_precision": 0.7, "delivered": 5}
    assert dc.passes(ctl, arm) is False


def test_selection_is_the_best_passing_delivered_precision():
    r = _report()
    # 099 and its joints all reach 1.0 here; ties go to more correct values,
    # then to the registered order -- the plain dose comes first.
    assert r["selected"] == "conf_below_099"


def test_selection_is_none_when_nothing_passes():
    r = _report(configs=["conf_below_090"])
    assert r["selected"] is None


def test_recall_cost_and_weightings_are_reported_but_not_gated():
    a = _report()["configs"]["conf_below_099"]
    assert {"recall", "cost", "cost_per_weighting", "better_under"} <= set(a["arm"]) | set(a)
    assert len(a["arm"]["cost_per_weighting"]) == 6


def test_configs_can_be_restricted_for_dev_and_test():
    r = _report(configs=["conf_below_099"])
    assert list(r["configs"]) == ["conf_below_099"]


def test_unknown_configuration_is_refused():
    with pytest.raises(ValueError, match="unknown"):
        _report(configs=["conf_below_050"])


def test_output_is_values_blind():
    blob = json.dumps(_report())
    for value in ("±", '"20"', '"36"', "0,1"):
        assert value not in blob


def test_input_dumps_are_not_mutated():
    dumps, golds = _setup()
    before = dumps["D"].model_dump_json()
    dc.drop_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams())
    assert dumps["D"].model_dump_json() == before
