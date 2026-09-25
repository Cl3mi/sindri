"""Before any GPU arm: do OCR proposals even land on the isolated misses?
The counting core is tested with proposals injected; the identity gate --
the isolated set reproduces DocScore.missed_isolated exactly -- is what makes
the coverage number trustworthy."""
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.proposal_check import count_doc, proposal_check
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _case():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(700, 500), nominal="7"),
        GoldCharacteristic(balloon=3, position_pt=(1000, 100), nominal="9"),
    ])
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=[
        Characteristic(pos=1, nominal="20", raw_text="20",
                       target_region=_box(100, 100))]))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    return dump, gold, s


def test_coverage_counts_isolated_misses_with_a_proposal_in_the_gate():
    dump, gold, s = _case()
    assert s.missed_isolated == 2
    props = [_box(705, 500), _box(300, 800)]   # near gold 2; far from all
    c = count_doc(dump, gold, s, props, MatchParams())
    assert c == {"isolated": 2, "isolated_covered": 1, "proposals": 2,
                 "near_unmatched_gold": 1, "near_matched_gold": 0,
                 "far_from_gold": 1}


def test_identity_gate_refuses_a_diverging_isolated_set():
    dump, gold, s = _case()
    s.missed_isolated = 1          # simulate a drifted predicate
    with pytest.raises(AssertionError):
        count_doc(dump, gold, s, [], MatchParams())


@dataclass
class _FakeRender:
    png_path: Path
    scale: float


def test_proposal_check_wires_render_and_ocr_together(tmp_path, monkeypatch):
    """No real PDF: render_page and ocr_proposals are monkeypatched. This tests
    that proposal_check calls count_doc correctly per document and totals the
    counts + the unverified_net_units bound -- not the CV/tesseract internals,
    which count_doc's own tests already exercise directly."""
    dump, gold, s = _case()
    png = tmp_path / "page.png"
    Image.new("RGB", (10, 10), "white").save(png)

    def fake_render_page(pdf_path, dpi=200, out_dir=None, **kw):
        return _FakeRender(png_path=png, scale=dump.scale)

    props = [_box(705, 500), _box(300, 800)]   # matches the injected-props test

    def fake_ocr_proposals(image, existing):
        return props

    monkeypatch.setattr("app.pipeline.render.render_page", fake_render_page)
    monkeypatch.setattr("app.pipeline.proposals.ocr_proposals", fake_ocr_proposals)

    out = proposal_check({"D": dump}, {"D": gold}, [s], str(tmp_path), MatchParams())

    assert out["docs"] == 1
    assert out["scale_mismatch"] == 0
    assert out["isolated"] == 2
    assert out["isolated_covered"] == 1
    assert out["proposals"] == 2
    assert out["near_unmatched_gold"] == 1
    assert out["near_matched_gold"] == 0
    assert out["far_from_gold"] == 1
    # 9 * covered - 2 * far = 9*1 - 2*1 = 7
    assert out["unverified_net_units"] == 7


def test_proposal_check_counts_scale_mismatch_and_skips_the_document(
        tmp_path, monkeypatch):
    dump, gold, s = _case()
    png = tmp_path / "page.png"
    Image.new("RGB", (10, 10), "white").save(png)

    def fake_render_page(pdf_path, dpi=200, out_dir=None, **kw):
        return _FakeRender(png_path=png, scale=dump.scale + 1.0)   # mismatched

    def fake_ocr_proposals(image, existing):
        raise AssertionError("must not run ocr on a scale-mismatched render")

    monkeypatch.setattr("app.pipeline.render.render_page", fake_render_page)
    monkeypatch.setattr("app.pipeline.proposals.ocr_proposals", fake_ocr_proposals)

    out = proposal_check({"D": dump}, {"D": gold}, [s], str(tmp_path), MatchParams())

    assert out["docs"] == 0
    assert out["scale_mismatch"] == 1
