"""The general tolerance a drawing names in its title block or notes, applied to
dimensions whose read carries none.

Gold carries the ISO 2768 general tolerance on nearly every row, so a dimension
read without one is wrong by construction and `no_tolerance` flags it. Applying
the class the drawing itself names makes those rows correct without a reviewer.
Everything here is FROZEN by docs/plans/2026-10-05-general-tolerance-registration.md
-- a test failing here after a split was priced means the registration moved,
not that the test is stale."""
from decimal import Decimal

import pytest

from app.models import (Characteristic, ExtractionResult, Note, NoteBlock,
                        TitleField)
from app.pipeline import general_tolerance as gt


# --- §2 the ISO 2768-1 table -------------------------------------------------

@pytest.mark.parametrize("cls,nominal,expected", [
    ("m", "0,5", "0.1"), ("m", "3", "0.1"), ("m", "3,01", "0.1"),
    ("m", "6", "0.1"), ("m", "6.5", "0.2"), ("m", "30", "0.2"),
    ("m", "31", "0.3"), ("m", "120", "0.3"), ("m", "400", "0.5"),
    ("m", "1000", "0.8"), ("m", "2000", "1.2"), ("m", "4000", "2"),
    ("f", "10", "0.1"), ("f", "50", "0.15"), ("f", "2000", "0.5"),
    ("c", "4", "0.3"), ("c", "500", "2"), ("v", "4", "0.5"),
    ("v", "3000", "8"),
])
def test_linear_table_boundaries_are_upper_inclusive(cls, nominal, expected):
    assert gt.iso2768(cls, nominal, "Distance") == Decimal(expected)
    assert gt.iso2768(cls, nominal, "Diameter") == Decimal(expected)


@pytest.mark.parametrize("cls,nominal", [
    ("v", "2"),         # v has no 0.5-3 cell
    ("f", "2500"),      # f has no >2000 cell
    ("m", "0,4"),       # below the table
    ("m", "4001"),      # above the table
    ("m", "0"), ("m", "-5"), ("m", ""), ("m", "abc"), ("m", "20 H7"),
])
def test_linear_table_returns_none_outside_its_cells(cls, nominal):
    assert gt.iso2768(cls, nominal, "Distance") is None


@pytest.mark.parametrize("cls,nominal,expected", [
    ("m", "0,5", "0.2"), ("m", "3", "0.2"), ("m", "4", "0.5"),
    ("m", "6", "0.5"), ("m", "6,1", "1"), ("m", "500", "1"),
    ("f", "2", "0.2"), ("c", "2", "0.4"), ("v", "10", "2"),
])
def test_radius_uses_the_radius_and_chamfer_table(cls, nominal, expected):
    """A radius is not a length: ISO 2768-1 gives it its own, coarser table,
    and R10 at class m is ±1, not the linear ±0.2."""
    assert gt.iso2768(cls, nominal, "Radius") == Decimal(expected)


def test_radius_below_the_table_is_none():
    assert gt.iso2768("m", "0,3", "Radius") is None


@pytest.mark.parametrize("char_type", ["Reference", "Theoretical", "Angle",
                                       "Flatness", "", "Note"])
def test_other_char_types_get_no_general_tolerance(char_type):
    assert gt.iso2768("m", "20", char_type) is None


def test_unknown_class_is_none():
    assert gt.iso2768("x", "20", "Distance") is None


# --- §1 the class pattern -------------------------------------------------------

@pytest.mark.parametrize("text,cls", [
    ("ISO 2768-mK", "m"), ("ISO 2768 mK", "m"), ("ISO 2768 - fH", "f"),
    ("2768-m", "m"), ("DIN ISO 2768-1 c", "c"), ("ISO 2768-1-m", "m"),
    ("ISO 2768-MK", "m"), ("Allgemeintoleranzen ISO 2768-vL", "v"),
])
def test_class_pattern_takes_the_first_letter_only(text, cls):
    assert gt.find_class(text) == cls


@pytest.mark.parametrize("text", [
    "22768-m", "2768 medium", "ISO 2768", "", "DIN 7168-m", "ISO 2768-x",
])
def test_class_pattern_rejects(text):
    """DIN 7168 is dropped completely by operator decision -- it must not
    match even though it names a class the same way."""
    assert gt.find_class(text) is None


# --- §3 the fit-notation guard -------------------------------------------------

