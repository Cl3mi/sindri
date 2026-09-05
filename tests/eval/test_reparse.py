"""The re-parse dry run. Its value depends entirely on the identity gate: if the
hint reconstruction drifts from extract.py, `identical` stops covering every
pair and the numbers would silently attribute extract's behaviour to the
parser."""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.reparse import _HINTS, reparse_report
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _pt_box(x, y):
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _case(pred_kwargs, gold_kwargs):
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[GoldCharacteristic(
                       balloon=1, position_pt=(100, 100), **gold_kwargs)])
    dump = PredictionDump(
        doc_id="D", config=RunConfig(model_id="stub", dpi=300), scale=SCALE,
        page_rect=RECT, result=ExtractionResult(characteristics=[
            Characteristic(pos=1, target_region=_pt_box(100, 100),
                           **pred_kwargs)]))
    score = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    return reparse_report({"D": dump}, {"D": gold}, [score])


def test_unmodified_parser_reproduces_every_stored_field():
    """The gate. Stored fields came from parse_value at predict time, so
    re-parsing must reproduce them exactly -- otherwise the hint reconstruction
    is wrong and every other number here is measuring that instead."""
    r = _case(dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1"))
    assert r["n_pairs"] == 1
    assert r["identical"] == r["n_pairs"]
    assert r["would_fix"] == 0 and r["would_break"] == 0


def test_would_fix_counts_a_row_a_better_parse_would_correct():
    """A stored parse that lost the value the raw text plainly contains: this is
    the bucket a candidate parser change is trying to grow."""
    r = _case(dict(char_type="Diameter", nominal="2", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1"))
    assert r["would_fix"] == 1
    assert r["identical"] == 0


def test_would_break_counts_a_row_the_reparse_makes_wrong():
    r = _case(dict(char_type="Distance", nominal="20", raw_text="totally other"),
              dict(char_type="Distance", nominal="20"))
    assert r["would_break"] == 1


def test_hint_map_matches_extract_so_the_coupling_cannot_drift_silently():
    from app.pipeline.extract import _HINTS as pipeline_hints
    assert _HINTS == pipeline_hints


def test_report_is_values_blind():
    import json
    r = _case(dict(char_type="Diameter", nominal="2", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20"))
    blob = json.dumps(r, ensure_ascii=False)
    for leak in ("Ø20", "0,1", "'20'", "raw_text"):
        assert leak not in blob, f"reparse report leaked {leak!r}"
