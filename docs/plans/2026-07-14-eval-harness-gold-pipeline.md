# Eval Harness + Gold-Data Pipeline Implementation Plan (Rung 0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every code task is TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.

**Spec:** `docs/plans/2026-07-14-extraction-quality-optimization-handoff.md` (all § references below point there).

**Goal:** Build the evaluation harness and gold-data pipeline that turns the client's ~100 ballooned PDFs + Excel sheets into a review-cost metric with confidence intervals, so every later optimization (prompt, knob, LoRA) is measured against the same frozen, comparable baseline.

**Architecture:** A new pure-Python package `app/eval/` (no GPU, no model imports at score time). All geometry is stored in **PDF points** (dpi-independent) so gold and predictions from any render resolution compare identically. Predictions are serialized once per run as versioned JSON dumps carrying a full config fingerprint; scoring and comparison run anywhere from those dumps. Everything is TDD-able **today** — before the client data arrives — via a synthetic-corpus builder that manufactures ballooned PDFs + Excel sheets with perfectly known truth.

**Tech Stack:** Python 3, PyMuPDF (already a dep — vector balloon recovery), openpyxl (already a dep — gold Excel), pydantic (already a dep — versioned schemas), pytest. **Zero new dependencies**; the matcher is a deterministic pure-Python greedy assignment.

---

## Comparability contract (the explicit requirement)

Every design choice below serves one requirement: **any two runs, made weeks apart, must be legitimately comparable.** The harness enforces this mechanically, not by convention:

1. **Versioned artifacts.** Gold docs, prediction dumps, splits, and reports all carry `schema_version`. Loaders reject unknown versions instead of misreading them.
2. **Config fingerprint in every dump.** Model ID, dpi, git SHA, and a SHA-256 over the five VLM prompt strings are captured at predict time and travel with the predictions. A report always says exactly what produced it.
3. **Gold hash.** Each `GoldDoc` gets a content hash; every `DocScore` records the hash of the gold it was scored against. `compare` **refuses** (raises) to compare runs scored against different gold, different doc sets, or different weights.
4. **dpi-independent geometry.** Gold positions and matching distances live in PDF points. Changing render dpi (a Rung-1 knob!) cannot silently shift the metric.
5. **Deterministic everywhere.** Greedy matching with total-order tie-breaking; bootstrap with a fixed seed; splits derived from a seeded shuffle committed to the repo. Same inputs → byte-identical reports.
6. **Frozen test split.** `docs/eval/splits.json` is committed once; variant drawings are forced into `test`; the scorer tags every report with the split-file hash. Tuning happens on `dev` only.
7. **Paired comparison + bootstrap CIs.** `compare` computes per-document deltas on the intersection-checked doc set and a 10,000-resample bootstrap CI on the mean delta. A delta whose CI includes 0 is reported as *not significant* — 100 docs is small; without this we ship noise.
8. **Regression guard.** `compare` automatically warns when the headline review-cost improves while recall drops or the escaped-error rate rises — the exact failure mode §6 of the handoff calls out.

## Background: coordinate systems (read before Task 1)

- `render.render_page(pdf, dpi)` returns `RenderResult(scale=dpi/72, page_rect=(x0,y0,x1,y1) in points)`. All pipeline boxes (`Characteristic.target_region`, `balloon_xy`) are **image pixels at that dpi**.
- Conversion pixel→point: `pt = page_rect[0] + px / scale` (and same for y). The dump stores `scale` + `page_rect` so the scorer can convert without re-rendering.
- Client balloons are recovered directly from the PDF **in points** (PyMuPDF `get_drawings` / `get_text` operate in point space). No conversion needed on the gold side.

## Objective function (from handoff §4)

`review_cost = w_miss·missed + w_escaped·escaped_errors + w_flag·flagged_rows + w_false·false_detections`

Default weights `miss=10, escaped=5, false=2, flag=1` (configurable; Task 13 replaces defaults with client-sourced estimates). A *flagged* row costs `w_flag` whether its value is right or wrong — the reviewer looks at it either way. Only **unflagged** wrong rows count as escaped.

## File structure

- **Create** `app/eval/__init__.py` — empty.
- **Create** `app/eval/models.py` — all versioned pydantic schemas: `GoldCharacteristic`, `GoldDoc`, `RunConfig`, `PredictionDump`, `ReviewCostWeights`, `MatchParams`, `MatchedPair`, `DocScore`, `RunReport`.
- **Create** `app/eval/normalize.py` — canonical value comparison (comma/period decimals, trailing zeros) + char_type synonym mapping. The single home for "what counts as equal".
- **Create** `app/eval/balloons.py` — recover `(balloon_number, center_pt)` from client vector PDFs + a probe reporting per-file encoding stats (answers the vector-vs-raster open question).
- **Create** `app/eval/excel_gold.py` — schema-adapter Excel reader (column aliases, header auto-detect) + a header-dump helper for day-one inspection.
- **Create** `app/eval/synthetic.py` — build a synthetic gold corpus (ballooned PDF + matching xlsx) from known records; the test bed for everything else.
- **Create** `app/eval/ingest.py` — join balloons + Excel rows → `GoldDoc` JSON with provenance/join-rate stats.
- **Create** `app/eval/matching.py` — deterministic greedy bipartite matcher (geometry + value similarity, gated).
- **Create** `app/eval/score.py` — per-document review-cost scoring + error-taxonomy tagging.
- **Create** `app/eval/splits.py` — seeded document-level train/dev/test split, variants forced into test.
- **Create** `app/eval/report.py` — run aggregation, paired bootstrap CIs, comparability guards, regression guard.
- **Create** `app/eval/dump.py` — save/load prediction dumps; px→pt conversion helper.
- **Create** `app/eval/runner.py` — CLI (`python -m app.eval.runner predict|score|compare|probe|headers|split`).
- **Create** `tests/eval/__init__.py`, `tests/eval/test_normalize.py`, `test_balloons.py`, `test_excel_gold.py`, `test_synthetic.py`, `test_ingest.py`, `test_matching.py`, `test_score.py`, `test_splits.py`, `test_report.py`, `test_dump.py`, `test_runner_e2e.py`.
- **Reuse, don't duplicate:** `app/pipeline/geom.py` for IoU if needed, `app/pipeline/render.py` for scale math, `diagnose.summarize_result` stays the per-drawing debugging tool; the harness is corpus-level.

## Data layout (created by tasks, gitignored where client data lives)

```
eval_data/                  # gitignored — client data + run artifacts
  pdfs/<doc_id>.pdf         # client ballooned PDFs
  excel/<doc_id>.xlsx       # client gold sheets (paired by file stem)
  gold/<doc_id>.gold.json   # ingested GoldDoc records
  runs/<run_name>/<doc_id>.pred.json   # prediction dumps
  reports/<run_name>.report.json
docs/eval/
  splits.json               # committed, frozen
  baseline-report.json      # committed after Task 14 (headline numbers only)
```

---

## Task 1: Versioned eval schemas (`app/eval/models.py`)

**Files:**
- Create: `app/eval/__init__.py` (empty)
- Create: `app/eval/models.py`
- Create: `tests/eval/__init__.py` (empty)
- Create: `tests/eval/test_models.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_models.py`

```python
import pytest
from app.eval.models import (
    GoldCharacteristic, GoldDoc, RunConfig, PredictionDump,
    ReviewCostWeights, DocScore, RunReport, SCHEMA_VERSION,
)
from app.models import ExtractionResult


def _gold_doc():
    return GoldDoc(
        doc_id="T1", pdf="a.pdf", excel="a.xlsx",
        page_rect=(0.0, 0.0, 1189.0, 841.0),
        characteristics=[GoldCharacteristic(
            balloon=1, position_pt=(100.0, 200.0),
            char_type="Diameter", nominal="20", upper_tol="0,1", lower_tol="-0,1",
        )],
    )


def test_gold_doc_roundtrips_and_carries_schema_version():
    g = _gold_doc()
    assert g.schema_version == SCHEMA_VERSION
    g2 = GoldDoc.model_validate_json(g.model_dump_json())
    assert g2 == g


def test_gold_hash_is_stable_and_ignores_provenance():
    a, b = _gold_doc(), _gold_doc()
    b.provenance = {"n_circles": 99}
    assert a.gold_hash() == b.gold_hash()
    b.characteristics[0].nominal = "21"
    assert a.gold_hash() != b.gold_hash()


def test_prediction_dump_wraps_extraction_result():
    d = PredictionDump(
        doc_id="T1",
        config=RunConfig(model_id="stub", dpi=300),
        scale=300 / 72.0, page_rect=(0.0, 0.0, 1189.0, 841.0),
        result=ExtractionResult(characteristics=[]),
    )
    d2 = PredictionDump.model_validate_json(d.model_dump_json())
    assert d2.config.model_id == "stub"
    assert d2.schema_version == SCHEMA_VERSION


def test_unknown_schema_version_rejected():
    g = _gold_doc()
    payload = g.model_dump()
    payload["schema_version"] = 999
    with pytest.raises(ValueError):
        GoldDoc.model_validate(payload)


def test_default_weights_ordering():
    w = ReviewCostWeights()
    assert w.miss > w.escaped > w.false > w.flag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.eval'`

- [ ] **Step 3: Write the implementation** — `app/eval/models.py`

