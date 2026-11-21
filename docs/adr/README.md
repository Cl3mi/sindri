# Architecture Decision Records

Short records of decisions that are expensive to reverse. Start from `ARCHITECTURE.md`;
each ADR links back to the design, plan and result documents in `docs/plans/` that hold
the evidence.

These ADRs were written retroactively (2026-11-21) from those documents. Where a source
did not record the alternatives considered, the ADR says so and marks reconstructed
reasoning as such.

Figures were re-checked on 2026-11-21 against the committed digests in `docs/eval/`
(cost, taxonomy, precision, crop-height and per-kind accuracy, test split, hybrid, read
adapter, drop stages). Timing figures, the corpus scope counts, the verifier's 191-of-196
and the render-budget result are quoted from `CLAUDE.md` / plan docs and have no committed
digest to confirm them.

| # | Decision | Status |
|---|---|---|
| [0001](0001-single-process-worker-with-sqlite-index.md) | One background worker thread, one process, SQLite index | Accepted |
| [0002](0002-journal-and-sealed-revisions-as-source-of-truth.md) | Event-sourced review journal, sealed immutable revisions | Accepted |
| [0003](0003-flag-and-drop-by-gold-free-policy-stages.md) | Gold-free flag/drop rules as stages, drops to a suggestion tray | Accepted |
| [0004](0004-replay-implemented-twice-in-js-and-python.md) | Review state implemented in JS and Python, fuzz-tested for parity | Accepted |
| [0005](0005-campaign-only-positive-allowlist.md) | Campaign instance: positive route allowlist plus object scoping | Accepted |
| [0006](0006-pipeline-and-eval-kept-separate.md) | Pipeline and eval separate; product never imports eval | Accepted |
| [0007](0007-counts-only-digests-and-hardened-outputs.md) | Counts-only digests; CLI refuses unsafe output paths | Accepted |
| [0008](0008-two-stage-vlm-detect-then-read-with-crop-context.md) | VLM detects, then reads each padded crop (pad 24) | Accepted |
| [0009](0009-suggestion-tray-for-dropped-rows.md) | Stage-2+ drops kept as unexported suggestions | Accepted |
| [0010](0010-chunked-resumable-upload-protocol.md) | Chunked, resumable upload into draft batches | Accepted |
| [0011](0011-single-sheet-supported-size-scope-policy.md) | Scope: single-sheet, full-resolution drawings | Accepted |
| [0012](0012-vlm-required-for-extraction-tesseract-as-loud-fallback.md) | VLM required for extraction; Tesseract a loud fallback | Accepted |
