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
    gold = GoldCharacteristic(balloon=1, char_type="Wackiness", nominal="20")
    with pytest.raises(UnrenderableRow, match="Wackiness"):
        render_target(gold, "")