```python
"""Versioned schemas for the eval harness. Everything that is written to disk
(gold docs, prediction dumps, scores, reports) lives here and carries
`schema_version` so old artifacts are rejected loudly, never misread.

Geometry convention: ALL positions/boxes in these models are PDF points
(dpi-independent). Conversion from render pixels happens exactly once, in
`app.eval.dump.to_points`.
"""
import hashlib
import json
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, field_validator

from app.models import ExtractionResult

SCHEMA_VERSION = 1


class _Versioned(BaseModel):
    schema_version: int = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v):
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {v} (this code reads {SCHEMA_VERSION})")
        return v


class GoldCharacteristic(BaseModel):
    balloon: int                                # client's balloon number
    position_pt: Tuple[float, float]            # balloon center, PDF points
    char_type: str = ""
    nominal: str = ""
    upper_tol: str = ""
    lower_tol: str = ""
    raw: str = ""                               # optional free-text from Excel


class GoldDoc(_Versioned):
    doc_id: str
    pdf: str
    excel: str
    page_rect: Tuple[float, float, float, float]   # PDF points
    characteristics: List[GoldCharacteristic] = []
    is_variant: bool = False
    provenance: Dict = {}    # join stats, balloon-recovery stats — NOT hashed

    def gold_hash(self) -> str:
        """Content hash of everything that affects scoring (provenance excluded)."""
        payload = self.model_dump(exclude={"provenance"})
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class RunConfig(BaseModel):
    """Fingerprint of what produced a prediction dump — captured at predict
    time on the GPU box, never reconstructed later."""
    model_id: str = ""
    dpi: int = 300
    git_sha: str = "unknown"
    prompt_sha256: str = "unavailable"
    extra: Dict = {}          # tuned knobs, few-shot bank id, adapter id, ...


class PredictionDump(_Versioned):
    doc_id: str
    config: RunConfig
    scale: float                                   # render pixels per PDF point
    page_rect: Tuple[float, float, float, float]
    result: ExtractionResult


class ReviewCostWeights(BaseModel):
    """Handoff §4: w_miss >> w_escaped > w_false > w_flag. Replace defaults with
    client-sourced estimates in Task 13; reports embed the weights used."""
    miss: float = 10.0
    escaped: float = 5.0
    false: float = 2.0
    flag: float = 1.0


class MatchParams(BaseModel):
    max_geo_frac: float = 0.10        # match gate: center distance / page diagonal
    value_bonus: float = 0.35         # cost reduction when nominals agree
    misplaced_frac: float = 0.04      # matched farther than this → tagged misplaced


class MatchedPair(BaseModel):
    gold_balloon: int
    pred_pos: int
    distance_frac: float
    fields_correct: bool
    field_errors: List[str] = []      # e.g. ["nominal: '20'!='28'"]
    flagged: bool = False
    taxonomy: str = ""                # correct|flagged_correct|flagged_error|
                                      # escaped_error (+ cause/misplaced tags in notes)
    notes: List[str] = []


class DocScore(_Versioned):
    doc_id: str
    gold_hash: str
    n_gold: int
    n_pred: int
    pairs: List[MatchedPair] = []
    missed_balloons: List[int] = []
    false_positions: List[int] = []   # pred.pos of unmatched predictions
    counts: Dict[str, int] = {}       # taxonomy histogram
    review_cost: float = 0.0
    recall: float = 0.0
    precision: float = 0.0
    escaped_rate: float = 0.0


class RunReport(_Versioned):
    run_name: str
    config: RunConfig
    weights: ReviewCostWeights
    match_params: MatchParams
    splits_hash: str = ""
    split_used: str = ""              # "dev" | "test" | "all"
    doc_scores: List[DocScore] = []
    mean_review_cost: float = 0.0
    micro_recall: float = 0.0
    micro_precision: float = 0.0
    escaped_rate: float = 0.0
    taxonomy: Dict[str, int] = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_models.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/__init__.py app/eval/models.py tests/eval/__init__.py tests/eval/test_models.py
git commit -m "feat(eval): versioned schemas for gold docs, prediction dumps, scores"
```

---

## Task 2: Value normalization — the single definition of "equal" (`app/eval/normalize.py`)

The gold Excel will contain `1,2`, our parser emits `1,2`; but Excel numeric cells load as float `1.2`, and `1.20` must equal `1,2`. This module is the **only** place that decides equality; matching and scoring both import it. Defaults are the best guess for the Intercable house style; Task 13 confirms them against real data.

**Files:**
- Create: `app/eval/normalize.py`
- Create: `tests/eval/test_normalize.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_normalize.py`

```python
from app.eval.normalize import canon_value, values_equal, char_type_equal


def test_canon_value_normalizes_decimal_comma_and_trailing_zeros():
    assert canon_value("1,20") == canon_value("1.2") == "1.2"
    assert canon_value("+0,1") == "0.1"
    assert canon_value("-0,10") == "-0.1"
    assert canon_value(1.2) == "1.2"          # Excel float cell
    assert canon_value(20) == "20"            # Excel int cell
    assert canon_value(" Ø ") == "ø"          # non-numeric: casefolded, stripped


def test_values_equal_numeric_and_string_paths():
    assert values_equal("1,2", "1.20")
    assert values_equal("-0,05", -0.05)
    assert not values_equal("1,2", "1,3")
    assert values_equal("MAX", " max ")
    assert not values_equal("", "0")          # empty is NOT zero (policy)
    assert values_equal("", "")
    assert values_equal("", None)


def test_char_type_equal_uses_synonyms_case_insensitively():
    assert char_type_equal("Diameter", "durchmesser")
    assert char_type_equal("Distance", "Maß")
    assert char_type_equal("Radius", "Radius")
    assert not char_type_equal("Radius", "Diameter")
    assert char_type_equal("", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.normalize`)

- [ ] **Step 3: Write the implementation** — `app/eval/normalize.py`

```python
"""Canonical value comparison for gold vs prediction. The ONLY place equality
is defined — matching, scoring, and taxonomy all import from here, so a policy
change (Task 13, after inspecting real Excel conventions) is one edit.

Policy defaults:
- numbers compare numerically: '1,20' == '1.2' == 1.2 (Excel float cell)
- empty != '0' (an absent tolerance is not a zero tolerance)
- non-numbers compare casefolded + whitespace-collapsed
- char_type compares through a synonym map (German gold labels -> parser
  constants); unknown labels compare as plain strings
"""
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

# Gold-sheet label -> parser.py char_type constant. Extend in Task 13 from the
# real Excel vocabulary; keys and values are matched casefolded.
CHAR_TYPE_SYNONYMS: Dict[str, str] = {
    "durchmesser": "Diameter",
    "diameter": "Diameter",
    "radius": "Radius",
    "mass": "Distance",
    "maß": "Distance",
    "abstand": "Distance",
    "distance": "Distance",
    "länge": "Distance",
    "ebenheit": "Flatness",
    "flatness": "Flatness",
    "position": "Position",
    "werkstoff": "Material",
    "material": "Material",
    "note": "Note",
    "hinweis": "Note",
    "theoretical": "Theoretical",
    "theoretisch": "Theoretical",
    "reference": "Reference",
    "klammermass": "Reference",
    "klammermaß": "Reference",
}


def _try_decimal(s: str) -> Optional[Decimal]:
    t = s.strip().replace(",", ".").lstrip("+")
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def canon_value(v) -> str:
    """Canonical string form: numeric values via Decimal (trailing zeros
    stripped, comma/period unified), everything else casefolded/stripped."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v)
    d = _try_decimal(s)
    if d is not None:
        d = d.normalize()
        # Decimal('20').normalize() -> '2E+1'; re-quantize integers
        if d == d.to_integral_value():
            d = d.quantize(Decimal(1))
        return str(d)
    return " ".join(s.split()).casefold()


def values_equal(a, b) -> bool:
    return canon_value(a) == canon_value(b)


def _canon_char_type(v) -> str:
    key = " ".join(str(v or "").split()).casefold()
    return CHAR_TYPE_SYNONYMS.get(key, key)


def char_type_equal(a, b) -> bool:
    return _canon_char_type(a) == _canon_char_type(b)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_normalize.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/normalize.py tests/eval/test_normalize.py
git commit -m "feat(eval): canonical value/char_type equality for gold comparison"
```

---

## Task 3: Client balloon recovery from vector PDFs (`app/eval/balloons.py`)

Recovers `(balloon_number, center_pt)` from a client ballooned PDF and — because the encoding is an open question (§3 of the handoff) — also exposes `probe_pdf` reporting what the file actually contains (vector circles? digit words? raster images?). The recovery test builds its own tiny vector PDF with PyMuPDF, so this is fully testable before client data arrives.

**Files:**
- Create: `app/eval/balloons.py`
- Create: `tests/eval/test_balloons.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_balloons.py`

```python
import fitz
import pytest

from app.eval.balloons import recover_balloons, probe_pdf


@pytest.fixture
def ballooned_pdf(tmp_path):
    """A minimal vector 'client' page: three circled numbers + decoy content."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    for num, (x, y) in [(1, (100, 100)), (2, (300, 150)), (12, (500, 300))]:
        page.draw_circle(fitz.Point(x, y), 9.0, color=(0, 0, 1), width=1.5)
        page.insert_text(fitz.Point(x - 5, y + 4), str(num), fontsize=10)
    # decoys: a big circle (not a balloon), loose text, a rectangle
    page.draw_circle(fitz.Point(300, 300), 60.0, color=(0, 0, 0), width=1.0)
    page.insert_text(fitz.Point(50, 350), "20 +0,1", fontsize=10)
    page.draw_rect(fitz.Rect(10, 10, 590, 390), color=(0, 0, 0), width=0.5)
    path = tmp_path / "client.pdf"
    doc.save(path)
    doc.close()
    return path


def test_recovers_all_numbered_balloons(ballooned_pdf):
    balloons = recover_balloons(ballooned_pdf)
    by_num = {b.number: b for b in balloons}
    assert set(by_num) == {1, 2, 12}
    bx, by = by_num[1].center_pt
    assert abs(bx - 100) < 3 and abs(by - 100) < 3


def test_ignores_oversized_circles_and_loose_text(ballooned_pdf):
    balloons = recover_balloons(ballooned_pdf)
    assert len(balloons) == 3            # decoy circle + '20 +0,1' not recovered


def test_probe_reports_encoding_facts(ballooned_pdf):
    p = probe_pdf(ballooned_pdf)
    assert p["n_balloons"] == 3
    assert p["n_circles"] >= 3
    assert p["has_images"] is False
    assert p["numbers"] == [1, 2, 12]
    assert p["duplicate_numbers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_balloons.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.balloons`)

- [ ] **Step 3: Write the implementation** — `app/eval/balloons.py`

```python
"""Recover the client's balloons — (number, center) — from a ballooned PDF.

Vector-first strategy (open question §3 of the handoff): client balloons are
expected to be vector circles (bezier 'c' items in get_drawings()) with a
digit-only text span centered inside. `probe_pdf` reports what a file actually
contains so day-one inspection (Task 13) can confirm or refute this per corpus;
raster-stamped balloons show up as n_circles==0 / has_images==True and would
need a raster detector (separate task, only if the probe demands it).

All coordinates are PDF points (native PyMuPDF space).
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List

import fitz

# Balloon radius window in points. Our own balloons are 9pt (ballooned_pdf.py);
# client balloons should be the same order of magnitude. Tune from probe output.
MIN_R_PT = 4.0
MAX_R_PT = 24.0


@dataclass(frozen=True)
class Balloon:
    number: int
    center_pt: tuple      # (x, y) in PDF points
    radius_pt: float


def _circle_rects(page) -> List[fitz.Rect]:
    """Rects of drawings that look like balloon circles: curve-based, roughly
    square, diameter within the balloon window."""
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if not any(item[0] == "c" for item in d["items"]):
            continue                     # rectangles/lines have no curves
        if abs(r.width - r.height) > max(r.width, r.height) * 0.25:
            continue                     # not circle-ish
        if not (MIN_R_PT * 2 <= r.width <= MAX_R_PT * 2):
            continue
        out.append(r)
    return out


def recover_balloons(pdf_path, page_index: int = 0) -> List[Balloon]:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        circles = _circle_rects(page)
        words = page.get_text("words")   # (x0, y0, x1, y1, text, ...)
        balloons = []
        for r in circles:
            inside = [w for w in words
                      if w[4].strip().isdigit()
                      and r.contains(fitz.Point((w[0] + w[2]) / 2,
                                                (w[1] + w[3]) / 2))]
            if not inside:
                continue
            # multi-word numbers (rare glyph splits): join left-to-right
            inside.sort(key=lambda w: w[0])
            number = int("".join(w[4].strip() for w in inside))
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            balloons.append(Balloon(number=number, center_pt=(cx, cy),
                                    radius_pt=r.width / 2))
        # dedupe identical (number, ~center) from doubled vector strokes
        seen, unique = set(), []
        for b in balloons:
            key = (b.number, round(b.center_pt[0]), round(b.center_pt[1]))
            if key not in seen:
                seen.add(key)
                unique.append(b)
        return unique
    finally:
        doc.close()


def probe_pdf(pdf_path, page_index: int = 0) -> dict:
    """Day-one encoding inspection for one client PDF. Cheap, no model."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        circles = _circle_rects(page)
        balloons = recover_balloons(pdf_path, page_index)
        numbers = sorted(b.number for b in balloons)
        dupes = sorted({n for n in numbers if numbers.count(n) > 1})
        return {
            "pdf": str(Path(pdf_path).name),
            "n_drawings": len(page.get_drawings()),
            "n_circles": len(circles),
            "n_words": len(page.get_text("words")),
            "has_images": len(page.get_images()) > 0,
            "n_balloons": len(balloons),
            "numbers": numbers,
            "duplicate_numbers": dupes,
            "gaps": [n for n in range(1, max(numbers) + 1)
                     if n not in numbers] if numbers else [],
        }
    finally:
        doc.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_balloons.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/balloons.py tests/eval/test_balloons.py
git commit -m "feat(eval): vector balloon recovery + per-PDF encoding probe"
```

