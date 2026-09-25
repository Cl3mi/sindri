"""Gold-free rules over a finished Characteristic. The pipeline and the eval
counterfactual call the SAME functions, which is what makes the offline price
exact rather than an estimate."""
import random

from app.models import Characteristic
from app.pipeline import policy_rules as pr


def _c(pos=1, box=(0, 0, 100, 40), **kw):
    return Characteristic(pos=pos, target_region=box, **kw)


def test_flag_rules_fire_on_what_they_name():
    assert pr.FLAG_RULES["nondim_kind"](_c(kind="theoretical"))
    assert not pr.FLAG_RULES["nondim_kind"](_c(kind="dimension"))
    assert pr.FLAG_RULES["gdt_guessed"](_c(kind="gdt", raw_text="0,05 A"))
    assert not pr.FLAG_RULES["gdt_guessed"](_c(kind="gdt", raw_text="⌖ Ø0,1 A"))
    assert pr.FLAG_RULES["tall_box"](_c(box=(0, 0, 50, 80)))
    assert not pr.FLAG_RULES["tall_box"](_c(box=(0, 0, 50, 79)))
    assert pr.FLAG_RULES["diameter_sign"](_c(raw_text="Ø20"))
    assert pr.FLAG_RULES["multiline"](_c(raw_text="20\n+0,1"))
    assert not pr.FLAG_RULES["multiline"](_c(raw_text="20\n"))
    assert pr.FLAG_RULES["no_tolerance"](_c(kind="dimension", nominal="20"))
    assert not pr.FLAG_RULES["no_tolerance"](
        _c(kind="dimension", nominal="20", upper_tol="0,1"))
    assert pr.FLAG_RULES["asymmetric_tol"](
        _c(upper_tol="+0,2", lower_tol="-0,1"))
    assert not pr.FLAG_RULES["asymmetric_tol"](
        _c(upper_tol="+0,1", lower_tol="-0,1"))


def test_apply_flag_rules_returns_one_reason_per_rule_that_fired():
    c = _c(kind="gdt", raw_text="0,05 A")
    assert pr.apply_flag_rules(c, ("nondim_kind", "gdt_guessed", "tall_box")) \
        == ["rule:nondim_kind", "rule:gdt_guessed"]


def test_contained_duplicate_drops_the_smaller_box_only():
    big = _c(pos=1, box=(0, 0, 100, 40), raw_text="20")
    small = _c(pos=2, box=(10, 5, 50, 35), raw_text="20")
    kept = pr.apply_drop_rules([big, small], ("contained_duplicate",))
    assert kept == [big]


def test_equal_boxes_are_never_both_dropped():
    """Ties keep both: an order-dependent tie-break would make the pipeline
    (pre-numbering order) and the dump (numbered order) disagree, and the
    offline price would stop being exact."""
    a = _c(pos=1, box=(0, 0, 100, 40))
    b = _c(pos=2, box=(0, 0, 100, 40))
    assert pr.apply_drop_rules([a, b], ("contained_duplicate",)) == [a, b]


def test_drop_rules_are_order_independent():
    rng = random.Random(7)
    chars = [_c(pos=i, box=(i * 7 % 50, i * 3 % 40, i * 7 % 50 + 30 + i,
                            i * 3 % 40 + 12), nominal=str(i % 3),
                raw_text=str(i % 3), kind=("note", "dimension")[i % 2],
                confidence=0.5 + (i % 4) / 10)
             for i in range(1, 12)]
    names = tuple(pr.DROP_RULES)
    base = {c.pos for c in pr.apply_drop_rules(chars, names)}
    for _ in range(5):
        shuffled = chars[:]
        rng.shuffle(shuffled)
        assert {c.pos for c in pr.apply_drop_rules(shuffled, names)} == base


def test_other_drop_rules():
    assert pr.DROP_RULES["empty_read"](_c(raw_text="  "), [])
    assert pr.DROP_RULES["no_digit"](_c(kind="dimension", raw_text="A-A"), [])
    assert not pr.DROP_RULES["no_digit"](_c(kind="dimension", raw_text="R5"), [])
    assert pr.DROP_RULES["theoretical_no_nominal"](_c(kind="theoretical"), [])
    assert pr.DROP_RULES["note_kind"](_c(kind="note"), [])
    assert pr.DROP_RULES["theoretical_kind"](_c(kind="theoretical",
                                                nominal="20"), [])
    near_hi = _c(pos=1, box=(0, 0, 40, 20), nominal="20", confidence=0.99)
    near_lo = _c(pos=2, box=(0, 30, 40, 50), nominal="20", confidence=0.90)
    far = _c(pos=3, box=(900, 900, 940, 920), nominal="20", confidence=0.5)
    chars = [near_hi, near_lo, far]
    assert pr.DROP_RULES["repeated_value_nearby"](near_lo, chars)
    assert not pr.DROP_RULES["repeated_value_nearby"](near_hi, chars)
    assert not pr.DROP_RULES["repeated_value_nearby"](far, chars)


def test_nothing_is_active_until_a_rule_is_kept():
    """Behaviour changes only land when the plan's keep/revert rule keeps them."""
    assert pr.ACTIVE_FLAG_RULES == ()
    assert pr.ACTIVE_DROP_RULES == ()
