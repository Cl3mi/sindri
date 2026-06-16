# Drawing-Balloon → Excel Extractor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, single-container web app that extracts numbered-balloon dimensions from an Intercable-template technical-drawing PDF and exports a reviewable inspection-sheet `.xlsx`.

**Architecture:** A FastAPI app serves a static review UI and runs a Python extraction pipeline (PyMuPDF for text/vector/render, a pluggable OCR backend for values, deterministic leader-line tracing for balloon→dimension association, openpyxl for export). Everything runs in one container; an optional GPU VLM OCR backend is opt-in.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, PyMuPDF (`fitz`), Tesseract (`pytesseract`), OpenCV + Pillow, openpyxl, pytest. Optional: a local vision-LLM via `transformers`/torch (GPU).

---

## File Structure

```
sindri/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app: routes + static serving
│   ├── models.py                    # Characteristic model
│   ├── excel.py                     # openpyxl writer
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── render.py                # PDF page → PNG + page→image transform
│   │   ├── anchors.py               # balloon-number text spans → anchors
│   │   ├── vectors.py               # leader lines + balloon circles
│   │   ├── tracer.py                # association: balloon → target region
│   │   ├── parser.py                # raw OCR text → Characteristic fields
│   │   ├── notes.py                 # notes-table (101–104) region extraction
│   │   ├── extract.py               # pipeline orchestration
│   │   └── ocr/
│   │       ├── __init__.py          # backend factory + selection/fallback
│   │       ├── base.py              # OCRBackend interface + OcrResult
│   │       ├── tesseract_backend.py # CPU default
│   │       └── vlm_backend.py       # optional GPU VLM
│   └── static/
│       ├── index.html
│       └── app.js
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── fixtures/sample.pdf          # copied from repo root
│   ├── test_models.py
│   ├── test_parser.py
│   ├── test_excel.py
│   ├── test_render.py
│   ├── test_anchors.py
│   ├── test_vectors.py
│   ├── test_tracer.py
│   ├── test_ocr_tesseract.py
│   └── test_pipeline_integration.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker-compose.gpu.yml
└── README.md
```

**Known facts about `sample.pdf` (used as test oracle):**
- Text layer contains exactly the balloon numbers **1–22** (verified via `pdftotext`).
- Note callout numbers **101–104** are vector art (not text) in the top-right notes table.
- Leader lines + balloon circles are vector paths (verified via SVG render: 6156 paths).
- Dimension values are vector line-art → require OCR.
- Balloons 1–8 expected values (from `excel_output.png`):

| Pos | char_type | nominal | upper_tol | lower_tol |
|-----|-----------|---------|-----------|-----------|
| 1 | Distance | 1,2 | 0,1 | -0,1 |
| 2 | Distance | 3,2 | 0,05 | -0,05 |
| 3 | Distance | 7,2 | 0,1 | -0,1 |
| 4 | Diameter | 7 | 0,1 | -0,1 |
| 5 | Diameter | 12 | 0,05 | -0,05 |
| 6 | Radius | 0,5 | 0 | |
| 7 | Radius | 0,5 | 0 | |
| 8 | Flatness | 0 | 0,1 | |

---

## Task 1: Project scaffold + test harness

**Files:**
- Create: `requirements.txt`, `app/__init__.py`, `app/pipeline/__init__.py`, `app/pipeline/ocr/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `pytest.ini`
- Create: `tests/fixtures/sample.pdf` (copy of repo-root `sample.pdf`)

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi==0.115.*
uvicorn[standard]==0.32.*
pymupdf==1.24.*
pytesseract==0.3.*
pillow==11.*
opencv-python-headless==4.10.*
openpyxl==3.1.*
python-multipart==0.0.*
pytest==8.*
```

- [ ] **Step 2: Create empty package files and `pytest.ini`**

`pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

`app/__init__.py`, `app/pipeline/__init__.py`, `app/pipeline/ocr/__init__.py`, `tests/__init__.py`: empty files.

- [ ] **Step 3: Create `tests/conftest.py`**

```python
from pathlib import Path
import shutil
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture(scope="session", autouse=True)
def ensure_sample_pdf():
    FIXTURES.mkdir(exist_ok=True)
    target = FIXTURES / "sample.pdf"
    if not target.exists():
        root_pdf = Path(__file__).parents[1] / "sample.pdf"
        shutil.copy(root_pdf, target)

@pytest.fixture
def sample_pdf():
    return FIXTURES / "sample.pdf"
```

- [ ] **Step 4: Create virtualenv and install**

Run: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
Expected: installs succeed. (Tesseract binary is provided by Docker; local OCR tests are skipped if absent — see Task 9.)

- [ ] **Step 5: Verify pytest runs**

Run: `. .venv/bin/activate && pytest -q`
Expected: "no tests ran" (exit 5) or 0 collected — harness works.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini app tests
git commit -m "chore: project scaffold and test harness"
```

---

## Task 2: Characteristic data model

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
from app.models import Characteristic

def test_characteristic_defaults():
    c = Characteristic(pos=5)
    assert c.pos == 5
    assert c.char_type == ""
    assert c.nominal == ""
    assert c.upper_tol == ""
    assert c.lower_tol == ""
    assert c.confidence == 0.0

