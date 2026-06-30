# Marks-Block Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the top-right Mark/Description table from each drawing into a new parallel output (`ExtractionResult.marks`), surface it in a new collapsible right-pane "Marks" section above Notes, and emit it as a separate Excel sheet — without touching the existing notes-block flow.

**Architecture:** A new module `app/pipeline/marks_block.py` mirrors `notes_block.py` but locates the table with a deterministic CV heuristic (largest rectangle whose centre falls in the top-right quadrant), reuses the VLM `read_notes_block` prompt for transcription, parses tab-separated EN/DE rows into `Mark` records, and masks the located region before `detect_characteristics` runs so the 101… numbers inside cannot be misclassified as note-ref balloons. New `Mark`/`MarkBlock` models, a new `marks` field on `ExtractionResult`, a new `_write_marks_sheet` in `excel.py`, and a new `#marks-section` in the right pane wire it end-to-end.

**Tech Stack:** Python 3 / FastAPI / Pydantic / OpenCV / Pillow / openpyxl / pytest; vanilla JS for the UI.

**Spec:** `docs/superpowers/specs/2026-06-29-marks-block-extraction-design.md`

---

## File Structure

**Create:**
- `app/pipeline/marks_block.py` — locator, reader, parser, masker, review-flagger
- `tests/test_marks_block.py` — unit tests for parser, locator, review flags

**Modify:**
- `app/models.py` — add `Mark`, `MarkBlock`, `ExtractionResult.marks`
- `app/pipeline/extract.py` — call marks locator/reader; mask region; populate result
- `app/excel.py` — `_write_marks_sheet`; `write_workbook` accepts `marks=`
- `app/main.py` — extraction response includes `marks`; `/api/export` request accepts `marks`
- `app/static/index.html` — `<section id="marks-section">` above notes
- `app/static/styles/components.css` — `.marks` table style (alias of `.notes`)
- `app/static/js/state.js` — `state.marks`, set/clear in session
- `app/static/js/table.js` — `renderMarks`, toggle binding
- `app/static/js/main.js` — include `marks` in export payloads
- `tests/test_extract_notes_integration.py` — extend with marks-block assertions
- `tests/test_excel.py` — assert "Marks" sheet on output
- `tests/test_api.py` — assert `marks` in upload response

---

## Task 1: Data models — `Mark`, `MarkBlock`, `ExtractionResult.marks`

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
from app.models import Mark, MarkBlock, ExtractionResult, Characteristic


def test_mark_defaults():
    m = Mark(pos=101)
    assert m.pos == 101
    assert m.text_en == "" and m.text_de == "" and m.raw_text == ""
    assert m.needs_review is False
    assert m.review_reasons == []


def test_markblock_holds_region_and_marks():
    block = MarkBlock(region=(10, 20, 200, 100), marks=[Mark(pos=101, text_en="A")])
    assert block.region == (10, 20, 200, 100)
    assert len(block.marks) == 1
    assert block.marks[0].text_en == "A"


def test_extractionresult_marks_optional_default_none():
    r = ExtractionResult(characteristics=[])
    assert r.notes is None
    assert r.marks is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v -k "mark or extractionresult_marks"`
Expected: FAIL with `ImportError: cannot import name 'Mark'` / `'MarkBlock'`.

- [ ] **Step 3: Add the models**

Edit `app/models.py`. Add the two classes immediately after `NoteBlock` and add the `marks` field on `ExtractionResult`:

```python
class Mark(BaseModel):
    pos: int                          # 101, 102, …
    text_en: str = ""
    text_de: str = ""
    raw_text: str = ""
    needs_review: bool = False
    review_reasons: List[str] = []


class MarkBlock(BaseModel):
    region: Tuple[float, float, float, float]
    marks: List[Mark] = []


class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
    marks: Optional[MarkBlock] = None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_models.py -v -k "mark or extractionresult_marks"`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat(models): add Mark/MarkBlock + ExtractionResult.marks"
```

---

## Task 2: Parser — `parse_marks_block`

**Files:**
- Create: `app/pipeline/marks_block.py`
- Test: `tests/test_marks_block.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_marks_block.py`:

```python
from app.pipeline.marks_block import parse_marks_block


def test_parses_top_level_bilingual_row():
    raw = "101\tCONTACT AREA FREE OF GREASE AND OIL\tKONTAKTBEREICH FREI VON FETTEN UND OEL"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert block.region == (0, 0, 100, 100)
    assert len(block.marks) == 1
    m = block.marks[0]
    assert m.pos == 101
    assert m.text_en == "CONTACT AREA FREE OF GREASE AND OIL"
    assert m.text_de == "KONTAKTBEREICH FREI VON FETTEN UND OEL"
    assert m.raw_text == raw


def test_parses_single_language_row_when_no_tab_after_en():
    raw = "102\tCONTACT AREA FREE FROM DAMAGES"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert len(block.marks) == 1
    m = block.marks[0]
    assert m.text_en == "CONTACT AREA FREE FROM DAMAGES"
    assert m.text_de == ""


def test_drops_malformed_lines_silently():
    raw = (
        "this is not a mark row\n"
        "101\tA\tB\n"
        "\n"
        "garbage 999\n"
    )
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    positions = [m.pos for m in block.marks]
    assert positions == [101]


def test_parses_multiple_rows_in_source_order():
    raw = (
        "101\tA-en\tA-de\n"
        "102\tB-en\tB-de\n"
        "109\tI-en\tI-de\n"
    )
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert [m.pos for m in block.marks] == [101, 102, 109]


def test_three_digit_pos_outside_10x_range_still_accepted():
    raw = "199\tmark text en\tmark text de"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert block.marks[0].pos == 199


def test_sub_bullet_lines_are_dropped():
    # marks table has no sub-bullets; if VLM emits one, parser must drop it
    raw = "101\tA\tB\n101.1\tsub\tnot expected\n102\tC\tD"
    block = parse_marks_block(raw, region=(0, 0, 100, 100))
    assert [m.pos for m in block.marks] == [101, 102]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marks_block.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_marks_block'`.

- [ ] **Step 3: Create `marks_block.py` with the parser**

Create `app/pipeline/marks_block.py`:

```python
"""Marks-block path: locate the top-right Mark/Description legend, read it as
structured bilingual data, mask it before the main detector runs so its 101…
numbers cannot be misclassified as note-ref callouts. Parallel to (and
independent of) notes_block.py."""
import re
import sys
from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image, ImageDraw

from app.models import Mark, MarkBlock


# Top-level row only. Marks table has no sub-bullets, so any "<pos>.<sub>\t…"
# line is rejected by this regex and dropped silently.
_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$")


def parse_marks_block(raw: str, region: Tuple[float, float, float, float]) -> MarkBlock:
    """Parse the tab-separated marks transcription into a MarkBlock.

    Each line is expected as '<pos>\\t<en>\\t<de>'. Lines containing a
    sub-index (e.g. '101.1\\t…') or any other shape are dropped silently
    (non-fatal pipeline convention)."""
    marks: List[Mark] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if "." in line.split("\t", 1)[0]:
            # sub-bullet — not expected in marks; drop
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        marks.append(Mark(
            pos=int(m.group(1)),
            text_en=m.group(2).strip(),
            text_de=m.group(3).strip(),
            raw_text=line,
        ))
    return MarkBlock(region=region, marks=marks)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_marks_block.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/marks_block.py tests/test_marks_block.py
git commit -m "feat(marks_block): parse_marks_block + tests"
```

---

## Task 3: Review flags — `review_flags_mark`

**Files:**
- Modify: `app/pipeline/marks_block.py`
- Test: `tests/test_marks_block.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marks_block.py`:

```python
from app.models import Mark
from app.pipeline.marks_block import review_flags_mark


def _mark(**kw):
    base = dict(pos=101, text_en="A", text_de="B", raw_text="101\tA\tB")
    base.update(kw)
    return Mark(**base)


def test_clean_mark_not_flagged():
    needs, reasons = review_flags_mark(_mark(), two_columns=True)
    assert needs is False and reasons == []


def test_empty_read_flagged():
    needs, reasons = review_flags_mark(_mark(raw_text=""), two_columns=True)
    assert needs is True and reasons == ["empty read"]


def test_missing_german_flagged_when_two_columns():
    needs, reasons = review_flags_mark(_mark(text_de=""), two_columns=True)
    assert needs is True and reasons == ["missing translation"]


def test_single_column_does_not_require_german():
    needs, reasons = review_flags_mark(_mark(text_de=""), two_columns=False)
    assert needs is False and reasons == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marks_block.py -v -k "review or empty or missing or single_column or clean_mark"`
Expected: FAIL with `ImportError: cannot import name 'review_flags_mark'`.

- [ ] **Step 3: Add the function**

Append to `app/pipeline/marks_block.py`:

```python
def review_flags_mark(mark: Mark, two_columns: bool) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a parsed mark.

    An empty read is its own reason and does not also report
    'missing translation'."""
    reasons: List[str] = []
    if not (mark.raw_text or "").strip():
        reasons.append("empty read")
    else:
        if two_columns and (not mark.text_en.strip() or not mark.text_de.strip()):
            reasons.append("missing translation")
    return bool(reasons), reasons
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_marks_block.py -v`
Expected: 10 PASS (6 from Task 2 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/marks_block.py tests/test_marks_block.py
git commit -m "feat(marks_block): review_flags_mark + tests"
```

---

## Task 4: Region masker — `MarksBlockRegion` + `mask_region`

**Files:**
- Modify: `app/pipeline/marks_block.py`
- Test: `tests/test_marks_block.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marks_block.py`:

```python
from PIL import Image
from app.pipeline.marks_block import MarksBlockRegion, mask_region


def test_mask_region_fills_outer_box_white_and_preserves_outside():
    img = Image.new("RGB", (100, 100), color=(50, 50, 50))
    region = MarksBlockRegion(outer_box=(20, 30, 60, 70), lang_columns=[(20, 60)])
    out = mask_region(img, region)
    # inside the box: white
    assert out.getpixel((25, 35)) == (255, 255, 255)
    # outside the box: untouched
    assert out.getpixel((5, 5)) == (50, 50, 50)
    # original not mutated
    assert img.getpixel((25, 35)) == (50, 50, 50)


def test_mask_region_noop_on_zero_size_box():
    img = Image.new("RGB", (50, 50), color=(0, 0, 0))
    region = MarksBlockRegion(outer_box=(10, 10, 10, 10), lang_columns=[(10, 10)])
    out = mask_region(img, region)
    assert out.getpixel((10, 10)) == (0, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marks_block.py -v -k "mask_region"`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add region dataclass and masker**

Append to `app/pipeline/marks_block.py`:

```python
@dataclass
class MarksBlockRegion:
    outer_box: Tuple[int, int, int, int]
    lang_columns: List[Tuple[int, int]]


def mask_region(image: Image.Image, region: MarksBlockRegion) -> Image.Image:
    """Return a copy of `image` with `region.outer_box` filled white. The
    original image is preserved so downstream manual re-reads still work."""
    out = image.copy()
    x0, y0, x1, y1 = region.outer_box
    if x1 > x0 and y1 > y0:
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill="white")
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_marks_block.py -v -k "mask_region"`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/marks_block.py tests/test_marks_block.py
git commit -m "feat(marks_block): MarksBlockRegion + mask_region"
```

---

## Task 5: Locator — `locate_marks_block`

**Files:**
- Modify: `app/pipeline/marks_block.py`
- Test: `tests/test_marks_block.py`

The locator finds rectangles via OpenCV contours (same algorithm as
`app/pipeline/boxes.py:_find_rectangles` but with a *larger* `max_area_frac`
because the Marks table can occupy up to ~30 % of the page area), filters to
those whose centre falls in the top-right quadrant, and picks the largest.
We inline the contour code rather than reusing `detect_boxes` because that
helper hard-caps `max_area_frac=0.05` which would reject the legend table.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_marks_block.py`:

```python
from PIL import Image, ImageDraw
from app.pipeline.marks_block import locate_marks_block


def _white_canvas(w=1000, h=700):
    return Image.new("RGB", (w, h), color=(255, 255, 255))


def _draw_rect(img, x0, y0, x1, y1, stroke=3):
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x1, y1), outline=(0, 0, 0), width=stroke)
    return img


def test_locator_picks_top_right_rectangle():
    img = _white_canvas()
    # decoy in bottom-left
    _draw_rect(img, 30, 500, 250, 650)
    # target in top-right
    _draw_rect(img, 700, 30, 970, 300)
    region = locate_marks_block(img)
    assert region is not None
    x0, y0, x1, y1 = region.outer_box
    # centre in top-right quadrant
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert cx > 0.55 * 1000
    assert cy < 0.45 * 700
    # roughly matches the drawn target (allow a few px for stroke)
    assert abs(x0 - 700) <= 5 and abs(y0 - 30) <= 5
    assert abs(x1 - 970) <= 5 and abs(y1 - 300) <= 5


def test_locator_returns_none_when_no_top_right_rectangle():
    img = _white_canvas()
    _draw_rect(img, 30, 500, 250, 650)        # bottom-left
    _draw_rect(img, 400, 300, 600, 500)       # centre
    assert locate_marks_block(img) is None


def test_locator_picks_largest_when_multiple_top_right():
    img = _white_canvas()
    _draw_rect(img, 700, 30, 800, 100)        # small top-right
    _draw_rect(img, 600, 50, 970, 400)        # large top-right
    region = locate_marks_block(img)
    assert region is not None
    x0, y0, x1, y1 = region.outer_box
    assert abs(x0 - 600) <= 5 and abs(y1 - 400) <= 5


