"""Deterministic CV detection of rectangular callouts (GD&T frames, theoretical
boxed dimensions, boxed note-references) on a rendered drawing page.

Runs once on the full page (frames are page-scale features, not tile-local).
Returns the outer box (for stamping/dedupe), a frame-stripped inner box (for a
clean read), the cell count, and a geometric sub-type. Never raises: any failure
is logged and yields []."""
import sys
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image

from app.pipeline.geom import _iou


@dataclass
class BoxDetection:
    outer_box: Tuple[int, int, int, int]
    inner_box: Tuple[int, int, int, int]
    cells: int
    subtype: str          # gdt|theoretical  (note_ref set downstream after OCR)
    conf: float


def _find_rectangles(gray, min_side, max_area_frac) -> List[tuple]:
    h, w = gray.shape
    _, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binv, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    page_area = float(w * h)
    rects = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, bw, bh = cv2.boundingRect(approx)
        if bw < min_side or bh < min_side:
            continue
        if bw * bh > max_area_frac * page_area:
            continue
        rects.append((x, y, x + bw, y + bh))
    return _dedupe_rects(rects)


def _dedupe_rects(rects, iou_thresh=0.5) -> List[tuple]:
    """A box outline drawn with thickness yields near-identical outer/inner
    contours; collapse them, keeping the larger."""
    kept = []
    for r in sorted(rects, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        if all(_iou(r, k) < iou_thresh for k in kept):
            kept.append(r)
    return kept


def _count_cells(gray, box, inset) -> int:
    x0, y0, x1, y1 = box
    roi = gray[y0 + inset:y1 - inset, x0 + inset:x1 - inset]
    if roi.size == 0:
        return 1
    _, binv = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    rh = roi.shape[0]
    col_ink = binv.sum(axis=0) / 255.0          # ink pixels per column
    is_divider = col_ink > 0.6 * rh             # near-full-height vertical line
    dividers, prev = 0, False
    for d in is_divider:
        if d and not prev:
            dividers += 1
        prev = bool(d)
    return dividers + 1


# Boxed note-references (100-series numbers) are NOT classified here — they look
# the same size as a boxed theoretical dim at production DPI.  They are retagged
# downstream once OCR confirms 100-series content.  "note_ref" is therefore a
# valid subtype value on BoxDetection but is set later, not by this module.
def _classify(box, cells) -> str:
    return "gdt" if cells >= 2 else "theoretical"


def tighten_to_ink(image: Image.Image, box, pad: int = 3,
                   min_area_frac: float = 0.02):
    """Shrink `box` to the bounding box of the ink inside it.

    The VLM detector returns generous boxes that swallow leader lines,
    arrowheads and whitespace around a callout; that oversize both pushes the
    balloon far from the number (placement anchors on the box corner) and dilutes
    the read crop. This projects the ink inside the box onto each axis and returns
    the tight extent (plus a small `pad`), in page coordinates.

    Robustness: an empty/degenerate box, a box with no ink, or a tight extent
    that collapses below `min_area_frac` of the original (Otsu noise on a nearly
    blank crop) all return the ORIGINAL box unchanged. Never raises."""
    try:
        x0, y0, x1, y1 = (int(v) for v in box)
        if x1 <= x0 or y1 <= y0:
            return box
        crop = np.array(image.convert("L").crop((x0, y0, x1, y1)))
        if crop.size == 0:
            return box
        _, binv = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        ink = binv // 255
        h, w = ink.shape
        # A row/col counts as ink only above a small floor, so a stray speckle or
        # a 1px thresholding artefact does not defeat the shrink.
        row_floor = max(1, int(0.01 * w))
        col_floor = max(1, int(0.01 * h))
        rows = np.where(ink.sum(axis=1) >= row_floor)[0]
        cols = np.where(ink.sum(axis=0) >= col_floor)[0]
        if rows.size == 0 or cols.size == 0:
            return box
        ny0, ny1 = int(rows[0]), int(rows[-1]) + 1
        nx0, nx1 = int(cols[0]), int(cols[-1]) + 1
        nx0 = max(0, nx0 - pad); ny0 = max(0, ny0 - pad)
        nx1 = min(w, nx1 + pad); ny1 = min(h, ny1 + pad)
        tight = (x0 + nx0, y0 + ny0, x0 + nx1, y0 + ny1)
        orig_area = (x1 - x0) * (y1 - y0)
        tight_area = (tight[2] - tight[0]) * (tight[3] - tight[1])
        if orig_area > 0 and tight_area < min_area_frac * orig_area:
            return box
        return tight
    except Exception as e:                       # never fatal
        print(f"[sindri.boxes] tighten failed: {e!r}", file=sys.stderr, flush=True)
        return box


def _has_sharp_corners(binv, box, patch: int = 3, need: int = 3) -> bool:
    """True when the outline's corners are square, not rounded.

    A rounded rectangle (stadium, slot) leaves the bounding box's extreme corner
    empty -- the arc starts ~0.3 radius in from it -- while a square corner has
    ink in the outermost pixels. At least `need` of the four corners must hold
    ink in a `patch`-pixel block."""
    x0, y0, x1, y1 = box
    h, w = binv.shape
    hits = 0
    for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1 - 1, y0, -1, 1),
                           (x0, y1 - 1, 1, -1), (x1 - 1, y1 - 1, -1, -1)):
        xs = sorted((max(0, min(w, cx)), max(0, min(w, cx + sx * patch))))
        ys = sorted((max(0, min(h, cy)), max(0, min(h, cy + sy * patch))))
        if binv[ys[0]:ys[1] + 1, xs[0]:xs[1] + 1].any():
            hits += 1
    return hits >= need