def test_characteristic_roundtrip_dict():
    c = Characteristic(pos=1, char_type="Distance", nominal="1,2",
                       upper_tol="0,1", lower_tol="-0,1", confidence=0.9)
    d = c.model_dump()
    assert d["pos"] == 1 and d["nominal"] == "1,2"
    assert Characteristic(**d) == c
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: app.models`

- [ ] **Step 3: Write minimal implementation**

```python
# app/models.py
from typing import Optional, Tuple
from pydantic import BaseModel

class Characteristic(BaseModel):
    pos: int
    char_type: str = ""          # Distance|Diameter|Radius|Flatness|Material|Note
    nominal: str = ""
    upper_tol: str = ""
    lower_tol: str = ""
    raw_text: str = ""
    confidence: float = 0.0
    balloon_xy: Optional[Tuple[float, float]] = None        # image-space
    target_region: Optional[Tuple[float, float, float, float]] = None  # x0,y0,x1,y1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: Characteristic data model"
```

---

## Task 3: Value parser + classifier

This is the highest-value pure unit. It converts a raw OCR string for one balloon into structured fields. European comma decimals are preserved (matches the Excel oracle).

**Files:**
- Create: `app/pipeline/parser.py`
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write the failing tests**

```python
from app.pipeline.parser import parse_value, DIAMETER, RADIUS, FLATNESS, DISTANCE, MATERIAL

def test_distance_stacked_tolerance():
    c = parse_value("1,2 +0,1 -0,1")
    assert c.char_type == DISTANCE
    assert c.nominal == "1,2"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_distance_multiline():
    c = parse_value("3,2\n+0,05\n-0,05")
    assert c.char_type == DISTANCE
    assert c.nominal == "3,2"
    assert c.upper_tol == "0,05"
    assert c.lower_tol == "-0,05"

def test_diameter_symbol():
    c = parse_value("Ø7 +0,1 -0,1")
    assert c.char_type == DIAMETER
    assert c.nominal == "7"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_diameter_misread_O_prefix():
    # Tesseract often reads Ø as O or 0 before a number
    c = parse_value("O12 +0,05 -0,05")
    assert c.char_type == DIAMETER
    assert c.nominal == "12"

def test_radius_max():
    c = parse_value("R0,5 MAX")
    assert c.char_type == RADIUS
    assert c.nominal == "0,5"
    assert c.upper_tol == "0"
    assert c.lower_tol == ""

def test_flatness_symbol():
    # flatness GD&T frame OCRs roughly as "0,1" alongside a parallelogram glyph
    c = parse_value("0,1", hint="flatness")
    assert c.char_type == FLATNESS
    assert c.nominal == "0"
    assert c.upper_tol == "0,1"

def test_symmetric_tolerance():
    c = parse_value("5 ±0,1")
    assert c.nominal == "5"
    assert c.upper_tol == "0,1"
    assert c.lower_tol == "-0,1"

def test_material_text():
    c = parse_value("Cu-ETP_R240", hint="material")
    assert c.char_type == MATERIAL
    assert c.nominal == "Cu-ETP_R240"
    assert c.upper_tol == "" and c.lower_tol == ""

def test_plain_distance_no_tol():
    c = parse_value("7,2")
    assert c.char_type == DISTANCE
    assert c.nominal == "7,2"
    assert c.upper_tol == "" and c.lower_tol == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.parser`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/parser.py
import re
from app.models import Characteristic

DIAMETER = "Diameter"
RADIUS = "Radius"
FLATNESS = "Flatness"
DISTANCE = "Distance"
MATERIAL = "Material"
NOTE = "Note"

# A signed European decimal, e.g. 0,1  -0,05  12  +0,1
_NUM = r"[+\-±]?\d+(?:,\d+)?"
_NUM_RE = re.compile(_NUM)


def _clean(s: str) -> str:
    return s.replace("\n", " ").strip()


def _strip_sign(tok: str) -> str:
    return tok.lstrip("+")


def parse_value(raw: str, hint: str = "") -> Characteristic:
    text = _clean(raw)
    c = Characteristic(pos=0, raw_text=raw)

    # --- non-numeric / text-class hints first ---
    if hint == "material":
        c.char_type = MATERIAL
        c.nominal = text
        return c
    if hint == "note":
        c.char_type = NOTE
        c.nominal = text
        return c

    # --- classify by leading symbol ---
    upper = text.upper()
    is_diameter = text.startswith("Ø") or bool(re.match(r"^[O0]\s*\d", text))
    is_radius = bool(re.match(r"^R\s*\d", upper))

    # strip the class prefix so number parsing is clean
    body = text
    if text.startswith("Ø"):
        body = text[1:]
    elif is_diameter:
        body = re.sub(r"^[O0]\s*", "", text, count=1)
    elif is_radius:
        body = re.sub(r"^R\s*", "", text, count=1, flags=re.IGNORECASE)

    if hint == "flatness":
        c.char_type = FLATNESS
    elif is_diameter:
        c.char_type = DIAMETER
    elif is_radius:
        c.char_type = RADIUS
    else:
        c.char_type = DISTANCE

    # --- symmetric tolerance: "5 ±0,1" ---
    sym = re.search(r"±\s*(\d+(?:,\d+)?)", body)
    if sym:
        nominal_part = body[:sym.start()]
        nums = _NUM_RE.findall(nominal_part)
        c.nominal = nums[0] if nums else ""
        c.upper_tol = sym.group(1)
        c.lower_tol = "-" + sym.group(1)
    else:
        nums = _NUM_RE.findall(body)
        # signed tokens (with explicit +/-) are tolerances; the rest is nominal
        signed = [n for n in nums if n[0] in "+-"]
        unsigned = [n for n in nums if n[0] not in "+-"]
        if unsigned:
            c.nominal = unsigned[0]
        elif nums:
            c.nominal = _strip_sign(nums[0])
        if len(signed) >= 1:
            c.upper_tol = _strip_sign(signed[0])
        if len(signed) >= 2:
            c.lower_tol = signed[1] if signed[1][0] == "-" else "-" + signed[1]

    # --- flatness convention: nominal is the controlled feature (0), tol is the value ---
    if c.char_type == FLATNESS and c.upper_tol == "" and c.nominal:
        c.upper_tol = c.nominal
        c.nominal = "0"

    # --- radius MAX convention: upper tol 0 when only nominal present ---
    if c.char_type == RADIUS and c.upper_tol == "" and "MAX" in upper:
        c.upper_tol = "0"

    return c
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/parser.py tests/test_parser.py
git commit -m "feat: value parser and characteristic classifier"
```

