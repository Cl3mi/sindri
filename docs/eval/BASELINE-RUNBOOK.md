# Baseline runbook (Rung 0, Task 14)

Everything up to prediction is done and automated. This is the one step that
needs a GPU, and the only step a human has to launch.

## Frozen state — do not regenerate

| | |
|---|---|
| Corpus | 100 drawings + 100 sheets, 99 usable triples |
| Gold rows | 3,594 — 2,489 dimensional, 1,086 verbal requirements, 19 blank |
| Position coverage | 2,288 of 2,489 dimensions (92%) |
| Split | train 60 / dev 20 / test 20, seed 13 |
| **splits_hash** | **`6d174d5e4f1b9228`** |
| Variants in test | 18 of 18 (all) |

The split file lists part numbers, so it lives at `<root>/meta/splits.json`, not
in git. The hash above is the committed proof it is frozen: every report embeds
`splits_hash`, and `compare` refuses to compare runs whose splits differ.

`docs/eval/weights.json` holds **provisional** review-cost weights. They do not
block anything — `compare` reports whether a verdict survives every plausible
weighting, so the client's real numbers are needed only to *confirm* a
conclusion, never to reach one. Replace the file when they arrive; reports
embed whatever was used.

## Before running: the data has to reach the GPU box

Prediction renders and reads the drawings, so the client files must be present
wherever the model runs. Copying them to the GPU host moves confidential data
onto another machine — that is a decision for the data owner, not something
this harness should do silently. If you do copy them, mirror the protections
there: keep the corpus outside any git tree, and add the path to that machine's
`~/.claude/sindri-protected-paths` if an agent ever runs on it.

## Run

```bash
R=~/sindri-client-data

# 1. Predict on the CLEAN drawings — what production actually sees.
#    dev first; test stays untouched until a final comparison.
python -m app.eval.runner predict \
    --pdfs $R/corpus/originals --out $R/runs/baseline \
    --splits $R/meta/splits.json --split dev

# 2. Score against gold.
python -m app.eval.runner score \
    --run $R/runs/baseline --gold $R/gold \
    --splits $R/meta/splits.json --split dev \
    --weights docs/eval/weights.json \
    --name baseline-dev --out $R/reports/baseline-dev.report.json

# 3. Read it. NEVER read the report directly — it embeds gold values.
python -m app.eval.runner summary $R/reports/baseline-dev.report.json \
    --out docs/eval/baseline-summary.json
```

Run each as its own command: the client-data guard rejects piped or chained
invocations, and the summary is the only artifact safe to commit or paste.

## What the result means

The headline covers **dimensional characteristics only** — a verbal requirement
was never ballooned, so it cannot be a missed callout. `excluded_by_kind` on
every DocScore records how many rows that leaves out; nothing is hidden. To
score notes too, pass `score_kinds=("dimension","note")` — but then match them
against the pipeline's notes output, not its characteristics.

Rows whose balloon could not be located (8% of dimensions) are matched on value
similarity instead of geometry, so they are still scored, just without
positional disambiguation.

## Then read the taxonomy, not the total

The taxonomy histogram is the routing decision for everything after Rung 0:

| dominant term | what it means | next rung |
|---|---|---|
| `missed` | callouts never detected | Rung 1: detection recall knobs |
| `cause:misparse` | glyphs read, parsing lost them | Rung 1: parser hardening |
| `cause:misread` | perception failure | Rung 2 prompts/few-shot, then Rung 3 LoRA |
| `flagged_correct` large | over-flagging wastes review time | Rung 1: review-flag calibration |

A large `missed` count on the **variant** documents specifically means a
template-generalization problem rather than a general recall problem — that is
exactly why the atypical drawings were forced into the test split.
