from app.models import Characteristic
from app.pipeline.place import number_characteristics


def _char(box):
    c = Characteristic(pos=0)
    c.target_region = box
    return c


def test_number_characteristics_reading_order():
    top_left = _char((10, 10, 40, 30))
    top_right = _char((200, 12, 240, 32))
    bottom = _char((10, 300, 40, 320))
    ordered = number_characteristics([bottom, top_right, top_left], band_tol=60)
    by_pos = {c.pos: c for c in ordered}
    assert by_pos[1].target_region == top_left.target_region
    assert by_pos[2].target_region == top_right.target_region
    assert by_pos[3].target_region == bottom.target_region


def test_number_characteristics_assigns_sequential_pos():
    chars = [_char((0, i * 100, 20, i * 100 + 20)) for i in range(5)]
    ordered = number_characteristics(chars)
    assert sorted(c.pos for c in ordered) == [1, 2, 3, 4, 5]


from app.pipeline.place import place_balloons


def test_place_balloons_offsets_up_and_left():
    c = _char((200, 200, 260, 230))
    place_balloons([c], offset=70)
    bx, by = c.balloon_xy
    assert bx == 130 and by == 130


def test_place_balloons_clamps_to_page_margin():
    c = _char((10, 10, 40, 30))
    place_balloons([c], offset=70, margin=10)
    bx, by = c.balloon_xy
    assert bx == 10 and by == 10


def test_place_balloons_default_gap_is_dpi_scaled():
    c = _char((200, 200, 260, 230))
    place_balloons([c])                       # dpi=300, gap_pt=14 -> 58 px
    assert c.balloon_xy == (142, 142)


def test_place_balloons_gap_scales_with_dpi():
    a = _char((200, 200, 260, 230))
    b = _char((200, 200, 260, 230))
    place_balloons([a], dpi=300)              # 58 px
    place_balloons([b], dpi=150)              # 29 px -> balloon closer in px
    assert a.balloon_xy == (142, 142)
    assert b.balloon_xy == (171, 171)
