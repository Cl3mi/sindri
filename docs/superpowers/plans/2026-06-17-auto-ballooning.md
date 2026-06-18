# Auto-Ballooning Bare Drawings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect every inspection characteristic on a bare (un-ballooned) engineering-drawing PDF, number and place balloons, read each value, and produce an inspection `.xlsx` plus a ballooned PDF — with a full-edit human review step.

**Architecture:** Two-stage pipeline. Stage 1 (`detect.py`) tiles the rendered page and runs the VLM as a *detector* returning callout boxes; detections are mapped to page coordinates, merged and deduped. Stage 2 reuses the existing `read_region` transcription + `parse_value` parser on each detected crop. `place.py` numbers and positions balloons; `ballooned_pdf.py` draws them onto a PDF copy. The old balloon-reading path (`anchors.py`, `balloons.py`) is retired.

**Tech Stack:** Python, FastAPI, PyMuPDF (`fitz`), Pillow, OpenCV/NumPy, Qwen2.5-VL via transformers (GPU), openpyxl, pytest, vanilla JS frontend.

**Spec:** `docs/superpowers/specs/2026-06-17-auto-ballooning-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `app/models.py` | **modify** — add `id`, `kind`, `source` to `Characteristic` |
| `app/pipeline/detect.py` | **new** — `Detection`, `tile_grid`, `_iou`, `dedupe`, `merge_adjacent`, `parse_detections`, `detect_characteristics` |
| `app/pipeline/ocr/vlm_backend.py` | **modify** — add `detect_regions` + detection prompt |
| `app/pipeline/place.py` | **new** — `number_characteristics`, `place_balloons` |
| `app/pipeline/extract.py` | **rewrite** — orchestrate detect→read→number→place |
| `app/pipeline/ballooned_pdf.py` | **new** — `render_ballooned_pdf` |
| `app/main.py` | **modify** — `/api/read_region`, `/api/export/pdf`, upload error handling |
| `app/static/app.js`, `app/static/index.html` | **modify** — full-edit review UI |
| `app/pipeline/anchors.py`, `app/pipeline/balloons.py` | **delete** — retired |
| `tests/conftest.py` | **modify** — add `StubVLMBackend` |
| `tests/test_detect.py`, `tests/test_place.py`, `tests/test_ballooned_pdf.py` | **new** |
| `tests/test_anchors.py`, `tests/test_balloons.py` | **delete** |
| `tests/test_pipeline_integration.py`, `tests/test_api.py`, `tests/test_models.py` | **modify** |

All commits below assume the venv is active: `. .venv/bin/activate`.

---

## Task 1: Extend the Characteristic model

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
from app.models import Characteristic

def test_characteristic_has_id_kind_source_defaults():
    c = Characteristic(pos=1)
    assert c.id == ""
    assert c.kind == ""
    assert c.source == "auto"

def test_characteristic_accepts_new_fields():
    c = Characteristic(pos=2, id="abc", kind="dimension", source="manual")
    assert c.id == "abc"
    assert c.kind == "dimension"
    assert c.source == "manual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `Characteristic` has no field `id`/`kind`/`source` (pydantic ignores or errors on unknown attr access).

- [ ] **Step 3: Add the fields**

In `app/models.py`, inside `class Characteristic(BaseModel)`, add after `confidence`:

```python
    id: str = ""                 # stable per-row id for the review UI
    kind: str = ""               # detector kind: dimension|gdt|surface|note|material
    source: str = "auto"         # "auto" (detected) or "manual" (user-added)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add id/kind/source fields to Characteristic"
```

---

## Task 2: Detection dataclass + tile grid

**Files:**
- Create: `app/pipeline/detect.py`
- Test: `tests/test_detect.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_detect.py`:

```python
from app.pipeline.detect import Detection, tile_grid


def test_detection_dataclass_fields():
    d = Detection(box=(0, 0, 10, 10), kind="dimension", conf=0.9)
    assert d.box == (0, 0, 10, 10)
    assert d.kind == "dimension"
    assert d.conf == 0.9


def test_tile_grid_single_tile_when_image_smaller_than_tile():
    boxes = tile_grid(800, 600, tile=1280, overlap=0.15)
    assert boxes == [(0, 0, 800, 600)]


