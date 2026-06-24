from app.pipeline.notes_block import parse_notes_block


def test_parses_top_level_bilingual_row():
    raw = "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert nb.region == (0, 0, 100, 100)
    assert len(nb.notes) == 1
    n = nb.notes[0]
    assert n.pos == 101 and n.parent_pos is None and n.sub_index is None
    assert n.text_en == "CONTACT AREA NOTES"
    assert n.text_de == "KONTAKTBEREICH HINWEISE"
    assert n.raw_text == raw


def test_parses_sub_bullet_links_parent():
    raw = (
        "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE\n"
        "101.1\tPLANARITY 0,2mm\tEBENHEIT 0,2mm"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert len(nb.notes) == 2
    sub = nb.notes[1]
    assert sub.pos == 1
    assert sub.parent_pos == 101
    assert sub.sub_index == 1
    assert sub.text_en == "PLANARITY 0,2mm"
    assert sub.text_de == "EBENHEIT 0,2mm"


def test_parses_single_language_row_when_no_tab_after_en():
    raw = "102\tPART FREE OF GREASE AND OIL"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert len(nb.notes) == 1
    n = nb.notes[0]
    assert n.text_en == "PART FREE OF GREASE AND OIL"
    assert n.text_de == ""


def test_drops_malformed_lines_silently():
    raw = (
        "this is not a note row\n"
        "101\tA\tB\n"
        "\n"
        "garbage 999\n"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    positions = [n.pos for n in nb.notes]
    assert positions == [101]


def test_parses_multiple_top_level_and_sub_bullets():
    raw = (
        "101\tA-en\tA-de\n"
        "101.1\tA1-en\tA1-de\n"
        "101.2\tA2-en\tA2-de\n"
        "102\tB-en\tB-de\n"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    flat = [(n.pos, n.parent_pos, n.sub_index) for n in nb.notes]
    assert flat == [(101, None, None), (1, 101, 1), (2, 101, 2), (102, None, None)]


def test_three_digit_pos_outside_10x_range_still_accepted():
    raw = "199\tnote text en\tnote text de"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert nb.notes[0].pos == 199


from app.models import Note
from app.pipeline.notes_block import review_flags_note


def _note(**kw):
    base = dict(pos=101, text_en="A", text_de="B", raw_text="101\tA\tB")
    base.update(kw)
    return Note(**base)


def test_clean_top_level_note_not_flagged():
    flagged, reasons = review_flags_note(
        _note(), two_columns=True, known_parents=set())
    assert flagged is False
    assert reasons == []


def test_empty_raw_text_is_flagged():
    flagged, reasons = review_flags_note(
        _note(raw_text="", text_en="", text_de=""),
        two_columns=True, known_parents=set())
    assert flagged is True
    assert reasons == ["empty read"]


def test_missing_translation_when_two_columns_expected():
    _, reasons = review_flags_note(
        _note(text_de=""), two_columns=True, known_parents=set())
    assert reasons == ["missing translation"]


def test_missing_translation_not_reported_for_single_column_block():
    _, reasons = review_flags_note(
        _note(text_de=""), two_columns=False, known_parents=set())
    assert reasons == []


def test_orphan_sub_bullet_when_parent_not_in_block():
    sub = _note(pos=1, parent_pos=999, sub_index=1, raw_text="999.1\tA\tB")
    _, reasons = review_flags_note(sub, two_columns=True, known_parents={101})
    assert "orphan sub-bullet" in reasons


def test_sub_bullet_with_known_parent_not_flagged_for_orphan():
    sub = _note(pos=1, parent_pos=101, sub_index=1, raw_text="101.1\tA\tB")
    _, reasons = review_flags_note(sub, two_columns=True, known_parents={101})
    assert reasons == []


def test_empty_read_suppresses_missing_translation():
    _, reasons = review_flags_note(
        _note(raw_text="", text_en="", text_de=""),
        two_columns=True, known_parents=set())
    assert reasons == ["empty read"]
