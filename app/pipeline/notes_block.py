"""Notes-block path: locate the general-notes table, read it as structured
bilingual data with sub-bullet linkage, mask it before the main detector
runs so its inline bullets cannot be misclassified as page-level callouts."""
from typing import List, Tuple

from app.models import Note, NoteBlock
from app.pipeline.legend_parse import parse_rows


def parse_notes_block(raw: str, region: Tuple[float, float, float, float]) -> NoteBlock:
    """Parse a notes-block transcription (JSON or tolerant text) into a NoteBlock
    via the shared legend parser. A row carrying a sub-index becomes a child note
    linked to its parent pos. Malformed input yields no notes (non-fatal)."""
    notes: List[Note] = []
    for r in parse_rows(raw):
        # A bare number with no description is an on-drawing reference bubble
        # (or a stray detection), never a genuine notes row — drop it so it
        # cannot pollute the notes table.
        if not (r["en"] or "").strip() and not (r["de"] or "").strip():
            continue
        if r["sub"] is None:
            notes.append(Note(pos=r["pos"], text_en=r["en"], text_de=r["de"],
                              raw_text=r["raw"]))
        else:
            notes.append(Note(pos=r["sub"], parent_pos=r["pos"], sub_index=r["sub"],
                              text_en=r["en"], text_de=r["de"], raw_text=r["raw"]))
    return NoteBlock(region=region, notes=notes)


def review_flags_note(note: Note, two_columns: bool,
                      known_parents: set) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a parsed note.

    Gating: an empty read is its own reason and does not also report
    'missing translation'."""
    reasons: List[str] = []
    if not (note.raw_text or "").strip():
        reasons.append("empty read")
    else:
        if two_columns and (not note.text_en.strip() or not note.text_de.strip()):
            reasons.append("missing translation")
    if note.parent_pos is not None and note.parent_pos not in known_parents:
        reasons.append("orphan sub-bullet")
    return bool(reasons), reasons


from dataclasses import dataclass
from PIL import Image, ImageDraw


@dataclass
class NotesBlockRegion:
    outer_box: Tuple[int, int, int, int]
    lang_columns: List[Tuple[int, int]]


def mask_region(image: Image.Image, region: NotesBlockRegion) -> Image.Image:
    """Return a copy of `image` with `region.outer_box` filled white. The
    original image is preserved so downstream manual re-reads still work."""
    out = image.copy()
    x0, y0, x1, y1 = region.outer_box
    if x1 > x0 and y1 > y0:
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill="white")
    return out


import sys
from app.pipeline.geom import _iou, _x_aligned, _y_close, _union
from app.pipeline.detect import tile_grid, Detection
from app.pipeline.boxes import detect_boxes


_LOCATOR_PAD = 8                # px padding when no CV snap is available
_SNAP_IOU = 0.4                 # IoU threshold to consider a CV rectangle a snap
_NOTE_CLUSTER_X_TOL = 30        # px: same-column note detections
_NOTE_CLUSTER_Y_GAP = 40        # px: vertical gap allowed between adjacent rows


def _cluster_notes(dets):
    """Merge same-column, vertically-close note detections into one bounding
    box. Returns the largest merged cluster, or None if no notes."""
    notes = [d for d in dets if d.kind == "note"]
    if not notes:
        return None
    items = [d.box for d in notes]
    changed = True
    while changed:
        changed = False
        out = []
        used = [False] * len(items)
        for i in range(len(items)):
            if used[i]:
                continue
            a = items[i]
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                b = items[j]
                if (_x_aligned(a, b, _NOTE_CLUSTER_X_TOL)
                        and _y_close(a, b, _NOTE_CLUSTER_Y_GAP)):
                    a = _union(a, b)
                    used[j] = True
                    changed = True
            out.append(a)
        items = out
    items.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return items[0]


def _snap_to_cv(proposal, image, cv_boxes):
    candidates = [b.outer_box for b in cv_boxes
                  if _iou(proposal, b.outer_box) >= _SNAP_IOU
                  and (b.outer_box[2] - b.outer_box[0]) * (b.outer_box[3] - b.outer_box[1])
                      >= (proposal[2] - proposal[0]) * (proposal[3] - proposal[1])]
    if not candidates:
        return proposal
    candidates.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return candidates[0]


def _pad(box, image):
    w, h = image.size
    x0, y0, x1, y1 = box
    return (max(0, int(x0) - _LOCATOR_PAD), max(0, int(y0) - _LOCATOR_PAD),
            min(w, int(x1) + _LOCATOR_PAD), min(h, int(y1) + _LOCATOR_PAD))


def _infer_columns(image, box):
    """Return language-column x-ranges inside `box`. 2 columns if a strong
    vertical divider is found near the middle, else 1."""
    import numpy as np
    import cv2
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


def locate_notes_block(image, backend):
    """Three-step hybrid locator: VLM proposes notes detections; CV snaps the
    region to the overlapping rectangle if one exists; columns inferred from
    the ink density inside the snapped box. Never raises; any failure returns
    None and the pipeline runs without a notes section."""
    try:
        width, height = image.size
        acc = []
        for (tx0, ty0, tx1, ty1) in tile_grid(width, height):
            tile_img = image.crop((tx0, ty0, tx1, ty1))
            try:
                dets = backend.detect_regions(tile_img)
            except Exception as e:
                print(f"[sindri.notes_block] tile ({tx0},{ty0}) failed: {e!r}",
                      file=sys.stderr, flush=True)
                continue
            for d in dets:
                if d.kind != "note":
                    continue
                acc.append(Detection(
                    box=(d.box[0] + tx0, d.box[1] + ty0,
                         d.box[2] + tx0, d.box[3] + ty0),
                    kind="note", conf=d.conf))
        proposal = _cluster_notes(acc)
        if proposal is None:
            return None
        try:
            cv_boxes = detect_boxes(image)
        except Exception:
            cv_boxes = []
        snapped = _snap_to_cv(proposal, image, cv_boxes)
        if snapped == proposal:
            snapped = _pad(proposal, image)
        try:
            columns = _infer_columns(image, snapped)
        except Exception:
            columns = [(snapped[0], snapped[2])]
        return NotesBlockRegion(outer_box=tuple(int(v) for v in snapped),
                                lang_columns=columns)
    except Exception as e:
        print(f"[sindri.notes_block] locator failed: {e!r}",
              file=sys.stderr, flush=True)
        return None


def read_notes_block(image, region: NotesBlockRegion, backend) -> str:
    """Read the notes block once and return the raw transcription text.
    Prefers a backend method named `read_notes_block` if the backend exposes
    one (lets the VLM backend use a dedicated prompt); otherwise falls back
    to the generic `read_region`. Never raises."""
    crop = image.crop(region.outer_box)
    try:
        if hasattr(backend, "read_notes_block"):
            result = backend.read_notes_block(crop)
        else:
            result = backend.read_region(crop)
        return (result.text or "")
    except Exception as e:
        print(f"[sindri.notes_block] read failed: {e!r}",
              file=sys.stderr, flush=True)
        return ""
