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
from app.pipeline.detect import tile_grid


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


@dataclass
class TitleBlockRegion:
    outer_box: Tuple[int, int, int, int]
    cells: List[Tuple[int, int, int, int]]   # ink-bearing cells, reading order


def locate_title_block(image: Image.Image) -> Optional[TitleBlockRegion]:
    """Find the title block in the bottom-right quadrant. Returns None (non-fatal)
    if no ink-bearing grid cells are found or anything goes wrong."""
    try:
        w, h = image.size
        quad = (int(w * 0.5), int(h * 0.6), w, h)
        cells = [c for c in detect_cells(image, quad) if _cell_has_ink(image, c)]
        if not cells:
            return None
        outer = (min(c[0] for c in cells), min(c[1] for c in cells),
                 max(c[2] for c in cells), max(c[3] for c in cells))
        return TitleBlockRegion(outer_box=outer, cells=cells)
    except Exception as e:
        print(f"[sindri.title_block] locator failed: {e!r}",
              file=sys.stderr, flush=True)
        return None


def mask_region(image: Image.Image, region: TitleBlockRegion) -> Image.Image:
    """Return a copy of `image` with `region.outer_box` filled white, so the
    main detector cannot misread title-block text as dimension callouts."""
    out = image.copy()
    x0, y0, x1, y1 = region.outer_box
    if x1 > x0 and y1 > y0:
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill="white")
    return out


def read_title_block(image: Image.Image, region: TitleBlockRegion,
                     backend) -> List[TitleField]:
    """Read each ink-bearing cell as a {label, value} pair. Prefers a backend
    `read_title_cell` method (dedicated prompt); falls back to `read_region`.
    Per-cell failures are skipped, never fatal."""
    fields: List[TitleField] = []
    for box in region.cells:
        crop = image.crop(box)
        try:
            if hasattr(backend, "read_title_cell"):
                res = backend.read_title_cell(crop)
            else:
                res = backend.read_region(crop)
            raw, conf = res.text, res.confidence
        except Exception as e:
            print(f"[sindri.title_block] cell read failed: {e!r}",
                  file=sys.stderr, flush=True)
            raw, conf = "", 0.0
        label, value = parse_title_cell(raw)
        if not label and not value:
            continue
        en, de = split_label(label)
        flagged, reasons = review_flags_field(value, label, expect_caption=True)
        fields.append(TitleField(
            label=label, label_en=en, label_de=de, value=value,
            box=tuple(float(v) for v in box), confidence=conf,
            needs_review=flagged, review_reasons=reasons,
        ))
    return fields


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def loose_text(image: Image.Image, backend,
               exclude_boxes: List[Tuple[float, float, float, float]]
               ) -> List[TitleField]:
    """Catch free text outside the structured blocks: detect `note`-kind regions
    across the page, drop any overlapping an exclude box (title/notes blocks),
    read the rest and emit label-less TitleFields. Never fatal."""
    out: List[TitleField] = []
    try:
        w, h = image.size
        boxes: List[Tuple[int, int, int, int]] = []
        for (tx0, ty0, tx1, ty1) in tile_grid(w, h):
            try:
                dets = backend.detect_regions(image.crop((tx0, ty0, tx1, ty1)))
            except Exception:
                continue
            for d in dets:
                if d.kind != "note":
                    continue
                box = (d.box[0] + tx0, d.box[1] + ty0,
                       d.box[2] + tx0, d.box[3] + ty0)
                if any(_overlaps(box, ex) for ex in exclude_boxes):
                    continue
                if any(_overlaps(box, seen) for seen in boxes):
                    continue
                boxes.append(box)
        for box in boxes:
            try:
                res = backend.read_region(image.crop(box))
            except Exception:
                continue
            text = (res.text or "").strip()
            if not text:
                continue
            out.append(TitleField(
                label="", value=text,
                box=tuple(float(v) for v in box), confidence=res.confidence,
                needs_review=False, review_reasons=[],
            ))
    except Exception as e:
        print(f"[sindri.title_block] loose_text failed: {e!r}",
              file=sys.stderr, flush=True)
    return out
