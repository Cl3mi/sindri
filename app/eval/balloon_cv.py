"""Recover balloons from the RENDERED page, when the PDF text layer cannot.

The text-layer route (`app.eval.balloons`) covers 83% of the gold rows. The
remainder are drawings whose balloon numbers were flattened to vector outlines
or rasterised, so no digit word exists to find. Those have to be read off
pixels.

What makes this tractable is colour: this client stamps balloons in BLUE while
the CAD geometry is black. Isolating blue ink removes essentially the whole
drawing, leaving the balloon outlines and their numbers — a far easier OCR
problem than the full page, and one where a black dimension value can never be
mistaken for a balloon.

Coordinates are returned in PDF points, in the same frame as the text-layer
route, so the two are interchangeable.
"""
import tempfile
from typing import List, Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.eval.balloons import (Balloon, MAX_BALLOON_NO, MIN_BALLOON_NO,
                               _dedupe)
from app.pipeline.render import render_page

_BLUE_MARGIN = 40       # how much bluer than red/green a pixel must be
_MIN_CONF = 30.0
_DEFAULT_DPI = 300
# Balloon outline size window, in PDF points.
_MIN_BALLOON_PT = 8.0
_MAX_BALLOON_PT = 120.0
# Crop this far inside the outline before reading: a diamond's inscribed box is
# about half its bounding box, and leaving the edges in makes Tesseract read
# the outline itself as digits ('3' came back as '60').
_INTERIOR_PAD = 0.30
_UPSCALE = 4
# Sparse whole-page OCR reads nothing off thin stamped strokes; one crop per
# balloon with several segmentation modes does. 7 = one text line,
# 10 = one character, 8 = one word.
_PSMS = (7, 10, 8)


def blue_ink_mask(bgr: np.ndarray, margin: int = _BLUE_MARGIN) -> np.ndarray:
    """White where the pixel is distinctly blue, black elsewhere.

    Keyed on blue exceeding both other channels rather than on an absolute
    value, so it survives anti-aliasing and a range of blues while rejecting
    black ink, grey rasters and white paper."""
    blue = bgr[:, :, 0].astype(np.int16)
    green = bgr[:, :, 1].astype(np.int16)
    red = bgr[:, :, 2].astype(np.int16)
    is_blue = (blue - np.maximum(green, red)) > margin
    return (is_blue.astype(np.uint8) * 255)


def _candidate_regions(mask: np.ndarray, scale: float) -> List[tuple]:
    """Bounding boxes of blue blobs that could be a balloon outline. Closing
    first so a dashed or anti-aliased outline becomes one contour."""
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                              np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        w_pt, h_pt = w / scale, h / scale
        if not (_MIN_BALLOON_PT <= w_pt <= _MAX_BALLOON_PT):
            continue
        if not (_MIN_BALLOON_PT <= h_pt <= _MAX_BALLOON_PT):
            continue
        if abs(w - h) > max(w, h) * 0.45:      # a balloon is symmetric
            continue
        regions.append((x, y, w, h))
    return regions


