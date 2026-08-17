# Handoff: finishing the Rung-0 baseline run

**Date:** 2026-08-18
**Branch:** `worktree-eval-harness` (PR #2), HEAD `1107774`
**Status:** Harness complete and green (378 tests). Gold built. Split frozen.
The baseline prediction run reached **16 of 20 dev documents** and then crashed.
Three fixes stand between here and a baseline number.

This doc is self-contained — you should not need the originating conversation.

---

## 1. Read this first: the data rules are enforced, not advisory

The client's drawings and inspection sheets are under NDA. A guard hook
(`~/.claude/hooks/sindri-guard.py`, registered in `~/.claude/settings.json`)
blocks every agent from reading them, on this machine, in every session.

* Protected root: `/home/clemi/sindri-client-data` (listed in
  `~/.claude/sindri-protected-paths`).
* **Any** command mentioning it is denied unless it is one of the reviewed
  aggregate-only tools, and even then **only as a single unpiped, unchained
  command**. `cmd | head`, `cmd && cmd`, and multi-line shell blocks are denied.
* Blocked everywhere by file type: `*.pdf`, spreadsheets, `*.gold.json`,
  `*.pred.json`, `*.report.json`, `doc_id_map*`.
* Sanctioned: `python -m app.eval.runner <probe|headers|ingest|split|predict|
  score|compare|summary|variants>`, plus `setup_client_data.py` and
  `sync_client_data.sh`.
* Verify the guard any time with `bash ~/.claude/hooks/test-sindri-guard.sh`
  (32 cases). Re-run it after ANY edit to the guard.
* Document ids are salted hashes in all output. Never use `--show-ids` in an
  agent session. Never read a `*.report.json` — use `runner summary`.

Full rationale: `docs/eval/DATA-HANDLING.md`.

**A guard edit to permit data egress was previously denied by the permission
classifier, correctly.** Do not try to widen the guard to move client data. If a
transfer is needed, hand the user the command to run.

## 2. What already exists

* `app/eval/` — the whole Rung-0 harness: gold ingestion, matching, review-cost
  scoring, splits, bootstrap comparison, CLI. 378 tests pass.
* **Gold** at `<root>/gold`: 3,594 rows from 100 sheets. **2,489 dimensional**,
  1,086 verbal requirements, 19 blank. Rows carry `kind`; the headline metric
  covers dimensions only (`MatchParams.score_kinds`). 2,288 of 2,489 dimensions
  (92%) carry a position; the rest are matched on value similarity.
* **Split frozen**: train 60 / dev 20 / test 20, seed 13,
  `splits_hash = 6d174d5e4f1b9228`, all 18 structurally-derived variants in test.
  Lives at `<root>/meta/splits.json` (part numbers — never commit it).
* **Weights** `docs/eval/weights.json` are provisional and block nothing:
  `compare` reports whether a verdict survives all six plausible weightings.
* Runbook: `docs/eval/BASELINE-RUNBOOK.md`.

## 3. The crash — analysis

Command: `./run_baseline_gpu.sh 4mehpc4_3 '~/sindri-eval-data' baseline`
(push and build succeeded; 72B AWQ loaded; 16/20 documents processed).

```
PIL.Image.DecompressionBombError: Image size (598394358 pixels)
exceeds limit of 178956970 pixels
  app/pipeline/extract.py:128  image = Image.open(render.png_path).convert("RGB")
```

**Cause.** `render_page` renders at a fixed dpi with no pixel budget. 598.4 M
pixels at 300 dpi implies a page of roughly 6,970 × 4,944 pt — about
2.5 m × 1.7 m, i.e. a very large-format drawing (or an oversized MediaBox).
PIL warns above 89.5 MP and hard-errors above 178.9 MP. Two earlier documents
survived with warnings at 139.5 MP and 163.6 MP, so this corpus sits right on
the boundary and one document is far past it.

**This is a product bug, not just an eval bug.** A user uploading such a drawing
to Sindri hits the same crash in `extract()`. The harness did its job by finding
it. Fix it in the pipeline, not by special-casing the eval.

Two further problems visible in the same log:

* `[sindri.title_block] cell read failed: ValueError('height:22 or width:190
  must be larger than factor:28')` — Qwen2.5-VL requires both crop dimensions
  ≥ 28 px. Small title-block cells fail and are silently lost. `extract._prep_crop`
  already upscales short crops for characteristics (`_MIN_CROP_H=40`), but the
  title-block path bypasses it.
* `do_sample=False` with `top_p`/`top_k` set — harmless generation-config noise.

## 4. What to fix, in order

### A. Pixel budget in `render_page` (blocking)

Clamp the effective dpi so `width * height` stays under a documented budget
(~80–120 MP keeps clear of PIL's warning and error thresholds), and set
`Image.MAX_IMAGE_PIXELS` deliberately rather than relying on the default.

**Watch the scale, or you will silently corrupt every coordinate.**
`RenderResult.scale` is what the eval uses to convert pixels to PDF points. If
the dpi is clamped, the actual scale differs from the requested one. Today
`runner.predict_one` computes `scale=dpi/72.0` from the **requested** dpi and
never looks at the render result — so after clamping, that dump's geometry would
be wrong while looking perfectly healthy. Thread the real scale out of
`extract()` (or have `predict_one` obtain it from the render) and store that.
There is a test-shaped hole here: assert that a clamped render produces a dump
whose `scale` round-trips a known box back to its true PDF points.

### B. Resilient, resumable `predict` (blocking)

`_cmd_predict` has no per-document error handling: one bad document aborts the
whole run. Wrap each document, record the failure (hashed id + exception class),
continue, and report a failure count at the end. Also skip documents whose
`.pred.json` already exists so a run resumes instead of restarting — 15 dumps
are already on the GPU host and should not be recomputed.

### C. Title-block cell upscaling (quality, not blocking)

Upscale cells below the 28 px patch factor before reading, mirroring
`_prep_crop`. Without it, title-block fields are quietly dropped on every
document.

## 5. Resuming the run

The GPU host already holds everything needed — nothing needs re-transferring:

* `4mehpc4_3:~/sindri-eval-data` — 99 clean drawings, split file, **15 dumps**
* image `sindri-gpu` built; 72B AWQ weights cached in volume `sindri-models`
* both GPUs were free (80 GB each)

After A and B are merged, the user re-runs (an agent cannot: the script names
the protected root):

```
./run_baseline_gpu.sh 4mehpc4_3 '~/sindri-eval-data' baseline
```

With resume in place it will skip the 15 completed documents. Steps 4–5 pull the
dumps back and score locally, because the gold and the sheets never leave this
machine. The shareable artifact is `docs/eval/baseline-summary.json`.

## 6. Then, and only then

Read the taxonomy, not the total — it routes all later work:
`missed` dominant → Rung 1 detection knobs; `cause:misparse` → parser hardening;
`cause:misread` → Rung 2 prompts, then Rung 3 LoRA; `flagged_correct` large →
review-flag calibration. A high `missed` on the **variant** documents means a
template-generalization problem, which is why they were forced into test.

## 7. Key files

| Path | What |
|---|---|
| `app/pipeline/render.py` | fix A lives here |
| `app/eval/runner.py` | `_cmd_predict` (fix B), `predict_one` (scale bug) |
| `app/pipeline/title_block.py` | fix C |
| `app/eval/models.py` | versioned schemas; `MatchParams` is the comparability guard |
| `docs/eval/DATA-HANDLING.md` | the NDA rules, in full |
| `docs/eval/BASELINE-RUNBOOK.md` | the run, and what its output means |
| `run_baseline_gpu.sh`, `sync_client_data.sh` | push → predict → pull → score |

## 8. Do not

* Do not read client PDFs, sheets, gold, dumps or reports — use `runner summary`.
* Do not commit `splits.json`, `variants.txt`, `doc_id_map.json` or any report
  with `doc_scores` populated; the pre-commit hook blocks most of it.
* Do not regenerate the split — `6d174d5e4f1b9228` is frozen and every report
  embeds it.
* Do not widen the guard to move data.
* Do not touch the test split until a final config comparison.
