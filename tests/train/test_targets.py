"""The gold -> target renderer, verified by round-tripping through the parser.

This is the load-bearing property of Rung 3's training data: a target is correct
exactly when parse_value maps it back to the gold row it came from. That is
checkable on synthetic gold, so none of it needs client data -- which matters,
because the real targets ARE client values and can never be looked at."""
import pytest

from app.eval.models import GoldCharacteristic
from app.eval.normalize import char_type_equal, values_equal
from app.pipeline.parser import parse_value
from app.train.targets import UnrenderableRow, render_target

# One case per shape the corpus contains, with the parser hint the detector
# supplies for it at inference time (extract._HINTS maps detector kind -> hint).
SHAPES = [
    ("plain distance",     dict(char_type="Distance", nominal="20"), ""),
    ("symmetric tol",      dict(char_type="Distance", nominal="5,5",
                                upper_tol="0,1", lower_tol="-0,1"), ""),
    ("diameter",           dict(char_type="Diameter", nominal="20",
                                upper_tol="0,1", lower_tol="-0,1"), ""),
    ("diameter one-sided", dict(char_type="Diameter", nominal="6,6",
                                upper_tol="0,2", lower_tol="0"), ""),
    ("radius max",         dict(char_type="Radius", nominal="0,5",
                                upper_tol="0"), ""),
    ("flatness",           dict(char_type="Flatness", nominal="0",
                                upper_tol="0,05", lower_tol="0"), "gdt"),
    ("position",           dict(char_type="Position", nominal="0",
                                upper_tol="0,1", lower_tol="0"), "gdt"),
    ("theoretical",        dict(char_type="Theoretical", nominal="20"),
                           "theoretical"),
]


@pytest.mark.parametrize("name,fields,hint", SHAPES, ids=[s[0] for s in SHAPES])
def test_every_shape_round_trips_through_the_parser(name, fields, hint):
    """The whole property in one assertion: whatever we render must parse back to
    the row it came from. If it does not, we would be training the model toward a
    value gold does not hold."""
    gold = GoldCharacteristic(balloon=1, **fields)
    text = render_target(gold, hint)
    back = parse_value(text, hint=hint)
    assert char_type_equal(back.char_type, gold.char_type), text
    for f in ("nominal", "upper_tol", "lower_tol"):
        assert values_equal(getattr(back, f), getattr(gold, f)), (f, text)


def test_tolerances_render_in_the_explicit_two_sided_form():
    """Not "±0,1". The parser's ± branch derives the lower bound as the negated
    upper, so ± cannot express "+0,2 0" or a one-sided tolerance at all. One form
    everywhere keeps the renderer total and the target distribution consistent,
    which is what the model is learning."""
    gold = GoldCharacteristic(balloon=1, char_type="Distance", nominal="5,5",
                              upper_tol="0,1", lower_tol="-0,1")
    text = render_target(gold, "")
    assert "±" not in text
    assert "+0,1" in text and "-0,1" in text


def test_a_diameter_keeps_its_symbol_because_char_type_is_scored():
    """char_type is one of the four fields _compare_fields requires, and the
    parser infers Diameter only from a leading Ø. Dropping the symbol would
    train the model to lose a scored field."""
    gold = GoldCharacteristic(balloon=1, char_type="Diameter", nominal="20")
    assert render_target(gold, "").startswith("Ø")


def test_an_unrenderable_row_raises_rather_than_approximating():
    """A silent approximation would train the model toward a value gold does not
    hold, and the COUNT of unrenderable rows is itself a finding worth having."""
    gold = GoldCharacteristic(balloon=1, char_type="Distance", nominal="")
    with pytest.raises(UnrenderableRow, match="nominal"):
        render_target(gold, "")


def test_an_unknown_char_type_raises_instead_of_guessing():
    """Asserts on the reason slug, not on the message text. The message used to
    quote the offending label back, and a gold char_type is the client's own
    string -- an exception that echoes it can carry client text anywhere the
    traceback goes. The slug says as much for routing and leaks nothing."""
    gold = GoldCharacteristic(balloon=1, char_type="Wackiness", nominal="20")
    with pytest.raises(UnrenderableRow) as exc:
        render_target(gold, "")
    assert exc.value.reason == "char_type"
    assert "Wackiness" not in str(exc.value)


# --- gold speaks German, and the fixtures above did not -------------------
# Every SHAPES case above builds gold with the PARSER's vocabulary ("Distance",
# "Diameter"). Real gold uses the client's: "Durchmesser", "Maß", "Ebenheit",
# and compound labels like "Diameter MIN". That gap is why the first train-split
# build produced 192 pairs and 790 unrenderable rows -- 80% of every matched row
# on the split, discarded because the label was not already an English constant.

def _gold(**kw):
    fields = dict(balloon=1)
    fields.update(kw)
    return GoldCharacteristic(**fields)