def _prepare_crop(mask: np.ndarray, box) -> Optional[Image.Image]:
    x, y, w, h = box
    pad = int(_INTERIOR_PAD * max(w, h))
    crop = mask[y + pad:y + h - pad, x + pad:x + w - pad]
    if crop.size == 0 or not crop.any():
        return None
    big = cv2.resize(crop, None, fx=_UPSCALE, fy=_UPSCALE,
                     interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (3, 3), 0)
    _, big = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Tesseract needs quiet space around a glyph, and dark ink on light paper.
    big = cv2.copyMakeBorder(big, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    return Image.fromarray(255 - big)


def _read_number(image: Image.Image, expect: Optional[set],
                 min_conf: float) -> Optional[int]:
    """Best digit reading of one balloon interior.

    When the sheet tells us which numbers exist, a candidate that IS one of
    them wins outright — the task is to locate a known number, not to read an
    unknown one — and a reading that is not among them is rejected rather than
    guessed into the gold."""
    best, best_conf = None, -1.0
    for psm in _PSMS:
        config = f"--psm {psm} -c tessedit_char_whitelist=0123456789"
        try:
            data = pytesseract.image_to_data(
                image, config=config, output_type=pytesseract.Output.DICT)
        except Exception:
            continue
        for text, conf in zip(data["text"], data["conf"]):
            token = (text or "").strip()
            if not token.isdigit():
                continue
            number = int(token)
            if not (MIN_BALLOON_NO <= number <= MAX_BALLOON_NO):
                continue
            try:
                confidence = float(conf)
            except (TypeError, ValueError):
                confidence = -1.0
            if expect is not None and number in expect:
                return number
            if confidence >= min_conf and confidence > best_conf:
                best, best_conf = number, confidence
    if expect is not None:
        return None            # nothing matched a number the sheet lists
    return best


def cv_report(pdf_path, page_index: int = 0,
              dpi: int = _DEFAULT_DPI) -> dict:
    """Where rendered-page detection succeeds or fails, stage by stage.

    Distinguishes the three ways it can come up empty: the balloon ink is not
    blue (blue_px ~ 0 while dark_px is large), the outlines fail the size or
    symmetry filter (n_contours large, n_candidates ~ 0), or OCR cannot read
    the interiors (n_candidates large, n_read ~ 0). Counts only — no content."""
    with tempfile.TemporaryDirectory() as tmp:
        render = render_page(pdf_path, dpi=dpi, out_dir=tmp,
                             page_index=page_index)
        bgr = cv2.imread(str(render.png_path))
    if bgr is None:
        return {"unreadable": True}

    blue = bgr[:, :, 0].astype(np.int16)
    green = bgr[:, :, 1].astype(np.int16)
    red = bgr[:, :, 2].astype(np.int16)
    hi = np.maximum(np.maximum(blue, green), red)
    lo = np.minimum(np.minimum(blue, green), red)
    out = {
        "coloured_px": int((((hi - lo) > 30) & (lo < 200)).sum()),
        "dark_px": int((hi < 128).sum()),
    }
    for margin in (15, 40, 80):
        out[f"blue_px_m{margin}"] = int((blue_ink_mask(bgr, margin) > 0).sum())

    scale = dpi / 72.0
    mask = blue_ink_mask(bgr)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                              np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    out["n_contours"] = len(contours)
    sizes = {}
    for contour in contours:
        _, _, w, h = cv2.boundingRect(contour)
        sizes[_bucket_pt(max(w, h) / scale)] = \
            sizes.get(_bucket_pt(max(w, h) / scale), 0) + 1
    out["contour_sizes_pt"] = sizes

    candidates = _candidate_regions(mask, scale)
    out["n_candidates"] = len(candidates)
    out["n_read"] = sum(
        1 for box in candidates
        if (lambda img: img is not None
            and _read_number(img, None, _MIN_CONF) is not None)(
                _prepare_crop(mask, box)))
    return out


_SIZE_EDGES = (4, 8, 16, 24, 40, 80, 160)


def _bucket_pt(value: float) -> str:
    for edge in _SIZE_EDGES:
        if value < edge:
            return f"<{edge}"
    return f">={_SIZE_EDGES[-1]}"


def detect_balloons_cv(pdf_path, page_index: int = 0, dpi: int = _DEFAULT_DPI,
                       expect: Optional[set] = None,
                       min_conf: float = _MIN_CONF) -> List[Balloon]:
    """Balloons read from the rendered page. `expect` restricts results to the
    numbers the sheet lists, which is what keeps OCR noise out of the gold."""
    with tempfile.TemporaryDirectory() as tmp:
        render = render_page(pdf_path, dpi=dpi, out_dir=tmp,
                             page_index=page_index)
        bgr = cv2.imread(str(render.png_path))
        page_rect = render.page_rect
    if bgr is None:
        return []

    mask = blue_ink_mask(bgr)
    if not mask.any():
        return []

    scale = dpi / 72.0
    found = []
    for box in _candidate_regions(mask, scale):
        image = _prepare_crop(mask, box)
        if image is None:
            continue
        number = _read_number(image, expect, min_conf)
        if number is None:
            continue
        x, y, w, h = box
        found.append(Balloon(
            number=number,
            center_pt=(page_rect[0] + (x + w / 2.0) / scale,
                       page_rect[1] + (y + h / 2.0) / scale),
            radius_pt=max(w, h) / (2.0 * scale),
            page=page_index))
    return _dedupe(found)
