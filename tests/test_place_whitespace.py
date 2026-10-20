"""Balloons go into blank space; leaders may be long to get there."""
import numpy as np

from app.models import Characteristic
from app.pipeline.ballooned_pdf import _RADIUS
from app.pipeline.place import InkMap, place_balloons

DPI = 300
R = _RADIUS * DPI / 72.0


def _char(box, pos=1):
    return Characteristic(pos=pos, target_region=box, raw_text="x")


def _blank(h=900, w=1200):
    return np.zeros((h, w), dtype=bool)


def _circle_ink(dark, xy, r=R):
    x, y = xy
    return dark[int(y - r):int(y + r) + 1, int(x - r):int(x + r) + 1].any()


def test_balloon_lands_on_blank_space_when_upleft_is_drawing():
    dark = _blank()
    dark[300:420, 300:420] = True            # drawing sitting up-left of the callout
    dark[500:520, 440:700] = True            # a dimension line below-left
    c = _char((500, 450, 600, 490))
    place_balloons([c], dpi=DPI, ink=InkMap(dark))
    assert not _circle_ink(dark, c.balloon_xy)


def test_leader_may_be_long_to_reach_whitespace():
    dark = _blank()
    dark[200:700, 200:1000] = True           # callout sits in a dense field...
    dark[440:500, 560:640] = False           # ...with its own little clearing
    c = _char((580, 450, 620, 490))
    place_balloons([c], dpi=DPI, ink=InkMap(dark))
    bx, by = c.balloon_xy
    assert not _circle_ink(dark, (bx, by))
    # had to leave the dense field entirely: a long leader, not a nearby spot
    assert max(abs(bx - 600), abs(by - 470)) > 150


def test_clear_drawing_puts_the_balloon_close_and_above():
    dark = _blank()
    c = _char((600, 450, 700, 490))
    place_balloons([c], dpi=DPI, ink=InkMap(dark))
    bx, by = c.balloon_xy
    assert by < 450                                   # above the callout
    assert ((bx - 650) ** 2 + (by - 470) ** 2) ** 0.5 < 150   # and not far


def test_two_balloons_do_not_share_a_spot():
    dark = _blank()
    a, b = _char((600, 450, 700, 490), 1), _char((610, 500, 710, 540), 2)
    place_balloons([a, b], dpi=DPI, ink=InkMap(dark))
    ax, ay = a.balloon_xy
    bx, by = b.balloon_xy
    assert ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 >= 2 * R


def test_no_ink_map_behaves_as_before():
    c = _char((200, 200, 260, 230))
    place_balloons([c], dpi=DPI)
    assert c.balloon_xy == (142, 142)
