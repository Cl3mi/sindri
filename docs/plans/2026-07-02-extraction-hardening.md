# Extraction Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every code task is TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.

**Goal:** Make the legend (marks/notes) extraction actually produce rows, stop the notes/marks paths from fighting over the same table, improve detector recall for stacked callouts, and surface a real confidence signal — closing the issues found on `T1025300_B.pdf` (`marks: 0`, `notes: 0`, diameters under-detected).

**Architecture:** The pipeline already isolates each concern (`detect` → `place`, plus `notes_block` / `marks_block` / `title_block` side-channels). We keep that shape and make each stage more robust: a shared, format-tolerant legend parser fed by a JSON-emitting VLM prompt; an ownership rule so one legend is read once; a merge guard so distinct stacked dimensions aren't collapsed; and confidence derived from the model rather than hard-coded.

**Tech Stack:** Python 3, FastAPI, PyMuPDF, OpenCV (headless), Pillow, pytest; local Qwen2.5-VL via transformers (GPU image).

---

## Background: why these tasks

Diagnostic on `T1025300_B.pdf` (`python -m app.pipeline.diagnose ... --vlm`):

- ✅ marks region located `(4064,62,6954,1498)`; `potential_duplicates: []` — the earlier locator and cross-kind fixes work.
- ❌ `marks: 0` — the legend is located but nothing parses. Both `parse_marks_block` and `parse_notes_block` require literal-tab rows (`^(10[0-9]|…)\t…`); the VLM does not reliably emit tabs, and note 101 is a multi-line block, not a one-line bilingual row.
- ⚠️ `notes: 0` while marks located the same top-right legend — two paths target one table.
- ⚠️ 8 characteristics, `Diameter: 1` where the drawing has ~4 (Ø20/Ø15/Ø7/Ø12.8) — detector recall and/or `merge_adjacent` collapsing the stacked Ø column.

The `report["raw_reads"]` block (already shipped) will show the exact VLM output; Task 1's JSON approach is format-independent and does not depend on that evidence, but the `has_tab`/`preview` fields confirm the diagnosis.

## File Structure

- **Create** `app/pipeline/legend_parse.py` — one shared parser turning a raw legend transcription (JSON *or* tolerant text) into `[{pos, sub, en, de, raw}]`. Single responsibility: text → rows. Used by both `marks_block` and `notes_block`.
- **Modify** `app/pipeline/marks_block.py` — `parse_marks_block` delegates to `legend_parse`.
- **Modify** `app/pipeline/notes_block.py` — `parse_notes_block` delegates to `legend_parse` (keeping sub-bullet semantics).
- **Modify** `app/pipeline/ocr/vlm_backend.py` — `_NOTES_PROMPT` requests a JSON array.
- **Modify** `app/pipeline/extract.py` — deconflict notes vs marks (Task 2); wire confidence into marks/notes review (Task 4).
- **Modify** `app/pipeline/detect.py` — `merge_adjacent` gains a stack-height cap (Task 3).
- **Modify** `app/pipeline/ocr/vlm_backend.py` — real confidence from the model (Task 4).
- **Tests:** `tests/test_legend_parse.py` (new), plus additions to `tests/test_marks_block.py`, `tests/test_notes_block.py`, `tests/test_detect.py`, `tests/test_extract*.py`, `tests/test_vlm_prompt.py`.

---

## Task 1: Shared, format-tolerant legend parser (fixes `marks: 0`)

**Files:**
- Create: `app/pipeline/legend_parse.py`
- Create: `tests/test_legend_parse.py`
- Modify: `app/pipeline/marks_block.py` (`parse_marks_block`)
- Modify: `app/pipeline/ocr/vlm_backend.py:50` (`_NOTES_PROMPT`)

- [ ] **Step 1: Write the failing test** — `tests/test_legend_parse.py`

