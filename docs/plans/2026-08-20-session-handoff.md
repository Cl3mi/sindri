# Session Handoff — 2026-08-18 → 2026-08-20

Everything learned, measured, and rejected, so none of it has to be re-derived.
Read `CLAUDE.md` first for the hard rules; this document is the evidence behind
them. The next action is `docs/plans/2026-08-19-gpu-direction-run.md`.

---

## 1. The headline: the baseline was measuring a bug

The Rung-0 baseline read `mean_review_cost=245.30 recall=0.350`, with
`missed=310` carrying 63% of the review cost. That pointed everyone at detection.

**It was wrong.** Gold balloon positions are CV-recovered from the **stamped**
drawings, while the pipeline reads the **clean originals** — two different files
(`setup_client_data.py`: `originals` = "clean drawings (pipeline input)",
`stamped` = "ballooned drawings (gold positions)"). `score_doc` compared them
directly: predictions placed with `dump.page_rect`, the match gate scaled by
`gold.page_rect`, and nothing ever validated that the two agreed. On 14 of 20 dev
documents the page extents differ — by up to 4.1× the page diagonal — so gold rows
and predictions occupied coordinate spaces that never overlapped.

Fixed at the source (`ingest --originals`). The corrected baseline:

| | before | after |
|---|---|---|
| micro recall | 0.350 | **0.646** |
| micro precision | 0.201 | **0.371** |
| mean review cost | 245.30 | **174.30** |
| `missed` | 310 | **169** |
| `false_detection` | 663 | **522** |
| `missed_isolated` | 251 | **74** |

141 real matches had each been charged **twice** — once as a missed callout, once
as a false detection.

### Why it is causation, not correlation

Three independent confirmations, worth knowing because the confound (bigger sheets
are plausibly harder, and extent mismatch is largest on the biggest sheets) was
real and had to be killed:

1. **Symmetry.** `missed` and `false_detection` each fell by *exactly 141*. Only a
   coordinate fix retires a miss and a phantom together; a detection improvement
   cannot produce that.
2. **Targeting.** Score-time reconciliation lifted `micro_recall_frames_differ`
   0.1875 → 0.6071 while `micro_recall_frames_agree` stayed **0.7376 unchanged**.
   The same sheets tripled recall with nothing touched but the transform, so
   "harder sheets" cannot explain it.
3. **Two mechanisms, one answer.** Transform-at-score-time predicted
   `174.30 / 0.646 / 0.270` from untouched dumps; transform-at-ingest-time landed
   on the same three numbers to the digit.

Before promoting the fix, re-ingesting **without** `--originals` reproduced the old
baseline character-identically (`245.30 / 0.350 / 0.182`, same 79 exclusions),
proving ingest is deterministic and that exactly one thing changed. `scale` beat
`center` (0.646 vs 0.570), identifying the stamped sheets as scaled-to-fit exports.

**The reproduction recipe** (no `--cv`, no `--variants` — verified):
```
python3 -m app.eval.runner ingest \
  --pdfs /home/clemi/sindri-client-data/corpus/stamped \
  --excel /home/clemi/sindri-client-data/corpus/excel \
  --originals /home/clemi/sindri-client-data/corpus/originals \
  --out /home/clemi/sindri-client-data/gold
```

---

## 2. Measured dead ends — with the evidence

### 2.1 Render resolution is not the constraint

Raised `MAX_RENDER_PIXELS` 80 → 150 MP. It worked mechanically: both 225 dpi
sheets reached full 300 dpi. It bought nothing.

`mean_delta +1.65` review cost, `b_better_fraction 0.0` — worse under **all six**
weightings. Corpus isolated misses 251 → 252, `n_pred` +13, `false_detection` +14,
matched −1. `7bd2fd06` went 109 → 149 dpi (+37% linear) and scored *bit-identically*
(recall 0.0377, 42 isolated misses, both runs). The two un-clamped sheets carried
all 76 of their isolated misses into the unclamped bucket. Also ~1.87× the detect
tiles on large sheets, so it is a pessimization in quality *and* compute.

Reverted (`0de52eb`). Artifacts kept: `docs/eval/render150-summary.json`,
`render150-vs-baseline.json`.

### 2.2 The matcher is not the lever

Matching is greedy, which genuinely is not maximum-cardinality and does strand
rows (`test_greedy_strands_a_gold_row_whose_only_candidate_is_taken`). So Kuhn
augmentation was implemented and measured:

| | greedy | max_cardinality |
|---|---|---|
| `missed` | 169 | 143 (−26) |
| `correct` | 72 | **52 (−20)** |
| `flagged_correct` | 40 | **33 (−7)** |
| `misplaced_matches` | 80 | **126** |
| field accuracy on matched rows | **36.4%** | **25.4%** |

It recovered 26 misses by destroying **27 correct pairings**. Review cost *fell*,
because `miss`=10 > `escaped`=5 — the metric being gamed, not the pipeline
improving. Kept as the `--assignment max_cardinality` diagnostic only.

**That failure is itself the diagnosis:** if stealing a prediction from one gold
row to give its neighbour breaks a correct pair, there is only **one detection
where two callouts exist** — `merge_adjacent` collapsing siblings. The contended
bucket is a detection problem, which is exactly what the GPU run tests.

### 2.3 Filtering predictions to `score_kinds` would hurt

