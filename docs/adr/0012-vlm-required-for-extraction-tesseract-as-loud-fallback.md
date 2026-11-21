# 0012. Extraction requires the VLM; Tesseract is the CPU fallback, and any fallback is recorded and surfaced

Status: Accepted
Date: 2026-06-16 (backend interface); 2026-07-08 (loud fallback)

## Context

Sindri is "fully offline, one container" (README). The first implementation used Tesseract
(`app/pipeline/ocr/tesseract_backend.py`); an optional local vision-LLM backend followed
(`971cec7`), because Tesseract reads stacked GD&T tolerances, the Ø symbol and rotated
dimensions poorly. The VLM needs an NVIDIA GPU and large weights (the 72B AWQ shares a
card with other users on the GPU host), while the container, CI and the campaign VPS have
no GPU.

On 2026-07-08 a real failure exposed the cost of a quiet fallback: with `OCR_BACKEND=vlm`
and the card held by another job, `device_map=auto` spilled to CPU, AWQ refused, the app
silently degraded to Tesseract, and extraction then died with a bare "auto-ballooning
requires the VLM backend" (`adf64e8`).

## Decision

OCR sits behind one `OCRBackend` protocol; `get_backend()` picks by `OCR_BACKEND`
(`tesseract` default, `vlm`, `vllm`, `hybrid`). **Tesseract is a deliberate CPU fallback,
not an extraction path:** it has no `detect_regions`, so `extract()` refuses to run on it
and raises an error that carries the fallback reason. It still serves
`/api/read_region` (manual adds, where the reviewer corrects the value) and tests. Any
fallback is **never silent**: `get_backend()` retries the VLM load first
(`VLM_LOAD_RETRIES`, default 3, and `VLM_LOAD_RETRY_DELAY`, default 8 s) to ride out transient VRAM contention, records
`_fallback_reason`, logs it, exposes it on `/api/health` (`backend_status`), and folds it
into the extraction error.

## Alternatives considered

- **Silent degradation** — the original behaviour; produced the cryptic error above.
- **Hard-fail when the GPU is missing** — rejected: the same image must boot on CPU for
  CI, the campaign VPS (which loads stored proposals and needs OCR only for
  `read_region`) and a fresh clone.
- **Tesseract as a real detector** — measured away: the OCR-proposal arm found a proposal
  in the match gate of only 6 of 58 isolated misses against a registered GO of ≥ 12; the
  misses the VLM makes are misses tesseract makes too
  (`2026-09-25-policy-arms-result.md` §7). Reverted.
- **Smaller VLM for low VRAM** — supported by env (`VLM_MODEL_ID=…3B`), not the default.

## Consequences

Easier: one image family serves GPU production, CPU campaign hosting and CI; a GPU problem
reads as an actionable message; the backend is swappable for experiments (vLLM, hybrid).

Accepted costs:

- **The CPU path cannot extract.** `docker compose up` (CPU) is a review/campaign host, not
  an extraction host; the README's "CPU/Tesseract" run is therefore misleading for live
  extraction and is listed under tech debt in `ARCHITECTURE.md`.
- **Operational fragility sits at the load.** On the shared GPU host a 72B AWQ load into an
  occupied card falls back to Tesseract and then fails every document, so the free card
  must be checked and pinned before every run; MIG is no longer configured.
- A serving stack decision is entangled with the weights: PEFT cannot attach adapters to the
  AWQ base (`WQLinear_GEMM`), which closed one deployment route
  ([0008](0008-two-stage-vlm-detect-then-read-with-crop-context.md)).
- Resolved model confidence must be guarded for NaN at the model boundary: float16 AWQ can
  emit a NaN logits row that makes a dump write-only and slips past `conf < LOW_CONF` as a
  silent error.

## Lessons

- A fallback that does not announce itself converts a resource problem into a mystery; the
  fix cost a retry loop, a recorded reason and one health field.
- The campaign deployment ([0005](0005-campaign-only-positive-allowlist.md)) depends on this
  decision: it uses the unmodified CPU image because it never extracts.
