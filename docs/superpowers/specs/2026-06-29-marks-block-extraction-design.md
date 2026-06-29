# Marks-Block Extraction — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

Customer drawings carry a **Mark / Description** table in the top-right
corner of the page (header row "Mark / Note" + "Description / Beschreibung",
rows numbered `101…1xx`, each row written bilingually as English on the top
line and German underneath). The same numbers appear as plain text on the
views — they are not balloon callouts, just legend references — so the
table is the canonical legend for the drawing.

Today nothing in the pipeline targets this table. The existing
`app/pipeline/notes_block.py` extracts a *structurally identical*
general-notes table located **elsewhere** on the drawing (bottom area on
customer PDFs), driven by the VLM `detect_regions` "note" kind. It does not
also target the top-right Marks table, and the two tables are conceptually
distinct outputs that the reviewer wants to see and export separately.

This design adds a dedicated marks-block path that runs **alongside** the
existing notes-block path (independent — neither blocks the other), is
located by a deterministic top-right CV heuristic (no dependency on the
VLM locator's reliability), and surfaces the parsed marks as a new parallel
output: own UI section, own Excel sheet, own API field.

## Goals

- Locate the Mark / Description table reliably without depending on the
  VLM tile-detector — engineering drawings have standardized layouts, so a
  positional CV-only heuristic is appropriate.
- Read the located region once and parse it into structured EN/DE rows.
- Surface the result in three places, parallel to the existing notes
  feature: `ExtractionResult.marks`, a "Marks" Excel sheet, and a new
  collapsible "Marks" section in the right pane (above the Notes section).
- Mask the located region before `detect_characteristics` runs so the
  101… numbers in the table cannot be misclassified as note-ref balloons.
- Leave the existing notes-block path untouched — zero regression risk
  on the Notes feature.

## Non-goals

- No code-sharing refactor with `notes_block.py` in this iteration. The
  two parsers are structurally similar but independent; consolidation can
  happen later once a real divergence appears.
- No join between Marks rows and balloon characteristics. The Marks table
  is a standalone legend; the inspection table is not re-decorated with
  Mark descriptions.
- No translation. The table on the PDF is already bilingual.
- No editability of marks in the UI in this iteration (read-only display;
  matches Notes UI behaviour).

## Architecture

A new module `app/pipeline/marks_block.py` mirrors `notes_block.py` in
shape but differs in two places: the locator is CV-only positional (no VLM
detection kind), and there are no sub-bullets in the parser.

Public surface:

- `parse_marks_block(raw: str, region: tuple[float,float,float,float]) -> MarkBlock`
- `review_flags_mark(mark: Mark, two_columns: bool) -> tuple[bool, list[str]]`
- `locate_marks_block(image) -> MarksBlockRegion | None`
- `read_marks_block(image, region, backend) -> str`
- `mask_region(image, region) -> Image.Image` (identical helper; can be
  copied from notes_block to avoid coupling)

`MarksBlockRegion` holds `outer_box: tuple[int,int,int,int]` and
`lang_columns: list[tuple[int,int]]` (same shape as `NotesBlockRegion`).

### Locator (the key new piece)

```
locate_marks_block(image):
    boxes = detect_boxes(image)     # existing CV utility
    W, H = image.size
    # top-right quadrant: cx > 0.55*W and cy < 0.45*H
    candidates = [b for b in boxes
                  if center_x(b) > 0.55*W
                  and center_y(b) < 0.45*H
                  and area(b) >= MIN_AREA]
    if not candidates: return None
    pick = max(candidates, key=area)
    columns = _infer_columns(image, pick.outer_box)  # same helper as notes
    return MarksBlockRegion(outer_box=pick.outer_box, lang_columns=columns)
```

Tunables (encoded as module constants for easy adjustment from tests):

- `_TOPRIGHT_CX_MIN_FRAC = 0.55`
- `_TOPRIGHT_CY_MAX_FRAC = 0.45`
- `_MIN_AREA_FRAC = 0.02`  (table must occupy at least ~2% of the page —
  filters out title-block sub-cells and tiny fragments)

Never raises; any failure logs to stderr and returns `None`, matching the
notes-block non-fatal convention.

### Reader

Reuses the VLM backend's existing `read_notes_block` prompt verbatim —
the table shape is identical (3-digit Pos column + EN row over DE row).
No new prompt is introduced. If the backend lacks `read_notes_block`, it
falls back to `read_region`.

### Parser

Same row regex as notes (`^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$`) but
no sub-row regex. The user confirmed marks in their PDFs are flat. If a
sub-row appears anyway, the parser drops the line silently (non-fatal
convention), which is the safest default.

### Review flags

Reuses the notes-block logic minus the orphan-sub-bullet check:

```
empty read         -> needs_review
two_columns and (en empty or de empty) -> needs_review with "missing translation"
```

## Data model (`app/models.py`)

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
    marks: Optional[MarkBlock] = None   # NEW
```

## Pipeline integration (`app/pipeline/extract.py`)

Marks extraction runs as a stage that mirrors the notes-block stage and
sits next to it. Both stages contribute to the masking image before
`detect_characteristics`:

```
region_notes = nb.locate_notes_block(image, backend)
region_marks = mb.locate_marks_block(image)

notes_obj = _read_and_parse_notes(image, region_notes, backend) if region_notes else None
marks_obj = _read_and_parse_marks(image, region_marks, backend) if region_marks else None

image_for_detect = image
if region_notes is not None:
    image_for_detect = nb.mask_region(image_for_detect, region_notes)
if region_marks is not None:
    image_for_detect = mb.mask_region(image_for_detect, region_marks)

detections = detect_characteristics(image_for_detect, backend)
...
return ExtractionResult(characteristics=results, notes=notes_obj, marks=marks_obj)
```

### Note-ref validation

`review_flags(..., known_note_positions=...)` continues to validate
boxed `10x` balloons against **notes** positions only — Marks positions
do **not** extend `known_note_positions`. Rationale: the user said marks
on the views are plain text, not balloon callouts; if the VLM
mis-detects a `10x` plain-text number as a balloon, the existing
"orphan note-ref" review flag is the right outcome.

If a future drawing actually does balloon a mark number, we can revisit
and union the two known-position sets.

## API surface (`app/main.py`)

- The extraction response payload gains `"marks": result.marks.model_dump() if result.marks else None`.
- The `/export/xlsx` request body gains `marks: Optional[MarkBlock] = None`.
- `write_workbook(req.rows, out, notes=req.notes, marks=req.marks)`.

## Excel export (`app/excel.py`)

- New `_write_marks_sheet(ws, block: MarkBlock)` modelled on
  `_write_notes_sheet`. Columns: Pos / English / German. No sub-bullet
  prefixing.
- `write_workbook` gains a `marks: Optional[MarkBlock] = None` argument.
- Sheet order: **Inspection → Marks → Notes** (Marks appears first
  among the two reference sheets because it's the legend that explains
  the position numbers in the inspection sheet).

## UI

### HTML (`app/static/index.html`)

A new `<section id="marks-section" hidden>` is inserted **immediately
above** `<section id="notes-section">` in `#table-wrap`. Same DOM shape
as the notes section:

```html
<section id="marks-section" hidden>
  <div class="marks-header" id="marks-toggle">
    <svg class="caret" width="11" height="11"><use href="#i-chev-down"/></svg>
    Marks
    <span class="count" id="marks-count">0</span>
  </div>
  <table class="marks">
    <thead>
      <tr><th style="width:60px">Pos</th><th>English</th><th>German</th></tr>
    </thead>
    <tbody id="marks-body"></tbody>
  </table>
</section>
```

### CSS (`app/static/styles/components.css`)

Add a `.marks` rule mirroring the existing `.notes` rule (or, if
acceptable, apply the same class — to be confirmed during implementation
when looking at the actual CSS file).

### JS (`app/static/js/`)

- `state.js`: top-level `marks: MarkBlock | null` next to `notes`.
- `api.js`: include `marks` in the extraction response handling and in
  the `/export/xlsx` request body.
- `table.js` (or wherever notes rendering lives): new
  `renderMarks(block)` mirroring `renderNotes`, wired to
  `#marks-body` + `#marks-count`, with the same collapse/toggle behaviour
  on `#marks-toggle`.

## Testing

Mirror `tests/test_notes_block.py` where it exists:

- `parse_marks_block` unit tests:
  - Well-formed tab-separated input → expected `Mark` list.
  - Empty input → empty block.
  - Malformed lines → dropped silently.
- `locate_marks_block` unit tests on synthetic PIL images:
  - Single rectangle in the top-right quadrant → locator picks it.
  - Decoy rectangles in the bottom and centre → locator ignores them.
  - No top-right rectangle → returns `None` without raising.
- `review_flags_mark` unit tests:
  - Empty read → `needs_review=True, reasons=["empty read"]`.
  - Two-column with empty DE → `["missing translation"]`.
- Integration: end-to-end on a `test_docs/` PDF that has a marks table;
  assert `result.marks.marks` contains the expected positions and that
  the masking image hides the table region (verifiable by checking the
  masked image at those pixels is white).

## Failure modes & observability

- Locator returns `None` (no rectangle in the top-right quadrant, or
  CV `detect_boxes` raises) → `result.marks = None`, the rest of the
  pipeline runs unchanged, no exception propagates. UI hides the section.
- Reader returns empty / VLM error → `MarkBlock(region=…, marks=[])`,
  section shows with count 0 and a single placeholder row marked as
  `needs_review="empty read"` so the reviewer notices.
- All locator/reader exceptions log to stderr with the
  `[sindri.marks_block]` prefix for parity with notes.

## Open assumptions (recorded for review)

1. **No sub-bullets.** The screenshot shows flat rows only and no
   sub-bullets were observed; this is a default, not an explicit user
   confirmation. If a future drawing breaks this, the parser drops the
   row silently and a regression test will catch it. Adding sub-bullets
   later is a parser-only change (same shape as notes).
2. **Marks do not validate balloons.** Mark Pos numbers are not added to
   `known_note_positions` for note-ref validation. Revisit if a real
   drawing balloons a mark.
3. **Top-right quadrant thresholds (`0.55`, `0.45`, `0.02`)** are starting
   values; will be tuned against the `test_docs/` corpus during
   implementation.
4. **CSS class reuse vs. new class** (`.marks` vs. shared with `.notes`)
   to be decided against the actual stylesheet during implementation;
   doesn't affect the design.
