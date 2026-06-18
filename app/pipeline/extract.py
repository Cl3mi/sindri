import uuid
from pathlib import Path
from typing import List, Tuple
from PIL import Image
from app.models import Characteristic
from app.pipeline.render import render_page
from app.pipeline.detect import detect_characteristics
from app.pipeline.place import number_characteristics, place_balloons
from app.pipeline.parser import parse_value
from app.pipeline.ocr import get_backend

# detector kind -> parser hint
_HINTS = {"material": "material", "note": "note", "gdt": "flatness"}


def _safe_read(backend, crop) -> Tuple[str, float]:
    try:
        result = backend.read_region(crop)
        return result.text, result.confidence
    except Exception:
        return "", 0.0


def _score(text: str, conf: float) -> float:
    c = parse_value(text)
    return (1.0 if c.nominal else 0.0) + (0.5 if c.upper_tol else 0.0) + conf


def _best_read(backend, crop: Image.Image, vertical: bool) -> Tuple[str, float]:
    """Read a crop; for vertical callouts try both 90 rotations and keep the best."""
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
    return (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))


def _is_vertical(box) -> bool:
    return (box[3] - box[1]) > (box[2] - box[0]) * 1.3


def extract(pdf_path, work_dir, dpi: int = 300, backend=None) -> List[Characteristic]:
    work_dir = Path(work_dir)
    backend = backend or get_backend()
    if not hasattr(backend, "detect_regions"):
        raise RuntimeError("auto-ballooning requires the VLM backend")

    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    image = Image.open(render.png_path).convert("RGB")

    detections = detect_characteristics(image, backend)

    results: List[Characteristic] = []
    for d in detections:
        box = _clamp(d.box, render.width, render.height)
        crop = image.crop(box)
        text, confidence = _best_read(backend, crop, _is_vertical(box))
        c = parse_value(text, hint=_HINTS.get(d.kind, ""))
        c.id = uuid.uuid4().hex
        c.kind = d.kind
        c.source = "auto"
        c.target_region = box
        c.confidence = confidence
        results.append(c)

    number_characteristics(results)
    place_balloons(results)
    return results
