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
    # Reference (Klammermaß) rows carry no tolerance by definition -- not a
    # missing-tolerance fault.
    assert not pr.FLAG_RULES["no_tolerance"](
        _c(kind="dimension", char_type="Reference", nominal="20"))
    assert pr.FLAG_RULES["asymmetric_tol"](
        _c(kind="dimension", upper_tol="+0,2", lower_tol="-0,1"))
    assert not pr.FLAG_RULES["asymmetric_tol"](
        _c(kind="dimension", upper_tol="+0,1", lower_tol="-0,1"))


def test_asymmetric_tol_is_dimension_only_and_numeric():
    # GD&T rows carry upper_tol=<zone>, lower_tol="0" by construction -- that
    # is a zone width, not a sign asymmetry, so the rule must never fire on
    # a non-dimension row at all.
    gdt_row = _c(kind="gdt", raw_text="⌖ Ø0,1 A", upper_tol="0,1", lower_tol="0")
    assert not pr.FLAG_RULES["asymmetric_tol"](gdt_row)
    # Formatting differences (leading "+", trailing zero) must not read as
    # asymmetric: compare the values NUMERICALLY, not as strings.
    assert not pr.FLAG_RULES["asymmetric_tol"](
        _c(kind="dimension", upper_tol="+0,1", lower_tol="-0,10"))
    assert pr.FLAG_RULES["asymmetric_tol"](
        _c(kind="dimension", upper_tol="+0,2", lower_tol="-0,1"))


def test_apply_flag_rules_returns_one_reason_per_rule_that_fired():
    c = _c(kind="gdt", raw_text="0,05 A")
    assert pr.apply_flag_rules(c, ("nondim_kind", "gdt_guessed", "tall_box")) \
        == [pr.FLAG_REASONS["nondim_kind"], pr.FLAG_REASONS["gdt_guessed"]]


def test_every_flag_rule_has_a_human_reason():
    """review_reasons reaches a reviewer as a tooltip (app/static/js/table.js,
    viewer.js) -- a bare `rule:no_tolerance` there is jargon, not guidance."""
    assert set(pr.FLAG_REASONS) == set(pr.FLAG_RULES)
    for name, text in pr.FLAG_REASONS.items():
        assert isinstance(text, str) and text.strip(), name
        assert not text.startswith("rule:"), name


def test_contained_duplicate_drops_the_smaller_box_only():
    big = _c(pos=1, box=(0, 0, 100, 40), raw_text="20")
    small = _c(pos=2, box=(10, 5, 50, 35), raw_text="20")
    kept = pr.apply_drop_rules([big, small], ("contained_duplicate",))
    assert kept == [big]


def test_contained_duplicate_requires_a_content_relation():
    """Containment alone is not enough: an oversized phantom box (a detection
    artefact, a title-block frame) must not silently swallow every real
    callout that happens to fall inside it."""
    frame = _c(pos=1, box=(0, 0, 200, 200), raw_text="frame border")
    real = _c(pos=2, box=(10, 10, 50, 50), raw_text="20")
    kept = pr.apply_drop_rules([frame, real], ("contained_duplicate",))
    assert real in kept


def test_contained_duplicate_requires_matching_kind():
    """A note box's boilerplate text can happen to contain a real dimension's
    digits ("...SEE NOTE 5"), but a note and a dimension are never the same
    read -- so a cross-kind containment must never drop the real value."""
    note_box = _c(pos=1, box=(0, 0, 200, 50), kind="note",
                  raw_text="ISO 2768-m, SEE NOTE 5")
    real = _c(pos=2, box=(150, 10, 190, 40), kind="dimension", raw_text="5")
    kept = pr.apply_drop_rules([note_box, real], ("contained_duplicate",))
    assert real in kept


