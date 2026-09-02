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


def test_a_read_in_the_zero_point_six_to_zero_point_eight_band_is_flagged():
    """LOW_CONF was 0.6, which let the 0.6-0.8 band through unflagged. That band
    is not marginal-but-usually-right; on the dev split it held 18 matched pairs
    at a 100% error rate with ZERO correct rows (baseline-dev-diag
    confidence_by_taxonomy: escaped_error 15, flagged_error 3), and every one of
    the 24 pairs below 0.8 was wrong.

    So flagging it converts 15 silent wrong values (w=5) into flagged ones (w=1)
    and flags no correct row: -60 total, -3.00 mean review cost, escaped_rate
    0.2704 -> 0.2390, field_acc untouched. Raising a threshold can only ADD the
    low-confidence reason, never remove one, which is what makes that arithmetic
    exact from stored confidences rather than an estimate."""
    _, reasons = review_flags(_row(raw_text="1,2", nominal="1,2", confidence=0.7),
                              rotation_ambiguous=False)
    assert reasons == ["low OCR confidence"]


def test_a_saturated_confidence_read_is_still_trusted():
    """The regression half, and the reason the threshold stops at 0.8 rather than
    going higher: 284 of the 308 matched pairs sit at >=0.8 and 112 of those are
    field-correct. Flagging that band would charge w=1 for 112 correct rows to
    catch 114 escaped ones -- a far worse trade than the 15-for-nothing below
    it. 0.8 must therefore be trusted, not flagged."""
    _, reasons = review_flags(_row(raw_text="1,2", nominal="1,2", confidence=0.8),
                              rotation_ambiguous=False)
    assert reasons == []


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
