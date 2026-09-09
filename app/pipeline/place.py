"""Number detected characteristics in reading order and position their balloons.

Pure functions over Characteristic lists: numbering sorts top-to-bottom in
horizontal bands then left-to-right; placement offsets a balloon marker from the
callout into nearby space (the human fixes overlaps by dragging in review).
"""


# Balloon radius in PDF points. Duplicated from ballooned_pdf._RADIUS rather
# than imported, because app.pipeline.place is pure geometry and importing the
# PDF writer here would drag fitz into every caller that only wants placement.
# tests/test_place_overlap.py pins the two together.
_RADIUS_PT = 9.0

# Where to try putting a marker, in units of `gap`, best first. Up-and-left is
# the historical position and stays first, so an uncrowded drawing is placed
# exactly as before; the rest walk the other three diagonals and then further
# out, which keeps the marker near the callout it belongs to.
_CANDIDATE_OFFSETS = ((-1, -1), (1, -1), (-1, 1), (1, 1),
                      (-2, -1), (2, -1), (-1, -2), (1, -2),
                      (-2, -2), (2, 2), (-3, -1), (3, -1))


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _hits_box(xy, r, box):
    """Does a marker of radius `r` centred at `xy` touch axis-aligned `box`?"""
    x, y = xy
    nx = max(box[0], min(x, box[2]))
    ny = max(box[1], min(y, box[3]))
    return (x - nx) ** 2 + (y - ny) ** 2 < r * r


def _is_clear(xy, r, boxes, placed, own_box):
    """True when a marker at `xy` covers no OTHER callout and no earlier marker.

    Its own callout is excluded: the balloon belongs to that value and sitting
    near it is the point. Two markers need 2r between centres to keep both
    numbers readable."""
    for box in boxes:
        if box is not own_box and _hits_box(xy, r, box):
            return False
    for px, py in placed:
        if (xy[0] - px) ** 2 + (xy[1] - py) ** 2 < (2 * r) ** 2:
            return False
    return True


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
    # The marker is drawn as a circle of ballooned_pdf._RADIUS PDF points, so its
    # footprint in image space scales with the render dpi exactly as the gap
    # does.
    r = _RADIUS_PT * dpi / 72.0
    boxes = [c.target_region for c in chars if c.target_region]
    placed = []
    for c in chars:
        x0, y0 = c.target_region[0], c.target_region[1]
        for dx, dy in _CANDIDATE_OFFSETS:
            bx = max(margin, x0 + dx * gap)
            by = max(margin, y0 + dy * gap)
            if _is_clear((bx, by), r, boxes, placed, c.target_region):
                break
        # Falls through with the last candidate when a dense drawing offers
        # nowhere clear. An overlapping balloon is still better than none: it is
        # visible and draggable, whereas skipping it loses the characteristic
        # from the sheet entirely.
        c.balloon_xy = (bx, by)
        placed.append((bx, by))
    return chars
