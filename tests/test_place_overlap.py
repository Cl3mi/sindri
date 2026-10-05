"""Balloons must not be drawn on top of the values they annotate.

`place_balloons` offsets every marker up-and-left by one fixed gap and clamps it
to the page. Nothing checks what is already there, and place.py's own docstring
concedes it: "the human fixes overlaps by dragging in review". On a dense
drawing that puts balloons over neighbouring callouts and over each other, which
is the most visible defect in the delivered PDF -- a reviewer sees an obscured
dimension long before they notice a review-cost decimal.

This is measurement-neutral by construction. `balloon_xy` appears NOWHERE in
app/eval: scoring takes every position from `target_region` via
score._center_pt. So none of the committed numbers (170.05, 176.40, 172.00,
174.05) can move, and improving the drawing cannot flatter the metric.
"""
from app.models import Characteristic
from app.pipeline.ballooned_pdf import _RADIUS
from app.pipeline.place import place_balloons

# The marker is drawn as a circle of _RADIUS PDF points; at 300 dpi that is
# _RADIUS * 300/72 pixels, and balloon_xy is its centre in image space.
_R_PX = _RADIUS * 300.0 / 72.0


def _char(box, pos=1):
    return Characteristic(pos=pos, target_region=box, raw_text="x")


def _hits_box(xy, box, r=_R_PX):
    """Does a marker centred at `xy` overlap the axis-aligned `box`?"""
    x, y = xy
    x0, y0, x1, y1 = box
    nx, ny = max(x0, min(x, x1)), max(y0, min(y, y1))
    return (x - nx) ** 2 + (y - ny) ** 2 < r ** 2


def test_a_balloon_is_not_placed_on_another_callout():
    """The defect a client sees first. Two callouts stacked a little more than
    one gap apart: the lower one's balloon lands squarely on the upper one's
    ink, hiding the very value it is pointing at."""
    # lower's marker goes to (500-58, 400-58) = (442, 342), which lands inside
    # upper's box. gap is round(14 pt * 300/72) = 58 px; the marker radius is
    # 9 pt = 37.5 px.
    upper = _char((400.0, 320.0, 700.0, 360.0), pos=1)
    lower = _char((500.0, 400.0, 700.0, 440.0), pos=2)

    place_balloons([upper, lower], dpi=300)

    assert not _hits_box(lower.balloon_xy, upper.target_region), (
        f"balloon {lower.balloon_xy} sits on the callout at "
        f"{upper.target_region}")


def test_two_balloons_do_not_land_on_each_other():
    """Two callouts on the same band, a gap apart, get markers at the same
    place. Overlapping circles read as one balloon with an unreadable number."""
    # markers land at (442, 242) and (502, 242): 60 px apart, against the
    # 75 px needed for two 37.5 px circles to clear each other.
    a = _char((500.0, 300.0, 555.0, 330.0), pos=1)
    b = _char((560.0, 300.0, 615.0, 330.0), pos=2)

    place_balloons([a, b], dpi=300)

    ax, ay = a.balloon_xy
    bx, by = b.balloon_xy
    assert (ax - bx) ** 2 + (ay - by) ** 2 >= (2 * _R_PX) ** 2, (
        f"balloons {a.balloon_xy} and {b.balloon_xy} overlap")


def test_an_isolated_callout_keeps_the_original_offset():
    """No regression for the common case. Where nothing is in the way the
    marker must stay exactly where it has always gone -- up and left by one
    gap -- so this changes crowded drawings only."""
    c = _char((500.0, 400.0, 600.0, 440.0))

    place_balloons([c], dpi=300, gap_pt=14.0)

    gap = round(14.0 * 300.0 / 72.0)
    assert c.balloon_xy == (500.0 - gap, 400.0 - gap)


def test_a_balloon_never_leaves_the_page():
    """Avoidance must not push a marker off-sheet: a balloon outside the page
    is worse than one that overlaps, because it is not drawn at all."""
    corner = _char((5.0, 5.0, 60.0, 30.0), pos=1)
    blocker = _char((0.0, 0.0, 200.0, 200.0), pos=2)

    place_balloons([corner, blocker], dpi=300, margin=10)

    x, y = corner.balloon_xy
    assert x >= 10 and y >= 10, f"balloon {corner.balloon_xy} left the page"


def test_the_placement_radius_matches_the_radius_actually_drawn():
    """place.py duplicates the radius rather than importing it, so that pure
    geometry does not drag fitz into every caller. Duplication is only safe
    while something checks it: if ballooned_pdf ever draws a bigger circle than
    placement reserves, every avoidance decision is computed against the wrong
    footprint and balloons overlap again with no test failing."""
    from app.pipeline.place import _RADIUS_PT
    assert _RADIUS_PT == _RADIUS
