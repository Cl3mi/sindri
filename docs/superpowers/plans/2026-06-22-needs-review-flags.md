# Per-Row Needs-Review Flags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag each extracted row with a reasoned `needs_review` signal so the reviewer knows which rows to check and why.

**Architecture:** A single pure policy function (`review.py`) computes `(needs_review, reasons)` from concrete extraction facts. `extract.py` surfaces a rotation-ambiguity signal from `_best_read` and calls the policy per row; `/api/read_region` calls it for manual re-reads. The review UI drives its existing row highlight off the flag and shows the reasons.

**Tech Stack:** Python, Pydantic, pytest, FastAPI, vanilla JS UI. Reuses the existing `Characteristic` model, `StubVLMBackend` test double, and `.low` CSS row style.

---

## File Structure

- `app/models.py` — add `needs_review` + `review_reasons` fields to `Characteristic` (Task 1).
- `app/pipeline/review.py` — **new**: the `review_flags` policy function (Task 2).
- `app/pipeline/extract.py` — `_best_read` returns rotation-ambiguity; loop calls `review_flags` (Task 3).
- `app/main.py` — `/api/read_region` calls `review_flags` (Task 4).
- `app/static/app.js` + `app/static/index.html` — highlight + `⚠` marker + reasons tooltip (Task 5).
- Tests live beside their modules under `tests/` (every task).

---

## Task 1: Add needs-review fields to the model

**Files:**
- Modify: `app/models.py:1` (import) and after `:15` (new fields)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_characteristic_has_review_fields_defaults_and_values():
    c = Characteristic(pos=1)
    assert c.needs_review is False
    assert c.review_reasons == []
    c2 = Characteristic(pos=2, needs_review=True, review_reasons=["empty read"])
    assert c2.needs_review is True
    assert c2.review_reasons == ["empty read"]


def test_characteristic_review_reasons_are_independent_per_instance():
    a = Characteristic(pos=1)
    b = Characteristic(pos=2)
    a.review_reasons.append("missing nominal")
    assert b.review_reasons == []      # no shared mutable default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py::test_characteristic_has_review_fields_defaults_and_values -v`
Expected: FAIL — `Characteristic` has no field `needs_review`.

- [ ] **Step 3: Add the fields**

In `app/models.py`, change the first line from:

```python
from typing import Optional, Tuple
```

to:

```python
from typing import List, Optional, Tuple
```

Then add after the `source` line (currently line 15):

```python
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty read", "missing nominal"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`
Expected: PASS (both new tests + all existing model tests). Pydantic v2 deep-copies mutable field defaults, so the per-instance independence test passes.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add needs_review and review_reasons fields to Characteristic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: The review policy — `review.py`

**Files:**
- Create: `app/pipeline/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review.py`:

```python
from app.models import Characteristic
from app.pipeline.review import review_flags


def _row(**kw):
    base = dict(pos=1, char_type="Distance", nominal="1,2", raw_text="1,2 +0,1 -0,1",
                confidence=0.9)
    base.update(kw)
    return Characteristic(**base)


def test_clean_dimension_row_is_not_flagged():
    flagged, reasons = review_flags(_row(), rotation_ambiguous=False)
    assert flagged is False
    assert reasons == []


def test_empty_read_is_flagged():
    flagged, reasons = review_flags(_row(raw_text="", nominal="", confidence=0.0),
                                    rotation_ambiguous=False)
    assert flagged is True
    assert reasons == ["empty read"]


def test_empty_read_does_not_also_report_missing_nominal_or_low_conf():
    _, reasons = review_flags(_row(raw_text="  ", nominal="", confidence=0.0),
                              rotation_ambiguous=False)
    assert reasons == ["empty read"]


def test_missing_nominal_when_text_present_but_unparsed():
    _, reasons = review_flags(_row(raw_text="garbled", nominal=""),
                              rotation_ambiguous=False)
    assert reasons == ["missing nominal"]


def test_low_ocr_confidence_when_text_present():
    _, reasons = review_flags(_row(raw_text="1,2", nominal="1,2", confidence=0.4),
                              rotation_ambiguous=False)
    assert reasons == ["low OCR confidence"]


def test_rotation_ambiguity_reason():
    _, reasons = review_flags(_row(), rotation_ambiguous=True)
    assert reasons == ["rotation ambiguity"]


def test_gdt_position_row_with_zero_nominal_not_flagged_for_missing_nominal():
    # GD&T rows carry nominal "0"; they must not trip the missing-nominal rule
    flagged, reasons = review_flags(
        _row(char_type="Position", nominal="0", raw_text="⊕ Ø0.1 A"),
        rotation_ambiguous=False)
    assert "missing nominal" not in reasons
    assert flagged is False


def test_note_row_without_nominal_not_flagged_for_missing_nominal():
    # Notes are not dimension types; only their content matters (covered by empty read)
    _, reasons = review_flags(_row(char_type="Note", nominal="see DBL 8585",
                                   raw_text="see DBL 8585"),
                              rotation_ambiguous=False)
    assert reasons == []


def test_combination_empty_read_and_rotation_ambiguity():
    flagged, reasons = review_flags(_row(raw_text="", nominal="", confidence=0.0),
                                    rotation_ambiguous=True)
    assert flagged is True
    assert reasons == ["empty read", "rotation ambiguity"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.review'`.

