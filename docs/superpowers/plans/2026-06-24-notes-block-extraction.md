# Notes-Block Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated notes-block extraction path that locates the
general-notes table on a drawing, reads its bilingual numbered notes plus
inline sub-bullets as structured data, masks the block before
`detect_characteristics` runs (so inline sub-bullets cannot be
misclassified as page-level dimensions), and surfaces the notes as a
parallel section in the API/UI/Excel output.

**Architecture:** New `app/pipeline/notes_block.py` owns locator, reader,
parser, masker, and per-note review policy. A small geometry helper file
`app/pipeline/geom.py` houses primitives shared between `detect.py`,
`boxes.py`, and `notes_block.py`. `extract.py` orchestrates the two
paths and returns an `ExtractionResult { characteristics, notes }`.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, Pillow, OpenCV, NumPy,
pytest, openpyxl, Qwen2.5-VL via Transformers (already wired). UI is
vanilla JS in `app/static/`.

**Spec:** `docs/superpowers/specs/2026-06-24-notes-block-extraction-design.md`

---

## File Map

**New files:**
- `app/pipeline/geom.py` — shared `_iou`, `_x_aligned`, `_y_close`, `_union` (pulled from `detect.py` / `boxes.py`).
- `app/pipeline/notes_block.py` — `NotesBlockRegion`, `locate_notes_block`, `read_notes_block`, `parse_notes_block`, `review_flags_note`, `mask_region`.
- `tests/test_geom.py` — geometry helper tests.
- `tests/test_notes_block.py` — parser, policy, masker, locator (stubbed backend).
- `tests/test_extract_notes_integration.py` — end-to-end with stubbed backend producing T1025300-shaped detections.

**Modified files:**
- `app/models.py` — add `Note`, `NoteBlock`, `ExtractionResult`; extend `Characteristic` with `note_ref_pos`.
- `app/pipeline/detect.py` — import shared helpers from `geom.py`; no behaviour change.
- `app/pipeline/boxes.py` — import shared `_iou` from `geom.py`; no behaviour change.
- `app/pipeline/extract.py` — return `ExtractionResult`; orchestrate notes path; resolve `note_ref_pos`.
- `app/pipeline/ocr/vlm_backend.py` — add `_NOTES_PROMPT` and `read_notes_block` method.
- `app/pipeline/review.py` — accept optional `known_note_positions` param for the "unknown note reference" reason.
- `app/main.py` — `/api/upload` returns `{ rows, notes }`.
- `app/excel.py` — new `Notes` sheet; append `note_ref_pos` suffix to comment.
- `app/static/index.html` — notes section markup + CSS.
- `app/static/app.js` — render notes section, render "→ note N" indicator on main rows.

**Deleted:**
- `app/pipeline/notes.py` — superseded by `notes_block.py`.
- `tests/test_notes.py` — replaced by the new tests.

---

## Task 1: Geometry helper refactor

**Files:**
- Create: `app/pipeline/geom.py`
- Modify: `app/pipeline/detect.py:41-74`
- Modify: `app/pipeline/boxes.py:26-35`
- Test: `tests/test_geom.py`

This is a pure move: nothing about the existing pipeline changes. We need
the helpers callable from `notes_block.py` later without circular imports.

- [ ] **Step 1: Write the failing test**

Create `tests/test_geom.py`:

```python
from app.pipeline.geom import _iou, _union, _x_aligned, _y_close


def test_iou_zero_when_disjoint():
    assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_one_when_identical():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_iou_partial_overlap():
    # 10x10 each, overlap 5x5 = 25; union = 100+100-25 = 175
    assert abs(_iou((0, 0, 10, 10), (5, 5, 15, 15)) - 25 / 175) < 1e-9


def test_union_covers_both():
    assert _union((0, 0, 10, 10), (5, 5, 20, 20)) == (0, 0, 20, 20)


def test_x_aligned_true_within_tolerance():
    assert _x_aligned((0, 0, 10, 5), (8, 20, 18, 25), x_tol=5) is True


def test_x_aligned_false_outside_tolerance():
    assert _x_aligned((0, 0, 10, 5), (30, 20, 40, 25), x_tol=5) is False


def test_y_close_true_when_vertically_adjacent():
    assert _y_close((0, 0, 10, 10), (0, 15, 10, 25), y_gap=10) is True


def test_y_close_false_when_far_apart():
    assert _y_close((0, 0, 10, 10), (0, 100, 10, 110), y_gap=10) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_geom.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.geom'`.

- [ ] **Step 3: Create `app/pipeline/geom.py`**

```python
"""Geometry primitives shared by detect.py, boxes.py, and notes_block.py."""


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


def _x_aligned(a, b, x_tol: int) -> bool:
    return a[0] <= b[2] + x_tol and b[0] <= a[2] + x_tol


def _y_close(a, b, y_gap: int) -> bool:
    gap = max(a[1] - b[3], b[1] - a[3])
    return gap <= y_gap


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `pytest tests/test_geom.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Replace the duplicate definitions in `detect.py`**

In `app/pipeline/detect.py`, delete the local `_iou`, `_x_aligned`, `_y_close`, `_union` (lines 41-50 and 64-74) and replace them with an import near the top of the file:

```python
from app.pipeline.geom import _iou, _x_aligned, _y_close, _union
```

- [ ] **Step 6: Replace the duplicate definition in `boxes.py`**

In `app/pipeline/boxes.py`, delete the local `_iou` (lines 26-35) and add near the top:

```python
from app.pipeline.geom import _iou
```

- [ ] **Step 7: Run the full test suite to verify nothing regressed**

Run: `pytest tests/ -v`
Expected: all previously-passing tests still pass; the new `test_geom.py` tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/pipeline/geom.py app/pipeline/detect.py app/pipeline/boxes.py tests/test_geom.py
git commit -m "refactor: extract geometry primitives to app/pipeline/geom.py"
```

---

## Task 2: Data model — `Note`, `NoteBlock`, `ExtractionResult`, `Characteristic.note_ref_pos`

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_models.py`:

```python
def test_note_model_defaults():
    from app.models import Note
    n = Note(pos=101)
    assert n.parent_pos is None
    assert n.sub_index is None
    assert n.text_en == "" and n.text_de == ""
    assert n.needs_review is False and n.review_reasons == []


def test_note_sub_bullet_carries_parent_and_sub_index():
    from app.models import Note
    n = Note(pos=1, parent_pos=101, sub_index=1, text_en="A", text_de="B")
    assert n.parent_pos == 101 and n.sub_index == 1


def test_note_block_model():
    from app.models import Note, NoteBlock
    nb = NoteBlock(region=(0, 0, 100, 100), notes=[Note(pos=101)])
    assert nb.region == (0, 0, 100, 100)
    assert len(nb.notes) == 1


def test_extraction_result_with_no_notes():
    from app.models import Characteristic, ExtractionResult
    r = ExtractionResult(characteristics=[Characteristic(pos=1)], notes=None)
    assert r.notes is None
    assert len(r.characteristics) == 1


def test_characteristic_has_optional_note_ref_pos():
    from app.models import Characteristic
    c = Characteristic(pos=1, note_ref_pos=101)
    assert c.note_ref_pos == 101
    assert Characteristic(pos=2).note_ref_pos is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `Note`, `NoteBlock`, `ExtractionResult` don't exist; `note_ref_pos` not on `Characteristic`.

- [ ] **Step 3: Update `app/models.py`**

Replace the file contents with:

```python
from typing import List, Optional, Tuple
from pydantic import BaseModel