@pytest.mark.parametrize("raw", ["Ø20H7", "20 h6", "12 js5", "Ø20 H7/g6",
                                 "3x5", "M8x1", "Ø10 ZA9", "Ø10 f01"])
def test_fit_guard_matches(raw):
    assert gt.has_fit_notation(raw)


@pytest.mark.parametrize("raw", ["20", "Ø20 ±0,1", "R5", "2x45°", "12,5",
                                 "4x M6", "Ø6,6 +0,2 0", "20 H 7", "", None])
def test_fit_guard_does_not_match(raw):
    assert not gt.has_fit_notation(raw)


# --- document class: sources, precedence, conflict ------------------------------

def _result(title=(), notes=(), chars=()):
    return ExtractionResult(
        characteristics=list(chars),
        title_block=list(title),
        notes=(NoteBlock(region=(0, 0, 1, 1),
                         notes=[Note(pos=101 + i, text_en=t)
                                for i, t in enumerate(notes)])
               if notes else None))


def _grid(label, value):
    return TitleField(label=label, value=value)


def _loose(value):
    # exactly what title_block.loose_text emits: no caption, no reasons
    return TitleField(label="", value=value)


def test_class_from_a_title_grid_cell():
    dc = gt.document_class(_result(title=[_grid("Allgemeintoleranz",
                                                "ISO 2768-mK")]))
    assert (dc.cls, dc.source, dc.conflict) == ("m", "title", False)


def test_class_in_the_caption_counts_too():
    dc = gt.document_class(_result(title=[_grid("Tolerances ISO 2768-f", "")]))
    assert (dc.cls, dc.source) == ("f", "title")


def test_class_from_notes():
    dc = gt.document_class(_result(notes=["General tolerances ISO 2768-c"]))
    assert (dc.cls, dc.source) == ("c", "notes")


def test_class_from_loose_text():
    dc = gt.document_class(_result(title=[_loose("ISO 2768-m")]))
    assert (dc.cls, dc.source) == ("m", "loose")


def test_a_captionless_grid_cell_is_title_not_loose():
    """read_title_block flags a grid cell with no caption 'missing caption';
    loose_text never does. That reason is the only stored difference."""
    cell = TitleField(label="", value="ISO 2768-m", needs_review=True,
                      review_reasons=["missing caption"])
    assert gt.document_class(_result(title=[cell])).source == "title"


def test_source_precedence_is_title_then_notes_then_loose():
    dc = gt.document_class(_result(title=[_loose("ISO 2768-m"),
                                          _grid("Tol", "ISO 2768-m")],
                                   notes=["ISO 2768-m"]))
    assert dc.source == "title"
    dc = gt.document_class(_result(title=[_loose("ISO 2768-m")],
                                   notes=["ISO 2768-m"]))
    assert dc.source == "notes"


def test_the_same_class_twice_is_not_a_conflict():
    dc = gt.document_class(_result(title=[_grid("Tol", "ISO 2768-mK")],
                                   notes=["Allgemeintoleranzen ISO 2768-m"]))
    assert (dc.cls, dc.conflict) == ("m", False)


def test_two_classes_anywhere_is_a_conflict_and_no_class():
    """Which one applies is not decidable from text: fill nothing."""
    dc = gt.document_class(_result(title=[_grid("Tol", "ISO 2768-m")],
                                   notes=["ISO 2768-f"]))
    assert (dc.cls, dc.conflict) == (None, True)
    assert dc.source == "title"


def test_two_classes_in_one_text_is_a_conflict():
    dc = gt.document_class(_result(notes=["ISO 2768-m, Kanten ISO 2768-c"]))
    assert (dc.cls, dc.conflict) == (None, True)


def test_no_class_anywhere():
    dc = gt.document_class(_result(title=[_grid("Material", "1.4301")]))
    assert (dc.cls, dc.source, dc.conflict) == (None, None, False)


def test_din_7168_is_not_a_source():
    dc = gt.document_class(_result(title=[_grid("Tol", "DIN 7168-m")]))
    assert dc.cls is None and dc.source is None


def test_notes_bilingual_and_raw_fields_are_searched():
    r = ExtractionResult(characteristics=[], notes=NoteBlock(
        region=(0, 0, 1, 1), notes=[Note(pos=101, text_de="ISO 2768-m"),
                                    Note(pos=102, raw_text="x")]))
    assert gt.document_class(r).cls == "m"
    r = ExtractionResult(characteristics=[], notes=NoteBlock(
        region=(0, 0, 1, 1), notes=[Note(pos=101, raw_text="ISO 2768-v")]))
    assert gt.document_class(r).cls == "v"


