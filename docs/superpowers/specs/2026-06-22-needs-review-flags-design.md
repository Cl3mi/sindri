# Per-Row Needs-Review Flags — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

Sindri auto-detects inspection characteristics, reads each one, and presents the
rows in a web review table for the human to correct before export. The operating
philosophy is "mark everything, the human corrects" — so the single most valuable
thing the tool can do for the reviewer is **tell them which rows to look at and
why**.

Today it can't:

- `Characteristic.confidence` is effectively meaningless on the production path.
  The VLM backend returns a flat `0.9 if text else 0.0`
  (`app/pipeline/ocr/vlm_backend.py:78,95`) — only the Tesseract backend produces
  a real per-character score.
- The review UI already has the hook — `app/static/app.js:158` highlights rows
  where `confidence < 0.6` as `.low` — but because VLM confidence is always `0.9`
  or `0.0`, the highlight never fires except on a totally empty read.

So a reviewer must eyeball every row equally. This design computes a **meaningful,
reasoned needs-review signal** from concrete conditions observed at extraction
time, and surfaces it in the review UI.

## Goals

- Flag each row with `needs_review: bool` and a human-readable `review_reasons`
  list, computed from concrete extraction facts (not an opaque float).
- Drive the existing UI highlight off the flag and show the reason(s).
- Keep the policy in one independently-testable place.

## Non-Goals

- No Excel/`FS 2230-0009` change — the export format is unchanged (stamping/UI is
  the core deliverable; the flag is a UI aid).
- No auto-clearing of the flag on manual edit (the reviewer is already in the row).
- No change to how `confidence` itself is produced; we consume it as-is.

## Signals

A row is flagged when any of these hold:

1. **Empty / failed read** — the read returned no text (`raw_text` blank).
2. **Missing nominal** — a dimension-type row read *some* text but parsed no
   nominal value (a garbled read of a measurement).
3. **Rotation ambiguity** — for a vertical callout, the two 90° rotation
   candidates scored within a small epsilon, so the chosen orientation (and thus
   the read) is uncertain.
4. **Low OCR confidence** — the backend confidence is below threshold *and* text
   was read. (On the VLM path confidence is a flat sentinel, so this fires only on
   the Tesseract path; an empty VLM read is `0.0` but is already covered by
   signal 1.)

### Gating rules (avoid redundant/misleading reasons)

- "missing nominal" is reported only when text was read — an empty read is its own
  reason, not also a missing-nominal.
- "low OCR confidence" is reported only when text was read — an empty read is its
  own reason.

## Components

### 1. Data model — `app/models.py`

Add to `Characteristic` (requires `List` from `typing`):

```python
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty read", "missing nominal"]
```

Backward-compatible; both fields serialize to the UI via the existing
`model_dump()` calls in `app/main.py`.

### 2. Review policy — `app/pipeline/review.py` (new)

One pure function; the single home for the needs-review policy:

```python
from typing import List, Tuple
from app.models import Characteristic

# Measurement types that must carry a numeric nominal. GD&T/Flatness/Position
# (nominal "0"), Theoretical, Reference, Note and Material are intentionally exempt.
DIMENSION_TYPES = {"Distance", "Diameter", "Radius"}
LOW_CONF = 0.6


def review_flags(c: Characteristic, rotation_ambiguous: bool) -> Tuple[bool, List[str]]:
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

### 3. Surface the rotation signal — `app/pipeline/extract.py`

`_best_read` currently computes per-rotation scores and discards the ambiguity.
Change it to return `(text, conf, rotation_ambiguous)`:

- `rotation_ambiguous` is `True` when the crop is vertical (two rotation
  candidates) **and** the best two candidate scores differ by less than
  `ROTATION_EPS` (a module constant ≈ `0.15`).
- A non-vertical crop has a single candidate → `rotation_ambiguous = False`.

In the `extract` loop:

- The GD&T branch (`read_region_gdt`, horizontal frames) sets
  `rotation_ambiguous = False`.
- The default branch takes the new third return value from `_best_read`.
- After the `Characteristic` is fully populated (including `confidence`,
  `char_type`, `nominal`, `raw_text`), set:
  ```python
  c.needs_review, c.review_reasons = review_flags(c, rotation_ambiguous)
  ```

`raw_text` is already set by `parse_value` (it constructs
`Characteristic(pos=0, raw_text=raw)`), so `review_flags` can read the row's text
from `c.raw_text`.

### 4. Manual re-reads — `app/main.py`

The `/api/read_region` endpoint builds a single `Characteristic` from a user-drawn
region and sets its `confidence`. After it is built, apply:

```python
    c.needs_review, c.review_reasons = review_flags(c, rotation_ambiguous=False)
```

so re-read rows carry consistent flags. (Rotation handling is not available on this
manual path, hence `False`.)

### 5. Review UI — `app/static/app.js` and `app/static/index.html`

In `renderGrid` (`app.js`):

- Replace the dead confidence highlight with the flag:
  ```javascript
  if (r.needs_review) tr.className = "low";
  ```
- Show the reasons on hover and mark the row:
  ```javascript
  if (r.needs_review) tr.title = r.review_reasons.join(", ");
  ```
  and prefix a `⚠ ` marker to the Pos cell for flagged rows (e.g. render the Pos
  cell as `${r.needs_review ? "⚠ " : ""}${r.pos}`).

The existing `tr.low td { background: #fff7ed; }` rule (`index.html:25`) provides
the highlight; no new CSS is required. The `⚠` marker and `title` tooltip add the
"why".

## Error handling

- `review_flags` is pure and total — it never raises; missing/empty fields produce
  the appropriate reason or no reason. A row with all fields blank yields
  `["empty read"]`.
- No new failure modes are introduced in `extract`; `_best_read`'s added return
  value is computed from data it already has.

## Testing

- **`tests/test_review.py`** (new) — `review_flags` with:
  - empty `raw_text` → `(True, ["empty read"])`; and that "missing nominal" /
    "low OCR confidence" are NOT also added for an empty read.
  - a `Distance` row with text but blank `nominal` → `["missing nominal"]`.
  - a Tesseract-style row with text and `confidence` 0.4 → `["low OCR confidence"]`.
  - `rotation_ambiguous=True` → reasons include `"rotation ambiguity"`.
  - a clean `Distance` row (text, nominal, conf 0.9, not ambiguous) →
    `(False, [])`.
  - a GD&T/`Position` row with `nominal "0"` → not flagged for missing nominal.
  - combination: empty read + rotation ambiguity → both reasons, no "missing
    nominal".
- **`tests/test_pipeline_integration.py`** (existing; extract has no separate test
  module) — `_best_read` returns `rotation_ambiguous=True` when a stub backend
  scores both rotations equally on a vertical crop, and `False` for a horizontal
  crop; an `extract` run with a stub
  returning empty text yields a row with `needs_review=True` and
  `review_reasons == ["empty read"]`; a stub returning dimension text with no
  parseable nominal yields `["missing nominal"]`.

## Tunable constants

- `DIMENSION_TYPES` — the measurement types requiring a nominal.
- `LOW_CONF = 0.6` — Tesseract confidence threshold (matches the prior UI value).
- `ROTATION_EPS ≈ 0.15` — how close the two rotation scores must be to count as
  ambiguous.

## Out of scope (sibling threads)

The type-model/parser overhaul, general-tolerance lookup, and FS-2230 template
population remain separate.
