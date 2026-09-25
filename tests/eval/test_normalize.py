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


def test_a_named_linear_dimension_is_a_distance():
    """The map already sends Maß/Abstand/Länge to Distance, and these are the
    same category with the entries missing: a width, height, depth or thickness
    IS a linear distance.

    Why it matters more than tidiness: the parser emits Distance for any bare
    number, and a callout printing "20" carries NO information about whether
    that 20 is a width or a length -- that lives in the drawing's geometry, not
    in the crop the read stage sees. So scoring these as char_type errors
    charges the pipeline for information it cannot possibly recover, which
    inflates wrong:char_type (115 of 308 matched pairs) with rows no model or
    prompt could ever fix."""
    for label in ("Breite", "width", "Höhe", "Hoehe", "height", "Tiefe",
                  "depth", "Dicke", "thickness"):
        assert char_type_equal("Distance", label), label


def test_a_german_geometric_tolerance_name_maps_to_the_parser_constant():
    """parser._GDT_SYMBOLS emits English constants (Circularity, Parallelism)
    while gold labels this corpus in German, so the two could never compare
    equal. Only the pairs where the parser HAS the constant and the German word
    is unambiguous are mapped -- see the module comment for the ones deliberately
    left alone."""
    assert char_type_equal("Circularity", "Rundheit")
    assert char_type_equal("Circularity", "roundness")
    assert char_type_equal("Parallelism", "Parallelität")
    assert char_type_equal("Parallelism", "Parallelitaet")


def test_every_synonym_value_is_also_a_key():
    """The map's load-bearing invariant, and it bit while adding Circularity.

    canon_char_type returns the mapped value VERBATIM and leaves an unmapped
    label merely casefolded. So if "rundheit" -> "Circularity" but "circularity"
    is not itself a key, the gold side canonicalises to "Circularity" and the
    parser side to "circularity", and the two never compare equal -- a synonym
    entry that silently does nothing. Every value must round-trip to itself."""
    from app.eval.normalize import CHAR_TYPE_SYNONYMS, canon_char_type

    for label, canon in CHAR_TYPE_SYNONYMS.items():
        assert canon_char_type(canon) == canon, (
            f"{label!r} -> {canon!r}, but {canon!r} canonicalises to "
            f"{canon_char_type(canon)!r}, so the entry cannot ever match")


def test_the_synonym_map_never_widens_which_gold_rows_are_scored():
    """The comparability guard for this whole change. _DIMENSION_WORDS is built
    from the synonym map's own keys, and char_type_kind decides which gold rows
    are scored at all -- so a NEW key that was not already a dimension word
    would silently move gold rows into the denominator, change n_gold, and make
    the re-scored report incomparable with every report before it.

    MatchParams has no fingerprint for the synonym map, so compare_runs cannot
    catch that. This test is the only thing standing in its place."""
    from app.eval.normalize import CHAR_TYPE_SYNONYMS, _DIMENSION_WORDS

    # Every key must already have been a dimension word before it was a synonym.
    # Frozen membership list, so adding a key that widens scoring fails here.
    assert set(CHAR_TYPE_SYNONYMS) <= set(_DIMENSION_WORDS)
    assert len(_DIMENSION_WORDS) == 59, (
        f"_DIMENSION_WORDS changed size to {len(_DIMENSION_WORDS)}; a synonym "
        f"key that is not already a dimension word changes which gold rows are "
        f"scored, so n_gold moves and the report is not comparable")


def test_a_qualified_gold_label_canonicalises_by_word_containment():
    """The measured cause of 68 of dev's 115 char_type disagreements: the map
    was matched on the WHOLE label while char_type_kind matches on word
    containment, so a compound gold label is scored as a dimension yet can
    never equal the parser's bare constant.

    char_type_kind's own docstring already argues for containment ("so
    'Diameter MIN' and 'Sym. 0,05 zu C' classify as dimensions"); this makes
    the two functions read a label the same way instead of two ways."""
    assert char_type_equal("Diameter", "Diameter MIN")
    assert char_type_equal("Flatness", "Ebenheit 0,05 zu C")
    assert char_type_equal("Distance", "Maß (Hilfsmaß)")


def test_containment_refuses_an_ambiguous_label_rather_than_picking_one():
    """The over-crediting guard. Two rows on dev carry a label whose words point
    at two different characteristic names, and there is no principled way to
    choose -- so those must stay unequal rather than be resolved arbitrarily.
    Silently picking the first would credit the pipeline for a read that may be
    wrong, which is exactly what a scoring change with no compare_runs
    fingerprint must never do."""
    assert not char_type_equal("Diameter", "Durchmesser oder Radius")
    assert not char_type_equal("Radius", "Durchmesser oder Radius")


def test_containment_can_only_ever_make_a_row_correct_never_break_one():
    """Why this relaxation is safe to apply to a frozen baseline: it is
    monotone. Exact matches still match, so no row that compared equal before
    can compare unequal now -- the re-score can lose no correct row."""
    assert char_type_equal("Diameter", "Durchmesser")
    assert char_type_equal("Distance", "Distance")
    assert not char_type_equal("Radius", "Diameter")
    assert not char_type_equal("Distance", "STANZGRATSEITE INNEN")
