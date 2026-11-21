# Sindri — auto-ballooning for engineering drawings

Sindri finds the inspection characteristics on a PDF engineering drawing with a local
vision-language model (VLM), reads their values (nominal, tolerances, characteristic type),
numbers and places balloons, and lets a person review the result in a web app. Finishing a
review exports the client's Excel inspection sheet and a ballooned PDF. Fully offline: the
model runs on your own GPU.

Extraction is tuned for **precision over recall**: every value delivered unflagged should be
true, so uncertain rows are flagged for review, or held back as unexported suggestions.

**Start with [`ARCHITECTURE.md`](ARCHITECTURE.md)** for the system design and why it is shaped
this way. The decision records are in [`docs/adr/`](docs/adr/README.md), the plan in
[`ROADMAP.md`](ROADMAP.md), and working notes for agents in [`CLAUDE.md`](CLAUDE.md).

## How it works

1. **Import** drawings (files, a folder or a ZIP) into a draft batch; large uploads are
   chunked and resumable. Out-of-scope drawings (multi-page, oversized) and duplicates are
   flagged in staging.
2. **Start** the batch. One background worker extracts one drawing at a time (10–20 minutes
   each on an H100); no browser needs to stay open.
3. **Review** each drawing from the work list: move, add, delete and correct balloons and
   values. Every operation is journaled on the server; the review can be resumed exactly.
4. **Finish** seals a revision. Export a drawing, or a selection as one ZIP with a manifest.

Scope: single-sheet drawings that render at full resolution. About three quarters of the
client's corpus is in scope (`docs/adr/0011-single-sheet-supported-size-scope-policy.md`).

## Run

Set `SINDRI_DATA_DIR` for local development: the default `/data` is usually not writable
outside the container, and the app fails loudly on first use. The review journal is
mandatory, so an unwritable review directory fails the boot.

### CPU image (review host, **no extraction**)

    docker compose up

Open http://localhost:8000. This image uses Tesseract, which has no detector, so **it cannot
extract drawings**: extraction fails with "auto-ballooning requires the VLM backend". It is
the right image for reviewing stored proposals (the client review campaign), for CI, and for
manual-add reads (`/api/read_region`). See
`docs/adr/0012-vlm-required-for-extraction-tesseract-as-loud-fallback.md`.

### GPU image (extraction)

Needs an NVIDIA GPU and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
The VLM (Qwen2.5-VL) runs entirely on the machine.

**podman (NVIDIA CDI), recommended on HPC hosts:**

    ./run-gpu.sh                       # builds Dockerfile.gpu, runs with --device nvidia.com/gpu=all
    # pin the full (non-MIG) GPU:  GPU=nvidia.com/gpu=1 ./run-gpu.sh
    # use docker instead:          ENGINE=docker ./run-gpu.sh

**docker with the nvidia runtime:**

    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build

> compose's `deploy.resources.devices` GPU reservation is Docker-Swarm syntax that
> **podman-compose ignores**; under podman use `run-gpu.sh`, or the container sees no GPU.

Confirm the GPU is in use:

    curl localhost:8000/api/health        # want "cuda": true and "ocr_backend_active": "VLMBackend"

- `docker-compose.gpu.yml` sets `OCR_BACKEND=vlm` and `VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ`,
  which needs a large, free GPU. For less VRAM use `Qwen/Qwen2.5-VL-3B-Instruct`.
- **The first run downloads the model weights from Hugging Face** (tens of GB for the 72B),
  cached in the `hf_models` volume. After that, everything is offline. To pre-seed an
  air-gapped host, populate the volume once on a networked machine.
- **A GPU that is missing or occupied does not fail quietly.** The VLM load is retried,
  then the app falls back to Tesseract and records why: `/api/health` carries the reason
  and extraction errors quote it. Check which GPU is free before starting a run.

### Important settings

| Variable | Meaning |
|---|---|
| `OCR_BACKEND` | `tesseract` (default), `vlm`, `vllm` or `hybrid` |
| `VLM_MODEL_ID` | Hugging Face model id for the VLM backends |
| `SINDRI_DATA_DIR` | Index, drawings, uploads and exports (default `/data`) |
| `SINDRI_REVIEW_DIR` | Review records (default `<data>/reviews`); client data |
| `SINDRI_ACCESS_TOKEN` | If set, every `/api/*` route needs it; unset means open |
| `SINDRI_RETENTION_DAYS` | Days exported drawings' files are kept (default 30, `0` = off) |
| `SINDRI_MIN_FREE_MB` | Free-disk reserve; uploads answer 507 below it (default 2048) |
| `SINDRI_CAMPAIGN_ONLY` | `1` serves only the client review campaign (route allowlist, campaign ids only) |
| `SINDRI_READ_REGION_PER_MIN` | Campaign instances: `read_region` calls per session per minute (default 30) |
| `SINDRI_REVIEW_CONSENT` | `1` records reviewer consent in each review header (compose sets `0`) |

Run **exactly one** uvicorn process: the extraction worker, import recovery and housekeeping
assume it, and the worker takes a lock file to enforce it
(`docs/adr/0001-single-process-worker-with-sqlite-index.md`).

## Client review campaign

A campaign loads stored proposals into a hosted, campaign-only instance, has the client
review and approve them, and pulls the records back for grading. Operator tooling:
`campaign_ops.sh` (runs the same mode locally now, or on a VPS). Runbook:
[`docs/deploy/vps.md`](docs/deploy/vps.md). The drawings and review records are client data.

## Measurement and client data

`app/eval/` scores the pipeline against client gold in expected reviewer effort
(`python -m app.eval.runner …`). **The client corpus is under NDA and lives outside this
repo**; hooks block agents and commits from touching it. Read
[`docs/eval/DATA-HANDLING.md`](docs/eval/DATA-HANDLING.md) and `CLAUDE.md` §1 before
touching evaluation, and run `./install-hooks.sh` on a fresh clone. Committed digests in
`docs/eval/` are counts-only.

## Tests

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    pytest -q

- `tesseract` (the binary) must be installed, or several tests fail as
  `TesseractNotFoundError`.
- Node tests for the UI run inside pytest and skip without `node`; real-browser tests in
  `tests/browser/` skip without Playwright and a Chromium build.
- Two GPU tests need `RUN_GPU_TESTS=1` on a GPU host.