# --- the per-row fill -------------------------------------------------------------

def _dim(**kw):
    base = dict(pos=1, kind="dimension", char_type="Distance", nominal="20",
                raw_text="20", confidence=0.99)
    base.update(kw)
    return Characteristic(**base)


def test_fill_writes_the_parsers_own_symmetric_format():
    """parse_value('20 ±0,2') yields upper '0,2', lower '-0,2'. A filled row
    must be indistinguishable from that read, or scoring and the UI would
    treat a general tolerance as a different shape from a printed one."""
    from app.pipeline.parser import parse_value
    c = _dim()
    assert gt.fill_row(c, "m") == "filled"
    printed = parse_value("20 ±0,2")
    assert (c.upper_tol, c.lower_tol) == (printed.upper_tol, printed.lower_tol)
    assert c.tol_source == "general"


def test_fill_formats_without_trailing_zeros_and_with_a_comma():
    c = _dim(nominal="50")
    gt.fill_row(c, "f")
    assert (c.upper_tol, c.lower_tol) == ("0,15", "-0,15")
    c = _dim(nominal="500")
    gt.fill_row(c, "c")
    assert (c.upper_tol, c.lower_tol) == ("2", "-2")


def test_fill_radius_from_the_radius_table():
    c = _dim(char_type="Radius", nominal="10", raw_text="R10")
    assert gt.fill_row(c, "m") == "filled"
    assert (c.upper_tol, c.lower_tol) == ("1", "-1")


@pytest.mark.parametrize("kw,outcome", [
    (dict(kind="gdt"), "not_dimension"),
    (dict(kind="note"), "not_dimension"),
    (dict(char_type="Reference"), "reference"),
    (dict(nominal=""), "no_nominal"),
    (dict(upper_tol="0,1", lower_tol="-0,1"), "has_tolerance"),
    (dict(upper_tol="0,1"), "has_tolerance"),
    (dict(lower_tol="0"), "has_tolerance"),
    (dict(raw_text="Ø20 H7", char_type="Diameter"), "fit_guard"),
    (dict(nominal="0,3"), "table_none"),
    (dict(char_type="Theoretical"), "table_none"),
    (dict(char_type=""), "table_none"),
])
def test_rows_the_fill_must_not_touch(kw, outcome):
    c = _dim(**kw)
    before = c.model_dump()
    assert gt.fill_row(c, "m") == outcome
    assert c.model_dump() == before


def test_no_class_touches_nothing():
    c = _dim()
    before = c.model_dump()
    assert gt.fill_row(c, None) == "no_class"
    assert c.model_dump() == before


def test_fill_never_changes_flags_itself():
    """The flag pass decides needs_review AFTER the fill. If fill_row cleared
    flags, a row flagged for low confidence would be auto-accepted on a
    general tolerance it was never checked against."""
    c = _dim(needs_review=True, review_reasons=["low OCR confidence"])
    gt.fill_row(c, "m")
    assert c.needs_review and c.review_reasons == ["low OCR confidence"]


def test_apply_fills_every_eligible_row_of_one_document():
    chars = [_dim(pos=1), _dim(pos=2, nominal="5"),
             _dim(pos=3, upper_tol="0,1", lower_tol="-0,1"),
             _dim(pos=4, kind="gdt")]
    r = _result(title=[_grid("Tol", "ISO 2768-m")], chars=chars)
    outcomes = gt.apply_general_tolerance(r)
    assert outcomes == {1: "filled", 2: "filled", 3: "has_tolerance",
                        4: "not_dimension"}
    assert [c.upper_tol for c in r.characteristics] == ["0,2", "0,1", "0,1", ""]


def test_apply_with_a_conflict_fills_nothing():
    r = _result(title=[_grid("Tol", "ISO 2768-m")], notes=["ISO 2768-c"],
                chars=[_dim()])
    assert gt.apply_general_tolerance(r) == {1: "conflict"}
    assert r.characteristics[0].upper_tol == ""


def test_tol_source_defaults_to_none_so_old_dumps_do_not_lie():
    assert Characteristic(pos=1).tol_source is None