def _frame_dividers(binv, box, inset: int, span: float = 0.95) -> int:
    """Vertical lines inside `box` that run (nearly) its full height."""
    x0, y0, x1, y1 = box
    roi = binv[y0 + inset:y1 - inset, x0 + inset:x1 - inset]
    if roi.size == 0:
        return 0
    col_ink = roi.sum(axis=0) / 255.0
    is_divider = col_ink >= span * roi.shape[0]
    # A thick outline can be wider than `inset`, leaving its own left/right
    # border in the region as full-height columns: a run touching either edge
    # is that border, not a divider.
    runs, start = [], None
    for i, d in enumerate(list(is_divider) + [False]):
        if d and start is None:
            start = i
        elif not d and start is not None:
            runs.append((start, i - 1))
            start = None
    last = len(is_divider) - 1
    return sum(1 for a, b in runs if a > 0 and b < last)


def _is_frame_cell(box, found, multis) -> bool:
    """Is `box` one cell of a GD&T frame? The gap between two cells is the
    divider's thickness and the tops/bottoms differ by line-width noise, so both
    tolerances scale with the cell's height (a fixed 6 px missed real drawings,
    where dividers are several pixels thick)."""
    for m in multis:
        if m != box and _inside(box, m):
            return True
    h = box[3] - box[1]
    tol = max(8, int(0.3 * h))
    for o in found:
        if o == box:
            continue
        aligned = abs(o[1] - box[1]) <= tol and abs(o[3] - box[3]) <= tol
        gap = max(o[0] - box[2], box[0] - o[2])
        if aligned and gap <= tol:
            return True
    return False


