# Boxed-Callout Detection & Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect GD&T frames, theoretical/basic dims, and boxed note-refs reliably, read them from a frame-stripped crop, and map each to the correct Excel columns — while fixing period-decimal parsing.

**Architecture:** Add a deterministic OpenCV rectangle pass (`boxes.py`) that runs once on the full rendered page; merge its results into the existing VLM tile detections (CV wins on overlap, non-overlapping CV boxes are added); read boxed content from the frame-stripped inner crop; extend the parser with theoretical/reference/GD&T branches and separator-agnostic number parsing.

**Tech Stack:** Python, OpenCV (`opencv-python-headless`, already a dep), Pillow, Pydantic, pytest. Reuses the existing VLM backend and `Detection`/`Characteristic` types.

---

## File Structure

- `app/models.py` — add `subtype` field to `Characteristic` (Task 1).
- `app/pipeline/parser.py` — separator-agnostic numbers + theoretical/reference/GD&T branches (Tasks 2–4).
- `app/pipeline/boxes.py` — **new**: `BoxDetection` + `detect_boxes` CV pass (Task 5).
- `app/pipeline/detect.py` — extend `Detection`, add `merge_boxes`, wire `detect_boxes` into `detect_characteristics` (Task 6).
- `app/pipeline/ocr/vlm_backend.py` — add `read_region_gdt` + GD&T prompt (Task 7).
- `app/pipeline/extract.py` — read inner crop, route GD&T reads, set `subtype` (Task 8).
- `tests/conftest.py` — extend `StubVLMBackend` with `read_region_gdt` (Task 8).
- Tests live beside their modules under `tests/` (every task).

---

## Task 1: Add `subtype` to the Characteristic model

**Files:**
- Modify: `app/models.py:4-16`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_characteristic_has_subtype_default_and_accepts_value():
    assert Characteristic(pos=1).subtype == ""
    c = Characteristic(pos=1, subtype="gdt")
    assert c.subtype == "gdt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::test_characteristic_has_subtype_default_and_accepts_value -v`
Expected: FAIL — `Characteristic` has no field `subtype` (pydantic ValidationError / AttributeError).

- [ ] **Step 3: Add the field**

In `app/models.py`, add after the `kind` line (currently line 13):

```python
    subtype: str = ""            # box sub-type: gdt|theoretical|reference|note_ref
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (all model tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add subtype field to Characteristic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Separator-agnostic number parsing (period → comma)

**Files:**
- Modify: `app/pipeline/parser.py:11-13` (the `_NUM` regex), `:62` (symmetric-tol regex), and add a `_norm` helper
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parser.py`:

```python
def test_period_decimal_diameter_stacked():
    c = parse_value("Ø6.6 +0.2 0")
    assert c.char_type == DIAMETER
    assert c.nominal == "6,6"
    assert c.upper_tol == "0,2"
    assert c.lower_tol == "0"

def test_period_decimal_distance_symmetric_pair():
    c = parse_value("15 +0.05 -0.05")
    assert c.nominal == "15"
    assert c.upper_tol == "0,05"
    assert c.lower_tol == "-0,05"

def test_period_decimal_symmetric_pm():
    c = parse_value("5 ±0.1")
    assert c.nominal == "5"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"
```

Note: `Ø6.6 +0.2 0` exercises the MAX pattern (`U-TOL=0` present, no sign). The unsigned trailing `0` is a lower-tolerance zero; with the current "signed tokens are tolerances" logic it parses as a second nominal, so this test also locks in the fix below.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py::test_period_decimal_diameter_stacked tests/test_parser.py::test_period_decimal_distance_symmetric_pair tests/test_parser.py::test_period_decimal_symmetric_pm -v`
Expected: FAIL — nominal comes back as `"6"` / tolerances empty because `_NUM` only matches comma decimals.

- [ ] **Step 3: Widen the number regex, add normalization, and treat a trailing unsigned `0` as a zero tolerance**

In `app/pipeline/parser.py`, replace the `_NUM` definition (lines 11-13):

```python
# A signed decimal with EITHER separator, e.g. 0,1  -0.05  12  +0,1
_NUM = r"[+\-±]?\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(_NUM)