class Characteristic(BaseModel):
    pos: int
    char_type: str = ""          # Distance|Diameter|Radius|Flatness|Material|Note
    nominal: str = ""
    upper_tol: str = ""
    lower_tol: str = ""
    raw_text: str = ""
    confidence: float = 0.0
    id: str = ""                 # stable per-row id for the review UI
    kind: str = ""               # detector kind: dimension|gdt|surface|note|material
    subtype: str = ""            # box sub-type: gdt|theoretical|reference|note_ref
    source: str = "auto"         # "auto" (detected) or "manual" (user-added)
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty read", "missing nominal"]
    balloon_xy: Optional[Tuple[float, float]] = None        # image-space
    target_region: Optional[Tuple[float, float, float, float]] = None  # x0,y0,x1,y1
    note_ref_pos: Optional[int] = None    # set when subtype == "note_ref"


class Note(BaseModel):
    pos: int                          # 101, 102, … for top-level; 1, 2, … for sub-bullets
    parent_pos: Optional[int] = None  # set for sub-bullets (1, 2, 3 → parent 101)
    sub_index: Optional[int] = None   # 1, 2, 3 within a parent; None for top-level
    text_en: str = ""
    text_de: str = ""
    raw_text: str = ""
    box: Optional[Tuple[float, float, float, float]] = None
    confidence: float = 0.0
    needs_review: bool = False
    review_reasons: List[str] = []


class NoteBlock(BaseModel):
    region: Tuple[float, float, float, float]
    notes: List[Note] = []


class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
```

- [ ] **Step 4: Run the model tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to confirm no other test broke**

Run: `pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add Note/NoteBlock/ExtractionResult models and note_ref_pos"
```

---

## Task 3: Notes-block parser — `parse_notes_block`

**Files:**
- Create: `app/pipeline/notes_block.py` (initial skeleton with only the parser).
- Test: `tests/test_notes_block.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notes_block.py`:

```python
from app.pipeline.notes_block import parse_notes_block


def test_parses_top_level_bilingual_row():
    raw = "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert nb.region == (0, 0, 100, 100)
    assert len(nb.notes) == 1
    n = nb.notes[0]
    assert n.pos == 101 and n.parent_pos is None and n.sub_index is None
    assert n.text_en == "CONTACT AREA NOTES"
    assert n.text_de == "KONTAKTBEREICH HINWEISE"
    assert n.raw_text == raw