```python
from app.pipeline.legend_parse import parse_rows


def test_parses_json_array():
    raw = '[{"pos": 101, "en": "FREE OF OIL", "de": "OELFREI"}]'
    rows = parse_rows(raw)
    assert rows == [{"pos": 101, "sub": None, "en": "FREE OF OIL",
                     "de": "OELFREI", "raw": raw}]


def test_json_multiline_cell_preserved():
    raw = '[{"pos": 101, "en": "A\\nB", "de": "C\\nD"}]'
    rows = parse_rows(raw)
    assert rows[0]["en"] == "A\nB" and rows[0]["de"] == "C\nD"


def test_falls_back_to_tab_rows():
    raw = "101\tEN\tDE"
    rows = parse_rows(raw)
    assert rows[0] == {"pos": 101, "sub": None, "en": "EN", "de": "DE", "raw": "101\tEN\tDE"}


def test_falls_back_to_multispace_rows_when_no_tabs():
    raw = "101   EN TEXT   DE TEXT"
    rows = parse_rows(raw)
    assert rows[0]["pos"] == 101
    assert rows[0]["en"] == "EN TEXT"
    assert rows[0]["de"] == "DE TEXT"


def test_json_sub_bullet_carries_parent_and_index():
    raw = '[{"pos": 101, "sub": 1, "en": "x", "de": "y"}]'
    assert parse_rows(raw)[0]["sub"] == 1


def test_garbage_returns_empty():
    assert parse_rows("no rows here") == []
    assert parse_rows("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_legend_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.legend_parse'`.

- [ ] **Step 3: Write minimal implementation** — `app/pipeline/legend_parse.py`

```python
"""Shared parser for legend/notes/marks transcriptions. Accepts either a JSON
array (preferred, robust to multi-line cells) or a tolerant text form where each
row starts with a 3-digit pos followed by a tab OR 2+ spaces. Returns a list of
dicts: {"pos": int, "sub": Optional[int], "en": str, "de": str, "raw": str}."""
import json
import re

_POS_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])(?:\.(\d+))?(?:\t| {2,})(.*)$")


def _from_json(raw):
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows = []
    for it in data:
        if not isinstance(it, dict):
            continue
        try:
            pos = int(str(it.get("pos", "")).strip())
        except (TypeError, ValueError):
            continue
        sub = it.get("sub")
        try:
            sub = int(sub) if sub is not None and str(sub) != "" else None
        except (TypeError, ValueError):
            sub = None
        rows.append({
            "pos": pos, "sub": sub,
            "en": str(it.get("en", "")).strip(),
            "de": str(it.get("de", "")).strip(),
            "raw": raw,
        })
    return rows


def _from_text(raw):
    rows = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _POS_RE.match(line)
        if not m:
            continue
        rest = m.group(3)
        parts = re.split(r"\t| {2,}", rest, maxsplit=1)
        en = parts[0].strip()
        de = parts[1].strip() if len(parts) > 1 else ""
        rows.append({
            "pos": int(m.group(1)),
            "sub": int(m.group(2)) if m.group(2) else None,
            "en": en, "de": de, "raw": line,
        })
    return rows


def parse_rows(raw: str):
    raw = raw or ""
    rows = _from_json(raw)
    if rows is None:
        rows = _from_text(raw)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_legend_parse.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Point `parse_marks_block` at the shared parser** — `app/pipeline/marks_block.py`

Replace the body of `parse_marks_block` (drop the local `_ROW_RE`/loop) with:

```python
from app.pipeline.legend_parse import parse_rows


def parse_marks_block(raw: str, region: Tuple[float, float, float, float]) -> MarkBlock:
    """Parse a marks transcription into a MarkBlock. Sub-bullets are not valid in
    a marks legend, so rows carrying a sub-index are dropped."""
    marks: List[Mark] = []
    for r in parse_rows(raw):
        if r["sub"] is not None:
            continue
        marks.append(Mark(pos=r["pos"], text_en=r["en"], text_de=r["de"],
                          raw_text=r["raw"]))
    return MarkBlock(region=region, marks=marks)
