from app.pipeline.parser import parse_value, DIAMETER, RADIUS, FLATNESS, DISTANCE, MATERIAL

def test_distance_stacked_tolerance():
    c = parse_value("1,2 +0,1 -0,1")
    assert c.char_type == DISTANCE
    assert c.nominal == "1,2"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_distance_multiline():
    c = parse_value("3,2\n+0,05\n-0,05")
    assert c.char_type == DISTANCE
    assert c.nominal == "3,2"
    assert c.upper_tol == "0,05"
    assert c.lower_tol == "-0,05"

def test_diameter_symbol():
    c = parse_value("Ø7 +0,1 -0,1")
    assert c.char_type == DIAMETER
    assert c.nominal == "7"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_diameter_misread_O_prefix():
    c = parse_value("O12 +0,05 -0,05")
    assert c.char_type == DIAMETER
    assert c.nominal == "12"

def test_radius_max():
    c = parse_value("R0,5 MAX")
    assert c.char_type == RADIUS
    assert c.nominal == "0,5"
    assert c.upper_tol == "0"
    assert c.lower_tol == ""

def test_flatness_symbol():
    c = parse_value("0,1", hint="flatness")
    assert c.char_type == FLATNESS
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"

def test_symmetric_tolerance():
    c = parse_value("5 ±0,1")
    assert c.nominal == "5"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_material_text():
    c = parse_value("Cu-ETP_R240", hint="material")
    assert c.char_type == MATERIAL
    assert c.nominal == "Cu-ETP_R240"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_plain_distance_no_tol():
    c = parse_value("7,2")
    assert c.char_type == DISTANCE
    assert c.nominal == "7,2"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_period_decimal_diameter_stacked():
    c = parse_value("Ø6.6 +0.2 0")
    assert c.char_type == DIAMETER
    assert c.nominal == "6,6"
    assert c.upper_tol == "0,2"
    assert c.lower_tol == "0"

def test_period_decimal_distance_symmetric_pair():
    c = parse_value("15 +0.05 -0.05")
    assert c.nominal == "15"
    assert c.upper_tol == "0,05"
    assert c.lower_tol == "-0,05"

def test_period_decimal_symmetric_pm():
    c = parse_value("5 ±0.1")
    assert c.nominal == "5"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_period_decimal_max_zero_lower_tol():
    c = parse_value("Ø6.6 +0.2 0.0")
    assert c.upper_tol == "0,2"
    assert c.lower_tol == "0"

def test_negative_single_tol_does_not_trigger_max_zero():
    c = parse_value("10 -0.5 0")
    # the single signed token is negative -> MAX-zero rule must NOT fire
    assert c.lower_tol != "0"
