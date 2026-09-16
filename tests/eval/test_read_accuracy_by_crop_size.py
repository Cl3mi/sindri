"""Does a SHORT crop read worse than a tall one?

Nothing in the harness has ever related read accuracy to crop SIZE, and that
gap now blocks a decision. `_CROP_PAD` is measured and shipped, but the other
two crop knobs -- `_MIN_CROP_H` (40) and `_MAX_UPSCALE` (3.0) -- are a different
mechanism: not how much CONTEXT the reader gets, but how many PIXELS the callout
itself occupies. Qwen rejects any crop with a side below its patch factor of 28,
and `_prep_crop` upscales short crops toward 40 precisely because of that.

`CLAUDE.md` §2 forbids proposing a knob without a bucket that predicts which way
it moves, and §4 now adds that the bucket must be one the treatment can actually
move -- the lesson from the crop dose, whose registered damage counter
(`misplaced_matches`) was flat at 44 -> 42 before it was ever registered. There
is no such bucket for crop size, so this builds it.

**It is GPU-free and it can close the family for nothing.** If short crops are
NOT over-represented among wrong rows, raising `_MIN_CROP_H` cannot help and the
whole resolution family is refuted without spending a night on it.

Height is in RENDER PIXELS, which is what `_prep_crop` and both knobs operate
in, and what the vision encoder actually sees. Points would be the wrong unit:
a render-clamped sheet draws the same callout at fewer pixels, and that
difference is exactly the effect under test.
"""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.report import aggregate, summarize
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _case(*rows):
    """(gold_nominal, read_nominal, box_height_px) per row, laid out down the
    page so every pair matches geometrically and nothing contends."""
    gold, preds = [], []
    for i, (g_nom, p_nom, h_px) in enumerate(rows):
        y = 100 + i * 100
        gold.append(GoldCharacteristic(balloon=i + 1, position_pt=(200.0, float(y)),
                                       char_type="Distance", nominal=g_nom))
        cx, cy = SCALE * 200, SCALE * y
        preds.append(Characteristic(
            pos=i + 1, char_type="Distance", nominal=p_nom, raw_text=p_nom,
            target_region=(cx - 50, cy - h_px / 2, cx + 50, cy + h_px / 2)))
    return (GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                    characteristics=gold),
            PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                           scale=SCALE, page_rect=RECT,
                           result=ExtractionResult(characteristics=preds)))


def _digest(*rows):
    gold, dump = _case(*rows)
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [s])
    return summarize(report, lambda d: "hashed")


def test_pairs_are_bucketed_by_the_height_the_reader_saw():
    """Boundaries are meaningful, not round: 28 is Qwen's patch floor, 40 is
    _MIN_CROP_H, and the tall ones are roughly 2, 3 and 5+ lines of callout
    text at 300 dpi."""
    d = _digest(("20", "20", 20.0), ("30", "30", 34.0),
                ("40", "40", 60.0), ("50", "50", 200.0))
    buckets = d["read_accuracy_by_crop_height"]["buckets"]
    assert {b["range"] for b in buckets} == {"<28", "28-40", "40-80",
                                             "80-120", "120-200", ">=200"}
    assert {b["range"]: b["n"] for b in buckets} == {
        "<28": 1, "28-40": 1, "40-80": 1,
        "80-120": 0, "120-200": 0, ">=200": 1}


def test_it_reports_field_accuracy_PER_bucket():
    """The whole point: the number that says whether short crops read worse.
    One short row wrong and one tall row right is the minimal shape of the
    effect the crop-resolution knobs would target."""
    d = _digest(("20", "99", 20.0), ("30", "30", 200.0))
    by = {b["range"]: b for b in d["read_accuracy_by_crop_height"]["buckets"]}
    assert by["<28"]["field_acc"] == 0.0
    assert by[">=200"]["field_acc"] == 1.0
    assert by["80-120"]["field_acc"] is None, "an empty bucket has no accuracy"


def test_the_buckets_reconcile_against_the_matched_count():
    """CLAUDE.md §4: every aggregate must cross-check against a count that
    already exists. A bucketing that silently dropped a pair would understate
    exactly the bucket it is being built to measure."""
    d = _digest(("20", "20", 10.0), ("30", "99", 34.0), ("40", "40", 500.0))
    cov = d["read_accuracy_by_crop_height"]
    tx = d["taxonomy"]
    matched = (tx.get("correct", 0) + tx.get("flagged_correct", 0)
               + tx.get("flagged_error", 0) + tx.get("escaped_error", 0))
    assert sum(b["n"] for b in cov["buckets"]) + cov["not_measured"] == matched


def test_a_pair_without_a_recorded_height_is_not_measured_rather_than_zero():
    """Reports written before this field carry None, and a height of 0 would
    put every historical pair in the `<28` bucket -- inventing the exact signal
    this aggregate exists to detect."""
    gold, dump = _case(("20", "20", 60.0))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    for p in s.pairs:
        p.pred_box_h_px = None
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [s])
    cov = summarize(report, lambda d: "hashed")["read_accuracy_by_crop_height"]
    assert cov["not_measured"] == 1
    assert sum(b["n"] for b in cov["buckets"]) == 0


# --- placing the knee -------------------------------------------------------
#
# The first cut was aligned to _MIN_CROP_H and the patch factor, because it was
# built to decide the crop-RESOLUTION knobs. Those are now refuted (4% reach,
# and the effect runs the wrong way), so the boundaries earn their keep a
# different way: by line counts, since >=80 px at 300 dpi is more than one line
# and that bucket is 56% of matched rows at the worst accuracy on the page.
#
# The old boundaries are KEPT as a subset so every number already published
# stays reproducible -- the old `>=80` is the sum of the three tall buckets.

def test_the_tall_bucket_is_split_to_find_the_knee():
    """Whether accuracy keeps falling with height decides the SHAPE of a
    height-dependent pad: still falling means the pad should scale with height,
    flattening means one threshold is enough."""
    d = _digest(("10", "10", 90.0), ("20", "20", 150.0), ("30", "30", 400.0))
    ranges = [b["range"] for b in d["read_accuracy_by_crop_height"]["buckets"]]
    assert "80-120" in ranges and "120-200" in ranges and ">=200" in ranges


def test_the_published_boundaries_remain_recoverable():
    """The refutation was published against `<28` / `28-40` / `40-80` / `>=80`.
    Re-cutting must keep those recoverable by summation, or the numbers in
    docs/plans/2026-09-16-crop-height-diagnostic.md stop being checkable."""
    d = _digest(("10", "10", 20.0), ("20", "20", 34.0), ("30", "30", 60.0),
                ("40", "40", 90.0), ("50", "50", 150.0), ("60", "60", 400.0))
    by = {b["range"]: b["n"] for b in d["read_accuracy_by_crop_height"]["buckets"]}
    assert by["<28"] == 1 and by["28-40"] == 1 and by["40-80"] == 1
    assert by["80-120"] + by["120-200"] + by[">=200"] == 3   # the old ">=80"
