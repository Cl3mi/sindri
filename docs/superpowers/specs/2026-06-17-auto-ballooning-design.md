# Auto-Ballooning Bare Drawings — Design

**Date:** 2026-06-17
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

Sindri today is a *balloon reader*: it assumes a technical-drawing PDF already
has inspection balloons (blue circle + sequence number + arrowhead) drawn on it,
locates each balloon's number and arrow, reads the dimension the arrow points
at, and exports an inspection `.xlsx`.

The real workflow produces **bare, un-ballooned drawings**. On a bare drawing
none of the inputs the current pipeline depends on exist:

- `extract_anchors` keys off balloon *numbers* in the PDF text layer — those
  numbers only exist because someone added the balloons.
- `label_balloons` segments *blue* balloon graphics — a bare drawing is
  black-on-white line-art.

Investigation of the one available sample (`sample.pdf`, which *is* ballooned)
confirmed the constraint that shapes this design:

- Text layer = **56 characters: only the 22 balloon numbers.**
- **6,272 vector paths, 0 raster images.** Every dimension value, GD&T frame,
  title block, and note is **outlined vector line-art, not selectable text.**

So even on a "vector" PDF there is **no dimension text layer to exploit** — which
is why the app already has a VLM OCR backend reading rendered pixels. The
vector-vs-scanned distinction is therefore moot for detection: detection must
work from the rendered page image either way (a vector export simply yields
sharper pixels than a scan).

This design adds the missing capability: **detect every inspection characteristic
on a bare drawing, number it, place a balloon, read its value, and produce both
an inspection `.xlsx` and a ballooned PDF** — with a human-review step to correct
the inevitable detection/read errors.

## Requirements

- **Input:** bare, un-ballooned drawings. Rendered to a high-DPI image for all
  processing; no usable dimension text layer is assumed.
- **Scope:** full FAI — detect *all* inspection characteristics (dimensions,
  tolerances, GD&T frames, surface finishes, notes, material/process specs).
  **Recall is the priority**: a missed characteristic is an inspection escape.
- **Engine:** Qwen2.5-VL (the existing `VLMBackend`). GPU is guaranteed in the
  target deployment; **no CPU-only detection fallback** is in scope.
- **Output:** inspection `.xlsx` **and** a ballooned PDF (copy of the source with
  numbered balloons + leader lines drawn on). Delivered via **two endpoints /
  two download buttons.**
- **Review:** full editing — click-to-add a missed balloon, delete false
  positives, drag to reposition, edit transcribed values.

## Approach (chosen: B — two-stage detect → read)

Separate **"where is a callout"** (detection) from **"what does it say"**
(transcription), reusing the existing, tested transcription + parser + Excel tail
unchanged.

Rejected alternatives:

- **A — single-pass VLM (detect + read in one whole-page call):** simplest, but a
  large drawing must be downscaled to fit the model, destroying recall on small
  callouts and entangling detection with read errors. Wrong fit for full-FAI
  recall.
- **C — classical CV detection (leader lines / arrowheads / frame rectangles) +
  VLM read:** deterministic and precise but brittle per-template; notes, material
  specs, and many callouts have no reliable geometric signature. High effort, low
  generality.

## Architecture & data flow

```
render_page (300 dpi PNG + scale)
   │
   ▼
detect_characteristics(image, backend)        ← NEW: app/pipeline/detect.py
   │   tile page → VLM detect per tile → map to page coords → dedupe/merge
   ▼
candidates: [Detection{box, kind, conf}]
   │
   ▼  for each candidate
crop(box) → backend.read_region → parse_value   ← REUSED unchanged
   │
   ▼
number_and_place(characteristics)              ← NEW: app/pipeline/place.py
   │   spatial sort → pos = 1..N; offset balloon_xy + leader line
   ▼
rows: [Characteristic{id, pos, kind, values…, balloon_xy, target_region, source}]

on export:
   rows → write_workbook            → inspection.xlsx        (/api/export)
   rows → render_ballooned_pdf      → ballooned.pdf          (/api/export/pdf)
```

### Module changes

| Module | Change |
|---|---|
| `app/pipeline/detect.py` | **NEW** — tiling, VLM detection, coord-mapping, dedupe/merge |
| `app/pipeline/ocr/vlm_backend.py` | **+** `detect_regions(image)` (structured-JSON detection prompt), distinct from `read_region` |
| `app/pipeline/place.py` | **NEW** — spatial numbering + balloon placement / leader geometry (pure functions) |
| `app/pipeline/ballooned_pdf.py` | **NEW** — draw balloons onto a PDF copy via `fitz` |
| `app/pipeline/extract.py` | **REWRITE** — orchestrate detect→read→number→place |
| `app/pipeline/anchors.py`, `app/pipeline/balloons.py` | **RETIRE** — superseded; depend on pre-existing balloons/text that bare PDFs lack |
| `app/models.py` | **+** `id`, `kind`, `source` fields on `Characteristic` |
| `app/main.py` | **+** `/api/read_region`, **+** `/api/export/pdf` |
| `app/static/app.js`, `index.html` | full-edit review UI (add / delete / move + edit values) |

