from app.models import Characteristic

def test_characteristic_defaults():
    c = Characteristic(pos=5)
    assert c.pos == 5
    assert c.char_type == ""
    assert c.nominal == ""
    assert c.upper_tol == ""
    assert c.lower_tol == ""
    assert c.confidence == 0.0

def test_characteristic_roundtrip_dict():
    c = Characteristic(pos=1, char_type="Distance", nominal="1,2",
                       upper_tol="0,1", lower_tol="-0,1", confidence=0.9)
    d = c.model_dump()
    assert d["pos"] == 1 and d["nominal"] == "1,2"
    assert Characteristic(**d) == c

def test_characteristic_has_id_kind_source_defaults():
    c = Characteristic(pos=1)
    assert c.id == ""
    assert c.kind == ""
    assert c.source == "auto"

def test_characteristic_accepts_new_fields():
    c = Characteristic(pos=2, id="abc", kind="dimension", source="manual")
    assert c.id == "abc"
    assert c.kind == "dimension"
    assert c.source == "manual"

def test_characteristic_has_subtype_default_and_accepts_value():
    assert Characteristic(pos=1).subtype == ""
    c = Characteristic(pos=1, subtype="gdt")
    assert c.subtype == "gdt"


def test_characteristic_has_review_fields_defaults_and_values():
    c = Characteristic(pos=1)
    assert c.needs_review is False
    assert c.review_reasons == []
    c2 = Characteristic(pos=2, needs_review=True, review_reasons=["empty read"])
    assert c2.needs_review is True
    assert c2.review_reasons == ["empty read"]


def test_characteristic_review_reasons_are_independent_per_instance():
    a = Characteristic(pos=1)
    b = Characteristic(pos=2)
    a.review_reasons.append("missing nominal")
    assert b.review_reasons == []      # no shared mutable default


def test_note_model_defaults():
    from app.models import Note
    n = Note(pos=101)
    assert n.parent_pos is None
    assert n.sub_index is None
    assert n.text_en == "" and n.text_de == ""
    assert n.needs_review is False and n.review_reasons == []


def test_note_sub_bullet_carries_parent_and_sub_index():
    from app.models import Note
    n = Note(pos=1, parent_pos=101, sub_index=1, text_en="A", text_de="B")
    assert n.parent_pos == 101 and n.sub_index == 1


def test_note_block_model():
    from app.models import Note, NoteBlock
    nb = NoteBlock(region=(0, 0, 100, 100), notes=[Note(pos=101)])
    assert nb.region == (0, 0, 100, 100)
    assert len(nb.notes) == 1


def test_extraction_result_with_no_notes():
    from app.models import Characteristic, ExtractionResult
    r = ExtractionResult(characteristics=[Characteristic(pos=1)], notes=None)
    assert r.notes is None
    assert len(r.characteristics) == 1


def test_characteristic_has_optional_note_ref_pos():
    from app.models import Characteristic
    c = Characteristic(pos=1, note_ref_pos=101)
    assert c.note_ref_pos == 101
    assert Characteristic(pos=2).note_ref_pos is None


def test_mark_defaults():
    from app.models import Mark
    m = Mark(pos=101)
    assert m.pos == 101
    assert m.text_en == "" and m.text_de == "" and m.raw_text == ""
    assert m.needs_review is False
    assert m.review_reasons == []


def test_mark_review_reasons_are_independent_per_instance():
    from app.models import Mark
    a = Mark(pos=101)
    b = Mark(pos=102)
    a.review_reasons.append("unreadable")
    assert b.review_reasons == []


def test_markblock_holds_region_and_marks():
    from app.models import Mark, MarkBlock
    block = MarkBlock(region=(10, 20, 200, 100), marks=[Mark(pos=101, text_en="A")])
    assert block.region == (10, 20, 200, 100)
    assert len(block.marks) == 1
    assert block.marks[0].text_en == "A"


def test_extractionresult_marks_optional_default_none():
    from app.models import ExtractionResult
    r = ExtractionResult(characteristics=[])
    assert r.notes is None
    assert r.marks is None
