# 0008. A VLM localises callouts, then reads each one from its own padded crop

Status: Accepted
Date: 2026-06-16 (VLM backend); 2026-09-16 (crop pad 24 shipped)

## Context

Sindri must find every inspection characteristic on a large engineering drawing and read
its value (nominal, tolerances, characteristic type). Tesseract reads stacked GD&T
tolerances, the Ø symbol and rotated dimensions poorly (README), and a drawing is far
larger than a VLM's useful field of view. Detection and transcription are also different
tasks with different failure modes.

Measurement shaped the design after the fact. The eval campaign found:

- **Box framing dominates read accuracy, about 5x over the reader's weights.** Holding the
  reader and degrading the boxes cost field accuracy −0.206; holding the boxes and
  swapping a 32B reader for a 72B moved it +0.013 (`2026-09-11-hybrid-arm-result.md`).
- A tight crop starves the reader of context. 126 of 223 matched rows were boxes ≥ 80 px
  tall and read at 0.397 against 0.591 for 40-80 px (`2026-09-16-crop-height-diagnostic.md`).

## Decision

The pipeline runs two VLM passes on one model (`app/pipeline/extract.py`,
`detect.py`): **detect** tiles the page (default tile 1024, overlap 0.15, env-overridable via `VLM_TILE` /
`VLM_TILE_OVERLAP`)
and returns regions; **read** crops each tightened box with a context pad and asks the
model to transcribe it, retrying a rotated candidate when the reads are ambiguous. The
crop pad (`_CROP_PAD`) is **24 px**, and the crop knobs (`SINDRI_CROP_PAD`,
`SINDRI_CROP_MIN_H`, `SINDRI_CROP_MAX_UPSCALE`) are *recorded* in `RunConfig.extra`
against a frozen baseline of 6/40/3.0.

## Alternatives considered

- **Hybrid: a 32B localises, a 72B transcribes** — measured and lost: +40.00 review cost,
  0 of 6 weightings better; recall of boxes rose (18 more gold rows) but fully-correct
  values fell by 41 and silent errors rose by 34. A stronger reader on bad boxes is more
  confident on crops it misreads, which removes the warning without fixing the value.
- **Pad 48** — read more accurately (field_acc 0.5426) but cost more (132.73 vs 131.87)
  because three rows moved into silent errors. The curve is peaked; "do not test 96".
- **Height-dependent pad** (48 for boxes ≥ 120 px) — the most precisely targeted arm,
  and it lost (132.53, 0 of 6): it fixed six *already-flagged* rows (worth nothing) and
  broke two unflagged ones.
- **Crop-resolution knobs** (`_MIN_CROP_H`, `_MAX_UPSCALE`) — refuted without a GPU run:
  only 9 of 223 matched rows sit below `_MIN_CROP_H`, and accuracy *falls* with crop
  height.
- **A trained read adapter** (`read-lora-v1`) — harmful under the shipped policy (see
  [0003](0003-flag-and-drop-by-gold-free-policy-stages.md)); also impossible to serve on the
  AWQ base because PEFT cannot inject into `WQLinear_GEMM`.
- **Larger render budget / tiled rendering** — 80 → 150 MP changed nothing (isolated misses
  251 → 252; figure quoted from `CLAUDE.md` §3. The committed `render150-*` digests
  predate the coordinate fix and show a different, unusable baseline, so they cannot
  confirm it and must not be quoted).

## Consequences

Easier: one model checkpoint serves both passes; detection can be priced separately from
reading; the crop constant was the campaign's first free win (131.87 vs 133.93, better
under 6 of 6 weightings, field_acc +0.049, detection bit-identical).

Accepted costs:

- **Detection is ~2/3 of per-document cost** (10-24 minutes on dev documents against a
  ~16 minute full-predict median), so a drawing takes 10-20 minutes on an H100; this is
  why extraction is a background queue ([0001](0001-single-process-worker-with-sqlite-index.md)).
- **Read quality is bounded by the boxes the detector produces**, which the reader cannot
  repair. Isolated misses move only with detector weights (adapter: 75 → 43), not knobs or
  prompts.
- 15 % of rows (non-dimension kinds) produce 41 % of silent errors; the pipeline cannot read
  them reliably and relies on flagging ([0003](0003-flag-and-drop-by-gold-free-policy-stages.md)).
- The recorded-knob discipline is load-bearing: comparing crop knobs to the *current*
  default would let `_reusable_dump` reuse pad-6 dumps across the change.

## Lessons

- **The input to the read holds the remaining quality, not the model doing the reading.**
- A registered decision rule for the dose ("field_acc up AND misplaced_matches ≤ 46 → take
  48") held on both conditions and picked the worse setting, because the counter never
  moved. Check that a damage counter responded to the previous dose before registering it.
- Fixing a read only pays when it converts an *escaped* error; a fix on an already-flagged
  row saves nothing.
- Related: [0006](0006-pipeline-and-eval-kept-separate.md),
  [0012](0012-vlm-required-for-extraction-tesseract-as-loud-fallback.md).
