import re
import uuid
from pathlib import Path
from typing import Tuple
from PIL import Image
from app.models import Characteristic, ExtractionResult
from app.pipeline.render import render_page
from app.pipeline.detect import detect_characteristics
from app.pipeline import boxes as bx
from app.pipeline.place import number_characteristics, place_balloons
from app.pipeline.parser import parse_value
from app.pipeline.ocr import get_backend
from app.pipeline.review import review_flags
from app.pipeline import notes_block as nb
from app.pipeline import marks_block as mb
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
    # An empty winning read is not "rotation-ambiguous" — both orientations
    # simply read nothing. Only flag ambiguity when a real read was produced and
    # the runner-up scores within EPS of it.
    ambiguous = (len(scored) >= 2 and bool((best_text or "").strip())
                 and (best_score - scored[1][0]) < ROTATION_EPS)
    return best_text, best_conf, ambiguous


def _clamp(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))


# Read-crop normalization: give the reader a little context around the (tight)
# box and upscale small crops so faint sub-mm text and stacked tolerances read
# consistently instead of "sometimes". Frame-stripped CV inner boxes are read
# with pad=0 (padding would re-introduce the border the crop deliberately removed).
_CROP_PAD = 6           # px of context added around a VLM read box
_MIN_CROP_H = 40        # upscale crops shorter than this…
_MAX_UPSCALE = 3.0      # …but never by more than this factor


def _prep_crop(image, box, w, h, pad: int):
    x0, y0, x1, y1 = box
    if pad:
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    crop = image.crop((x0, y0, x1, y1))
    ch = crop.height
    if 0 < ch < _MIN_CROP_H:
        scale = min(_MAX_UPSCALE, _MIN_CROP_H / ch)
        crop = crop.resize((max(1, int(crop.width * scale)),
                            max(1, int(ch * scale))), Image.LANCZOS)
    return crop


def _is_vertical(box) -> bool:
    return (box[3] - box[1]) > (box[2] - box[0]) * 1.3


def _regions_overlap(a, b, min_frac: float = 0.5) -> bool:
    """True if boxes a and b overlap by at least `min_frac` of the smaller box's
    area — used to detect when the notes and marks locators found the same
    physical legend (a small corner touch does not count)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return smaller > 0 and inter / smaller >= min_frac


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

    # Locate the notes and marks legends first. On many drawings there is a
    # single top-right legend that BOTH locators find; when their regions
    # coincide the deterministic CV marks locator owns it and the notes region
    # is dropped, so the legend is neither double-read nor double-masked.
    emit("notes", "Reading notes block")
    region = nb.locate_notes_block(image, backend)
    region_marks = mb.locate_marks_block(image)
    if (region is not None and region_marks is not None
            and _regions_overlap(region.outer_box, region_marks.outer_box)):
        region = None

    # Notes-block path: read, parse, mask. Any failure leaves notes=None
    # and the rest of the pipeline runs unchanged.
    notes_obj = None
    if region is not None:
        raw_notes = nb.read_notes_block(image, region, backend)
        notes_obj = nb.parse_notes_block(raw_notes, region.outer_box)
        known_parents = {n.pos for n in notes_obj.notes if n.parent_pos is None}
        two_columns = len(region.lang_columns) == 2
        for n in notes_obj.notes:
            n.needs_review, n.review_reasons = nb.review_flags_note(
                n, two_columns=two_columns, known_parents=known_parents)

    # Marks-block path: top-right legend table.
    marks_obj = None
    if region_marks is not None:
        raw_marks = mb.read_marks_block(image, region_marks, backend)
        marks_obj = mb.parse_marks_block(raw_marks, region_marks.outer_box)
        two_columns_marks = len(region_marks.lang_columns) == 2
        for m in marks_obj.marks:
            m.needs_review, m.review_reasons = mb.review_flags_mark(
                m, two_columns=two_columns_marks)

    image_for_detect = image
    if region is not None:
        image_for_detect = nb.mask_region(image_for_detect, region)
    if region_marks is not None:
        image_for_detect = mb.mask_region(image_for_detect, region_marks)

    # Title-block path: locate the bottom-right Schriftfeld, read its cells as
    # label/value fields, and mask it so its text is not misread as dimensions.
    # Locate runs on the raw image; the bottom-right quadrant restriction makes
    # overlap with a (typically elsewhere) notes block very unlikely.
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
        # Tighten generous VLM boxes to their ink so the balloon anchors on the
        # real glyph corner and the read crop isn't diluted. CV boxes already
        # carry an exact frame-stripped inner_box, so leave those untouched.
        if d.inner_box is None:
            outer = _clamp(bx.tighten_to_ink(image, outer),
                           render.width, render.height)
        read_box = _clamp(d.inner_box, render.width, render.height) if d.inner_box else outer
        crop = _prep_crop(image, read_box, render.width, render.height,
                          pad=0 if d.inner_box else _CROP_PAD)
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
    place_balloons(results, dpi=dpi)
    # Free text outside the structured blocks (e.g. margin notes). Exclude the
    # notes/marks/title regions AND every region the main detector already
    # captured, so loose_text only adds text nothing else picked up
    # (no double-extraction, no redundant reads).
    exclude = [b for b in (tb_region.outer_box if tb_region else None,
                           region.outer_box if region is not None else None,
                           region_marks.outer_box if region_marks is not None else None)
               if b is not None]
    exclude += [c.target_region for c in results if c.target_region is not None]
    title_fields += tb.loose_text(image, backend, exclude)
    return ExtractionResult(characteristics=results, notes=notes_obj,
                            title_block=title_fields, marks=marks_obj)
