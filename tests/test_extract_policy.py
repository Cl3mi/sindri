"""The pipeline applies the ACTIVE rules through the same functions the
counterfactual priced them with, and records them in RunConfig."""
from app.models import Characteristic
from app.pipeline import policy_rules as pr
from app.pipeline.detect import Detection
from app.pipeline.extract import _apply_active_policy
from app.pipeline.review import active_review_policy
from tests.conftest import StubVLMBackend


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


def test_extract_applies_active_drop_before_numbering_and_excludes_the_dropped_box(
        sample_pdf, tmp_path, monkeypatch):
    """extract() runs _apply_active_policy BEFORE number_characteristics
    (app/pipeline/extract.py), and the loose_text exclusion it hands
    title_block.loose_text must still cover a DROPPED box's region -- or that
    box's text resurfaces as a title field even though the row itself never
    reaches the caller. Exercised at the extract() level, not just on
    _apply_active_policy directly, so a regression in extract's WIRING
    (drop before numbering, pre-drop regions into exclude) fails here even if
    _apply_active_policy in isolation still behaves."""
    import app.pipeline.boxes as boxes_mod
    import app.pipeline.extract as extract_mod
    import app.pipeline.title_block as tb_mod

    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: None)
    monkeypatch.setattr("app.pipeline.marks_block.locate_marks_block",
                        lambda image: None)
    monkeypatch.setattr("app.pipeline.title_block.locate_title_block",
                        lambda image: None)

    # A big dimension box wholly containing a small one, both reading the
    # same value -- exactly what contained_duplicate (in ACTIVE_DROP_RULES by
    # default) drops. inner_box == box on both so extract skips
    # boxes.tighten_to_ink, which would otherwise perturb these coordinates.
    big = Detection(box=(20, 20, 220, 120), kind="dimension", conf=0.9,
                    inner_box=(20, 20, 220, 120))
    small = Detection(box=(40, 40, 90, 90), kind="dimension", conf=0.9,
                      inner_box=(40, 40, 90, 90))
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [big, small])

    captured = {}

    def fake_loose_text(image, backend, exclude_boxes):
        captured["exclude"] = list(exclude_boxes)
        return []
    monkeypatch.setattr(tb_mod, "loose_text", fake_loose_text)

    backend = StubVLMBackend(detections=[], text="20")
    result = extract_mod.extract(sample_pdf, work_dir=tmp_path, dpi=300,
                                 backend=backend)
    rows = result.characteristics

    # One row survives the drop (equal confidence keeps the LARGER box: see
    # policy_rules._contained_duplicate's confidence-tie comment).
    assert len(rows) == 1
    assert rows[0].target_region == (20, 20, 220, 120)
    # pos is assigned by number_characteristics AFTER the drop, so it is
    # contiguous over the SURVIVING rows only, not the pre-drop count.
    assert sorted(r.pos for r in rows) == list(range(1, len(rows) + 1))
    # The dropped box's pre-drop region must still be excluded from
    # loose_text, or its text would resurface as an unlabelled title field.
    assert (40, 40, 90, 90) in captured["exclude"]
