"""Crops of a drawing around one review row, for the OPERATOR's eyes only.

The review app shows two crops per row so nobody opens a PDF or hunts for a
balloon: the clean original with the pipeline's box (red) and gold's balloon
position (blue) drawn on it, and the stamped sheet with the printed balloon.
Overlays go onto an in-memory page that is closed without saving, so the
drawing on disk is never modified.

Geometry is kept pure (`crop_rect`) so it is testable without any PDF.
"""
from typing import Optional, Tuple

import fitz

PAD_PT = 40.0                       # context around the callout: the frame's
MIN_W_PT, MIN_H_PT = 160.0, 100.0   # neighbours are what make it readable
GOLD_HALF_PT = 5.0
MAX_PX = 1600                       # long edge of the PNG; no need for more
RED = (0.86, 0.15, 0.15)
BLUE = (0.12, 0.42, 0.95)


class CropError(RuntimeError):
    """The drawing or its page is not there; the server shows a placeholder."""


def _grow(a, b, minimum):
    if b - a >= minimum:
        return a, b
    c = (a + b) / 2
    return c - minimum / 2, c + minimum / 2


def _fit(a, b, lo, hi):
    """Shift [a, b] inside [lo, hi] without shrinking it, unless it is wider."""
    width = b - a
    if width >= hi - lo:
        return lo, hi
    if a < lo:
        return lo, lo + width
    if b > hi:
        return hi - width, hi
    return a, b


def crop_rect(page, pred_box_pt, gold_pt) -> Optional[Tuple[float, ...]]:
    """The region to show: the pipeline's box and gold's balloon position,
    padded, grown to a readable minimum, and kept on the page."""
    boxes = []
    if pred_box_pt:
        boxes.append(tuple(pred_box_pt))
    if gold_pt:
        gx, gy = gold_pt
        boxes.append((gx - GOLD_HALF_PT, gy - GOLD_HALF_PT,
                      gx + GOLD_HALF_PT, gy + GOLD_HALF_PT))
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes) - PAD_PT
    y0 = min(b[1] for b in boxes) - PAD_PT
    x1 = max(b[2] for b in boxes) + PAD_PT
    y1 = max(b[3] for b in boxes) + PAD_PT
    x0, x1 = _grow(x0, x1, MIN_W_PT)
    y0, y1 = _grow(y0, y1, MIN_H_PT)
    x0, x1 = _fit(x0, x1, page[0], page[2])
    y0, y1 = _fit(y0, y1, page[1], page[3])
    return (x0, y0, x1, y1)


def map_rect(rect, src_page, dst_page) -> Tuple[float, float, float, float]:
    """Carry a rect from one sheet's page space to another's by the per-axis
    scale between the two page rects -- the inverse of what ingest applied
    (ingest._to_target maps stamped -> original the same way). Gold positions
    were produced by that transform, so inverting it lands on the balloon
    exactly, not approximately."""
    sw, sh = src_page[2] - src_page[0], src_page[3] - src_page[1]
    sx = (dst_page[2] - dst_page[0]) / sw if sw else 1.0
    sy = (dst_page[3] - dst_page[1]) / sh if sh else 1.0
    x0, y0, x1, y1 = rect
    return (dst_page[0] + (x0 - src_page[0]) * sx,
            dst_page[1] + (y0 - src_page[1]) * sy,
            dst_page[0] + (x1 - src_page[0]) * sx,
            dst_page[1] + (y1 - src_page[1]) * sy)


def page_rect(pdf_path) -> Optional[Tuple[float, float, float, float]]:
    try:
        with fitz.open(pdf_path) as doc:
            if doc.page_count < 1:
                return None
            r = doc[0].rect
            return (r.x0, r.y0, r.x1, r.y1)
    except Exception:
        return None


def render_crop(pdf_path, rect, pred_box_pt=None, gold_pt=None) -> bytes:
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise CropError("drawing not available") from e
    try:
        if doc.page_count < 1:
            raise CropError("drawing has no pages")
        page = doc[0]
        if pred_box_pt:
            page.draw_rect(fitz.Rect(pred_box_pt), color=RED, width=1.2)
        if gold_pt:
            page.draw_circle(fitz.Point(gold_pt), 3.5, color=BLUE, fill=BLUE)
        clip = fitz.Rect(rect) & page.rect
        if clip.is_empty:
            raise CropError("position is outside the page")
        zoom = min(3.0, MAX_PX / max(clip.width, clip.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip,
                              alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()     # never saved: the overlays die with the document


def placeholder_png(message: str) -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page(width=360, height=90)
        page.insert_text((16, 50), message, fontsize=13, color=(0.4, 0.4, 0.4))
        return page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    finally:
        doc.close()