---

## Task 4: Synthetic gold corpus builder (`app/eval/synthetic.py`)

Manufactures a "client deliverable" — ballooned PDF + gold Excel — from records we define, so ingestion, matching, and scoring are proven end-to-end on perfectly known truth **before** the real corpus lands. Also the regression bed forever after (no client data in the repo).

**Files:**
- Create: `app/eval/synthetic.py`
- Create: `tests/eval/test_synthetic.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_synthetic.py`

```python
import fitz
from openpyxl import load_workbook

from app.eval.models import GoldCharacteristic
from app.eval.synthetic import make_synthetic_doc
from app.eval.balloons import recover_balloons

RECORDS = [
    GoldCharacteristic(balloon=1, position_pt=(120.0, 90.0),
                       char_type="Diameter", nominal="20",
                       upper_tol="0,1", lower_tol="-0,1"),
    GoldCharacteristic(balloon=2, position_pt=(340.0, 200.0),
                       char_type="Distance", nominal="5,5"),
    GoldCharacteristic(balloon=3, position_pt=(500.0, 320.0),
                       char_type="Radius", nominal="2", upper_tol="0"),
]


def test_synthetic_doc_produces_recoverable_balloons_and_gold_excel(tmp_path):
    pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN1")
    assert pdf.name == "SYN1.pdf" and xlsx.name == "SYN1.xlsx"

    balloons = recover_balloons(pdf)
    assert sorted(b.number for b in balloons) == [1, 2, 3]

    wb = load_workbook(xlsx)
    ws = wb.active
    header = [ws.cell(1, c).value for c in range(1, 6)]
    assert header == ["Pos.", "Merkmal", "Nennmaß", "O-TOL", "U-TOL"]
    assert ws.cell(2, 1).value == 1
    assert ws.cell(2, 3).value == "20"
    assert ws.cell(3, 3).value == "5,5"


def test_synthetic_page_size_defaults_to_a3_landscape(tmp_path):
    pdf, _ = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN2")
    doc = fitz.open(pdf)
    assert round(doc[0].rect.width) == 1191 and round(doc[0].rect.height) == 842
    doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_synthetic.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.synthetic`)

- [ ] **Step 3: Write the implementation** — `app/eval/synthetic.py`

```python
"""Build a synthetic 'client deliverable' (ballooned PDF + gold Excel) from
known GoldCharacteristic records. Test bed for the whole gold pipeline: the
balloons match the client's expected vector encoding (circle + centered number)
and the Excel mirrors the expected schema (same header vocabulary as
app/excel.py — the client sheets came out of the same inspection workflow;
Task 13 confirms against real files and only excel_gold's schema config needs
to change if they differ)."""
from pathlib import Path
from typing import List, Tuple

import fitz
from openpyxl import Workbook

from app.eval.models import GoldCharacteristic

_A3_LANDSCAPE = (1191, 842)   # points
_HEADERS = ["Pos.", "Merkmal", "Nennmaß", "O-TOL", "U-TOL"]


def make_synthetic_doc(records: List[GoldCharacteristic], out_dir,
                       doc_id: str = "SYN",
                       page_size: Tuple[int, int] = _A3_LANDSCAPE,
                       ) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{doc_id}.pdf"
    xlsx_path = out_dir / f"{doc_id}.xlsx"

    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    for r in records:
        x, y = r.position_pt
        page.draw_circle(fitz.Point(x, y), 9.0, color=(0, 0, 1), width=1.5)
        page.insert_text(fitz.Point(x - 5, y + 4), str(r.balloon),
                         fontsize=10, color=(0, 0, 1))
        # the callout text the balloon points at, offset like a real drawing
        label = f"{r.nominal} {r.upper_tol} {r.lower_tol}".strip()
        page.insert_text(fitz.Point(x + 14, y + 4), label, fontsize=8)
    doc.save(pdf_path)
    doc.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection"
    for col, h in enumerate(_HEADERS, start=1):
        ws.cell(1, col, h)
    for i, r in enumerate(sorted(records, key=lambda r: r.balloon), start=2):
        ws.cell(i, 1, r.balloon)
        ws.cell(i, 2, r.char_type)
        ws.cell(i, 3, r.nominal)
        ws.cell(i, 4, r.upper_tol)
        ws.cell(i, 5, r.lower_tol)
    wb.save(xlsx_path)
    return pdf_path, xlsx_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_synthetic.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/synthetic.py tests/eval/test_synthetic.py
git commit -m "feat(eval): synthetic ballooned-PDF + gold-Excel corpus builder"
```

---

## Task 5: Gold Excel reader with schema adapter (`app/eval/excel_gold.py`)

The real column layout is an open question (§10). The reader is therefore an **adapter**: header auto-detection over an alias table, so a schema surprise on day one is a one-dict edit, not a rewrite. `dump_headers` is the day-one inspection tool for all 100 files.

**Files:**
- Create: `app/eval/excel_gold.py`
- Create: `tests/eval/test_excel_gold.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_excel_gold.py`

```python
from openpyxl import Workbook

from app.eval.excel_gold import read_gold_excel, dump_headers


def _sheet(tmp_path, headers, rows, header_row=1, name="g.xlsx"):
    wb = Workbook()
    ws = wb.active
    for col, h in enumerate(headers, start=1):
        ws.cell(header_row, col, h)
    for i, row in enumerate(rows, start=header_row + 1):
        for col, v in enumerate(row, start=1):
            ws.cell(i, col, v)
    path = tmp_path / name
    wb.save(path)
    return path


def test_reads_house_style_sheet(tmp_path):
    path = _sheet(tmp_path, ["Pos.", "Merkmal", "Nennmaß", "O-TOL", "U-TOL"],
                  [[1, "Diameter", "20", "0,1", "-0,1"],
                   [2, "Distance", 5.5, None, None]])
    rows = read_gold_excel(path)
    assert rows[1] == {"char_type": "Diameter", "nominal": "20",
                       "upper_tol": "0,1", "lower_tol": "-0,1", "raw": ""}
    assert rows[2]["nominal"] == "5.5"       # numeric cell -> canonical string
    assert rows[2]["upper_tol"] == ""        # None -> empty


def test_header_aliases_and_offset_header_row(tmp_path):
    path = _sheet(tmp_path, ["Position", "Characteristic", "Nominal value",
                             "Upper-tol", "Lower-tol"],
                  [[7, "Radius", "2", "0", ""]], header_row=3)
    rows = read_gold_excel(path)
    assert rows[7]["char_type"] == "Radius"


def test_missing_pos_column_raises(tmp_path):
    path = _sheet(tmp_path, ["Foo", "Bar"], [[1, 2]])
    try:
        read_gold_excel(path)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Pos" in str(e)


def test_dump_headers_reports_detected_row(tmp_path):
    path = _sheet(tmp_path, ["Pos.", "Merkmal", "Nennmaß", "O-TOL", "U-TOL"],
                  [[1, "Diameter", "20", "", ""]])
    info = dump_headers(path)
    assert info["header_row"] == 1
    assert "Merkmal" in info["headers"]
    assert info["n_rows"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_excel_gold.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.excel_gold`)

- [ ] **Step 3: Write the implementation** — `app/eval/excel_gold.py`

```python
"""Read a client gold Excel into {balloon_number: field dict}.

Schema is an adapter around COLUMN_ALIASES + header auto-detection: the header
row is found by scanning the first 12 rows for a 'Pos' alias. When the real
corpus arrives (Task 13), run `dump_headers` over all files; if the layout
differs, extend COLUMN_ALIASES — nothing else changes.

Numeric cells are canonicalized through normalize.canon_value at read time so
'5,5' (text) and 5.5 (float cell) ingest identically.
"""
from typing import Dict, List, Optional

from openpyxl import load_workbook

from app.eval.normalize import canon_value

# canonical field -> header aliases (matched casefolded/stripped)
COLUMN_ALIASES: Dict[str, List[str]] = {
    "pos": ["pos.", "pos", "position", "nr.", "nr", "ballon", "balloon"],
    "char_type": ["merkmal", "characteristic", "typ", "type"],
    "nominal": ["nennmaß", "nennmass", "nominal value", "nominal", "soll"],
    "upper_tol": ["o-tol", "upper-tol", "oberes abmaß", "upper tol", "otol"],
    "lower_tol": ["u-tol", "lower-tol", "unteres abmaß", "lower tol", "utol"],
    "raw": ["raw", "text", "bemerkung", "remark"],
}
_MAX_HEADER_SCAN = 12


def _norm_header(v) -> str:
    return " ".join(str(v or "").split()).casefold()


def _find_header(ws):
    """Return (header_row, {field: column}) or raise ValueError."""
    pos_aliases = set(COLUMN_ALIASES["pos"])
    for row in range(1, min(_MAX_HEADER_SCAN, ws.max_row) + 1):
        headers = {_norm_header(ws.cell(row, c).value): c
                   for c in range(1, ws.max_column + 1)}
        if not (pos_aliases & set(headers)):
            continue
        cols = {}
        for field, aliases in COLUMN_ALIASES.items():
            for a in aliases:
                if a in headers:
                    cols[field] = headers[a]
                    break
        return row, cols
    raise ValueError(f"no header row with a 'Pos' column found in {ws.title!r} "
                     f"(scanned {_MAX_HEADER_SCAN} rows)")


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return canon_value(v)
    return str(v).strip()


def read_gold_excel(path, sheet: Optional[str] = None) -> Dict[int, dict]:
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    header_row, cols = _find_header(ws)
    out: Dict[int, dict] = {}
    for row in range(header_row + 1, ws.max_row + 1):
        pos_v = ws.cell(row, cols["pos"]).value
        if pos_v is None or not str(pos_v).strip():
            continue
        try:
            balloon = int(float(str(pos_v).replace(",", ".")))
        except ValueError:
            continue                      # sub-header / footer rows
        out[balloon] = {
            field: _cell_str(ws.cell(row, col).value)
            for field, col in cols.items() if field != "pos"
        }
        for field in ("char_type", "nominal", "upper_tol", "lower_tol", "raw"):
            out[balloon].setdefault(field, "")
    return out


def dump_headers(path) -> dict:
    """Day-one inspection: which header row/labels does this file use?"""
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    try:
        header_row, cols = _find_header(ws)
        rows = read_gold_excel(path)
        return {
            "file": str(path),
            "sheet": ws.title,
            "header_row": header_row,
            "headers": [str(ws.cell(header_row, c).value)
                        for c in range(1, ws.max_column + 1)
                        if ws.cell(header_row, c).value is not None],
            "mapped_fields": sorted(cols),
            "n_rows": len(rows),
        }
    except ValueError as e:
        return {"file": str(path), "sheet": ws.title, "error": str(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_excel_gold.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/excel_gold.py tests/eval/test_excel_gold.py
git commit -m "feat(eval): schema-adaptive gold Excel reader + header inspector"
```

