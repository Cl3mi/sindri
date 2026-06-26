"""Title-block path: locate the bottom-right Schriftfeld, detect its grid
cells with OpenCV, read each non-empty cell as a {label, value} pair, mask the
region before the main detector runs. Mirrors notes_block.py. Never raises from
the public entry points; any failure yields an empty title block."""
import json
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import cv2
from PIL import Image, ImageDraw

from app.models import TitleField


_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def parse_title_cell(raw: str) -> Tuple[str, str]:
    """Return (label, value) from the backend's per-cell read. Accepts a JSON
    object {"label":..,"value":..} (optionally wrapped in code fences), then a
    'label: value' fallback, else ("", raw)."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    cleaned = _FENCE_RE.sub("", raw).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return str(obj.get("label", "")).strip(), str(obj.get("value", "")).strip()
    except (ValueError, TypeError):
        pass
    if ":" in cleaned:
        label, _, value = cleaned.partition(":")
        return label.strip(), value.strip()
    return "", cleaned


def split_label(label: str) -> Tuple[str, str]:
    """Split a bilingual caption 'English / German' into (en, de)."""
    if "/" in label:
        en, _, de = label.partition("/")
        return en.strip(), de.strip()
    return label.strip(), ""


def review_flags_field(value: str, label: str,
                       expect_caption: bool = True) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons). An empty value is always a reason. A
    present value with no caption is flagged only for grid cells
    (expect_caption=True); loose text legitimately has no caption."""
    reasons: List[str] = []
    if not value.strip():
        reasons.append("empty value")
    elif expect_caption and not label.strip():
        reasons.append("missing caption")
    return bool(reasons), reasons


_MIN_CELL_W = 40          # px: ignore slivers and line artifacts
_MIN_CELL_H = 18
_INK_MIN = 0.004          # fraction of dark pixels for a cell to count as text
_BAND_TOL = 30            # px: rows within this y-band sort left-to-right


def _cell_has_ink(image: Image.Image, box: Tuple[int, int, int, int]) -> bool:
    """True if the cell interior contains a meaningful amount of dark pixels."""
    crop = np.asarray(image.convert("L").crop(box))
    if crop.size == 0:
        return False
    return float((crop < 128).mean()) >= _INK_MIN


def detect_cells(image: Image.Image,
                 region_box: Tuple[float, float, float, float]
                 ) -> List[Tuple[int, int, int, int]]:
    """Detect ruled grid cells inside `region_box`. Returns cell boxes in
    absolute page coordinates, sorted top-to-bottom then left-to-right. Empty
    list on any too-small/blank region."""
    x0, y0, x1, y1 = (int(v) for v in region_box)
    crop = np.asarray(image.convert("L").crop((x0, y0, x1, y1)))
    if crop.size == 0 or crop.shape[0] < 40 or crop.shape[1] < 40:
        return []
    bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = bw.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 25), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 25)))
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    grid = cv2.add(hor, ver)
    inv = cv2.bitwise_not(grid)
    _, _, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    cells: List[Tuple[int, int, int, int]] = []
    for cx, cy, cw, ch, _area in stats[1:]:
        if cw < _MIN_CELL_W or ch < _MIN_CELL_H:
            continue
        if cw > w * 0.95 and ch > h * 0.95:   # the whole-region background blob
            continue
        cells.append((x0 + int(cx), y0 + int(cy),
                      x0 + int(cx + cw), y0 + int(cy + ch)))
    cells.sort(key=lambda b: (round(b[1] / _BAND_TOL), b[0]))
    return cells
