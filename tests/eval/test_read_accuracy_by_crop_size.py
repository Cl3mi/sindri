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


# --- which KIND of box sits in each band ------------------------------------
#
# Two bands are unexplained and both hypotheses are about kind:
#   >=200 px reads at 0.586 and is flat to three decimals at every pad --
#         context-insensitive, which is what a `gdt` or `note` box on its own
#         prompt (_GDT_PROMPT, _NOTES_PROMPT) would look like, since those do
#         not ask for one line of dimension text at all.
#   80-120 px reads at 0.242, the worst on the page, and padding cannot touch
#         it -- if it is overwhelmingly `dimension`, the fault is in the
#         one-line read of a two-line box.
# Counts per band settle both. Per-cell accuracy would be too sparse to read.

def _kinded(*rows):
    """(kind, box_height_px) per row; values always read correctly, because
    this aggregate is about composition, not accuracy."""
    gold, preds = [], []
    for i, (kind, h_px) in enumerate(rows):
        y = 100 + i * 100
        gold.append(GoldCharacteristic(balloon=i + 1, position_pt=(200.0, float(y)),
                                       char_type="Distance", nominal=str(10 + i)))
        cx, cy = SCALE * 200, SCALE * y
        preds.append(Characteristic(
            pos=i + 1, char_type="Distance", nominal=str(10 + i),
            raw_text=str(10 + i), kind=kind,
            target_region=(cx - 50, cy - h_px / 2, cx + 50, cy + h_px / 2)))
    gold_doc = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                       characteristics=gold)
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=preds))
    s = score_doc(dump, gold_doc, ReviewCostWeights(), MatchParams())
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [s])
    return summarize(report, lambda d: "hashed")


def test_each_band_reports_what_kinds_of_box_are_in_it():
    d = _kinded(("dimension", 60.0), ("gdt", 250.0), ("note", 250.0))
    by = {b["range"]: b for b in d["read_accuracy_by_crop_height"]["buckets"]}
    assert by["40-80"]["kinds"] == {"dimension": 1}
    assert by[">=200"]["kinds"] == {"gdt": 1, "note": 1}


def test_the_kind_counts_reconcile_against_the_band_count():
    """Section 4 of the working notes: an aggregate that cannot be cross-checked
    is a number asking to be trusted. A kind dropped here would understate
    exactly the composition the band is being read for."""
    d = _kinded(("dimension", 90.0), ("dimension", 100.0), ("gdt", 110.0))
    for b in d["read_accuracy_by_crop_height"]["buckets"]:
        assert sum(b["kinds"].values()) == b["n"], b


def test_an_empty_band_has_no_kinds():
    d = _kinded(("dimension", 60.0))
    by = {b["range"]: b for b in d["read_accuracy_by_crop_height"]["buckets"]}
    assert by[">=200"]["kinds"] == {}


# --- how well does each KIND read, and is fixing it worth anything? ---------
#
# r3-tallpad taught the question that has to travel with any read-quality
# aggregate: flagged_error and flagged_correct BOTH cost 1, so fixing a read on
# an already-flagged row saves NOTHING. Accuracy per kind alone would therefore
# route work to a kind whose errors are all flagged, which is exactly the
# mistake that arm made -- it fixed six flagged rows for zero saving.
#
# So this reports the PAYABLE part too: escaped_error per kind is what a fix in
# that kind could actually recover.

def _rows(*rows):
    """(kind, correct, flagged) per row."""
    gold, preds = [], []
    for i, (kind, ok, flagged) in enumerate(rows):
        y = 100 + i * 100
        gold.append(GoldCharacteristic(balloon=i + 1, position_pt=(200.0, float(y)),
                                       char_type="Distance", nominal=str(10 + i)))
        cx, cy = SCALE * 200, SCALE * y
        preds.append(Characteristic(
            pos=i + 1, char_type="Distance",
            nominal=str(10 + i) if ok else "999",
            raw_text=str(10 + i) if ok else "999", kind=kind,
            needs_review=flagged,
            review_reasons=["low OCR confidence"] if flagged else [],
            target_region=(cx - 50, cy - 30, cx + 50, cy + 30)))
    gold_doc = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                       characteristics=gold)
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=preds))
    s = score_doc(dump, gold_doc, ReviewCostWeights(), MatchParams())
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [s])
    return summarize(report, lambda d: "hashed")


def test_each_kind_reports_its_read_accuracy():
    """The number nothing has: matched_by_pred_kind gives counts only, and the
    worst crop-height band turned out to be the one where non-dimension kinds
    concentrate."""
    d = _rows(("dimension", True, False), ("dimension", False, False),
              ("gdt", False, False))
    by = {k["kind"]: k for k in d["read_accuracy_by_kind"]["kinds"]}
    assert by["dimension"]["n"] == 2 and by["dimension"]["field_acc"] == 0.5
    assert by["gdt"]["n"] == 1 and by["gdt"]["field_acc"] == 0.0


def test_it_reports_the_PAYABLE_part_not_just_accuracy():
    """Two kinds, equally wrong, worth completely different amounts: one kind's
    errors are already flagged and cost 1 whether fixed or not, the other's are
    silent and cost 5. Accuracy alone cannot tell them apart, and r3-tallpad
    lost precisely by fixing the first sort."""
    d = _rows(("gdt", False, True), ("surface", False, False))
    by = {k["kind"]: k for k in d["read_accuracy_by_kind"]["kinds"]}
    assert by["gdt"]["field_acc"] == 0.0 and by["gdt"]["escaped_error"] == 0
    assert by["surface"]["field_acc"] == 0.0 and by["surface"]["escaped_error"] == 1


