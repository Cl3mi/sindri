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
