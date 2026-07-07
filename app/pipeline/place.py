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


def place_balloons(chars, dpi: int = 300, gap_pt: float = 14.0,
                   margin: int = 10, offset: int = None):
    """Set balloon_xy for each characteristic: a marker offset up-and-left from
    the callout's top-left corner, clamped so it stays on the page. The leader
    line to the callout is drawn later from balloon_xy to target_region.

    The gap is expressed in PDF points (`gap_pt`) and converted to render pixels
    via `dpi`, so the balloon sits a physically-constant distance from the
    callout at any render resolution — it does not blow up with DPI the way a
    fixed pixel offset did. Since detection boxes are now tightened to their ink
    (see boxes.tighten_to_ink), this corner is the real glyph corner, so a modest
    gap keeps the balloon next to its number instead of drifting into the page.
    `offset` (pixels) overrides the computed gap when given."""
    gap = offset if offset is not None else int(round(gap_pt * dpi / 72.0))
    for c in chars:
        x0, y0 = c.target_region[0], c.target_region[1]
        bx = max(margin, x0 - gap)
        by = max(margin, y0 - gap)
        c.balloon_xy = (bx, by)
    return chars
