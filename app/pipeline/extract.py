import sys
from pathlib import Path
from typing import List, Tuple
from PIL import Image
from app.models import Characteristic
from app.pipeline.render import render_page
from app.pipeline.anchors import extract_anchors
from app.pipeline.balloons import label_balloons, value_region
from app.pipeline.parser import parse_value
from app.pipeline.notes import extract_notes
from app.pipeline.ocr import get_backend

# Notes table region as a fraction of page (top-right block); tuned for the template.
_NOTES_FRAC = (0.55, 0.0, 1.0, 0.22)


def _safe_read(backend, crop) -> Tuple[str, float]:
    """Call backend.read_region(crop), returning ("", 0.0) on any failure."""
    try:
        result = backend.read_region(crop)
        return result.text, result.confidence
    except Exception:
        return "", 0.0


def _score(text: str, conf: float) -> float:
    """Prefer reads that parse into a real dimension (nominal/tolerance present)."""
    c = parse_value(text)
    return (1.0 if c.nominal else 0.0) + (0.5 if c.upper_tol else 0.0) + conf


def _best_read(backend, crop: Image.Image, vertical: bool) -> Tuple[str, float]:
    """Read a crop; for vertical dimensions try both 90 rotations and keep the best."""
    candidates = [crop]
    if vertical:
        candidates = [crop.rotate(-90, expand=True), crop.rotate(90, expand=True)]
    best_text, best_conf, best_score = "", 0.0, -1.0
    for im in candidates:
        text, conf = _safe_read(backend, im)
        s = _score(text, conf)
        if s > best_score:
            best_text, best_conf, best_score = text, conf, s
    return best_text, best_conf


def _clamp(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, x0), max(0, y0), min(w, x1), min(h, y1))


def extract(pdf_path, work_dir, dpi: int = 300, backend=None) -> List[Characteristic]:
    work_dir = Path(work_dir)
    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    image = Image.open(render.png_path).convert("RGB")

    anchors = extract_anchors(pdf_path, scale=render.scale)
    labels = label_balloons(image)
    backend = backend or get_backend()

    results: List[Characteristic] = []
    for a in anchors:
        vr = value_region(labels, (a.x, a.y))
        if vr is None:
            # fallback: a band to the right of the balloon
            box, vertical = (a.x + 30, a.y - 48, a.x + 260, a.y + 48), False
        else:
            box, vertical = vr.box, vr.vertical
        box = _clamp(box, render.width, render.height)
        crop = image.crop(box)
        text, confidence = _best_read(backend, crop, vertical)
        c = parse_value(text)
        c.pos = a.number
        c.balloon_xy = (a.x, a.y)
        c.target_region = box
        c.confidence = confidence
        results.append(c)

    nx0 = render.width * _NOTES_FRAC[0]
    ny0 = render.height * _NOTES_FRAC[1]
    nx1 = render.width * _NOTES_FRAC[2]
    ny1 = render.height * _NOTES_FRAC[3]
    try:
        results.extend(extract_notes(image, (nx0, ny0, nx1, ny1), backend))
    except Exception as e:
        print(f"[sindri.extract] notes extraction failed: {e!r}", file=sys.stderr, flush=True)

    return results
