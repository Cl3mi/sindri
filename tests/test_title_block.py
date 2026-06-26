from app.models import TitleField, ExtractionResult


def test_title_field_defaults():
    f = TitleField(label="Sheet / Blatt", value="1/1")
    assert f.label == "Sheet / Blatt"
    assert f.value == "1/1"
    assert f.label_en == "" and f.label_de == ""
    assert f.confidence == 0.0
    assert f.needs_review is False
    assert f.review_reasons == []
    assert f.box is None


def test_extraction_result_title_block_defaults_empty():
    r = ExtractionResult(characteristics=[])
    assert r.title_block == []
    # round-trips through model_dump (used by the SSE payload)
    assert r.model_dump()["title_block"] == []