def _norm(tok: str) -> str:
    """Normalize a captured number to European output: period decimal -> comma."""
    return tok.replace(".", ",")
```

Replace the symmetric-tolerance block (currently lines 62-81) with:

```python
    # --- symmetric tolerance: "5 ±0,1" / "5 ±0.1" ---
    sym = re.search(r"±\s*(\d+(?:[.,]\d+)?)", body)
    if sym:
        nominal_part = body[:sym.start()]
        nums = _NUM_RE.findall(nominal_part)
        c.nominal = _norm(nums[0]) if nums else ""
        c.upper_tol = _norm(sym.group(1))
        c.lower_tol = "-" + _norm(sym.group(1))
    else:
        nums = _NUM_RE.findall(body)
        signed = [n for n in nums if n[0] in "+-"]
        unsigned = [n for n in nums if n[0] not in "+-"]
        if unsigned:
            c.nominal = _norm(unsigned[0])
        elif nums:
            c.nominal = _norm(_strip_sign(nums[0]))
        if len(signed) >= 1:
            c.upper_tol = _norm(_strip_sign(signed[0]))
        if len(signed) >= 2:
            c.lower_tol = _norm(signed[1]) if signed[1][0] == "-" else "-" + _norm(signed[1])
        # a single explicit upper tol followed by an unsigned 0 is a MAX-type
        # zero lower tol (e.g. "Ø6.6 +0.2 0")
        if len(signed) == 1 and len(unsigned) >= 2 and unsigned[1] in ("0", "0,0"):
            c.lower_tol = "0"
```

- [ ] **Step 4: Run the full parser suite to verify it passes**

Run: `pytest tests/test_parser.py -v`
Expected: PASS — the three new tests pass and all pre-existing comma-based tests still pass (widening `[.,]` and `_norm` are no-ops on comma input).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/parser.py tests/test_parser.py
git commit -m "feat: separator-agnostic number parsing (period to comma)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Theoretical and reference (Klammermaß) parsing

**Files:**
- Modify: `app/pipeline/parser.py` (add `THEORETICAL`/`REFERENCE` constants and two early branches in `parse_value`)
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parser.py`:

```python
from app.pipeline.parser import THEORETICAL, REFERENCE

def test_theoretical_boxed_value_nominal_only():
    c = parse_value("20", hint="theoretical")
    assert c.char_type == THEORETICAL
    assert c.nominal == "20"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_theoretical_period_decimal():
    c = parse_value("12.5", hint="theoretical")
    assert c.char_type == THEORETICAL
    assert c.nominal == "12,5"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_reference_parenthesized_nominal_only():
    c = parse_value("(1)")
    assert c.char_type == REFERENCE
    assert c.nominal == "1"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_reference_parenthesized_multi_digit():
    c = parse_value("(20)")
    assert c.char_type == REFERENCE
    assert c.nominal == "20"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -k "theoretical or reference" -v`
