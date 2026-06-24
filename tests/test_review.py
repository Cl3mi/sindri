from app.models import Characteristic
from app.pipeline.review import review_flags


def _row(**kw):
    base = dict(pos=1, char_type="Distance", nominal="1,2", raw_text="1,2 +0,1 -0,1",
                confidence=0.9)
    base.update(kw)
    return Characteristic(**base)


def test_clean_dimension_row_is_not_flagged():
    flagged, reasons = review_flags(_row(), rotation_ambiguous=False)
    assert flagged is False
    assert reasons == []


def test_empty_read_is_flagged():
    flagged, reasons = review_flags(_row(raw_text="", nominal="", confidence=0.0),
                                    rotation_ambiguous=False)
    assert flagged is True
    assert reasons == ["empty read"]


def test_empty_read_does_not_also_report_missing_nominal_or_low_conf():
    _, reasons = review_flags(_row(raw_text="  ", nominal="", confidence=0.0),
                              rotation_ambiguous=False)
    assert reasons == ["empty read"]


def test_missing_nominal_when_text_present_but_unparsed():
    _, reasons = review_flags(_row(raw_text="garbled", nominal=""),
                              rotation_ambiguous=False)
    assert reasons == ["missing nominal"]


def test_low_ocr_confidence_when_text_present():
    _, reasons = review_flags(_row(raw_text="1,2", nominal="1,2", confidence=0.4),
                              rotation_ambiguous=False)
    assert reasons == ["low OCR confidence"]


def test_rotation_ambiguity_reason():
    _, reasons = review_flags(_row(), rotation_ambiguous=True)
    assert reasons == ["rotation ambiguity"]


def test_gdt_position_row_with_zero_nominal_not_flagged_for_missing_nominal():
    flagged, reasons = review_flags(
        _row(char_type="Position", nominal="0", raw_text="⊕ Ø0.1 A"),
        rotation_ambiguous=False)
    assert "missing nominal" not in reasons
    assert flagged is False


def test_note_row_without_nominal_not_flagged_for_missing_nominal():
    _, reasons = review_flags(_row(char_type="Note", nominal="see DBL 8585",
                                   raw_text="see DBL 8585"),
                              rotation_ambiguous=False)
    assert reasons == []


def test_combination_empty_read_and_rotation_ambiguity():
    flagged, reasons = review_flags(_row(raw_text="", nominal="", confidence=0.0),
                                    rotation_ambiguous=True)
    assert flagged is True
    assert reasons == ["empty read", "rotation ambiguity"]


def test_theoretical_row_with_text_but_no_nominal_is_flagged():
    # a boxed theoretical value that read text but parsed no number is a garbled read
    _, reasons = review_flags(_row(char_type="Theoretical", nominal="", raw_text="garbled"),
                              rotation_ambiguous=False)
    assert reasons == ["missing nominal"]


def test_theoretical_row_with_nominal_is_not_flagged():
    flagged, reasons = review_flags(_row(char_type="Theoretical", nominal="20", raw_text="20"),
                                    rotation_ambiguous=False)
    assert flagged is False
    assert reasons == []


def test_unknown_note_reference_when_pos_not_in_block():
    c = _row(char_type="Note", subtype="note_ref", raw_text="101",
             nominal="101", note_ref_pos=101)
    _, reasons = review_flags(c, rotation_ambiguous=False, known_note_positions={102, 103})
    assert "unknown note reference" in reasons


def test_known_note_reference_not_flagged():
    c = _row(char_type="Note", subtype="note_ref", raw_text="101",
             nominal="101", note_ref_pos=101)
    flagged, reasons = review_flags(c, rotation_ambiguous=False,
                                    known_note_positions={101, 102})
    assert "unknown note reference" not in reasons
    assert flagged is False


def test_note_ref_when_no_block_present_skips_unknown_check():
    c = _row(char_type="Note", subtype="note_ref", raw_text="101",
             nominal="101", note_ref_pos=101)
    _, reasons = review_flags(c, rotation_ambiguous=False, known_note_positions=None)
    assert "unknown note reference" not in reasons