```

- [ ] **Step 6: Update the existing marks parser tests for the shared `raw`**

In `tests/test_marks_block.py`, `test_parses_top_level_bilingual_row` currently asserts `m.raw_text == raw` for a single-line input — that still holds (single line ⇒ `raw` is that line). Run the file and fix only assertions that assumed the old per-line `raw_text` on multi-line JSON inputs (there are none today). 

Run: `python -m pytest tests/test_marks_block.py -v`
Expected: PASS.

- [ ] **Step 7: Switch the VLM notes prompt to JSON** — `app/pipeline/ocr/vlm_backend.py:50`

```python
_NOTES_PROMPT = (
    "This image is the general-notes / mark-legend table from a mechanical "
    "engineering drawing. Each row starts with a 3-digit number (101, 102, …) "
    "and contains an English text and a German text; a single cell may span "
    "several lines. Return ONLY a JSON array, one object per row, of the form "
    '[{"pos": 101, "en": "<english>", "de": "<german>"}]. For an inline '
    'numbered sub-bullet, add "sub": <n>. If a row has no German text use an '
    "empty string. Use a comma as the decimal separator. No prose, no code "
    "fences, no trailing text."
)
```

- [ ] **Step 8: Update the prompt test** — `tests/test_vlm_prompt.py`

Find the assertion pinning the old tab-format wording for the notes prompt and replace it with the JSON contract:

```python
def test_notes_prompt_requests_json_array():
    from app.pipeline.ocr.vlm_backend import _NOTES_PROMPT
    assert "JSON array" in _NOTES_PROMPT
    assert '"pos"' in _NOTES_PROMPT
```

Run: `python -m pytest tests/test_vlm_prompt.py -v`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add app/pipeline/legend_parse.py tests/test_legend_parse.py \
        app/pipeline/marks_block.py tests/test_marks_block.py \
        app/pipeline/ocr/vlm_backend.py tests/test_vlm_prompt.py
git commit -m "feat(legend): JSON-first tolerant legend parser + JSON notes prompt"
```

---

## Task 2: Route the notes parser through the shared parser (hardens `notes: 0`)

**Files:**
- Modify: `app/pipeline/notes_block.py` (`parse_notes_block`)
- Modify: `tests/test_notes_block.py`

- [ ] **Step 1: Write the failing test** — `tests/test_notes_block.py`

```python
def test_notes_parses_json_with_sub_bullet():
    from app.pipeline.notes_block import parse_notes_block
    raw = '[{"pos":101,"en":"parent","de":"eltern"},{"pos":101,"sub":1,"en":"child","de":"kind"}]'
    block = parse_notes_block(raw, region=(0, 0, 100, 100))
    top = [n for n in block.notes if n.sub_index is None]
    subs = [n for n in block.notes if n.sub_index is not None]
    assert [n.pos for n in top] == [101]
    assert subs and subs[0].parent_pos == 101 and subs[0].sub_index == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notes_block.py::test_notes_parses_json_with_sub_bullet -v`
Expected: FAIL (JSON input yields no notes under the current tab-only parser).

- [ ] **Step 3: Reimplement `parse_notes_block` over `parse_rows`** — `app/pipeline/notes_block.py`

```python
from app.pipeline.legend_parse import parse_rows


def parse_notes_block(raw: str, region: Tuple[float, float, float, float]) -> NoteBlock:
    """Parse a notes transcription into a NoteBlock. Rows with a sub-index become
    child notes carrying parent_pos + sub_index."""
    notes: List[Note] = []
    for r in parse_rows(raw):
        if r["sub"] is None:
            notes.append(Note(pos=r["pos"], text_en=r["en"], text_de=r["de"],
                              raw_text=r["raw"]))
        else:
            notes.append(Note(pos=r["sub"], parent_pos=r["pos"], sub_index=r["sub"],
                              text_en=r["en"], text_de=r["de"], raw_text=r["raw"]))
    return NoteBlock(region=region, notes=notes)
```

- [ ] **Step 4: Run the notes tests**