---

## Task 4: Excel writer

**Files:**
- Create: `app/excel.py`
- Test: `tests/test_excel.py`

- [ ] **Step 1: Write the failing test**

```python
from openpyxl import load_workbook
from app.models import Characteristic
from app.excel import write_workbook

def test_write_workbook(tmp_path):
    rows = [
        Characteristic(pos=2, char_type="Distance", nominal="3,2", upper_tol="0,05", lower_tol="-0,05"),
        Characteristic(pos=1, char_type="Distance", nominal="1,2", upper_tol="0,1", lower_tol="-0,1"),
    ]
    out = tmp_path / "out.xlsx"
    write_workbook(rows, out)
    wb = load_workbook(out)
    ws = wb.active
    # header row 1 (German) and row 2 (English)
    assert ws.cell(1, 1).value == "Pos."
    assert ws.cell(1, 2).value == "Merkmal"
    assert ws.cell(2, 2).value == "Characteristic"
    # data sorted by pos starting row 3
    assert ws.cell(3, 1).value == 1
    assert ws.cell(3, 3).value == "1,2"
    assert ws.cell(4, 1).value == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_excel.py -v`
Expected: FAIL — `ModuleNotFoundError: app.excel`

- [ ] **Step 3: Write the implementation**

```python
# app/excel.py
from pathlib import Path
from typing import Iterable
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Side, Font
from app.models import Characteristic

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


def write_workbook(rows: Iterable[Characteristic], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection"

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

    path = Path(path)
    wb.save(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_excel.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/excel.py tests/test_excel.py
git commit -m "feat: openpyxl inspection-sheet writer"
```

---

## Task 5: PDF render stage

**Files:**
- Create: `app/pipeline/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
from app.pipeline.render import render_page

def test_render_page_returns_image_and_scale(sample_pdf, tmp_path):
    result = render_page(sample_pdf, dpi=200, out_dir=tmp_path)
    assert result.png_path.exists()
    assert result.width > 1000 and result.height > 700  # landscape A2-ish
    # scale maps PDF points (72dpi) to pixels
    assert abs(result.scale - 200 / 72) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.render`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/render.py
from dataclasses import dataclass
from pathlib import Path
import fitz  # PyMuPDF


@dataclass
class RenderResult:
    png_path: Path
    width: int
    height: int
    scale: float          # pixels per PDF point
    page_rect: tuple      # (x0, y0, x1, y1) in PDF points


