from app.pipeline.marks_block import parse_marks_block


def test_parses_top_level_bilingual_row():
    raw = "101\tCONTACT AREA FREE OF GREASE AND OIL\tKONTAKTBEREICH FREI VON FETTEN UND OEL"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert block.region == (0, 0, 100, 100)
    assert len(block.marks) == 1
    m = block.marks[0]
    assert m.pos == 101
    assert m.text_en == "CONTACT AREA FREE OF GREASE AND OIL"
    assert m.text_de == "KONTAKTBEREICH FREI VON FETTEN UND OEL"
    assert m.raw_text == raw


def test_parses_single_language_row_when_no_tab_after_en():
    raw = "102\tCONTACT AREA FREE FROM DAMAGES"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert len(block.marks) == 1
    m = block.marks[0]
    assert m.text_en == "CONTACT AREA FREE FROM DAMAGES"
    assert m.text_de == ""


def test_drops_malformed_lines_silently():
    raw = (
        "this is not a mark row\n"
        "101\tA\tB\n"
        "\n"
        "garbage 999\n"
    )
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    positions = [m.pos for m in block.marks]
    assert positions == [101]


def test_parses_multiple_rows_in_source_order():
    raw = (
        "101\tA-en\tA-de\n"
        "102\tB-en\tB-de\n"
        "109\tI-en\tI-de\n"
    )
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert [m.pos for m in block.marks] == [101, 102, 109]


def test_three_digit_pos_outside_10x_range_still_accepted():
    raw = "199\tmark text en\tmark text de"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert block.marks[0].pos == 199


def test_sub_bullet_lines_are_dropped():
    # marks table has no sub-bullets; if VLM emits one, parser must drop it
    raw = "101\tA\tB\n101.1\tsub\tnot expected\n102\tC\tD"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert [m.pos for m in block.marks] == [101, 102]


from app.models import Mark
from app.pipeline.marks_block import review_flags_mark


def _mark(**kw):
    base = dict(pos=101, text_en="A", text_de="B", raw_text="101\tA\tB")
    base.update(kw)
    return Mark(**base)


def test_clean_mark_not_flagged():
    needs, reasons = review_flags_mark(_mark(), two_columns=True)
    assert needs is False and reasons == []


def test_empty_read_flagged():
    needs, reasons = review_flags_mark(_mark(raw_text=""), two_columns=True)
    assert needs is True and reasons == ["empty read"]


def test_missing_german_flagged_when_two_columns():
    needs, reasons = review_flags_mark(_mark(text_de=""), two_columns=True)
    assert needs is True and reasons == ["missing translation"]


def test_single_column_does_not_require_german():
    needs, reasons = review_flags_mark(_mark(text_de=""), two_columns=False)
    assert needs is False and reasons == []


from PIL import Image
from app.pipeline.marks_block import MarksBlockRegion, mask_region


def test_mask_region_fills_outer_box_white_and_preserves_outside():
    img = Image.new("RGB", (100, 100), color=(50, 50, 50))
    region = MarksBlockRegion(outer_box=(20, 30, 60, 70), lang_columns=[(20, 60)])
    out = mask_region(img, region)
    # inside the box: white
    assert out.getpixel((25, 35)) == (255, 255, 255)
    # outside the box: untouched
    assert out.getpixel((5, 5)) == (50, 50, 50)
    # original not mutated
    assert img.getpixel((25, 35)) == (50, 50, 50)


def test_mask_region_noop_on_zero_size_box():
    img = Image.new("RGB", (50, 50), color=(0, 0, 0))
    region = MarksBlockRegion(outer_box=(10, 10, 10, 10), lang_columns=[(10, 10)])
    out = mask_region(img, region)
    assert out.getpixel((10, 10)) == (0, 0, 0)


from app.pipeline.marks_block import locate_marks_block
from PIL import ImageDraw


def _white_canvas(w=1000, h=700):
    return Image.new("RGB", (w, h), color=(255, 255, 255))


def _draw_rect(img, x0, y0, x1, y1, stroke=3):
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x1, y1), outline=(0, 0, 0), width=stroke)
    return img