- [ ] **Step 3: Create `review.py`**

Create `app/pipeline/review.py`:

```python
"""The needs-review policy: one pure function mapping a row's observed extraction
facts to a flag + human-readable reasons. The single home for this policy so it
can be understood and tested in isolation."""
from typing import List, Tuple

from app.models import Characteristic

# Measurement types that must carry a numeric nominal. GD&T/Flatness/Position
# (nominal "0"), Theoretical, Reference, Note and Material are intentionally exempt.
DIMENSION_TYPES = {"Distance", "Diameter", "Radius"}
LOW_CONF = 0.6


def review_flags(c: Characteristic, rotation_ambiguous: bool) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a populated Characteristic.

    Gating: an empty read is its own reason and does not also report
    "missing nominal" or "low OCR confidence"."""
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
    return bool(reasons), reasons
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/review.py tests/test_review.py
git commit -m "feat: review_flags policy for per-row needs-review signal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Surface rotation ambiguity and apply flags in `extract`

**Files:**
- Modify: `app/pipeline/extract.py` — add `ROTATION_EPS`, rewrite `_best_read` to return a third value, call `review_flags` in the loop
- Test: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_integration.py`:

```python
def test_best_read_flags_rotation_ambiguity_on_vertical_crop():
    from PIL import Image
    from app.pipeline.extract import _best_read

    # the stub returns identical text/conf for every rotation -> equal scores -> ambiguous
    backend = StubVLMBackend(text="1,2", confidence=0.9)
    tall = Image.new("RGB", (20, 80), "white")
    text, conf, ambiguous = _best_read(backend, tall, vertical=True)
    assert text == "1,2"
    assert ambiguous is True


def test_best_read_not_ambiguous_on_horizontal_crop():
    from PIL import Image
    from app.pipeline.extract import _best_read

    backend = StubVLMBackend(text="1,2", confidence=0.9)
    wide = Image.new("RGB", (80, 20), "white")
    _, _, ambiguous = _best_read(backend, wide, vertical=False)
    assert ambiguous is False


def test_extract_flags_empty_read(sample_pdf, tmp_path, monkeypatch):
    from app.pipeline.detect import Detection
    import app.pipeline.boxes as boxes_mod
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)], text="")
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert len(rows) == 1
    assert rows[0].needs_review is True
    assert rows[0].review_reasons == ["empty read"]


def test_extract_flags_missing_nominal(sample_pdf, tmp_path, monkeypatch):
    from app.pipeline.detect import Detection
    import app.pipeline.boxes as boxes_mod
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)], text="garbled")
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert len(rows) == 1
    assert rows[0].needs_review is True
    assert "missing nominal" in rows[0].review_reasons
```