def render_page(pdf_path, dpi: int = 200, out_dir: Path = None, page_index: int = 0) -> RenderResult:
    out_dir = Path(out_dir or Path(pdf_path).parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_path = out_dir / "page.png"
    pix.save(png_path)
    rect = page.rect
    doc.close()
    return RenderResult(
        png_path=png_path,
        width=pix.width,
        height=pix.height,
        scale=scale,
        page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_render.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/render.py tests/test_render.py
git commit -m "feat: PDF page render stage"
```

---

## Task 6: Balloon-number anchor extraction

**Files:**
- Create: `app/pipeline/anchors.py`
- Test: `tests/test_anchors.py`

- [ ] **Step 1: Write the failing test**

```python
from app.pipeline.anchors import extract_anchors

def test_extract_balloon_anchors(sample_pdf):
    anchors = extract_anchors(sample_pdf, scale=200 / 72)
    nums = sorted(a.number for a in anchors)
    # text layer holds balloons 1..22 (verified via pdftotext)
    assert nums == list(range(1, 23))
    # each anchor has an image-space centre
    a1 = next(a for a in anchors if a.number == 1)
    assert a1.x > 0 and a1.y > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_anchors.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.anchors`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/anchors.py
from dataclasses import dataclass
import re
import fitz

_INT_RE = re.compile(r"^\d{1,3}$")


@dataclass
class Anchor:
    number: int
    x: float        # image-space centre x
    y: float        # image-space centre y
    bbox: tuple     # image-space (x0,y0,x1,y1)


def extract_anchors(pdf_path, scale: float, page_index: int = 0):
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    anchors = []
    seen = set()
    for w in page.get_text("words"):
        x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
        token = word.strip()
        if not _INT_RE.match(token):
            continue
        n = int(token)
        if not (1 <= n <= 199):       # balloon range guard
            continue
        if n in seen:
            continue
        seen.add(n)
        anchors.append(Anchor(
            number=n,
            x=(x0 + x1) / 2 * scale,
            y=(y0 + y1) / 2 * scale,
            bbox=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
        ))
    doc.close()
    return anchors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_anchors.py -v`
Expected: PASS. If the count assertion fails, print `nums` and adjust the range guard — the oracle is balloons 1–22.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/anchors.py tests/test_anchors.py
git commit -m "feat: balloon-number anchor extraction"
```

---

## Task 7: Vector extraction (leader lines + circles)

**Files:**
- Create: `app/pipeline/vectors.py`
- Test: `tests/test_vectors.py`

- [ ] **Step 1: Write the failing test**

```python
from app.pipeline.vectors import extract_segments

def test_extract_segments(sample_pdf):
    segs = extract_segments(sample_pdf, scale=200 / 72)
    assert len(segs) > 50          # drawing is line-art heavy
    s = segs[0]
    assert len(s) == 4            # (x0, y0, x1, y1) image-space
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vectors.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.vectors`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/vectors.py
import fitz


def extract_segments(pdf_path, scale: float, page_index: int = 0):
    """Return straight line segments as (x0,y0,x1,y1) in image space."""
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    segments = []
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] == "l":           # ("l", p1, p2)
                p1, p2 = item[1], item[2]
                segments.append((p1.x * scale, p1.y * scale,
                                 p2.x * scale, p2.y * scale))
            elif item[0] == "re":        # rectangle → 4 edges
                r = item[1]
                pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for i in range(4):
                    a, b = pts[i], pts[(i + 1) % 4]
                    segments.append((a[0]*scale, a[1]*scale, b[0]*scale, b[1]*scale))
    doc.close()
    return segments
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vectors.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/vectors.py tests/test_vectors.py
git commit -m "feat: vector segment extraction"
```

---

## Task 8: Tracer (balloon → target region)

Deterministic association. For each anchor, find the leader segment whose endpoint is nearest the anchor centre, then project along that segment to its far endpoint and build a target region there. Falls back to a region directly around the anchor when no segment is close.

**Files:**
- Create: `app/pipeline/tracer.py`
- Test: `tests/test_tracer.py`

- [ ] **Step 1: Write the failing test (synthetic geometry)**

```python
from app.pipeline.tracer import trace_target, _nearest_segment

def test_nearest_segment_picks_touching_leader():
    anchor = (100.0, 100.0)
    segments = [
        (102.0, 100.0, 300.0, 100.0),   # leader starting near anchor
        (500.0, 500.0, 600.0, 600.0),   # unrelated
    ]
    seg = _nearest_segment(anchor, segments, max_dist=20)
    assert seg == (102.0, 100.0, 300.0, 100.0)

def test_trace_target_region_at_far_end():
    anchor = (100.0, 100.0)
    segments = [(102.0, 100.0, 300.0, 100.0)]
    region = trace_target(anchor, segments, region=80, max_dist=20)
    x0, y0, x1, y1 = region
    # region centred on far endpoint (300,100)
    assert x0 < 300 < x1 and y0 < 100 < y1

def test_trace_target_fallback_when_no_leader():
    anchor = (100.0, 100.0)
    region = trace_target(anchor, [], region=80, max_dist=20)
    x0, y0, x1, y1 = region
    assert x0 < 100 < x1 and y0 < 100 < y1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tracer.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.tracer`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/tracer.py
import math


def _dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _nearest_segment(anchor, segments, max_dist):
    ax, ay = anchor
    best = None
    best_d = max_dist
    for seg in segments:
        x0, y0, x1, y1 = seg
        d = min(_dist(ax, ay, x0, y0), _dist(ax, ay, x1, y1))
        if d < best_d:
            best_d = d
            best = seg
    return best


def trace_target(anchor, segments, region: float = 120, max_dist: float = 25):
    """Return an (x0,y0,x1,y1) image-space box around the leader's far endpoint."""
    ax, ay = anchor
    seg = _nearest_segment(anchor, segments, max_dist)
    if seg is None:
        cx, cy = ax, ay
    else:
        x0, y0, x1, y1 = seg
        # the near end is whichever endpoint is closest to the anchor; target = the other end
        if _dist(ax, ay, x0, y0) <= _dist(ax, ay, x1, y1):
            cx, cy = x1, y1
        else:
            cx, cy = x0, y0
    half = region / 2
    return (cx - half, cy - half, cx + half, cy + half)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tracer.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/tracer.py tests/test_tracer.py
git commit -m "feat: deterministic balloon→target tracer"
```

---

## Task 9: OCR backend interface + Tesseract implementation

**Files:**
- Create: `app/pipeline/ocr/base.py`, `app/pipeline/ocr/tesseract_backend.py`
- Test: `tests/test_ocr_tesseract.py`

- [ ] **Step 1: Write the failing test**

```python
import shutil
import pytest
from PIL import Image, ImageDraw
from app.pipeline.ocr.tesseract_backend import TesseractBackend

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None,
                                reason="tesseract binary not installed")

def test_tesseract_reads_simple_text():
    img = Image.new("RGB", (220, 80), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 25), "12,5", fill="black")
    backend = TesseractBackend()
    result = backend.read_region(img)
    assert "12" in result.text
    assert 0.0 <= result.confidence <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ocr_tesseract.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.ocr.tesseract_backend` (or SKIP if no tesseract; install Docker-side, but module import must still succeed once written)

- [ ] **Step 3: Write `base.py`**

```python
# app/pipeline/ocr/base.py
from dataclasses import dataclass
from typing import Protocol
from PIL import Image