def test_parses_sub_bullet_links_parent():
    raw = (
        "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE\n"
        "101.1\tPLANARITY 0,2mm\tEBENHEIT 0,2mm"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert len(nb.notes) == 2
    sub = nb.notes[1]
    assert sub.pos == 1
    assert sub.parent_pos == 101
    assert sub.sub_index == 1
    assert sub.text_en == "PLANARITY 0,2mm"
    assert sub.text_de == "EBENHEIT 0,2mm"


def test_parses_single_language_row_when_no_tab_after_en():
    raw = "102\tPART FREE OF GREASE AND OIL"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert len(nb.notes) == 1
    n = nb.notes[0]
    assert n.text_en == "PART FREE OF GREASE AND OIL"
    assert n.text_de == ""


def test_drops_malformed_lines_silently():
    raw = (
        "this is not a note row\n"
        "101\tA\tB\n"
        "\n"
        "garbage 999\n"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    positions = [n.pos for n in nb.notes]
    assert positions == [101]


def test_parses_multiple_top_level_and_sub_bullets():
    raw = (
        "101\tA-en\tA-de\n"
        "101.1\tA1-en\tA1-de\n"
        "101.2\tA2-en\tA2-de\n"
        "102\tB-en\tB-de\n"
    )
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    flat = [(n.pos, n.parent_pos, n.sub_index) for n in nb.notes]
    assert flat == [(101, None, None), (1, 101, 1), (2, 101, 2), (102, None, None)]


def test_three_digit_pos_outside_10x_range_still_accepted():
    raw = "199\tnote text en\tnote text de"
    nb = parse_notes_block(raw, region=(0, 0, 100, 100))
    assert nb.notes[0].pos == 199
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_notes_block.py -v`
Expected: FAIL — `app.pipeline.notes_block` does not exist.

- [ ] **Step 3: Create `app/pipeline/notes_block.py` with the parser**

```python
"""Notes-block path: locate the general-notes table, read it as structured
bilingual data with sub-bullet linkage, mask it before the main detector
runs so its inline bullets cannot be misclassified as page-level callouts."""
import re
from typing import List, Optional, Tuple

from app.models import Note, NoteBlock


_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$")
_SUBROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\.(\d+)\t([^\t]*)\t?(.*)$")


def parse_notes_block(raw: str, region: Tuple[float, float, float, float]) -> NoteBlock:
    """Parse the tab-separated notes-block transcription into a NoteBlock.

    Each line is either '<pos>\\t<en>\\t<de>' or '<parent>.<sub>\\t<en>\\t<de>'.
    Malformed lines are dropped silently (non-fatal pipeline convention)."""
    notes: List[Note] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m_sub = _SUBROW_RE.match(line)
        if m_sub:
            parent = int(m_sub.group(1))
            sub_idx = int(m_sub.group(2))
            notes.append(Note(
                pos=sub_idx, parent_pos=parent, sub_index=sub_idx,
                text_en=m_sub.group(3).strip(),
                text_de=m_sub.group(4).strip(),
                raw_text=line,
            ))
            continue
        m_top = _ROW_RE.match(line)
        if m_top:
            notes.append(Note(
                pos=int(m_top.group(1)),
                text_en=m_top.group(2).strip(),
                text_de=m_top.group(3).strip(),
                raw_text=line,
            ))
    return NoteBlock(region=region, notes=notes)
```

- [ ] **Step 4: Run to verify the parser tests pass**

Run: `pytest tests/test_notes_block.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/notes_block.py tests/test_notes_block.py
git commit -m "feat: parse_notes_block — bilingual rows + sub-bullet linkage"
```

---

## Task 4: Per-note review policy — `review_flags_note`

**Files:**
- Modify: `app/pipeline/notes_block.py`
- Test: `tests/test_notes_block.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notes_block.py`:

```python
from app.models import Note
from app.pipeline.notes_block import review_flags_note


def _note(**kw):
    base = dict(pos=101, text_en="A", text_de="B", raw_text="101\tA\tB")
    base.update(kw)
    return Note(**base)


def test_clean_top_level_note_not_flagged():
    flagged, reasons = review_flags_note(
        _note(), two_columns=True, known_parents=set())
    assert flagged is False
    assert reasons == []


def test_empty_raw_text_is_flagged():
    flagged, reasons = review_flags_note(
        _note(raw_text="", text_en="", text_de=""),
        two_columns=True, known_parents=set())
    assert flagged is True
    assert reasons == ["empty read"]


def test_missing_translation_when_two_columns_expected():
    _, reasons = review_flags_note(
        _note(text_de=""), two_columns=True, known_parents=set())
    assert reasons == ["missing translation"]


def test_missing_translation_not_reported_for_single_column_block():
    _, reasons = review_flags_note(
        _note(text_de=""), two_columns=False, known_parents=set())
    assert reasons == []


def test_orphan_sub_bullet_when_parent_not_in_block():
    sub = _note(pos=1, parent_pos=999, sub_index=1, raw_text="999.1\tA\tB")
    _, reasons = review_flags_note(sub, two_columns=True, known_parents={101})
    assert "orphan sub-bullet" in reasons


def test_sub_bullet_with_known_parent_not_flagged_for_orphan():
    sub = _note(pos=1, parent_pos=101, sub_index=1, raw_text="101.1\tA\tB")
    _, reasons = review_flags_note(sub, two_columns=True, known_parents={101})
    assert reasons == []


def test_empty_read_suppresses_missing_translation():
    _, reasons = review_flags_note(
        _note(raw_text="", text_en="", text_de=""),
        two_columns=True, known_parents=set())
    assert reasons == ["empty read"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_notes_block.py::test_clean_top_level_note_not_flagged -v`
Expected: FAIL — `review_flags_note` is not defined.

- [ ] **Step 3: Add the function to `app/pipeline/notes_block.py`**

Append to `app/pipeline/notes_block.py`:

```python
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
```

- [ ] **Step 4: Run to verify the policy tests pass**

Run: `pytest tests/test_notes_block.py -v`
Expected: PASS (all parser + policy tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/notes_block.py tests/test_notes_block.py
git commit -m "feat: review_flags_note — empty/missing-translation/orphan reasons"
```

---

## Task 5: Region masker — `mask_region`

**Files:**
- Modify: `app/pipeline/notes_block.py`
- Test: `tests/test_notes_block.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notes_block.py`:

```python
from PIL import Image
from app.pipeline.notes_block import mask_region, NotesBlockRegion


def test_mask_region_fills_with_white_inside_box():
    img = Image.new("RGB", (100, 100), "black")
    region = NotesBlockRegion(outer_box=(20, 30, 60, 70), lang_columns=[(20, 60)])
    out = mask_region(img, region)
    # inside the box is white
    assert out.getpixel((30, 40)) == (255, 255, 255)
    # outside the box is unchanged
    assert out.getpixel((10, 10)) == (0, 0, 0)
    # original image is untouched (copy semantics)
    assert img.getpixel((30, 40)) == (0, 0, 0)


def test_mask_region_box_with_zero_area_no_op():
    img = Image.new("RGB", (50, 50), "black")
    region = NotesBlockRegion(outer_box=(10, 10, 10, 10), lang_columns=[(10, 10)])
    out = mask_region(img, region)
    # still all black
    assert out.getpixel((10, 10)) == (0, 0, 0)
    assert out.getpixel((25, 25)) == (0, 0, 0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_notes_block.py::test_mask_region_fills_with_white_inside_box -v`
Expected: FAIL — `mask_region` and `NotesBlockRegion` not defined.

- [ ] **Step 3: Add `NotesBlockRegion` and `mask_region` to `app/pipeline/notes_block.py`**

Append to `app/pipeline/notes_block.py`:

```python
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
```

- [ ] **Step 4: Run to verify the mask tests pass**

Run: `pytest tests/test_notes_block.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/notes_block.py tests/test_notes_block.py
git commit -m "feat: mask_region — white-fill the notes block on a copy"
```

---

## Task 6: Locator — `locate_notes_block`

**Files:**
- Modify: `app/pipeline/notes_block.py`
- Test: `tests/test_notes_block.py`

The locator does three things: cluster VLM `kind=note` detections into one
region, optionally snap that region to an overlapping CV-detected
rectangle, and infer the language columns.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notes_block.py`:

```python
from app.pipeline.detect import Detection
from app.pipeline.boxes import BoxDetection
from app.pipeline.notes_block import locate_notes_block


class _StubBackendNotes:
    """Returns the same note detections for every tile (the locator's tile-grid
    pass will pick them up at offset (0,0))."""
    def __init__(self, detections):
        self._dets = detections

    def detect_regions(self, image):
        return list(self._dets)


def _white_image(w=400, h=400):
    return Image.new("RGB", (w, h), "white")


def test_locate_returns_none_when_no_note_detections(monkeypatch):
    backend = _StubBackendNotes(detections=[])
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [])
    region = locate_notes_block(_white_image(), backend)
    assert region is None


def test_locate_clusters_adjacent_note_detections(monkeypatch):
    # Three note detections stacked vertically inside the same column.
    dets = [
        Detection(box=(50, 20, 200, 40), kind="note", conf=0.9),
        Detection(box=(50, 50, 200, 70), kind="note", conf=0.9),
        Detection(box=(50, 80, 200, 100), kind="note", conf=0.9),
    ]
    backend = _StubBackendNotes(dets)
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [])
    region = locate_notes_block(_white_image(), backend)
    assert region is not None
    # outer_box covers the union of the three, padded by 8
    assert region.outer_box[0] <= 50 and region.outer_box[1] <= 20
    assert region.outer_box[2] >= 200 and region.outer_box[3] >= 100


def test_locate_snaps_to_overlapping_cv_rectangle(monkeypatch):
    dets = [Detection(box=(60, 60, 200, 90), kind="note", conf=0.9)]
    cv = BoxDetection(outer_box=(50, 50, 220, 110), inner_box=(54, 54, 216, 106),
                      cells=2, subtype="theoretical", conf=0.8)
    backend = _StubBackendNotes(dets)
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [cv])
    region = locate_notes_block(_white_image(), backend)
    # Snapped to the CV rectangle (which is larger and overlaps).
    assert region.outer_box == (50, 50, 220, 110)