The read→parse→Excel tail (`read_region`, `parse_value`, `write_workbook`,
`extract_notes`) is reused unchanged.

## Detection stage (`detect.py` + VLM `detect_regions`)

The one genuinely new, recall-critical unit. Three concerns:

### Tiling (preserve resolution for small callouts)

A 300-dpi A3 page is ~3500×5000 px; sending it whole forces a downscale that
loses tiny tolerances. The page is sliced into overlapping tiles, detection runs
per tile.

- Defaults (tunable module constants): tile **1280×1280**, **15% overlap**.
  Overlap guarantees a callout split by a tile edge appears whole in a neighbor.
- Each tile is a high-res crop of the rendered PNG; tile-local detections map
  back to page-space by adding the tile origin.

### VLM detection call — new `detect_regions(image) -> list[Detection]`

Distinct from `read_region` (which transcribes). The prompt asks the model to
return **structured JSON only** — every inspection callout in the tile:

```json
[{"box":[x0,y0,x1,y1],"kind":"dimension|gdt|surface|note|material","text":"<verbatim, optional>"}]
```

- `kind` is coarse and drives the parser `hint=`. `text` is opportunistic and
  **not trusted** — Stage 2 re-reads every crop with the tested `read_region`.
  Detection's job is purely *where + what sort*.
- Output parsed defensively: malformed/invalid JSON from a tile → log to stderr,
  skip that tile, never fail the upload.
- `Detection` dataclass: `box` (page-space px), `kind` (str), `conf` (float).

### Merge / dedupe across tile overlaps

The same callout detected in two overlapping tiles must collapse to one.

- `dedupe(detections, iou_thresh=0.5)` — pure function, greedy NMS: sort by
  confidence, suppress any later box with IoU ≥ threshold against a kept box.
  Cross-`kind` overlaps are kept separate (a Ø dimension and a GD&T frame may
  legitimately overlap).
- `merge_adjacent(detections)` — pure function: boxes of the same `kind`,
  horizontally aligned and within a small vertical gap, merge into one so the
  crop captures a full stacked callout (tolerance stacked over a nominal).

**Output:** a deduped `list[Detection]` in page-space pixels.

### Backend requirement

`detect_regions` lives on `VLMBackend` only. If the active backend is Tesseract,
detection is impossible — `extract()` raises, surfaced by `/api/upload` as
`400 "auto-ballooning requires the VLM backend"` rather than silently producing
nothing.

## Read, number & place (`extract.py` + `place.py`)

### Stage 2 read (reused)

For each deduped `Detection`: crop its box from the 300-dpi image, run the
existing `read_region` → `parse_value`, passing detection `kind` as the parser
`hint` (`material`→`hint="material"`, `gdt`→`hint="flatness"` where applicable).
Vertical callouts use the existing `_best_read` two-rotation trick. Yields a
`Characteristic` with `target_region = box`, `char_type`, values, `confidence`.

- A read failure (exception/empty) does **not** drop the callout: it becomes a
  row with empty values + `confidence=0.0`, balloon still placed. Recall is never
  sacrificed to a bad read.

### Numbering (`place.py`, pure function)

Sort characteristics into reading order — top-to-bottom in horizontal bands (a
y-tolerance groups items into a "row"), left-to-right within each band — then
assign `pos = 1..N`.

- **Flat sequential numbering (1..N).** The sample's 100-series notes convention
  (101/103/104) is template-specific; replicating it generically is **deferred
  (YAGNI)**.

### Placement (`place.py`, pure function)

For each characteristic, `balloon_xy` = a point offset from the callout box into
adjacent space (default: up-and-left of the box, fixed offset), plus a leader
line from the balloon edge to the box. Placement is deliberately simple; overlaps
are expected and fixed by dragging in review. No whitespace-finding (low ROI
given the review step).

### Resulting row

```
Characteristic{ id, pos, source="auto", kind, char_type,
                nominal, upper_tol, lower_tol, raw_text, confidence,
                balloon_xy (marker + leader origin),
                target_region (the box that was read) }
```

`id` = stable per-row uuid hex so the UI can add/delete/move/edit rows
unambiguously and the server can re-read a specific row.

## Outputs

### Excel — unchanged

`write_workbook(rows, out)` already produces the inspection sheet from
`Characteristic` rows.

### Ballooned PDF — new `app/pipeline/ballooned_pdf.py` (`fitz`)