def test_tile_grid_covers_width_with_overlap():
    boxes = tile_grid(2000, 1000, tile=1280, overlap=0.15)
    # two columns, one row; last tile flush to the right edge
    assert len(boxes) == 2
    assert boxes[0] == (0, 0, 1280, 1000)
    assert boxes[1] == (720, 0, 2000, 1000)
    # tiles overlap (720 < 1280)
    assert boxes[1][0] < boxes[0][2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_detect.py -v`
Expected: FAIL — `No module named 'app.pipeline.detect'`

- [ ] **Step 3: Create the module with Detection and tile_grid**

Create `app/pipeline/detect.py`:

```python
"""Detect inspection characteristics on a bare (un-ballooned) drawing.

Stage 1 of the pipeline: tile the rendered page, run the VLM as a detector on
each tile, map tile-local boxes back to page space, then merge and dedupe.
"""
from dataclasses import dataclass


@dataclass
class Detection:
    box: tuple        # (x0, y0, x1, y1) page-space pixels
    kind: str         # dimension|gdt|surface|note|material
    conf: float


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_detect.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/detect.py tests/test_detect.py
git commit -m "feat: Detection dataclass and overlapping tile grid"
```

---

## Task 3: IoU + dedupe (non-max suppression)

**Files:**
- Modify: `app/pipeline/detect.py`
- Test: `tests/test_detect.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_detect.py`:

```python
from app.pipeline.detect import dedupe


def test_dedupe_collapses_overlapping_same_kind():
    a = Detection(box=(0, 0, 100, 100), kind="dimension", conf=0.9)
    b = Detection(box=(5, 5, 105, 105), kind="dimension", conf=0.7)  # high IoU
    kept = dedupe([a, b], iou_thresh=0.5)
    assert len(kept) == 1
    assert kept[0].conf == 0.9          # higher-confidence box kept


def test_dedupe_keeps_different_kinds_that_overlap():
    a = Detection(box=(0, 0, 100, 100), kind="dimension", conf=0.9)
    b = Detection(box=(0, 0, 100, 100), kind="gdt", conf=0.8)
    kept = dedupe([a, b], iou_thresh=0.5)
    assert len(kept) == 2


def test_dedupe_keeps_distant_same_kind():
    a = Detection(box=(0, 0, 50, 50), kind="dimension", conf=0.9)
    b = Detection(box=(500, 500, 550, 550), kind="dimension", conf=0.8)
    assert len(dedupe([a, b])) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_detect.py -v`
Expected: FAIL — cannot import `dedupe`

- [ ] **Step 3: Add _iou and dedupe**

Append to `app/pipeline/detect.py`:

```python
def _iou(a: tuple, b: tuple) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def dedupe(detections, iou_thresh: float = 0.5):
    """Greedy NMS: keep the highest-confidence box, suppress later boxes of the
    SAME kind that overlap it past the threshold. Different kinds never suppress
    each other (a diameter and a GD&T frame may legitimately overlap)."""
    kept = []
    for d in sorted(detections, key=lambda d: -d.conf):
        if all(d.kind != k.kind or _iou(d.box, k.box) < iou_thresh for k in kept):
            kept.append(d)
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_detect.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/detect.py tests/test_detect.py
git commit -m "feat: IoU-based NMS dedupe for detections"
```

---

## Task 4: Merge adjacent (stacked callouts)

**Files:**
- Modify: `app/pipeline/detect.py`
- Test: `tests/test_detect.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_detect.py`:

```python
from app.pipeline.detect import merge_adjacent


def test_merge_adjacent_combines_vertically_stacked_same_kind():
    nominal = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.8)
    tol = Detection(box=(12, 35, 52, 55), kind="dimension", conf=0.6)  # gap 5
    merged = merge_adjacent([nominal, tol], x_tol=20, y_gap=20)
    assert len(merged) == 1
    assert merged[0].box == (10, 10, 52, 55)


def test_merge_adjacent_leaves_far_apart_boxes():
    a = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.8)
    b = Detection(box=(10, 200, 50, 220), kind="dimension", conf=0.6)  # gap 170
    assert len(merge_adjacent([a, b], x_tol=20, y_gap=20)) == 2