def _border_continues(binv, box, reach: float = 0.6, need: float = 0.85) -> bool:
    """Does the box's top or bottom border keep running past its left/right
    side? That is a GD&T frame: the border passes over the divider into the next
    cell. An isolated box's border ends at its corner, so the band beyond is
    blank. Only horizontal continuation is tested, so a vertical stack of boxes
    (101 above 102) is unaffected."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    span = max(4, int(reach * h))
    H, W = binv.shape
    for yb in (y0, y1):
        rows = binv[max(0, yb - 3):min(H, yb + 4), :]
        if rows.size == 0:
            continue
        col = rows.any(axis=0)
        right = col[min(W, x1 + 2):min(W, x1 + 2 + span)]
        left = col[max(0, x0 - 2 - span):max(0, x0 - 2)]
        for side in (right, left):
            if side.size and side.mean() >= need:
                return True
    return False


class PlainRects(list):
    """The plain rectangles, plus `.parts`: every other rectangle found (frame
    cells, whole frames, rounded boxes). A detection that contains a part is a
    frame, never a plain box, whatever else hugs it."""
    parts: list = []


def _inside(a, b, frac: float = 0.9) -> bool:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    area = (a[2] - a[0]) * (a[3] - a[1])
    return w > 0 and h > 0 and area > 0 and w * h / area >= frac


def find_plain_rectangles(image: Image.Image, min_side: int = 12,
                          max_area_frac: float = 0.05, inset: int = 4) -> List[tuple]:
    """Outer boxes of single-cell, square-cornered rectangles on the page --
    the frames around boxed note numbers, datum letters and basic dimensions.

    Multi-cell frames (GD&T control frames) and rounded rectangles are NOT
    returned: the client wants neither of the first class dropped nor the
    second one treated as a box at all. Never raises: failure yields [].

    The cell test here is stricter than `_count_cells`: a divider must span
    >= 95% of the frame's height, so the tall stroke of a "1" inside a boxed
    "101" does not make a note number look like a two-cell GD&T frame. The
    individual cells of a GD&T frame are themselves found as rectangles, so a
    rectangle whose left or right neighbour has the same top and bottom (a
    row of cells) is a frame cell, not a box of its own. A vertical stack of
    boxes (101 above 102) shares no such alignment and stays plain."""
    try:
        gray = np.array(image.convert("L"))
        _, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        found = _find_rectangles(gray, min_side, max_area_frac)
        singles = [b for b in found
                   if _frame_dividers(binv, b, inset) == 0 and _has_sharp_corners(binv, b)]
        multis = [b for b in found if _frame_dividers(binv, b, inset) > 0]
        plain = PlainRects(b for b in singles
                           if not _is_frame_cell(b, found, multis)
                           and not _border_continues(binv, b))
        plain.parts = [b for b in found if b not in plain]
        return plain
    except Exception as e:                       # never fatal
        print(f"[sindri.boxes] plain rectangles failed: {e!r}", file=sys.stderr, flush=True)
        return []


def in_plain_rectangle(box, rects, max_ratio: float = 4.0, min_cover: float = 0.8) -> bool:
    """Is `box` the content of one of `rects`? The two must nearly coincide
    (the overlap covers >= `min_cover` of the smaller, and neither is more than
    `max_ratio` times the other's area) so a callout merely sitting inside a
    large view frame or title block is not mistaken for a boxed one."""
    a = (box[2] - box[0]) * (box[3] - box[1])
    if a <= 0:
        return False
    for part in getattr(rects, "parts", ()):
        if (part[2] - part[0]) * (part[3] - part[1]) < 0.7 * a and _inside(part, box):
            return False
    for r in rects:
        b = (r[2] - r[0]) * (r[3] - r[1])
        w = min(box[2], r[2]) - max(box[0], r[0])
        h = min(box[3], r[3]) - max(box[1], r[1])
        if w <= 0 or h <= 0 or b <= 0:
            continue
        if w * h / min(a, b) >= min_cover and max(a, b) / min(a, b) <= max_ratio:
            return True
    return False


def detect_boxes(image: Image.Image, min_side: int = 12, max_area_frac: float = 0.05,
                 inset: int = 4) -> List[BoxDetection]:
    try:
        gray = np.array(image.convert("L"))
        out = []
        for box in _find_rectangles(gray, min_side, max_area_frac):
            cells = _count_cells(gray, box, inset)
            subtype = _classify(box, cells)
            x0, y0, x1, y1 = box
            inner = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
            if inner[2] <= inner[0] or inner[3] <= inner[1]:
                inner = box
            out.append(BoxDetection(outer_box=box, inner_box=inner,
                                    cells=cells, subtype=subtype, conf=0.8))
        return out
    except Exception as e:                       # never fatal
        print(f"[sindri.boxes] failed: {e!r}", file=sys.stderr, flush=True)
        return []
