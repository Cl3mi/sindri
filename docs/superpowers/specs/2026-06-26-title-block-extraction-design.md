# Title-Block Extraction Design

**Date:** 2026-06-26
**Status:** Approved (design), pending implementation plan

## Problem

The bottom-right block of every drawing is the **title block (Schriftfeld)** — a grid of
bilingual key/value fields (Material/Werkstoff, Sheet/Blatt, Scale/Maßstab, Drawing-No,
Released/Freigabe, etc.). Today none of it is extracted: the pipeline produces dimension
characteristics and the numbered general-notes table, but the title block is ignored. The
user wants **every value on the page** captured, with heading↔value pairing preserved where
it exists.

### Confirmed constraints

- These CAD PDFs have **no text layer** — text is drawn as vector outlines (`get_text("words")`
  returns 0 words on `T1025206_D`, `T1025300_B`). The local Qwen VLM (`app/pipeline/ocr/vlm_backend.py`)
  is the only way to read it. No native-text shortcut exists.
- The title block is a ruled grid. Each cell contains **one field**: a small italic caption
  (bilingual, `English / German`) plus a prominent value. The caption sits at the **top** in
  most rows but at the **bottom** in the modification/version row (value `FC` with
  `Released / Freigabe` underneath). Therefore label↔value pairing happens **within a cell**,
  not across cells.
- OpenCV grid-line morphology reliably recovers the cells: a probe on a 300-DPI render of
  `T1025206_D` found 33 candidate cells with sensible dimensions.

## Approach

Chosen: **CV grid detection + per-cell VLM read** (approach #2 of the three considered;
single-pass structured read and full-page text dump were the alternatives). Per-cell reads
trade latency for accuracy — an accepted tradeoff.

The new path mirrors the existing notes-block path (`app/pipeline/notes_block.py`):
*locate region → read → parse/structure → flag for review → mask before the main detector*.

### Pipeline — `app/pipeline/title_block.py` (new)

1. **Locate** — find the title-block region by running grid-line morphology over the
   bottom-right quadrant of the page and taking the bounding box of the detected grid cells.
   Returns `None` on any failure (non-fatal pipeline convention, like the notes locator).
2. **Detect cells** — horizontal + vertical line kernels (`cv2.getStructuringElement` +
   `MORPH_OPEN`) → combined grid mask → invert → `connectedComponentsWithStats` → cell
   rectangles. Filter out: the outer frame, sub-threshold noise, and **ink-empty cells**
   (column/row ink density below a threshold) so blank and logo cells don't consume a VLM
   call. Sort cells top-to-bottom, then left-to-right, for stable reading order.
3. **Read each cell** — crop each surviving cell and call a new backend method
   `read_title_cell(crop)` with a dedicated prompt: *"This is one cell from a mechanical-drawing
   title block. It contains a small italic caption (a bilingual label in the form
   `English / German`) and a prominent value. The caption may appear above or below the value.
   Return JSON `{"label": ..., "value": ...}`; if the cell has only a value and no caption,
   return an empty label."* Tesseract fallback (`read_title_cell` falls back to `read_region`)
   for no-GPU environments.
4. **Structure** — parse each `{label, value}` into a `TitleField`. Split `label` on `/` into
   `label_en` / `label_de` (cheap; matches the bilingual theme). Keep `box`, `confidence`.
5. **Mask** — white-out the title-block region before the main dimension detector runs (exactly
   as `mask_region` does for the notes block), so captions and values cannot be misclassified
   as dimension callouts. This also improves the existing characteristic extraction.

### Loose-text catch (covers "all text on page")

A light pass for free text **outside** the title block (e.g. *"CONDITION AT THE MANUFACTURER'S
OPTION / IM ZUSTAND NACH WAHL DES HERSTELLERS"* on the left margin). Emit each such string as a
`TitleField` with an empty `label` (standalone value). This honors the "pairs where possible,
standalone otherwise" requirement. The bottom disclaimer row is already inside the grid and is
captured by the cell pass.

## Data model — `app/models.py`

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

class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
    title_block: List[TitleField] = []   # NEW
```

### Review flagging

Reuse the notes review-flag pattern (`review_flags_note`): a field with an empty value gets
`"empty value"`; a field with a value but no caption gets `"missing caption"` (informational,
so it surfaces in the UI but does not block). Loose-text catches (intentional empty label) are
not flagged for "missing caption".

## Surfacing (the seven existing seams, parallel to notes)

1. `app/pipeline/extract.py` — run title-block extraction, attach `title_block` to
   `ExtractionResult`, mask the region before detection.
2. `app/main.py` extract route — add `title_block` to the SSE `result` payload
   (`[t.model_dump() for t in result.title_block]`).
3. `app/main.py` `ExportRequest` — add `title_block: List[TitleField] = []`.
4. `app/excel.py` — new **"Title Block"** sheet: columns Label (EN), Label (DE), Value.
   `write_workbook` gains a `title_block` parameter.
5. `app/static/js/state.js` — store `state.title_block` from the result payload.
6. `app/static/index.html` + `app/static/js/table.js` — new collapsible **Title Block**
   section rendering Label-EN / Label-DE / Value, with review highlighting and contentEditable
   cells, mirroring the notes section.
7. (No persistent JSON store exists; data flows in-memory → SSE → browser state → export POST,
   so no storage changes are needed.)

## Cost / risk

- **Latency:** ~15–25 VLM calls per page (vs 1 for the notes block), mitigated by ink-empty-cell
  filtering. This is the accuracy-for-latency cost of the CV-grid approach and is accepted.
- **Mis-split:** dense or merged cells may mis-separate caption vs value. Mitigated by review
  flags plus editable cells in the UI.
- **Locate failure:** if grid detection fails, the path returns `None` and the rest of the
  pipeline runs unchanged (no title block produced) — never fatal.

## Testing (TDD)

Deterministic units get real tests; the non-deterministic VLM read is exercised via a **fake
backend** (the established pattern in `tests/conftest.py` and `tests/test_notes_block.py`):

- Cell-detection geometry on the sample PDFs (`test_docs/T1025206_D.pdf`): expected cell count
  range and that known cells fall in expected page regions.
- Caption/value parsing and `/`-split into `label_en` / `label_de`.
- Review flagging (`empty value`, `missing caption`, loose-text not flagged).
- Cell ordering (top-to-bottom, left-to-right).
- `app/excel.py` Title Block sheet contents and headers.
- `TitleField` / `ExtractionResult` model round-trip (model_dump / re-parse).
- Integration: fake-backend run of `extract()` asserts `title_block` is populated and the
  region is masked before detection.

## Out of scope

- Field-name normalization to a canonical schema (e.g. mapping `Sheet / Blatt` → `sheet`).
  Labels are emitted as printed. Can be layered on later.
- Multi-page title blocks (current pipeline is single-page, `page_index=0`).
