"""The pipeline applies the ACTIVE rules through the same functions the
counterfactual priced them with, and records them in RunConfig."""
from app.models import Characteristic
from app.pipeline import policy_rules as pr
from app.pipeline.extract import _apply_active_policy
from app.pipeline.review import active_review_policy


def test_active_policy_flags_and_drops_through_the_registry(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ("empty_read",))
    a = Characteristic(pos=0, kind="gdt", raw_text="0,05",
                       target_region=(0, 0, 10, 10))
    b = Characteristic(pos=0, kind="dimension", raw_text="",
                       target_region=(20, 0, 30, 10))
    out = _apply_active_policy([a, b])
    assert out == [a]
    assert a.needs_review and pr.FLAG_REASONS["nondim_kind"] in a.review_reasons


def test_a_row_already_flagged_keeps_its_reasons_and_gains_the_rule(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ())
    a = Characteristic(pos=0, kind="gdt", raw_text="0,05", needs_review=True,
                       review_reasons=["low OCR confidence"],
                       target_region=(0, 0, 10, 10))
    _apply_active_policy([a])
    assert a.review_reasons == ["low OCR confidence",
                                pr.FLAG_REASONS["nondim_kind"]]


def test_run_config_records_the_active_rules(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ())
    pol = active_review_policy()
    assert pol["flag_rules"] == ["nondim_kind"]
    assert "drop_rules" not in pol


def test_no_active_rules_leaves_run_config_as_it_was(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ())
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ())
    assert active_review_policy() == {"review_low_conf": 0.8}
