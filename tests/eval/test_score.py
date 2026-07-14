from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _pt_box(x, y):
    """A 30x10pt box centered at (x, y), expressed in render pixels."""
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _gold():
    return GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), char_type="Diameter",
                           nominal="20", upper_tol="0,1", lower_tol="-0,1"),
        GoldCharacteristic(balloon=2, position_pt=(400, 200), char_type="Distance",
                           nominal="5,5"),
        GoldCharacteristic(balloon=3, position_pt=(700, 300), char_type="Distance",
                           nominal="8"),
        GoldCharacteristic(balloon=4, position_pt=(900, 500), char_type="Radius",
                           nominal="2"),
    ])


def _dump():
    chars = [
        # pos 1: correct, unflagged -> "correct"
        Characteristic(pos=1, char_type="Diameter", nominal="20",
                       upper_tol="0,1", lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1",
                       target_region=_pt_box(100, 100)),
        # pos 2: wrong nominal, NOT flagged -> escaped_error, cause misread
        Characteristic(pos=2, char_type="Distance", nominal="6,5",
                       raw_text="6,5", target_region=_pt_box(400, 200)),
        # pos 3: wrong nominal, flagged -> flagged_error
        Characteristic(pos=3, char_type="Distance", nominal="9",
                       raw_text="9", needs_review=True,
                       review_reasons=["low OCR confidence"],
                       target_region=_pt_box(700, 300)),
        # gold 4 has no prediction -> missed
        # pos 5: phantom far from all gold -> false detection
        Characteristic(pos=5, char_type="Distance", nominal="99",
                       raw_text="99", target_region=_pt_box(200, 700)),
    ]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_taxonomy_counts_and_review_cost():
    s = score_doc(_dump(), _gold(), ReviewCostWeights(), MatchParams())
    assert s.counts == {"correct": 1, "escaped_error": 1, "flagged_error": 1,
                        "missed": 1, "false_detection": 1}
    # cost = 10*1 missed + 5*1 escaped + 2*1 false + 1*1 flagged = 18
    assert s.review_cost == 18.0
    assert s.recall == 0.75 and s.n_gold == 4 and s.n_pred == 4
    assert s.missed_balloons == [4]
    assert s.false_positions == [5]
    assert s.gold_hash == _gold().gold_hash()


def test_field_errors_and_cause_are_recorded():
    s = score_doc(_dump(), _gold(), ReviewCostWeights(), MatchParams())
    pair2 = next(p for p in s.pairs if p.pred_pos == 2)
    assert not pair2.fields_correct
    assert any("nominal" in e for e in pair2.field_errors)
    assert "cause:misread" in pair2.notes


def test_misparse_cause_when_raw_text_contains_gold_value():
    d = _dump()
    # read captured the right glyphs ('5,5' is in raw) but fields are wrong
    d.result.characteristics[1].raw_text = "5,5 +0,1"
    d.result.characteristics[1].nominal = "51"
    s = score_doc(d, _gold(), ReviewCostWeights(), MatchParams())
    pair2 = next(p for p in s.pairs if p.pred_pos == 2)
    assert "cause:misparse" in pair2.notes


def test_flagged_correct_costs_flag_weight_only():
    d = _dump()
    d.result.characteristics[0].needs_review = True     # correct row, flagged
    s = score_doc(d, _gold(), ReviewCostWeights(), MatchParams())
    assert s.counts["flagged_correct"] == 1
    # cost = 10 + 5 + 2 + 1(pos3) + 1(pos1) = 19
    assert s.review_cost == 19.0
