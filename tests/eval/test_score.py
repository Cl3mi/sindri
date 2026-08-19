import pytest

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


def test_verbal_requirements_are_excluded_from_the_headline_metric():
    """1,086 of the 3,594 delivered rows are verbal requirements that were
    never ballooned. Charging them as missed callouts at the highest weight
    would let note text dominate the score instead of extraction quality."""
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Diameter", nominal="20",
                           kind="dimension"),
        GoldCharacteristic(balloon=2, position_pt=None,
                           char_type="SCHNITTKANTEN BLANK ZULAESSIG",
                           nominal="", kind="note"),
    ])
    empty = PredictionDump(doc_id="D", config=RunConfig(model_id="stub"),
                           scale=SCALE, page_rect=RECT,
                           result=ExtractionResult(characteristics=[]))
    s = score_doc(empty, gold, ReviewCostWeights(), MatchParams())
    assert s.n_gold == 1                      # the note is not scored
    assert s.counts == {"missed": 1}
    assert s.excluded_by_kind == 1
    assert s.review_cost == 10.0              # one miss, not two


def test_scoring_scope_can_include_notes_explicitly():
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Diameter", nominal="20", kind="dimension"),
        GoldCharacteristic(balloon=2, position_pt=None, char_type="NOTE TEXT",
                           nominal="", kind="note"),
    ])
    empty = PredictionDump(doc_id="D", config=RunConfig(model_id="stub"),
                           scale=SCALE, page_rect=RECT,
                           result=ExtractionResult(characteristics=[]))
    params = MatchParams(score_kinds=("dimension", "note"))
    s = score_doc(empty, gold, ReviewCostWeights(), params)
    assert s.n_gold == 2 and s.excluded_by_kind == 0


def test_flagged_correct_costs_flag_weight_only():
    d = _dump()
    d.result.characteristics[0].needs_review = True     # correct row, flagged
    s = score_doc(d, _gold(), ReviewCostWeights(), MatchParams())
    assert s.counts["flagged_correct"] == 1
    # cost = 10 + 5 + 2 + 1(pos3) + 1(pos1) = 19
    assert s.review_cost == 19.0


def _clamped_dump():
    """The same page rendered under the 80 MP budget. Boxes are in the CLAMPED
    render's pixels, which is what the pipeline actually produces after
    b266367 — so the geometry must still round-trip through dump.scale."""
    clamped_scale = 109 / 72.0                 # the 598 MP sheet's real dpi

    def box(x, y):
        return (clamped_scale * (x - 15), clamped_scale * (y - 5),
                clamped_scale * (x + 15), clamped_scale * (y + 5))

    chars = [Characteristic(pos=1, char_type="Diameter", nominal="20",
                            upper_tol="0,1", lower_tol="-0,1",
                            raw_text="Ø20 +0,1 -0,1", kind="dimension",
                            target_region=box(100, 100))]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=clamped_scale, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_doc_score_records_the_effective_render_dpi():
    s = score_doc(_dump(), _gold(), ReviewCostWeights(), MatchParams())
    assert s.effective_dpi == pytest.approx(300.0)


def test_doc_score_records_a_clamped_dpi_and_still_matches():
    """A clamped document is scored at reduced resolution, not scored wrongly:
    the box still lands on gold balloon 1."""
    s = score_doc(_clamped_dump(), _gold(), ReviewCostWeights(), MatchParams())
    assert s.effective_dpi == pytest.approx(109.0)
    assert s.counts.get("correct") == 1


def _kinded_dump():
    """Two in-scope dimensions plus two predictions of kinds the metric removed
    from gold — the asymmetry this test exists to measure."""
    chars = [
        Characteristic(pos=1, char_type="Diameter", nominal="20", upper_tol="0,1",
                       lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1", kind="dimension",
                       target_region=_pt_box(100, 100)),
        Characteristic(pos=2, char_type="Distance", nominal="5,5", raw_text="5,5",
                       kind="dimension", target_region=_pt_box(400, 200)),
        # a surface-finish callout and a note: correctly detected, but gold was
        # filtered to score_kinds=("dimension",), so neither can ever match
        Characteristic(pos=6, char_type="Surface", nominal="Ra1,6",
                       raw_text="Ra 1,6", kind="surface",
                       target_region=_pt_box(250, 650)),
        Characteristic(pos=7, char_type="Note", nominal="", raw_text="see note 3",
                       kind="note", target_region=_pt_box(600, 700)),
    ]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_doc_score_breaks_predictions_and_false_detections_down_by_kind():
    """score_doc filters GOLD to score_kinds but never filters PREDICTIONS, so
    every correctly-detected surface/note/gdt callout lands in false_detection.
    Record the breakdown so that inflation can be measured before anyone reads
    precision as a statement about the model."""
    s = score_doc(_kinded_dump(), _gold(), ReviewCostWeights(), MatchParams())

    assert s.pred_kinds == {"dimension": 2, "surface": 1, "note": 1}
    assert s.false_kinds == {"surface": 1, "note": 1}
    assert s.counts["false_detection"] == 2
    # Conservation: the breakdown must account for every prediction and every
    # false detection, or "N of 663" is quoting an unverified denominator.
    assert sum(s.pred_kinds.values()) == s.n_pred
    assert sum(s.false_kinds.values()) == s.counts["false_detection"]


