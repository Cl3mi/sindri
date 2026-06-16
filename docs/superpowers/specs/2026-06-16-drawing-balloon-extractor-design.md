# Sindri — Offline Drawing Ballooning → Excel Extractor

**Date:** 2026-06-16
**Status:** Design approved (pending spec review)

## 1. Goal

Given a technical engineering drawing PDF using the Intercable title-block template
(see `sample.pdf`), extract every **numbered balloon** and the dimension / tolerance /
specification it points to, let a human review and correct the result locally, and
produce an inspection-sheet `.xlsx` matching `excel_output.png`.

The application runs **fully offline** (no data ever leaves the machine), in **one
container by default**, started with a **single command** (`docker compose up`).

### Success criteria
- Running the pipeline on `sample.pdf` recovers all 22 numbered balloons + 4 note
  callouts (101–104).
- Balloons 1–8 recover the known values from `excel_output.png`
  (Distance/Diameter/Radius/Flatness with correct nominal + tolerances).
- The produced `.xlsx` reproduces the column layout and bilingual headers of
  `excel_output.png`.
- A reviewer can correct any cell in the browser before downloading.
- Default run requires no GPU and no internet.

## 2. Input / Output

**Input:** single-page PDF, Intercable template. Confirmed properties of the sample:
- Balloon **numbers** are embedded vector *text* with coordinates (reliable anchors).
- Leader lines and balloon circles are **vector paths** (traceable via PyMuPDF
  `get_drawings()`).
- Dimension *values* are vector line-art, **not** in the text layer → require OCR.
- Notation is European: comma decimal separator (`1,2`), `Ø` for diameter, `R` for
  radius, GD&T flatness symbol, stacked tolerances (`+0,1` / `−0,1`).

**Output:** `.xlsx` with bordered table, rows sorted by `Pos`, bilingual headers:

| Pos. / Pos. | Merkmal / Characteristic | Nennmaß / Nominal value | O-TOL / Upper-tol | U-TOL / Lower-tol |

Every numbered balloon becomes a row. Non-dimensional balloons (material, surface,
note callouts) fill Characteristic + a text value, leaving tolerance columns blank.

## 3. Architecture — one container

```
┌─────────────────────────────────────────────────┐
│  Docker image (python:3.12-slim + tesseract-ocr)  │
│                                                    │
│   FastAPI app (uvicorn) :8000                      │
│   ├─ static frontend (HTML/JS, served by the app)  │
│   └─ extraction pipeline (Python package)          │
│        PyMuPDF · OCR backend · OpenCV · openpyxl   │
└─────────────────────────────────────────────────┘
            ↑ browser at http://localhost:8000 ↑
```

- No database, no second service in the default profile. Per-session state lives in a
  temp working directory keyed by an upload/session id.
- Stack: **Python 3.12 + FastAPI + uvicorn**. Frontend is plain HTML/JS served by the
  app (no separate frontend build/container).

## 4. Extraction pipeline

Each stage is an independently testable unit with a clear input/output.

> **Revised during implementation.** The original design associated balloons to
> dimensions by tracing vector leader-line segments. In practice the balloons in
> this template are circle+number+**arrowhead** glyphs, so the nearest vector
> segments are the balloon's own geometry and the traced region landed back on
> the balloon. The shipped approach instead uses the balloons' distinct **blue
> colour** to find each arrowhead's pointing direction and crops the value
> offset in that direction. See §4a. (The superseded `vectors`/`tracer` modules
> were removed.)

1. **Render** — PyMuPDF rasterizes the page at ~300 DPI → PNG (for OCR + display).
   Persist the page→image scale factor for coordinate mapping.
2. **Anchors** — PyMuPDF `get_text("words")` extracts balloon numbers as text spans
   with bounding boxes. These are the canonical balloon IDs (no OCR error on the
   number itself) — verified to recover balloons 1–22 exactly.
3. **Balloon arrow direction (association)** — see §4a.
4. **OCR (pluggable backend)** — read the located value region. For vertical
   (diameter) dimensions the crop is OCR'd at both 90° rotations and the better
   parse is kept. Returns `text` + a confidence score. See §5.
5. **Parse & classify** — rules convert the read text into structured fields:
   - Characteristic: `Ø`→Diameter, `R`→Radius, flatness glyph→Flatness, plain
     number→Distance, alphanumeric spec→Material/Note.
   - Nominal, Upper-tol, Lower-tol — handling stacked `+a/−b`, symmetric `±a`,
     single-sided, and exact `0` forms. Comma decimals preserved (European).
6. **Confidence** — the OCR/VLM confidence becomes a per-row flag for the review UI.

### 4a. Association via blue-arrow direction

1. **Blue segmentation** — threshold the render for the balloons' blue colour.
2. **Component isolation** — dilate (~6 px) so each balloon's circle, number and
   arrowhead merge into one connected component without bridging neighbours;
   pick the component under the balloon's anchor.
3. **Direction** — the arrow tip is the component pixel farthest from the
   component centroid; its bearing gives a cardinal direction (and whether the
   dimension text runs horizontally or vertically).
4. **Crop band** — a rectangle beyond the arrow tip in that direction, sized to
   hold a dimension + tolerances. Fallback: a band to the right of the balloon
   if no blue component is found.

This stage is deterministic (no ML); only the *reading* of the located crop uses
OCR/VLM.

### Data model

```
Characteristic:
  pos: int                # balloon number (canonical id)
  char_type: str          # Distance | Diameter | Radius | Flatness | Material | Note
  nominal: str
  upper_tol: str
  lower_tol: str
  raw_text: str           # exact OCR output, for audit
  confidence: float       # 0..1
  balloon_xy: (x, y)      # image-space anchor
  target_region: bbox     # traced dimension region
```