Expected: FAIL — `ImportError: cannot import name 'THEORETICAL'` (constants don't exist yet).

- [ ] **Step 3: Add constants and branches**

In `app/pipeline/parser.py`, add to the constants block (after `NOTE = "Note"`, line 9):

```python
THEORETICAL = "Theoretical"
REFERENCE = "Reference"
```

In `parse_value`, immediately after the `_clean` call and `c = Characteristic(...)` (after line 26), add the reference branch before the existing hint checks:

```python
    # --- reference / Klammermaß: a value in parentheses, no tolerance ---
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        nums = _NUM_RE.findall(stripped)
        c.char_type = REFERENCE
        c.nominal = _norm(_strip_sign(nums[0])) if nums else ""
        return c
```

Add the theoretical branch alongside the other hint checks (after the `hint == "note"` block, around line 36):

```python
    if hint == "theoretical":
        nums = _NUM_RE.findall(text)
        c.char_type = THEORETICAL
        c.nominal = _norm(_strip_sign(nums[0])) if nums else ""
        return c
```

- [ ] **Step 4: Run the full parser suite to verify it passes**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (new theoretical/reference tests + all prior tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/parser.py tests/test_parser.py
git commit -m "feat: parse theoretical (boxed) and reference (Klammermass) dims

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: GD&T frame parsing (generalize flatness)

**Files:**
- Modify: `app/pipeline/parser.py` (add a GD&T symbol map and a `hint="gdt"` branch; route `hint="flatness"` through it)
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parser.py`:

```python
def test_gdt_position_frame():
    c = parse_value("⊕ Ø0.1 A", hint="gdt")
    assert c.char_type == "Position"
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "0"

def test_gdt_flatness_value_only_defaults_to_flatness():
    c = parse_value("0.1", hint="gdt")
    assert c.char_type == FLATNESS
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "0"

def test_flatness_hint_still_works_as_gdt_alias():
    c = parse_value("0,1", hint="flatness")
    assert c.char_type == FLATNESS
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -k gdt -v`
Expected: FAIL — `hint="gdt"` is unhandled, so it falls through to DISTANCE parsing (`char_type` wrong, `nominal` non-zero).

- [ ] **Step 3: Add the GD&T symbol map and branch**

In `app/pipeline/parser.py`, add after the constants block:

```python
# GD&T characteristic symbols -> characteristic name. Tolerant of common
# OCR/VLM substitutions for the position symbol.
_GDT_SYMBOLS = {
    "⊕": "Position", "+": "Position",
    "⏥": FLATNESS, "▱": FLATNESS,
    "○": "Circularity", "◯": "Circularity",
    "◎": "Concentricity",
    "⌭": "Cylindricity",
    "∥": "Parallelism", "//": "Parallelism",
    "⊥": "Perpendicularity",
    "∠": "Angularity",
    "⌖": "Position",
}


def _gdt_type(text: str) -> str:
    for sym, name in _GDT_SYMBOLS.items():
        if sym in text:
            return name
    return FLATNESS    # default geometric tolerance when no symbol is recognized
```

In `parse_value`, add the GD&T branch alongside the other hint checks (after the `hint == "theoretical"` block from Task 3):

```python
    if hint in ("gdt", "flatness"):
        # geometric tolerance: nominal is the controlled zero, the value is the
        # tolerance zone (spec example: Flatness -> 0 / 0,1 / 0).
        c.char_type = _gdt_type(text)
        nums = _NUM_RE.findall(text)        # ignores the leading Ø and datum letters
        c.nominal = "0"
        c.upper_tol = _norm(_strip_sign(nums[0])) if nums else ""
        c.lower_tol = "0"
        return c
```

Note: this branch returns early, so the legacy flatness block further down (lines 84-86 in the original) is now dead for `hint="flatness"`. Leave it in place; it is harmless and only reachable if a future caller relies on it.

- [ ] **Step 4: Run the full parser suite to verify it passes**

Run: `pytest tests/test_parser.py -v`
Expected: PASS. The pre-existing `test_flatness_symbol` still passes (it does not assert `lower_tol`, which is now `"0"`).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/parser.py tests/test_parser.py
git commit -m "feat: parse GD&T frames to 0/zone/0 columns

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: CV box detection (`boxes.py`)

**Files:**
- Create: `app/pipeline/boxes.py`
- Test: `tests/test_boxes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_boxes.py`:

```python
from PIL import Image, ImageDraw
from app.pipeline.boxes import BoxDetection, detect_boxes


def _blank(w=400, h=300):
    return Image.new("RGB", (w, h), "white")


def test_box_detection_dataclass_fields():
    b = BoxDetection(outer_box=(0, 0, 10, 10), inner_box=(2, 2, 8, 8),
                     cells=1, subtype="theoretical", conf=0.8)
    assert b.outer_box == (0, 0, 10, 10)
    assert b.inner_box == (2, 2, 8, 8)
    assert b.cells == 1


def test_blank_page_yields_no_boxes():
    assert detect_boxes(_blank()) == []


def test_single_box_classified_theoretical_with_inset_inner():
    img = _blank()
    ImageDraw.Draw(img).rectangle([100, 100, 180, 132], outline="black", width=3)
    boxes = detect_boxes(img)
    assert len(boxes) == 1
    b = boxes[0]
    assert b.subtype == "theoretical"
    assert b.cells == 1
    # inner box is strictly inside the outer box
    assert b.inner_box[0] > b.outer_box[0] and b.inner_box[1] > b.outer_box[1]
    assert b.inner_box[2] < b.outer_box[2] and b.inner_box[3] < b.outer_box[3]


def test_small_box_classified_note_ref():
    img = _blank()
    ImageDraw.Draw(img).rectangle([50, 50, 80, 78], outline="black", width=3)
    boxes = detect_boxes(img)
    assert len(boxes) == 1
    assert boxes[0].subtype == "note_ref"


def test_multi_cell_box_classified_gdt():
    img = _blank()
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 260, 132], outline="black", width=3)
    d.line([153, 100, 153, 132], fill="black", width=3)   # divider 1
    d.line([206, 100, 206, 132], fill="black", width=3)   # divider 2
    boxes = detect_boxes(img)
    assert any(b.subtype == "gdt" and b.cells >= 3 for b in boxes)