Note on the empty/missing tests: they monkeypatch `detect_boxes` to `[]` so only the one stubbed VLM detection flows through (same isolation pattern the existing `test_extract_detects_numbers_places_and_reads` uses). The detection box `(40,40,120,70)` is horizontal, so rotation is not ambiguous and the only reason is the read-quality one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline_integration.py -k "rotation or flags_empty or flags_missing" -v`
Expected: FAIL — `_best_read` returns 2 values (ValueError unpacking 3); `needs_review` not set.

- [ ] **Step 3: Add `ROTATION_EPS` and rewrite `_best_read`**

In `app/pipeline/extract.py`, add the import of the policy near the other pipeline imports (after the `from app.pipeline.parser import parse_value` line):

```python
from app.pipeline.review import review_flags
```

Add a module constant next to `_HINTS` (after the `_NOTE_REF_RE` block):

```python
# how close the two rotation candidates must score to count as ambiguous
ROTATION_EPS = 0.15
```

Replace the entire `_best_read` function with:

```python
def _best_read(backend, crop: Image.Image, vertical: bool):
    """Read a crop; for vertical callouts try both 90 rotations and keep the best.
    Returns (text, conf, rotation_ambiguous) where rotation_ambiguous is True when
    the crop is vertical and the best two candidates score within ROTATION_EPS."""
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
```

- [ ] **Step 4: Wire the flag into the extraction loop**

In `app/pipeline/extract.py`, replace the loop body that reads and builds each row. The current body is:

```python
    for d in detections:
        outer = _clamp(d.box, render.width, render.height)
        read_box = _clamp(d.inner_box, render.width, render.height) if d.inner_box else outer
        crop = image.crop(read_box)
        if d.subtype == "gdt" and hasattr(backend, "read_region_gdt"):
            text, confidence = _safe_read(backend.read_region_gdt, crop)
        else:
            text, confidence = _best_read(backend, crop, _is_vertical(read_box))

        hint = _HINTS.get(d.kind, "")
        subtype = d.subtype or ""
        kind = d.kind
        # content retag: a boxed value reading as a 100-series number is a note-ref
        if subtype == "theoretical" and _NOTE_REF_RE.match(text or ""):
            hint, subtype, kind = "note", "note_ref", "note"

        c = parse_value(text, hint=hint)
        c.id = uuid.uuid4().hex
        c.kind = kind
        c.subtype = subtype
        c.source = "auto"
        c.target_region = outer
        c.confidence = confidence
        results.append(c)
```

Replace it with (note: `_safe_read` returns 2 values, so the GD&T branch sets `rotation_ambiguous = False`; the default branch takes the new third value):

```python
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
        # content retag: a boxed value reading as a 100-series number is a note-ref
        if subtype == "theoretical" and _NOTE_REF_RE.match(text or ""):
            hint, subtype, kind = "note", "note_ref", "note"

        c = parse_value(text, hint=hint)
        c.id = uuid.uuid4().hex
        c.kind = kind
        c.subtype = subtype
        c.source = "auto"
        c.target_region = outer
        c.confidence = confidence
        c.needs_review, c.review_reasons = review_flags(c, rotation_ambiguous)
        results.append(c)
```

- [ ] **Step 5: Run the targeted tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_pipeline_integration.py -v`
then: `.venv/bin/python -m pytest -q`
Expected: PASS. The pre-existing `test_extract_detects_numbers_places_and_reads` still passes — `_best_read` is called there via `extract`, and its detection is horizontal (`(40,40,120,70)`), so the row reads a clean nominal and is not flagged; the third return value is consumed inside `extract`, not by that test.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/extract.py tests/test_pipeline_integration.py
git commit -m "feat: compute rotation ambiguity and apply review_flags per row

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Apply flags to manual re-reads in the API

**Files:**
- Modify: `app/main.py:14` (import) and `:117-122` (the `/api/read_region` body)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_read_region_sets_needs_review_on_empty_read(monkeypatch):
    import app.main as main
    from PIL import Image

    # empty-text stub backend (StubVLMBackend.read_region returns text="")
    monkeypatch.setattr("app.main._BACKEND", StubVLMBackend(text=""))

    # a session with a page image present (read_region requires work/page.png)
    session = "sess-review"
    work = main._session_dir(session)
    work.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), "white").save(work / "page.png")

    resp = client.post("/api/read_region",
                       json={"session_id": session, "box": [10, 10, 80, 40]})
    assert resp.status_code == 200
    row = resp.json()
    assert row["needs_review"] is True
    assert row["review_reasons"] == ["empty read"]
```

This reuses the module-level `client = TestClient(app)` and `StubVLMBackend` already imported at the top of `tests/test_api.py`, and the `monkeypatch.setattr("app.main._BACKEND", ...)` pattern the existing fixtures use (so the real backend isn't mutated across tests). `main._session_dir(session)` builds the session path; writing `page.png` into it satisfies the endpoint's `png.is_file()` check.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_read_region_sets_needs_review_on_empty_read -v`
Expected: FAIL — the returned row has `needs_review == False` (flag not applied on the manual path).

