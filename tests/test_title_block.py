from pathlib import Path
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


from app.pipeline.ocr.base import OcrResult
from app.pipeline.title_block import read_title_block


class _StubTitleBackend:
    """Returns a canned per-cell JSON read for read_title_cell."""
    def __init__(self, by_call):
        self._by_call = list(by_call)
        self._i = 0

    def read_title_cell(self, image):
        text = self._by_call[self._i] if self._i < len(self._by_call) else ""
        self._i += 1
        return OcrResult(text=text, confidence=0.9 if text else 0.0)


def test_read_title_block_builds_fields_with_split_labels():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    # force a single known ink cell so the read is deterministic
    region.cells = [region.cells[0]]
    backend = _StubTitleBackend(['{"label": "Size / Format", "value": "A2"}'])
    fields = read_title_block(img, region, backend)
    assert len(fields) == 1
    f = fields[0]
    assert f.label == "Size / Format"
    assert f.label_en == "Size" and f.label_de == "Format"
    assert f.value == "A2"
    assert f.box is not None
    assert f.needs_review is False


def test_read_title_block_flags_empty_value():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    region.cells = [region.cells[0]]
    backend = _StubTitleBackend(['{"label": "Scale / Maßstab", "value": ""}'])
    fields = read_title_block(img, region, backend)
    assert fields[0].needs_review is True
    assert fields[0].review_reasons == ["empty value"]


def test_read_title_block_skips_fully_empty_reads():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    region.cells = [region.cells[0]]
    backend = _StubTitleBackend([''])     # no label, no value -> dropped
    assert read_title_block(img, region, backend) == []


def test_read_title_block_survives_backend_error():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    region.cells = [region.cells[0]]

    class Boom:
        def read_title_cell(self, image):
            raise RuntimeError("kaboom")

    assert read_title_block(img, region, Boom()) == []


from app.pipeline.detect import Detection
from app.pipeline.title_block import loose_text


class _LooseBackend:
    """Detects the same note boxes for every tile, reads canned text."""
    def __init__(self, dets, text):
        self._dets = dets
        self._text = text

    def detect_regions(self, image):
        return list(self._dets)

    def read_region(self, image):
        return OcrResult(text=self._text, confidence=0.8)


def test_loose_text_emits_label_less_field_outside_excludes(monkeypatch):
    # one note detection at tile-local (10,10,120,40); single tile at origin
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, 400, 400)])
    backend = _LooseBackend([Detection(box=(10, 10, 120, 40), kind="note", conf=0.9)],
                            text="NACH WAHL DES HERSTELLERS")
    fields = loose_text(Image.new("RGB", (400, 400), "white"), backend,
                        exclude_boxes=[(300, 300, 400, 400)])
    assert len(fields) == 1
    assert fields[0].label == "" and fields[0].value == "NACH WAHL DES HERSTELLERS"
    assert fields[0].needs_review is False     # loose text not flagged


def test_loose_text_drops_detections_inside_exclude(monkeypatch):
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, 400, 400)])
    backend = _LooseBackend([Detection(box=(310, 310, 360, 340), kind="note", conf=0.9)],
                            text="INSIDE TITLE BLOCK")
    fields = loose_text(Image.new("RGB", (400, 400), "white"), backend,
                        exclude_boxes=[(300, 300, 400, 400)])
    assert fields == []


def test_loose_text_survives_detector_error(monkeypatch):
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, 400, 400)])

    class Boom:
        def detect_regions(self, image):
            raise RuntimeError("kaboom")

    assert loose_text(Image.new("RGB", (400, 400), "white"), Boom(),
                      exclude_boxes=[]) == []


import uuid
from app.pipeline.extract import extract


def test_extract_attaches_title_block(tmp_path, monkeypatch):
    """A fake backend + stubbed locate yields a title_block on the result and
    masks the region before detection."""
    from app.pipeline import title_block as tb
    from app.models import TitleField as TF

    region = tb.TitleBlockRegion(outer_box=(600, 560, 980, 760),
                                 cells=[(610, 570, 780, 650)])
    monkeypatch.setattr("app.pipeline.extract.tb.locate_title_block",
                        lambda image: region)
    monkeypatch.setattr(
        "app.pipeline.extract.tb.read_title_block",
        lambda image, reg, backend: [TF(label="Size / Format", label_en="Size",
                                        label_de="Format", value="A2")])
    monkeypatch.setattr("app.pipeline.extract.tb.loose_text",
                        lambda image, backend, exclude_boxes: [])
    # notes locator off so it doesn't interfere
    monkeypatch.setattr("app.pipeline.extract.nb.locate_notes_block",
                        lambda image, backend: None)

    from tests.conftest import StubVLMBackend
    backend = StubVLMBackend(detections=[])

    import shutil
    src = Path(__file__).parents[1] / "test_docs" / "T1025206_D.pdf"
    if not src.exists():
        src = Path(__file__).parents[1] / "sample.pdf"
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(src, work / "input.pdf")

    result = extract(work / "input.pdf", work_dir=work, dpi=150, backend=backend)
    assert len(result.title_block) == 1
    assert result.title_block[0].value == "A2"


def test_margin_note_not_double_extracted_as_loose_text(tmp_path, monkeypatch):
    """A 'note'-kind region the main detector captures as a Characteristic must
    not ALSO appear as a loose title field (no double-extraction)."""
    import shutil
    from app.pipeline import title_block as tb2
    from app.pipeline.detect import Detection as Det
    from app.pipeline.ocr.base import OcrResult as OR

    # No title block, no notes block — isolate the loose-text path.
    monkeypatch.setattr("app.pipeline.extract.tb.locate_title_block",
                        lambda image: None)
    monkeypatch.setattr("app.pipeline.extract.nb.locate_notes_block",
                        lambda image, backend: None)
    # Make the title-block tiler a single full-page tile for determinism.
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, w, h)])

    class _FB:
        # one note-kind detection at a fixed page box
        def detect_regions(self, image):
            return [Det(box=(100, 100, 300, 140), kind="note", conf=0.9)]
        def read_region(self, image):
            return OR(text="NACH WAHL DES HERSTELLERS", confidence=0.8)

    src = Path(__file__).parents[1] / "test_docs" / "T1025206_D.pdf"
    if not src.exists():
        src = Path(__file__).parents[1] / "sample.pdf"
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy(src, work / "input.pdf")

    from app.pipeline.extract import extract
    result = extract(work / "input.pdf", work_dir=work, dpi=150, backend=_FB())

    # The note-kind region was detected -> it is a characteristic.
    assert len(result.characteristics) >= 1
    # It must NOT be duplicated into the loose title_block.
    assert result.title_block == []
