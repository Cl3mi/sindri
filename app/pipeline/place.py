"""Number detected characteristics in reading order and position their balloons.

Pure functions over Characteristic lists: numbering sorts top-to-bottom in
horizontal bands then left-to-right; placement offsets a balloon marker from the
callout into nearby space (the human fixes overlaps by dragging in review).
"""
import math


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


class InkMap:
    """Where the drawing has ink, at 1/`factor` resolution, for whitespace
    queries. A coarse cell counts as ink if ANY pixel in it is, so "no ink"
    means genuinely blank. Built from a binary page array (True = ink)."""

    def __init__(self, dark, factor: int = 3):
        import numpy as np
        h, w = dark.shape
        f = self.f = max(1, int(factor))
        H, W = -(-h // f), -(-w // f)
        pad = np.zeros((H * f, W * f), dtype=bool)
        pad[:h, :w] = dark
        self.cells = pad.reshape(H, f, W, f).any(axis=(1, 3))
        self.width, self.height = w, h
        self._sum = np.pad(self.cells.astype(np.int32).cumsum(0).cumsum(1),
                           ((1, 0), (1, 0)))

    @classmethod
    def from_image(cls, image, factor: int = 3, threshold: int = 170):
        import numpy as np
        return cls(np.asarray(image.convert("L")) < threshold, factor)

    def ink_in(self, box) -> int:
        """Number of ink cells inside `box` (page pixels)."""
        f = self.f
        H, W = self.cells.shape
        x0 = max(0, int(box[0]) // f)
        y0 = max(0, int(box[1]) // f)
        x1 = min(W, -(-int(box[2]) // f))
        y1 = min(H, -(-int(box[3]) // f))
        if x1 <= x0 or y1 <= y0:
            return 0
        s = self._sum
        return int(s[y1, x1] - s[y0, x1] - s[y1, x0] + s[y0, x0])

    def inside_page(self, box, margin: int = 0) -> bool:
        return (box[0] >= margin and box[1] >= margin
                and box[2] <= self.width - margin and box[3] <= self.height - margin)

    def segment_ink(self, p, q, skip_box=None) -> int:
        """Ink cells met walking p -> q, ignoring those inside `skip_box`."""
        f = self.f
        n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / f))
        H, W = self.cells.shape
        hits = 0
        for i in range(n + 1):
            x = p[0] + (q[0] - p[0]) * i / n
            y = p[1] + (q[1] - p[1]) * i / n
            if skip_box and skip_box[0] <= x <= skip_box[2] and skip_box[1] <= y <= skip_box[3]:
                continue
            cx, cy = int(x) // f, int(y) // f
            if 0 <= cx < W and 0 <= cy < H and self.cells[cy, cx]:
                hits += 1
        return hits


# Directions tried around a callout, as angles in degrees clockwise from "right"
# in image space (y down). Up-left (225) is the historical position and gets a
# small bias so an uncrowded drawing still looks the way it always did.
_ANGLES = tuple(range(0, 360, 20))
_PREFERRED_ANGLE = 225.0


def _ray_exit(box, ux, uy) -> float:
    """Distance from the callout's centre to its edge along (ux, uy)."""
    hw, hh = (box[2] - box[0]) / 2.0, (box[3] - box[1]) / 2.0
    tx = hw / abs(ux) if abs(ux) > 1e-9 else math.inf
    ty = hh / abs(uy) if abs(uy) > 1e-9 else math.inf
    return min(tx, ty)


def _find_whitespace(box, ink, r, boxes, placed, own_box, margin, lead_px, max_reach):
    """The best balloon centre in blank space near `box`, or None.

    The circle (radius r, already padded by the caller) must contain no ink, sit
    on the page, and clear other callouts and balloons. Among those, the cost is
    leader length plus a heavy charge per ink cell the leader crosses, so a
    nearer spot wins unless its leader would cut through the drawing - then a
    longer leader to a cleaner spot does."""
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    step = max(2.0, r * 0.5)
    best, best_cost = None, None
    for ang in _ANGLES:
        a = math.radians(ang)
        ux, uy = math.cos(a), math.sin(a)
        dist = _ray_exit(box, ux, uy) + r + lead_px
        bias = (1 - math.cos(math.radians(ang - _PREFERRED_ANGLE))) * r * 1.0
        while dist <= max_reach:
            x, y = cx + ux * dist, cy + uy * dist
            circle = (x - r, y - r, x + r, y + r)
            if best_cost is not None and dist + bias >= best_cost:
                break                       # farther along this ray cannot win
            if ink.inside_page(circle, margin) and ink.ink_in(circle) == 0 \
                    and _is_clear((x, y), r, boxes, placed, own_box):
                # leader runs balloon rim -> callout edge; both ends are ours
                crossing = ink.segment_ink((x, y), (cx, cy), skip_box=_grow(box, ink.f * 2))
                cost = dist + bias + crossing * ink.f * 4.0
                if best_cost is None or cost < best_cost:
                    best, best_cost = (x, y), cost
                break                       # first blank spot on this ray; farther only lengthens
            dist += step
    return best


def _grow(box, d):
    return (box[0] - d, box[1] - d, box[2] + d, box[3] + d)


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
                   margin: int = 10, offset: int = None, ink: "InkMap" = None,
                   reach_pt: float = 120.0, clearance_pt: float = 3.0):
    """Set balloon_xy for each characteristic: a marker offset up-and-left from
    the callout's top-left corner, clamped so it stays on the page. The leader
    line to the callout is drawn later from balloon_xy to target_region.

    The gap is expressed in PDF points (`gap_pt`) and converted to render pixels
    via `dpi`, so the balloon sits a physically-constant distance from the
    callout at any render resolution — it does not blow up with DPI the way a
    fixed pixel offset did. Since detection boxes are now tightened to their ink
    (see boxes.tighten_to_ink), this corner is the real glyph corner, so a modest
    gap keeps the balloon next to its number instead of drifting into the page.
    `offset` (pixels) overrides the computed gap when given.

    With an `ink` map the balloon goes into BLANK space: the nearest spot (up to
    `reach_pt` away, so leaders may be long) whose circle, padded by
    `clearance_pt`, touches no part of the drawing and no other callout or
    balloon. Where no blank spot exists the fixed-offset search below is the
    fallback, as before."""
    gap = offset if offset is not None else int(round(gap_pt * dpi / 72.0))
    # The marker is drawn as a circle of ballooned_pdf._RADIUS PDF points, so its
    # footprint in image space scales with the render dpi exactly as the gap
    # does.
    r = _RADIUS_PT * dpi / 72.0
    boxes = [c.target_region for c in chars if c.target_region]
    placed = []
    for c in chars:
        if ink is not None:
            spot = _find_whitespace(
                c.target_region, ink, r + clearance_pt * dpi / 72.0, boxes, placed,
                c.target_region, margin, lead_px=gap_pt * 0.5 * dpi / 72.0,
                max_reach=reach_pt * dpi / 72.0)
            if spot is not None:
                c.balloon_xy = spot
                placed.append(spot)
                continue
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
