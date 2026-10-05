"""Why each false detection is false. missed_diagnosis exists for misses;
nothing said whether 346 false detections are fragments of a match, a callout
read twice, surplus near gold, or text far from anything ballooned -- and each
routes to a different fix. The categories partition false_detection exactly."""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.report import aggregate, summarize
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _score():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(500, 400), nominal="7"),
    ])
    chars = [
        Characteristic(pos=1, kind="dimension", nominal="20", raw_text="20",
                       target_region=_box(100, 100)),          # matched
        Characteristic(pos=2, kind="dimension", nominal="2", raw_text="2",
                       target_region=_box(102, 100, 5, 3)),     # inside_matched
        Characteristic(pos=3, kind="note", raw_text="",
                       target_region=_box(900, 700)),           # empty_read
        Characteristic(pos=4, kind="dimension", nominal="20", raw_text="20",
                       target_region=_box(160, 100)),           # same_value
        Characteristic(pos=5, kind="theoretical", nominal="99", raw_text="99",
                       target_region=_box(1100, 50)),           # far_numeric
        Characteristic(pos=6, kind="dimension", raw_text="A-A",
                       target_region=_box(50, 800)),            # far_other
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    return score_doc(dump, gold, ReviewCostWeights(), MatchParams())


def test_each_false_detection_gets_exactly_one_category():
    s = _score()
    assert s.false_diagnosis == {"inside_matched": 1, "empty_read": 1,
                                 "same_value_as_matched": 1,
                                 "far_numeric": 1, "far_other": 1}
    assert sum(s.false_diagnosis.values()) == s.counts["false_detection"]
    assert s.false_diagnosis_by_kind["theoretical"] == {"far_numeric": 1}


def test_near_gold_splits_by_whether_that_gold_was_matched():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20")])
    chars = [
        Characteristic(pos=1, kind="dimension", nominal="20", raw_text="20",
                       target_region=_box(100, 100)),
        Characteristic(pos=2, kind="dimension", nominal="8", raw_text="8",
                       target_region=_box(100, 140)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    assert s.false_diagnosis == {"near_matched_gold": 1}


def test_digest_sums_and_reconciles():
    s = _score()
    r = aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [s])
    fd = summarize(r, lambda d: "x")["false_diagnosis"]
    assert fd["total"] == r.taxonomy["false_detection"]
    assert fd["not_measured_docs"] == 0
    assert fd["categories"]["far_numeric"] == 1


def test_an_old_report_says_not_measured_rather_than_zero():
    from app.eval.models import DocScore
    old = DocScore(doc_id="D", gold_hash="h", n_gold=1, n_pred=1,
                   counts={"false_detection": 3})
    r = aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [old])
    fd = summarize(r, lambda d: "x")["false_diagnosis"]
    assert fd["not_measured_docs"] == 1
    assert fd["total"] == 0
