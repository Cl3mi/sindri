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