def test_the_kinds_reconcile_against_the_matched_count():
    d = _rows(("dimension", True, False), ("gdt", False, True),
              ("note", False, False))
    cov = d["read_accuracy_by_kind"]
    tx = d["taxonomy"]
    matched = (tx.get("correct", 0) + tx.get("flagged_correct", 0)
               + tx.get("flagged_error", 0) + tx.get("escaped_error", 0))
    assert sum(k["n"] for k in cov["kinds"]) + cov["not_measured"] == matched


# --- is a kind wrong for ONE reason, or for several? ------------------------
#
# The `theoretical` arm (2026-09-22) was built on "wrong by construction": every
# boxed row carried char_type THEORETICAL, which gold never holds, so the fix
# looked like it would free the whole bucket. It freed nothing -- the rows were
# wrong in their values TOO, and a correctness fix pays only when it is the
# LAST fault on the row. field_acc 0.0000 cannot tell "wrong for one reason"
# from "wrong for two", so the arm had to be priced to find out. This is the
# aggregate that would have answered it before the edit was written.

def _typed(*rows):
    """(kind, pred_char_type, nominal_ok) per row, against Distance / 10+i gold."""
    gold, preds = [], []
    for i, (kind, ctype, ok) in enumerate(rows):
        y = 100 + i * 100
        gold.append(GoldCharacteristic(balloon=i + 1, position_pt=(200.0, float(y)),
                                       char_type="Distance", nominal=str(10 + i)))
        cx, cy = SCALE * 200, SCALE * y
        nom = str(10 + i) if ok else "999"
        preds.append(Characteristic(
            pos=i + 1, char_type=ctype, nominal=nom, raw_text=nom, kind=kind,
            target_region=(cx - 50, cy - 30, cx + 50, cy + 30)))
    gold_doc = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                       characteristics=gold)
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=preds))
    s = score_doc(dump, gold_doc, ReviewCostWeights(), MatchParams())
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [s])
    return summarize(report, lambda d: "hashed")


def test_each_kind_says_WHICH_fields_its_wrong_rows_got_wrong():
    """Two rows at field_acc 0.0, and only one of them a char_type fix could
    free. That difference is the whole of the theoretical arm's result."""
    d = _typed(("theoretical", "Theoretical", True),
               ("theoretical", "Theoretical", False))
    by = {k["kind"]: k for k in d["read_accuracy_by_kind"]["kinds"]}
    assert by["theoretical"]["field_acc"] == 0.0
    assert by["theoretical"]["wrong_fields"] == {
        "fields:char_type": 1, "fields:char_type+nominal": 1}


def test_wrong_fields_reconcile_against_the_wrong_rows_of_each_kind():
    """Every wrong row lands in exactly one signature, so a kind's signatures
    sum to its wrong rows -- the cross-check CLAUDE.md §4 requires."""
    d = _typed(("dimension", "Distance", True), ("dimension", "Distance", False),
               ("theoretical", "Theoretical", False), ("gdt", "Flatness", True))
    for k in d["read_accuracy_by_kind"]["kinds"]:
        wrong = k["n"] - round(k["field_acc"] * k["n"])
        assert sum(k["wrong_fields"].values()) == wrong, k["kind"]
    by = {k["kind"]: k for k in d["read_accuracy_by_kind"]["kinds"]}
    assert by["dimension"]["wrong_fields"] == {"fields:nominal": 1}


def test_wrong_field_keys_are_namespaced_so_digests_stay_committable():
    """The pre-commit hook refuses a staged .json with a quoted bare tolerance
    field name. A kind whose only fault is a single tolerance field would emit
    exactly that key if the space were not prefixed."""
    d = _typed(("theoretical", "Theoretical", False))
    for k in d["read_accuracy_by_kind"]["kinds"]:
        assert all(key.startswith("fields:") for key in k["wrong_fields"])


# --- for the rows a char_type fix WOULD free: which types were confused? -----
#
# `gdt` has 8 rows wrong in char_type ONLY -- the one bucket that passes the
# last-fault test. Whether the fix lives in the synonym map (gold names the
# type in words the scorer does not know) or the parser (`_gdt_type` defaults
# to Flatness when it recognises no symbol) depends on WHICH pair of types each
# row confused. The global char_type_confusion mixes those 8 with every
# multi-fault row, so it cannot answer that.

def test_each_kind_reports_the_confusion_of_its_char_type_ONLY_rows():
    d = _typed(("gdt", "Flatness", True), ("gdt", "Flatness", True),
               ("gdt", "Position", True))
    by = {k["kind"]: k for k in d["read_accuracy_by_kind"]["kinds"]}
    assert by["gdt"]["char_type_only_confusion"] == {
        "chartype:Distance->Flatness": 2, "chartype:Distance->Position": 1}


def test_rows_with_a_second_fault_are_left_out_of_it():
    """A row also wrong in nominal cannot be freed by a char_type fix, so it
    must not dilute the confusion that routes that fix."""
    d = _typed(("gdt", "Flatness", True), ("gdt", "Position", False))
    by = {k["kind"]: k for k in d["read_accuracy_by_kind"]["kinds"]}
    assert by["gdt"]["char_type_only_confusion"] == {
        "chartype:Distance->Flatness": 1}


def test_it_reconciles_against_the_char_type_only_signature():
    d = _typed(("gdt", "Flatness", True), ("gdt", "Flatness", False),
               ("dimension", "Diameter", True), ("dimension", "Distance", False))
    for k in d["read_accuracy_by_kind"]["kinds"]:
        assert (sum(k["char_type_only_confusion"].values())
                == k["wrong_fields"].get("fields:char_type", 0)), k["kind"]