def test_merge_adjacent_does_not_merge_different_kinds():
    a = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.8)
    b = Detection(box=(12, 35, 52, 55), kind="note", conf=0.6)
    assert len(merge_adjacent([a, b], x_tol=20, y_gap=20)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_detect.py -v`
Expected: FAIL — cannot import `merge_adjacent`

- [ ] **Step 3: Add merge_adjacent and helpers**

Append to `app/pipeline/detect.py`:

```python
def _x_aligned(a: tuple, b: tuple, x_tol: int) -> bool:
    return a[0] <= b[2] + x_tol and b[0] <= a[2] + x_tol


def _y_close(a: tuple, b: tuple, y_gap: int) -> bool:
    gap = max(a[1] - b[3], b[1] - a[3])   # positive when boxes don't vertically overlap
    return gap <= y_gap


def _union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_detect.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/detect.py tests/test_detect.py
git commit -m "feat: merge vertically-stacked same-kind detections"
```

---

## Task 5: Defensive detection-JSON parser

**Files:**
- Modify: `app/pipeline/detect.py`
- Test: `tests/test_detect.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_detect.py`:

```python
from app.pipeline.detect import parse_detections


def test_parse_detections_valid_json():
    raw = '[{"box":[1,2,3,4],"kind":"dimension","conf":0.9}]'
    dets = parse_detections(raw)
    assert len(dets) == 1
    assert dets[0].box == (1, 2, 3, 4)
    assert dets[0].kind == "dimension"


def test_parse_detections_strips_code_fence_and_prose():
    raw = 'Here you go:\n```json\n[{"box":[0,0,5,5],"kind":"note"}]\n```'
    dets = parse_detections(raw)
    assert len(dets) == 1
    assert dets[0].kind == "note"
    assert dets[0].conf == 1.0          # default when omitted


def test_parse_detections_garbage_returns_empty():
    assert parse_detections("not json at all") == []
    assert parse_detections("") == []


def test_parse_detections_skips_invalid_items():
    raw = ('[{"box":[0,0,5,5],"kind":"dimension"},'
           '{"box":[10,10,8,8],"kind":"dimension"},'    # zero/negative area
           '{"kind":"note"},'                            # missing box
           '{"box":[1,1,2,2],"kind":"weird"}]')          # unknown kind -> dimension
    dets = parse_detections(raw)
    assert len(dets) == 2
    assert dets[0].box == (0, 0, 5, 5)
    assert dets[1].kind == "dimension"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_detect.py -v`
Expected: FAIL — cannot import `parse_detections`

- [ ] **Step 3: Add parse_detections**

Add near the top of `app/pipeline/detect.py` (after the imports add `import json`), and append the function:

```python
import json

_KINDS = {"dimension", "gdt", "surface", "note", "material"}


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
```

Put `import json` at the top with the other imports rather than mid-file; the placement above is for reading convenience.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_detect.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/detect.py tests/test_detect.py
git commit -m "feat: defensive parser for VLM detection JSON"
```

---

## Task 6: detect_characteristics orchestration + StubVLMBackend

**Files:**
- Modify: `app/pipeline/detect.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_detect.py`

- [ ] **Step 1: Add the shared stub backend to conftest**

Append to `tests/conftest.py`:

```python
from app.pipeline.detect import Detection
from app.pipeline.ocr.base import OcrResult


class StubVLMBackend:
    """Test double for the VLM backend: returns canned detections (tile-local)
    and a canned transcription. Has detect_regions, so extract() treats it as a
    detection-capable backend."""

    def __init__(self, detections=None, text="1,2 +0,1 -0,1", confidence=0.9):
        self._detections = detections or []
        self._text = text
        self._confidence = confidence

    def detect_regions(self, image):
        return [Detection(box=d.box, kind=d.kind, conf=d.conf)
                for d in self._detections]

    def read_region(self, image):
        return OcrResult(text=self._text, confidence=self._confidence)
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_detect.py`:

```python
from PIL import Image
from app.pipeline.detect import detect_characteristics
from tests.conftest import StubVLMBackend


def test_detect_characteristics_single_tile_passes_box_through():
    img = Image.new("RGB", (400, 300), "white")          # smaller than one tile
    backend = StubVLMBackend(detections=[Detection((10, 20, 60, 40), "dimension", 0.9)])
    dets = detect_characteristics(img, backend)
    assert len(dets) == 1
    assert dets[0].box == (10, 20, 60, 40)               # no offset for a single tile


def test_detect_characteristics_offsets_per_tile():
    # 2000x1000 -> two tiles at x-origin 0 and 720; stub returns one det per tile
    img = Image.new("RGB", (2000, 1000), "white")
    backend = StubVLMBackend(detections=[Detection((0, 0, 30, 30), "note", 0.8)])
    dets = detect_characteristics(img, backend)
    # one detection per tile, offset by the tile origin, not overlapping -> both kept
    xs = sorted(d.box[0] for d in dets)
    assert xs == [0, 720]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_detect.py -v`
Expected: FAIL — cannot import `detect_characteristics`

- [ ] **Step 4: Add detect_characteristics**

Append to `app/pipeline/detect.py` (add `import sys` to the imports):

```python
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
    return dedupe(merge_adjacent(acc))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_detect.py -v`
Expected: PASS (15 tests)

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/detect.py tests/conftest.py tests/test_detect.py
git commit -m "feat: tiled detect_characteristics orchestration + test stub"
```

---

## Task 7: VLMBackend.detect_regions

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py`

Note: the real model call requires a GPU and is exercised only by the GPU-gated
integration check (Task 15). The JSON parsing it relies on is already unit-tested
(Task 5). This task wires the prompt + decode to `parse_detections`.

- [ ] **Step 1: Add the detection prompt constant**

In `app/pipeline/ocr/vlm_backend.py`, after the `_PROMPT` definition add:

```python
# Detection prompt: the model LOCATES every inspection callout in the tile and
# returns JSON only. It does not need to transcribe accurately — Stage 2 re-reads
# each crop with read_region. kind is coarse; box is in pixels of THIS image.
_DETECT_PROMPT = (
    "This image is a tile cropped from a mechanical engineering drawing. Find "
    "EVERY inspection callout: linear/diameter/radius dimensions with their "
    "tolerances, GD&T feature-control frames, surface-finish symbols, numbered "
    "notes, and material/process specifications. Return ONLY a JSON array, no "
    "prose. Each element: {\"box\":[x0,y0,x1,y1],\"kind\":\"dimension|gdt|"
    "surface|note|material\"}. box is pixel coordinates within this image. If "
    "there are no callouts, return []."
)
```

- [ ] **Step 2: Add the detect_regions method**

In `class VLMBackend`, add a method (and add `from app.pipeline.detect import parse_detections` as a lazy import inside the method to avoid a circular import at module load):

```python
    def detect_regions(self, image: Image.Image):
        from app.pipeline.detect import parse_detections
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _DETECT_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=1024, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return parse_detections(text)
```

- [ ] **Step 3: Verify the module imports cleanly (no GPU needed for import)**

Run: `python -c "import ast; ast.parse(open('app/pipeline/ocr/vlm_backend.py').read()); print('ok')"`
Expected: `ok` (syntax check; the file is not imported because transformers/torch may be absent on a CPU box — the lazy imports inside `__init__`/`detect_regions` keep import-time clean).

- [ ] **Step 4: Run the detect tests to confirm no regression**

Run: `pytest tests/test_detect.py -v`
Expected: PASS (15 tests — the circular-import guard works because `detect.py` does not import `vlm_backend`).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py
git commit -m "feat: VLMBackend.detect_regions detection prompt"
```

---

## Task 8: Spatial numbering

**Files:**
- Create: `app/pipeline/place.py`
- Test: `tests/test_place.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_place.py`:

```python
from app.models import Characteristic
from app.pipeline.place import number_characteristics


def _char(box):
    c = Characteristic(pos=0)
    c.target_region = box
    return c


def test_number_characteristics_reading_order():
    # two on the top row (left, right), one on a lower row
    top_left = _char((10, 10, 40, 30))
    top_right = _char((200, 12, 240, 32))      # same band as top_left
    bottom = _char((10, 300, 40, 320))
    ordered = number_characteristics([bottom, top_right, top_left], band_tol=60)
    by_pos = {c.pos: c for c in ordered}
    assert by_pos[1].target_region == top_left.target_region
    assert by_pos[2].target_region == top_right.target_region
    assert by_pos[3].target_region == bottom.target_region


def test_number_characteristics_assigns_sequential_pos():
    chars = [_char((0, i * 100, 20, i * 100 + 20)) for i in range(5)]
    ordered = number_characteristics(chars)
    assert sorted(c.pos for c in ordered) == [1, 2, 3, 4, 5]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_place.py -v`
Expected: FAIL — `No module named 'app.pipeline.place'`

- [ ] **Step 3: Create place.py with number_characteristics**

Create `app/pipeline/place.py`:

```python
"""Number detected characteristics in reading order and position their balloons.

Pure functions over Characteristic lists: numbering sorts top-to-bottom in
horizontal bands then left-to-right; placement offsets a balloon marker from the
callout into nearby space (the human fixes overlaps by dragging in review).
"""


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def number_characteristics(chars, band_tol: int = 60):
    """Sort into reading order (banded rows top-to-bottom, left-to-right within a
    band) and assign pos = 1..N. Returns the sorted list (pos set in place)."""
    def key(c):
        cx, cy = _center(c.target_region)
        return (round(cy / band_tol), cx)
    ordered = sorted(chars, key=key)
    for i, c in enumerate(ordered, start=1):
        c.pos = i
    return ordered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_place.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/place.py tests/test_place.py
git commit -m "feat: spatial reading-order numbering of characteristics"
```

---

## Task 9: Balloon placement

**Files:**
- Modify: `app/pipeline/place.py`
- Test: `tests/test_place.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_place.py`:

```python
from app.pipeline.place import place_balloons


def test_place_balloons_offsets_up_and_left():
    c = _char((200, 200, 260, 230))
    place_balloons([c], offset=70)
    bx, by = c.balloon_xy
    assert bx == 130 and by == 130           # box top-left (200,200) minus 70


def test_place_balloons_clamps_to_page_margin():
    c = _char((10, 10, 40, 30))              # near the top-left corner
    place_balloons([c], offset=70, margin=10)
    bx, by = c.balloon_xy
    assert bx == 10 and by == 10             # clamped to the margin, never negative
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_place.py -v`
Expected: FAIL — cannot import `place_balloons`

- [ ] **Step 3: Add place_balloons**

Append to `app/pipeline/place.py`:

```python
def place_balloons(chars, offset: int = 70, margin: int = 10):
    """Set balloon_xy for each characteristic: a marker offset up-and-left from
    the callout's top-left corner, clamped so it stays on the page. The leader
    line to the callout is drawn later from balloon_xy to target_region."""
    for c in chars:
        x0, y0 = c.target_region[0], c.target_region[1]
        bx = max(margin, x0 - offset)
        by = max(margin, y0 - offset)
        c.balloon_xy = (bx, by)
    return chars
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_place.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/place.py tests/test_place.py
git commit -m "feat: offset balloon placement with page-margin clamp"
```

---

## Task 10: Rewrite extract.py + retire the old path

**Files:**
- Rewrite: `app/pipeline/extract.py`
- Delete: `app/pipeline/anchors.py`, `app/pipeline/balloons.py`
- Delete: `tests/test_anchors.py`, `tests/test_balloons.py`
- Rewrite: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Write the failing integration test**

Replace the entire contents of `tests/test_pipeline_integration.py`:

```python
from app.pipeline.detect import Detection
from app.pipeline.extract import extract
from tests.conftest import StubVLMBackend


def test_extract_detects_numbers_places_and_reads(sample_pdf, tmp_path):
    # stub "detects" one dimension callout per tile and reads a fixed value
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1",
    )
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert len(rows) >= 1
    for r in rows:
        assert r.source == "auto"
        assert r.id != ""
        assert r.target_region is not None
        assert r.balloon_xy is not None
        assert r.char_type == "Distance"        # parsed from "1,2 +0,1 -0,1"
        assert r.nominal == "1,2"
    positions = sorted(r.pos for r in rows)
    assert positions == list(range(1, len(rows) + 1))   # sequential 1..N