def test_full_page_border_is_ignored():
    img = _blank()
    ImageDraw.Draw(img).rectangle([1, 1, 398, 298], outline="black", width=3)
    assert detect_boxes(img) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_boxes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.boxes'`.

- [ ] **Step 3: Implement `boxes.py`**

Create `app/pipeline/boxes.py`:

```python
"""Deterministic CV detection of rectangular callouts (GD&T frames, theoretical
boxed dimensions, boxed note-references) on a rendered drawing page.

Runs once on the full page (frames are page-scale features, not tile-local).
Returns the outer box (for stamping/dedupe), a frame-stripped inner box (for a
clean read), the cell count, and a geometric sub-type. Never raises: any failure
is logged and yields []."""
import sys
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image


@dataclass
class BoxDetection:
    outer_box: Tuple[int, int, int, int]
    inner_box: Tuple[int, int, int, int]
    cells: int
    subtype: str          # gdt|theoretical|note_ref
    conf: float


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def _find_rectangles(gray, min_side, max_area_frac) -> List[tuple]:
    h, w = gray.shape
    _, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binv, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    page_area = float(w * h)
    rects = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, bw, bh = cv2.boundingRect(approx)
        if bw < min_side or bh < min_side:
            continue
        if bw * bh > max_area_frac * page_area:
            continue
        rects.append((x, y, x + bw, y + bh))
    return _dedupe_rects(rects)


def _dedupe_rects(rects, iou_thresh=0.8) -> List[tuple]:
    """A box outline drawn with thickness yields near-identical outer/inner
    contours; collapse them, keeping the larger."""
    kept = []
    for r in sorted(rects, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        if all(_iou(r, k) < iou_thresh for k in kept):
            kept.append(r)
    return kept


def _count_cells(gray, box, inset) -> int:
    x0, y0, x1, y1 = box
    roi = gray[y0 + inset:y1 - inset, x0 + inset:x1 - inset]
    if roi.size == 0:
        return 1
    _, binv = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    rh = roi.shape[0]
    col_ink = binv.sum(axis=0) / 255.0          # ink pixels per column
    is_divider = col_ink > 0.6 * rh             # near-full-height vertical line
    dividers, prev = 0, False
    for d in is_divider:
        if d and not prev:
            dividers += 1
        prev = bool(d)
    return dividers + 1


def _classify(box, cells, note_ref_max_side) -> str:
    bw, bh = box[2] - box[0], box[3] - box[1]
    if cells >= 2:
        return "gdt"
    if max(bw, bh) <= note_ref_max_side:
        return "note_ref"
    return "theoretical"


def detect_boxes(image: Image.Image, min_side: int = 12, max_area_frac: float = 0.05,
                 inset: int = 4, note_ref_max_side: int = 40) -> List[BoxDetection]:
    try:
        gray = np.array(image.convert("L"))
        out = []
        for box in _find_rectangles(gray, min_side, max_area_frac):
            cells = _count_cells(gray, box, inset)
            subtype = _classify(box, cells, note_ref_max_side)
            x0, y0, x1, y1 = box
            inner = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
            if inner[2] <= inner[0] or inner[3] <= inner[1]:
                inner = box
            out.append(BoxDetection(outer_box=box, inner_box=inner,
                                    cells=cells, subtype=subtype, conf=0.8))
        return out
    except Exception as e:                       # never fatal
        print(f"[sindri.boxes] failed: {e!r}", file=sys.stderr, flush=True)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_boxes.py -v`