def _page_with_top_right_legend(w=1000, h=800):
    """A white page with a 3-row ruled legend flush against the drawing frame in
    the top-right, ink in every row. The middle description cell is oversized, so
    the largest *single* rectangle is an inner cell — this reproduces the real
    drawing where picking the biggest rectangle missed the header and other rows.
    """
    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((6, 6, w - 6, h - 6), outline=(0, 0, 0), width=3)  # drawing frame
    left, top, right, bottom, div = 600, 6, w - 6, 360, 680
    d.line((div, top, div, bottom), fill=(0, 0, 0), width=3)       # mark# | desc
    d.line((left, top, left, bottom), fill=(0, 0, 0), width=3)
    d.line((left, bottom, right, bottom), fill=(0, 0, 0), width=3)
    for y in (90, 300):                       # row separators -> oversized middle
        d.line((left, y, right, y), fill=(0, 0, 0), width=3)
    for cy in (48, 195, 330):                 # ink in every row, both columns
        d.rectangle((615, cy - 8, 670, cy + 8), fill=(0, 0, 0))
        d.rectangle((700, cy - 6, right - 40, cy + 6), fill=(0, 0, 0))
    return img


from app.pipeline.marks_block import _legend_cells_in_band


def test_legend_band_drops_tall_frame_enclosed_cells():
    # Cells observed on the real drawing: three legend rows (mark# col + desc
    # col) plus a tall frame-enclosed corner cell and a low sliver that must NOT
    # be pulled into the region (they would over-mask the drawing views).
    cells = [
        (4064, 62, 4309, 176), (4313, 62, 6954, 176),
        (4064, 179, 4309, 837), (4313, 179, 6246, 837),
        (4064, 840, 4309, 1002), (4313, 840, 6954, 1002),
        (4750, 2, 7014, 2728),        # tall corner region -> drop
        (3508, 2165, 3561, 2728),     # low sliver -> drop
    ]
    band = _legend_cells_in_band(cells)
    outer = (min(c[0] for c in band), min(c[1] for c in band),
             max(c[2] for c in band), max(c[3] for c in band))
    assert outer == (4064, 62, 6954, 1002)


def test_locator_does_not_over_capture_below_legend():
    # The region must stay within the legend rows, not swallow geometry beneath.
    region = locate_marks_block(_page_with_top_right_legend())
    assert region is not None
    assert region.outer_box[3] <= 400


def test_locator_spans_full_multirow_legend():
    # Regression: must capture the WHOLE table, not just the largest inner cell.
    region = locate_marks_block(_page_with_top_right_legend())
    assert region is not None
    x0, y0, x1, y1 = region.outer_box
    assert y0 <= 30, f"top row missed: y0={y0}"
    assert y1 >= 330, f"bottom row missed: y1={y1}"
    assert x0 <= 640 and x1 >= 940


def test_locator_region_centre_in_top_right():
    region = locate_marks_block(_page_with_top_right_legend())
    assert region is not None
    x0, y0, x1, y1 = region.outer_box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert cx > 0.5 * 1000
    assert cy < 0.5 * 800


def test_locator_returns_none_when_no_top_right_rectangle():
    img = _white_canvas()
    _draw_rect(img, 30, 500, 250, 650)        # bottom-left
    _draw_rect(img, 400, 300, 600, 500)       # centre
    assert locate_marks_block(img) is None


def test_locator_ignores_ink_less_outline_in_top_right():
    # An empty bordered box (no text) is not a legend.
    img = _white_canvas()
    _draw_rect(img, 700, 30, 970, 300)
    assert locate_marks_block(img) is None


def test_locator_returns_none_on_blank_image():
    assert locate_marks_block(_white_canvas()) is None


from app.pipeline.marks_block import read_marks_block, MarksBlockRegion


class _FakeOcrResult:
    def __init__(self, text): self.text = text


class _BackendWithNotesPrompt:
    def __init__(self, text):
        self._text = text
        # tracks whether the dedicated prompt was used
        self.used_notes_prompt = False

    def read_notes_block(self, image):
        self.used_notes_prompt = True
        return _FakeOcrResult(self._text)

    def read_region(self, image):
        return _FakeOcrResult("WRONG-PROMPT")


class _BackendGenericOnly:
    def read_region(self, image):
        return _FakeOcrResult("GENERIC")


def test_read_marks_prefers_notes_prompt_when_available():
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    region = MarksBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    backend = _BackendWithNotesPrompt("101\tA\tB")
    text = read_marks_block(img, region, backend)
    assert text == "101\tA\tB"
    assert backend.used_notes_prompt is True


def test_read_marks_falls_back_to_read_region():
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    region = MarksBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    text = read_marks_block(img, region, _BackendGenericOnly())
    assert text == "GENERIC"


def test_read_marks_returns_empty_on_backend_exception():
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    region = MarksBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])

    class _Bad:
        def read_region(self, image):
            raise RuntimeError("boom")

    assert read_marks_block(img, region, _Bad()) == ""
