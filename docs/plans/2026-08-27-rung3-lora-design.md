# Rung 3 — LoRA on the read stage: design

Written 2026-08-27, after Rung 2 closed at seven arms and seven losses. Read
`CLAUDE.md` first, then `docs/plans/2026-08-27-session-handoff.md`.

**Decision taken:** climb straight to LoRA (skipping Rung 2's untried few-shot
leg), and fine-tune the **7B** first, per the original ladder's own
recommendation in `2026-07-14-extraction-quality-optimization-handoff.md` §5.

---

## 1. What this builds, and what it deliberately does not

**Scope: the read stage only.** One task — a cropped callout image in, a
transcription out — which is `_PROMPT` and `VLMBackend.read_region`. The ladder
notes detection LoRA is "possible but data-thin"; it is out of scope, and so is
the notes/title/GD&T-frame reads (those rows sit outside `score_kinds` or have a
ceiling of ~31 matched rows).

**Not in scope:** few-shot exemplars (deferred by decision), any change to
`MatchParams`, `SCHEMA_VERSION`, or the frozen split `6d174d5e4f1b9228`, and any
retry of the levers in `CLAUDE.md` §3 or handoff §6.

## 2. This is three runs, not one

`report.compare_runs._check_comparable` guards the doc set, per-document gold
hashes, weights, `match_params` and `splits_hash`. It does **not** look at
`RunConfig`. So a 7B report will compare against the 72B baseline **silently**,
crediting the base-model swap to whatever we believe we are testing. Switching
base model is a larger change than any knob measured so far.

| run | isolates | model | status |
|---|---|---|---|
| `baseline-dev` | the frozen comparison point, 174.30 | 72B AWQ zero-shot | **have it** |
| `base7b` | the base-model change | 7B zero-shot | to run |
| `lora7b` | the fine-tune | 7B + adapter | to run |

* **LoRA's effect** = `lora7b` vs `base7b`.
* **The deployment question** — the ladder's actual question, "does a LoRA'd 7B
  beat a zero-shot 72B?" — = `lora7b` vs `baseline-dev`.

Neither is answerable without `base7b`. It is cheap: `_DEFAULT_MODEL` in
`app/pipeline/ocr/vlm_backend.py:7` is already `Qwen/Qwen2.5-VL-7B-Instruct`, and
`run_experiment_gpu.sh` propagates `VLM_MODEL_ID` into the container, so it needs
no new pipeline code. A 7B is also expected to be materially faster than the 72B
— to be measured, not assumed.

**Therefore the comparability hole is closed first** (§7, task 1): the base model
must appear in the arm table and a cross-model comparison must warn, so it is
never silent. It must *warn*, not refuse — comparing across base models is
precisely the experiment.

## 3. The training target: render gold, verify by round-trip

Gold gives *parsed* fields (`char_type`, `nominal`, `upper_tol`, `lower_tol`), not
the printed string. Targets must therefore be **rendered** from gold, and the
renderer is the inverse of `app/pipeline/parser.py:parse_value`.

That inverse is verifiable with **zero client data**: render a synthetic gold row,
parse it back, and require the scored fields to match. Verified empirically before
writing this spec — all nine shapes the corpus contains round-trip:

| shape | rendered | hint |
|---|---|---|
| plain distance | `20` | — |
| symmetric tolerance | `5,5 +0,1 -0,1` | — |
| symmetric via ± | `5,5 ±0,1` | — |
| diameter | `Ø20 +0,1 -0,1` | — |
| diameter one-sided | `Ø6,6 +0,2 0` | — |
| radius MAX | `R0,5 MAX` | — |
| flatness | `⏥ 0,05` | `gdt` |
| position | `⊕ Ø0,1 A` | `gdt` |
| theoretical | `20` | `theoretical` |

**The hint matters.** `parse_value(text, hint)` behaves differently per hint, and
at inference the hint comes from the detector kind (`extract._HINTS`). So the
renderer signature is `render_target(gold_row, hint) -> str`, and the round-trip
property is `parse_value(render_target(g, hint), hint) ≈ g` under
`normalize.values_equal` / `char_type_equal`.

**One tolerance form, chosen deliberately: the explicit two-sided `+0,1 -0,1`.**
`±0,1` is listed above because it round-trips, but only for a *symmetric*
tolerance — the parser's `±` branch derives the lower bound as the negated upper,
so it cannot express `+0,2 0` or a one-sided tolerance at all. Rendering one form
everywhere keeps the renderer a total function over the shapes gold contains and
keeps the target distribution consistent, which is what the model is learning.

**A row that cannot be rendered must raise, not emit an approximation.** A silent
approximation would train the model toward a value gold does not hold, and the
count of unrenderable rows is itself a finding.