def test_contained_duplicate_substring_match_is_whole_token():
    """A bare digit-string containment check would match "5" inside "25" or
    "2768" -- require a WHOLE-TOKEN match so an unrelated number that happens
    to embed the small box's digits does not count as the same read."""
    big_25 = _c(pos=1, box=(0, 0, 100, 40), kind="dimension", raw_text="25")
    small_5 = _c(pos=2, box=(10, 5, 50, 35), kind="dimension", raw_text="5")
    kept = pr.apply_drop_rules([big_25, small_5], ("contained_duplicate",))
    assert small_5 in kept

    big_zone = _c(pos=1, box=(0, 0, 100, 40), kind="dimension",
                  raw_text="Ø20 ±0,1")
    small_frag = _c(pos=2, box=(10, 5, 50, 35), kind="dimension", raw_text="20")
    kept = pr.apply_drop_rules([big_zone, small_frag], ("contained_duplicate",))
    assert small_frag not in kept


def test_contained_duplicate_defers_to_the_more_confident_read():
    """contained_duplicate must not delete a callout that repeated_value_nearby
    would otherwise keep: if the smaller (contained) row is the MORE
    confident read, containment must not drop it, so the two rules together
    can settle on exactly one survivor instead of deleting both."""
    outer = _c(pos=1, box=(0, 0, 100, 40), kind="dimension", nominal="20",
               raw_text="20", confidence=0.80)
    inner = _c(pos=2, box=(10, 5, 50, 35), kind="dimension", nominal="20",
               raw_text="20", confidence=0.99)
    kept = pr.apply_drop_rules([outer, inner],
                               ("contained_duplicate", "repeated_value_nearby"))
    assert kept == [inner]


def test_contained_duplicate_chain_drops_only_up_to_the_largest():
    """A subset-of B subset-of C, all naming the same value: each smaller box
    is a duplicate of a still-larger one, but C has nothing bigger to defer
    to and survives."""
    c_box = _c(pos=3, box=(0, 0, 100, 100), nominal="20", raw_text="20")
    b_box = _c(pos=2, box=(10, 10, 60, 60), nominal="20", raw_text="20")
    a_box = _c(pos=1, box=(20, 20, 40, 40), nominal="20", raw_text="20")
    kept = pr.apply_drop_rules([a_box, b_box, c_box], ("contained_duplicate",))
    assert kept == [c_box]


def test_equal_boxes_are_never_both_dropped():
    """Ties keep both: `pos` is assigned by number_characteristics only AFTER
    the drop rules run, and `extract` discards its return value, so the list
    a rule sees is in raw detection order, not reading order -- no order is
    guaranteed twice. An order-dependent tie-break would make one call site
    disagree with another, and the offline price would stop being exact."""
    a = _c(pos=1, box=(0, 0, 100, 40))
    b = _c(pos=2, box=(0, 0, 100, 40))
    assert pr.apply_drop_rules([a, b], ("contained_duplicate",)) == [a, b]


def test_drop_rules_do_not_depend_on_pos():
    """pos is assigned strictly after the rules run, so a rule that reads
    `c.pos` (or relies on list order matching it) would disagree between the
    pipeline's pre-numbering call and a dump reloaded with pos already set."""
    chars = [_c(pos=i, box=(i * 7 % 50, i * 3 % 40, i * 7 % 50 + 30 + i,
                            i * 3 % 40 + 12), nominal=str(i % 3),
                raw_text=str(i % 3), kind=("note", "dimension")[i % 2],
                confidence=0.5 + (i % 4) / 10)
             for i in range(1, 12)]
    names = tuple(pr.DROP_RULES)
    base_ids = {id(c) for c in pr.apply_drop_rules(chars, names)}
    for c, new_pos in zip(chars, reversed(range(1, len(chars) + 1))):
        c.pos = new_pos
    renumbered_ids = {id(c) for c in pr.apply_drop_rules(chars, names)}
    assert renumbered_ids == base_ids


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


def test_repeated_value_nearby_requires_full_read_match():
    """GD&T rows all carry nominal '0' by construction (no dimension value);
    two stacked frames with different zones are different reads and must
    not collide just because their nominal happens to coincide."""
    a = _c(pos=1, box=(0, 0, 40, 20), kind="gdt", nominal="0",
           upper_tol="0,1", lower_tol="0", confidence=0.90)
    b = _c(pos=2, box=(0, 10, 40, 30), kind="gdt", nominal="0",
           upper_tol="0,2", lower_tol="0", confidence=0.95)
    assert not pr.DROP_RULES["repeated_value_nearby"](a, [a, b])
    assert not pr.DROP_RULES["repeated_value_nearby"](b, [a, b])