def test_locator_returns_none_on_blank_image():
    assert locate_marks_block(_white_canvas()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marks_block.py -v -k "locator"`
Expected: FAIL with `ImportError: cannot import name 'locate_marks_block'`.

- [ ] **Step 3: Implement the locator**

Append to `app/pipeline/marks_block.py`:

```python
import cv2
import numpy as np


# Top-right quadrant thresholds (tunable). The locator considers only
# rectangles whose centre falls into the region x > _CX_MIN_FRAC*W,
# y < _CY_MAX_FRAC*H, and whose area is at least _MIN_AREA_FRAC of the page.
_CX_MIN_FRAC = 0.55
_CY_MAX_FRAC = 0.45
_MIN_AREA_FRAC = 0.02
_MAX_AREA_FRAC = 0.40   # legend table tops out around ~30 % of page
_MIN_SIDE = 40          # px — reject narrow strips


def _find_large_rectangles(gray: "np.ndarray") -> List[Tuple[int, int, int, int]]:
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
        if bw < _MIN_SIDE or bh < _MIN_SIDE:
            continue
        area = bw * bh
        if area < _MIN_AREA_FRAC * page_area:
            continue
        if area > _MAX_AREA_FRAC * page_area:
            continue
        rects.append((x, y, x + bw, y + bh))
    return rects


def _infer_columns(image: Image.Image, box: Tuple[int, int, int, int]) -> List[Tuple[int, int]]:
    """Return language-column x-ranges inside `box`. 2 columns if a strong
    vertical divider is found near the middle, else 1."""
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


def locate_marks_block(image: Image.Image):
    """Find the Mark/Description legend by picking the largest rectangle whose
    centre lies in the top-right quadrant. Never raises; any failure logs to
    stderr and returns None so the pipeline runs without a marks section."""
    try:
        w, h = image.size
        gray = np.array(image.convert("L"))
        candidates = []
        for rect in _find_large_rectangles(gray):
            x0, y0, x1, y1 = rect
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if cx < _CX_MIN_FRAC * w:
                continue
            if cy > _CY_MAX_FRAC * h:
                continue
            candidates.append(rect)
        if not candidates:
            return None
        pick = max(candidates, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        columns = _infer_columns(image, pick)
        return MarksBlockRegion(outer_box=tuple(int(v) for v in pick),
                                lang_columns=columns)
    except Exception as e:
        print(f"[sindri.marks_block] locator failed: {e!r}",
              file=sys.stderr, flush=True)
        return None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_marks_block.py -v -k "locator"`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/marks_block.py tests/test_marks_block.py
git commit -m "feat(marks_block): locate_marks_block (top-right CV heuristic)"
```

---

## Task 6: Reader — `read_marks_block`

**Files:**
- Modify: `app/pipeline/marks_block.py`
- Test: `tests/test_marks_block.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marks_block.py`:

```python
from app.pipeline.marks_block import read_marks_block, MarksBlockRegion


class _FakeOcrResult:
    def __init__(self, text): self.text = text


class _BackendWithNotesPrompt:
    def __init__(self, text):
        self._text = text
        # tracks whether the dedicated prompt was used
        self.used_notes_prompt = False

    def read_notes_block(self, image):
        self.used_notes_prompt = True
        return _FakeOcrResult(self._text)

    def read_region(self, image):
        return _FakeOcrResult("WRONG-PROMPT")


class _BackendGenericOnly:
    def read_region(self, image):
        return _FakeOcrResult("GENERIC")


def test_read_marks_prefers_notes_prompt_when_available():
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    region = MarksBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    backend = _BackendWithNotesPrompt("101\tA\tB")
    text = read_marks_block(img, region, backend)
    assert text == "101\tA\tB"
    assert backend.used_notes_prompt is True


def test_read_marks_falls_back_to_read_region():
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    region = MarksBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])
    text = read_marks_block(img, region, _BackendGenericOnly())
    assert text == "GENERIC"


def test_read_marks_returns_empty_on_backend_exception():
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    region = MarksBlockRegion(outer_box=(0, 0, 100, 100), lang_columns=[(0, 100)])

    class _Bad:
        def read_region(self, image):
            raise RuntimeError("boom")

    assert read_marks_block(img, region, _Bad()) == ""
```

(One small fix: the `_BackendWithNotesPrompt.__init__` has a syntax-style
issue with the comment on the same line as the assignment — Python tolerates
it because the comment starts on the next physical line, but if pyflakes
complains move the comment above the line. The test as written runs.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_marks_block.py -v -k "read_marks"`
Expected: FAIL with `ImportError: cannot import name 'read_marks_block'`.

- [ ] **Step 3: Add the reader**

Append to `app/pipeline/marks_block.py`:

```python
def read_marks_block(image: Image.Image, region: MarksBlockRegion, backend) -> str:
    """Read the marks block once and return the raw transcription text.

    The marks table has the same bilingual 'pos / EN / DE' shape as the
    notes table, so we reuse the VLM backend's notes prompt when available.
    Falls back to the generic `read_region` otherwise. Never raises."""
    crop = image.crop(region.outer_box)
    try:
        if hasattr(backend, "read_notes_block"):
            result = backend.read_notes_block(crop)
        else:
            result = backend.read_region(crop)
        return (result.text or "")
    except Exception as e:
        print(f"[sindri.marks_block] read failed: {e!r}",
              file=sys.stderr, flush=True)
        return ""
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_marks_block.py -v`
Expected: all 16 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/marks_block.py tests/test_marks_block.py
git commit -m "feat(marks_block): read_marks_block (reuses notes prompt)"
```

---

## Task 7: Wire into extraction pipeline

**Files:**
- Modify: `app/pipeline/extract.py:1-30, 70-90, 120-130`
- Test: `tests/test_extract_notes_integration.py` (extend in next task)

- [ ] **Step 1: Add import**

Edit `app/pipeline/extract.py`. Below the existing
`from app.pipeline import notes_block as nb` line (currently line 13), add:

```python
from app.pipeline import marks_block as mb
```

- [ ] **Step 2: Add the marks stage between notes-block and detection**

In `app/pipeline/extract.py`, replace the block currently spanning the notes
extraction (the `# Notes-block path: ...` comment through
`image_for_detect = image`) with:

```python
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

    # Marks-block path: top-right legend table. Independent of the notes
    # path — neither blocks the other. Same non-fatal convention.
    region_marks = mb.locate_marks_block(image)
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
```

- [ ] **Step 3: Return the marks in `ExtractionResult`**

In the same file, change the final return to:

```python
    return ExtractionResult(characteristics=results, notes=notes_obj, marks=marks_obj)
```

- [ ] **Step 4: Run the existing pipeline tests to confirm no regression**

Run: `pytest tests/test_pipeline_integration.py tests/test_extract_notes_integration.py -v`
Expected: PASS (or unchanged from main — no new failures attributable to this change).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/extract.py
git commit -m "feat(extract): wire marks_block stage alongside notes_block"
```

---

## Task 8: Integration test for marks extraction

**Files:**
- Test: `tests/test_extract_notes_integration.py` (extend)

- [ ] **Step 1: Inspect the existing test to learn the pattern**

Run: `head -80 tests/test_extract_notes_integration.py`

Look for how it constructs a fake backend and asserts on `result.notes`.
The same fixture shape (a stub backend with `detect_regions` returning the
right "note" detection, and `read_notes_block` returning hand-crafted text)
will be re-used.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_extract_notes_integration.py`:

```python
def test_extract_populates_marks_block_alongside_notes(sample_pdf, tmp_path, monkeypatch):
    """End-to-end: when the marks locator returns a region and the backend
    transcribes it, result.marks is populated independently of result.notes."""
    import app.pipeline.extract as extract_mod
    import app.pipeline.boxes as boxes_mod
    from app.pipeline.notes_block import NotesBlockRegion
    from app.pipeline.marks_block import MarksBlockRegion
    from app.pipeline.ocr.base import OcrResult

    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])

    # Notes locator returns a region (so the existing notes path still runs).
    notes_region = NotesBlockRegion(outer_box=(100, 100, 400, 300),
                                    lang_columns=[(100, 250), (250, 400)])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: notes_region)

    # Marks locator returns a top-right region.
    marks_region = MarksBlockRegion(outer_box=(1500, 50, 1900, 400),
                                    lang_columns=[(1500, 1700), (1700, 1900)])
    monkeypatch.setattr("app.pipeline.marks_block.locate_marks_block",
                        lambda image: marks_region)

    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [])

    class _Backend:
        # The same `read_notes_block` method is used for both notes and marks
        # transcription. The pipeline crops different regions, but our stub
        # ignores the crop and returns canned text — so we need to return
        # different text depending on which call this is. We track call count.
        def __init__(self):
            self._calls = 0
        def detect_regions(self, image): return []
        def read_region(self, image): return OcrResult(text="", confidence=0.0)
        def read_notes_block(self, image):
            self._calls += 1
            if self._calls == 1:
                return OcrResult(text="101\tNote-EN\tNote-DE", confidence=0.9)
            return OcrResult(text="201\tMark-EN\tMark-DE\n202\tM2-EN\tM2-DE",
                             confidence=0.9)

    result = extract_mod.extract(sample_pdf, tmp_path, backend=_Backend())

    assert result.notes is not None
    assert [n.pos for n in result.notes.notes] == [101]

    assert result.marks is not None
    assert [m.pos for m in result.marks.marks] == [201, 202]
    assert result.marks.marks[0].text_en == "Mark-EN"
    assert result.marks.marks[0].text_de == "Mark-DE"


def test_extract_marks_none_when_locator_returns_none(sample_pdf, tmp_path, monkeypatch):
    """When no top-right rectangle is found, result.marks is None and the
    rest of the pipeline runs unchanged."""
    import app.pipeline.extract as extract_mod
    import app.pipeline.boxes as boxes_mod
    from app.pipeline.ocr.base import OcrResult

    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    monkeypatch.setattr("app.pipeline.notes_block.locate_notes_block",
                        lambda image, backend: None)
    monkeypatch.setattr("app.pipeline.marks_block.locate_marks_block",
                        lambda image: None)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: [])

    class _Backend:
        def detect_regions(self, image): return []
        def read_region(self, image): return OcrResult(text="", confidence=0.0)

    result = extract_mod.extract(sample_pdf, tmp_path, backend=_Backend())
    assert result.notes is None
    assert result.marks is None
    assert result.characteristics == []
```

- [ ] **Step 3: Run the test**

Run: `pytest tests/test_extract_notes_integration.py::test_extract_populates_marks_block_when_top_right_rectangle_present -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_extract_notes_integration.py
git commit -m "test(extract): integration coverage for marks-block path"
```

---

## Task 9: Excel — `_write_marks_sheet`

**Files:**
- Modify: `app/excel.py`
- Test: `tests/test_excel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_excel.py`:

```python
def test_write_workbook_creates_marks_sheet(tmp_path):
    from openpyxl import load_workbook
    from app.models import Characteristic, Mark, MarkBlock
    from app.excel import write_workbook

    rows = [Characteristic(pos=1, char_type="Distance", nominal="10")]
    marks = MarkBlock(region=(0, 0, 100, 100), marks=[
        Mark(pos=101, text_en="EN-A", text_de="DE-A"),
        Mark(pos=102, text_en="EN-B", text_de="DE-B"),
    ])
    out = tmp_path / "out.xlsx"
    write_workbook(rows, out, marks=marks)

    wb = load_workbook(out)
    assert "Marks" in wb.sheetnames
    ws = wb["Marks"]
    # row 1 = headers; row 2+ = marks
    assert ws.cell(1, 1).value == "Pos"
    assert ws.cell(1, 2).value == "English"
    assert ws.cell(1, 3).value == "German"
    assert ws.cell(2, 1).value == "101"
    assert ws.cell(2, 2).value == "EN-A"
    assert ws.cell(2, 3).value == "DE-A"
    assert ws.cell(3, 1).value == "102"


def test_write_workbook_omits_marks_sheet_when_marks_none(tmp_path):
    from openpyxl import load_workbook
    from app.models import Characteristic
    from app.excel import write_workbook

    out = tmp_path / "out.xlsx"
    write_workbook([Characteristic(pos=1)], out)
    wb = load_workbook(out)
    assert "Marks" not in wb.sheetnames


def test_marks_sheet_ordered_before_notes_when_both_present(tmp_path):
    from openpyxl import load_workbook
    from app.models import Characteristic, Mark, MarkBlock, Note, NoteBlock
    from app.excel import write_workbook

    out = tmp_path / "out.xlsx"
    write_workbook(
        [Characteristic(pos=1)], out,
        notes=NoteBlock(region=(0, 0, 1, 1), notes=[Note(pos=101)]),
        marks=MarkBlock(region=(0, 0, 1, 1), marks=[Mark(pos=101)]),
    )
    wb = load_workbook(out)
    assert wb.sheetnames == ["Inspection", "Marks", "Notes"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_excel.py -v -k "marks"`
Expected: FAIL with `TypeError: write_workbook() got an unexpected keyword argument 'marks'` (and friends).

- [ ] **Step 3: Add the marks-sheet writer**

Edit `app/excel.py`. Update the imports line to:

```python
from app.models import Characteristic, NoteBlock, MarkBlock
```

Add this function immediately after `_write_notes_sheet`:

```python
def _write_marks_sheet(ws, block: MarkBlock) -> None:
    headers = ["Pos", "English", "German"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(1, col, h)
        cell.font = Font(bold=True)
        cell.alignment = _center
        cell.border = _border
    for i, m in enumerate(block.marks, start=2):
        ws.cell(i, 1, f"{m.pos}")
        ws.cell(i, 2, m.text_en)
        ws.cell(i, 3, m.text_de)
    for col, w in enumerate([10, 48, 48], start=1):
        ws.column_dimensions[chr(64 + col)].width = w
```

Update `write_workbook` to:

```python
def write_workbook(rows: Iterable[Characteristic], path: Path,
                   notes: Optional[NoteBlock] = None,
                   marks: Optional[MarkBlock] = None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection"
    _write_characteristics_sheet(ws, rows)
    if marks is not None and marks.marks:
        _write_marks_sheet(wb.create_sheet("Marks"), marks)
    if notes is not None and notes.notes:
        _write_notes_sheet(wb.create_sheet("Notes"), notes)
    path = Path(path)
    wb.save(path)
```

- [ ] **Step 4: Run the tests to verify pass**

Run: `pytest tests/test_excel.py -v`
Expected: all PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add app/excel.py tests/test_excel.py
git commit -m "feat(excel): Marks sheet between Inspection and Notes"
```

---

## Task 10: API — surface `marks` on upload and accept on export

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_export_request_accepts_marks_field():
    """The export endpoint's request body must accept a `marks` field of
    type Optional[MarkBlock] — symmetric with the existing `notes` field."""
    from app.main import ExportRequest
    from app.models import MarkBlock

    req = ExportRequest(session_id="abc", rows=[], notes=None, marks=None)
    assert req.marks is None

    block = MarkBlock(region=(0, 0, 1, 1), marks=[])
    req2 = ExportRequest(session_id="abc", rows=[], marks=block)
    assert req2.marks == block
```

Also append this test, modelled exactly on the existing
`test_upload_returns_notes_field` at `tests/test_api.py:117`:

```python
def test_upload_returns_marks_field(monkeypatch, sample_pdf):
    """The upload endpoint returns {rows, notes, marks}; marks may be null."""
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import (
        Characteristic, ExtractionResult, MarkBlock, Mark,
    )

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, char_type="Distance", nominal="1,2")],
        marks=MarkBlock(region=(0, 0, 10, 10),
                        marks=[Mark(pos=101, text_en="A", text_de="B"),
                               Mark(pos=102, text_en="C", text_de="D")])
    ))
    client = TestClient(main.app)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert "marks" in data and data["marks"] is not None
    mark_positions = [m["pos"] for m in data["marks"]["marks"]]
    assert mark_positions == [101, 102]


def test_upload_returns_null_marks_when_extract_returns_none(monkeypatch, sample_pdf):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1)], marks=None))
    client = TestClient(main.app)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["marks"] is None
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pytest tests/test_api.py -v -k "marks"`
Expected: FAIL — `ValidationError: extra fields not permitted` or
`pydantic` complains that `marks` isn't a known field on `ExportRequest`.

- [ ] **Step 3: Update the API**

Edit `app/main.py`. Update the import line currently reading
`from app.models import Characteristic, NoteBlock` to:

```python
from app.models import Characteristic, NoteBlock, MarkBlock
```

Update `ExportRequest`:

```python
class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]
    notes: Optional[NoteBlock] = None
    marks: Optional[MarkBlock] = None
```

Update the upload-response JSON in the `/api/upload` handler to include
`marks`:

```python
    return JSONResponse({
        "session_id": session_id,
        "image_url": f"/api/image/{session_id}",
        "rows": [r.model_dump() for r in result.characteristics],
        "notes": result.notes.model_dump() if result.notes is not None else None,
        "marks": result.marks.model_dump() if result.marks is not None else None,
    })
```

Update the export handler call:

```python
    write_workbook(req.rows, out, notes=req.notes, marks=req.marks)
```

- [ ] **Step 4: Run the test to verify pass**

Run: `pytest tests/test_api.py -v`
Expected: all PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat(api): expose marks on upload + accept on export"
```

---

## Task 11: UI — HTML & CSS for the Marks section

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/styles/components.css`

This task has no automated test — UI rendering is verified by a manual
smoke check after Task 12 wires the JS.

- [ ] **Step 1: Add the Marks section ABOVE the existing Notes section**

In `app/static/index.html`, find the block that starts with:

```html
        <section id="notes-section" hidden>
```

(currently around line 192). Insert directly **before** that line:

```html
        <section id="marks-section" hidden>
          <div class="notes-header" id="marks-toggle">
            <svg class="caret" width="11" height="11"><use href="#i-chev-down"/></svg>
            Marks
            <span class="count" id="marks-count">0</span>
          </div>
          <table class="notes">
            <thead>
              <tr><th style="width:60px">Pos</th><th>English</th><th>German</th></tr>
            </thead>
            <tbody id="marks-body"></tbody>
          </table>
        </section>

```

Note: we reuse the `.notes-header` and `.notes` CSS classes so the new
section matches the visual style without adding new CSS. If a future change
needs distinct styling, swap to `.marks` here and add a rule mirroring
`.notes` in `components.css`.

- [ ] **Step 2: Verify the HTML parses by loading the file**

Run: `python -c "from pathlib import Path; html = Path('app/static/index.html').read_text(); assert html.count('id=\"marks-section\"') == 1; assert html.find('marks-section') < html.find('notes-section')"`
Expected: no output, exit 0. (Confirms exactly one `marks-section` and that it precedes `notes-section`.)

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html
git commit -m "feat(ui): add Marks section above Notes (HTML only)"
```

---

## Task 12: UI — JS state, rendering, export wiring

**Files:**
- Modify: `app/static/js/state.js`
- Modify: `app/static/js/table.js`
- Modify: `app/static/js/main.js`

- [ ] **Step 1: Add `marks` to client state**

Edit `app/static/js/state.js`. In the `state` object literal (currently
around line 18), add a `marks: null` line directly under `notes: null`:

```javascript
export const state = {
  sessionId: null,
  imageUrl: null,
  imageSize: { w: 0, h: 0 },
  fileName: null,
  rows: [],
  notes: null,
  marks: null,
  selectedId: null,
  hoverId: null,
  filter: 'all',
  search: '',
  ocrBackend: '—',
  ocrOk: null,
};
```

In `setSession`, add the marks assignment directly after the notes line:

```javascript
  state.notes     = payload.notes;
  state.marks     = payload.marks ?? null;
```

In `clearSession`, add the marks reset:

```javascript
  state.notes     = null;
  state.marks     = null;
```

- [ ] **Step 2: Add Marks rendering**

Edit `app/static/js/table.js`. Update the module-level `let` (currently
line 13) to:

```javascript
let body, notesBody, notesSection, notesCount;
let marksBody, marksSection, marksCount;
```

In `initTable()` add the DOM lookups after the existing notes lookups (line
~19) and the toggle binding after `bindNotesToggle()`:

```javascript
  marksBody    = document.getElementById('marks-body');
  marksSection = document.getElementById('marks-section');
  marksCount   = document.getElementById('marks-count');
```

Add a sibling toggle binding immediately under `bindNotesToggle();`:

```javascript
  bindMarksToggle();
```

Add the function near `bindNotesToggle`:

```javascript
function bindMarksToggle() {
  document.getElementById('marks-toggle').addEventListener('click', () => {
    const collapsed = marksSection.dataset.collapsed === 'true';
    marksSection.dataset.collapsed = collapsed ? 'false' : 'true';
  });
}
```

In `renderAll()`, call `renderMarks()` directly after `renderNotes()`:

```javascript
function renderAll() {
  renderRows();
  renderNotes();
  renderMarks();
  renderCounts();
  updateBulkAvailability();
}
```

Add the `renderMarks` function directly under `renderNotes`:

```javascript
function renderMarks() {
  marksBody.innerHTML = '';
  const block = state.marks;
  if (!block || !block.marks || block.marks.length === 0) {
    marksSection.hidden = true;
    return;
  }
  marksSection.hidden = false;
  marksCount.textContent = block.marks.length;
  for (const m of block.marks) {
    const tr = document.createElement('tr');
    if (m.needs_review) {
      tr.classList.add('review');
      tr.title = (m.review_reasons || []).join(', ');
    }
    const posTd = document.createElement('td');
    posTd.className = 'pos';
    posTd.textContent = `${m.pos}`;
    posTd.id = `mark-${m.pos}`;
    tr.appendChild(posTd);
    const en = document.createElement('td'); en.textContent = m.text_en ?? ''; tr.appendChild(en);
    const de = document.createElement('td'); de.textContent = m.text_de ?? ''; tr.appendChild(de);
    marksBody.appendChild(tr);
  }
}
```

- [ ] **Step 3: Include marks in export payloads**

Edit `app/static/js/main.js`. Find both `exportFile` calls (currently
around lines 103 and 114) and add `marks: state.marks` to each payload:

```javascript
      await exportFile('/api/export',
        { session_id: state.sessionId, rows: state.rows, notes: state.notes, marks: state.marks },
        '...');
```

```javascript
      await exportFile('/api/export/pdf',
        { session_id: state.sessionId, rows: state.rows, notes: state.notes, marks: state.marks },
        '...');
```

(Keep the existing filename argument unchanged; only the second positional
argument changes.)

- [ ] **Step 4: Smoke-test in the browser**

Start the app:

```bash
docker compose up --build
```

Open `http://localhost:8000`, upload one of the PDFs in `test_docs/` that
has a top-right Mark/Description table (e.g. `T1025300_B.pdf`). Expected:

- The right pane shows a "Marks" section above "Notes".
- "Marks" count matches the number of rows in the top-right table.
- Clicking the "Marks" header collapses/expands it.
- Clicking the **Excel** export button downloads a `.xlsx` whose sheet
  order is `Inspection | Marks | Notes`.

If any of these fail, **do not** commit — fix the issue first.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/state.js app/static/js/table.js app/static/js/main.js
git commit -m "feat(ui): render Marks section and include in export payloads"
```

---

## Task 13: Documentation touch-up

**Files:**
- Modify: `README.md` (if it documents the notes-block feature)

- [ ] **Step 1: Check whether README mentions notes-block**

Run: `grep -n -i "notes.block\|notes block\|/api/upload\|excel\|sheet" README.md`

If notes-block is documented, mirror that language for marks-block in the
same section. If README does not document notes-block at all, skip this
task (no doc change needed for parity).

- [ ] **Step 2: If a doc update is needed, write it then commit**

```bash
git add README.md
git commit -m "docs: mention marks-block extraction"
```

If no doc update is needed, skip the commit. **This task does not block any
later task.**

---

## Self-review checklist (run before declaring complete)

- [ ] `pytest -q` — full suite green
- [ ] Manual smoke test from Task 12 Step 4 still passes
- [ ] `git log --oneline` shows one commit per logical step (~12 commits)
- [ ] `app/pipeline/notes_block.py` is byte-for-byte unchanged from main
- [ ] No new imports of `notes_block` from `marks_block` (the modules are
  independent — confirm with `grep notes_block app/pipeline/marks_block.py`
  returns no matches)