---

## Task 6: Gold ingestion — join balloons + Excel into `GoldDoc` (`app/eval/ingest.py`)

**Files:**
- Create: `app/eval/ingest.py`
- Create: `tests/eval/test_ingest.py`
- Modify: `.gitignore` (create if absent) — add `eval_data/`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_ingest.py`

```python
from app.eval.models import GoldCharacteristic
from app.eval.synthetic import make_synthetic_doc
from app.eval.ingest import build_gold_doc

RECORDS = [
    GoldCharacteristic(balloon=1, position_pt=(120.0, 90.0),
                       char_type="Diameter", nominal="20",
                       upper_tol="0,1", lower_tol="-0,1"),
    GoldCharacteristic(balloon=2, position_pt=(340.0, 200.0),
                       char_type="Distance", nominal="5,5"),
]


def test_join_recovers_positions_and_values(tmp_path):
    pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN1")
    gold = build_gold_doc(pdf, xlsx, doc_id="SYN1")
    assert gold.doc_id == "SYN1"
    assert round(gold.page_rect[2]) == 1191
    by_num = {c.balloon: c for c in gold.characteristics}
    assert set(by_num) == {1, 2}
    assert by_num[1].nominal == "20" and by_num[1].char_type == "Diameter"
    x, y = by_num[2].position_pt
    assert abs(x - 340.0) < 3 and abs(y - 200.0) < 3
    assert gold.provenance["join_rate"] == 1.0


