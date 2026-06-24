# Notes-Block Extraction — Design

**Date:** 2026-06-24
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

On a representative customer drawing (`T1025300_B`), Sindri produced 17
characteristic rows with several visible extraction errors. The most
prominent class of failure: small balloons inside the **general-notes table**
(the bullets numbered `1`, `2`, `3` inside note `101`) were treated as
page-level dimension callouts. Examples from the run:

- Balloon `1` next to `CONTACT AREA PLANARITY 0,2mm` → emitted as
  `Distance 16` (the `16` likely lifted from a neighbouring `Rz16`).
- Balloon `2` next to `CONTACT AREA SURFACE QUALITY 2,5×5 Rz16` → emitted as
  `Distance 5 ±0,16`.
- Balloon `3` next to `PART FREE OF GREASE AND OIL` (text-only) → emitted as
  `Flatness 0 / +7 / 0`.

The root cause is that the general-notes table is not modelled by the
pipeline: `app/pipeline/notes.py` exists with a row-splitter, but
`app/pipeline/extract.py` does not call it on the production path. The notes
block is just a region of pixels on the page, so the VLM detector picks up
its inline bullets as ordinary callouts and the parser forces them into the
numeric `nominal/upper/lower` schema they don't fit.

This design adds a dedicated notes-block path that runs before the main
detector, **masks the located block on the image passed to
`detect_characteristics`**, and surfaces the parsed notes as a parallel
output (separate from the main characteristics table).

## Goals

- Locate the general-notes table on the page and read it once as structured
  data: numbered notes (`101`, `102`, …), with inline numbered sub-bullets
  (`1.`, `2.`, …) attached to their parent note, in both EN and DE columns.
- Prevent the inline sub-bullets from leaking into `detect_characteristics`
  by masking the notes-block region before that pass.
- Validate boxed `10x` reference balloons on the views against the parsed
  block (an unknown reference becomes a `needs_review` reason).
- Render the notes as a parallel section in the review UI and a separate
  sheet in the Excel export — not as rows in the main characteristics
  table.

## Non-Goals

- No new VLM model or weights. The existing Qwen2.5-VL backend is reused.
- No change to balloon stamping/placement (`place.py`, `ballooned_pdf.py`).
- No change to the customer-side characteristics template (FS 2230-0009);
  the Notes sheet is additive.
- No retroactive migration of past Excel exports.
- No change to symbol-anchored cropping, tolerance-grammar parsing, or
  two-pass dimension reconciliation. Those are separate failure classes
  identified in the same run and will get their own specs.

## Architecture

```
render_page
   │
   ├──▶ locate_notes_block(image, backend)           # VLM proposes, CV snaps
   │        returns: NotesBlockRegion | None
   │
   ├──▶ read_notes_block(image, region, backend)     # one VLM read of block
   │        returns: raw bilingual text (tab-separated, see prompt)
   │
   ├──▶ parse_notes_block(raw, region)
   │        returns: NoteBlock with notes + sub-bullets linked
   │
   ├──▶ mask_region(image, region) ──▶ masked_image
   │
   └──▶ detect_characteristics(masked_image, backend)
            │
            └──▶ parse_value, review_flags, balloons     (unchanged)

extract() returns ExtractionResult { characteristics, notes: NoteBlock | None }
```

Three new modules under `app/pipeline/`:

- `notes_block.py` — replaces the thin `notes.py`. Holds the locator,
  reader, parser, masker, and `review_flags_note`.
- `geom.py` — tiny refactor home for the existing `_x_aligned`,
  `_y_close`, `_union`, `_iou` helpers currently duplicated between
  `detect.py` and `boxes.py`. Both modules import from here; no behaviour
  change.
- (no separate `mask.py` — masking is a five-line helper kept inside
  `notes_block.py`.)

`extract.py` orchestrates the two paths and assembles the
`ExtractionResult`.

## Components

### 1. Data model — `app/models.py`

```python
class Note(BaseModel):
    pos: int                          # 101, 102, … for top-level; 1, 2, … for sub-bullets
    parent_pos: Optional[int] = None  # set for sub-bullets (1, 2, 3 → parent 101)
    sub_index: Optional[int] = None   # 1, 2, 3 within a parent; None for top-level
    text_en: str = ""
    text_de: str = ""
    raw_text: str = ""
    box: Optional[Tuple[float, float, float, float]] = None  # image-space row box
    confidence: float = 0.0
    needs_review: bool = False
    review_reasons: List[str] = []

class NoteBlock(BaseModel):
    region: Tuple[float, float, float, float]   # outer box of the notes table
    notes: List[Note] = []                      # flat list; linkage via parent_pos
```

`Characteristic` gains a single optional field:

```python
    note_ref_pos: Optional[int] = None   # set when subtype == "note_ref"
```

`extract()` return type changes:

```python
class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
```

A separate `Note` model (rather than reusing `Characteristic`) keeps both
classes focused on their actual shape. Notes have bilingual bodies,
parent/child structure, and no nominal/upper/lower — forcing them through
`Characteristic` would leave most fields empty and require the review
policy to branch on `char_type == "Note"`. A separate model puts the
notes policy next to the notes data.

### 2. Locator — `app/pipeline/notes_block.py: locate_notes_block`

```python
def locate_notes_block(image, backend) -> Optional[NotesBlockRegion]:
    ...
```

Three steps. Any step that fails returns `None`; never raises.

1. **VLM proposal.** Reuse the existing tile-grid pass over the page.
   Collect detections of `kind == "note"`. Cluster adjacent note
   detections into one bounding region using `geom._x_aligned`,
   `geom._y_close`, `geom._union`. If zero note detections, return
   `None` — the page has no notes block, and the downstream flow
   handles that cleanly.
2. **CV snap.** Pass the VLM-proposed rectangle to
   `boxes._find_rectangles`. If a CV rectangle overlaps the proposal
   with IoU ≥ 0.4 and is larger, snap the region to the CV rectangle
   (it sees the actual frame edge). Otherwise keep the VLM proposal,
   padded by 8 px to absorb minor edge drift.
3. **Column inference.** Inside the snapped region, find the vertical
   column divider with the column-ink heuristic already used by
   `boxes._count_cells`. Two columns is the expected case (EN left,
   DE right); one column means a single-language block. Both are
   supported. Output:

```python
@dataclass
class NotesBlockRegion:
    outer_box: Tuple[int, int, int, int]
    lang_columns: List[Tuple[int, int]]   # x-ranges per language column
```

### 3. Reader — `app/pipeline/notes_block.py: read_notes_block`

A single VLM call on the cropped region, with a dedicated prompt added to
`app/pipeline/ocr/vlm_backend.py`:

```
This image is the general-notes table from a mechanical engineering
drawing. Each row begins with a 3-digit number (101, 102, …) followed by
the English note and then the German note. Some rows contain inline
numbered sub-bullets (1., 2., 3., …) — preserve them with their numbers.
Output one row per line, in the form:
  <pos>\t<english>\t<german>
For sub-bullets, prefix with the parent pos: e.g. "101.1\t<en>\t<de>".
No prose, no headers, no explanations. Use a comma as the decimal
separator.
```

The two columns are read in one shot; asking the model to align
EN↔DE rows once is more reliable than reading the two columns
separately and stitching them by index. When `lang_columns` has one
entry, the prompt collapses to a single text column (parser
tolerates an empty DE).

### 4. Parser — `app/pipeline/notes_block.py: parse_notes_block`

```python
_ROW_RE    = re.compile(r"^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$")
_SUBROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\.(\d+)\t([^\t]*)\t?(.*)$")
```

Per line, in order:

- `_SUBROW_RE` match → `Note(pos=child_idx, parent_pos=parent,
  sub_index=child_idx, text_en, text_de)`.
- `_ROW_RE` match → `Note(pos=N, text_en, text_de)`.
- Otherwise drop silently (non-fatal pipeline convention).

Output `NoteBlock(region, notes=[…])` with the flat list. Children
remain discoverable via `parent_pos`/`sub_index`; the UI and Excel
renderers reconstruct the indentation from those fields.

### 5. Review policy — `app/pipeline/notes_block.py: review_flags_note`

Mirrors `app/pipeline/review.review_flags`. A note is flagged when any of:

- `raw_text` blank → `"empty read"`.
- Both `text_en` and `text_de` columns were expected (two-column block)
  but one is empty → `"missing translation"`.
- Sub-row whose `parent_pos` is not present among parsed top-level notes
  → `"orphan sub-bullet"`.

A pure function, tested in isolation.

### 6. Masker — `app/pipeline/notes_block.py: mask_region`

```python
def mask_region(image: Image.Image, region: NotesBlockRegion) -> Image.Image:
    out = image.copy()
    ImageDraw.Draw(out).rectangle(region.outer_box, fill="white")
    return out
```

White-fill (page background), not transparent, because the downstream
detector reasons about ink-on-paper. The mask is applied to a **copy**;
the original image is preserved for any later manual re-reads through
`/api/read_region`.

### 7. Orchestration — `app/pipeline/extract.py`

`extract()` becomes (sketch):

