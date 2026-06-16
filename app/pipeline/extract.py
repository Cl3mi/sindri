from pathlib import Path
from typing import List
from PIL import Image
from app.models import Characteristic
from app.pipeline.render import render_page
from app.pipeline.anchors import extract_anchors
from app.pipeline.vectors import extract_segments
from app.pipeline.tracer import trace_target
from app.pipeline.parser import parse_value
from app.pipeline.notes import extract_notes
from app.pipeline.ocr import get_backend

# Notes table region as a fraction of page (top-right block); tuned for the template.
_NOTES_FRAC = (0.55, 0.0, 1.0, 0.22)


def _hint_for(anchor_number: int) -> str:
    return ""  # extendable: map specific balloons to 'material'/'flatness' if needed


def _safe_read(backend, crop):
    """Call backend.read_region(crop), returning ("", 0.0) on any failure."""
    try:
        result = backend.read_region(crop)
        return result.text, result.confidence
    except Exception:
        return "", 0.0


def extract(pdf_path, work_dir, dpi: int = 300) -> List[Characteristic]:
    work_dir = Path(work_dir)
    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    scale = render.scale
    image = Image.open(render.png_path).convert("RGB")

    anchors = extract_anchors(pdf_path, scale=scale)
    segments = extract_segments(pdf_path, scale=scale)
    backend = get_backend()

    results: List[Characteristic] = []
    for a in anchors:
        region = trace_target((a.x, a.y), segments)
        x0, y0, x1, y1 = region
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(render.width, x1), min(render.height, y1)
        crop = image.crop((x0, y0, x1, y1))
        text, confidence = _safe_read(backend, crop)
        c = parse_value(text, hint=_hint_for(a.number))
        c.pos = a.number
        c.balloon_xy = (a.x, a.y)
        c.target_region = (x0, y0, x1, y1)
        c.confidence = confidence
        results.append(c)

    nx0 = render.width * _NOTES_FRAC[0]
    ny0 = render.height * _NOTES_FRAC[1]
    nx1 = render.width * _NOTES_FRAC[2]
    ny1 = render.height * _NOTES_FRAC[3]
    try:
        results.extend(extract_notes(image, (nx0, ny0, nx1, ny1), backend))
    except Exception:
        pass

    return results