Tempting, because 180 of 522 false detections carry a kind gold does not have. But
`normalize._DIMENSION_WORDS` deliberately includes GD&T and surface terms, so
gold's in-scope `dimension` bucket covers those callouts while the detector splits
them across `gdt`/`surface`/`theoretical`. `matched_by_pred_kind` shows **61 of 308
matches come from non-`dimension` kinds** — filtering would convert every one into
a miss at weight 10. Still an open metric question (§5), but the naive version is
measured harmful.

Only `theoretical` (untoleranced basic dimensions) and `material` have no possible
gold counterpart.

---

## 3. Defects found and fixed

| what | where | why it mattered |
|---|---|---|
| **Client part number leaked to the terminal** | `report._check_comparable` interpolated the raw `doc_id`, and `_cmd_compare` built its `Anonymizer` *after* the call | Pre-existing, reachable by a sanctioned command — the guard hook cannot help when the tool is allowed. Now hashed, with a position-based fallback so no code path can emit one. `813993e` |
| Gold in stamped coordinate space | `ingest` used one `--pdfs` for both balloon recovery and `page_rect` | The §1 headline. `b85d283` |
| `976d3c0d` silently kept wrong geometry | it has no clean original, so `--originals` had nothing to map onto | Positions now dropped → matched on value, counted `unlocated`, which is honest. `1271278` |
| GPU script clobbered the baseline digest | every path was `$RUN`-parameterised **except** the summary output | Any experiment would have destroyed the baseline it was compared against. `ba59abb` |
| Detection knobs invisible and un-resumable | nothing populated `RunConfig.extra` | Two experiment arms produced indistinguishable reports, and resume skipped knob changes as "already predicted". `44e95e0` |
| Stale reports claimed a clean bill of health | new `DocScore` fields defaulted to `0.0` | A re-summarised (not re-scored) report reported "all 20 frames agree" for the run where 14 disagreed. Now `Optional = None` + `n_docs_not_measured`. `d875726` |

Two defects in the **plan** that was being executed, worth knowing because that
plan is still referenced: its Task 2 `-k effective_dpi` selector matches no test
name, and its Task 3 "metric was not redefined" grep is unsatisfiable — it counts
the explanatory comment the plan itself prescribes. Substance was verified another
way (0 non-comment diff lines touching any match knob).

---

## 4. Instrumentation added — what each view answers

All in `runner summary` output, all values-blind and committable.

| key | question it answers |
|---|---|
| `error_causes` | parser vs perception: `misparse` 52 / `misread` 144 |
| `misplaced_matches` | matched but far from the gold balloon — geometry quality |
| `clamped_docs`, `clamped_vs_unclamped` | did the render budget bind, and did it cost recall |
| `pred_kinds`, `false_detections_by_kind`, `matched_by_pred_kind` | the kind asymmetry, with per-kind conservation |
| `missed_diagnosis` | **the routing view**: `contended` (merge/dedupe) vs `isolated` (coverage) vs `unlocated` (gold-side, unreachable) |
| `frame_mismatch` | gold/dump page frames, split into origin vs extent, with a `not_measured` staleness tell |

`app/eval/experiment.py` turns multi-arm digests into a decision table and a
verdict that cannot be satisfied by cost alone.

---

## 5. Open decisions — none of these are mine to make

1. **Run the GPU arms?** Built and tested; ~4–5 h per arm.
   `docs/plans/2026-08-19-gpu-direction-run.md`. Control arm is a hard
   reproduction gate that aborts the run if it fails.
2. **The `false_detection` metric question, still unanswered.** 180 of 522 false
   detections carry a kind gold cannot have (`dimension` 342, `theoretical` 85,
   `note` 49, `gdt` 26, `surface` 14, `material` 6); of those only **91**
   (`theoretical` 85 + `material` 6) have no possible gold counterpart at all,
   since GD&T and surface callouts *do* have in-scope gold. Options: exclude only
   the impossible kinds, admit verbal gold under value matching, or leave it and
   quote the artefact. All change `MatchParams` and so break comparability with
   this baseline. §2.3 has the numbers.
3. **Rung 2 prompts.** The largest remaining bucket is not misses at all: 196
   matched-but-wrong rows, `misread` 144 vs `misparse` 52. Prompt edits are local
   and testable, but measuring them needs a GPU run.
4. **`976d3c0d` for a test-split run.** Its gold has no positions now. Fine for
   dev (it is excluded), but a test-split evaluation needs the clean original
   sourced or the document formally excluded.

---

## 6. Artifact map

* `CLAUDE.md` — hard rules, conventions, dead ends. Loaded every session.
* `docs/plans/2026-08-19-gpu-direction-run.md` — the next action, step by step.
* `docs/eval/DATA-HANDLING.md` — NDA rationale.
* `docs/eval/BASELINE-RUNBOOK.md` — how to take a baseline.
* `docs/eval/baseline-summary.json` — the current baseline digest.
* `docs/eval/baseline-recon-scale-summary.json` — the causation proof (§1).
* `docs/eval/baseline-maxcardinality-summary.json` — the inflation result (§2.2).
* `docs/eval/render150-summary.json`, `render150-vs-baseline.json` — the
  resolution dead end (§2.1).
* `run_experiment_gpu.sh` — multi-arm runner. `run_baseline_gpu.sh` — single run.
* `python3 -m app.eval.experiment` — decision table.

19 commits, `f5cbfe6..44e95e0`, all pushed to PR #2. Suite went 391 → **441**.
`SCHEMA_VERSION` never bumped; `splits_hash` still `6d174d5e4f1b9228`; no hook file
ever touched; the doc-id salt digest is unchanged from session start.
