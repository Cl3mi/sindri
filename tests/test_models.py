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