def test_repeated_value_nearby_is_non_transitive():
    """Dropping must chase a LOCAL maximum, not any more-confident neighbour
    transitively. row2 is more confident than row3 and within reach, but
    row2 is itself shadowed by row1 -- so row3 has no surviving neighbour to
    defer to and must be kept, even though naive transitivity would drop it."""
    row1 = _c(pos=1, box=(0, 10, 40, 30), nominal="20", confidence=0.99)
    row2 = _c(pos=2, box=(0, 60, 40, 80), nominal="20", confidence=0.90)
    row3 = _c(pos=3, box=(0, 110, 40, 130), nominal="20", confidence=0.80)
    kept = pr.apply_drop_rules([row1, row2, row3], ("repeated_value_nearby",))
    assert kept == [row1, row3]


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


def test_active_sets_are_exactly_what_the_result_doc_kept():
    """docs/plans/2026-09-25-policy-arms-result.md is the authority: each rule
    passed train, dev and test individually and jointly. Change both together
    or not at all."""
    assert pr.ACTIVE_FLAG_RULES == ("nondim_kind", "no_tolerance")
    assert pr.ACTIVE_DROP_RULES == ("contained_duplicate",)


# --- the phantom drops (docs/plans/2026-10-06-phantom-drops-registration.md) ---
#
# Precision at any recall: each fires ONLY on a row that would ship unflagged.
# A flagged row is not delivered, so dropping it could only cost recall.

PHANTOM_DROPS = ("conf_below_090", "conf_below_095", "conf_below_099",
                 "material_kind", "tight_cluster")


def test_phantom_drops_are_registered_and_not_active():
    for name in PHANTOM_DROPS:
        assert name in pr.DROP_RULES
        assert name not in pr.ACTIVE_DROP_RULES


def test_confidence_drops_are_strict_thresholds():
    for name, t in (("conf_below_090", 0.90), ("conf_below_095", 0.95),
                    ("conf_below_099", 0.99)):
        rule = pr.DROP_RULES[name]
        assert rule(_c(confidence=t - 0.001), [])
        assert not rule(_c(confidence=t), [])


def test_material_drop_is_material_only():
    assert pr.DROP_RULES["material_kind"](_c(kind="material"), [])
    assert not pr.DROP_RULES["material_kind"](_c(kind="dimension"), [])


def test_tight_cluster_is_a_centre_within_50_px():
    a = _c(pos=1, box=(0, 0, 100, 40))           # centre (50, 20)
    near = _c(pos=2, box=(30, 0, 130, 40))       # centre (80, 20): 30 px
    far = _c(pos=3, box=(60, 0, 160, 40))        # centre (110, 20): 60 px
    assert pr.DROP_RULES["tight_cluster"](a, [a, near])
    assert pr.DROP_RULES["tight_cluster"](near, [a, near])
    assert not pr.DROP_RULES["tight_cluster"](a, [a, far])
    assert not pr.DROP_RULES["tight_cluster"](a, [a])


def test_no_phantom_drop_touches_a_flagged_row():
    flagged = dict(needs_review=True, review_reasons=["no tolerance read"])
    a = _c(pos=1, confidence=0.5, kind="material", **flagged)
    b = _c(pos=2, box=(5, 0, 105, 40), confidence=0.5, kind="material",
           **flagged)
    for name in PHANTOM_DROPS:
        assert not pr.DROP_RULES[name](a, [a, b]), name


def test_a_regionless_row_is_never_a_cluster_member():
    a = _c(pos=1)
    manual = Characteristic(pos=2, target_region=None)
    assert not pr.DROP_RULES["tight_cluster"](a, [a, manual])
    assert not pr.DROP_RULES["tight_cluster"](manual, [a, manual])