Run: `python -m pytest tests/test_notes_block.py -v`
Expected: PASS. Fix any legacy assertion that hard-coded the old `raw_text` line format for multi-line JSON (single-line legacy inputs are unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/notes_block.py tests/test_notes_block.py
git commit -m "feat(notes): parse notes block via shared JSON/tolerant parser"
```

---

## Task 3: Stop one legend being owned by both notes and marks

**Files:**
- Modify: `app/pipeline/extract.py:96-112`
- Test: `tests/test_extract_notes_integration.py`

- [ ] **Step 1: Write the failing test** — `tests/test_extract_notes_integration.py`

```python
def test_marks_and_notes_do_not_both_claim_one_legend(tmp_path, monkeypatch):
    # When the CV marks locator and the VLM notes locator find the SAME region,
    # only one path keeps it (marks wins — deterministic CV locator).
    from app.pipeline import extract as ex
    from app.pipeline.marks_block import MarksBlockRegion
    from app.pipeline.notes_block import NotesBlockRegion

    box = (4000, 60, 6900, 1500)
    monkeypatch.setattr(ex.mb, "locate_marks_block",
                        lambda img: MarksBlockRegion(outer_box=box, lang_columns=[(4000, 6900)]))
    monkeypatch.setattr(ex.nb, "locate_notes_block",
                        lambda img, backend: NotesBlockRegion(outer_box=box, lang_columns=[(4000, 6900)]))
    assert ex._regions_overlap(box, box) is True
```

(If `NotesBlockRegion`'s constructor differs, match its real fields — check `app/pipeline/notes_block.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_notes_integration.py::test_marks_and_notes_do_not_both_claim_one_legend -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_regions_overlap'`.

- [ ] **Step 3: Add the overlap helper and ownership rule** — `app/pipeline/extract.py`

Add near the other helpers:

```python
def _regions_overlap(a, b, min_frac: float = 0.5) -> bool:
    """True if boxes a and b overlap by at least `min_frac` of the smaller area."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return smaller > 0 and inter / smaller >= min_frac
```

Then, right after both `region` (notes) and `region_marks` are computed, drop the notes region if it coincides with the marks region:

```python
    if (region is not None and region_marks is not None
            and _regions_overlap(region.outer_box, region_marks.outer_box)):
        # One physical legend: the deterministic CV marks locator owns it.
        region = None
        notes_obj = None
```

Place this before the masking block so the notes region is no longer masked/read.

- [ ] **Step 4: Run the extract tests**

Run: `python -m pytest tests/test_extract_notes_integration.py tests/test_pipeline_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/extract.py tests/test_extract_notes_integration.py
git commit -m "fix(extract): a single legend is owned by marks, not both paths"
```

---

## Task 4: Cap `merge_adjacent` so stacked distinct dimensions aren't collapsed

**Files:**
- Modify: `app/pipeline/detect.py:54-80` (`merge_adjacent`)
- Test: `tests/test_detect.py`

- [ ] **Step 1: Write the failing test** — `tests/test_detect.py`

```python
def test_merge_adjacent_does_not_collapse_three_stacked_dims():
    # A column of three distinct same-kind callouts must not become one box.
    a = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.9)
    b = Detection(box=(10, 45, 50, 65), kind="dimension", conf=0.9)
    c = Detection(box=(10, 80, 50, 100), kind="dimension", conf=0.9)
    merged = merge_adjacent([a, b, c], x_tol=20, y_gap=20)
    assert len(merged) >= 2   # not all three fused into one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detect.py::test_merge_adjacent_does_not_collapse_three_stacked_dims -v`
Expected: FAIL — currently all three merge into one (`len == 1`).

- [ ] **Step 3: Add the stack-height cap** — `app/pipeline/detect.py`

```python
import statistics


def merge_adjacent(detections, x_tol: int = 20, y_gap: int = 20, max_lines: int = 2):
    """Merge same-kind boxes that are horizontally aligned and vertically close,
    so a stacked callout (tolerance over a nominal) becomes one crop. A merge is
    rejected if the resulting box would be taller than `max_lines` typical rows —
    this stops a column of distinct stacked dimensions (e.g. Ø20/Ø15/Ø7) from
    collapsing into a single detection."""
    items = list(detections)
    if not items:
        return []
    line_h = statistics.median(b.box[3] - b.box[1] for b in items)
    cap = max_lines * line_h + (max_lines - 1) * y_gap
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
                    u = _union(a.box, b.box)
                    if (u[3] - u[1]) > cap:
                        continue
                    a = Detection(box=u, kind=a.kind, conf=max(a.conf, b.conf))
                    used[j] = True
                    changed = True
            out.append(a)
        items = out
    return items
```

- [ ] **Step 4: Run detect tests**

Run: `python -m pytest tests/test_detect.py -v`
Expected: PASS — including the existing `test_merge_adjacent_combines_vertically_stacked_same_kind` (two boxes, union height 45 ≤ cap = 2·20 + 20 = 60).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/detect.py tests/test_detect.py
git commit -m "fix(detect): cap merge_adjacent height so stacked dims stay distinct"
```

---

## Task 5: Real confidence from the VLM read

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py` (`read_region`, `read_notes_block`, `read_region_gdt`, `read_title_cell`)
- Test: `tests/test_vlm_prompt.py` (or a new `tests/test_vlm_confidence.py`)

- [ ] **Step 1: Write the failing test** — `tests/test_vlm_confidence.py`

```python
from app.pipeline.ocr.vlm_backend import _mean_token_confidence


def test_mean_token_confidence_high_for_confident_scores():
    # scores: list of per-step max softmax probabilities
    assert _mean_token_confidence([0.99, 0.98, 0.97]) > 0.9


def test_mean_token_confidence_low_for_uncertain_scores():
    assert _mean_token_confidence([0.4, 0.5, 0.3]) < 0.6


def test_mean_token_confidence_empty_is_zero():
    assert _mean_token_confidence([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vlm_confidence.py -v`
Expected: FAIL — `ImportError: cannot import name '_mean_token_confidence'`.

- [ ] **Step 3: Implement confidence from generation scores** — `app/pipeline/ocr/vlm_backend.py`

Add the pure helper (module level):

```python
def _mean_token_confidence(step_probs) -> float:
    """Mean of per-token max-softmax probabilities; 0.0 for an empty sequence."""
    probs = list(step_probs)
    return float(sum(probs) / len(probs)) if probs else 0.0
```

Then in each read method, request scores and derive confidence. For `read_region` (repeat the pattern in the other read methods):

```python
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False,
                output_scores=True, return_dict_in_generate=True,
            )
        seq = out.sequences[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(seq, skip_special_tokens=True).strip()
        step_probs = [float(self.torch.softmax(s[0], dim=-1).max()) for s in out.scores]
        return OcrResult(text=text, confidence=_mean_token_confidence(step_probs) if text else 0.0)
```

- [ ] **Step 4: Run the confidence tests**

Run: `python -m pytest tests/test_vlm_confidence.py -v`
Expected: PASS. (The `generate(...)` change is exercised only on GPU; verify end-to-end with `./test.sh` — see below.)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py tests/test_vlm_confidence.py
git commit -m "feat(vlm): derive read confidence from token probabilities"
```

---

## Verification (on the GPU box, via test.sh)

After Tasks 1–5 are pushed:

```bash
BUILD=1 MODE=vlm ./test.sh > /tmp/diag.json 2> /tmp/diag.log
```

Expected in `/tmp/diag.json`:
- `raw_reads.marks.preview` shows a JSON array; `extraction.marks` ≈ **5** (was 0).
- `extraction.notes` is `null` (Task 3 hands the single legend to marks) or a real count if a separate notes table exists.
- `extraction.by_char_type.Diameter` closer to the true count (Task 4), `potential_duplicates: []` still.
- Some characteristics carry `needs_review: true` where confidence is low (Task 5).

Also open `test_docs/diag/T1025300_B_diag.png` (orange = detected callout boxes) to confirm which diameters, if any, are still missed — that decides whether the follow-up below is needed.

## Follow-up (not scheduled — gated on the annotated image)

If diameters are still missing *after* Task 4, the gap is detector **recall**, not merging. Options, cheapest first: (a) shrink `tile` / raise `overlap` in `detect_characteristics`; (b) strengthen the detector `_PROMPT` to explicitly enumerate Ø/R/linear callouts; (c) evaluate a larger VLM (`Qwen2.5-VL-32B-Instruct`) behind `VLM_MODEL_ID`. Each is a small, separately-tested change; pick based on the annotated image.

---

## Self-Review

- **Spec coverage:** `marks: 0` → Task 1 (+2). `notes: 0` / dual ownership → Task 3. Diameter under-detection → Task 4 (+ follow-up). Hard-coded confidence → Task 5. Diagnostic evidence → already shipped (`raw_reads`) + `test.sh` verification section. ✅
- **Type consistency:** `parse_rows` returns `{"pos","sub","en","de","raw"}` everywhere; `parse_marks_block`/`parse_notes_block` consume exactly those keys; `Note(pos, parent_pos, sub_index, text_en, text_de, raw_text)` and `Mark(pos, text_en, text_de, raw_text)` match `app/models.py`. `_regions_overlap`, `_mean_token_confidence`, `merge_adjacent(..., max_lines=2)` names are used consistently. ✅
- **Placeholder scan:** every code step contains complete code and an exact command with expected output. Task 3's `NotesBlockRegion` field note tells the implementer to confirm the constructor — verify against `app/pipeline/notes_block.py` before writing. ✅