## 5. OCR backend (pluggable) + local VLM analysis

OCR is a single interface (`read_region(image_crop) -> (text, confidence)`) with two
implementations, selected by environment variable (`OCR_BACKEND=tesseract|vlm`).

### `tesseract` (default, no GPU)
- CPU-only Tesseract with German + English language data, character allowlist tuned
  for technical notation, plus crop preprocessing (grayscale, upscale, Otsu).
- Guarantees the one-container, no-GPU, no-internet baseline always runs.
- **Reading fidelity is low** on this content — stacked tolerances, the `Ø` symbol
  and rotated diameters are largely unreadable. Useful mainly as a fallback and for
  balloon/region detection; values then need manual entry in the review UI.

### `vlm` (recommended for accuracy, GPU)
- A **local** vision-LLM (Qwen2.5-VL-7B-Instruct by default; 3B via `VLM_MODEL_ID`)
  runs on-machine — still fully offline after a one-time weight download.
- Used **only** for *constrained per-region reads*: given the small crop the
  geometry stage already located, it transcribes the dimension text on one line.
  It is **never** asked to reason over the whole drawing or perform association.
- **Validated result:** with correct crops it reads most dimensions accurately
  (e.g. balloons 2/4/7 exact). Residual errors are crop-precision (neighbour bleed
  on stacked balloons, GD&T frames) — see §11.
- Falls back to `tesseract` if no CUDA GPU is detected; the active backend is logged
  and exposed at `GET /api/health`.

### Why this split
- **Association** is deterministic geometry (blue-arrow direction) — reproducible and
  not subject to model hallucination.
- **Reading values** is where Tesseract fails and the VLM excels (`Ø`, GD&T glyphs,
  stacked/rotated tolerances).
- **Hallucination** risk is bounded by constraining the VLM to small isolated crops
  plus the human review UI.
- **Cost:** needs a host GPU (NVIDIA CDI / Container Toolkit), multi-GB weights and a
  torch/CUDA image — hence opt-in, not the default.

## 6. Review UI

- **Left:** rendered drawing with balloon markers at known coordinates.
- **Right:** editable table with the Excel columns. Low-confidence cells are flagged
  (amber). Rows that failed extraction appear empty for manual entry.
- **Download .xlsx** triggers the backend to build the file with openpyxl.
- The OCR backend is loaded once at startup and reused (the VLM model is multi-GB).

## 7. Excel output

`openpyxl` writes a bordered table reproducing `excel_output.png`: bilingual two-line
headers, rows sorted by `Pos`, blank tolerance columns for non-dimensional rows.

## 8. Docker

- **`Dockerfile`:** `python:3.12-slim`; `apt-get install tesseract-ocr tesseract-ocr-deu`
  and PyMuPDF/OpenCV runtime deps; `pip install` requirements; copy app;
  `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
- **`docker-compose.yml`:** builds the image, maps `8000:8000`, optional `./data`
  bind-mount for convenient input/output.
- **GPU image:** `Dockerfile.gpu` (PyTorch/CUDA base + transformers) sets
  `OCR_BACKEND=vlm` and caches weights in `/models`.
- **Single command (default, CPU):** `docker compose up` → `http://localhost:8000`.
- **GPU launch:** `./run-gpu.sh` injects the GPU via **NVIDIA CDI**
  (`--device nvidia.com/gpu=all`) under podman or docker. Compose's
  `deploy.resources.devices` reservation is **not** used because podman-compose
  ignores it (the container would silently see no GPU).

## 9. Testing

- `sample.pdf` is the golden fixture (balloons 1–22; balloons 1–8 values known).
- **Unit tests** on the parser (string → fields): stacked `+/−`, symmetric `±`,
  single-sided, exact `0`, `Ø`, `R`, flatness, material/text.
- **Unit tests** on the blue-arrow geometry with a synthetic balloon image.
- **Integration test:** run the full pipeline on `sample.pdf`; assert all 22 balloons
  are returned and (with an OCR binary) diameter classification holds.
- OCR backend selection falls back to Tesseract without a GPU, so tests run anywhere.

## 10. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| OCR misreads small/rotated text | VLM backend (recommended); review UI + confidence flags |
| Crop catches a neighbouring dimension | Human correction in UI (see §11 status) |
| Tolerance notation variants | Explicit parser rules + tests per form |
| VLM hallucinated values | Constrained per-region reads only; never page-level reasoning; human review |
| GPU/driver absence | VLM is opt-in; auto-fallback to Tesseract |

## 11. Shipped status & known limitations (2026-06-16)

Working end-to-end on `sample.pdf`, validated on an H100 host via the VLM backend:
- All 22 leader balloons detected with correct positions; values read accurately by
  the VLM when the crop is clean (balloons 2/4/7 exact, several others nominal-correct).
- The CPU/Tesseract default runs everywhere but reads this content poorly — treat it as
  detection + manual entry rather than full extraction.

Known limitations (deferred — review UI covers them):
- **Crop precision:** stacked balloons (1/2) and GD&T frames can bleed into a
  neighbouring dimension; a tighter band + sharper VLM prompt is the next improvement.
- **Notes 101–104:** not yet extracted into rows (only the 22 leader balloons appear).
- A few tolerance digits misread (e.g. `0,1`→`0,0`).

## 12. Out of scope (YAGNI)

- Multi-page PDFs and non-Intercable templates (template is fixed per requirements).
- Cloud/online anything (hard offline requirement).
- Persistent storage / user accounts / multi-user concurrency.
- Editing the drawing itself or re-ballooning.
