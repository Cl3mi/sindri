import pytest
from app.eval.models import (
    GoldCharacteristic, GoldDoc, RunConfig, PredictionDump,
    ReviewCostWeights, DocScore, RunReport, SCHEMA_VERSION,
)
from app.models import ExtractionResult


def _gold_doc():
    return GoldDoc(
        doc_id="T1", pdf="a.pdf", excel="a.xlsx",
        page_rect=(0.0, 0.0, 1189.0, 841.0),
        characteristics=[GoldCharacteristic(
            balloon=1, position_pt=(100.0, 200.0),
            char_type="Diameter", nominal="20", upper_tol="0,1", lower_tol="-0,1",
        )],
    )


def test_gold_doc_roundtrips_and_carries_schema_version():
    g = _gold_doc()
    assert g.schema_version == SCHEMA_VERSION
    g2 = GoldDoc.model_validate_json(g.model_dump_json())
    assert g2 == g


def test_gold_hash_is_stable_and_ignores_provenance():
    a, b = _gold_doc(), _gold_doc()
    b.provenance = {"n_circles": 99}
    assert a.gold_hash() == b.gold_hash()
    b.characteristics[0].nominal = "21"
    assert a.gold_hash() != b.gold_hash()


def test_prediction_dump_wraps_extraction_result():
    d = PredictionDump(
        doc_id="T1",
        config=RunConfig(model_id="stub", dpi=300),
        scale=300 / 72.0, page_rect=(0.0, 0.0, 1189.0, 841.0),
        result=ExtractionResult(characteristics=[]),
    )
    d2 = PredictionDump.model_validate_json(d.model_dump_json())
    assert d2.config.model_id == "stub"
    assert d2.schema_version == SCHEMA_VERSION


def test_unknown_schema_version_rejected():
    g = _gold_doc()
    payload = g.model_dump()
    payload["schema_version"] = 999
    with pytest.raises(ValueError):
        GoldDoc.model_validate(payload)


def test_default_weights_ordering():
    w = ReviewCostWeights()
    assert w.miss > w.escaped > w.false > w.flag
