# Robust Boxed-Callout Detection & Reading — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

Client feedback names "numbers in boxes" as the single hardest case to extract
reliably (project brief §7), and it is the priority area for improvement.
Inspecting the real sample drawings in `test_docs/` (`T1025206_D`, `T1026449_C`,
…) shows that "boxed callout" is not one thing but **three distinct sub-types**,
all currently mishandled:

1. **GD&T feature-control frames** — multi-cell boxes such as `⊕ | Ø0.1 | A`
   (true-position, tolerance zone Ø0.1, datum A). Common in these drawings.
2. **Theoretical / basic dimensions** — a single value in a single rectangle
   (theoretisch exaktes Maß / TED). The spec's named case.
3. **Boxed note-references** — small boxed 100-series integers (`101`, `102` …)
   that point into the internal notes table, stamped on the geometry.

Two cross-cutting facts make the current pipeline fail on these:

- **The frame border corrupts the read.** `extract.py` crops the detector's
  box and hands the whole thing — frame lines included — to the OCR/VLM. The
  border lines get read as characters, and `merge_adjacent` can split or fuse
  cells of a multi-cell frame.
- **Period decimals are not parsed.** The real drawings use a period decimal
  separator (`16.5`, `Ø6.6`, `0.2`), but `parser._NUM` only matches *comma*
  decimals (`\d+(?:,\d+)?`). Today correctness depends entirely on the VLM
  rewriting `.`→`,` per crop (the read prompt asks it to) — fragile, and it
  breaks the Tesseract fallback path. On `6.6` the regex yields `6` then `6`.

The detector currently has no notion of a "box," so it cannot route a boxed
value to the correct export columns. A GD&T frame, a theoretical dim, and a
note-ref each need different column treatment, and right now they all fall
through `parse_value`'s generic number logic.

## Goals

- Detect all three boxed sub-types reliably, recovering boxes the VLM misses.
- Read box contents from a **frame-stripped** crop so border lines no longer
  corrupt the value.
