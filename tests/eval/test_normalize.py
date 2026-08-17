from app.eval.normalize import canon_value, values_equal, char_type_equal


def test_canon_value_normalizes_decimal_comma_and_trailing_zeros():
    assert canon_value("1,20") == canon_value("1.2") == "1.2"
    assert canon_value("+0,1") == "0.1"
    assert canon_value("-0,10") == "-0.1"
    assert canon_value(1.2) == "1.2"          # Excel float cell
    assert canon_value(20) == "20"            # Excel int cell
    assert canon_value(" Ø ") == "ø"          # non-numeric: casefolded, stripped
    assert canon_value("Infinity") == "infinity"   # non-finite -> string path
    assert canon_value("NaN") == "nan"             # not IEEE NaN semantics


def test_values_equal_numeric_and_string_paths():
    assert values_equal("1,2", "1.20")
    assert values_equal("-0,05", -0.05)
    assert not values_equal("1,2", "1,3")
    assert values_equal("MAX", " max ")
    assert not values_equal("", "0")          # empty is NOT zero (policy)
    assert values_equal("", "")
    assert values_equal("", None)


def test_char_type_kind_separates_measurements_from_verbal_requirements():
    """The sheets mix dimensional characteristics with verbal requirements that
    were never ballooned. Scoring the latter as missed callouts would let note
    text dominate the review-cost metric."""
    from app.eval.normalize import char_type_kind
    for dimensional in ("Abstand", "Distance", "Diameter", "Durchmesser",
                        "Diameter MIN", "Perpendicularity", "Surface shape",
                        "Sym. 0,05 zu C", "Ebenheit", "Radius"):
        assert char_type_kind(dimensional) == "dimension", dimensional
    for verbal in ("STANZGRATSEITE",
                   "MESSPUNKT FUER SCHICHTDICKE",
                   "KEINE STREIFENANBINDUNG IN DIESEM BEREICH ZULAESSIG",
                   "SCHNITTKANTEN BLANK ZULAESSIG",
                   "BESCHRIFTUNG [NEST-NR.]"):
        assert char_type_kind(verbal) == "note", verbal
    assert char_type_kind("") == "unknown"


def test_char_type_equal_uses_synonyms_case_insensitively():
    assert char_type_equal("Diameter", "durchmesser")
    assert char_type_equal("Distance", "Maß")
    assert char_type_equal("Radius", "Radius")
    assert not char_type_equal("Radius", "Diameter")
    assert char_type_equal("", "")
