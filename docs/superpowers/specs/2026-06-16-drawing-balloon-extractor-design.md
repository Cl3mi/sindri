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

1. **Render** — PyMuPDF rasterizes the page at ~300 DPI → PNG (for OCR + display).
   Persist the page→image affine transform for coordinate mapping.
2. **Anchors** — PyMuPDF `get_text("dict")` extracts balloon numbers as text spans
   with bounding boxes. These are the canonical balloon IDs (no OCR error on the
   number itself).
3. **Vectors** — `get_drawings()` extracts leader-line segments and balloon circles.
4. **Trace (association)** — for each balloon: find the leader line touching its
   circle, follow it to its endpoint, and define a **target region** around that
   endpoint. Fallback: directional nearest-text proximity when no clean leader exists.
   This stage is deterministic and is **not** delegated to any ML model.
5. **OCR (pluggable backend)** — read the target region (plus title-block regions for
   material/notes). Returns `raw_text` + a confidence score. See §5.
6. **Parse & classify** — rules convert `raw_text` into structured fields:
   - Characteristic: `Ø`→Diameter, `R`→Radius, flatness glyph→Flatness, plain
     number→Distance, alphanumeric spec→Material/Note.
   - Nominal, Upper-tol, Lower-tol — handling stacked `+a/−b`, symmetric `±a`,
     single-sided, and exact `0` forms. Comma decimals normalized consistently.
7. **Confidence** — combine OCR confidence and trace certainty into a per-row flag for
   the review UI.

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

### `tesseract` (default)
- CPU-only Tesseract with German + English language data, character set tuned for
  technical notation (digits, `Ø`, `R`, `,`, `±`, `°`).
- Guarantees the one-container, no-GPU, no-internet baseline always works.

### `vlm` (optional, GPU)
- A **local** vision-LLM (e.g. Qwen2.5-VL-7B-Instruct, or a 3B variant for low VRAM)
  runs on-machine — still fully offline.
- Used **only** for *constrained per-region reads*: it is given the small cropped
  image that the geometry stage already isolated and asked to return exactly the
  characters as JSON. It is **never** asked to reason over the whole drawing or to
  perform association.
- Falls back to `tesseract` if no GPU is detected.

### Rationale (analysis result)
- **Association** stays deterministic (exact balloon coords + vector leaders). A VLM
  cannot beat provably-correct geometry and is unreliable in crowded balloon clusters
  (17/18/19, 20/21/22) — so it is excluded from this stage.
- **Reading values** is where Tesseract is weakest (the `Ø` symbol, GD&T glyphs,
  stacked/rotated tolerance text). A modern VLM reads these markedly better.
- **Hallucination** is the key risk for metrology data (a wrong tolerance is worse than
  a blank). Constraining the VLM to small isolated crops plus the human review UI
  reduces this risk to near zero.
- **Costs:** requires host GPU + NVIDIA Container Toolkit, multi-GB weights, a
  torch/CUDA layer (large image), slower cold start — hence opt-in, not default.

## 6. Review UI

- **Left:** rendered drawing with clickable balloon markers at known coordinates;
  selecting a table row highlights its balloon and traced target region.
- **Right:** editable table with the Excel columns. Low-confidence cells are flagged
  (amber). Rows that failed extraction appear empty for manual entry.
- **Download .xlsx** triggers the backend to build the file with openpyxl.

## 7. Excel output

`openpyxl` writes a bordered table reproducing `excel_output.png`: bilingual two-line
headers, rows sorted by `Pos`, blank tolerance columns for non-dimensional rows.

## 8. Docker

- **`Dockerfile`:** `python:3.12-slim`; `apt-get install tesseract-ocr tesseract-ocr-deu`
  and PyMuPDF/OpenCV runtime deps; `pip install` requirements; copy app;
  `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
- **`docker-compose.yml`:** builds the image, maps `8000:8000`, optional `./data`
  bind-mount for convenient input/output.
- **GPU override (optional):** `docker-compose.gpu.yml` adds
  `deploy.resources.reservations.devices` for NVIDIA and sets `OCR_BACKEND=vlm`.
- **Single command (default):** `docker compose up` → open `http://localhost:8000`.

## 9. Testing

- `sample.pdf` is the golden fixture (22 balloons + 4 notes; balloons 1–8 known).
- **Unit tests** on the parser (raw string → fields): cover stacked `+/−`, symmetric
  `±`, single-sided, exact `0`, `Ø`, `R`, flatness, and material/text strings.
- **Unit tests** on the tracer with synthetic balloon+leader geometry.
- **Integration test:** run the full pipeline on `sample.pdf`; assert balloon count and
  that balloons 1–8 recover the known nominal/tolerance values.
- OCR backend selection is mockable so tests run without a GPU.

## 10. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| OCR misreads small/rotated text | Review UI + per-cell confidence flags; optional VLM backend |
| Leader-line ambiguity in dense regions | Proximity fallback + human correction in UI |
| Tolerance notation variants | Explicit parser rules + tests per form |
| VLM hallucinated values | Constrained per-region reads only; never page-level reasoning; human review |
| GPU/driver absence | VLM is opt-in; auto-fallback to Tesseract |

## 11. Out of scope (YAGNI)

- Multi-page PDFs and non-Intercable templates (template is fixed per requirements).
- Cloud/online anything (hard offline requirement).
- Persistent storage / user accounts / multi-user concurrency.
- Editing the drawing itself or re-ballooning.