- Map each sub-type to the correct export columns:
  - **GD&T frame** → `Nennmaß=0`, `O-TOL=`zone value, `U-TOL=0`
    (the spec's Flatness-row convention: `Flatness 0 / 0,1 / 0`).
  - **Theoretical** → nominal as-is, tolerances empty (spec §6.4).
  - **Note-ref** → routed to the notes path (Merkmal = text, numeric cols empty).
- Parse values deterministically regardless of decimal separator
  (`16.5`→`16,5`), independent of what the VLM emits.
- Also handle the sibling **reference / Klammermaß** case `(1)`, `(20)`
  (parenthesized, no box) → nominal as-is, tolerances empty. Present in the
  samples and cheap to add alongside the theoretical branch.

## Non-Goals (sibling threads, explicitly out of scope)

Sub-numbering (`1A`, `1.1`), per-view clockwise traversal order, color /
LAB-TC stamp categories, populating the real `FS 2230-0009.xlsm` template, the
confidence/"needs-review" overhaul, and general-tolerance (ISO 2768) lookup.
These are tracked separately; this design touches none of them.

## Approach

Hybrid / ensemble: keep the existing VLM tile detector for broad coverage, and
add a **deterministic OpenCV rectangle pass** (run once on the full page) whose
results are merged into the VLM detections before reading. Deterministic
geometry where we can have it (frame location, cell count, clean crop),
model-driven reading where we must. `opencv-python-headless` is already a
dependency.

### Data flow

```
render → ┌ VLM tile detect ┐
         │                 ├─ merge/dedupe → read (frame-aware for boxes)
         └ CV box detect ──┘                   → parse_value(subtype) → number/place
```

## Components

### 1. `app/pipeline/boxes.py` (new)

`detect_boxes(image) -> List[BoxDetection]`. Pure function over the rendered
page image; no GPU.

- Binarize (adaptive threshold) → extract horizontal and vertical line segments
  via morphology → find enclosed rectangles and any internal cell dividers.
- Each `BoxDetection` carries:
  - `outer_box` (x0,y0,x1,y1) — page-space, used for stamping and dedupe.
  - `inner_box` — `outer_box` inset by a small margin so frame lines are
    excluded from the read crop.
  - `cells` — count and per-cell boxes (≥2 ⇒ candidate GD&T frame).
  - `subtype_guess` — `gdt` when ≥2 cells; otherwise `theoretical` or
    `note_ref`, refined after reading by box size + 100-series content.
  - `conf` — geometric confidence (e.g. how rectangular / how complete the
    border is).

### 2. `app/pipeline/detect.py` (changes)

- Add `"theoretical"` to `_KINDS`.
- Extend the `Detection` dataclass with optional `inner_box`, `cells`,
  `subtype` (default `None`) so existing construction sites are unaffected.
- After the VLM tile pass, run `detect_boxes(full_page_image)` and merge:
  - **CV box overlapping a VLM detection** (IoU > 0.5) → keep the CV box (it
    carries the clean crop + structure), suppress the VLM duplicate. This is
    the "don't stamp twice" guard.
  - **CV box with no overlap** → add it (recovers what the VLM missed).
  - **VLM detection with no CV box** → unchanged.
- `detect_characteristics` returns the merged list with the new optional fields
  populated for boxed detections.

### 3. `app/pipeline/ocr/vlm_backend.py` + `app/pipeline/extract.py` (changes)

- In `extract`, when a detection has an `inner_box`, crop the **inner_box**
  (frame removed) for reading.
- Add a **GD&T-aware read prompt** to the VLM backend: transcribe the frame as
  `symbol tolerance datum`, e.g. `⊕ Ø0.1 A`. Theoretical and note-ref boxes
  reuse the existing transcription prompt on the clean inner crop.
- Pass the box `subtype` through to `parse_value` as the hint.

### 4. `app/pipeline/parser.py` (changes)

- **Decimal normalization:** widen `_NUM` to accept either separator,
  `r"[+\-±]?\d+(?:[.,]\d+)?"`, and normalize captured numbers `.`→`,` before
  storing nominal and tolerances. Deterministic, independent of the VLM.
- **`hint="theoretical"`:** parse the nominal, force `upper_tol="" `and
  `lower_tol=""`.
- **`hint="gdt"`:** generalize the existing flatness branch. Map the leading
  geometric symbol → `char_type` (Position, Flatness, Concentricity, …),
  set `nominal="0"`, `upper_tol=`the zone value (strip a leading `Ø`),
  `lower_tol="0"`.
- **Reference / Klammermaß:** body wrapped in parentheses `(…)` → nominal taken
  as-is from inside the parens, tolerances empty.

### 5. `app/models.py` (changes)

Add `subtype: str = ""` (`gdt|theoretical|reference|note_ref|""`) to
`Characteristic`, to drive export-column rules and a future review flag.
Backward-compatible — `excel.py` continues to use `char_type/nominal/tols`
unchanged.

### 6. Note-refs

Boxed 100-series integers are tagged `subtype="note_ref"` and routed to the
existing notes handling (`notes.py`): Merkmal = note text, numeric columns
empty. No new numbering or traversal logic is added here.

## Error handling

- `detect_boxes` follows the existing pipeline convention: any failure is
  logged to stderr and returns `[]` for that page — never fatal. A drawing with
  no detectable frames simply yields the current VLM-only behavior.
- A GD&T read that does not match the `symbol value datum` shape falls back to
  the generic transcription/parse path (mark-everything: a rough row beats a
  missing one, per brief §3.1).
- Frame-stripping uses a bounded inset; if `inner_box` collapses to empty
  (tiny box), fall back to reading `outer_box`.

## Testing

CPU / no-GPU unit tests:

- **`boxes.py`** — synthetic PIL-drawn images: a single rectangle, a multi-cell
  grid (GD&T), a small note-ref box. Assert detection count, cell count,
  `subtype_guess`, and that `inner_box` excludes the border.
- **`parser.py`** —
  - period decimals: `Ø6.6 +0.2 0` → `6,6 / 0,2 / 0`; `15 +0.05 -0.05` →
    `15 / 0,05 / -0,05`.
  - theoretical: boxed `20` → nominal `20`, tolerances empty.
  - reference: `(1)` → nominal `1`, tolerances empty; `(20)` → `20`.
  - GD&T: `⊕ Ø0.1 A` with `hint="gdt"` → `char_type` Position, `0 / 0,1 / 0`.
- **`detect.py`** — merge: an overlapping CV+VLM pair collapses to one detection
  carrying the CV structure; a non-overlapping CV box is added.

GPU-gated integration test (mirroring the existing `ff7427c` pattern) on a
`test_docs` PDF, asserting the `⊕ Ø0.1 A`, `(1)`, and `(20)` callouts surface in
the extracted output.

## Risks / open questions

- **CV rectangle tuning** across line weights and DPI; the morphology kernels
  may need a DPI-relative size. Mitigated by synthetic tests at the render DPI.
- **Frames whose border touches part geometry** may not close as a clean
  rectangle; those fall back to VLM-only detection (no regression).
- **Note-ref re-stamping policy** (re-number vs. preserve the 100-series number)
  is an open client question (brief §9); this design only tags and routes them,
  leaving the policy to the numbering thread.