def test_unjoined_rows_and_balloons_recorded_not_dropped_silently(tmp_path):
    pdf, xlsx = make_synthetic_doc(RECORDS, tmp_path, doc_id="SYN2")
    # Excel has a row 9 with no balloon on the page
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    ws = wb.active
    ws.cell(4, 1, 9); ws.cell(4, 2, "Distance"); ws.cell(4, 3, "7")
    wb.save(xlsx)
    gold = build_gold_doc(pdf, xlsx, doc_id="SYN2")
    assert gold.provenance["excel_only"] == [9]
    assert gold.provenance["pdf_only"] == []
    assert gold.provenance["join_rate"] < 1.0
    assert {c.balloon for c in gold.characteristics} == {1, 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.ingest`)

- [ ] **Step 3: Write the implementation** — `app/eval/ingest.py`

```python
"""Join recovered balloons (positions) with gold Excel rows (values) into a
GoldDoc. Join failures are never silent: every unjoined balloon number lands in
provenance, and join_rate < 1.0 is the day-one signal that a document needs
manual attention (Task 13 triages those)."""
from pathlib import Path

import fitz

from app.eval.balloons import recover_balloons
from app.eval.excel_gold import read_gold_excel
from app.eval.models import GoldCharacteristic, GoldDoc


def build_gold_doc(pdf_path, excel_path, doc_id: str,
                   is_variant: bool = False, page_index: int = 0) -> GoldDoc:
    balloons = {b.number: b for b in recover_balloons(pdf_path, page_index)}
    rows = read_gold_excel(excel_path)

    doc = fitz.open(pdf_path)
    rect = doc[page_index].rect
    page_rect = (rect.x0, rect.y0, rect.x1, rect.y1)
    doc.close()

    joined = sorted(set(balloons) & set(rows))
    chars = [GoldCharacteristic(
                 balloon=n,
                 position_pt=balloons[n].center_pt,
                 char_type=rows[n]["char_type"],
                 nominal=rows[n]["nominal"],
                 upper_tol=rows[n]["upper_tol"],
                 lower_tol=rows[n]["lower_tol"],
                 raw=rows[n].get("raw", ""),
             ) for n in joined]
    total = len(set(balloons) | set(rows))
    return GoldDoc(
        doc_id=doc_id,
        pdf=str(Path(pdf_path)),
        excel=str(Path(excel_path)),
        page_rect=page_rect,
        characteristics=chars,
        is_variant=is_variant,
        provenance={
            "n_balloons": len(balloons),
            "n_excel_rows": len(rows),
            "pdf_only": sorted(set(balloons) - set(rows)),
            "excel_only": sorted(set(rows) - set(balloons)),
            "join_rate": (len(joined) / total) if total else 0.0,
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_ingest.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Gitignore the client data directory**

Append to `.gitignore` (create the file if it does not exist):

```
eval_data/
```

- [ ] **Step 6: Commit**

```bash
git add app/eval/ingest.py tests/eval/test_ingest.py .gitignore
git commit -m "feat(eval): gold ingestion joining balloon positions with Excel values"
```

---

## Task 7: Prediction dumps — serialize a run once, score anywhere (`app/eval/dump.py`)

The GPU box runs the model once and writes JSON; every scoring/comparison after that is CPU-only. `to_points` is the **single** pixel→point conversion in the harness.

**Files:**
- Create: `app/eval/dump.py`
- Create: `tests/eval/test_dump.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_dump.py`

```python
from app.eval.dump import to_points, save_dump, load_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult


def test_to_points_inverts_render_scaling():
    scale = 300 / 72.0
    page_rect = (0.0, 0.0, 1191.0, 842.0)
    box_px = (scale * 100, scale * 50, scale * 130, scale * 60)
    assert [round(v, 6) for v in to_points(box_px, scale, page_rect)] == \
        [100.0, 50.0, 130.0, 60.0]


def test_to_points_honors_page_origin_offset():
    pt = to_points((0, 0, 72, 72), scale=1.0, page_rect=(10.0, 20.0, 500.0, 500.0))
    assert pt[0] == 10.0 and pt[1] == 20.0


def test_save_load_roundtrip(tmp_path):
    d = PredictionDump(
        doc_id="T1", config=RunConfig(model_id="stub", dpi=300),
        scale=300 / 72.0, page_rect=(0.0, 0.0, 1191.0, 842.0),
        result=ExtractionResult(characteristics=[
            Characteristic(pos=1, nominal="20", target_region=(10, 10, 40, 20)),
        ]),
    )
    path = save_dump(d, tmp_path)
    assert path.name == "T1.pred.json"
    d2 = load_dump(path)
    assert d2 == d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_dump.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.dump`)

- [ ] **Step 3: Write the implementation** — `app/eval/dump.py`

```python
"""Prediction dump I/O + the single pixel->point conversion.

A dump is one document's ExtractionResult plus the RunConfig fingerprint and
the render geometry (scale, page_rect) needed to interpret its pixel-space
boxes. Scoring never re-renders and never imports the model."""
from pathlib import Path
from typing import Tuple

from app.eval.models import PredictionDump


def to_points(box_px, scale: float, page_rect) -> Tuple[float, float, float, float]:
    """Convert an image-pixel box (rendered at `scale` px/pt) to PDF points."""
    x0, y0 = page_rect[0], page_rect[1]
    return (x0 + box_px[0] / scale, y0 + box_px[1] / scale,
            x0 + box_px[2] / scale, y0 + box_px[3] / scale)


def save_dump(dump: PredictionDump, out_dir) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{dump.doc_id}.pred.json"
    path.write_text(dump.model_dump_json(indent=1), encoding="utf-8")
    return path


def load_dump(path) -> PredictionDump:
    return PredictionDump.model_validate_json(
        Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_dump.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/dump.py tests/eval/test_dump.py
git commit -m "feat(eval): prediction dump IO + single px->pt conversion"
```

---

## Task 8: Deterministic matcher — geometry + value similarity (`app/eval/matching.py`)

Bipartite assignment prediction↔gold (handoff §3 step 4). Pure-Python **greedy over globally sorted pair costs** with a hard geometry gate: with ~30 callouts per page and two independent signals (position + nominal), greedy is near-optimal, dependency-free, and — critically for comparability — deterministic under total-order tie-breaking. (If Task 13's position-reliability check shows dense ambiguity, swapping in Hungarian is a drop-in change behind the same function signature.)

**Files:**
- Create: `app/eval/matching.py`
- Create: `tests/eval/test_matching.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_matching.py`

```python
from app.eval.matching import Cand, match_candidates
from app.eval.models import MatchParams

PAGE_DIAG = 1000.0
P = MatchParams()   # max_geo_frac=0.10, value_bonus=0.35


def test_matches_nearest_when_values_unavailable():
    preds = [Cand(key=1, center_pt=(100, 100), nominal=""),
             Cand(key=2, center_pt=(500, 500), nominal="")]
    golds = [Cand(key=7, center_pt=(105, 100), nominal=""),
             Cand(key=8, center_pt=(510, 505), nominal="")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, P)
    assert {(p, g) for p, g, _ in pairs} == {(1, 7), (2, 8)}


def test_geometry_gate_blocks_distant_pairs():
    preds = [Cand(key=1, center_pt=(100, 100), nominal="20")]
    golds = [Cand(key=7, center_pt=(400, 400), nominal="20")]   # 42% of diag
    assert match_candidates(preds, golds, PAGE_DIAG, P) == []


def test_value_agreement_breaks_geometric_ambiguity():
    # two golds equidistant-ish from two preds; nominals disambiguate
    preds = [Cand(key=1, center_pt=(100, 100), nominal="20"),
             Cand(key=2, center_pt=(110, 100), nominal="5,5")]
    golds = [Cand(key=7, center_pt=(105, 108), nominal="5.5"),
             Cand(key=8, center_pt=(105, 92), nominal="20")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, P)
    assert {(p, g) for p, g, _ in pairs} == {(1, 8), (2, 7)}


def test_one_to_one_and_deterministic():
    preds = [Cand(key=1, center_pt=(100, 100), nominal=""),
             Cand(key=2, center_pt=(100, 100), nominal="")]   # identical preds
    golds = [Cand(key=7, center_pt=(100, 100), nominal="")]
    for _ in range(5):
        pairs = match_candidates(preds, golds, PAGE_DIAG, P)
        assert pairs == [(1, 7, 0.0)]          # lower key wins the tie, always
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_matching.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.matching`)

- [ ] **Step 3: Write the implementation** — `app/eval/matching.py`

```python
"""Deterministic greedy bipartite matching of predictions to gold records.

cost(pred, gold) = center_distance / page_diagonal
                   - value_bonus   (when the nominals agree via normalize)
Pairs farther than max_geo_frac of the diagonal are forbidden outright — a
value match cannot rescue a geometrically absurd pair. Greedy consumes pairs
in ascending (cost, pred_key, gold_key) order, so output is a pure function of
the inputs (comparability requirement: same inputs -> same matching, always).
"""
import math
from dataclasses import dataclass
from typing import List, Tuple

from app.eval.models import MatchParams
from app.eval.normalize import values_equal


@dataclass(frozen=True)
class Cand:
    key: int              # pred.pos or gold.balloon
    center_pt: tuple      # PDF points
    nominal: str = ""


def match_candidates(preds: List[Cand], golds: List[Cand],
                     page_diag_pt: float, params: MatchParams,
                     ) -> List[Tuple[int, int, float]]:
    """Return [(pred_key, gold_key, distance_frac)], one-to-one, sorted by
    pred_key. distance_frac = center distance / page diagonal."""
    scored = []
    for p in preds:
        for g in golds:
            d = math.dist(p.center_pt, g.center_pt) / page_diag_pt
            if d > params.max_geo_frac:
                continue
            cost = d
            if p.nominal and g.nominal and values_equal(p.nominal, g.nominal):
                cost -= params.value_bonus
            scored.append((cost, p.key, g.key, d))
    scored.sort()
    used_p, used_g, out = set(), set(), []
    for _cost, pk, gk, d in scored:
        if pk in used_p or gk in used_g:
            continue
        used_p.add(pk)
        used_g.add(gk)
        out.append((pk, gk, d))
    return sorted(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_matching.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/matching.py tests/eval/test_matching.py
git commit -m "feat(eval): deterministic greedy pred<->gold matcher with geometry gate"
```

---

## Task 9: Per-document scoring — review cost + error taxonomy (`app/eval/score.py`)

Implements §4 exactly: misses dominate, escaped (unflagged) errors are expensive, flagged rows are cheap whether right or wrong, false detections are moderate. Tags each matched pair with a taxonomy label and a cause note (`misread` vs `misparse`) so Rung 1+ knows where the residual lives.

**Files:**
- Create: `app/eval/score.py`
- Create: `tests/eval/test_score.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_score.py`

```python
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _pt_box(x, y):
    """A 30x10pt box centered at (x, y), expressed in render pixels."""
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _gold():
    return GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), char_type="Diameter",
                           nominal="20", upper_tol="0,1", lower_tol="-0,1"),
        GoldCharacteristic(balloon=2, position_pt=(400, 200), char_type="Distance",
                           nominal="5,5"),
        GoldCharacteristic(balloon=3, position_pt=(700, 300), char_type="Distance",
                           nominal="8"),
        GoldCharacteristic(balloon=4, position_pt=(900, 500), char_type="Radius",
                           nominal="2"),
    ])


def _dump():
    chars = [
        # pos 1: correct, unflagged -> "correct"
        Characteristic(pos=1, char_type="Diameter", nominal="20",
                       upper_tol="0,1", lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1",
                       target_region=_pt_box(100, 100)),
        # pos 2: wrong nominal, NOT flagged -> escaped_error, cause misread
        Characteristic(pos=2, char_type="Distance", nominal="6,5",
                       raw_text="6,5", target_region=_pt_box(400, 200)),
        # pos 3: wrong nominal, flagged -> flagged_error
        Characteristic(pos=3, char_type="Distance", nominal="9",
                       raw_text="9", needs_review=True,
                       review_reasons=["low OCR confidence"],
                       target_region=_pt_box(700, 300)),
        # gold 4 has no prediction -> missed
        # pos 5: phantom far from all gold -> false detection
        Characteristic(pos=5, char_type="Distance", nominal="99",
                       raw_text="99", target_region=_pt_box(200, 700)),
    ]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_taxonomy_counts_and_review_cost():
    s = score_doc(_dump(), _gold(), ReviewCostWeights(), MatchParams())
    assert s.counts == {"correct": 1, "escaped_error": 1, "flagged_error": 1,
                        "missed": 1, "false_detection": 1}
    # cost = 10*1 missed + 5*1 escaped + 2*1 false + 1*1 flagged = 18
    assert s.review_cost == 18.0
    assert s.recall == 0.75 and s.n_gold == 4 and s.n_pred == 4
    assert s.missed_balloons == [4]
    assert s.false_positions == [5]
    assert s.gold_hash == _gold().gold_hash()


def test_field_errors_and_cause_are_recorded():
    s = score_doc(_dump(), _gold(), ReviewCostWeights(), MatchParams())
    pair2 = next(p for p in s.pairs if p.pred_pos == 2)
    assert not pair2.fields_correct
    assert any("nominal" in e for e in pair2.field_errors)
    assert "cause:misread" in pair2.notes


def test_misparse_cause_when_raw_text_contains_gold_value():
    d = _dump()
    # read captured the right glyphs ('5,5' is in raw) but fields are wrong
    d.result.characteristics[1].raw_text = "5,5 +0,1"
    d.result.characteristics[1].nominal = "51"
    s = score_doc(d, _gold(), ReviewCostWeights(), MatchParams())
    pair2 = next(p for p in s.pairs if p.pred_pos == 2)
    assert "cause:misparse" in pair2.notes


def test_flagged_correct_costs_flag_weight_only():
    d = _dump()
    d.result.characteristics[0].needs_review = True     # correct row, flagged
    s = score_doc(d, _gold(), ReviewCostWeights(), MatchParams())
    assert s.counts["flagged_correct"] == 1
    # cost = 10 + 5 + 2 + 1(pos3) + 1(pos1) = 19
    assert s.review_cost == 19.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_score.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.score`)

- [ ] **Step 3: Write the implementation** — `app/eval/score.py`

```python
"""Score one prediction dump against one GoldDoc: match, compare fields, tag
the error taxonomy, and price the result in expected review effort (§4 of the
handoff). Pure CPU; imports nothing from the model stack."""
import math
import re
from typing import List

from app.eval.dump import to_points
from app.eval.matching import Cand, match_candidates
from app.eval.models import (DocScore, GoldDoc, MatchParams, MatchedPair,
                             PredictionDump, ReviewCostWeights)
from app.eval.normalize import canon_value, char_type_equal, values_equal

# Same numeric token shape as app/pipeline/parser.py's _NUM (kept local so the
# eval package never imports pipeline internals that may move under tuning).
_NUM_RE = re.compile(r"[+\-±]?\d+(?:[.,]\d+)?")

_FIELDS = ("nominal", "upper_tol", "lower_tol")


def _center_pt(char, dump):
    box = to_points(char.target_region, dump.scale, dump.page_rect)
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _compare_fields(pred, gold) -> List[str]:
    errors = []
    if gold.char_type and not char_type_equal(pred.char_type, gold.char_type):
        errors.append(f"char_type: {pred.char_type!r}!={gold.char_type!r}")
    for f in _FIELDS:
        pv, gv = getattr(pred, f), getattr(gold, f)
        if not values_equal(pv, gv):
            errors.append(f"{f}: {pv!r}!={gv!r}")
    return errors


def _cause(pred, gold) -> str:
    """misparse: the raw transcription contains the gold nominal (reader saw the
    right glyphs; parsing/structuring lost them). misread otherwise. A heuristic
    — good enough to steer Rung 1 (parser) vs Rung 2/3 (perception) effort."""
    gold_nom = canon_value(gold.nominal)
    raw_nums = {canon_value(t) for t in _NUM_RE.findall(pred.raw_text or "")}
    return "misparse" if gold_nom and gold_nom in raw_nums else "misread"


def score_doc(dump: PredictionDump, gold: GoldDoc,
              weights: ReviewCostWeights, params: MatchParams) -> DocScore:
    preds = [c for c in dump.result.characteristics if c.target_region is not None]
    pred_by_pos = {c.pos: c for c in preds}
    gold_by_num = {g.balloon: g for g in gold.characteristics}

    diag = math.dist(gold.page_rect[:2], gold.page_rect[2:])
    pairs_raw = match_candidates(
        [Cand(key=c.pos, center_pt=_center_pt(c, dump), nominal=c.nominal)
         for c in preds],
        [Cand(key=g.balloon, center_pt=g.position_pt, nominal=g.nominal)
         for g in gold.characteristics],
        diag, params)

    pairs, counts = [], {}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    for pk, gk, dist in pairs_raw:
        p, g = pred_by_pos[pk], gold_by_num[gk]
        errors = _compare_fields(p, g)
        notes = []
        if dist > params.misplaced_frac:
            notes.append("misplaced")
        if errors:
            taxonomy = "flagged_error" if p.needs_review else "escaped_error"
            notes.append(f"cause:{_cause(p, g)}")
        else:
            taxonomy = "flagged_correct" if p.needs_review else "correct"
        bump(taxonomy)
        pairs.append(MatchedPair(
            gold_balloon=gk, pred_pos=pk, distance_frac=round(dist, 5),
            fields_correct=not errors, field_errors=errors,
            flagged=p.needs_review, taxonomy=taxonomy, notes=notes))

    matched_g = {gk for _, gk, _ in pairs_raw}
    matched_p = {pk for pk, _, _ in pairs_raw}
    missed = sorted(set(gold_by_num) - matched_g)
    false = sorted(set(pred_by_pos) - matched_p)
    for _ in missed:
        bump("missed")
    for _ in false:
        bump("false_detection")

    flagged_rows = counts.get("flagged_correct", 0) + counts.get("flagged_error", 0)
    escaped = counts.get("escaped_error", 0)
    cost = (weights.miss * len(missed) + weights.escaped * escaped
            + weights.false * len(false) + weights.flag * flagged_rows)

    n_gold, n_pred = len(gold_by_num), len(pred_by_pos)
    return DocScore(
        doc_id=gold.doc_id, gold_hash=gold.gold_hash(),
        n_gold=n_gold, n_pred=n_pred, pairs=pairs,
        missed_balloons=missed, false_positions=false, counts=counts,
        review_cost=cost,
        recall=(len(matched_g) / n_gold) if n_gold else 1.0,
        precision=(len(matched_p) / n_pred) if n_pred else 1.0,
        escaped_rate=(escaped / n_gold) if n_gold else 0.0,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_score.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/score.py tests/eval/test_score.py
git commit -m "feat(eval): review-cost scoring with error taxonomy per document"
```

---

## Task 10: Frozen document-level splits (`app/eval/splits.py`)

**Files:**
- Create: `app/eval/splits.py`
- Create: `tests/eval/test_splits.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_splits.py`

```python
from app.eval.splits import make_splits, save_splits, load_splits, splits_hash

DOCS = [f"T{i:03d}" for i in range(20)]
VARIANTS = ["T003", "T011", "T017"]


def test_split_is_document_level_disjoint_and_complete():
    s = make_splits(DOCS, VARIANTS, seed=13)
    all_ids = s["train"] + s["dev"] + s["test"]
    assert sorted(all_ids) == sorted(DOCS)
    assert not (set(s["train"]) & set(s["dev"]))
    assert not (set(s["train"]) & set(s["test"]))
    assert not (set(s["dev"]) & set(s["test"]))


def test_variants_forced_into_test():
    s = make_splits(DOCS, VARIANTS, seed=13)
    assert set(VARIANTS) <= set(s["test"])


def test_same_seed_same_split_different_seed_different():
    assert make_splits(DOCS, VARIANTS, seed=13) == make_splits(DOCS, VARIANTS, seed=13)
    assert make_splits(DOCS, VARIANTS, seed=13) != make_splits(DOCS, VARIANTS, seed=14)


def test_roundtrip_and_stable_hash(tmp_path):
    s = make_splits(DOCS, VARIANTS, seed=13)
    path = tmp_path / "splits.json"
    save_splits(s, path)
    s2 = load_splits(path)
    assert s2 == s
    assert splits_hash(s) == splits_hash(s2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_splits.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.splits`)

