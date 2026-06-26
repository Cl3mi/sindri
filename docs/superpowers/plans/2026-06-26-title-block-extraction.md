# Title-Block Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract every value from a drawing's bottom-right title block (Schriftfeld) as bilingual label/value fields, plus stray free-text outside it, and surface them in the review UI and the Excel export.

**Architecture:** A new `app/pipeline/title_block.py` mirrors the existing `notes_block.py` pattern — *locate region → read → parse → flag for review → mask before the main detector*. The title block is read with OpenCV grid-line detection (one VLM call per non-empty cell), because these CAD PDFs have no text layer. Label↔value pairing happens *within* a cell (caption + prominent value co-located). The new `TitleField` list threads through `ExtractionResult` → SSE result → browser state → a new UI section → a new Excel sheet, exactly parallel to notes.

**Tech Stack:** Python, OpenCV (`cv2`), NumPy, Pydantic, PyMuPDF, FastAPI, openpyxl, Qwen2.5-VL (local), vanilla-JS frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-title-block-extraction-design.md`

**Branch:** `feature/title-block-extraction` (already checked out).

**Test note:** The VLM needs a GPU and is never invoked in tests. All pipeline tests use a **fake backend** (the established pattern in `tests/conftest.py::StubVLMBackend` and `tests/test_notes_block.py`). CV geometry is tested against **synthetic grid images** drawn with PIL, so tests are hermetic (no dependence on a real PDF render).

---

### Task 1: `TitleField` model + `ExtractionResult.title_block`

**Files:**
- Modify: `app/models.py:42-44`
- Test: `tests/test_title_block.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_title_block.py`:

```python
from app.models import TitleField, ExtractionResult


def test_title_field_defaults():
    f = TitleField(label="Sheet / Blatt", value="1/1")
    assert f.label == "Sheet / Blatt"
    assert f.value == "1/1"
    assert f.label_en == "" and f.label_de == ""
    assert f.confidence == 0.0
    assert f.needs_review is False
    assert f.review_reasons == []
    assert f.box is None