@dataclass
class OcrResult:
    text: str
    confidence: float        # 0..1


class OCRBackend(Protocol):
    def read_region(self, image: Image.Image) -> OcrResult:
        ...
```

- [ ] **Step 4: Write `tesseract_backend.py`**

```python
# app/pipeline/ocr/tesseract_backend.py
import pytesseract
from PIL import Image
from app.pipeline.ocr.base import OcrResult

# technical-notation char allowlist; psm 6 = uniform block of text
_CONFIG = (
    "--psm 6 "
    "-c tessedit_char_whitelist=0123456789,.±+-RØMAXxX°/ "
)


class TesseractBackend:
    def __init__(self, lang: str = "deu+eng", config: str = _CONFIG):
        self.lang = lang
        self.config = config

    def read_region(self, image: Image.Image) -> OcrResult:
        data = pytesseract.image_to_data(
            image, lang=self.lang, config=self.config,
            output_type=pytesseract.Output.DICT,
        )
        words, confs = [], []
        for txt, conf in zip(data["text"], data["conf"]):
            if txt.strip():
                words.append(txt.strip())
                try:
                    confs.append(float(conf))
                except ValueError:
                    pass
        text = " ".join(words)
        confidence = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return OcrResult(text=text, confidence=confidence)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_ocr_tesseract.py -v`
Expected: PASS if tesseract installed, else SKIP. Verify the test runs (not errors on import).

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/ocr/base.py app/pipeline/ocr/tesseract_backend.py tests/test_ocr_tesseract.py
git commit -m "feat: OCR backend interface and Tesseract implementation"
```

---

## Task 10: Optional VLM backend + backend factory

The VLM backend is optional and lazily imports torch/transformers so the default container never needs them. The factory selects by env var and falls back to Tesseract.

**Files:**
- Create: `app/pipeline/ocr/vlm_backend.py`, `app/pipeline/ocr/__init__.py` (replace empty)
- Test: extend `tests/test_ocr_tesseract.py` with a factory test

- [ ] **Step 1: Write the failing test (append)**

```python
from app.pipeline.ocr import get_backend
from app.pipeline.ocr.tesseract_backend import TesseractBackend

def test_factory_defaults_to_tesseract(monkeypatch):
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    backend = get_backend()
    assert isinstance(backend, TesseractBackend)

def test_factory_vlm_falls_back_without_gpu(monkeypatch):
    monkeypatch.setenv("OCR_BACKEND", "vlm")
    # no GPU/torch in test env → must fall back, not crash
    backend = get_backend()
    assert isinstance(backend, TesseractBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ocr_tesseract.py -v -k factory`
Expected: FAIL — `cannot import name 'get_backend'`

- [ ] **Step 3: Write `vlm_backend.py`**

```python
# app/pipeline/ocr/vlm_backend.py
from PIL import Image
from app.pipeline.ocr.base import OcrResult

_PROMPT = (
    "You are reading one dimension callout from a mechanical drawing. "
    "Return ONLY the exact characters you see, no explanation. "
    "Preserve symbols like Ø, R, ± and comma decimals."
)


class VLMBackend:
    """Local GPU vision-LLM doing constrained per-region reads only."""

    def __init__(self, model_id: str = "Qwen/Qwen2.5-VL-7B-Instruct"):
        # Imported lazily so the default image needs no torch.
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto"
        )

    def read_region(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": _PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        out = self.model.generate(**inputs, max_new_tokens=40, do_sample=False)
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.85)
```

- [ ] **Step 4: Write the factory `app/pipeline/ocr/__init__.py`**

```python
# app/pipeline/ocr/__init__.py
import os
from app.pipeline.ocr.base import OCRBackend, OcrResult
from app.pipeline.ocr.tesseract_backend import TesseractBackend


def _gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def get_backend() -> OCRBackend:
    choice = os.getenv("OCR_BACKEND", "tesseract").lower()
    if choice == "vlm" and _gpu_available():
        try:
            from app.pipeline.ocr.vlm_backend import VLMBackend
            return VLMBackend()
        except Exception:
            pass  # fall through to tesseract
    return TesseractBackend()


__all__ = ["OCRBackend", "OcrResult", "get_backend", "TesseractBackend"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_ocr_tesseract.py -v -k factory`
Expected: PASS (2 passed) — both resolve to Tesseract without a GPU.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/ocr/__init__.py app/pipeline/ocr/vlm_backend.py tests/test_ocr_tesseract.py
git commit -m "feat: optional VLM OCR backend with tesseract fallback"
```

---

## Task 11: Notes-table extraction (101–104)

The note callouts are boxed numbers in the top-right notes table, not in the text layer. Read that table region with OCR and split each row into mark number + bilingual description.

**Files:**
- Create: `app/pipeline/notes.py`
- Test: extend `tests/test_pipeline_integration.py` (added in Task 12); here, unit-test the row splitter.
- Test: `tests/test_notes.py`

- [ ] **Step 1: Write the failing test**

```python
from app.pipeline.notes import split_note_rows

