"""Number detected characteristics in reading order and position their balloons.

Pure functions over Characteristic lists: numbering sorts top-to-bottom in
horizontal bands then left-to-right; placement offsets a balloon marker from the
callout into nearby space (the human fixes overlaps by dragging in review).
"""


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def number_characteristics(chars, band_tol: int = 60):
    """Sort into reading order (banded rows top-to-bottom, left-to-right within a
    band) and assign pos = 1..N. Returns the sorted list (pos set in place)."""
    def key(c):
        cx, cy = _center(c.target_region)
        return (round(cy / band_tol), cx)
    ordered = sorted(chars, key=key)
    for i, c in enumerate(ordered, start=1):
        c.pos = i
    return ordered