- Open the original `input.pdf`, page 0.
- For each row, convert image-space coords to PDF points (`÷ RenderResult.scale`).
- Draw: a leader line from `balloon_xy` to the `target_region` edge, a circle at
  `balloon_xy`, and the `pos` number centered in it (blue, matching the sample).
- Save a copy as `ballooned.pdf` in the session dir. **Source PDF never mutated.**
- Coords clamped to the page rect.

### Delivery — two endpoints

- `POST /api/export` → `inspection.xlsx`
- `POST /api/export/pdf` → `ballooned.pdf`

UI presents two download buttons. Both receive the final `rows` from the client,
so xlsx and ballooned PDF always match what is on screen.

## API

| Endpoint | Change | Purpose |
|---|---|---|
| `POST /api/upload` | rewritten internals | detect→read→number→place; returns `{session_id, image_url, rows[]}`, each row with `id, source, kind` |
| `POST /api/read_region` | **NEW** | body `{session_id, box}` (image-space) → crop + `read_region` + `parse_value` → one `Characteristic`. Backs manual-add and re-read. Empty/off-page box → clamp; degenerate → 400 |
| `POST /api/export` | unchanged shape | `{session_id, rows}` → `inspection.xlsx` |
| `POST /api/export/pdf` | **NEW** | `{session_id, rows}` → `ballooned.pdf` |

## Review UI (full edit)

`app/static/app.js` + `index.html`:

- **Markers** keyed by row `id`, drawn with `pos` number and a leader line to
  `target_region`.
- **Add (recall safety net):** click empty drawing → drag a box around the missed
  callout → call `/api/read_region` → new row prefilled (values + `target_region`),
  `source="manual"`, `pos` appended. This is the primary tool for catching
  detection misses on a full-FAI drawing.
- **Delete:** remove a false-positive balloon → drops its row.
- **Move:** drag a marker → updates `balloon_xy` (cosmetic + leader origin; does
  not change what was read).
- **Edit values:** existing editable grid stays, two-way bound to rows by `id`.
- **Renumber:** after add/delete the client re-runs the spatial sort so `pos`
  stays reading-ordered.
- Low-confidence rows keep the existing `<0.6` highlight to focus review.

## Error handling

Principle: never lose a characteristic, never fail the whole upload on a local error.

| Failure | Behavior |
|---|---|
| Active backend is Tesseract (no VLM) | `extract()` raises; `/api/upload` → 400 *"auto-ballooning requires the VLM backend"*; surfaced in `#status` |
| Tile returns malformed/invalid detection JSON | log to stderr, skip tile, continue |
| Zero detections on the page | return `{rows: [], image_url}`; UI shows drawing for fully-manual ballooning — not an error |
| Per-crop `read_region` throws/empty | row created with empty values, `confidence=0.0`, balloon still placed |
| Notes/material region read fails | unchanged: logged, skipped |
| Ballooned-PDF coord out of page bounds | clamp to page rect |
| `/api/read_region` box off-page / degenerate | clamp; empty → 400 |

## Testing

pytest, mirroring the existing suite.

- **Pure functions (no model):** tiling geometry, tile-local→page coord mapping,
  `dedupe`/NMS (IoU), `merge_adjacent`, spatial numbering, balloon/leader
  placement, image↔PDF-point coord round-trip. Synthetic boxes.
- **Stub backend:** fake backend with canned `detect_regions` + `read_region`
  drives `extract()` end-to-end → assert rows, numbering, placement,
  `source="auto"`.
- **`ballooned_pdf`:** generate from known rows, re-open with `fitz`, assert
  expected drawn objects / leader count; assert source PDF unmodified.
- **API:** `/api/read_region` (stub) returns a parsed `Characteristic`;
  `/api/export` and `/api/export/pdf` return correct content types;
  backend-missing path returns 400.
- **Reused, kept green:** `test_parser`, `test_excel`, `test_models`,
  `test_notes`, `test_render`.
- **Removed:** `test_anchors`, `test_balloons` (modules retired).
- **GPU-gated integration:** real `detect_regions` against a bare drawing behind a
  marker that skips without a GPU/model — not in CI default.

### Known coverage gap

There is no *bare* PDF fixture yet (the only sample is pre-ballooned; its 22
balloon-number text spans are its only text). Automated detection tests therefore
run against the **stub backend**, not the real model. A GPU-gated end-to-end check
is added once real bare drawings arrive. Detection-quality (recall/precision)
tuning of tile size, overlap, IoU threshold, and the detection prompt is expected
to require those real drawings.

## Out of scope / deferred

- CPU-only detection fallback (GPU guaranteed).
- Template-specific 100-series note numbering (flat 1..N used instead).
- Whitespace-aware balloon placement (simple offset + human drag instead).
- Multi-page drawings (page 0 only, matching current behavior).
- An "already-ballooned" mode (the retired `anchors`/`balloons` path); bare PDFs
  are the only stated input.