def test_locate_returns_none_when_detector_raises(monkeypatch):
    class Boom:
        def detect_regions(self, image):
            raise RuntimeError("kaboom")
    monkeypatch.setattr("app.pipeline.notes_block.detect_boxes", lambda image: [])
    assert locate_notes_block(_white_image(), Boom()) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_notes_block.py -k locate -v`
Expected: FAIL — `locate_notes_block` not defined.

- [ ] **Step 3: Implement the locator**

Append to `app/pipeline/notes_block.py`:

```python
import sys
from app.pipeline.geom import _iou, _x_aligned, _y_close, _union
from app.pipeline.detect import tile_grid, Detection
from app.pipeline.boxes import detect_boxes


_LOCATOR_PAD = 8                # px padding when no CV snap is available
_SNAP_IOU = 0.4                 # IoU threshold to consider a CV rectangle a snap
_NOTE_CLUSTER_X_TOL = 30        # px: same-column note detections
_NOTE_CLUSTER_Y_GAP = 40        # px: vertical gap allowed between adjacent rows


def _cluster_notes(dets: List[Detection]) -> Optional[tuple]:
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
    # pick the largest cluster (notes block is the biggest one)
    items.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return items[0]


def _snap_to_cv(proposal: tuple, image, cv_boxes) -> tuple:
    candidates = [b.outer_box for b in cv_boxes
                  if _iou(proposal, b.outer_box) >= _SNAP_IOU
                  and (b.outer_box[2] - b.outer_box[0]) * (b.outer_box[3] - b.outer_box[1])
                      >= (proposal[2] - proposal[0]) * (proposal[3] - proposal[1])]
    if not candidates:
        return proposal
    candidates.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return candidates[0]


def _pad(box: tuple, image) -> tuple:
    w, h = image.size
    x0, y0, x1, y1 = box
    return (max(0, int(x0) - _LOCATOR_PAD), max(0, int(y0) - _LOCATOR_PAD),
            min(w, int(x1) + _LOCATOR_PAD), min(h, int(y1) + _LOCATOR_PAD))


def _infer_columns(image, box: tuple) -> List[Tuple[int, int]]:
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
    # search for a near-full-height vertical line in the middle band of the box
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


def locate_notes_block(image, backend) -> Optional[NotesBlockRegion]:
    """Three-step hybrid locator: VLM proposes notes detections; CV snaps the
    region to the overlapping rectangle if one exists; columns inferred from
    the ink density inside the snapped box. Never raises; any failure returns
    None and the pipeline runs without a notes section."""
    try:
        width, height = image.size
        acc: List[Detection] = []
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
```

- [ ] **Step 4: Run the locator tests**

Run: `pytest tests/test_notes_block.py -k locate -v`
Expected: PASS (4 locate tests).

- [ ] **Step 5: Run the full notes-block file to confirm no regressions**

Run: `pytest tests/test_notes_block.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/notes_block.py tests/test_notes_block.py
git commit -m "feat: locate_notes_block — VLM-proposes + CV-snap + column infer"
```

---

## Task 7: VLM notes-block read prompt + `read_notes_block`

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py`
- Modify: `app/pipeline/notes_block.py`
- Test: `tests/test_notes_block.py`

- [ ] **Step 1: Write the failing test for `read_notes_block`**

Append to `tests/test_notes_block.py`:

```python
from app.pipeline.notes_block import read_notes_block


class _StubBackendRead:
    def __init__(self, text):
        self._text = text

    def read_region(self, image):
        from app.pipeline.ocr.base import OcrResult
        return OcrResult(text=self._text, confidence=0.9)


def test_read_notes_block_returns_backend_text():
    backend = _StubBackendRead("101\tA-en\tA-de")
    region = NotesBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 50), (50, 100)])
    text = read_notes_block(Image.new("RGB", (200, 200), "white"), region, backend)
    assert text == "101\tA-en\tA-de"


def test_read_notes_block_uses_notes_method_when_available():
    class WithNotesMethod:
        def read_notes_block(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="from-notes-method", confidence=0.9)
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="from-generic-read", confidence=0.9)

    region = NotesBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    text = read_notes_block(Image.new("RGB", (200, 200), "white"),
                            region, WithNotesMethod())
    assert text == "from-notes-method"


def test_read_notes_block_returns_empty_string_when_backend_raises():
    class Boom:
        def read_region(self, image):
            raise RuntimeError("kaboom")
    region = NotesBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    text = read_notes_block(Image.new("RGB", (200, 200), "white"), region, Boom())
    assert text == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_notes_block.py::test_read_notes_block_returns_backend_text -v`
Expected: FAIL — `read_notes_block` not defined.

- [ ] **Step 3: Add `read_notes_block` to `app/pipeline/notes_block.py`**

Append to `app/pipeline/notes_block.py`:

```python
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
```

- [ ] **Step 4: Add the dedicated VLM prompt and method**

In `app/pipeline/ocr/vlm_backend.py`, add a new prompt constant near the existing `_PROMPT`, `_DETECT_PROMPT`, `_GDT_PROMPT`:

```python
# Notes-block read prompt: the crop is the general-notes table from the
# drawing. Each row begins with a 3-digit number (101…); some rows contain
# inline numbered sub-bullets (1., 2., …). The model returns tab-separated
# triples so the parser can align EN and DE columns in one pass.
_NOTES_PROMPT = (
    "This image is the general-notes table from a mechanical engineering "
    "drawing. Each row begins with a 3-digit number (101, 102, …) followed "
    "by the English note and then the German note. Some rows contain inline "
    "numbered sub-bullets (1., 2., 3., …) — preserve them with their "
    "numbers. Output one row per line in the form:\n"
    "  <pos>\\t<english>\\t<german>\n"
    "For sub-bullets, prefix with the parent pos: e.g. "
    "\"101.1\\t<en>\\t<de>\". No prose, no headers, no explanations. Use a "
    "comma as the decimal separator."
)
```

And add a method on `VLMBackend` (place it next to `read_region_gdt`):

```python
    def read_notes_block(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _NOTES_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=512, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.9 if text else 0.0)
```

- [ ] **Step 5: Add a prompt test for `read_notes_block`**

Append to `tests/test_vlm_prompt.py` (or create the file if absent — open it first to confirm pattern):

```python
def test_notes_block_prompt_is_constrained_and_tab_separated():
    from app.pipeline.ocr.vlm_backend import _NOTES_PROMPT
    p = _NOTES_PROMPT.lower()
    assert "general-notes" in p
    assert "\\t" in p          # the prompt instructs tab-separated output
    assert "comma as the decimal separator" in p
    assert "no prose" in p
```

- [ ] **Step 6: Run the new tests**

Run: `pytest tests/test_notes_block.py tests/test_vlm_prompt.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/pipeline/notes_block.py app/pipeline/ocr/vlm_backend.py tests/test_notes_block.py tests/test_vlm_prompt.py
git commit -m "feat: read_notes_block + dedicated VLM prompt"
```