def _crosstab_dump():
    """A gdt and a surface prediction sitting ON in-scope gold balloons.

    normalize._DIMENSION_WORDS lists 'symmetry', 'runout', 'surface',
    'oberflaeche' and friends, so char_type_kind() classifies a GD&T or
    surface-finish row as kind="dimension" — i.e. IN scope. The detector splits
    those same callouts into kind="gdt"/"surface". So a non-dimension prediction
    kind CAN legitimately match in-scope gold, and this fixture is that case."""
    chars = [
        Characteristic(pos=1, char_type="Diameter", nominal="20", upper_tol="0,1",
                       lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1", kind="dimension",
                       target_region=_pt_box(100, 100)),
        # gold balloon 2 is char_type "Distance" here, but the DETECTOR called
        # this box a gdt frame. It still matches: geometry decides the pair.
        Characteristic(pos=2, char_type="Symmetry", nominal="5,5", raw_text="5,5",
                       kind="gdt", target_region=_pt_box(400, 200)),
        # a theoretical (basic, untoleranced) box far from any gold: no gold
        # counterpart can exist for it, so it is a pure artefact
        Characteristic(pos=8, char_type="Theoretical", nominal="30",
                       raw_text="30", kind="theoretical",
                       target_region=_pt_box(250, 650)),
    ]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_doc_score_records_which_kinds_matched_in_scope_gold():
    """The measurement that decides the false_detection question: if a gdt or
    surface prediction matches in-scope gold, then filtering predictions to
    score_kinds=("dimension",) would convert that match into a MISS (w=10) from
    a false detection (w=2) — making the metric worse, not cleaner."""
    s = score_doc(_crosstab_dump(), _gold(), ReviewCostWeights(), MatchParams())

    # a gdt prediction matched an in-scope gold row: the whole point
    assert s.matched_kinds == {"dimension": 1, "gdt": 1}
    assert s.false_kinds == {"theoretical": 1}
    # Double conservation, per kind: every prediction of kind k either matched
    # or did not. This is what ties the new view to two numbers that already
    # exist instead of asking to be trusted.
    for k, total in s.pred_kinds.items():
        assert total == s.matched_kinds.get(k, 0) + s.false_kinds.get(k, 0)
    # and the matched total is the run's matched-gold count
    assert sum(s.matched_kinds.values()) == s.n_gold - s.counts["missed"]


def _contention_gold():
    """b1 and b2 sit 50pt apart — well inside the 145.9pt match gate (0.10 of
    the 1458.7pt page diagonal). b3 is far from everything. b4 has no recovered
    balloon position at all."""
    return GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Diameter", nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(150, 100),
                           char_type="Diameter", nominal="21"),
        GoldCharacteristic(balloon=3, position_pt=(900, 500),
                           char_type="Distance", nominal="8"),
        GoldCharacteristic(balloon=4, position_pt=None,
                           char_type="Distance", nominal="9"),
    ])


def _single_pred_dump():
    """One prediction, landing on b1 (its nominal agrees, so value_bonus wins it
    that pair). b2 is then missed even though a detection sits inside its gate."""
    chars = [Characteristic(pos=1, char_type="Diameter", nominal="20",
                            raw_text="Ø20", kind="dimension",
                            target_region=_pt_box(100, 100))]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_doc_score_diagnoses_why_each_gold_row_was_missed():
    """missed=310 carries 63% of the review cost, but "missed" does not say
    whether detection found nothing there or found something the matcher gave to
    a neighbour. Those route to different Rung 1 knobs -- tile size/overlap vs
    merge_adjacent/dedupe -- and a third bucket routes away from detection
    entirely, to gold balloon recovery."""
    s = score_doc(_single_pred_dump(), _contention_gold(), ReviewCostWeights(),
                  MatchParams())

    assert s.counts["missed"] == 3
    # b2: a detection sits inside its gate, but the matcher spent it on b1
    assert s.missed_contended == 1
    # b3: nothing detected anywhere near it
    assert s.missed_isolated == 1
    # b4: no gold position, so no detection knob can ever fix it
    assert s.missed_unlocated == 1
    # Conservation: the three buckets partition the misses exactly.
    assert (s.missed_contended + s.missed_isolated + s.missed_unlocated
            == s.counts["missed"])