def test_extraction_result_title_block_defaults_empty():
    r = ExtractionResult(characteristics=[])
    assert r.title_block == []
    # round-trips through model_dump (used by the SSE payload)
    assert r.model_dump()["title_block"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -v`
Expected: FAIL with `ImportError: cannot import name 'TitleField'`.

- [ ] **Step 3: Add the model**

In `app/models.py`, add after the `NoteBlock` class (after line 39) and before `ExtractionResult`:

```python
class TitleField(BaseModel):
    label: str = ""          # caption as printed, e.g. "Sheet / Blatt"
    label_en: str = ""
    label_de: str = ""
    value: str = ""
    box: Optional[Tuple[float, float, float, float]] = None
    confidence: float = 0.0
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty value", "missing caption"]
```

Then change `ExtractionResult` (lines 42-44) to:

```python
class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
    title_block: List[TitleField] = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_title_block.py
git commit -m "feat(models): add TitleField and ExtractionResult.title_block"
```

---

### Task 2: Pure parsing helpers — `parse_title_cell`, `split_label`, `review_flags_field`

**Files:**
- Create: `app/pipeline/title_block.py`
- Test: `tests/test_title_block.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_title_block.py`:

```python
from app.pipeline.title_block import (
    parse_title_cell, split_label, review_flags_field,
)


def test_parse_title_cell_json():
    assert parse_title_cell('{"label": "Sheet / Blatt", "value": "1/1"}') \
        == ("Sheet / Blatt", "1/1")


def test_parse_title_cell_strips_code_fence():
    raw = '```json\n{"label": "Scale / Maßstab", "value": "5:1"}\n```'
    assert parse_title_cell(raw) == ("Scale / Maßstab", "5:1")


def test_parse_title_cell_empty_label_for_value_only():
    assert parse_title_cell('{"label": "", "value": "1025206"}') == ("", "1025206")


def test_parse_title_cell_colon_fallback():
    assert parse_title_cell("Format / Size: A2") == ("Format / Size", "A2")


def test_parse_title_cell_blank_returns_empty_pair():
    assert parse_title_cell("") == ("", "")
    assert parse_title_cell("   ") == ("", "")


def test_split_label_bilingual():
    assert split_label("Released / Freigabe") == ("Released", "Freigabe")


def test_split_label_no_separator():
    assert split_label("Maßstab") == ("Maßstab", "")


def test_review_flags_empty_value():
    flagged, reasons = review_flags_field(value="", label="Sheet / Blatt")
    assert flagged is True and reasons == ["empty value"]


def test_review_flags_missing_caption_when_expected():
    flagged, reasons = review_flags_field(value="A2", label="")
    assert flagged is True and reasons == ["missing caption"]


def test_review_flags_loose_text_not_flagged():
    # loose text intentionally has no caption -> not a problem
    flagged, reasons = review_flags_field(value="SOME NOTE", label="",
                                          expect_caption=False)
    assert flagged is False and reasons == []


def test_review_flags_clean_field():
    flagged, reasons = review_flags_field(value="1/1", label="Sheet / Blatt")
    assert flagged is False and reasons == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "parse_title_cell or split_label or review_flags" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.title_block'`.

- [ ] **Step 3: Create the module with the pure helpers**

Create `app/pipeline/title_block.py`:

```python
"""Title-block path: locate the bottom-right Schriftfeld, detect its grid
cells with OpenCV, read each non-empty cell as a {label, value} pair, mask the
region before the main detector runs. Mirrors notes_block.py. Never raises from
the public entry points; any failure yields an empty title block."""
import json
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import cv2
from PIL import Image, ImageDraw

from app.models import TitleField


_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def parse_title_cell(raw: str) -> Tuple[str, str]:
    """Return (label, value) from the backend's per-cell read. Accepts a JSON
    object {"label":..,"value":..} (optionally wrapped in code fences), then a
    'label: value' fallback, else ("", raw)."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    cleaned = _FENCE_RE.sub("", raw).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return str(obj.get("label", "")).strip(), str(obj.get("value", "")).strip()
    except (ValueError, TypeError):
        pass
    if ":" in cleaned:
        label, _, value = cleaned.partition(":")
        return label.strip(), value.strip()
    return "", cleaned


def split_label(label: str) -> Tuple[str, str]:
    """Split a bilingual caption 'English / German' into (en, de)."""
    if "/" in label:
        en, _, de = label.partition("/")
        return en.strip(), de.strip()
    return label.strip(), ""


def review_flags_field(value: str, label: str,
                       expect_caption: bool = True) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons). An empty value is always a reason. A
    present value with no caption is flagged only for grid cells
    (expect_caption=True); loose text legitimately has no caption."""
    reasons: List[str] = []
    if not value.strip():
        reasons.append("empty value")
    elif expect_caption and not label.strip():
        reasons.append("missing caption")
    return bool(reasons), reasons
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "parse_title_cell or split_label or review_flags" -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/title_block.py tests/test_title_block.py
git commit -m "feat(title-block): per-cell parse, label split, review flags"
```

---

### Task 3: CV cell detection — `detect_cells` and `_cell_has_ink`

**Files:**
- Modify: `app/pipeline/title_block.py`
- Test: `tests/test_title_block.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_title_block.py`:

```python
from PIL import ImageDraw as _ImageDraw
from app.pipeline.title_block import detect_cells, _cell_has_ink


def _grid_image():
    """A 2x2 ruled grid (400x200) with text only in the top-left cell."""
    img = Image.new("RGB", (400, 200), "white")
    d = _ImageDraw.Draw(img)
    # outer frame + one vertical + one horizontal divider, thick black lines
    d.rectangle((10, 10, 390, 190), outline="black", width=3)
    d.line((200, 10, 200, 190), fill="black", width=3)
    d.line((10, 100, 390, 100), fill="black", width=3)
    d.text((40, 40), "HELLO", fill="black")          # ink in top-left cell only
    return img


def test_detect_cells_finds_four_cells():
    cells = detect_cells(_grid_image(), (0, 0, 400, 200))
    # 2x2 grid -> 4 interior cells (give or take border slivers, so >= 4)
    assert len(cells) >= 4
    # every cell lies inside the image bounds
    for x0, y0, x1, y1 in cells:
        assert 0 <= x0 < x1 <= 400 and 0 <= y0 < y1 <= 200


def test_detect_cells_reading_order_top_to_bottom_left_to_right():
    cells = detect_cells(_grid_image(), (0, 0, 400, 200))
    # first cell is in the top band and left of the last cell's column
    assert cells[0][1] <= cells[-1][1]


def test_detect_cells_empty_region_returns_empty():
    assert detect_cells(Image.new("RGB", (400, 200), "white"), (0, 0, 5, 5)) == []


def test_cell_has_ink_true_for_text_cell_false_for_blank():
    img = _grid_image()
    assert _cell_has_ink(img, (15, 15, 195, 95)) is True     # top-left has HELLO
    assert _cell_has_ink(img, (205, 105, 385, 185)) is False  # bottom-right blank
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "detect_cells or cell_has_ink" -v`
Expected: FAIL with `ImportError: cannot import name 'detect_cells'`.

- [ ] **Step 3: Implement cell detection**

Append to `app/pipeline/title_block.py`:

```python
_MIN_CELL_W = 40          # px: ignore slivers and line artifacts
_MIN_CELL_H = 18
_INK_MIN = 0.004          # fraction of dark pixels for a cell to count as text
_BAND_TOL = 30            # px: rows within this y-band sort left-to-right


def _cell_has_ink(image: Image.Image, box: Tuple[int, int, int, int]) -> bool:
    """True if the cell interior contains a meaningful amount of dark pixels."""
    crop = np.asarray(image.convert("L").crop(box))
    if crop.size == 0:
        return False
    return float((crop < 128).mean()) >= _INK_MIN


def detect_cells(image: Image.Image,
                 region_box: Tuple[float, float, float, float]
                 ) -> List[Tuple[int, int, int, int]]:
    """Detect ruled grid cells inside `region_box`. Returns cell boxes in
    absolute page coordinates, sorted top-to-bottom then left-to-right. Empty
    list on any too-small/blank region."""
    x0, y0, x1, y1 = (int(v) for v in region_box)
    crop = np.asarray(image.convert("L").crop((x0, y0, x1, y1)))
    if crop.size == 0 or crop.shape[0] < 40 or crop.shape[1] < 40:
        return []
    bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = bw.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 25), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 25)))
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    grid = cv2.add(hor, ver)
    inv = cv2.bitwise_not(grid)
    _, _, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    cells: List[Tuple[int, int, int, int]] = []
    for cx, cy, cw, ch, _area in stats[1:]:
        if cw < _MIN_CELL_W or ch < _MIN_CELL_H:
            continue
        if cw > w * 0.95 and ch > h * 0.95:   # the whole-region background blob
            continue
        cells.append((x0 + int(cx), y0 + int(cy),
                      x0 + int(cx + cw), y0 + int(cy + ch)))
    cells.sort(key=lambda b: (round(b[1] / _BAND_TOL), b[0]))
    return cells
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "detect_cells or cell_has_ink" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/title_block.py tests/test_title_block.py
git commit -m "feat(title-block): OpenCV grid cell detection"
```

---

### Task 4: Region locator + mask — `TitleBlockRegion`, `locate_title_block`, `mask_region`

**Files:**
- Modify: `app/pipeline/title_block.py`
- Test: `tests/test_title_block.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_title_block.py`:

```python
from app.pipeline.title_block import (
    TitleBlockRegion, locate_title_block, mask_region,
)


def _page_with_bottom_right_grid():
    """A 1000x800 white page with a ruled 2x2 grid in the bottom-right corner
    and text in its top-left cell."""
    img = Image.new("RGB", (1000, 800), "white")
    d = _ImageDraw.Draw(img)
    d.rectangle((600, 560, 980, 760), outline="black", width=3)
    d.line((790, 560, 790, 760), fill="black", width=3)
    d.line((600, 660, 980, 660), fill="black", width=3)
    d.text((630, 590), "A2", fill="black")
    return img


def test_locate_finds_bottom_right_region():
    region = locate_title_block(_page_with_bottom_right_grid())
    assert region is not None
    assert isinstance(region, TitleBlockRegion)
    # outer box sits in the bottom-right of the page
    assert region.outer_box[0] >= 500 and region.outer_box[1] >= 480
    assert len(region.cells) >= 1


def test_locate_returns_none_on_blank_page():
    assert locate_title_block(Image.new("RGB", (1000, 800), "white")) is None


def test_mask_region_fills_white_and_preserves_original():
    img = Image.new("RGB", (100, 100), "black")
    region = TitleBlockRegion(outer_box=(20, 30, 60, 70), cells=[])
    out = mask_region(img, region)
    assert out.getpixel((30, 40)) == (255, 255, 255)   # inside masked
    assert out.getpixel((10, 10)) == (0, 0, 0)          # outside untouched
    assert img.getpixel((30, 40)) == (0, 0, 0)          # copy semantics
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "locate or mask_region" -v`
Expected: FAIL with `ImportError: cannot import name 'TitleBlockRegion'`.

- [ ] **Step 3: Implement locator and mask**

Append to `app/pipeline/title_block.py`:

```python
@dataclass
class TitleBlockRegion:
    outer_box: Tuple[int, int, int, int]
    cells: List[Tuple[int, int, int, int]]   # ink-bearing cells, reading order


def locate_title_block(image: Image.Image) -> Optional[TitleBlockRegion]:
    """Find the title block in the bottom-right quadrant. Returns None (non-fatal)
    if no ink-bearing grid cells are found or anything goes wrong."""
    try:
        w, h = image.size
        quad = (int(w * 0.5), int(h * 0.6), w, h)
        cells = [c for c in detect_cells(image, quad) if _cell_has_ink(image, c)]
        if not cells:
            return None
        outer = (min(c[0] for c in cells), min(c[1] for c in cells),
                 max(c[2] for c in cells), max(c[3] for c in cells))
        return TitleBlockRegion(outer_box=outer, cells=cells)
    except Exception as e:
        print(f"[sindri.title_block] locator failed: {e!r}",
              file=sys.stderr, flush=True)
        return None


def mask_region(image: Image.Image, region: TitleBlockRegion) -> Image.Image:
    """Return a copy of `image` with `region.outer_box` filled white, so the
    main detector cannot misread title-block text as dimension callouts."""
    out = image.copy()
    x0, y0, x1, y1 = region.outer_box
    if x1 > x0 and y1 > y0:
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill="white")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "locate or mask_region" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/title_block.py tests/test_title_block.py
git commit -m "feat(title-block): region locator and pre-detect mask"
```

---

### Task 5: Per-cell reader — `read_title_block`

**Files:**
- Modify: `app/pipeline/title_block.py`
- Test: `tests/test_title_block.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_title_block.py`:

```python
from app.pipeline.ocr.base import OcrResult
from app.pipeline.title_block import read_title_block


class _StubTitleBackend:
    """Returns a canned per-cell JSON read for read_title_cell."""
    def __init__(self, by_call):
        self._by_call = list(by_call)
        self._i = 0

    def read_title_cell(self, image):
        text = self._by_call[self._i] if self._i < len(self._by_call) else ""
        self._i += 1
        return OcrResult(text=text, confidence=0.9 if text else 0.0)


def test_read_title_block_builds_fields_with_split_labels():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    # force a single known ink cell so the read is deterministic
    region.cells = [region.cells[0]]
    backend = _StubTitleBackend(['{"label": "Size / Format", "value": "A2"}'])
    fields = read_title_block(img, region, backend)
    assert len(fields) == 1
    f = fields[0]
    assert f.label == "Size / Format"
    assert f.label_en == "Size" and f.label_de == "Format"
    assert f.value == "A2"
    assert f.box is not None
    assert f.needs_review is False


def test_read_title_block_flags_empty_value():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    region.cells = [region.cells[0]]
    backend = _StubTitleBackend(['{"label": "Scale / Maßstab", "value": ""}'])
    fields = read_title_block(img, region, backend)
    assert fields[0].needs_review is True
    assert fields[0].review_reasons == ["empty value"]


def test_read_title_block_skips_fully_empty_reads():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    region.cells = [region.cells[0]]
    backend = _StubTitleBackend([''])     # no label, no value -> dropped
    assert read_title_block(img, region, backend) == []


def test_read_title_block_survives_backend_error():
    img = _page_with_bottom_right_grid()
    region = locate_title_block(img)
    region.cells = [region.cells[0]]

    class Boom:
        def read_title_cell(self, image):
            raise RuntimeError("kaboom")

    assert read_title_block(img, region, Boom()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "read_title_block" -v`
Expected: FAIL with `ImportError: cannot import name 'read_title_block'`.

- [ ] **Step 3: Implement the reader**

Append to `app/pipeline/title_block.py`:

```python
def read_title_block(image: Image.Image, region: TitleBlockRegion,
                     backend) -> List[TitleField]:
    """Read each ink-bearing cell as a {label, value} pair. Prefers a backend
    `read_title_cell` method (dedicated prompt); falls back to `read_region`.
    Per-cell failures are skipped, never fatal."""
    fields: List[TitleField] = []
    for box in region.cells:
        crop = image.crop(box)
        try:
            if hasattr(backend, "read_title_cell"):
                res = backend.read_title_cell(crop)
            else:
                res = backend.read_region(crop)
            raw, conf = res.text, res.confidence
        except Exception as e:
            print(f"[sindri.title_block] cell read failed: {e!r}",
                  file=sys.stderr, flush=True)
            raw, conf = "", 0.0
        label, value = parse_title_cell(raw)
        if not label and not value:
            continue
        en, de = split_label(label)
        flagged, reasons = review_flags_field(value, label, expect_caption=True)
        fields.append(TitleField(
            label=label, label_en=en, label_de=de, value=value,
            box=tuple(float(v) for v in box), confidence=conf,
            needs_review=flagged, review_reasons=reasons,
        ))
    return fields
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "read_title_block" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/title_block.py tests/test_title_block.py
git commit -m "feat(title-block): per-cell VLM reader"
```

---

### Task 6: VLM backend `read_title_cell` prompt + method

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py:60` (after `_NOTES_PROMPT`) and after the `read_notes_block` method (line 129)
- Test: `tests/test_vlm_prompt.py`

- [ ] **Step 1: Write the failing test**

First inspect the existing assertions: `cat tests/test_vlm_prompt.py`. They check prompt-constant content without loading the model. Append a matching test to `tests/test_vlm_prompt.py`:

```python
from app.pipeline.ocr import vlm_backend


def test_title_prompt_requests_json_label_value():
    p = vlm_backend._TITLE_PROMPT
    assert "title block" in p.lower()
    assert '"label"' in p and '"value"' in p
    # caption can be above OR below the value (the two-layout requirement)
    assert "above" in p.lower() and "below" in p.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vlm_prompt.py -k title -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_TITLE_PROMPT'`.

- [ ] **Step 3: Add prompt constant and method**

In `app/pipeline/ocr/vlm_backend.py`, add after `_NOTES_PROMPT` (after line 60):

```python
# Title-block cell read prompt: the crop is ONE cell of the bottom-right title
# block (Schriftfeld). Each cell holds a small bilingual caption ("English /
# German") and a prominent value; the caption may sit above OR below the value.
# Returns a JSON object so the parser can split caption from value in one pass.
_TITLE_PROMPT = (
    "This image is a single cell cropped from the title block (Schriftfeld) of "
    "a mechanical engineering drawing. The cell contains a small printed caption "
    "(a bilingual label in the form 'English / German', e.g. 'Sheet / Blatt' or "
    "'Released / Freigabe') together with a prominent value. The caption may "
    "appear ABOVE or BELOW the value. Return ONLY a JSON object "
    "{\"label\": \"<caption as printed>\", \"value\": \"<the value>\"}. If the "
    "cell has only a value and no caption, use an empty label. Use a comma as the "
    "decimal separator. No prose, no explanation, no code fences."
)
```

Then add this method to the `VLMBackend` class, after `read_notes_block` (after line 129):

```python
    def read_title_cell(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _TITLE_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=128, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.9 if text else 0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_vlm_prompt.py -k title -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py tests/test_vlm_prompt.py
git commit -m "feat(vlm): read_title_cell prompt and method"
```

---

### Task 7: Loose-text catch — `loose_text`

**Files:**
- Modify: `app/pipeline/title_block.py`
- Test: `tests/test_title_block.py`

This is the only piece that reads text *outside* the title block (e.g. a left-margin
note). It reuses the existing tiled detector: keep `note`-kind detections that fall
outside the supplied exclude boxes, read each, emit as a label-less `TitleField`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_title_block.py`:

```python
from app.pipeline.detect import Detection
from app.pipeline.title_block import loose_text


class _LooseBackend:
    """Detects the same note boxes for every tile, reads canned text."""
    def __init__(self, dets, text):
        self._dets = dets
        self._text = text

    def detect_regions(self, image):
        return list(self._dets)

    def read_region(self, image):
        return OcrResult(text=self._text, confidence=0.8)


def test_loose_text_emits_label_less_field_outside_excludes(monkeypatch):
    # one note detection at tile-local (10,10,120,40); single tile at origin
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, 400, 400)])
    backend = _LooseBackend([Detection(box=(10, 10, 120, 40), kind="note", conf=0.9)],
                            text="NACH WAHL DES HERSTELLERS")
    fields = loose_text(Image.new("RGB", (400, 400), "white"), backend,
                        exclude_boxes=[(300, 300, 400, 400)])
    assert len(fields) == 1
    assert fields[0].label == "" and fields[0].value == "NACH WAHL DES HERSTELLERS"
    assert fields[0].needs_review is False     # loose text not flagged