- [ ] **Step 3: Write the implementation** — `app/eval/splits.py`

```python
"""Document-level train/dev/test split. Generated ONCE from the ingested
corpus, committed to docs/eval/splits.json, and never regenerated (the test
set is frozen — handoff §6). Variant drawings are forced into test so
cross-template generalization stays visible."""
import hashlib
import json
import random
from pathlib import Path
from typing import Dict, List

from app.eval.models import SCHEMA_VERSION


def make_splits(doc_ids: List[str], variant_ids: List[str], seed: int = 13,
                dev_frac: float = 0.2, test_frac: float = 0.2) -> Dict:
    doc_ids = sorted(set(doc_ids))
    variants = sorted(set(variant_ids) & set(doc_ids))
    rest = [d for d in doc_ids if d not in variants]
    random.Random(seed).shuffle(rest)
    n = len(doc_ids)
    n_test = max(0, int(round(n * test_frac)) - len(variants))
    n_dev = int(round(n * dev_frac))
    test = sorted(variants + rest[:n_test])
    dev = sorted(rest[n_test:n_test + n_dev])
    train = sorted(rest[n_test + n_dev:])
    return {"schema_version": SCHEMA_VERSION, "seed": seed,
            "variants": variants, "train": train, "dev": dev, "test": test}


def splits_hash(splits: Dict) -> str:
    blob = json.dumps(splits, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def save_splits(splits: Dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(splits, indent=1), encoding="utf-8")
    return path


def load_splits(path) -> Dict:
    splits = json.loads(Path(path).read_text(encoding="utf-8"))
    if splits.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported splits schema_version in {path}")
    return splits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_splits.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/splits.py tests/eval/test_splits.py
git commit -m "feat(eval): frozen seeded document-level splits, variants forced to test"
```

---

## Task 11: Run reports, paired bootstrap comparison, comparability guards (`app/eval/report.py`)

This is where the comparability contract is **enforced**: `compare_runs` raises unless the two runs cover the identical doc set, were scored against identical gold (per-doc hash), and used identical weights/match params. Deltas are paired per document; a 10,000-resample bootstrap CI (fixed seed) decides significance; the regression guard flags "improved cost, dropped recall".

**Files:**
- Create: `app/eval/report.py`
- Create: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_report.py`

```python
import pytest

from app.eval.models import (DocScore, MatchParams, ReviewCostWeights,
                             RunConfig)
from app.eval.report import aggregate, compare_runs


def _doc(doc_id, cost, recall=1.0, escaped=0, gold_hash="g" + "0" * 15,
         n_gold=10):
    counts = {"correct": n_gold - escaped, "escaped_error": escaped}
    return DocScore(doc_id=doc_id, gold_hash=gold_hash, n_gold=n_gold,
                    n_pred=n_gold, counts=counts, review_cost=cost,
                    recall=recall, precision=1.0,
                    escaped_rate=escaped / n_gold)


def _run(name, costs, recall=1.0, escaped=0):
    scores = [_doc(f"D{i}", c, recall=recall, escaped=escaped)
              for i, c in enumerate(costs)]
    return aggregate(name, RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), scores)


def test_aggregate_computes_headline_numbers():
    r = _run("base", [10.0, 20.0], escaped=1)
    assert r.mean_review_cost == 15.0
    assert r.micro_recall == 1.0
    assert r.taxonomy["escaped_error"] == 2
    assert r.escaped_rate == pytest.approx(0.1)


def test_compare_paired_delta_and_significance():
    a = _run("a", [10.0, 12.0, 14.0, 16.0])
    b = _run("b", [8.0, 10.0, 12.0, 14.0])       # uniformly 2 better
    cmp = compare_runs(a, b, seed=13)
    assert cmp["mean_delta"] == -2.0
    assert cmp["ci95"][1] <= 0.0
    assert cmp["significant"] is True
    assert cmp["n_docs"] == 4


def test_compare_self_is_zero_and_not_significant():
    a = _run("a", [10.0, 12.0, 14.0, 16.0])
    cmp = compare_runs(a, a, seed=13)
    assert cmp["mean_delta"] == 0.0
    assert cmp["significant"] is False


def test_regression_guard_flags_recall_drop_on_improved_cost():
    a = _run("a", [10.0, 10.0, 10.0, 10.0], recall=0.95)
    b = _run("b", [8.0, 8.0, 8.0, 8.0], recall=0.90)
    cmp = compare_runs(a, b, seed=13)
    assert any("recall" in w for w in cmp["warnings"])


def test_guards_refuse_incomparable_runs():
    a = _run("a", [10.0, 12.0])
    b = _run("b", [10.0])                                   # different doc set
    with pytest.raises(ValueError, match="doc set"):
        compare_runs(a, b)

    c = _run("c", [10.0, 12.0])
    c.weights = ReviewCostWeights(miss=99)                  # different weights
    with pytest.raises(ValueError, match="weights"):
        compare_runs(a, c)

    d = _run("d", [10.0, 12.0])
    d.doc_scores[0].gold_hash = "f" * 16                    # different gold
    with pytest.raises(ValueError, match="gold"):
        compare_runs(a, d)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.report`)

- [ ] **Step 3: Write the implementation** — `app/eval/report.py`

```python
"""Aggregate DocScores into a RunReport; compare two RunReports with paired
per-document deltas + bootstrap CIs. compare_runs is the comparability
gatekeeper: it RAISES on any mismatch (doc set, per-doc gold hash, weights,
match params) instead of producing a quietly meaningless number."""
import random
from typing import Dict, List

from app.eval.models import (DocScore, MatchParams, ReviewCostWeights,
                             RunConfig, RunReport, SCHEMA_VERSION)

N_BOOTSTRAP = 10_000


def aggregate(run_name: str, config: RunConfig, weights: ReviewCostWeights,
              match_params: MatchParams, doc_scores: List[DocScore],
              splits_hash: str = "", split_used: str = "all") -> RunReport:
    n_gold = sum(d.n_gold for d in doc_scores)
    n_pred = sum(d.n_pred for d in doc_scores)
    matched_gold = sum(round(d.recall * d.n_gold) for d in doc_scores)
    matched_pred = sum(round(d.precision * d.n_pred) for d in doc_scores)
    taxonomy: Dict[str, int] = {}
    for d in doc_scores:
        for k, v in d.counts.items():
            taxonomy[k] = taxonomy.get(k, 0) + v
    escaped = taxonomy.get("escaped_error", 0)
    return RunReport(
        run_name=run_name, config=config, weights=weights,
        match_params=match_params, splits_hash=splits_hash,
        split_used=split_used,
        doc_scores=sorted(doc_scores, key=lambda d: d.doc_id),
        mean_review_cost=(sum(d.review_cost for d in doc_scores)
                          / len(doc_scores)) if doc_scores else 0.0,
        micro_recall=(matched_gold / n_gold) if n_gold else 1.0,
        micro_precision=(matched_pred / n_pred) if n_pred else 1.0,
        escaped_rate=(escaped / n_gold) if n_gold else 0.0,
        taxonomy=taxonomy,
    )


def _check_comparable(a: RunReport, b: RunReport) -> None:
    ids_a = [d.doc_id for d in a.doc_scores]
    ids_b = [d.doc_id for d in b.doc_scores]
    if ids_a != ids_b:
        raise ValueError(f"doc set differs: {len(ids_a)} vs {len(ids_b)} docs "
                         f"(runs are only comparable on the identical corpus)")
    for da, db in zip(a.doc_scores, b.doc_scores):
        if da.gold_hash != db.gold_hash:
            raise ValueError(f"gold differs for {da.doc_id}: scored against "
                             f"different gold data — re-score both runs")
    if a.weights != b.weights:
        raise ValueError("weights differ between runs — re-score with one set")
    if a.match_params != b.match_params:
        raise ValueError("match params differ between runs — re-score with one set")
    if a.splits_hash and b.splits_hash and a.splits_hash != b.splits_hash:
        raise ValueError("splits differ between runs")


def compare_runs(a: RunReport, b: RunReport, seed: int = 13,
                 n_boot: int = N_BOOTSTRAP) -> Dict:
    """Paired comparison: delta = b - a per document (negative = b better).
    Returns headline deltas, a bootstrap CI on the mean delta, and regression
    warnings. Deterministic for fixed seed."""
    _check_comparable(a, b)
    deltas = [db.review_cost - da.review_cost
              for da, db in zip(a.doc_scores, b.doc_scores)]
    n = len(deltas)
    mean_delta = sum(deltas) / n if n else 0.0

    rng = random.Random(seed)
    boot_means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(n_boot)) if n else [0.0]
    ci95 = (boot_means[int(0.025 * len(boot_means))],
            boot_means[int(0.975 * len(boot_means)) - 1])
    significant = bool(deltas) and (ci95[1] < 0.0 or ci95[0] > 0.0)

    warnings = []
    improved = mean_delta < 0
    if improved and b.micro_recall < a.micro_recall - 0.005:
        warnings.append(
            f"review-cost improved but recall dropped "
            f"{a.micro_recall:.3f} -> {b.micro_recall:.3f} — likely a net "
            f"review-time LOSS on missed callouts (handoff §6 regression guard)")
    if improved and b.escaped_rate > a.escaped_rate + 0.005:
        warnings.append(
            f"review-cost improved but escaped-error rate rose "
            f"{a.escaped_rate:.3f} -> {b.escaped_rate:.3f} — silent errors up")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_a": a.run_name, "run_b": b.run_name, "n_docs": n,
        "mean_delta": round(mean_delta, 4),
        "ci95": [round(ci95[0], 4), round(ci95[1], 4)],
        "significant": significant,
        "per_doc_deltas": {da.doc_id: round(d, 4)
                           for da, d in zip(a.doc_scores, deltas)},
        "headline": {
            a.run_name: {"mean_review_cost": a.mean_review_cost,
                         "recall": a.micro_recall,
                         "escaped_rate": a.escaped_rate},
            b.run_name: {"mean_review_cost": b.mean_review_cost,
                         "recall": b.micro_recall,
                         "escaped_rate": b.escaped_rate},
        },
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_report.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "feat(eval): run reports, paired bootstrap comparison, comparability guards"
```

---

## Task 12: CLI + end-to-end synthetic acceptance (`app/eval/runner.py`)

One entry point, six subcommands. `probe`/`headers` are the day-one inspection tools (Task 13); `ingest`/`split` build the gold corpus; `predict` runs on the GPU box; `score`/`compare` run anywhere. The model stack is imported **only** inside `predict`.

**Files:**
- Create: `app/eval/runner.py`
- Create: `tests/eval/test_runner_e2e.py`

- [ ] **Step 1: Write the failing test** — `tests/eval/test_runner_e2e.py`

```python
"""End-to-end on synthetic truth: build corpus -> ingest via CLI -> score a
hand-perturbed prediction set via CLI -> compare a run against itself."""
import json