def test_split_note_rows():
    raw = "101 CONTACT AREA PLANARITY 0,2mm\n102 PART FREE OF GREASE AND OIL"
    rows = split_note_rows(raw)
    assert rows[0].pos == 101
    assert "PLANARITY" in rows[0].nominal
    assert rows[0].char_type == "Note"
    assert rows[1].pos == 102
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notes.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.notes`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/notes.py
import re
from typing import List
from PIL import Image
from app.models import Characteristic
from app.pipeline.ocr.base import OCRBackend

_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\b\s*(.*)$")


def split_note_rows(raw: str) -> List[Characteristic]:
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        m = _ROW_RE.match(line)
        if not m:
            continue
        rows.append(Characteristic(
            pos=int(m.group(1)),
            char_type="Note",
            nominal=m.group(2).strip(),
            raw_text=line,
            confidence=0.5,
        ))
    return rows


def extract_notes(image: Image.Image, region, backend: OCRBackend) -> List[Characteristic]:
    """region = (x0,y0,x1,y1) image-space box of the notes table."""
    crop = image.crop(region)
    result = backend.read_region(crop)
    rows = split_note_rows(result.text)
    for r in rows:
        r.confidence = min(r.confidence, result.confidence or 0.5)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notes.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/notes.py tests/test_notes.py
git commit -m "feat: notes-table (101-104) extraction"
```

---

## Task 12: Pipeline orchestration + integration test

Ties render → anchors → vectors → trace → OCR → parse together, plus the notes table, into a single `extract()` returning `List[Characteristic]`.

**Files:**
- Create: `app/pipeline/extract.py`
- Test: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Write the failing integration test**

```python
import shutil
import pytest
from app.pipeline.extract import extract

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed")

def test_extract_returns_all_balloons(sample_pdf, tmp_path):
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300)
    positions = sorted(r.pos for r in rows)
    # 22 leader balloons present regardless of OCR quality
    for n in range(1, 23):
        assert n in positions

@needs_tesseract
def test_extract_recovers_known_values(sample_pdf, tmp_path):
    rows = {r.pos: r for r in extract(sample_pdf, work_dir=tmp_path, dpi=300)}
    # Diameter classification is symbol-driven and robust
    assert rows[4].char_type == "Diameter"
    assert rows[5].char_type == "Diameter"
    # Distance balloons carry a numeric nominal
    assert rows[1].nominal != ""
```

> Note: the value-recovery test asserts structural facts (classification, non-empty nominal) rather than exact OCR strings, because OCR fidelity is the human-review safety net. Exact-string assertions live in `test_parser.py` where input is controlled.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: FAIL — `ModuleNotFoundError: app.pipeline.extract`

- [ ] **Step 3: Write the implementation**

```python
# app/pipeline/extract.py
from pathlib import Path
from typing import List
from PIL import Image
from app.models import Characteristic
from app.pipeline.render import render_page
from app.pipeline.anchors import extract_anchors
from app.pipeline.vectors import extract_segments
from app.pipeline.tracer import trace_target
from app.pipeline.parser import parse_value
from app.pipeline.notes import extract_notes
from app.pipeline.ocr import get_backend

# Notes table region as a fraction of page (top-right block); tuned for the template.
_NOTES_FRAC = (0.55, 0.0, 1.0, 0.22)


def _hint_for(anchor_number: int) -> str:
    return ""  # extendable: map specific balloons to 'material'/'flatness' if needed


def extract(pdf_path, work_dir, dpi: int = 300) -> List[Characteristic]:
    work_dir = Path(work_dir)
    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    scale = render.scale
    image = Image.open(render.png_path).convert("RGB")

    anchors = extract_anchors(pdf_path, scale=scale)
    segments = extract_segments(pdf_path, scale=scale)
    backend = get_backend()

    results: List[Characteristic] = []
    for a in anchors:
        region = trace_target((a.x, a.y), segments)
        # clamp region to image bounds
        x0, y0, x1, y1 = region
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(render.width, x1), min(render.height, y1)
        crop = image.crop((x0, y0, x1, y1))
        ocr = backend.read_region(crop)
        c = parse_value(ocr.text, hint=_hint_for(a.number))
        c.pos = a.number
        c.balloon_xy = (a.x, a.y)
        c.target_region = (x0, y0, x1, y1)
        c.confidence = ocr.confidence
        results.append(c)

    # notes table (101–104)
    nx0 = render.width * _NOTES_FRAC[0]
    ny0 = render.height * _NOTES_FRAC[1]
    nx1 = render.width * _NOTES_FRAC[2]
    ny1 = render.height * _NOTES_FRAC[3]
    results.extend(extract_notes(image, (nx0, ny0, nx1, ny1), backend))

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: first test PASSES (anchor-driven, no OCR needed); second PASSES if tesseract present, else SKIP. If `_NOTES_FRAC` misses the table, adjust the fractions and re-run.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/extract.py tests/test_pipeline_integration.py
git commit -m "feat: end-to-end extraction pipeline"
```