def test_extract_requires_detection_capable_backend(sample_pdf, tmp_path):
    class ReadOnlyBackend:                     # has read_region but NOT detect_regions
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)

    import pytest
    with pytest.raises(RuntimeError, match="VLM backend"):
        extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=ReadOnlyBackend())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: FAIL — current `extract` still uses anchors/balloons and has no detection-capability check.

- [ ] **Step 3: Rewrite extract.py**

Replace the entire contents of `app/pipeline/extract.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Delete the retired modules and their tests**

```bash
git rm app/pipeline/anchors.py app/pipeline/balloons.py tests/test_anchors.py tests/test_balloons.py
```

- [ ] **Step 6: Verify nothing else imports the retired modules**

Run: `grep -rn "anchors\|balloons" app/ tests/`
Expected: no matches in `app/` or `tests/` (only the design/plan docs may mention them). If any code match remains, remove it.

- [ ] **Step 7: Run the full pipeline + detect + place tests**

Run: `pytest tests/test_pipeline_integration.py tests/test_detect.py tests/test_place.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: rewrite extract as detect->read->number->place; retire balloon-reader path"
```

---

## Task 11: Ballooned-PDF renderer

**Files:**
- Create: `app/pipeline/ballooned_pdf.py`
- Test: `tests/test_ballooned_pdf.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ballooned_pdf.py`:

```python
import fitz
from app.models import Characteristic
from app.pipeline.ballooned_pdf import render_ballooned_pdf


