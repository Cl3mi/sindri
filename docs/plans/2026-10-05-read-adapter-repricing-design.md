# Re-pricing `read-lora-v1` under the shipped policy — before building any serving path

Written 2026-10-05. Predictions are registered separately, BEFORE any score
runs: `2026-10-05-read-adapter-repricing-prediction.md`. Control: `r4-control`
(dev 118.73 scoped, auto-accept precision 0.8046 / rate 0.2251).

## 0. Why this, and not the merge route as staged

The merge-and-requantise route (`loramerged` / `mergedcontrol`, CLAUDE.md §3)
was the obvious next step for `read-lora-v1`. Two facts, found reading its
history, make running it as staged premature:

1. **A merged checkpoint cannot keep the read/detect scoping.** Folding the
   adapter into the weights means `detect_regions` runs through it too — the
   shape of the void arm `r3-lora72bnf4` (isolated misses 75 → 43, false
   detections 607 → 931, +10.00), not the scoped `r3-loraread` win (−4.40).
   `VLMBackend._base_weights()` has no base to fall back to inside a merged
   checkpoint. Scoping would survive only with base AWQ detecting and the merged
   checkpoint reading, on two cards (`VLM_DETECT_MODEL_ID`, the hybrid
   machinery). Neither the `loramerged` stage nor the deployment-routes doc
   records this.
2. **The adapter's remaining ceiling is small.** Its −4.40 was almost entirely
   escaped → flagged conversions (23 rows). `r4-control` has **17 escaped
   errors left on dev, 85 cost units, 5.67 per document**, and the shipped flag
   rules already reach many of the rows the adapter used to rescue. It was also
   trained on pad-6 crops; pad 24 ships.

Serving is additionally blocked: both AWQ merges are built, but the pinned
transformers-4.49 image cannot load llm-compressor's output (three failures
deep), and vLLM, which can, cost +8.65 by itself.

So the work worth doing first is the CPU question: **under today's policy, is
the adapter still worth more than the cheapest way to serve it, and would an
unscoped (merged) checkpoint even break even?**

## 1. Phase 0 — CPU pricing (DERIVED)

Six dev dump sets, each scored under the scope policy and today's post-read
code:

    python3 -m app.eval.runner score --run <run> --reapply-policy \
        --max-pages 1 --dpi 300 --exclude-clamped --pdfs <originals> ...

for `r3-awqcontrol`, `r3-nf4control`, `r3-loraread`, `r3-vllmcontrol`,
`r3-vllmlora`, `r3-lora72bnf4`, then `runner compare` on these pairs:

| quantity | pair | what it is |
|---|---|---|
| **G_nf4** | loraread − nf4control | adapter gain, NF4/transformers |
| **G_vllm** | vllmlora − vllmcontrol | adapter gain, AWQ/vLLM |
| **O_nf4** | nf4control − awqcontrol | NF4 serving overhead |
| **O_vllm** | vllmcontrol − awqcontrol | vLLM serving overhead |
| **U** | lora72bnf4 − nf4control | unscoped (merged-equivalent) shape |

Digests go to `docs/eval/reapply-<run>-scoped-summary.json` (counts only;
the digest-key namespacing in CLAUDE.md §5 applies). Commands are run bare,
one per call, per CLAUDE.md §1.

**Two stacks, one adapter.** G_nf4 and G_vllm are the same adapter on two
independent stacks: 0.25 apart on the full split, 0.93 apart scoped, under the
old policy. Their agreement is a registered prediction, not an exactness gate.
The exactness of `--reapply-policy` itself is already established: `r4-control`
reproduced it to the decimal.

**What Phase 0 cannot price.** Every one of these runs is pad 6. The adapter at
pad 24 has never run. Pad 24 and the adapter both convert escaped tolerance
rows, so the overlap most plausibly makes G an **upper bound** on the pad-24
gain; registered as such, with the mechanism, so a native run that beats it is
a surprise to explain, not a confirmation.

## 2. Decision rule (binding)

**Build a scoped serving path** (route A loader in the 4.49 image, or two-card
base-detect / merged-read) **iff BOTH G_nf4 and G_vllm satisfy, on scoped dev:**

* mean review cost delta ≤ **−1.50** per document;
* better under **6 of 6** `WEIGHT_GRID` weightings;
* auto-accept **precision does not fall**;
* auto-accept **rate rises strictly** (arm > control). This is stricter than
  `experiment.verdict`'s "falls ≤ 0.02" on purpose: the product goal is
  automation, and an adapter that buys cost without automating more rows is not
  worth a serving stack.

**Otherwise** close the read adapter's deployment as a measured dead end in
CLAUDE.md §3, with the numbers, and do not run `mergedcontrol` / `loramerged`.

**U never authorises the merge route as staged.** It is reported for one
decision only: if U's false-detection increase is substantially absorbed by
`contained_duplicate` (the only policy rule that can touch it), a
DETECT-trained adapter becomes worth its own brainstorm. Otherwise that
direction inherits this file's evidence against it.

**Damage counter:** `escaped_error`. It responded in both old pairs
(81 → 69, 75 → 64), so it can falsify (CLAUDE.md §4).

## 3. Split protocol — what a pass would and would not license

The adapter was selected on train (epoch chosen on a by-document holdout inside
train). Phase 0 is dev, DERIVED. A pass licenses **building** the serving path
only. **Keeping** the adapter additionally needs:

1. a native dev run of the built path against its own zero-adapter control on
   the same stack, reproducing the gate above; then
2. a native test run (arm and control), passing the same rule on test.

No adapter dumps exist on test, so step 2 is a GPU step and is never claimed
from derived numbers.

## 4. Deliverables

* Prediction file, committed before any score command runs.
* Six scoped reapplied digests and five comparisons in `docs/eval/`.
* A result doc `2026-10-05-read-adapter-repricing-result.md`: the five
  quantities, the rule's verdict, prediction-vs-measured per line.
* CLAUDE.md §2/§3 updated with the verdict; the `loramerged` stage's
  `stage_why` amended to record the scoping loss whatever the verdict.

No pipeline code changes. If a CPU helper is needed to tabulate the comparisons,
it goes in the scratchpad and reads only `docs/eval/`.
