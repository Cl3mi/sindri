"""Marks-block path: locate the top-right Mark/Description legend, read it as
structured bilingual data, mask it before the main detector runs so its 101…
numbers cannot be misclassified as note-ref callouts. Parallel to (and
independent of) notes_block.py."""
import re
import sys
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.models import Mark, MarkBlock


# Top-level row only. Marks table has no sub-bullets, so any "<pos>.<sub>\t…"
# line is rejected by this regex and dropped silently.
_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$")


def parse_marks_block(raw: str, region: Tuple[float, float, float, float]) -> MarkBlock:
    """Parse the tab-separated marks transcription into a MarkBlock.

    Each line is expected as '<pos>\\t<en>\\t<de>'. Lines containing a
    sub-index (e.g. '101.1\\t…') or any other shape are dropped silently
    (non-fatal pipeline convention)."""
    marks: List[Mark] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if "." in line.split("\t", 1)[0]:
            # sub-bullet — not expected in marks; drop
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        marks.append(Mark(
            pos=int(m.group(1)),
            text_en=m.group(2).strip(),
            text_de=m.group(3).strip(),
            raw_text=line,
        ))
    return MarkBlock(region=region, marks=marks)


def review_flags_mark(mark: Mark, two_columns: bool) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a parsed mark.

    Gating: an empty read is its own reason and does not also report
    'missing translation'."""
    reasons: List[str] = []
    if not (mark.raw_text or "").strip():
        reasons.append("empty read")
    else:
        if two_columns and (not mark.text_en.strip() or not mark.text_de.strip()):
            reasons.append("missing translation")
    return bool(reasons), reasons


@dataclass
class MarksBlockRegion:
    outer_box: Tuple[int, int, int, int]
    lang_columns: List[Tuple[int, int]]


def mask_region(image: Image.Image, region: MarksBlockRegion) -> Image.Image:
    """Return a copy of `image` with `region.outer_box` filled white. The
    original image is preserved so downstream manual re-reads still work."""
    out = image.copy()
    x0, y0, x1, y1 = region.outer_box
    if x1 > x0 and y1 > y0:
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill="white")
    return out


# Top-right quadrant thresholds (tunable). The locator considers only
# rectangles whose centre falls into the region x > _CX_MIN_FRAC*W,
# y < _CY_MAX_FRAC*H, and whose area is at least _MIN_AREA_FRAC of the page.
_CX_MIN_FRAC = 0.55
_CY_MAX_FRAC = 0.45
_MIN_AREA_FRAC = 0.02
_MAX_AREA_FRAC = 0.40   # legend table tops out around ~30 % of page
_MIN_SIDE = 40          # px — reject narrow strips


def _find_large_rectangles(gray: "np.ndarray") -> List[Tuple[int, int, int, int]]:
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
        if bw < _MIN_SIDE or bh < _MIN_SIDE:
            continue
        area = bw * bh
        if area < _MIN_AREA_FRAC * page_area:
            continue
        if area > _MAX_AREA_FRAC * page_area:
            continue
        rects.append((x, y, x + bw, y + bh))
    return rects


def _infer_columns(image: Image.Image, box: Tuple[int, int, int, int]) -> List[Tuple[int, int]]:
    """Return language-column x-ranges inside `box`. 2 columns if a strong
    vertical divider is found near the middle, else 1."""
    x0, y0, x1, y1 = box
    crop = np.array(image.convert("L").crop((x0, y0, x1, y1)))
    if crop.size == 0 or crop.shape[1] < 20:
        return [(x0, x1)]
    _, binv = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    rh = crop.shape[0]
    col_ink = binv.sum(axis=0) / 255.0
    midband_lo = int(crop.shape[1] * 0.30)
    midband_hi = int(crop.shape[1] * 0.70)
    threshold = 0.6 * rh
    best = None
    for x in range(midband_lo, midband_hi):
        if col_ink[x] > threshold and (best is None or col_ink[x] > best[1]):
            best = (x, col_ink[x])
    if best is None:
        return [(x0, x1)]
    split_x = x0 + best[0]
    return [(x0, split_x), (split_x, x1)]


def locate_marks_block(image: Image.Image):
    """Find the Mark/Description legend by picking the largest rectangle whose
    centre lies in the top-right quadrant. Never raises; any failure logs to
    stderr and returns None so the pipeline runs without a marks section."""
    try:
        w, h = image.size
        gray = np.array(image.convert("L"))
        candidates = []
        for rect in _find_large_rectangles(gray):
            x0, y0, x1, y1 = rect
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if cx < _CX_MIN_FRAC * w:
                continue
            if cy > _CY_MAX_FRAC * h:
                continue
            candidates.append(rect)
        if not candidates:
            return None
        pick = max(candidates, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        columns = _infer_columns(image, pick)
        return MarksBlockRegion(outer_box=tuple(int(v) for v in pick),
                                lang_columns=columns)
    except Exception as e:
        print(f"[sindri.marks_block] locator failed: {e!r}",
              file=sys.stderr, flush=True)
        return None