def test_loose_text_drops_detections_inside_exclude(monkeypatch):
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, 400, 400)])
    backend = _LooseBackend([Detection(box=(310, 310, 360, 340), kind="note", conf=0.9)],
                            text="INSIDE TITLE BLOCK")
    fields = loose_text(Image.new("RGB", (400, 400), "white"), backend,
                        exclude_boxes=[(300, 300, 400, 400)])
    assert fields == []


def test_loose_text_survives_detector_error(monkeypatch):
    monkeypatch.setattr("app.pipeline.title_block.tile_grid",
                        lambda w, h: [(0, 0, 400, 400)])

    class Boom:
        def detect_regions(self, image):
            raise RuntimeError("kaboom")

    assert loose_text(Image.new("RGB", (400, 400), "white"), Boom(),
                      exclude_boxes=[]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "loose_text" -v`
Expected: FAIL with `ImportError: cannot import name 'loose_text'`.

- [ ] **Step 3: Implement loose-text catch**

Add this import near the top of `app/pipeline/title_block.py` (with the other `app.pipeline` imports — keep it at module level so the test's `monkeypatch.setattr("app.pipeline.title_block.tile_grid", ...)` works):

```python
from app.pipeline.detect import tile_grid
```

Append the function and a geometry helper:

```python
def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def loose_text(image: Image.Image, backend,
               exclude_boxes: List[Tuple[float, float, float, float]]
               ) -> List[TitleField]:
    """Catch free text outside the structured blocks: detect `note`-kind regions
    across the page, drop any overlapping an exclude box (title/notes blocks),
    read the rest and emit label-less TitleFields. Never fatal."""
    out: List[TitleField] = []
    try:
        w, h = image.size
        boxes: List[Tuple[int, int, int, int]] = []
        for (tx0, ty0, tx1, ty1) in tile_grid(w, h):
            try:
                dets = backend.detect_regions(image.crop((tx0, ty0, tx1, ty1)))
            except Exception:
                continue
            for d in dets:
                if d.kind != "note":
                    continue
                box = (d.box[0] + tx0, d.box[1] + ty0,
                       d.box[2] + tx0, d.box[3] + ty0)
                if any(_overlaps(box, ex) for ex in exclude_boxes):
                    continue
                if any(_overlaps(box, seen) for seen in boxes):
                    continue
                boxes.append(box)
        for box in boxes:
            try:
                res = backend.read_region(image.crop(box))
            except Exception:
                continue
            text = (res.text or "").strip()
            if not text:
                continue
            out.append(TitleField(
                label="", value=text,
                box=tuple(float(v) for v in box), confidence=res.confidence,
                needs_review=False, review_reasons=[],
            ))
    except Exception as e:
        print(f"[sindri.title_block] loose_text failed: {e!r}",
              file=sys.stderr, flush=True)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "loose_text" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/title_block.py tests/test_title_block.py
git commit -m "feat(title-block): loose-text catch outside structured blocks"
```

---

### Task 8: Wire into the extraction pipeline

**Files:**
- Modify: `app/pipeline/extract.py:13` (import), `:93-95` (mask), `:142` (return), plus a new `title` emit
- Test: `tests/test_title_block.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_title_block.py`:

```python
import uuid
from app.pipeline.extract import extract


def test_extract_attaches_title_block(tmp_path, monkeypatch):
    """A fake backend + stubbed locate yields a title_block on the result and
    masks the region before detection."""
    from app.pipeline import title_block as tb
    from app.models import TitleField as TF

    region = tb.TitleBlockRegion(outer_box=(600, 560, 980, 760),
                                 cells=[(610, 570, 780, 650)])
    monkeypatch.setattr("app.pipeline.extract.tb.locate_title_block",
                        lambda image: region)
    monkeypatch.setattr(
        "app.pipeline.extract.tb.read_title_block",
        lambda image, reg, backend: [TF(label="Size / Format", label_en="Size",
                                        label_de="Format", value="A2")])
    monkeypatch.setattr("app.pipeline.extract.tb.loose_text",
                        lambda image, backend, exclude_boxes: [])
    # notes locator off so it doesn't interfere
    monkeypatch.setattr("app.pipeline.extract.nb.locate_notes_block",
                        lambda image, backend: None)

    from tests.conftest import StubVLMBackend
    backend = StubVLMBackend(detections=[])

    import shutil
    src = Path(__file__).parents[1] / "test_docs" / "T1025206_D.pdf"
    if not src.exists():
        src = Path(__file__).parents[1] / "sample.pdf"
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(src, work / "input.pdf")

    result = extract(work / "input.pdf", work_dir=work, dpi=150, backend=backend)
    assert len(result.title_block) == 1
    assert result.title_block[0].value == "A2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "extract_attaches" -v`
Expected: FAIL — `extract()` returns a result whose `title_block` is `[]` (attribute exists from Task 1, but nothing populates it), so the `len == 1` assertion fails. (If `app.pipeline.extract.tb` does not exist yet, it fails on the monkeypatch path instead — both are the expected pre-implementation failure.)

- [ ] **Step 3: Wire the pipeline**

In `app/pipeline/extract.py`, add the import after line 13 (`from app.pipeline import notes_block as nb`):

```python
from app.pipeline import title_block as tb
```

Replace the notes block (lines 80-95) so the title block is located, read, and masked on top of the notes mask. The existing notes block ends by assigning `image_for_detect`; add the title-block handling immediately after it. Replace lines 80-95:

```python
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
```

Then, just before the final `return` (line 142), add the loose-text pass and include `title_block` in the result:

```python
    # Free text outside the structured blocks (e.g. margin notes).
    exclude = [b for b in (tb_region.outer_box if tb_region else None,
                           region.outer_box if region is not None else None)
               if b is not None]
    title_fields += tb.loose_text(image, backend, exclude)
    return ExtractionResult(characteristics=results, notes=notes_obj,
                            title_block=title_fields)
```

(Delete the old `return ExtractionResult(characteristics=results, notes=notes_obj)` line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_title_block.py -k "extract_attaches" -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full pipeline test suite to check nothing regressed**

Run: `.venv/bin/python -m pytest tests/test_title_block.py tests/test_pipeline_integration.py tests/test_extract_notes_integration.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/extract.py tests/test_title_block.py
git commit -m "feat(extract): locate, read, mask title block + loose text"
```

---

### Task 9: Excel "Title Block" sheet

**Files:**
- Modify: `app/excel.py:5` (import), add `_write_title_block_sheet`, extend `write_workbook`
- Test: `tests/test_excel.py`

- [ ] **Step 1: Write the failing test**

First check the existing style: `cat tests/test_excel.py`. Append a test in the same style:

```python
from openpyxl import load_workbook
from app.models import TitleField


def test_workbook_has_title_block_sheet(tmp_path):
    from app.excel import write_workbook
    fields = [
        TitleField(label="Sheet / Blatt", label_en="Sheet", label_de="Blatt",
                   value="1/1"),
        TitleField(label="Scale / Maßstab", label_en="Scale", label_de="Maßstab",
                   value="5:1"),
    ]
    out = tmp_path / "wb.xlsx"
    write_workbook([], out, title_block=fields)
    wb = load_workbook(out)
    assert "Title Block" in wb.sheetnames
    ws = wb["Title Block"]
    assert [c.value for c in ws[1]] == ["Label (EN)", "Label (DE)", "Value"]
    assert ws.cell(2, 1).value == "Sheet" and ws.cell(2, 3).value == "1/1"
    assert ws.cell(3, 2).value == "Maßstab"


def test_workbook_omits_title_block_sheet_when_empty(tmp_path):
    from app.excel import write_workbook
    out = tmp_path / "wb.xlsx"
    write_workbook([], out, title_block=[])
    wb = load_workbook(out)
    assert "Title Block" not in wb.sheetnames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_excel.py -k "title_block" -v`
Expected: FAIL with `TypeError: write_workbook() got an unexpected keyword argument 'title_block'`.

- [ ] **Step 3: Implement the sheet**

In `app/excel.py`, extend the import on line 5:

```python
from app.models import Characteristic, NoteBlock, TitleField
```

Add a writer after `_write_notes_sheet` (after line 59):

```python
def _write_title_block_sheet(ws, fields) -> None:
    headers = ["Label (EN)", "Label (DE)", "Value"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(1, col, h)
        cell.font = Font(bold=True)
        cell.alignment = _center
        cell.border = _border
    for i, f in enumerate(fields, start=2):
        ws.cell(i, 1, f.label_en)
        ws.cell(i, 2, f.label_de)
        ws.cell(i, 3, f.value)
    for col, w in enumerate([22, 22, 40], start=1):
        ws.column_dimensions[chr(64 + col)].width = w
```

Replace `write_workbook` (lines 62-71):

```python
def write_workbook(rows: Iterable[Characteristic], path: Path,
                   notes: Optional[NoteBlock] = None,
                   title_block: Optional[Iterable[TitleField]] = None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection"
    _write_characteristics_sheet(ws, rows)
    if notes is not None and notes.notes:
        _write_notes_sheet(wb.create_sheet("Notes"), notes)
    if title_block:
        _write_title_block_sheet(wb.create_sheet("Title Block"), list(title_block))
    path = Path(path)
    wb.save(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_excel.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add app/excel.py tests/test_excel.py
git commit -m "feat(excel): Title Block sheet"
```

---

### Task 10: API — SSE result payload + `ExportRequest`

**Files:**
- Modify: `app/main.py:18` (import), `:48-51` (`ExportRequest`), `:114-119` (result payload), `:174` (export call)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

First check the existing style: `cat tests/test_api.py`. The export endpoint is exercised there. Append a test that posts a `title_block` to `/api/export` and asserts the sheet exists:

```python
def test_export_includes_title_block_sheet(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from openpyxl import load_workbook
    import uuid

    client = TestClient(main.app)
    session_id = uuid.uuid4().hex
    (main._SESSIONS / session_id).mkdir(parents=True, exist_ok=True)

    payload = {
        "session_id": session_id,
        "rows": [],
        "notes": None,
        "title_block": [
            {"label": "Sheet / Blatt", "label_en": "Sheet", "label_de": "Blatt",
             "value": "1/1"}
        ],
    }
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 200
    out = tmp_path / "got.xlsx"
    out.write_bytes(resp.content)
    wb = load_workbook(out)
    assert "Title Block" in wb.sheetnames
    assert wb["Title Block"].cell(2, 3).value == "1/1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api.py -k "title_block" -v`
Expected: FAIL — `title_block` is dropped by the `ExportRequest` model (extra field ignored), so the "Title Block" sheet is absent and the assertion fails.

- [ ] **Step 3: Wire the API**

In `app/main.py`, extend the model import on line 18:

```python
from app.models import Characteristic, NoteBlock, TitleField
```

Extend `ExportRequest` (lines 48-51):

```python
class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]
    notes: Optional[NoteBlock] = None
    title_block: List[TitleField] = []
```

Add `title_block` to the SSE result payload (lines 114-119), after the `notes` line:

```python
            events.put(("result", {
                "session_id": session_id,
                "image_url": f"/api/image/{session_id}",
                "rows": [r.model_dump() for r in result.characteristics],
                "notes": result.notes.model_dump() if result.notes is not None else None,
                "title_block": [t.model_dump() for t in result.title_block],
            }))
```

Pass it to the export (line 174):

```python
    write_workbook(req.rows, out, notes=req.notes, title_block=req.title_block)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: PASS (all, including the new test).

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat(api): title_block in extract result and export request"
```

---

### Task 11: Frontend state — store `title_block`

**Files:**
- Modify: `app/static/js/state.js:24` (state), `:33-44` (`setSession`), `:46-58` (`clearSession`)

No JS test infra exists in this repo; frontend tasks are verified by running the app (Task 13). Each step here is a precise edit.

- [ ] **Step 1: Add the state field**

In `app/static/js/state.js`, after line 24 (`notes: null,`):

```javascript
  title_block: [],
```

- [ ] **Step 2: Populate in `setSession`**

In `setSession` after line 37 (`state.notes = payload.notes;`):

```javascript
  state.title_block = payload.title_block ?? [];
```

- [ ] **Step 3: Reset in `clearSession`**

In `clearSession` after line 52 (`state.notes = null;`):

```javascript
  state.title_block = [];
```

- [ ] **Step 4: Commit**

```bash
git add app/static/js/state.js
git commit -m "feat(ui-state): hold title_block from extract result"
```

---

### Task 12: Frontend UI — Title Block section + render + export + step

**Files:**
- Modify: `app/static/index.html:237` (add section after the notes section)
- Modify: `app/static/js/table.js` (init refs, toggle, renderAll, new `renderTitleBlock`)
- Modify: `app/static/js/main.js:165` (extract step), `:236` and `:247` (export payloads)

- [ ] **Step 1: Add the HTML section**

In `app/static/index.html`, immediately after the notes `</section>` (line 237) and before the closing `</div>` of `#table-wrap` (line 238), add:

```html
        <section id="title-section" hidden>
          <div class="notes-header" id="title-toggle">
            <svg class="caret" width="11" height="11"><use href="#i-chev-down"/></svg>
            Title block
            <span class="count" id="title-count">0</span>
          </div>
          <table class="notes">
            <thead>
              <tr><th>Label (EN)</th><th>Label (DE)</th><th>Value</th></tr>
            </thead>
            <tbody id="title-body"></tbody>
          </table>
        </section>
```

- [ ] **Step 2: Wire refs + toggle + render in `table.js`**

In `app/static/js/table.js`, extend the module-level refs (line 13):

```javascript
let body, notesBody, notesSection, notesCount;
let titleBody, titleSection, titleCount;
```

In `initTable` after line 19 (`notesCount = document.getElementById('notes-count');`):

```javascript
  titleBody    = document.getElementById('title-body');
  titleSection = document.getElementById('title-section');
  titleCount   = document.getElementById('title-count');
```

In `initTable` after `bindNotesToggle();` (line 30) add:

```javascript
  bindTitleToggle();
```

Add a toggle binder after `bindNotesToggle` (after line 89):

```javascript
function bindTitleToggle() {
  document.getElementById('title-toggle').addEventListener('click', () => {
    const collapsed = titleSection.dataset.collapsed === 'true';
    titleSection.dataset.collapsed = collapsed ? 'false' : 'true';
  });
}
```

In `renderAll` (after line 94 `renderNotes();`):

```javascript
  renderTitleBlock();
```

Add the renderer after `renderNotes` (after line 218):

```javascript
function renderTitleBlock() {
  titleBody.innerHTML = '';
  const fields = state.title_block;
  if (!fields || fields.length === 0) {
    titleSection.hidden = true;
    return;
  }
  titleSection.hidden = false;
  titleCount.textContent = fields.length;
  for (const f of fields) {
    const tr = document.createElement('tr');
    if (f.needs_review) {
      tr.classList.add('review');
      tr.title = (f.review_reasons || []).join(', ');
    }
    const en = document.createElement('td'); en.textContent = f.label_en ?? ''; tr.appendChild(en);
    const de = document.createElement('td'); de.textContent = f.label_de ?? ''; tr.appendChild(de);
    const val = document.createElement('td'); val.textContent = f.value ?? ''; tr.appendChild(val);
    titleBody.appendChild(tr);
  }
}
```

- [ ] **Step 3: Add the extract step + export payloads in `main.js`**

In `app/static/js/main.js`, add the title step to `EXTRACT_STEPS` after the `notes` entry (line 165):

```javascript
  { key: 'title',  label: 'Reading title block' },
```

Update both export payloads (lines 236 and 247) to include `title_block`:

```javascript
        { session_id: state.sessionId, rows: state.rows, notes: state.notes, title_block: state.title_block },
```

(Apply the same change to both the `/api/export` call and the `/api/export/pdf` call.)

- [ ] **Step 4: Verify the frontend loads without console errors**

Run the app and confirm the bundle parses (no automated JS tests exist):

```bash
.venv/bin/python -m uvicorn app.main:app --port 8099 &
sleep 3
curl -fsS http://localhost:8099/js/table.js | head -1
curl -fsS http://localhost:8099/ | grep -q 'title-section' && echo "section present"
kill %1
```

Expected: prints the first line of `table.js` and `section present`.

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html app/static/js/table.js app/static/js/main.js
git commit -m "feat(ui): title-block section, render, export, progress step"
```

---

### Task 13: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (existing + new). If `test_detect_gpu.py` skips without a GPU, that is expected.

- [ ] **Step 2: End-to-end smoke test against a real title-block PDF (GPU only)**

Only if a GPU + VLM model are available (otherwise skip and note it):

```bash
.venv/bin/python -m uvicorn app.main:app --port 8099 &
sleep 5
SID=$(curl -fsS -F file=@test_docs/T1025206_D.pdf http://localhost:8099/api/upload | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
curl -fsS -N -X POST http://localhost:8099/api/extract/$SID | grep -m1 'event: result'
kill %1
```

Expected: a `result` event whose JSON includes a non-empty `title_block` array with recognizable labels (e.g. `Sheet`, `Scale`, `Material`) and values. If no GPU, record "smoke test skipped — no GPU" and rely on the fake-backend integration test from Task 8.

- [ ] **Step 3: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test: title-block extraction end-to-end verification"
```

---

## Self-Review

**Spec coverage:**
- CV grid detection + per-cell VLM read → Tasks 3, 5, 6.
- Within-cell label/value pairing, label above OR below → Task 2 (`parse_title_cell`) + Task 6 prompt.
- Bilingual label split → Task 2 (`split_label`).
- Locate region (bottom-right) → Task 4.
- Mask before detector → Tasks 4 + 8.
- Ink-empty-cell filtering (cost mitigation) → Tasks 3 (`_cell_has_ink`) + 4 (locate filters).
- Review flags (`empty value`, `missing caption`, loose-text not flagged) → Task 2.
- Loose-text catch (all-text-on-page) → Task 7 + wired in Task 8.
- `TitleField` model + `ExtractionResult.title_block` → Task 1.
- Excel "Title Block" sheet (Label EN / Label DE / Value) → Task 9.
- SSE payload + `ExportRequest` → Task 10.
- Browser state + UI section (mirrors notes) + export payload + progress step → Tasks 11, 12.
- Non-fatal failure (locate returns None) → Tasks 4, 5, 7 + Task 8 guards.
- Fake-backend / synthetic-grid testing strategy → Tasks 3–8.

**Placeholder scan:** No TBD/TODO; every code step shows full code; every command shows expected output.

**Type/name consistency:** `TitleField(label,label_en,label_de,value,box,confidence,needs_review,review_reasons)` is used identically across Tasks 1, 5, 7, 9, 10, 12. `TitleBlockRegion(outer_box,cells)` consistent across Tasks 4, 5, 8. `locate_title_block`/`read_title_block`/`mask_region`/`loose_text`/`detect_cells`/`_cell_has_ink`/`parse_title_cell`/`split_label`/`review_flags_field` names match between definition and call sites. `write_workbook(..., title_block=...)` consistent between Tasks 9 and 10. The pipeline module alias `tb` (Task 8) matches the monkeypatch targets in the Task 8 test.