from app.eval.models import (GoldCharacteristic, GoldDoc, PredictionDump,
                             RunConfig)
from app.eval.dump import save_dump
from app.eval.runner import main, predict_one
from app.eval.synthetic import make_synthetic_doc
from app.models import Characteristic, ExtractionResult

RECORDS = {
    "SYNA": [
        GoldCharacteristic(balloon=1, position_pt=(120.0, 90.0),
                           char_type="Diameter", nominal="20",
                           upper_tol="0,1", lower_tol="-0,1"),
        GoldCharacteristic(balloon=2, position_pt=(340.0, 200.0),
                           char_type="Distance", nominal="5,5"),
    ],
    "SYNB": [
        GoldCharacteristic(balloon=1, position_pt=(200.0, 150.0),
                           char_type="Radius", nominal="2"),
        GoldCharacteristic(balloon=2, position_pt=(600.0, 400.0),
                           char_type="Distance", nominal="8"),
        GoldCharacteristic(balloon=3, position_pt=(800.0, 500.0),
                           char_type="Distance", nominal="12"),
    ],
}
SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _perfect_dump(doc_id, gold: GoldDoc, drop_last=False) -> PredictionDump:
    chars = []
    records = gold.characteristics[:-1] if drop_last else gold.characteristics
    for i, g in enumerate(records, start=1):
        x, y = g.position_pt
        chars.append(Characteristic(
            pos=i, char_type=g.char_type, nominal=g.nominal,
            upper_tol=g.upper_tol, lower_tol=g.lower_tol, raw_text=g.nominal,
            target_region=(SCALE * (x - 15), SCALE * (y - 5),
                           SCALE * (x + 15), SCALE * (y + 5))))
    return PredictionDump(doc_id=doc_id, config=RunConfig(model_id="stub"),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def _setup_corpus(root):
    pdfs, excel = root / "pdfs", root / "excel"
    for doc_id, recs in RECORDS.items():
        make_synthetic_doc(recs, root / "raw", doc_id=doc_id)
        pdfs.mkdir(exist_ok=True), excel.mkdir(exist_ok=True)
        (root / "raw" / f"{doc_id}.pdf").rename(pdfs / f"{doc_id}.pdf")
        (root / "raw" / f"{doc_id}.xlsx").rename(excel / f"{doc_id}.xlsx")
    return pdfs, excel


def test_full_pipeline_ingest_score_compare(tmp_path):
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "base"

    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    gold_files = sorted(gold_dir.glob("*.gold.json"))
    assert [p.name for p in gold_files] == ["SYNA.gold.json", "SYNB.gold.json"]

    for path in gold_files:
        gold = GoldDoc.model_validate_json(path.read_text())
        save_dump(_perfect_dump(gold.doc_id, gold,
                                drop_last=(gold.doc_id == "SYNB")), run_dir)

    report_path = tmp_path / "base.report.json"
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "base", "--out", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["taxonomy"] == {"correct": 4, "missed": 1}
    assert report["mean_review_cost"] == 5.0      # (0 + 10)/2

    cmp_path = tmp_path / "cmp.json"
    assert main(["compare", str(report_path), str(report_path),
                 "--out", str(cmp_path)]) == 0
    cmp = json.loads(cmp_path.read_text())
    assert cmp["mean_delta"] == 0.0 and cmp["significant"] is False


def test_probe_and_headers_inspection_commands(tmp_path, capsys):
    pdfs, excel = _setup_corpus(tmp_path)
    assert main(["probe", str(pdfs)]) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert {l["n_balloons"] for l in lines} == {2, 3}
    assert main(["headers", str(excel)]) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert all(l["header_row"] == 1 for l in lines)


def test_predict_one_builds_dump_from_stub_backend(tmp_path):
    from tests.conftest import StubVLMBackend
    from app.pipeline.detect import Detection
    pdfs, _ = _setup_corpus(tmp_path)
    backend = StubVLMBackend(detections=[
        Detection(box=(100, 100, 200, 140), kind="dimension", conf=0.9)])
    dump = predict_one(pdfs / "SYNA.pdf", "SYNA", dpi=300, backend=backend,
                       config=RunConfig(model_id="stub", dpi=300),
                       work_dir=tmp_path / "work")
    assert dump.doc_id == "SYNA"
    assert dump.scale == 300 / 72.0
    assert round(dump.page_rect[2]) == 1191
    assert len(dump.result.characteristics) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_runner_e2e.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `app.eval.runner`)

- [ ] **Step 3: Write the implementation** — `app/eval/runner.py`

```python
"""Eval harness CLI.

    python -m app.eval.runner probe   eval_data/pdfs            # day-one: balloon encoding
    python -m app.eval.runner headers eval_data/excel           # day-one: Excel schema
    python -m app.eval.runner ingest  --pdfs ... --excel ... --out eval_data/gold
    python -m app.eval.runner split   --gold eval_data/gold --variants v.txt \
                                      --out docs/eval/splits.json
    python -m app.eval.runner predict --pdfs ... --out eval_data/runs/<name> \
                                      [--splits docs/eval/splits.json --split dev]
    python -m app.eval.runner score   --run eval_data/runs/<name> --gold ... \
                                      --name <name> --out <report.json> \
                                      [--splits ... --split dev] [--weights w.json]
    python -m app.eval.runner compare <report_a.json> <report_b.json> [--out c.json]

probe/headers/ingest/split/score/compare are CPU-only. predict imports the
model stack lazily and captures the RunConfig fingerprint at run time.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import fitz

from app.eval.balloons import probe_pdf
from app.eval.dump import load_dump, save_dump
from app.eval.excel_gold import dump_headers
from app.eval.ingest import build_gold_doc
from app.eval.models import (GoldDoc, MatchParams, PredictionDump,
                             ReviewCostWeights, RunConfig, RunReport)
from app.eval.report import aggregate, compare_runs
from app.eval.score import score_doc
from app.eval.splits import load_splits, make_splits, save_splits, splits_hash


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent, text=True).strip()
    except Exception:
        return "unknown"


def _prompt_sha256() -> str:
    try:
        from app.pipeline.ocr import vlm_backend as vb
        blob = "\n".join([vb._PROMPT, vb._DETECT_PROMPT, vb._GDT_PROMPT,
                          vb._NOTES_PROMPT, vb._TITLE_PROMPT])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unavailable"


def _select_docs(doc_ids, splits_path, split_name):
    if not splits_path:
        return sorted(doc_ids), "", "all"
    splits = load_splits(splits_path)
    keep = set(splits[split_name])
    return (sorted(d for d in doc_ids if d in keep),
            splits_hash(splits), split_name)


def predict_one(pdf_path, doc_id: str, dpi: int, backend,
                config: RunConfig, work_dir) -> PredictionDump:
    from app.pipeline.extract import extract
    result = extract(pdf_path, Path(work_dir) / doc_id, dpi=dpi,
                     backend=backend)
    doc = fitz.open(pdf_path)
    rect = doc[0].rect
    doc.close()
    return PredictionDump(doc_id=doc_id, config=config, scale=dpi / 72.0,
                          page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                          result=result)


def _cmd_probe(args):
    for pdf in sorted(Path(args.dir).glob("*.pdf")):
        print(json.dumps(probe_pdf(pdf), ensure_ascii=False))
    return 0


def _cmd_headers(args):
    for xlsx in sorted(Path(args.dir).glob("*.xlsx")):
        print(json.dumps(dump_headers(xlsx), ensure_ascii=False))
    return 0


def _cmd_ingest(args):
    pdfs = {p.stem: p for p in Path(args.pdfs).glob("*.pdf")}
    excels = {p.stem: p for p in Path(args.excel).glob("*.xlsx")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    variants = set(Path(args.variants).read_text().split()) if args.variants else set()
    unpaired = sorted(set(pdfs) ^ set(excels))
    if unpaired:
        print(f"WARNING: unpaired stems (skipped): {unpaired}", file=sys.stderr)
    low_join = []
    for stem in sorted(set(pdfs) & set(excels)):
        gold = build_gold_doc(pdfs[stem], excels[stem], doc_id=stem,
                              is_variant=stem in variants)
        (out / f"{stem}.gold.json").write_text(gold.model_dump_json(indent=1),
                                               encoding="utf-8")
        if gold.provenance["join_rate"] < 0.95:
            low_join.append((stem, gold.provenance["join_rate"]))
    if low_join:
        print(f"ATTENTION: join_rate < 0.95 (inspect manually): {low_join}",
              file=sys.stderr)
    print(f"ingested {len(set(pdfs) & set(excels))} docs -> {out}")
    return 0


def _load_gold_dir(gold_dir):
    return {g.doc_id: g for g in
            (GoldDoc.model_validate_json(p.read_text(encoding="utf-8"))
             for p in sorted(Path(gold_dir).glob("*.gold.json")))}


def _cmd_split(args):
    gold = _load_gold_dir(args.gold)
    variants = [d for d, g in gold.items() if g.is_variant]
    splits = make_splits(sorted(gold), variants, seed=args.seed)
    path = save_splits(splits, args.out)
    print(f"splits -> {path} (train={len(splits['train'])} "
          f"dev={len(splits['dev'])} test={len(splits['test'])})")
    return 0


def _cmd_predict(args):
    import os
    from app.pipeline.ocr import get_backend
    backend = get_backend()
    config = RunConfig(
        model_id=os.environ.get("VLM_MODEL_ID", "default"), dpi=args.dpi,
        git_sha=_git_sha(), prompt_sha256=_prompt_sha256())
    pdfs = {p.stem: p for p in Path(args.pdfs).glob("*.pdf")}
    doc_ids, _, _ = _select_docs(pdfs, args.splits, args.split)
    for i, doc_id in enumerate(doc_ids, 1):
        print(f"[{i}/{len(doc_ids)}] {doc_id}", file=sys.stderr)
        dump = predict_one(pdfs[doc_id], doc_id, args.dpi, backend, config,
                           Path(args.out) / "_work")
        save_dump(dump, args.out)
    return 0


def _cmd_score(args):
    gold = _load_gold_dir(args.gold)
    dumps = {d.doc_id: d for d in
             (load_dump(p) for p in sorted(Path(args.run).glob("*.pred.json")))}
    weights = (ReviewCostWeights.model_validate_json(
                   Path(args.weights).read_text()) if args.weights
               else ReviewCostWeights())
    params = MatchParams()
    doc_ids, sp_hash, sp_name = _select_docs(
        set(gold) & set(dumps), args.splits, args.split)
    missing = sorted((set(gold) & set(dumps)) ^ set(dumps))
    if missing:
        print(f"WARNING: dumps without gold (excluded): {missing}",
              file=sys.stderr)
    scores = [score_doc(dumps[d], gold[d], weights, params) for d in doc_ids]
    config = scores and dumps[doc_ids[0]].config or RunConfig()
    report = aggregate(args.name, config, weights, params, scores,
                       splits_hash=sp_hash, split_used=sp_name)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report.model_dump_json(indent=1),
                              encoding="utf-8")
    print(f"{args.name}: docs={len(scores)} "
          f"mean_review_cost={report.mean_review_cost:.2f} "
          f"recall={report.micro_recall:.3f} "
          f"escaped_rate={report.escaped_rate:.3f}")
    return 0


def _cmd_compare(args):
    a = RunReport.model_validate_json(Path(args.report_a).read_text())
    b = RunReport.model_validate_json(Path(args.report_b).read_text())
    cmp = compare_runs(a, b)
    out = json.dumps(cmp, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    print(out)
    for w in cmp["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.eval.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe"); p.add_argument("dir"); p.set_defaults(fn=_cmd_probe)
    p = sub.add_parser("headers"); p.add_argument("dir"); p.set_defaults(fn=_cmd_headers)

    p = sub.add_parser("ingest")
    p.add_argument("--pdfs", required=True); p.add_argument("--excel", required=True)
    p.add_argument("--out", required=True); p.add_argument("--variants", default=None)
    p.set_defaults(fn=_cmd_ingest)

    p = sub.add_parser("split")
    p.add_argument("--gold", required=True); p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=13)
    p.set_defaults(fn=_cmd_split)

    p = sub.add_parser("predict")
    p.add_argument("--pdfs", required=True); p.add_argument("--out", required=True)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.set_defaults(fn=_cmd_predict)

    p = sub.add_parser("score")
    p.add_argument("--run", required=True); p.add_argument("--gold", required=True)
    p.add_argument("--name", required=True); p.add_argument("--out", required=True)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.add_argument("--weights", default=None)
    p.set_defaults(fn=_cmd_score)

    p = sub.add_parser("compare")
    p.add_argument("report_a"); p.add_argument("report_b")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=_cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the e2e test, then the whole suite**

Run: `pytest tests/eval/test_runner_e2e.py -v`
Expected: PASS (3 tests)

Run: `pytest tests/ -q`
Expected: full suite green (no existing test touched)

- [ ] **Step 5: Commit**

```bash
git add app/eval/runner.py tests/eval/test_runner_e2e.py
git commit -m "feat(eval): CLI (probe/headers/ingest/split/predict/score/compare) + synthetic e2e"
```

---

## Task 13: Day-one data inspection playbook (runs when the client corpus arrives)

Everything above is built and green **before** the data lands; this task is the checklist that adapts it to reality in hours, not days. All commands are CPU-only. Record every decision in a new committed file `docs/eval/data-decisions.md` (create it from the template below) — these decisions are part of the comparability contract: they must never drift silently after the baseline is taken.

- [ ] **Step 1: Stage the corpus**

```bash
mkdir -p eval_data/pdfs eval_data/excel
# copy client PDFs -> eval_data/pdfs/<doc_id>.pdf
# copy client Excel -> eval_data/excel/<doc_id>.xlsx  (same stem = same doc)
```

- [ ] **Step 2: Resolve the balloon-encoding open question (§3, §10)**

```bash
python -m app.eval.runner probe eval_data/pdfs > /tmp/probe.jsonl
python - <<'EOF'
import json
lines = [json.loads(l) for l in open('/tmp/probe.jsonl')]
bad = [l['pdf'] for l in lines if l['n_balloons'] == 0]
print(f"{len(lines)} PDFs, {len(bad)} with zero recovered balloons: {bad[:10]}")
print(f"raster suspects (has_images, no circles):",
      [l['pdf'] for l in lines if l['has_images'] and l['n_circles'] == 0])
