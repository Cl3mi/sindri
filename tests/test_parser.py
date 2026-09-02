from app.pipeline.parser import parse_value, DIAMETER, RADIUS, FLATNESS, DISTANCE, MATERIAL, THEORETICAL, REFERENCE

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


def test_theoretical_boxed_value_nominal_only():
    c = parse_value("20", hint="theoretical")
    assert c.char_type == THEORETICAL
    assert c.nominal == "20"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_theoretical_period_decimal():
    c = parse_value("12.5", hint="theoretical")
    assert c.char_type == THEORETICAL
    assert c.nominal == "12,5"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_reference_parenthesized_nominal_only():
    c = parse_value("(1)")
    assert c.char_type == REFERENCE
    assert c.nominal == "1"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_reference_parenthesized_multi_digit():
    c = parse_value("(20)")
    assert c.char_type == REFERENCE
    assert c.nominal == "20"

def test_parenthetical_text_note_is_not_reference():
    c = parse_value("(optional)")
    assert c.char_type != REFERENCE


def test_gdt_position_frame():
    c = parse_value("⊕ Ø0.1 A", hint="gdt")
    assert c.char_type == "Position"
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "0"

def test_gdt_flatness_value_only_defaults_to_flatness():
    c = parse_value("0.1", hint="gdt")
    assert c.char_type == FLATNESS
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "0"

def test_flatness_hint_still_works_as_gdt_alias():
    c = parse_value("0,1", hint="flatness")
    assert c.char_type == FLATNESS
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"

def test_gdt_hint_on_plain_tolerance_text_is_not_position():
    # a normal tolerance string routed through the gdt hint must NOT match the
    # '+' sign as a position symbol; with no real symbol it defaults to Flatness
    c = parse_value("1,2 +0,1 -0,1", hint="gdt")
    assert c.char_type != "Position"


def test_two_positive_tolerances_keep_their_sign():
    """A shaft or hole fit prints both bounds positive -- "20 +0,3 +0,1" means a
    lower bound of PLUS 0,1, not minus. The old branch did
    `"-" + _norm(signed[1])`, which produced the malformed '-+0,1' AND inverted
    a bound the drawing states explicitly. Two defects in one expression.

    Found while building Rung 3's training targets: rendering such a row and
    parsing it back returned different fields, so the row could not round-trip.
    That made it visible as training data; in production it has been silently
    misparsing these callouts all along."""
    c = parse_value("20 +0,3 +0,1")
    assert c.nominal == "20"
    assert c.upper_tol == "0,3"
    assert c.lower_tol == "0,1"


def test_a_negative_lower_tolerance_is_still_negative():
    """The regression half: the common case must not move. Every committed
    measurement rests on it."""
    c = parse_value("20 +0,1 -0,1")
    assert c.upper_tol == "0,1" and c.lower_tol == "-0,1"


def test_an_unsigned_lower_bound_is_still_read_as_negative():
    """Deliberately unchanged. A drawing printing "20 +0,1 0,1" with no sign on
    the second bound means +0,1/-0,1 by convention, and _NUM_RE puts an unsigned
    token in `unsigned`, not `signed` -- so this path never reached the branch
    above and must keep behaving as it did."""
    c = parse_value("20 +0,2 0")
    assert c.upper_tol == "0,2" and c.lower_tol == "0"
