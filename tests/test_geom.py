from app.pipeline.geom import _iou, _union, _x_aligned, _y_close


def test_iou_zero_when_disjoint():
    assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_one_when_identical():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_iou_partial_overlap():
    # 10x10 each, overlap 5x5 = 25; union = 100+100-25 = 175
    assert abs(_iou((0, 0, 10, 10), (5, 5, 15, 15)) - 25 / 175) < 1e-9


def test_union_covers_both():
    assert _union((0, 0, 10, 10), (5, 5, 20, 20)) == (0, 0, 20, 20)


def test_x_aligned_true_within_tolerance():
    assert _x_aligned((0, 0, 10, 5), (8, 20, 18, 25), x_tol=5) is True


def test_x_aligned_false_outside_tolerance():
    assert _x_aligned((0, 0, 10, 5), (30, 20, 40, 25), x_tol=5) is False


def test_y_close_true_when_vertically_adjacent():
    assert _y_close((0, 0, 10, 10), (0, 15, 10, 25), y_gap=10) is True


def test_y_close_false_when_far_apart():
    assert _y_close((0, 0, 10, 10), (0, 100, 10, 110), y_gap=10) is False