print(f"files with duplicate balloon numbers:",
      [l['pdf'] for l in lines if l['duplicate_numbers']])
EOF
```

Decision gate: if ≥90% of files recover balloons cleanly (vector), proceed; triage the rest by hand. If the corpus is raster-stamped, STOP and spec a raster balloon detector as its own task (circle Hough + digit OCR on the rendered page) before continuing — do not bend the vector path around it.

- [ ] **Step 3: Resolve the Excel-schema open question (§10)**

```bash
python -m app.eval.runner headers eval_data/excel
```

If any file reports `error` or unmapped fields: extend `COLUMN_ALIASES` in `app/eval/excel_gold.py` (TDD: add the real header row to `tests/eval/test_excel_gold.py` first). Also inspect 5 sheets by hand for: decimal convention, how char_type is worded (extend `CHAR_TYPE_SYNONYMS` in `normalize.py`), whether tolerances use empty-vs-`0` for MAX dimensions — if gold writes `0` where our parser writes `""`, decide the policy in `normalize.py`/`score.py` and write it down.

- [ ] **Step 4: Ingest + triage joins**

```bash
python -m app.eval.runner ingest --pdfs eval_data/pdfs --excel eval_data/excel \
    --out eval_data/gold
```

Every `ATTENTION: join_rate < 0.95` doc gets eyeballed: open the PDF, find why balloons and rows disagree (multi-page? balloon on a detail view? renumbered revision?). Fix systematically if a pattern, exclude the doc (documented) if singular.

- [ ] **Step 5: Identify variants, freeze the split**

Skim all 100 drawings (or ask the client) and list the layout/customer variants in `eval_data/variants.txt`, then re-ingest with `--variants eval_data/variants.txt` and:

```bash
python -m app.eval.runner split --gold eval_data/gold --out docs/eval/splits.json
git add docs/eval/splits.json && git commit -m "chore(eval): freeze document splits"
```

The test split is now frozen. It is touched exactly once more (final Rung-comparison); all tuning reads `--split dev`.

- [ ] **Step 6: Calibrate the remaining defaults from data + client**

- **Review-cost weights:** ask the reviewer for rough minutes per action (add missed row / verify flag / fix escaped error later / delete phantom); encode as `ReviewCostWeights` in a committed `docs/eval/weights.json`, pass via `--weights` everywhere, forever.
- **Position reliability → `MatchParams.max_geo_frac`:** for 5 hand-checked docs, measure the distance between the client balloon center and the true callout; set the gate at ~2× the observed max. Update the default in `models.py` (TDD) and record it.

- [ ] **Step 7: Commit the decisions document**

`docs/eval/data-decisions.md` template:

```markdown
# Gold-data decisions (frozen 2026-MM-DD)
- Balloon encoding: vector|raster|mixed; recovery rate: N/100 clean
- Excel schema: header vocabulary found; aliases added: [...]
- Value conventions: decimal=comma|period; empty-vs-0 tolerance policy: ...
- char_type vocabulary mapped: [...]
- Review-cost weights (client-sourced): miss=?, escaped=?, false=?, flag=?
- max_geo_frac: ? (measured position reliability: ...)
- Excluded docs + why: [...]
- Variant docs: [...]
```

```bash
git add docs/eval/data-decisions.md docs/eval/weights.json
git commit -m "docs(eval): freeze gold-data conventions and review-cost weights"
```

---

## Task 14: Baseline run (GPU box) — the number every optimization is measured against

Per the GPU workflow: run on the 4mehpc4 host after `test.sh` (pulls main + rebuilds); use the deployed 72B AWQ config that production uses — the baseline must measure the *shipping* configuration.

- [ ] **Step 1: Predict on dev + test with the default config**

```bash
python -m app.eval.runner predict --pdfs eval_data/pdfs \
    --out eval_data/runs/baseline --splits docs/eval/splits.json --split dev
python -m app.eval.runner predict --pdfs eval_data/pdfs \
    --out eval_data/runs/baseline --splits docs/eval/splits.json --split test
```

- [ ] **Step 2: Score both splits**

```bash
python -m app.eval.runner score --run eval_data/runs/baseline \
    --gold eval_data/gold --splits docs/eval/splits.json --split dev \
    --weights docs/eval/weights.json --name baseline-dev \
    --out eval_data/reports/baseline-dev.report.json
python -m app.eval.runner score --run eval_data/runs/baseline \
    --gold eval_data/gold --splits docs/eval/splits.json --split test \
    --weights docs/eval/weights.json --name baseline-test \
    --out eval_data/reports/baseline-test.report.json
```

- [ ] **Step 3: Sanity: compare baseline to itself**

```bash
python -m app.eval.runner compare eval_data/reports/baseline-dev.report.json \
    eval_data/reports/baseline-dev.report.json
```
Expected: `mean_delta: 0.0`, `significant: false` — the guards and pairing work on real data.

- [ ] **Step 4: Commit the headline (numbers only, no client data)**

Copy the dev report **with `doc_scores` stripped** to `docs/eval/baseline-report.json`:

```bash
python - <<'EOF'
import json
r = json.load(open('eval_data/reports/baseline-dev.report.json'))
r['doc_scores'] = []
json.dump(r, open('docs/eval/baseline-report.json', 'w'), indent=1)
EOF
git add docs/eval/baseline-report.json
git commit -m "chore(eval): record baseline review-cost on dev split"
```

- [ ] **Step 5: Read the taxonomy — decide the next rung**

The taxonomy histogram in the baseline report *is* the routing decision from the handoff (§5): `missed` dominant → Rung 1 detection-recall knobs; `cause:misparse` heavy → Rung 1 parser hardening; `cause:misread` heavy → Rung 2 prompts/few-shot, then Rung 3 LoRA; `flagged_correct` huge → Rung 1 review-flag calibration. Each becomes its own plan, tuned on dev, confirmed on test **once**.

---

## Explicitly deferred (so Rung 0 stays sharp)

- **Structured-block scoring (notes/marks/title).** Gold signal is thin (~100 examples) and the Excel encoding of these blocks is unknown; the review-cost driver is the characteristics table. Hook exists: `GoldDoc.provenance` and the schema-versioned models make adding a `blocks` section a backward-compatible extension once the real sheets show what block gold actually looks like.
- **Raster balloon detector.** Built only if the Task 13 probe proves the corpus needs it.
- **Hungarian matcher.** Drop-in behind `match_candidates` if the position-reliability check shows greedy ambiguity (not expected at ~30 callouts/page with value tie-breaks).
- **Per-field review flagging, confidence calibration, template-structural sanity checks, prompt/few-shot/LoRA work.** All Rungs 1–3 — each gets its own plan **after** the baseline taxonomy says where the error lives.

## Execution notes

- Tasks 1–12 have zero dependency on client data or GPU: run them now, in order (each task's tests use only synthetic fixtures). Task 13 blocks on data arrival; Task 14 on Task 13 + GPU box.
- Dependency chain: 1 → 2 → {3, 5} → 4 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14. Tasks 3 and 5 are independent of each other; everything else is linear.
- The eval package ships inside the container image (it's under `app/`) but imports the model stack only inside `predict` — the offline/CPU constraint (§8) holds by construction.




