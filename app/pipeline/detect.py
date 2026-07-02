"""Detect inspection characteristics on a bare (un-ballooned) drawing.

Stage 1 of the pipeline: tile the rendered page, run the VLM as a detector on
each tile, map tile-local boxes back to page space, then merge and dedupe.
"""
import json
import sys
from dataclasses import dataclass

from app.pipeline.geom import _iou, _x_aligned, _y_close, _union


@dataclass
class Detection:
    box: tuple        # (x0, y0, x1, y1) page-space pixels
    kind: str         # dimension|gdt|surface|note|material|theoretical
    conf: float
    inner_box: tuple = None     # frame-stripped read crop (boxed callouts only)
    cells: int = 1              # cell count for multi-cell GD&T frames
    subtype: str = None         # gdt|theoretical|note_ref (boxed callouts only)


def _starts(length: int, tile: int, step: int):
    if length <= tile:
        return [0]
    starts = list(range(0, length - tile + 1, step))
    if starts[-1] != length - tile:
        starts.append(length - tile)
    return starts


def tile_grid(width: int, height: int, tile: int = 1280, overlap: float = 0.15):
    """Overlapping tile boxes covering the page; last tile in each axis is
    flush to the far edge so nothing is dropped."""
    step = max(1, int(tile * (1 - overlap)))
    boxes = []
    for y0 in _starts(height, tile, step):
        for x0 in _starts(width, tile, step):
            boxes.append((x0, y0, min(x0 + tile, width), min(y0 + tile, height)))
    return boxes


def dedupe(detections, iou_thresh: float = 0.5):
    """Greedy NMS: keep the highest-confidence box, suppress later boxes of the
    SAME kind that overlap it past the threshold. Different kinds never suppress
    each other (a diameter and a GD&T frame may legitimately overlap)."""
    kept = []
    for d in sorted(detections, key=lambda d: -d.conf):
        if all(d.kind != k.kind or _iou(d.box, k.box) < iou_thresh for k in kept):
            kept.append(d)
    return kept


def resolve_cross_kind_overlaps(detections, iou_thresh: float = 0.7):
    """Suppress duplicate detections of the *same physical callout* that survived
    same-kind `dedupe` because they were classified as different kinds (e.g. one
    tile read a value as a `dimension`, an overlapping tile read it as a `gdt`
    frame). Keep the highest-confidence box; drop any other box — of any kind —
    overlapping it past a HIGH threshold. The threshold is deliberately stricter
    than `dedupe`'s so genuinely distinct callouts that merely touch (a diameter
    beside a GD&T frame) are preserved."""
    kept = []
    for d in sorted(detections, key=lambda d: -d.conf):
        if all(_iou(d.box, k.box) < iou_thresh for k in kept):
            kept.append(d)
    return kept


def merge_adjacent(detections, x_tol: int = 20, y_gap: int = 20):
    """Merge same-kind boxes that are horizontally aligned and vertically close,
    so a stacked callout (tolerance over a nominal) becomes one crop. Repeats
    until no further merge is possible."""
    items = list(detections)
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
                if (a.kind == b.kind and _x_aligned(a.box, b.box, x_tol)
                        and _y_close(a.box, b.box, y_gap)):
                    a = Detection(box=_union(a.box, b.box), kind=a.kind,
                                  conf=max(a.conf, b.conf))
                    used[j] = True
                    changed = True
            out.append(a)
        items = out
    return items


_KINDS = {"dimension", "gdt", "surface", "note", "material", "theoretical"}


def parse_detections(raw: str):
    """Parse the VLM's JSON detection output defensively. Tolerates code fences
    and surrounding prose; drops any malformed item; returns [] on total
    failure (never raises)."""
    if not raw:
        return []
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        try:
            b = it["box"]
            box = (int(b[0]), int(b[1]), int(b[2]), int(b[3]))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            kind = it.get("kind", "dimension")
            if kind not in _KINDS:
                kind = "dimension"
            conf = float(it.get("conf", 1.0))
            out.append(Detection(box=box, kind=kind, conf=conf))
        except Exception:
            continue
    return out


# CV box sub-type -> detector kind. note_ref folds into the existing notes path.
_BOX_KIND = {"gdt": "gdt", "theoretical": "theoretical", "note_ref": "note"}


def _box_to_detection(b):
    return Detection(box=b.outer_box, kind=_BOX_KIND[b.subtype], conf=b.conf,
                     inner_box=b.inner_box, cells=b.cells, subtype=b.subtype)


def merge_boxes(vlm_dets, box_dets, iou_thresh: float = 0.5):
    """Intersection hybrid: a CV box is kept ONLY where it overlaps a VLM-detected
    callout — CV supplies the clean frame-stripped crop + cell structure for a box
    the VLM already identified, and suppresses that overlapped VLM detection. CV
    boxes with no VLM overlap are dropped (standalone CV over-detects structural
    rectangles like table cells). VLM detections not covered by a kept CV box are
    returned as-is."""
    converted = [_box_to_detection(b) for b in box_dets]
    kept_cv = [c for c in converted
               if any(_iou(c.box, v.box) > iou_thresh for v in vlm_dets)]
    kept_vlm = [v for v in vlm_dets
                if all(_iou(v.box, c.box) <= iou_thresh for c in kept_cv)]
    return kept_vlm + kept_cv


def detect_characteristics(image, backend, tile: int = 1280, overlap: float = 0.15):
    """Run the detector over overlapping tiles, map detections to page space,
    then merge stacked callouts and dedupe overlaps. A tile whose detection call
    fails is logged and skipped — never fatal."""
    width, height = image.size
    acc = []
    for (tx0, ty0, tx1, ty1) in tile_grid(width, height, tile, overlap):
        tile_img = image.crop((tx0, ty0, tx1, ty1))
        try:
            dets = backend.detect_regions(tile_img)
        except Exception as e:
            print(f"[sindri.detect] tile ({tx0},{ty0}) failed: {e!r}",
                  file=sys.stderr, flush=True)
            continue
        for d in dets:
            acc.append(Detection(
                box=(d.box[0] + tx0, d.box[1] + ty0, d.box[2] + tx0, d.box[3] + ty0),
                kind=d.kind, conf=d.conf))
    from app.pipeline.boxes import detect_boxes
    vlm = dedupe(merge_adjacent(acc))
    merged = merge_boxes(vlm, detect_boxes(image))
    return resolve_cross_kind_overlaps(merged)