def _row(pos, box, balloon):
    c = Characteristic(pos=pos)
    c.target_region = box
    c.balloon_xy = balloon
    return c


def test_render_ballooned_pdf_adds_drawings_and_leaves_source_untouched(sample_pdf, tmp_path):
    before = fitz.open(sample_pdf)
    n_before = len(before[0].get_drawings())
    src_bytes_before = sample_pdf.read_bytes()
    before.close()

    rows = [_row(1, (300, 300, 380, 330), (200, 200)),
            _row(2, (600, 400, 680, 430), (500, 320))]
    out = tmp_path / "ballooned.pdf"
    render_ballooned_pdf(sample_pdf, rows, dpi=300, out_path=out)

    assert out.exists()
    after = fitz.open(out)
    assert len(after[0].get_drawings()) > n_before     # circles + leader lines added
    text = after[0].get_text()
    assert "1" in text and "2" in text                  # balloon numbers rendered
    after.close()

    # source PDF on disk is unchanged
    assert sample_pdf.read_bytes() == src_bytes_before


def test_render_ballooned_pdf_skips_rows_without_geometry(sample_pdf, tmp_path):
    rows = [Characteristic(pos=1)]                       # no balloon_xy / target_region
    out = tmp_path / "b.pdf"
    render_ballooned_pdf(sample_pdf, rows, dpi=300, out_path=out)
    assert out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ballooned_pdf.py -v`
Expected: FAIL — `No module named 'app.pipeline.ballooned_pdf'`

- [ ] **Step 3: Create ballooned_pdf.py**

Create `app/pipeline/ballooned_pdf.py`:

```python
"""Draw numbered balloons + leader lines onto a copy of the source PDF.

Coordinates on Characteristic rows are image-space pixels at render dpi; convert
back to PDF points by dividing by scale = dpi/72. The source PDF is never
mutated — a new file is written to out_path.
"""
from pathlib import Path
import fitz

_BLUE = (0.0, 0.3, 0.8)
_RADIUS = 9.0      # balloon radius in PDF points


def render_ballooned_pdf(src_pdf, rows, dpi: int = 300, out_path=None, page_index: int = 0):
    out_path = Path(out_path)
    scale = dpi / 72.0
    doc = fitz.open(src_pdf)
    page = doc[page_index]
    rect = page.rect

    def to_pt(x, y):
        px = min(max(x / scale, rect.x0), rect.x1)
        py = min(max(y / scale, rect.y0), rect.y1)
        return fitz.Point(px, py)

    for c in rows:
        if not c.balloon_xy or not c.target_region:
            continue
        bx, by = c.balloon_xy
        tx = (c.target_region[0] + c.target_region[2]) / 2.0
        ty = (c.target_region[1] + c.target_region[3]) / 2.0
        b_pt, t_pt = to_pt(bx, by), to_pt(tx, ty)

        page.draw_line(b_pt, t_pt, color=_BLUE, width=1.0)
        page.draw_circle(b_pt, _RADIUS, color=_BLUE, width=1.5)
        page.insert_text(fitz.Point(b_pt.x - 5, b_pt.y + 4), str(c.pos),
                         fontsize=10, color=_BLUE)

    doc.save(out_path)
    doc.close()
    return out_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ballooned_pdf.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ballooned_pdf.py tests/test_ballooned_pdf.py