Expected: PASS. If `test_multi_cell_box_classified_gdt` fails on the divider threshold, lower the `0.6 * rh` factor toward `0.5`; if `test_single_box_classified_theoretical` finds duplicates, the `_dedupe_rects` IoU is too low — these are the only two tuning points.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/boxes.py tests/test_boxes.py
git commit -m "feat: CV detection of boxed callouts (gdt/theoretical/note-ref)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Merge CV boxes into detections (`detect.py`)

**Files:**
- Modify: `app/pipeline/detect.py:11-15` (the `Detection` dataclass), `:103` (`_KINDS`), `:138-156` (`detect_characteristics`); add `merge_boxes`
- Test: `tests/test_detect.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_detect.py`:

```python
from app.pipeline.boxes import BoxDetection
from app.pipeline.detect import merge_boxes


def test_detection_dataclass_has_box_fields_defaulting_none():
    d = Detection(box=(0, 0, 10, 10), kind="dimension", conf=0.9)
    assert d.inner_box is None
    assert d.cells == 1
    assert d.subtype is None


def test_merge_boxes_cv_wins_on_overlap():
    vlm = [Detection(box=(100, 100, 160, 130), kind="dimension", conf=0.9)]
    boxes = [BoxDetection(outer_box=(98, 98, 162, 132), inner_box=(102, 102, 158, 128),
                          cells=1, subtype="theoretical", conf=0.8)]
    merged = merge_boxes(vlm, boxes)
    assert len(merged) == 1
    assert merged[0].subtype == "theoretical"
    assert merged[0].kind == "theoretical"
    assert merged[0].inner_box == (102, 102, 158, 128)


def test_merge_boxes_adds_non_overlapping_cv_box():
    vlm = [Detection(box=(0, 0, 20, 20), kind="dimension", conf=0.9)]
    boxes = [BoxDetection(outer_box=(300, 300, 360, 330), inner_box=(304, 304, 356, 326),
                          cells=3, subtype="gdt", conf=0.8)]
    merged = merge_boxes(vlm, boxes)
    assert len(merged) == 2
    assert any(m.kind == "gdt" and m.subtype == "gdt" for m in merged)


def test_merge_boxes_note_ref_maps_to_note_kind():
    merged = merge_boxes([], [BoxDetection(outer_box=(0, 0, 30, 28),
                          inner_box=(4, 4, 26, 24), cells=1,
                          subtype="note_ref", conf=0.8)])
    assert len(merged) == 1
    assert merged[0].kind == "note"
    assert merged[0].subtype == "note_ref"


def test_detect_characteristics_includes_cv_boxes():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(img).rectangle([100, 100, 180, 132], outline="black", width=3)
    backend = StubVLMBackend(detections=[])          # VLM finds nothing
    dets = detect_characteristics(img, backend)
    assert any(d.subtype == "theoretical" for d in dets)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_detect.py -k "merge_boxes or box_fields or includes_cv" -v`
Expected: FAIL — `Detection` has no `inner_box`/`cells`/`subtype`; `merge_boxes` does not exist.

- [ ] **Step 3: Extend `Detection`, add `merge_boxes`, wire into `detect_characteristics`**

In `app/pipeline/detect.py`, replace the `Detection` dataclass (lines 11-15):

```python
@dataclass
class Detection:
    box: tuple        # (x0, y0, x1, y1) page-space pixels
    kind: str         # dimension|gdt|surface|note|material|theoretical
    conf: float
    inner_box: tuple = None     # frame-stripped read crop (boxed callouts only)
    cells: int = 1              # cell count for multi-cell GD&T frames
    subtype: str = None         # gdt|theoretical|note_ref (boxed callouts only)
```

Add `"theoretical"` to `_KINDS` (line 103):

```python
_KINDS = {"dimension", "gdt", "surface", "note", "material", "theoretical"}
```

Add the box→detection mapping and merge function (place above `detect_characteristics`):