---

## Task 13: FastAPI app (upload, image, export)

**Files:**
- Create: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_upload_returns_rows_and_image(sample_pdf):
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert "rows" in data and len(data["rows"]) >= 22
    assert data["image_url"].startswith("/api/image/")

def test_export_roundtrip(sample_pdf):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")}).json()
    rows = up["rows"]
    r = client.post("/api/export", json={"session_id": up["session_id"], "rows": rows})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(r.content) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: app.main`

- [ ] **Step 3: Write the implementation**

```python
# app/main.py
import tempfile
import uuid
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.models import Characteristic
from app.pipeline.extract import extract
from app.excel import write_workbook

app = FastAPI(title="Sindri")

_SESSIONS = Path(tempfile.gettempdir()) / "sindri_sessions"
_SESSIONS.mkdir(exist_ok=True)


class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    work = _SESSIONS / session_id
    work.mkdir(parents=True, exist_ok=True)
    pdf_path = work / "input.pdf"
    pdf_path.write_bytes(await file.read())
    rows = extract(pdf_path, work_dir=work, dpi=300)
    return JSONResponse({
        "session_id": session_id,
        "image_url": f"/api/image/{session_id}",
        "rows": [r.model_dump() for r in rows],
    })


@app.get("/api/image/{session_id}")
def image(session_id: str):
    png = _SESSIONS / session_id / "page.png"
    return FileResponse(png, media_type="image/png")


@app.post("/api/export")
def export(req: ExportRequest):
    work = _SESSIONS / req.session_id
    work.mkdir(parents=True, exist_ok=True)
    out = work / "inspection.xlsx"
    write_workbook(req.rows, out)
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="inspection.xlsx",
    )