**The design commitment this makes explicit.** Training on a canonical rendering
teaches the model a *normalisation*, not a literal transcription — a drawing
printing `Ø20 ±0,1` and one printing `Ø20 +0,1 -0,1` get the same target. This is
defensible, because the read stage's job is to produce text `parse_value` maps to
the right fields, and it is exactly what the metric rewards. It is also why the
round-trip test is the load-bearing one: it proves the target is *parseable to
gold*, which is the only property that matters downstream.

## 4. Where crops come from

Training pairs are (crop, target). Crops come from detection boxes, and **the
train split's 60 documents have never been predicted** — only dev's 20 have.

At dev's ~26 min/document that is ~26 h of GPU before training can start. A
detection-only pass would skip the read stage, but the saving is **not
predictable**: detection issues ~12 tile generates at `max_new_tokens=1024`, while
reads issue ~41–80 at 40 tokens, and per-call vision-encoding cost differs. It is
plausible that detection dominates.

**So it gets measured on one document before it gets built out** (§7, tasks 4–5).
If the saving is large, the train-split pass uses `--detect-only`; if not, the
existing `predict` runs and resume absorbs the host's flakiness. Either way the
boxes land in the same place.

Crops are then re-derived on **CPU**: the dataset builder re-renders each page and
applies the pipeline's own `boxes.tighten_to_ink` and `extract._prep_crop`, so a
training crop is byte-for-byte the crop inference would produce. Nothing about
cropping is reimplemented.

## 5. NDA shape — stricter than the digests

Training pairs *are* gold values. Consequences, all non-negotiable:

* The dataset is built and stays under `/home/clemi/sindri-client-data`. It is
  never committed, never printed, never enters an AI context.
* Only **aggregate counts** about it may be reported (pairs per split, pairs per
  `char_type`, unrenderable rows) — the same discipline as `runner summary`.
* The renderer and the builder are verified on **synthetic** gold. That is
  sufficient: the round-trip property is universal, so it does not need real data
  to be proven.
* Training runs on the GPU host, which already holds the drawings. The rendered
  targets must be pushed there too, which is a **new category of client data
  leaving this machine** — the drawings are images, these are the inspection
  values. That is a decision for the data owner, and
  `docs/eval/BASELINE-RUNBOOK.md` already states the rule for it.

## 6. Training and integration

**A separate image.** `requirements-gpu.txt` documents at length why the inference
image is pinned to `transformers==4.49.0` + `autoawq==0.2.8 --no-deps` + torch
2.6.0/CUDA 12.4: Qwen2.5-VL support landed in exactly 4.49.0, and 4.50+ breaks AWQ
dispatch for this model. Adding `peft`/`trl` there risks the inference path every
measurement to date depends on. `Dockerfile.train` is therefore separate, and
needs no AWQ at all — the 7B trains in bf16.

**Adapter integration** mirrors the prompt-variant registry that Rung 2 built:
the adapter id lands in `RunConfig.extra` (the field's own comment already
anticipated `"adapter id"`), so a LoRA arm is attributable exactly as
`readcenter` and `detectbox` were, and an unknown adapter name raises rather than
silently running the base model under an arm's name.

**Holdout discipline**, from the ladder: *"Hold out by document, and ensure
variant drawings are in the holdout, or you'll measure memorization not
generalization."* The frozen split already forces all 18 variant drawings into
test. So: train on **train** (60 docs), tune on **dev** (20), touch **test**
exactly once, at the end, and never before.

## 7. Verdict rule

Unchanged from the hardened Rung-2 rule (`app/eval/experiment.py`), which now
requires cost down **and** matched-row field accuracy held **and** silent errors
not up **and** recall held **and** the gain robust across all six weightings, with
unmeasured counting as not passing.

Two campaign-specific additions:

* **`field_acc` must rise**, not merely hold — this is a reading arm, and the same
  condition `readcenter` and `detectbox` were judged against.
* **The base model must be stated in every comparison.** A `lora7b`-vs-`baseline`
  delta that omits which model produced each side is not a result.

## 8. Risks, named

| risk | why it is real here | mitigation |
|---|---|---|
| overfitting to one template | the corpus is one house style; the ladder names this the primary risk | hold out by document; all 18 variants already in test |
| the 7B is simply worse, LoRA or not | 63.6% of matched rows are already wrong at 72B | `base7b` makes this interpretable instead of confusing — it is a result, not a failure |
| canonical-form training | targets are normalisations, not literal transcriptions (§3) | round-trip test; judge on the metric, which rewards parseability |
| training data never leaves as planned | pushing gold values to the GPU host is a new NDA category (§5) | data-owner decision, taken before the push, not during |
| the 26 h crop prerequisite | the host dropped off the network twice in one evening | measure `--detect-only` first (§4); resume already preserves partial runs |