- [ ] **Step 3: Import the policy and apply it**

In `app/main.py`, add to the imports (after `from app.pipeline.parser import parse_value`, line 14):

```python
from app.pipeline.review import review_flags
```

In the `read_region` endpoint, the current tail is:

```python
    c = parse_value(text)
    c.id = uuid.uuid4().hex
    c.source = "manual"
    c.target_region = box
    c.confidence = conf
    place_balloons([c])
    return c.model_dump()
```

Replace it with (add the `review_flags` call after `confidence` is set; rotation handling is unavailable on this manual path, so pass `False`):

```python
    c = parse_value(text)
    c.id = uuid.uuid4().hex
    c.source = "manual"
    c.target_region = box
    c.confidence = conf
    c.needs_review, c.review_reasons = review_flags(c, rotation_ambiguous=False)
    place_balloons([c])
    return c.model_dump()
```

- [ ] **Step 4: Run the test and the API suite**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: PASS (new test + existing API tests).

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: apply review_flags to manual /api/read_region re-reads

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Surface the flag in the review UI

**Files:**
- Modify: `app/static/app.js` (the `renderGrid` function, currently ~lines 153-164)

There is no automated JS test harness in this repo; this task is verified by reading the diff and a manual smoke check. Keep the change minimal and self-contained.

- [ ] **Step 1: Update `renderGrid` to drive highlight + reasons off the flag**

In `app/static/app.js`, the current `renderGrid` row loop is:

```javascript
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
```

Replace that `forEach` block with:

```javascript
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    if (r.needs_review) {
      tr.className = "low";
      tr.title = (r.review_reasons || []).join(", ");
    }
    const posCell = `${r.needs_review ? "⚠ " : ""}${r.pos}`;
    tr.innerHTML =
      `<td>${posCell}</td>` +
      ["char_type", "nominal", "upper_tol", "lower_tol"]
        .map((k) => `<td contenteditable data-i="${i}" data-k="${k}">${r[k] ?? ""}</td>`)
        .join("");
    tb.appendChild(tr);
  });
```

This reuses the existing `tr.low td { background: #fff7ed; }` rule in `index.html` (no CSS change), adds a `⚠` marker on the Pos cell, and exposes the reasons as a hover tooltip.

- [ ] **Step 2: Smoke-check the change**

Confirm the edit is syntactically valid and self-consistent:

Run: `node --check app/static/app.js`
Expected: no output (exit 0) — the file parses.

(If `node` is unavailable, instead visually verify the `forEach` block matches the replacement above and that no other reference to `r.confidence` remains in `renderGrid`: `grep -n "confidence" app/static/app.js` should show only unrelated lines, not the removed highlight check.)

- [ ] **Step 3: Run the full backend suite to confirm nothing regressed**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (GPU tests skipped).

- [ ] **Step 4: Commit**

```bash
git add app/static/app.js
git commit -m "feat: highlight needs-review rows with marker and reasons tooltip

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review Notes (verified against the spec)

- **Model fields `needs_review` + `review_reasons`** — Task 1. ✓
- **`review.py` pure policy with the four signals + gating** — Task 2 (empty read; missing nominal gated on text present; low OCR confidence gated on text present; rotation ambiguity). ✓
- **`DIMENSION_TYPES` exemptions** (GD&T/Position nominal "0", Note not a dimension) — Task 2 tests cover both. ✓
- **Rotation-ambiguity signal from `_best_read`** (`ROTATION_EPS`) — Task 3. ✓
- **Per-row `review_flags` call in `extract`**, GD&T branch passes `False` — Task 3. ✓
- **Manual re-read flagging in `/api/read_region`** — Task 4. ✓
- **UI: flag-driven highlight + `⚠` marker + reasons tooltip, no new CSS** — Task 5. ✓
- **No Excel change; no auto-clear on edit** — not implemented anywhere (correct per non-goals). ✓
- **Type consistency** — `review_flags(c, rotation_ambiguous)` returns `(bool, list)` and is called identically in Tasks 3 and 4; `_best_read` returns a 3-tuple consumed only inside `extract`; `needs_review`/`review_reasons` field names match across model, policy, extract, API, and UI. ✓
- **Thresholds** — `LOW_CONF = 0.6` and `ROTATION_EPS = 0.15` defined as named constants. ✓
