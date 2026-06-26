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


from app.pipeline.title_block import (
    parse_title_cell, split_label, review_flags_field,
)


def test_parse_title_cell_json():
    assert parse_title_cell('{"label": "Sheet / Blatt", "value": "1/1"}') \
        == ("Sheet / Blatt", "1/1")


def test_parse_title_cell_strips_code_fence():
    raw = '```json\n{"label": "Scale / Maßstab", "value": "5:1"}\n```'
    assert parse_title_cell(raw) == ("Scale / Maßstab", "5:1")


def test_parse_title_cell_empty_label_for_value_only():
    assert parse_title_cell('{"label": "", "value": "1025206"}') == ("", "1025206")


def test_parse_title_cell_colon_fallback():
    assert parse_title_cell("Format / Size: A2") == ("Format / Size", "A2")


def test_parse_title_cell_blank_returns_empty_pair():
    assert parse_title_cell("") == ("", "")
    assert parse_title_cell("   ") == ("", "")


def test_split_label_bilingual():
    assert split_label("Released / Freigabe") == ("Released", "Freigabe")


def test_split_label_no_separator():
    assert split_label("Maßstab") == ("Maßstab", "")


def test_review_flags_empty_value():
    flagged, reasons = review_flags_field(value="", label="Sheet / Blatt")
    assert flagged is True and reasons == ["empty value"]


def test_review_flags_missing_caption_when_expected():
    flagged, reasons = review_flags_field(value="A2", label="")
    assert flagged is True and reasons == ["missing caption"]


def test_review_flags_loose_text_not_flagged():
    # loose text intentionally has no caption -> not a problem
    flagged, reasons = review_flags_field(value="SOME NOTE", label="",
                                          expect_caption=False)
    assert flagged is False and reasons == []


def test_review_flags_clean_field():
    flagged, reasons = review_flags_field(value="1/1", label="Sheet / Blatt")
    assert flagged is False and reasons == []