```python
def extract(pdf_path, work_dir, dpi=300, backend=None) -> ExtractionResult:
    ...
    image = Image.open(render.png_path).convert("RGB")

    region = locate_notes_block(image, backend)
    notes_block = None
    if region is not None:
        raw = read_notes_block(image, region, backend)
        notes_block = parse_notes_block(raw, region.outer_box)
        for n in notes_block.notes:
            top_level = {x.pos for x in notes_block.notes if x.parent_pos is None}
            n.needs_review, n.review_reasons = review_flags_note(
                n, two_columns=len(region.lang_columns) == 2,
                known_parents=top_level,
            )
        image_for_detect = mask_region(image, region)
    else:
        image_for_detect = image

    detections = detect_characteristics(image_for_detect, backend)
    # ... (existing per-detection read / parse / review_flags loop) ...

    # Validate boxed note-references against parsed notes.
    known_positions = ({n.pos for n in notes_block.notes if n.parent_pos is None}
                       if notes_block else set())
    for c in results:
        if c.subtype == "note_ref":
            try:
                c.note_ref_pos = int((c.raw_text or "").strip())
            except ValueError:
                pass
            if c.note_ref_pos is not None and c.note_ref_pos not in known_positions:
                c.review_reasons.append("unknown note reference")
                c.needs_review = True

    number_characteristics(results)
    place_balloons(results)
    return ExtractionResult(characteristics=results, notes=notes_block)
```

### 8. API — `app/main.py`

The extract endpoint returns the new shape additively:

```json
{
  "characteristics": [ ... ],
  "notes": {
    "region": [x0, y0, x1, y1],
    "notes": [
      { "pos": 101, "parent_pos": null, "sub_index": null,
        "text_en": "...", "text_de": "...", "needs_review": false,
        "review_reasons": [] },
      { "pos": 1, "parent_pos": 101, "sub_index": 1,
        "text_en": "CONTACT AREA PLANARITY 0,2mm",
        "text_de": "KONTAKTBEREICH EBENHEIT 0,2mm",
        "needs_review": false, "review_reasons": [] }
    ]
  }
}
```

`notes` is `null` when no block was detected. `/api/read_region`
(manual re-read on a user-drawn crop) is unchanged.

### 9. UI — `app/static/app.js`, `app/static/index.html`

Two sections in the review view:

1. The existing characteristics table — unchanged columns. Rows where
   `note_ref_pos` is set get a small "→ note 101" indicator linking
   (anchor scroll) to the matching row in the notes section.
2. A new collapsible **Notes** section below: `Pos | EN | DE | needs-review`.
   Sub-bullets render indented under their parent; the `Pos` cell shows
   `101.1`, `101.2`, … Cells are contentEditable, same edit model as the
   main table. The existing `.low` row class is reused for any note where
   `needs_review === true`.

### 10. Excel — `app/excel.py`

Two sheets:

- `Characteristics` — existing columns. `note_ref_pos` is appended as a
  text suffix to the existing comment column (`→ note 101`) rather than
  a new column, to avoid breaking the customer template.
- `Notes` — `Pos | English | German`. Sub-bullets indented; `Pos` formatted
  as `<parent>.<sub>` (e.g. `101.1`).

The single-sheet customer template (FS 2230-0009) is preserved for the
characteristics half.

## Tests

- `tests/test_notes_block.py` (new):
  - `parse_notes_block` cases: single-language, bilingual, sub-bullets,
    malformed lines silently dropped.
  - `review_flags_note` cases: empty read, missing translation, orphan
    sub-bullet, all-clear.
  - `mask_region` idempotence and that the masked image differs from the
    original only inside the region.
  - `locate_notes_block` with a stubbed backend that returns canned
    `kind=note` detections — asserts the cluster→snap→column-infer flow
    end-to-end without invoking a real VLM.
- `tests/test_extract_notes_integration.py` (new): the `T1025300_B`
  fixture end-to-end with a recorded backend stub. Asserts:
  - balloons `1`, `2`, `3` do **not** appear in `characteristics`;
  - the parsed `NoteBlock` contains notes `101..105`;
  - note `101` has three sub-bullets;
  - a boxed `101` callout on the view yields a characteristic with
    `subtype == "note_ref"`, `note_ref_pos == 101`, no
    `"unknown note reference"` flag.
- `tests/test_excel.py` (extension): the Notes sheet exists, headers
  match, sub-bullet `Pos` formatting is `101.1`/`101.2`.

## Risks & Mitigations

- **VLM mis-locates the notes block.** Mitigation: the CV-snap step
  corrects boundary drift; if both steps fail, the locator returns `None`
  and the pipeline behaves exactly as today (no regression, just no notes
  surface). The integration test exercises this fallback.
- **Notes prompt mis-aligns EN/DE rows.** Mitigation: the prompt asks for
  tab-separated triples per line so the model performs the alignment
  once. `review_flags_note` flags any row with one column empty when two
  were expected, so any mis-alignment surfaces in the UI rather than
  silently corrupting the output.
- **Customer template breakage.** Mitigation: `Notes` is a new sheet;
  the existing `Characteristics` sheet keeps its column order. The
  `note_ref_pos` indicator goes into the existing comment column as a
  suffix.

## Open questions

None at design time.
