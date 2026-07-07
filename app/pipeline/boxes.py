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
