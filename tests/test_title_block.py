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


from PIL import Image
from PIL import ImageDraw as _ImageDraw
from app.pipeline.title_block import detect_cells, _cell_has_ink


def _grid_image():
    """A 2x2 ruled grid (400x200) with text only in the top-left cell."""
    img = Image.new("RGB", (400, 200), "white")
    d = _ImageDraw.Draw(img)
    # outer frame + one vertical + one horizontal divider, thick black lines
    d.rectangle((10, 10, 390, 190), outline="black", width=3)
    d.line((200, 10, 200, 190), fill="black", width=3)
    d.line((10, 100, 390, 100), fill="black", width=3)
    d.rectangle((40, 40, 110, 70), fill="black")     # ink in top-left cell only
    return img


def test_detect_cells_finds_four_cells():
    cells = detect_cells(_grid_image(), (0, 0, 400, 200))
    # 2x2 grid -> 4 interior cells (give or take border slivers, so >= 4)
    assert len(cells) >= 4
    # every cell lies inside the image bounds
    for x0, y0, x1, y1 in cells:
        assert 0 <= x0 < x1 <= 400 and 0 <= y0 < y1 <= 200


def test_detect_cells_reading_order_top_to_bottom_left_to_right():
    cells = detect_cells(_grid_image(), (0, 0, 400, 200))
    # first cell is in the top band and left of the last cell's column
    assert cells[0][1] <= cells[-1][1]


def test_detect_cells_empty_region_returns_empty():
    assert detect_cells(Image.new("RGB", (400, 200), "white"), (0, 0, 5, 5)) == []


def test_cell_has_ink_true_for_text_cell_false_for_blank():
    img = _grid_image()
    assert _cell_has_ink(img, (15, 15, 195, 95)) is True     # top-left has ink rect
    assert _cell_has_ink(img, (205, 105, 385, 185)) is False  # bottom-right blank


from app.pipeline.title_block import (
    TitleBlockRegion, locate_title_block, mask_region,
)


def _page_with_bottom_right_grid():
    """A 1000x800 white page with a ruled 2x2 grid in the bottom-right corner
    and text in its top-left cell."""
    img = Image.new("RGB", (1000, 800), "white")
    d = _ImageDraw.Draw(img)
    d.rectangle((600, 560, 980, 760), outline="black", width=3)
    d.line((790, 560, 790, 760), fill="black", width=3)
    d.line((600, 660, 980, 660), fill="black", width=3)
    d.rectangle((630, 590, 720, 630), fill="black")
    return img


def test_locate_finds_bottom_right_region():
    region = locate_title_block(_page_with_bottom_right_grid())
    assert region is not None
    assert isinstance(region, TitleBlockRegion)
    # outer box sits in the bottom-right of the page
    assert region.outer_box[0] >= 500 and region.outer_box[1] >= 480
    assert len(region.cells) >= 1


def test_locate_returns_none_on_blank_page():
    assert locate_title_block(Image.new("RGB", (1000, 800), "white")) is None


def test_mask_region_fills_white_and_preserves_original():
    img = Image.new("RGB", (100, 100), "black")
    region = TitleBlockRegion(outer_box=(20, 30, 60, 70), cells=[])
    out = mask_region(img, region)
    assert out.getpixel((30, 40)) == (255, 255, 255)   # inside masked
    assert out.getpixel((10, 10)) == (0, 0, 0)          # outside untouched
    assert img.getpixel((30, 40)) == (0, 0, 0)          # copy semantics
