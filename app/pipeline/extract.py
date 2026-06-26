import re
import uuid
from pathlib import Path
from typing import Tuple
from PIL import Image
from app.models import Characteristic, ExtractionResult
from app.pipeline.render import render_page
from app.pipeline.detect import detect_characteristics
from app.pipeline.place import number_characteristics, place_balloons
from app.pipeline.parser import parse_value
from app.pipeline.ocr import get_backend
from app.pipeline.review import review_flags
from app.pipeline import notes_block as nb
from app.pipeline import title_block as tb

# detector kind -> parser hint
_HINTS = {"material": "material", "note": "note", "gdt": "gdt",
          "theoretical": "theoretical"}

# A bare 100-series integer in a box is a note-reference, not a dimension.
_NOTE_REF_RE = re.compile(r"^\s*(10[0-9]|1[1-9][0-9])\s*$")

# how close the two rotation candidates must score to count as ambiguous
ROTATION_EPS = 0.15


def _safe_read(reader, crop) -> Tuple[str, float]:
    try:
        result = reader(crop)
        return result.text, result.confidence
    except Exception:
        return "", 0.0


def _score(text: str, conf: float) -> float:
    c = parse_value(text)
    return (1.0 if c.nominal else 0.0) + (0.5 if c.upper_tol else 0.0) + conf


def _best_read(backend, crop: Image.Image, vertical: bool) -> Tuple[str, float, bool]:
    candidates = [crop]
    if vertical:
        candidates = [crop.rotate(-90, expand=True), crop.rotate(90, expand=True)]
    scored = []
    for im in candidates:
        text, conf = _safe_read(backend.read_region, im)
        scored.append((_score(text, conf), text, conf))
    scored.sort(key=lambda t: -t[0])
    best_score, best_text, best_conf = scored[0]
    ambiguous = len(scored) >= 2 and (best_score - scored[1][0]) < ROTATION_EPS
    return best_text, best_conf, ambiguous


def _clamp(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))


def _is_vertical(box) -> bool:
    return (box[3] - box[1]) > (box[2] - box[0]) * 1.3


def extract(pdf_path, work_dir, dpi: int = 300, backend=None,
            progress=None) -> ExtractionResult:
    work_dir = Path(work_dir)
    backend = backend or get_backend()

    # `progress(step, detail, current, total)` lets callers stream pipeline
    # status to the UI; it is a no-op when no callback is supplied.
    def emit(step, detail="", current=None, total=None):
        if progress is not None:
            progress(step, detail, current, total)

    if not hasattr(backend, "detect_regions"):
        raise RuntimeError("auto-ballooning requires the VLM backend")

    emit("render", "Rendering page")
    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    image = Image.open(render.png_path).convert("RGB")

    # Notes-block path: locate, read, parse, mask. Any failure leaves notes=None
    # and the rest of the pipeline runs unchanged.
    emit("notes", "Reading notes block")
    region = nb.locate_notes_block(image, backend)
    notes_obj = None
    if region is not None:
        raw_notes = nb.read_notes_block(image, region, backend)
        notes_obj = nb.parse_notes_block(raw_notes, region.outer_box)
        known_parents = {n.pos for n in notes_obj.notes if n.parent_pos is None}
        two_columns = len(region.lang_columns) == 2
        for n in notes_obj.notes:
            n.needs_review, n.review_reasons = nb.review_flags_note(
                n, two_columns=two_columns, known_parents=known_parents)
        image_for_detect = nb.mask_region(image, region)
    else:
        image_for_detect = image

    # Title-block path: locate the bottom-right Schriftfeld, read its cells as
    # label/value fields, and mask it so its text is not misread as dimensions.
    emit("title", "Reading title block")
    tb_region = tb.locate_title_block(image)
    title_fields = []
    if tb_region is not None:
        title_fields = tb.read_title_block(image, tb_region, backend)
        image_for_detect = tb.mask_region(image_for_detect, tb_region)

    emit("detect", "Detecting characteristics")
    detections = detect_characteristics(image_for_detect, backend)

    known_positions = ({n.pos for n in notes_obj.notes if n.parent_pos is None}
                       if notes_obj is not None else None)
    total = len(detections)
    emit("ocr", f"Reading {total} region{'' if total == 1 else 's'}", 0, total)
    results = []
    for i, d in enumerate(detections):
        outer = _clamp(d.box, render.width, render.height)
        read_box = _clamp(d.inner_box, render.width, render.height) if d.inner_box else outer
        crop = image.crop(read_box)
        if d.subtype == "gdt" and hasattr(backend, "read_region_gdt"):
            text, confidence = _safe_read(backend.read_region_gdt, crop)
            rotation_ambiguous = False
        else:
            text, confidence, rotation_ambiguous = _best_read(
                backend, crop, _is_vertical(read_box))

        hint = _HINTS.get(d.kind, "")
        subtype = d.subtype or ""
        kind = d.kind
        if subtype == "theoretical" and _NOTE_REF_RE.match(text or ""):
            hint, subtype, kind = "note", "note_ref", "note"

        c = parse_value(text, hint=hint)
        c.id = uuid.uuid4().hex
        c.kind = kind
        c.subtype = subtype
        c.source = "auto"
        c.target_region = outer
        c.confidence = confidence
        if subtype == "note_ref":
            try:
                c.note_ref_pos = int((text or "").strip())
            except ValueError:
                c.note_ref_pos = None
        c.needs_review, c.review_reasons = review_flags(
            c, rotation_ambiguous, known_note_positions=known_positions)
        results.append(c)
        emit("ocr", "Reading regions", i + 1, total)

    emit("place", "Placing balloons")
    number_characteristics(results)
    place_balloons(results)
    # Free text outside the structured blocks (e.g. margin notes).
    exclude = [b for b in (tb_region.outer_box if tb_region else None,
                           region.outer_box if region is not None else None)
               if b is not None]
    title_fields += tb.loose_text(image, backend, exclude)
    return ExtractionResult(characteristics=results, notes=notes_obj,
                            title_block=title_fields)