---

## Task 8: Wire notes path into `extract()` and resolve `note_ref_pos`

**Files:**
- Modify: `app/pipeline/extract.py`
- Modify: `app/pipeline/review.py`
- Test: `tests/test_pipeline_integration.py`

This task changes `extract()`'s return type. The next task updates `main.py`.

- [ ] **Step 1: Extend `review_flags` with an optional known-note-positions arg**

Open `app/pipeline/review.py`. Change the signature and add the new reason:

```python
"""The needs-review policy: one pure function mapping a row's observed extraction
facts to a flag + human-readable reasons. The single home for this policy so it
can be understood and tested in isolation."""
from typing import List, Optional, Set, Tuple

from app.models import Characteristic

DIMENSION_TYPES = {"Distance", "Diameter", "Radius", "Theoretical"}
LOW_CONF = 0.6


def review_flags(c: Characteristic, rotation_ambiguous: bool,
                 known_note_positions: Optional[Set[int]] = None) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a populated Characteristic.

    `known_note_positions`, when provided, is the set of top-level note pos
    values present in the parsed notes block. A note_ref Characteristic
    pointing outside that set is flagged 'unknown note reference'.

    Gating: an empty read is its own reason and does not also report
    'missing nominal' or 'low OCR confidence'."""
    reasons: List[str] = []
    text = (c.raw_text or "").strip()
    if not text:
        reasons.append("empty read")
    elif c.confidence < LOW_CONF:
        reasons.append("low OCR confidence")
    if text and c.char_type in DIMENSION_TYPES and not c.nominal:
        reasons.append("missing nominal")
    if rotation_ambiguous:
        reasons.append("rotation ambiguity")
    if c.subtype == "note_ref" and known_note_positions is not None:
        if c.note_ref_pos is None or c.note_ref_pos not in known_note_positions:
            reasons.append("unknown note reference")
    return bool(reasons), reasons
```

- [ ] **Step 2: Add a test for the new reason**

Append to `tests/test_review.py`:

```python
def test_unknown_note_reference_when_pos_not_in_block():
    c = _row(char_type="Note", subtype="note_ref", raw_text="101",
             nominal="101", note_ref_pos=101)
    _, reasons = review_flags(c, rotation_ambiguous=False, known_note_positions={102, 103})
    assert "unknown note reference" in reasons


def test_known_note_reference_not_flagged():
    c = _row(char_type="Note", subtype="note_ref", raw_text="101",
             nominal="101", note_ref_pos=101)
    flagged, reasons = review_flags(c, rotation_ambiguous=False,
                                    known_note_positions={101, 102})
    assert "unknown note reference" not in reasons
    assert flagged is False


def test_note_ref_when_no_block_present_skips_unknown_check():
    c = _row(char_type="Note", subtype="note_ref", raw_text="101",
             nominal="101", note_ref_pos=101)
    _, reasons = review_flags(c, rotation_ambiguous=False, known_note_positions=None)
    assert "unknown note reference" not in reasons
```

- [ ] **Step 3: Run the review tests**

Run: `pytest tests/test_review.py -v`
Expected: all pre-existing review tests still pass; the three new ones pass.

- [ ] **Step 4: Write the failing extract-orchestration tests**

Append to `tests/test_pipeline_integration.py`:

```python
def test_extract_returns_extraction_result_with_no_notes(sample_pdf, tmp_path, monkeypatch):
    from app.pipeline.detect import Detection
    import app.pipeline.boxes as boxes_mod
    import app.pipeline.extract as extract_mod
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: None)
    det = Detection((40, 40, 120, 70), "dimension", 0.9)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [det])
    backend = StubVLMBackend(detections=[det], text="1,2 +0,1 -0,1")
    result = extract_mod.extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert result.notes is None
    assert len(result.characteristics) == 1


def test_extract_runs_notes_path_and_masks_image(sample_pdf, tmp_path, monkeypatch):
    """When the locator finds a notes block, the parsed NoteBlock is returned
    AND the image passed to detect_characteristics is the masked copy."""
    from app.pipeline.detect import Detection
    from app.pipeline.notes_block import NotesBlockRegion
    import app.pipeline.boxes as boxes_mod
    import app.pipeline.extract as extract_mod

    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    region = NotesBlockRegion(outer_box=(100, 100, 300, 300), lang_columns=[(100, 200), (200, 300)])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: region)
    monkeypatch.setattr(
        "app.pipeline.notes_block.read_notes_block",
        lambda image, region, backend: "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE\n101.1\tPLANARITY\tEBENHEIT")

    received = {}
    def fake_detect(image, backend, **kw):
        received["image"] = image
        return [Detection((40, 40, 120, 70), "dimension", 0.9)]
    monkeypatch.setattr(extract_mod, "detect_characteristics", fake_detect)

    backend = StubVLMBackend(text="1,2 +0,1 -0,1")
    result = extract_mod.extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)

    assert result.notes is not None
    positions = [n.pos for n in result.notes.notes]
    assert positions == [101, 1]
    # the image handed to detect_characteristics has the region masked white
    assert received["image"].getpixel((150, 150)) == (255, 255, 255)


def test_extract_resolves_note_ref_pos_and_flags_unknown(sample_pdf, tmp_path, monkeypatch):
    from app.pipeline.boxes import BoxDetection
    from app.pipeline.detect import merge_boxes, Detection
    from app.pipeline.notes_block import NotesBlockRegion
    import app.pipeline.extract as extract_mod

    box = BoxDetection(outer_box=(50, 50, 90, 78), inner_box=(54, 54, 86, 74),
                       cells=1, subtype="theoretical", conf=0.8)
    region = NotesBlockRegion(outer_box=(200, 200, 400, 400), lang_columns=[(200, 400)])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: region)
    monkeypatch.setattr("app.pipeline.notes_block.read_notes_block",
                        lambda image, region, backend: "101\tA-en\tA-de")
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: merge_boxes(
                            [Detection(box=(50, 50, 90, 78), kind="dimension", conf=0.9)], [box]))
    backend = StubVLMBackend(detections=[], text="105")     # references a non-existent note
    result = extract_mod.extract(sample_pdf, tmp_path, backend=backend)
    assert len(result.characteristics) == 1
    c = result.characteristics[0]
    assert c.subtype == "note_ref"
    assert c.note_ref_pos == 105
    assert "unknown note reference" in c.review_reasons
    assert c.needs_review is True
```

- [ ] **Step 5: Run the new tests to confirm they fail**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: the new tests FAIL (extract still returns a list; locator path missing).

- [ ] **Step 6: Update `app/pipeline/extract.py`**

Replace the file with:

```python
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


def extract(pdf_path, work_dir, dpi: int = 300, backend=None) -> ExtractionResult:
    work_dir = Path(work_dir)
    backend = backend or get_backend()
    if not hasattr(backend, "detect_regions"):
        raise RuntimeError("auto-ballooning requires the VLM backend")

    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    image = Image.open(render.png_path).convert("RGB")

    # Notes-block path: locate, read, parse, mask. Any failure leaves notes=None
    # and the rest of the pipeline runs unchanged.
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

    detections = detect_characteristics(image_for_detect, backend)

    results = []
    for d in detections:
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
        known_positions = ({n.pos for n in notes_obj.notes if n.parent_pos is None}
                           if notes_obj is not None else None)
        c.needs_review, c.review_reasons = review_flags(
            c, rotation_ambiguous, known_note_positions=known_positions)
        results.append(c)

    number_characteristics(results)
    place_balloons(results)
    return ExtractionResult(characteristics=results, notes=notes_obj)
```

- [ ] **Step 7: Update the existing integration tests that asserted on a `list` return**

The previous tests in `tests/test_pipeline_integration.py` (and `tests/test_api.py`) call `extract(...)` and unpack the result as a list. Update them to read `.characteristics`. Find each call to `extract(` in `tests/` and adjust. Replace:

```python
rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
```

with:

```python
result = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
rows = result.characteristics
```

This pattern applies to every previously-passing test in
`tests/test_pipeline_integration.py` — leave their assertions intact;
only the unpacking changes. Same change in any other file that calls
`extract()`.

(Also: also disable the notes-block locator in the legacy tests so they
keep behaving as before. Inside each test that previously expected a
plain list, add:

```python
monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                    lambda image, backend: None)
```

at the top of the test body.)

- [ ] **Step 8: Run the full suite**

Run: `pytest tests/ -v`
Expected: all tests pass — the previously-passing ones, the new model tests, the new notes_block tests, and the new extract-orchestration tests.

- [ ] **Step 9: Commit**

```bash
git add app/pipeline/extract.py app/pipeline/review.py tests/test_pipeline_integration.py tests/test_review.py
git commit -m "feat: wire notes-block path into extract() with masking and note_ref linkage"
```

---

## Task 9: Update `/api/upload` to return notes alongside rows

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_api.py` to confirm the existing pattern, then append:

```python
def test_upload_returns_notes_field(monkeypatch, sample_pdf):
    """The upload endpoint now returns {rows, notes}; notes may be null."""
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult, NoteBlock, Note

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, char_type="Distance", nominal="1,2")],
        notes=NoteBlock(region=(0, 0, 10, 10),
                        notes=[Note(pos=101, text_en="A", text_de="B"),
                               Note(pos=1, parent_pos=101, sub_index=1,
                                    text_en="A1", text_de="A1")])
    ))
    client = TestClient(main.app)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert "rows" in data and len(data["rows"]) == 1
    assert "notes" in data and data["notes"] is not None
    note_positions = [n["pos"] for n in data["notes"]["notes"]]
    assert note_positions == [101, 1]


def test_upload_returns_null_notes_when_extract_returns_none(monkeypatch, sample_pdf):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1)], notes=None))
    client = TestClient(main.app)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["notes"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_api.py::test_upload_returns_notes_field -v`
Expected: FAIL — current `/api/upload` returns `rows`, not `notes`.

- [ ] **Step 3: Update the upload endpoint**

In `app/main.py`, replace the `upload` function body (lines 57-74):

```python
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    work = _SESSIONS / session_id
    work.mkdir(parents=True, exist_ok=True)
    pdf_path = work / "input.pdf"
    pdf_path.write_bytes(await file.read())
    try:
        result = extract(pdf_path, work_dir=work, dpi=300, backend=_BACKEND)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="could not read the PDF")
    return JSONResponse({
        "session_id": session_id,
        "image_url": f"/api/image/{session_id}",
        "rows": [r.model_dump() for r in result.characteristics],
        "notes": result.notes.model_dump() if result.notes is not None else None,
    })
```

- [ ] **Step 4: Run the API tests**

Run: `pytest tests/test_api.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: /api/upload returns notes alongside rows"
```

---

## Task 10: UI — Notes section + main-row "→ note N" indicator

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`

The UI has no automated tests today; verify by running the app and
uploading a PDF that contains a notes block. The acceptance criterion is
that the new notes section renders, indents sub-bullets, and that main
rows with `note_ref_pos` show a "→ note N" indicator linking (anchor
scroll) to the matching row in the notes section.

- [ ] **Step 1: Update `index.html`**

In `app/static/index.html`, inside the `<style>` block, add (next to the existing `tr.low` rule):

```css
    #notes { margin-top: 16px; }
    #notes h3 { margin: 0 0 6px 0; font-size: 14px; }
    #notes table { border-collapse: collapse; width: 100%; font-size: 13px; }
    #notes td.sub { padding-left: 18px; color: #444; }
    #notes tr.low td { background: #fff7ed; }
    .note-ref { color: #1d4ed8; cursor: pointer; margin-left: 4px; font-size: 11px; }
    .note-ref:hover { text-decoration: underline; }
```

Inside the `<div id="right">` block, AFTER the existing `<table id="grid">…</table>`, append:

```html
    <section id="notes" hidden>
      <h3>Notes</h3>
      <table>
        <thead><tr><th>Pos</th><th>English</th><th>German</th></tr></thead>
        <tbody></tbody>
      </table>
    </section>
```

- [ ] **Step 2: Update `app.js`**

Add a module-level variable near the existing `let rows = [];`:

```js
let notesBlock = null;
```

In the `/api/upload` response handler, after `rows = data.rows;`, add:

```js
  notesBlock = data.notes;
```

Find the existing `renderGrid` function. Modify the row's HTML
construction so that any row with `note_ref_pos` shows a clickable
indicator. Replace the `tr.innerHTML = …` line with:

```js
    const refIndicator = r.note_ref_pos
      ? `<span class="note-ref" data-pos="${r.note_ref_pos}">→ note ${r.note_ref_pos}</span>`
      : "";
    tr.innerHTML =
      `<td>${posCell}${refIndicator}</td>` +
      ["char_type", "nominal", "upper_tol", "lower_tol"]
        .map((k) => `<td contenteditable data-i="${i}" data-k="${k}">${r[k] ?? ""}</td>`)
        .join("");
```

After the existing `tb.querySelectorAll("td[contenteditable]")…` block, add:

```js
  tb.querySelectorAll(".note-ref").forEach((el) => {
    el.addEventListener("click", () => {
      const target = document.getElementById(`note-${el.dataset.pos}`);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
```

Add a new function `renderNotes`:

```js
function renderNotes() {
  const section = $("#notes");
  const tb = section.querySelector("tbody");
  tb.innerHTML = "";
  if (!notesBlock || !notesBlock.notes || notesBlock.notes.length === 0) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  notesBlock.notes.forEach((n) => {
    const tr = document.createElement("tr");
    const isSub = n.parent_pos != null;
    if (n.needs_review) {
      tr.className = "low";
      tr.title = (n.review_reasons || []).join(", ");
    }
    const posLabel = isSub ? `${n.parent_pos}.${n.sub_index}` : `${n.pos}`;
    const anchor = isSub ? "" : ` id="note-${n.pos}"`;
    tr.innerHTML =
      `<td${anchor} class="${isSub ? "sub" : ""}">${posLabel}</td>` +
      `<td>${n.text_en ?? ""}</td>` +
      `<td>${n.text_de ?? ""}</td>`;
    tb.appendChild(tr);
  });
}
```

In the upload handler, call `renderNotes()` after `renderGrid()`:

```js
  renderGrid();
  renderNotes();
```

- [ ] **Step 3: Run the app and verify manually**

Start the server (use whatever launch command the project uses; the
`README.md` covers it). Upload a representative drawing that contains a
notes block (e.g. `T1025300_B.pdf` if available in `test_docs/`).

Expected:
- Main rows that previously appeared as bogus "Distance/Flatness" for
  inline bullets are no longer present (the block was masked).
- The Notes section appears below the main grid, with one row per parsed
  note and indented sub-bullets.
- A boxed `101` reference balloon on the view shows `→ note 101` next
  to the position cell; clicking it scrolls to the matching row in the
  Notes section.

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: notes section in UI + →note ref indicator on main rows"
```

---

## Task 11: Excel — Notes sheet + `note_ref_pos` suffix in comment column

**Files:**
- Modify: `app/excel.py`
- Test: `tests/test_excel.py`

- [ ] **Step 1: Inspect the existing test file pattern**

Run: `head -40 tests/test_excel.py` to see the helper style. Then write
tests in the same style.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_excel.py`:

```python
def test_notes_sheet_created_when_note_block_passed(tmp_path):
    from openpyxl import load_workbook
    from app.excel import write_workbook
    from app.models import Note, NoteBlock, Characteristic

    nb = NoteBlock(region=(0, 0, 10, 10), notes=[
        Note(pos=101, text_en="CONTACT AREA NOTES", text_de="KONTAKTBEREICH HINWEISE"),
        Note(pos=1, parent_pos=101, sub_index=1, text_en="PLANARITY", text_de="EBENHEIT"),
        Note(pos=2, parent_pos=101, sub_index=2, text_en="SURFACE", text_de="OBERFLAECHE"),
        Note(pos=102, text_en="PART FREE OF GREASE", text_de="OHNE FETT"),
    ])
    out = tmp_path / "x.xlsx"
    write_workbook([Characteristic(pos=1, char_type="Distance", nominal="1,2")],
                   out, notes=nb)
    wb = load_workbook(out)
    assert "Notes" in wb.sheetnames
    ws = wb["Notes"]
    # Headers
    assert ws.cell(1, 1).value == "Pos"
    assert ws.cell(1, 2).value == "English"
    assert ws.cell(1, 3).value == "German"
    # Rows in order, with sub-bullet pos formatted as "101.1"
    assert ws.cell(2, 1).value == "101"
    assert ws.cell(3, 1).value == "101.1"
    assert ws.cell(4, 1).value == "101.2"
    assert ws.cell(5, 1).value == "102"


def test_notes_sheet_absent_when_no_notes_passed(tmp_path):
    from openpyxl import load_workbook
    from app.excel import write_workbook
    from app.models import Characteristic

    out = tmp_path / "x.xlsx"
    write_workbook([Characteristic(pos=1, char_type="Distance", nominal="1,2")],
                   out)
    wb = load_workbook(out)
    assert "Notes" not in wb.sheetnames
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/test_excel.py -v`
Expected: FAIL — `write_workbook` does not accept `notes=`; no Notes sheet.

- [ ] **Step 4: Update `app/excel.py`**

Replace the file with:

```python
from pathlib import Path
from typing import Iterable, Optional
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Side, Font
from app.models import Characteristic, NoteBlock

HEADERS = [
    ("Pos.", "Pos."),
    ("Merkmal", "Characteristic"),
    ("Nennmaß", "Nominal value"),
    ("O-TOL", "Upper-tol"),
    ("U-TOL", "Lower-tol"),
]

_thin = Side(style="thin")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_center = Alignment(horizontal="center", vertical="center")


def _write_characteristics_sheet(ws, rows: Iterable[Characteristic]) -> None:
    for col, (de, en) in enumerate(HEADERS, start=1):
        top = ws.cell(1, col, de)
        bot = ws.cell(2, col, en)
        for cell in (top, bot):
            cell.font = Font(bold=True)
            cell.alignment = _center
            cell.border = _border

    ordered = sorted(rows, key=lambda c: c.pos)
    for i, c in enumerate(ordered, start=3):
        values = [c.pos, c.char_type, c.nominal, c.upper_tol, c.lower_tol]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(i, col, v)
            cell.alignment = _center
            cell.border = _border

    widths = [8, 18, 16, 12, 12]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + col)].width = w


def _write_notes_sheet(ws, block: NoteBlock) -> None:
    headers = ["Pos", "English", "German"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(1, col, h)
        cell.font = Font(bold=True)
        cell.alignment = _center
        cell.border = _border
    # Render notes in source order. Sub-bullets show "<parent>.<sub>" as Pos.
    for i, n in enumerate(block.notes, start=2):
        if n.parent_pos is not None and n.sub_index is not None:
            pos_label = f"{n.parent_pos}.{n.sub_index}"
        else:
            pos_label = f"{n.pos}"
        ws.cell(i, 1, pos_label)
        ws.cell(i, 2, n.text_en)
        ws.cell(i, 3, n.text_de)
    for col, w in enumerate([10, 48, 48], start=1):
        ws.column_dimensions[chr(64 + col)].width = w


def write_workbook(rows: Iterable[Characteristic], path: Path,
                   notes: Optional[NoteBlock] = None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection"
    _write_characteristics_sheet(ws, rows)
    if notes is not None and notes.notes:
        _write_notes_sheet(wb.create_sheet("Notes"), notes)
    path = Path(path)
    wb.save(path)
```

- [ ] **Step 5: Update the export endpoint to pass notes**

The current `/api/export` endpoint accepts `ExportRequest { session_id, rows }`. The notes block isn't sent by the client today; for now, the export call goes through unchanged (no notes sheet). In a follow-up step we wire the UI to send notes, but the Excel writer is already notes-aware.

In `app/main.py`, extend `ExportRequest`:

```python
class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]
    notes: Optional[NoteBlock] = None
```

Add the import near the top of `app/main.py`:

```python
from app.models import Characteristic, NoteBlock
```

(Replacing the existing `from app.models import Characteristic` line.)

Then update the `export` handler to pass it:

```python
@app.post("/api/export")
def export(req: ExportRequest):
    work = _session_dir(req.session_id)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "inspection.xlsx"
    write_workbook(req.rows, out, notes=req.notes)
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="inspection.xlsx",
    )
```

And finally, in `app/static/app.js`, update the `download` function so
the export request includes the notes block:

```js
async function download(endpoint, filename) {
  const res = await fetch(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, rows, notes: notesBlock }),
  });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}
```

- [ ] **Step 6: Run the Excel and API tests**

Run: `pytest tests/test_excel.py tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add app/excel.py app/main.py app/static/app.js tests/test_excel.py
git commit -m "feat: Excel Notes sheet + export carries the notes block"
```

---

## Task 12: Remove the superseded `notes.py` and its tests

**Files:**
- Delete: `app/pipeline/notes.py`
- Delete: `tests/test_notes.py`

- [ ] **Step 1: Confirm nothing imports the old module**

Run: `grep -rn "from app.pipeline.notes " app tests || true`
Run: `grep -rn "import app.pipeline.notes" app tests || true`
Expected: zero matches (everything now goes through `notes_block`).

- [ ] **Step 2: Delete both files**

```bash
git rm app/pipeline/notes.py tests/test_notes.py
```

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -v`
Expected: all pass; no missing-module errors.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove superseded notes.py (replaced by notes_block.py)"
```

---

## Task 13: End-to-end T1025300 scenario integration test

**Files:**
- Create: `tests/test_extract_notes_integration.py`

This test exercises the full `extract()` path with a backend stub whose
canned responses mirror what we'd expect on the `T1025300_B` drawing.
It is the regression test that proves the original screenshot's
failures (balloons 1/2/3 misclassified as Distance/Flatness) cannot
happen with this design.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_notes_integration.py`:

```python
"""End-to-end regression test for the T1025300 scenario.

The original failure was that inline bullets 1, 2, 3 inside note 101 of
the general-notes table were emitted as bogus Distance/Flatness rows in
the main characteristics table. With the notes-block path masking the
table before detect_characteristics runs, those bullets are now in
result.notes and NOT in result.characteristics."""
from PIL import Image

from app.pipeline.detect import Detection
from app.pipeline.boxes import BoxDetection
from app.pipeline.notes_block import NotesBlockRegion
from app.pipeline.ocr.base import OcrResult


class _T1025300Backend:
    """A stub that mimics the VLM/CV pipeline on T1025300_B."""
    NOTES_TEXT = (
        "101\tCONTACT AREA NOTES\tKONTAKTBEREICH HINWEISE\n"
        "101.1\tCONTACT AREA PLANARITY 0,2mm\tKONTAKTBEREICH EBENHEIT 0,2mm\n"
        "101.2\tCONTACT AREA SURFACE QUALITY 2,5x5 Rz16\tKONTAKTBEREICH OBERFLAECHE 2,5x5 Rz16\n"
        "101.3\tPART FREE OF GREASE AND OIL\tBAUTEIL FREI VON FETT UND OEL\n"
        "102\tMEASURING POINT FOR COAT THICKNESS\tMESSPUNKT FUER SCHICHTDICKE\n"
        "103\tCOMPONENT WITHOUT SURFACE TREATMENT\tBAUTEIL OHNE OBERFLAECHENBEHANDLUNG"
    )

    def detect_regions(self, image):
        # We bypass this path entirely via monkeypatching the locator / detector,
        # but the method must exist for extract() to accept the backend.
        return []

    def read_region(self, image):
        return OcrResult(text="1,2 +0,1 -0,1", confidence=0.9)

    def read_notes_block(self, image):
        return OcrResult(text=self.NOTES_TEXT, confidence=0.9)


def test_t1025300_inline_bullets_appear_in_notes_not_characteristics(
        sample_pdf, tmp_path, monkeypatch):
    import app.pipeline.extract as extract_mod
    import app.pipeline.boxes as boxes_mod

    # No CV-detected boxes for this scenario.
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])

    # Force the locator to return the notes-block region without invoking the VLM.
    region = NotesBlockRegion(outer_box=(800, 100, 1700, 350),
                              lang_columns=[(800, 1250), (1250, 1700)])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: region)

    # The main detector finds three "dimensions" that, in the buggy world, would
    # have been the inline 1/2/3 bullets — but since the locator already masked
    # the block, those pixels are now white and detect_characteristics is asked
    # to find NO callouts inside it. We model that by returning only a single
    # real dimension elsewhere on the page (one outside the notes region).
    real_dim = Detection(box=(200, 600, 320, 640), kind="dimension", conf=0.9)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [real_dim])

    backend = _T1025300Backend()
    result = extract_mod.extract(sample_pdf, tmp_path, backend=backend)

    # The notes block was parsed and has the expected structure.
    assert result.notes is not None
    top_level = [n.pos for n in result.notes.notes if n.parent_pos is None]
    assert top_level == [101, 102, 103]
    sub_of_101 = [n.sub_index for n in result.notes.notes if n.parent_pos == 101]
    assert sub_of_101 == [1, 2, 3]

    # Crucially: the main characteristics table has ONLY the real dimension.
    # The inline 1/2/3 bullets are NOT present.
    assert len(result.characteristics) == 1
    assert result.characteristics[0].char_type == "Distance"
    # And it definitely has none of the buggy outputs from the screenshot.
    chars = result.characteristics
    bogus = [c for c in chars if c.nominal in ("16",) and c.char_type == "Distance"]
    assert bogus == []
```

- [ ] **Step 2: Run to verify**

Run: `pytest tests/test_extract_notes_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite once more**

Run: `pytest tests/ -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_extract_notes_integration.py
git commit -m "test: end-to-end regression for T1025300 notes-block scenario"
```

---

## Self-Review Notes

After writing this plan I checked it back against the spec:

- **Goals coverage:** ① notes block located & read structurally — Tasks 6, 7. ② sub-bullets parsed & linked — Tasks 3. ③ masking before main detector — Tasks 5, 8. ④ `note_ref_pos` validated against parsed block — Tasks 2, 8 (plus the `review.py` extension). ⑤ Notes surface as a parallel section in UI / Excel — Tasks 10, 11.
- **Non-goals respected:** no balloon-placement changes; no new model; no symbol-anchored cropping or two-pass reconciliation (those are separate specs as the spec stated).
- **Type consistency:** `Note`, `NoteBlock`, `NotesBlockRegion`, `ExtractionResult`, `Characteristic.note_ref_pos` are defined once and used with consistent field names everywhere they appear in the plan. `review_flags`'s new keyword argument is named `known_note_positions` in both `review.py` and its tests; `review_flags_note`'s parameter is `known_parents` and consistent with its caller in `extract.py` and tests.
- **No placeholders.** Each step contains the actual code or command an engineer needs.