```python
# CV box sub-type -> detector kind. note_ref folds into the existing notes path.
_BOX_KIND = {"gdt": "gdt", "theoretical": "theoretical", "note_ref": "note"}


def _box_to_detection(b):
    return Detection(box=b.outer_box, kind=_BOX_KIND[b.subtype], conf=b.conf,
                     inner_box=b.inner_box, cells=b.cells, subtype=b.subtype)


def merge_boxes(vlm_dets, box_dets, iou_thresh: float = 0.5):
    """Merge deterministic CV boxes into VLM detections. A CV box that overlaps
    a VLM detection wins (it carries the clean inner crop + structure) and
    suppresses the VLM duplicate; a CV box with no overlap is added; VLM
    detections with no CV box are kept unchanged."""
    converted = [_box_to_detection(b) for b in box_dets]
    kept = [v for v in vlm_dets
            if all(_iou(v.box, c.box) <= iou_thresh for c in converted)]
    return kept + converted
```

Replace the return line of `detect_characteristics` (line 156):

```python
    from app.pipeline.boxes import detect_boxes
    vlm = dedupe(merge_adjacent(acc))
    return merge_boxes(vlm, detect_boxes(image))
```

- [ ] **Step 4: Run the detect suite to verify it passes**

Run: `pytest tests/test_detect.py -v`
Expected: PASS — new tests pass; pre-existing tests still pass (white-image tests yield no CV boxes, so `merge_boxes(vlm, [])` returns `vlm` unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/detect.py tests/test_detect.py
git commit -m "feat: merge CV box detections into VLM detections

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: GD&T-aware read prompt (`vlm_backend.py`)

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py` (add `_GDT_PROMPT` and `read_region_gdt`)
- Test: `tests/test_vlm_prompt.py` (new — asserts on the prompt constant, no GPU)

- [ ] **Step 1: Write the failing test**

Create `tests/test_vlm_prompt.py`:

```python
from app.pipeline.ocr import vlm_backend


def test_gdt_prompt_exists_and_is_frame_aware():
    p = vlm_backend._GDT_PROMPT
    assert "feature control frame" in p.lower()
    assert "datum" in p.lower()
    # must not instruct the model to include the surrounding box border
    assert "comma" in p.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_prompt.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_GDT_PROMPT'`.

- [ ] **Step 3: Add the prompt and read method**

In `app/pipeline/ocr/vlm_backend.py`, add after the `_DETECT_PROMPT` block (after line 32):

```python
# GD&T read prompt: the crop is the INNER content of a feature-control frame
# (border already stripped). Transcribe symbol, tolerance value and datum(s)
# on one line, e.g. "⊕ Ø0.1 A". The parser maps this to 0 / zone / 0.
_GDT_PROMPT = (
    "This image is the inner content of a GD&T feature control frame from a "
    "mechanical drawing, with the surrounding box border removed. Transcribe it "
    "on one line as: <symbol> <tolerance value> <datum letters>, e.g. "
    "'⊕ Ø0.1 A' or '⏥ 0,05'. Use a comma as the decimal separator. Preserve the "
    "geometric symbol and any Ø. Output nothing else, no explanation."
)
```

Add the method to `VLMBackend`, after `read_region` (after line 67):

```python
    def read_region_gdt(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _GDT_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.9 if text else 0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vlm_prompt.py -v`
Expected: PASS. (Importing `vlm_backend` does not import torch — torch is imported lazily inside `VLMBackend.__init__`.)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py tests/test_vlm_prompt.py
git commit -m "feat: GD&T-aware read prompt and read_region_gdt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Wire inner-crop reads, GD&T routing, and subtype into `extract`

**Files:**
- Modify: `app/pipeline/extract.py:13` (`_HINTS`), `:16-21` (`_safe_read`), `:29-40` (`_best_read`), `:62-74` (the loop)
- Modify: `tests/conftest.py:38-39` (`StubVLMBackend.read_region`, add `read_region_gdt`)
- Test: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Extend the stub backend, then write the failing test**

In `tests/conftest.py`, replace `StubVLMBackend.read_region` (lines 38-39) with:

```python
    def read_region(self, image, gdt: bool = False):
        return OcrResult(text=self._text, confidence=self._confidence)

    def read_region_gdt(self, image):
        return OcrResult(text=self._gdt_text, confidence=self._confidence)
```

And add `_gdt_text` to `__init__` (extend the signature on line 29):

```python
    def __init__(self, detections=None, text="1,2 +0,1 -0,1", confidence=0.9,
                 gdt_text="⊕ Ø0.1 A"):
        self._detections = detections or []
        self._text = text
        self._confidence = confidence
        self._gdt_text = gdt_text
```

Append to `tests/test_pipeline_integration.py`:

```python
def test_extract_reads_gdt_box_with_gdt_prompt_and_sets_subtype(tmp_path, sample_pdf):
    from PIL import Image, ImageDraw
    import app.pipeline.extract as extract_mod
    from app.pipeline.boxes import BoxDetection
    from tests.conftest import StubVLMBackend

    # Force a single GD&T box, bypassing CV + render variability.
    monkey_box = BoxDetection(outer_box=(50, 50, 210, 82),
                              inner_box=(54, 54, 206, 78),
                              cells=3, subtype="gdt", conf=0.8)

    orig_detect = extract_mod.detect_characteristics
    def fake_detect(image, backend, **kw):
        from app.pipeline.detect import merge_boxes
        return merge_boxes([], [monkey_box])
    extract_mod.detect_characteristics = fake_detect
    try:
        backend = StubVLMBackend(detections=[], gdt_text="⊕ Ø0.1 A")
        rows = extract_mod.extract(sample_pdf, tmp_path, backend=backend)
    finally:
        extract_mod.detect_characteristics = orig_detect

    assert len(rows) == 1
    r = rows[0]
    assert r.subtype == "gdt"
    assert r.char_type == "Position"
    assert r.nominal == "0"
    assert r.upper_tol == "0,1"
    assert r.lower_tol == "0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_integration.py::test_extract_reads_gdt_box_with_gdt_prompt_and_sets_subtype -v`
Expected: FAIL — `extract` does not route GD&T to `read_region_gdt` and does not set `subtype`, so `char_type`/tolerances are wrong.

- [ ] **Step 3: Update `extract.py`**

In `app/pipeline/extract.py`, replace `_HINTS` (line 13):

```python
# detector kind -> parser hint
_HINTS = {"material": "material", "note": "note", "gdt": "gdt",
          "theoretical": "theoretical"}
```

Replace `_safe_read` (lines 16-21) with a version that accepts a reader callable:

```python
def _safe_read(reader, crop) -> Tuple[str, float]:
    try:
        result = reader(crop)
        return result.text, result.confidence
    except Exception:
        return "", 0.0
```

Update `_best_read` (lines 29-40) to call `backend.read_region` through the new helper:

```python
def _best_read(backend, crop: Image.Image, vertical: bool) -> Tuple[str, float]:
    """Read a crop; for vertical callouts try both 90 rotations and keep the best."""
    candidates = [crop]
    if vertical:
        candidates = [crop.rotate(-90, expand=True), crop.rotate(90, expand=True)]
    best_text, best_conf, best_score = "", 0.0, -1.0
    for im in candidates:
        text, conf = _safe_read(backend.read_region, im)
        s = _score(text, conf)
        if s > best_score:
            best_text, best_conf, best_score = text, conf, s
    return best_text, best_conf
```

Replace the detection loop body (lines 64-74) with:

```python
    for d in detections:
        outer = _clamp(d.box, render.width, render.height)
        read_box = _clamp(d.inner_box, render.width, render.height) if d.inner_box else outer
        crop = image.crop(read_box)
        if d.subtype == "gdt" and hasattr(backend, "read_region_gdt"):
            text, confidence = _safe_read(backend.read_region_gdt, crop)
        else:
            text, confidence = _best_read(backend, crop, _is_vertical(read_box))
        c = parse_value(text, hint=_HINTS.get(d.kind, ""))
        c.id = uuid.uuid4().hex
        c.kind = d.kind
        c.subtype = d.subtype or ""
        c.source = "auto"
        c.target_region = outer
        c.confidence = confidence
        results.append(c)
```

- [ ] **Step 4: Run the integration suite to verify it passes**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: PASS — the new test passes; pre-existing integration tests still pass (`_HINTS.get(d.kind)` returns the same hints as before for non-box kinds, and `d.inner_box` is `None` for VLM detections so they read the outer crop exactly as today).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/extract.py tests/conftest.py tests/test_pipeline_integration.py
git commit -m "feat: route boxed reads through inner crop + GD&T prompt, set subtype

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Full-suite regression + GPU-gated real-drawing check

**Files:**
- Modify: `tests/test_detect_gpu.py` (add a GPU-gated assertion that boxed callouts surface on a real drawing)

- [ ] **Step 1: Add the GPU-gated test**

Append to `tests/test_detect_gpu.py`:

```python
@gpu_only
def test_detect_characteristics_surfaces_boxed_callouts(sample_pdf, tmp_path):
    from PIL import Image
    from app.pipeline.render import render_page
    from app.pipeline.detect import detect_characteristics
    from app.pipeline.ocr.vlm_backend import VLMBackend

    render = render_page(sample_pdf, dpi=300, out_dir=tmp_path)
    image = Image.open(render.png_path).convert("RGB")
    dets = detect_characteristics(image, VLMBackend())
    # the CV pass must contribute at least one structured box on a real drawing
    assert any(d.subtype in ("gdt", "theoretical", "note_ref") for d in dets)
```

- [ ] **Step 2: Run the CPU portion to confirm it is correctly skipped**

Run: `pytest tests/test_detect_gpu.py -v`
Expected: the new test reports SKIPPED (RUN_GPU_TESTS unset) — no error.

- [ ] **Step 3: Run the entire non-GPU suite**

Run: `pytest -q`
Expected: PASS — all tests green, GPU tests skipped. If any pre-existing test fails, fix the regression before proceeding.

- [ ] **Step 4: Manual confidence check on a real drawing (CPU, no model)**

Run:

```bash
.venv/bin/python -c "
import fitz; from PIL import Image; from app.pipeline.boxes import detect_boxes
d = fitz.open('test_docs/T1026449_C.pdf'); p = d[0]
pix = p.get_pixmap(matrix=fitz.Matrix(300/72, 300/72)); pix.save('/tmp/page.png')
boxes = detect_boxes(Image.open('/tmp/page.png'))
from collections import Counter
print('boxes:', len(boxes), Counter(b.subtype for b in boxes))
"
```

Expected: a non-zero count with at least some `gdt` and/or `note_ref` boxes (this drawing has multiple `⊕ Ø0.1 A` frames and boxed `101`–`106` note-refs). This is a sanity check, not an assertion — tune `min_side`/`note_ref_max_side`/`max_area_frac` in `boxes.py` if the counts look obviously wrong, then re-run `pytest tests/test_boxes.py`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_detect_gpu.py
git commit -m "test: GPU-gated check that boxed callouts surface on real drawings

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review Notes (verified against the spec)

- **GD&T frames → 0/zone/0** — Tasks 4 (parser), 7 (prompt), 8 (routing). ✓
- **Theoretical → nominal as-is, tols empty** — Task 3. ✓
- **Reference / Klammermaß `(…)`** — Task 3. ✓
- **Note-refs routed to notes path** — Task 6 (`_BOX_KIND` maps `note_ref`→`note`); kept minimal, no new numbering (matches spec non-goal). ✓
- **CV detection of all three box types** — Task 5. ✓
- **CV-wins-on-overlap merge** — Task 6. ✓
- **Frame-stripped inner crop read** — Task 5 (`inner_box`) + Task 8 (read `inner_box`). ✓
- **Period→comma decimal fix** — Task 2. ✓
- **`subtype` on the model** — Task 1, set in Task 8. ✓
- **Error handling never fatal** — Task 5 (`detect_boxes` try/except → `[]`). ✓
- **Out-of-scope items untouched** — no sub-numbering, color, traversal, or template work appears in any task. ✓
- **Type consistency** — `BoxDetection(outer_box/inner_box/cells/subtype/conf)`, `Detection(... inner_box/cells/subtype)`, `read_region_gdt`, and `_HINTS` keys (`gdt`/`theoretical`) are used identically across Tasks 5–8. ✓