@pytest.mark.parametrize("label", ["Durchmesser", "durchmesser", "Diameter MIN"])
def test_a_german_or_qualified_diameter_still_renders_with_its_symbol(label):
    """char_type must be canonicalised through the SAME synonym map scoring
    uses, or the renderer and the metric disagree about what the row even is.
    The Ø is not decoration: parser.py infers Diameter from exactly that leading
    symbol, so dropping the row loses a training example for the single field
    Phase A found most broken."""
    text = render_target(_gold(char_type=label, nominal="20",
                               upper_tol="0,1", lower_tol="-0,1"), "")
    assert text.startswith("Ø"), text
    back = parse_value(text, hint="")
    assert char_type_equal(back.char_type, label), (text, back.char_type)
    assert values_equal(back.nominal, "20")


def test_a_german_linear_dimension_renders():
    for label in ("Maß", "Abstand", "Breite"):
        text = render_target(_gold(char_type=label, nominal="5,5"), "")
        back = parse_value(text, hint="")
        assert char_type_equal(back.char_type, label), (label, text)
        assert values_equal(back.nominal, "5,5")


def test_a_german_geometric_label_renders_under_the_gdt_hint():
    """"Ebenheit 0,05 zu C" is a Flatness row wearing a datum reference. It
    canonicalises to Flatness by word containment, exactly as char_type_kind
    already reads it."""
    text = render_target(_gold(char_type="Ebenheit 0,05 zu C", nominal="0",
                               upper_tol="0,05", lower_tol="0"), "gdt")
    back = parse_value(text, hint="gdt")
    assert char_type_equal(back.char_type, "Ebenheit"), text
    assert values_equal(back.upper_tol, "0,05")


def test_a_geometric_row_without_the_gdt_hint_is_unrenderable():
    """Measured, not assumed: parse_value('⏥ 0,05', hint='') returns
    Distance/0,05, NOT Flatness. So when the detector did not call this callout
    gdt, no text exists that parses back to a geometric char_type -- the parser
    only reaches those constants through the hint.

    Emitting the GD&T rendering anyway would put a target in the training set
    that the pipeline provably cannot reproduce, which is worse than dropping
    the row. This is the branch canonicalisation makes reachable: before it,
    German geometric labels never matched _GDT_SYMBOL at all."""
    with pytest.raises(UnrenderableRow) as exc:
        render_target(_gold(char_type="Ebenheit", nominal="0",
                            upper_tol="0,05", lower_tol="0"), "")
    assert exc.value.reason == "gdt_no_hint"


def test_every_unrenderable_row_carries_a_values_blind_reason():
    """The count alone said 790 and nothing about WHY. These slugs are the only
    thing that can be reported about a rejected row -- the label itself is the
    client's text and can never be printed."""
    cases = [
        ("no_nominal", dict(char_type="Maß", nominal=""), ""),
        ("char_type", dict(char_type="STANZGRATSEITE INNEN", nominal="20"), ""),
        ("gdt_no_zone", dict(char_type="Ebenheit", nominal="0",
                             upper_tol=""), "gdt"),
        ("gdt_no_hint", dict(char_type="Position", nominal="0",
                             upper_tol="0,1"), ""),
    ]
    for expected, fields, hint in cases:
        with pytest.raises(UnrenderableRow) as exc:
            render_target(_gold(**fields), hint)
        assert exc.value.reason == expected, (fields, exc.value.reason)


def test_render_target_verifies_its_own_output_against_the_parser():
    """The module promises "a target is correct exactly when parse_value maps it
    back to the row it came from". That was TESTED on eight shapes and never
    ENFORCED, so any shape nobody thought of produced a silently wrong target --
    which is worse than a dropped row, because it trains the model to emit text
    the parser maps to the wrong fields.

    Three such shapes exist and were found by probing, not by reasoning:

      lower-only tolerance   "5 -0,1"      -> parser reads upper='-0,1'
      two positive tolerances "5 +0,3 0,1" -> parser drops the lower entirely
      negative nominal        "-3"         -> parser copies it into upper too

    None is a formatting problem: there is no text that yields upper='' with a
    lower set, and '5 +0,3 +0,1' parses to lo='-+0,1'. They are unrenderable
    with this parser, so they must raise."""
    unrenderable = [
        ("lower-only tolerance", dict(char_type="Maß", nominal="5",
                                      lower_tol="-0,1")),
        ("two positive tolerances", dict(char_type="Maß", nominal="5",
                                         upper_tol="0,3", lower_tol="0,1")),
        ("negative nominal", dict(char_type="Maß", nominal="-3")),
    ]
    for name, fields in unrenderable:
        with pytest.raises(UnrenderableRow) as exc:
            render_target(_gold(**fields), "")
        assert exc.value.reason == "not_round_tripping", (name, exc.value.reason)


def test_the_self_check_uses_the_same_predicate_as_scoring():
    """char_type is compared only when gold HAS one, and the three value fields
    always -- exactly score._compare_fields. A stricter check here would reject
    rows the metric would have credited; a looser one would admit targets the
    metric will score as errors."""
    text = render_target(_gold(char_type="", nominal="20"), "")
    assert text == "20"


@pytest.mark.parametrize("name,fields,hint", SHAPES, ids=[s[0] for s in SHAPES])
def test_the_self_check_admits_every_shape_the_corpus_contains(name, fields,
                                                               hint):
    """The regression half: enforcing the property must not start rejecting the
    shapes that were already correct."""
    assert render_target(GoldCharacteristic(balloon=1, **fields), hint)
