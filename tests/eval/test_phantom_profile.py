"""score --phantom-profile: where the phantoms sit among the values the product
DELIVERS (ships unflagged), sliced by signals a drop rule could use without
gold.

The client ranks precision over recall at any recall (2026-10-06), and the
delivered set on dev is 70 correct, 17 wrong and 80 phantoms. A drop rule
raises delivered precision exactly when the rows it removes are more untrue
than the set they come from, so the profile reports, per bucket, how many of
each the rule would remove. Measurement only; run on TRAIN only, so dev and
test stay unselected (docs/plans/2026-10-06-phantom-arm-design.md)."""
import json

import pytest

from app.eval import phantom_profile as pp
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1000.0, 1000.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _setup():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(300, 300),
                           char_type="Distance", nominal="20",
                           upper_tol="0,1", lower_tol="-0,1"),
        GoldCharacteristic(balloon=2, position_pt=(500, 300),
                           char_type="Distance", nominal="35",
                           upper_tol="0,1", lower_tol="-0,1"),
        GoldCharacteristic(balloon=3, position_pt=(300, 500),
                           char_type="Distance", nominal="12",
                           upper_tol="0,1", lower_tol="-0,1"),
    ])
    def c(pos, x, y, **kw):
        base = dict(pos=pos, kind="dimension", char_type="Distance",
                    upper_tol="0,1", lower_tol="-0,1", confidence=0.99,
                    target_region=_box(x, y))
        base.update(kw)
        return Characteristic(**base)
    chars = [
        # correct, delivered
        c(1, 300, 300, nominal="20", raw_text="20 ±0,1"),
        # wrong nominal, delivered -> escaped
        c(2, 500, 300, nominal="36", raw_text="36 ±0,1", confidence=0.93),
        # matched but flagged -> not delivered, not profiled
        c(3, 300, 500, nominal="12", raw_text="12 ±0,1", needs_review=True,
          review_reasons=["low OCR confidence"], confidence=0.5),
        # phantom, delivered: at the page edge, repeats a matched nominal
        c(4, 980, 500, nominal="20", raw_text="20 ±0,1", confidence=0.96),
        # phantom, flagged -> not delivered, not profiled
        c(5, 700, 700, nominal="5", raw_text="5", needs_review=True,
          review_reasons=["no tolerance read"]),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    return {"D": dump}, {"D": gold}


def _profile():
    dumps, golds = _setup()
    scores = [score_doc(dumps["D"], golds["D"], ReviewCostWeights(),
                        MatchParams())]
    return pp.phantom_profile(dumps, scores, ["D"])


def test_only_delivered_rows_are_profiled():
    t = _profile()["totals"]
    assert (t["correct"], t["escaped"], t["phantom"]) == (1, 1, 1)
    assert t["delivered"] == 3
    assert t["delivered_precision"] == pytest.approx(1 / 3, abs=1e-4)


def test_every_feature_reconciles_with_the_delivered_totals():
    r = _profile()
    assert r["reconciles"] is True
    for name, buckets in r["features"].items():
        for outcome in ("correct", "escaped", "phantom"):
            assert sum(b[outcome] for b in buckets.values()) == \
                r["totals"][outcome], (name, outcome)


def test_the_registered_features_are_all_present():
    assert set(_profile()["features"]) == {
        "kind", "confidence", "box_height", "box_width", "aspect",
        "char_type", "nominal_digits", "nominal_decimal", "raw_length",
        "nearest_other", "page_position", "repeated_nominal", "verifier_p"}


def test_confidence_bands():
    f = _profile()["features"]["confidence"]
    assert f[">=0.99"]["correct"] == 1
    assert f["0.9-0.95"]["escaped"] == 1
    assert f["0.95-0.98"]["phantom"] == 1


def test_page_position_puts_the_margin_phantom_on_the_edge():
    f = _profile()["features"]["page_position"]
    assert f["edge"] == {"correct": 0, "escaped": 0, "phantom": 1, "n": 1,
                         "untrue_share": 1.0}
    assert f["interior"]["correct"] == 1


def test_title_corner_wins_over_edge():
    assert pp.page_position(0.97, 0.97) == "title_corner"
    assert pp.page_position(0.97, 0.5) == "edge"
    assert pp.page_position(0.5, 0.02) == "edge"
    assert pp.page_position(0.5, 0.5) == "interior"


def test_repeated_nominal_counts_any_other_prediction_flagged_or_not():
    f = _profile()["features"]["repeated_nominal"]
    # pos 1 and pos 4 share "20"; pos 2 ("36") is unique
    assert f["yes"]["correct"] == 1 and f["yes"]["phantom"] == 1
    assert f["no"]["escaped"] == 1


def test_untrue_share_is_what_a_drop_would_remove():
    f = _profile()["features"]["repeated_nominal"]
    assert f["yes"]["untrue_share"] == pytest.approx(0.5)


@pytest.mark.parametrize("fn,value,bucket", [
    (pp.height_band, 30, "28-40"), (pp.height_band, 200, ">=200"),
    (pp.width_band, 59, "<60"), (pp.width_band, 240, ">=240"),
    (pp.aspect_band, 0.5, "<1"), (pp.aspect_band, 4, ">=4"),
    (pp.digits_band, "12,5", "3"), (pp.digits_band, "", "0"),
    (pp.digits_band, "12345", "4+"),
    (pp.raw_length_band, "20", "<4"), (pp.raw_length_band, "x" * 16, ">=16"),
    (pp.nearest_band, None, "alone"), (pp.nearest_band, 0.005, "<0.01"),
    (pp.nearest_band, 0.2, ">=0.1"),
    (pp.confidence_band, 0.8, "<0.9"), (pp.confidence_band, 0.99, ">=0.99"),
])
def test_band_edges(fn, value, bucket):
    assert fn(value) == bucket


def test_output_is_values_blind():
    blob = json.dumps(_profile())
    for value in ("±", '"20"', '"36"', "0,1"):
        assert value not in blob


def test_reconciliation_detects_a_lost_row(monkeypatch):
    """If a feature function ever dropped a row, the identity must fail
    loudly rather than publish a histogram short of the delivered set."""
    monkeypatch.setitem(pp.FEATURES, "kind", lambda row: None)
    assert _profile()["reconciles"] is False


def test_totals_equal_the_digests_delivered_counts():
    """Two independent paths to the same three numbers: the digest's
    auto_accept block and this profile. If they disagree, one of them is
    counting a different set than it claims."""
    from app.eval.report import aggregate, summarize
    dumps, golds = _setup()
    s = score_doc(dumps["D"], golds["D"], ReviewCostWeights(), MatchParams())
    aa = summarize(aggregate("r", RunConfig(), ReviewCostWeights(),
                             MatchParams(), [s]), lambda d: d)["auto_accept"]
    t = pp.phantom_profile(dumps, [s], ["D"])["totals"]
    assert (t["correct"], t["escaped"], t["phantom"]) == \
        (aa["correct"], aa["escaped"], aa["false_unflagged"])
    assert t["delivered_precision"] == aa["delivered_precision"]


def test_verifier_p_band_is_profiled_and_none_is_its_own_bucket():
    """Explains a verifier result: does P(yes) separate phantoms from correct
    values at all, or does the model say yes to everything?"""
    from app.eval import phantom_profile as pp
    assert pp.verifier_band(None) == "none"
    assert pp.verifier_band(0.3) == "<0.5"
    assert pp.verifier_band(0.95) == "0.9-0.99"
    assert pp.verifier_band(0.995) == ">=0.99"
    assert "verifier_p" in pp.FEATURES