git commit -m "feat: render numbered balloons onto a PDF copy"
```

---

## Task 12: API — read_region, export/pdf, upload error handling

**Files:**
- Modify: `app/main.py`
- Rewrite: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.pipeline.detect import Detection
from tests.conftest import StubVLMBackend

client = TestClient(app)


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1",
    )
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


def test_upload_returns_rows_and_image(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert len(data["rows"]) >= 1
    assert data["rows"][0]["source"] == "auto"
    assert data["rows"][0]["id"]
    assert data["image_url"].startswith("/api/image/")


def test_upload_without_detection_backend_returns_400(sample_pdf, monkeypatch):
    class ReadOnly:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)
    monkeypatch.setattr("app.main._BACKEND", ReadOnly())
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 400
    assert "VLM" in r.json()["detail"]


def test_read_region_returns_parsed_characteristic(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload",
                         files={"file": ("sample.pdf", f, "application/pdf")}).json()
    r = client.post("/api/read_region",
                    json={"session_id": up["session_id"], "box": [40, 40, 200, 90]})
    assert r.status_code == 200
    row = r.json()
    assert row["source"] == "manual"
    assert row["nominal"] == "1,2"
    assert row["target_region"] == [40, 40, 200, 90]
    assert row["balloon_xy"] is not None


def test_export_xlsx_roundtrip(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload",
                         files={"file": ("sample.pdf", f, "application/pdf")}).json()
    r = client.post("/api/export",
                    json={"session_id": up["session_id"], "rows": up["rows"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(r.content) > 0


def test_export_pdf_roundtrip(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload",
                         files={"file": ("sample.pdf", f, "application/pdf")}).json()
    r = client.post("/api/export/pdf",
                    json={"session_id": up["session_id"], "rows": up["rows"]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_export_rejects_path_traversal_session_id():
    r = client.post("/api/export", json={
        "session_id": "../../../../tmp/sindri_pwn_test", "rows": [{"pos": 1}]})
    assert r.status_code == 404


def test_image_rejects_bad_session_id():
    assert client.get("/api/image/not-a-valid-uuid").status_code == 404


def test_image_missing_session_returns_404():
    assert client.get("/api/image/" + "0" * 32).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — `/api/read_region` and `/api/export/pdf` don't exist; upload doesn't surface the VLM-required 400.

- [ ] **Step 3: Update main.py**

In `app/main.py`:

Add imports near the top (after the existing `from app.excel import write_workbook`):

```python
from app.models import Characteristic
from app.pipeline.parser import parse_value
from app.pipeline.place import place_balloons
from app.pipeline.ballooned_pdf import render_ballooned_pdf
from PIL import Image
```

Replace the `upload` function body's `try/except` so the VLM-required error is surfaced (replace the existing `try: ... except Exception:` block inside `upload`):

```python
    try:
        rows = extract(pdf_path, work_dir=work, dpi=300, backend=_BACKEND)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="could not read the PDF")
```

Add a request model near `ExportRequest`:

```python
class ReadRegionRequest(BaseModel):
    session_id: str
    box: List[float]        # [x0, y0, x1, y1] image-space pixels