# static UI mounted last so /api/* takes precedence
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
```

- [ ] **Step 4: Create a placeholder static file so the mount succeeds**

`app/static/index.html` (full version in Task 14):
```html
<!doctype html><html><body><h1>Sindri</h1></body></html>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS (2 passed) if tesseract present; the upload test's row count relies only on anchors so it passes regardless.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/static/index.html tests/test_api.py
git commit -m "feat: FastAPI upload/image/export endpoints"
```

---

## Task 14: Review UI (frontend)

**Files:**
- Modify: `app/static/index.html`
- Create: `app/static/app.js`

- [ ] **Step 1: Write `index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sindri — Drawing Balloon Extractor</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; display: flex; height: 100vh; }
    #left { flex: 1; overflow: auto; border-right: 1px solid #ccc; position: relative; }
    #left img { width: 100%; display: block; }
    .marker { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px;
              border: 2px solid #2563eb; border-radius: 50%; background: rgba(37,99,235,.2); }
    #right { width: 46%; overflow: auto; padding: 12px; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 3px 5px; }
    td[contenteditable] { background: #fff; }
    tr.low td { background: #fff7ed; }
    #bar { padding: 8px 12px; border-bottom: 1px solid #ccc; display: flex; gap: 8px; align-items: center; }
    button { padding: 6px 12px; cursor: pointer; }
  </style>
</head>
<body>
  <div id="left"><div id="overlay"></div></div>
  <div id="right">
    <div id="bar">
      <input type="file" id="file" accept="application/pdf" />
      <button id="exportBtn" disabled>Download .xlsx</button>
      <span id="status"></span>
    </div>
    <table id="grid">
      <thead><tr><th>Pos</th><th>Characteristic</th><th>Nominal</th><th>Upper-tol</th><th>Lower-tol</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `app.js`**

```javascript
let sessionId = null;
let rows = [];

const $ = (s) => document.querySelector(s);

$("#file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("#status").textContent = "Extracting…";
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await res.json();
  sessionId = data.session_id;
  rows = data.rows;
  renderImage(data.image_url);
  renderGrid();
  $("#exportBtn").disabled = false;
  $("#status").textContent = `${rows.length} balloons`;
});

function renderImage(url) {
  const left = $("#left");
  let img = left.querySelector("img");
  if (!img) { img = document.createElement("img"); left.prepend(img); }
  img.onload = () => placeMarkers(img);
  img.src = url + "?t=" + Date.now();
}

function placeMarkers(img) {
  const overlay = $("#overlay");
  overlay.innerHTML = "";
  const sx = img.clientWidth / img.naturalWidth;
  const sy = img.clientHeight / img.naturalHeight;
  rows.forEach((r) => {
    if (!r.balloon_xy) return;
    const m = document.createElement("div");
    m.className = "marker";
    m.style.left = r.balloon_xy[0] * sx + "px";
    m.style.top = r.balloon_xy[1] * sy + "px";
    m.title = "Pos " + r.pos;
    overlay.appendChild(m);
  });
}

function renderGrid() {
  const tb = $("#grid tbody");
  tb.innerHTML = "";
  rows.sort((a, b) => a.pos - b.pos).forEach((r, i) => {
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

$("#exportBtn").addEventListener("click", async () => {
  const res = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, rows }),
  });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "inspection.xlsx";
  a.click();
});
```

- [ ] **Step 3: Manual smoke check**

Run: `. .venv/bin/activate && uvicorn app.main:app --port 8000`
Open `http://localhost:8000`, upload `sample.pdf`, confirm: image with markers appears, grid populates, editing a cell then Download produces an `.xlsx`.
Expected: all three behaviors work. (Requires tesseract locally; otherwise verify in Docker in Task 15.)

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: review UI with balloon overlay and editable grid"
```

---

## Task 15: Docker packaging + README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `docker-compose.gpu.yml`, `.dockerignore`, `README.md`

- [ ] **Step 1: Write `.dockerignore`**

```
.venv
__pycache__
*.pyc
tests
docs
data
.git
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-deu \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  sindri:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - OCR_BACKEND=tesseract
```

- [ ] **Step 4: Write `docker-compose.gpu.yml` (optional GPU override)**

```yaml
services:
  sindri:
    environment:
      - OCR_BACKEND=vlm
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

> The GPU image needs torch/transformers. To keep the default image small, build the GPU variant with an extra requirements layer when needed; document this in the README rather than bloating the default image.

- [ ] **Step 5: Write `README.md`**

```markdown
# Sindri — Offline Drawing Balloon → Excel Extractor

Extracts numbered-balloon dimensions from an Intercable-template technical drawing PDF
into a reviewable inspection-sheet `.xlsx`. Fully offline, one container.

## Run (default, CPU/Tesseract)

    docker compose up

Open http://localhost:8000, upload your PDF, review/correct the table, download the .xlsx.

## Optional GPU vision-LLM OCR

Requires an NVIDIA GPU + NVIDIA Container Toolkit and a torch/transformers layer:

    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

Falls back to Tesseract automatically if no GPU is available.

## Tests

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    pytest -q
```

- [ ] **Step 6: Build and run the container (verification)**

Run: `docker compose up --build -d && sleep 5 && curl -sf http://localhost:8000/ | head -1`
Expected: HTML returned (the UI). Then open the UI, upload `sample.pdf`, confirm extraction + export work end-to-end inside the container.

- [ ] **Step 7: Tear down and commit**

```bash
docker compose down
git add Dockerfile docker-compose.yml docker-compose.gpu.yml .dockerignore README.md
git commit -m "feat: docker packaging and README"
```

---

## Self-Review

**Spec coverage:**
- Offline / one container → Tasks 13–15 (FastAPI single service, Dockerfile, compose). ✓
- Balloon anchors via embedded text → Task 6. ✓
- Leader-line tracing for association → Tasks 7–8. ✓
- OCR of values, pluggable backend → Tasks 9–10. ✓
- VLM analysis decision (opt-in, fallback) → Task 10 + `docker-compose.gpu.yml`. ✓
- Parser/classifier incl. Diameter/Radius/Flatness/Distance/Material/Note → Task 3 + Task 11. ✓
- All numbered balloons incl. 101–104 notes → Task 11 + Task 12 orchestration. ✓
- Review UI with confidence flags + editable cells → Task 14. ✓
- Excel layout matching `excel_output.png` → Task 4. ✓
- Golden-fixture testing → Tasks 6, 12. ✓

**Placeholder scan:** No "TBD/TODO"; every code step contains runnable code; commands have expected output. The GPU image's torch layer is intentionally documented (not bundled) to keep the default image small — this is a stated design choice, not a placeholder.

**Type consistency:** `Characteristic` fields (`pos, char_type, nominal, upper_tol, lower_tol, raw_text, confidence, balloon_xy, target_region`) are used identically across `parser.py`, `excel.py`, `notes.py`, `extract.py`, and `main.py`. `OcrResult(text, confidence)` and `read_region(image)` are consistent across both backends and the factory `get_backend()`. `RenderResult` fields (`png_path, width, height, scale, page_rect`) match their uses in `extract.py`.

**Known tuning points (expected during execution, not failures):** `_NOTES_FRAC` region, tracer `max_dist`/`region` sizes, and Tesseract `--psm`/whitelist may need adjustment against the real render — each has a test or smoke step that reveals when tuning is needed.

---

## Post-implementation revisions (2026-06-16)

The plan was executed task-by-task, then revised based on results against the real drawing (see the design doc §4a, §5, §11):

- **Tasks 7–8 (vector/tracer association) were replaced.** The segment-leader-tracing model cropped the balloon glyph itself, because the nearest segments are the balloon's own circle/arrow. Association now uses **blue-balloon connected-component arrow-direction** detection (`app/pipeline/balloons.py`); `vectors.py`/`tracer.py` and their tests were removed.
- **OCR reading:** Tesseract reads this content poorly even with correct crops and preprocessing. The **local VLM backend (Qwen2.5-VL)** is the accuracy path and is validated working on an H100 host; Tesseract remains the no-GPU fallback.
- **GPU launch:** via **NVIDIA CDI** (`./run-gpu.sh`, `--device nvidia.com/gpu=all`), not compose `deploy.devices` (ignored by podman-compose).
- **Observability/perf:** backend selection is logged + exposed at `GET /api/health`; the backend is loaded once at startup (not per request).
- **Deferred:** notes 101–104 extraction and crop-precision tightening — handled by the review UI for now.
