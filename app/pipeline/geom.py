"""Geometry primitives shared by detect.py, boxes.py, and notes_block.py."""


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def _x_aligned(a, b, x_tol: int) -> bool:
    return a[0] <= b[2] + x_tol and b[0] <= a[2] + x_tol


def _y_close(a, b, y_gap: int) -> bool:
    gap = max(a[1] - b[3], b[1] - a[3])
    return gap <= y_gap


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
