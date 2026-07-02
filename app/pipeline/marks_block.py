"""Marks-block path: locate the top-right Mark/Description legend, read it as
structured bilingual data, mask it before the main detector runs so its 101…
numbers cannot be misclassified as note-ref callouts. Parallel to (and
independent of) notes_block.py."""
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.models import Mark, MarkBlock
from app.pipeline import title_block as tb


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


# Top-right quadrant the legend is searched in (fractions of page W/H). Kept
# generous on height so a tall multi-row legend is not clipped, but well clear
# of the bottom-right title block (which starts around y = 0.6 H).
_QUAD_X_MIN_FRAC = 0.5
_QUAD_Y_MAX_FRAC = 0.55


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


def _legend_cells_in_band(
        cells: List[Tuple[int, int, int, int]]
) -> List[Tuple[int, int, int, int]]:
    """Keep only the cells belonging to the legend table's row band.

    The table's defining feature is a vertical stack of cells sharing an
    x-column (the Mark-number column). We pick the x-column with the most
    stacked cells, take its y-extent as the band, and drop any cell that starts
    above or ends below it. This discards the large frame-enclosed corner
    regions whose bounding boxes would otherwise engulf the whole quadrant and
    over-mask the drawing views. With fewer than two stacked cells there is no
    table to anchor on, so the cells are returned unchanged."""
    from collections import defaultdict
    cols: dict = defaultdict(list)
    for c in cells:
        cols[round(c[0] / 15)].append(c)
    anchor = max(cols.values(), key=len)
    if len(anchor) < 2:
        return cells
    by0 = min(c[1] for c in anchor)
    by1 = max(c[3] for c in anchor)
    tol = max(20, int(0.05 * (by1 - by0)))
    band = [c for c in cells if c[1] >= by0 - tol and c[3] <= by1 + tol]
    return band or cells


def locate_marks_block(image: Image.Image) -> Optional[MarksBlockRegion]:
    """Find the Mark/Description legend in the top-right quadrant. Detects the
    ruled grid cells (same primitive the title block uses) and returns the union
    of the ink-bearing cells as the region — so a multi-row legend is captured
    whole, not just its largest single cell. Never raises; any failure logs to
    stderr and returns None so the pipeline runs without a marks section."""
    try:
        w, h = image.size
        quad = (int(w * _QUAD_X_MIN_FRAC), 0, w, int(h * _QUAD_Y_MAX_FRAC))
        cells = [c for c in tb.detect_cells(image, quad)
                 if tb._cell_has_ink(image, c)]
        if not cells:
            return None
        cells = _legend_cells_in_band(cells)
        outer = (min(c[0] for c in cells), min(c[1] for c in cells),
                 max(c[2] for c in cells), max(c[3] for c in cells))
        columns = _infer_columns(image, outer)
        return MarksBlockRegion(outer_box=tuple(int(v) for v in outer),
                                lang_columns=columns)
    except Exception as e:
        print(f"[sindri.marks_block] locator failed: {e!r}",
              file=sys.stderr, flush=True)
        return None


def read_marks_block(image: Image.Image, region: MarksBlockRegion, backend) -> str:
    """Read the marks block once and return the raw transcription text.

    The marks table has the same bilingual 'pos / EN / DE' shape as the
    notes table, so we reuse the VLM backend's notes prompt when available.
    Falls back to the generic `read_region` otherwise. Never raises."""
    crop = image.crop(region.outer_box)
    try:
        if hasattr(backend, "read_notes_block"):
            result = backend.read_notes_block(crop)
        else:
            result = backend.read_region(crop)
        return (result.text or "")
    except Exception as e:
        print(f"[sindri.marks_block] read failed: {e!r}",
              file=sys.stderr, flush=True)
        return ""