```

Add the two new endpoints (place before the static mount at the bottom):

```python
@app.post("/api/read_region")
def read_region(req: ReadRegionRequest):
    work = _session_dir(req.session_id)
    png = work / "page.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="unknown session")
    if len(req.box) != 4:
        raise HTTPException(status_code=400, detail="box must be [x0,y0,x1,y1]")
    image = Image.open(png).convert("RGB")
    w, h = image.size
    x0, y0, x1, y1 = req.box
    box = (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise HTTPException(status_code=400, detail="degenerate box")
    crop = image.crop(box)
    try:
        ocr = _BACKEND.read_region(crop)
        text, conf = ocr.text, ocr.confidence
    except Exception:
        text, conf = "", 0.0
    c = parse_value(text)
    c.id = uuid.uuid4().hex
    c.source = "manual"
    c.target_region = box
    c.confidence = conf
    place_balloons([c])
    return c.model_dump()


@app.post("/api/export/pdf")
def export_pdf(req: ExportRequest):
    work = _session_dir(req.session_id)
    src = work / "input.pdf"
    if not src.is_file():
        raise HTTPException(status_code=404, detail="unknown session")
    out = work / "ballooned.pdf"
    render_ballooned_pdf(src, req.rows, dpi=300, out_path=out)
    return FileResponse(out, media_type="application/pdf", filename="ballooned.pdf")
```

Note: `uuid` is already imported in `main.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: /api/read_region + /api/export/pdf; surface VLM-required error"
```

---

## Task 13: Full-edit review UI

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`

This task is verified manually in the browser (no automated UI tests in this repo).
Each step shows the complete code to write.

- [ ] **Step 1: Update index.html — styles, buttons, instructions**

Replace the `.marker` style block in `app/static/index.html` and add styles for an "add mode" + delete affordance. Find the existing `.marker { ... }` rule (lines ~11-12) and replace with:

```css
    .marker { position: absolute; width: 18px; height: 18px; margin: -9px 0 0 -9px;
              border: 2px solid #2563eb; border-radius: 50%;
              background: rgba(37,99,235,.2); cursor: move; font-size: 10px;
              color: #1e3a8a; text-align: center; line-height: 16px; user-select: none; }
    .marker .del { position: absolute; top: -10px; right: -10px; width: 14px;
                   height: 14px; border-radius: 50%; background: #dc2626; color: #fff;
                   font-size: 10px; line-height: 14px; cursor: pointer; display: none; }
    .marker:hover .del { display: block; }
    #left.adding { cursor: crosshair; }
    #addBtn.active { background: #2563eb; color: #fff; }
```

Add an "Add balloon" button next to the export button. Find the element with `id="exportBtn"` and add before it (export controls become two buttons + an add toggle):

```html
    <button id="addBtn">+ Add balloon</button>
    <button id="exportBtn" disabled>Download Excel</button>
    <button id="exportPdfBtn" disabled>Download ballooned PDF</button>
```

- [ ] **Step 2: Verify the page still loads**

Run (if not already running): `docker compose up` (or the existing run method), open `http://localhost:8000`.
Expected: page renders with the three buttons; no console errors.

- [ ] **Step 3: Rewrite app.js with full-edit behavior**

Replace the entire contents of `app/static/app.js`:

```javascript
let sessionId = null;
let rows = [];
let imgEl = null;
let addMode = false;

const $ = (s) => document.querySelector(s);
const BAND_TOL = 60;

$("#file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("#status").textContent = "Extracting…";
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "upload failed" }));
    $("#status").textContent = err.detail;
    return;
  }
  const data = await res.json();
  sessionId = data.session_id;
  rows = data.rows;
  renderImage(data.image_url);
  renderGrid();
  $("#exportBtn").disabled = false;
  $("#exportPdfBtn").disabled = false;
  $("#status").textContent = `${rows.length} characteristics`;
});

function renderImage(url) {
  const left = $("#left");
  let img = left.querySelector("img");
  if (!img) { img = document.createElement("img"); left.prepend(img); }
  imgEl = img;
  img.onload = () => placeMarkers();
  img.src = url + "?t=" + Date.now();
}

function scales() {
  return { sx: imgEl.clientWidth / imgEl.naturalWidth,
           sy: imgEl.clientHeight / imgEl.naturalHeight };
}

function renumber() {
  // reading order: banded rows top-to-bottom, left-to-right within a band
  const c = (r) => r.target_region
    ? [(r.target_region[1] + r.target_region[3]) / 2,
       (r.target_region[0] + r.target_region[2]) / 2]
    : [r.balloon_xy[1], r.balloon_xy[0]];
  rows.sort((a, b) => {
    const [ay, ax] = c(a), [by, bx] = c(b);
    const band = Math.round(ay / BAND_TOL) - Math.round(by / BAND_TOL);
    return band !== 0 ? band : ax - bx;
  });
  rows.forEach((r, i) => (r.pos = i + 1));
}

function placeMarkers() {
  const overlay = $("#overlay");
  overlay.innerHTML = "";
  const { sx, sy } = scales();
  rows.forEach((r) => {
    if (!r.balloon_xy) return;
    const m = document.createElement("div");
    m.className = "marker";
    m.style.left = r.balloon_xy[0] * sx + "px";
    m.style.top = r.balloon_xy[1] * sy + "px";
    m.textContent = r.pos;
    m.title = "Pos " + r.pos;
    const del = document.createElement("div");
    del.className = "del";
    del.textContent = "×";
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteRow(r.id); });
    m.appendChild(del);
    makeDraggable(m, r);
    overlay.appendChild(m);
  });
}

function makeDraggable(m, r) {
  let dragging = false;
  m.addEventListener("mousedown", (e) => {
    if (e.target.classList.contains("del")) return;
    dragging = true; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = imgEl.getBoundingClientRect();
    const { sx, sy } = scales();
    const x = (e.clientX - rect.left), y = (e.clientY - rect.top);
    m.style.left = x + "px"; m.style.top = y + "px";
    r.balloon_xy = [x / sx, y / sy];
  });
  window.addEventListener("mouseup", () => { dragging = false; });
}

function deleteRow(id) {
  rows = rows.filter((r) => r.id !== id);
  renumber(); placeMarkers(); renderGrid();
}

$("#addBtn").addEventListener("click", () => {
  addMode = !addMode;
  $("#addBtn").classList.toggle("active", addMode);
  $("#left").classList.toggle("adding", addMode);
  $("#status").textContent = addMode
    ? "Add mode: drag a box around the missed callout"
    : `${rows.length} characteristics`;
});

// drag a box on the image (in add mode) -> /api/read_region
(function enableBoxDraw() {
  const left = $("#left");
  let start = null, rubber = null;
  left.addEventListener("mousedown", (e) => {
    if (!addMode || !imgEl) return;
    const rect = imgEl.getBoundingClientRect();
    start = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    rubber = document.createElement("div");
    rubber.style.cssText =
      "position:absolute;border:1px dashed #dc2626;background:rgba(220,38,38,.1);";
    $("#overlay").appendChild(rubber);
  });
  left.addEventListener("mousemove", (e) => {
    if (!start) return;
    const rect = imgEl.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    rubber.style.left = Math.min(start.x, x) + "px";
    rubber.style.top = Math.min(start.y, y) + "px";
    rubber.style.width = Math.abs(x - start.x) + "px";
    rubber.style.height = Math.abs(y - start.y) + "px";
  });
  left.addEventListener("mouseup", async (e) => {
    if (!start) return;
    const rect = imgEl.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const { sx, sy } = scales();
    const box = [Math.min(start.x, x) / sx, Math.min(start.y, y) / sy,
                 Math.max(start.x, x) / sx, Math.max(start.y, y) / sy];
    rubber.remove(); start = null; rubber = null;
    if (box[2] - box[0] < 4 || box[3] - box[1] < 4) return;   // ignore stray clicks
    const res = await fetch("/api/read_region", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, box }),
    });
    if (!res.ok) return;
    rows.push(await res.json());
    renumber(); placeMarkers(); renderGrid();
  });
})();

function renderGrid() {
  const tb = $("#grid tbody");
  tb.innerHTML = "";
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    if ((r.confidence ?? 0) < 0.6) tr.className = "low";
    tr.innerHTML =
      `<td>${r.pos}</td>` +
      ["char_type", "nominal", "upper_tol", "lower_tol"]
        .map((k) => `<td contenteditable data-i="${i}" data-k="${k}">${r[k] ?? ""}</td>`)
        .join("");
    tb.appendChild(tr);
  });
  tb.querySelectorAll("td[contenteditable]").forEach((td) => {
    td.addEventListener("input", () => {
      rows[+td.dataset.i][td.dataset.k] = td.textContent;
    });
  });
}

async function download(endpoint, filename) {
  const res = await fetch(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, rows }),
  });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

$("#exportBtn").addEventListener("click", () => download("/api/export", "inspection.xlsx"));
$("#exportPdfBtn").addEventListener("click", () => download("/api/export/pdf", "ballooned.pdf"));
```

- [ ] **Step 4: Manual verification in the browser**

With the app running and the VLM backend active (`curl localhost:8000/api/health` shows `"ocr_backend_active":"VLMBackend"`), upload a drawing and verify:
- markers appear with numbers; the grid lists characteristics
- hovering a marker shows a red ×; clicking it deletes the row and renumbers
- dragging a marker moves it
- "+ Add balloon" → drag a box around a callout → a new numbered row appears, prefilled
- "Download Excel" downloads `inspection.xlsx`; "Download ballooned PDF" downloads `ballooned.pdf` with balloons drawn on

Expected: all behaviors work; no console errors.

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: full-edit review UI (add/delete/move + two downloads)"
```

---

## Task 14: Full regression run

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `pytest -q`
Expected: all tests pass. The GPU-gated real-model detection test (Task 15, if added) is skipped without a GPU.

- [ ] **Step 2: Confirm no references to retired modules remain**

Run: `grep -rn "import.*anchors\|import.*balloons\|extract_notes" app/`
Expected: no matches (note: `app/pipeline/notes.py` itself may remain as an unused parser module; that is acceptable and its tests stay green).

- [ ] **Step 3: Commit if anything changed**

```bash
git add -A && git commit -m "test: full regression green for auto-ballooning" || echo "nothing to commit"
```

---

## Task 15: GPU-gated real-model detection check (optional, run on a GPU host)

**Files:**
- Create: `tests/test_detect_gpu.py`

This test is skipped without a GPU/model and is **not** part of CI defaults. It is
the first real-data feedback loop for tuning tile size, overlap, IoU threshold and
the detection prompt once bare drawings are available.

- [ ] **Step 1: Write the GPU-gated test**

Create `tests/test_detect_gpu.py`:

```python
import os
import pytest

gpu_only = pytest.mark.skipif(
    os.getenv("RUN_GPU_TESTS") != "1",
    reason="set RUN_GPU_TESTS=1 on a GPU host with the VLM model available")


@gpu_only
def test_vlm_detects_callouts_on_real_drawing(sample_pdf, tmp_path):
    from PIL import Image
    from app.pipeline.render import render_page
    from app.pipeline.detect import detect_characteristics
    from app.pipeline.ocr.vlm_backend import VLMBackend

    render = render_page(sample_pdf, dpi=300, out_dir=tmp_path)
    image = Image.open(render.png_path).convert("RGB")
    dets = detect_characteristics(image, VLMBackend())
    # sanity: the model finds a non-trivial number of callouts on a real drawing
    assert len(dets) >= 10
```

- [ ] **Step 2: Verify it skips without the flag**

Run: `pytest tests/test_detect_gpu.py -v`
Expected: SKIPPED (1 skipped)

- [ ] **Step 3: Commit**

```bash
git add tests/test_detect_gpu.py
git commit -m "test: GPU-gated real-model detection sanity check"
```

---

## Self-review notes

- **Spec coverage:** detection/tiling/dedupe/merge (Tasks 2–6), VLM detect prompt (Task 7), numbering + placement (Tasks 8–9), extract rewrite + retire old path (Task 10), ballooned PDF (Task 11), `/api/read_region` + `/api/export/pdf` + VLM-required error + zero/failed-read handling (Tasks 10, 12), full-edit UI (Task 13), error handling (clamping in Tasks 10–12; malformed-tile skip in Task 6; zero detections returns empty rows naturally), testing incl. GPU-gated gap (Tasks 14–15).
- **Two endpoints** for the two artifacts (choice B) — Task 12.
- **Flat 1..N numbering** — Task 8 (100-series convention deferred per spec).
- **`notes.py`** is left in place but no longer called by `extract`; `test_notes` stays green. This is a deliberate, documented deviation from the spec line that listed `extract_notes` in the reused tail — full-FAI detection produces note rows directly, so a separate notes-table pass would double-count.
