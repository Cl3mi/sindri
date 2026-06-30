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


def test_locator_picks_top_right_rectangle():
    img = _white_canvas()
    # decoy in bottom-left
    _draw_rect(img, 30, 500, 250, 650)
    # target in top-right
    _draw_rect(img, 700, 30, 970, 300)
    region = locate_marks_block(img)
    assert region is not None
    x0, y0, x1, y1 = region.outer_box
    # centre in top-right quadrant
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert cx > 0.55 * 1000
    assert cy < 0.45 * 700
    # roughly matches the drawn target (allow a few px for stroke)
    assert abs(x0 - 700) <= 5 and abs(y0 - 30) <= 5
    assert abs(x1 - 970) <= 5 and abs(y1 - 300) <= 5


def test_locator_returns_none_when_no_top_right_rectangle():
    img = _white_canvas()
    _draw_rect(img, 30, 500, 250, 650)        # bottom-left
    _draw_rect(img, 400, 300, 600, 500)       # centre
    assert locate_marks_block(img) is None


def test_locator_picks_largest_when_multiple_top_right():
    img = _white_canvas()
    _draw_rect(img, 700, 30, 800, 100)        # small top-right
    _draw_rect(img, 600, 50, 970, 400)        # large top-right
    region = locate_marks_block(img)
    assert region is not None
    x0, y0, x1, y1 = region.outer_box
    assert abs(x0 - 600) <= 5 and abs(y1 - 400) <= 5


def test_locator_returns_none_on_blank_image():
    assert locate_marks_block(_white_canvas()) is None
