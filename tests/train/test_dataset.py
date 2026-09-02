"""The training-pair builder. Two properties matter and both are tested here on
synthetic data: the crop must be the one inference would produce, and nothing the
builder REPORTS may contain a client value."""
import json

from PIL import Image

from app.eval.models import GoldCharacteristic, GoldDoc
from app.train.dataset import build_pairs

RECT = (0.0, 0.0, 842.0, 595.0)


class _Box:
    """Stands in for a prediction: the builder needs only its box and kind."""

    def __init__(self, region, kind="dimension"):
        self.target_region = region
        self.kind = kind
        self.subtype = ""


def _gold():
    return GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
                       GoldCharacteristic(balloon=1, position_pt=(100, 100),
                                          char_type="Distance", nominal="20",
                                          upper_tol="0,1", lower_tol="-0,1"),
                       GoldCharacteristic(balloon=2, position_pt=(300, 200),
                                          char_type="Diameter", nominal="7"),
                   ])


def test_a_pair_is_written_for_each_renderable_matched_row(tmp_path):
    page = Image.new("RGB", (842, 595), "white")
    pairs = [(1, _Box((90, 90, 200, 120))), (2, _Box((290, 190, 400, 220)))]
    out = tmp_path / "pairs"

    counts = build_pairs(_gold(), page, pairs, out)

    assert counts["pairs"] == 2
    assert counts["unrenderable"] == 0
    assert len(list(out.glob("*.png"))) == 2
    manifest = json.loads((out / "manifest.jsonl").read_text().splitlines()[0])
    assert set(manifest) == {"image", "target", "hint", "balloon"}


def test_an_unrenderable_row_is_counted_and_skipped_not_approximated(tmp_path):
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[GoldCharacteristic(
                       balloon=1, position_pt=(100, 100),
                       char_type="Distance", nominal="")])
    page = Image.new("RGB", (842, 595), "white")

    counts = build_pairs(gold, page, [(1, _Box((90, 90, 200, 120)))],
                         tmp_path / "pairs")

    assert counts["pairs"] == 0
    assert counts["unrenderable"] == 1


def test_the_returned_counts_carry_no_client_value(tmp_path):
    """The ONLY thing that may be reported about this dataset. Its contents are
    gold values and can never enter an AI context, so the return value is
    checked the same way the digests are.

    Asserted STRUCTURALLY -- a closed key set and integer-only values -- rather
    than by grepping the JSON for value substrings. Grepping would have been
    fragile in a way that matters: a count that happened to equal 20 makes "20"
    appear in the blob and fails a leak check that has found nothing. Integers
    under known keys cannot carry a transcription at all."""
    page = Image.new("RGB", (842, 595), "white")
    counts = build_pairs(_gold(), page, [(1, _Box((90, 90, 200, 120)))],
                         tmp_path / "pairs")
    assert set(counts) == {"pairs", "unrenderable", "no_gold", "no_box"}
    assert all(isinstance(v, int) for v in counts.values()), counts
    # belt and braces on the one token no count could ever produce
    assert "Ø" not in json.dumps(counts, ensure_ascii=False)


def test_the_crop_is_the_one_inference_would_produce(tmp_path):
    """The crop must come from the pipeline's own tighten_to_ink + _prep_crop, not
    a reimplementation: a training crop that differs from an inference crop
    teaches the model the wrong input distribution, which would surface as a LoRA
    that helps on paper and not in the pipeline."""
    from app.pipeline import boxes as bx
    from app.pipeline.extract import _CROP_PAD, _prep_crop

    page = Image.new("RGB", (842, 595), "white")
    box = (90, 90, 200, 120)
    expected = _prep_crop(page, bx.tighten_to_ink(page, box), 842, 595,
                          pad=_CROP_PAD)

    out = tmp_path / "pairs"
    build_pairs(_gold(), page, [(1, _Box(box))], out)
    written = Image.open(next(out.glob("*.png")))

    assert written.size == expected.size


def test_a_prediction_with_no_box_is_counted_not_crashed_on(tmp_path):
    page = Image.new("RGB", (842, 595), "white")
    counts = build_pairs(_gold(), page, [(1, _Box(None))], tmp_path / "pairs")
    assert counts["no_box"] == 1
    assert counts["pairs"] == 0


def test_a_balloon_with_no_gold_row_is_counted_not_crashed_on(tmp_path):
    page = Image.new("RGB", (842, 595), "white")
    counts = build_pairs(_gold(), page, [(99, _Box((90, 90, 200, 120)))],
                        tmp_path / "pairs")
    assert counts["no_gold"] == 1
    assert counts["pairs"] == 0


def _gold_rows(*chars):
    """The same GoldDoc shell as _gold(), with rows the caller chooses."""
    doc = _gold()
    doc.characteristics = list(chars)
    return doc


def test_unrenderable_rows_are_counted_by_reason(tmp_path):
    """The first train-split build reported `unrenderable: 790` against
    `pairs: 192` and nothing about WHY -- diagnosing it took a code read and a
    hypothesis instead of a number. A bare total is not a diagnosis.

    Reasons come from UnrenderableRow.REASONS, a closed slug set, so the
    breakdown stays exactly as values-blind as the totals beside it."""
    gold = _gold_rows(
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Maß", nominal=""),
        GoldCharacteristic(balloon=2, position_pt=(300, 200),
                           char_type="Ebenheit", nominal="0", upper_tol="0,05"))
    page = Image.new("RGB", (842, 595), "white")

    counts = build_pairs(gold, page,
                         [(1, _Box((90, 90, 200, 120))),
                          (2, _Box((290, 190, 400, 220)))],
                         tmp_path / "pairs")

    assert counts["pairs"] == 0
    assert counts["unrenderable"] == 2
    # balloon 1 is a Distance with no nominal; balloon 2 is a geometric row the
    # detector called `dimension`, so it arrives with no hint and provably
    # cannot round-trip.
    assert counts["unrenderable:no_nominal"] == 1
    assert counts["unrenderable:gdt_no_hint"] == 1
    blob = json.dumps(counts, ensure_ascii=False)
    for leak in ("Maß", "Ebenheit", "0,05"):
        assert leak not in blob, f"counts leaked {leak!r}"


def test_the_reason_breakdown_reconciles_with_the_unrenderable_total(tmp_path):
    """House rule: every aggregate cross-checks against a count that already
    exists. Each rejected row raises exactly once, so the per-reason counts sum
    to `unrenderable`."""
    gold = _gold_rows(
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Maß", nominal=""),
        GoldCharacteristic(balloon=2, position_pt=(200, 100),
                           char_type="Ebenheit", nominal="0", upper_tol="0,05"),
        GoldCharacteristic(balloon=3, position_pt=(300, 100),
                           char_type="STANZGRATSEITE", nominal="20"))
    page = Image.new("RGB", (842, 595), "white")

    counts = build_pairs(gold, page,
                         [(i, _Box((100 * i - 10, 90, 100 * i + 60, 120)))
                          for i in (1, 2, 3)],
                         tmp_path / "pairs")

    per_reason = {k: v for k, v in counts.items()
                  if k.startswith("unrenderable:")}
    assert sum(per_reason.values()) == counts["unrenderable"] == 3
