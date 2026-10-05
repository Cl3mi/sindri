# Session handoff — Rung 2 closed, Rung 3 not yet started

Written 2026-08-27. Supersedes `2026-08-20-session-handoff.md` for anything they
disagree on. Read `CLAUDE.md` first, then this.

**One line:** Rung 2 is finished and lost — seven arms, seven losses, both prompt
levers tested and neither moved the bucket it targeted. Two GPU-free wins are
sitting unbanked. Rung 3 (LoRA) has three prerequisites nobody has paid for yet,
and the ladder as originally written says to do something cheaper first.

---

## 1. State

Branch `worktree-eval-harness`, PR #2, HEAD `d4b4ff6`, pushed.

```
python -m pytest -q                          # 510 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -m app.eval.experiment               # 5 arms, all "no"
```

`SCHEMA_VERSION` = 1 (never bumped). Split frozen at `6d174d5e4f1b9228`
(train 60 / dev 20 / test 20, seed 13; all 18 variant drawings forced into test).

Rung-0 baseline, unchanged and still the comparison point for everything:

| | |
|---|---|
| `mean_review_cost` | **174.30** |
| `micro_recall` | 0.6457023060796646 |
| `field_acc` (matched rows) | 0.3636 |
| taxonomy | missed 169, escaped_error 129, flagged_error 67, flagged_correct 40, correct 72, false_detection 522 |
| `prompt_sha256` | `aa7659f1929184ea` |

An older `245.30 / 0.350` is in git history — it measured a coordinate bug. Do
not quote it.

---

## 2. What Rung 2 established

**Seven arms, seven losses.** Every perturbation of this pipeline, in every
direction, has lost:

| arm | lever | cost | `field_acc` | robust |
|---|---|---|---|---|
| detectbox | detect prompt | −0.25 | +0.0284 | **no** (4/6 weightings) |
| tightmerge | `merge_y_gap` | +0.35 | +0.0009 | no (2/6) |
| readcenter | read prompt | +0.90 | −0.0162 | no (0/6) |
| finetiles | detect tile size | +5.00 | −0.0662 | no (0/6) |
| nomerge | `merge_max_lines` | +5.60 | −0.0123 | no (0/6) |
| render150 | render pixel budget | worse | — | — |
| max-cardinality | matcher assignment | *lower* | −0.110 | — |

Full evidence in `docs/plans/2026-08-24-rung2-reading-quality.md` (Phase A, C and
D sections). The two prompt arms are the important ones:

* **`readcenter`** changed nothing upstream — `n_pred`, `missed`, `contended`,
  `isolated`, `misplaced`, `false_detection` came back **bit-identical** — so it
  was an isolated test of the read prompt. Its target bucket,
  `error_cause_crosstab.misread.misplaced`, went **64 → 64**. Naming the centre
  callout recovered none of them. Those rows are not ambiguous about *which*
  callout to read; the crop does not contain the right one.
* **`detectbox`** is the trap case. It passed the three original verdict
  conditions and the tool recommended confirming it on the full corpus. It is
  not robust (ci95 `[−6.35, 6.00]`), it tripped both `compare_runs` regression
  guards, it destroyed 18 legitimate matches (`gdt` 31→21, `theoretical` 17→9) by
  the documented `score_kinds` mechanism, and its target bucket moved the *wrong*
  way (49 → 54).

**Diagnosis of the residual**, from the Phase A diagnostics (all GPU-free):

* Failures are diffuse, not tolerance-shaped: all four fields fail on ~60% of the
  196 wrong rows, and the modal signature is **all four wrong at once (49 rows,
  25%)**.
* `wrong:` outnumbers `missing:` **314 to 142** — the pipeline produces a value
  and it is a *different* value.
* 80 rows dropped a tolerance, and they use **49 distinct gold (upper, lower)
  pairs across 18 documents (61% distinct)** — so the tolerances really are
  printed per callout, not inherited from an ISO 2768 table. The bucket is
  winnable. Four documents do show the general-tolerance signature (19 of 80
  rows), so `field_acc` has a modest ceiling below 1.0.
* Confidence is saturated: **284 of 308 matched pairs sit at ≥0.8**, and every one
  of the 24 below 0.8 is wrong.

---

## 3. Bank these two first — they are free and now unblocked

Both were parked all campaign so they could not confound an arm. Rung 2 is
closed, so that constraint is lifted. Together they are worth **~4× detectbox's
headline, for no GPU**.

**3.1 `review.LOW_CONF` 0.6 → 0.8 — worth −3.00 cost.**
`app/pipeline/review.py:16`. Every one of the 24 matched pairs below confidence
0.8 is wrong, and the `0.6–0.8` band holds 18 rows with a **100% error rate** and
**zero correct rows**. Raising the threshold flags the 15 that currently escape
and flags no correct row:

```
cost         174.30 -> 171.30   (-3.00, i.e. 15 rows x (5-1))
escaped_rate 0.2704 -> 0.2390
field_acc    0.3636 -> 0.3636   (unchanged)
```

Derived exactly from the stored confidences in `confidence_by_taxonomy`, not
estimated. Caveat: n=24 is small, so the *rate* is uncertain even though the
−3.00 is exact for these dumps. It is a `review.py` change, so confirming it
needs one predict run — but the arithmetic does not.

**3.2 The 23 `char_type`-only rows — worth ~−4.6 cost.**
`fields:char_type` alone is 23 of 196 wrong rows (11.7%), and `wrong:char_type`
is the single largest failure mode at 115. Look at
`app/eval/normalize.py:CHAR_TYPE_SYNONYMS` and `app/pipeline/parser.py` first —
`--reparse-check` prices the parser half in a CPU second.

**A warning specific to 3.2.** `CHAR_TYPE_SYNONYMS` is *scoring* policy, not
pipeline behaviour. Changing it changes what "correct" means, and `compare_runs`
has **no fingerprint for it** — `MatchParams` is the only comparability guard and
a synonym-map edit leaves no trace in it. Any change there must re-score both
sides and say so loudly, or it will silently credit itself.

---

## 4. Rung 3 — what it actually requires

The ladder in `2026-07-14-extraction-quality-optimization-handoff.md` §5 is the
original design, and it says three things this session's conclusion glossed over.

**4.1 Rung 2 is not actually finished.** The ladder defines Rung 2 as "Prompt
optimization **+ few-shot**", and specifically: *"In-context image exemplars
(Qwen2.5-VL is multi-image): prepend 1–3 crop→correct-answer examples, optionally
retrieved to match the current crop / dominant template. Training-free adaptation
that exploits the consistent house style."* Only the prompt half was run. Nobody
has tried exemplars, and `RunConfig.extra`'s own comment
(`"tuned knobs, few-shot bank id, adapter id, ..."`) anticipated them.

Given that two *instruction* changes both lost, "show it examples instead of
telling it rules" is the obvious untested hypothesis, and it needs no training
stack.

**4.2 The ladder recommends LoRA-ing the 7B, not the 72B.** Verbatim: *"Best
experiment: LoRA the **7B** and see if it beats zero-shot 72B (would also cut
inference cost)."* That is a cheaper, more informative first experiment than
adapting the 72B.

**4.3 You cannot LoRA the current 72B weights.** The deployed checkpoint is
`Qwen/Qwen2.5-VL-72B-Instruct-AWQ`. AWQ is an inference-only quantisation; PEFT
cannot train adapters against it. Training the 72B needs a **second copy of the
model in a trainable format** (bf16 ~145 GB, or NF4 via bitsandbytes), which
means disk, download, and a different quantisation stack on a shared host.

And the dependency window is genuinely fragile. `requirements-gpu.txt` documents
at length why it is pinned to `transformers==4.49.0` + `autoawq==0.2.8 --no-deps`
+ torch 2.6.0/CUDA 12.4 — Qwen2.5-VL support landed in exactly 4.49.0 and 4.50+
breaks AWQ dispatch for it. There is no `peft`, `trl`, or `bitsandbytes` in the
image. Adding a training stack to *this* image risks the inference path that
every measurement so far depends on; a separate training image is safer.

**4.4 Training data does not exist yet, and building it costs GPU.** Trainable
pairs are (crop, correct transcription). Crops come from detection boxes, and
**the train split (60 documents) has never been predicted** — only dev has. At
~26 min/doc that is ~26 h of GPU just to obtain crops. Gold gives *parsed* fields,
not the printed string, so targets must be rendered from gold and that renderer
is the inverse of `parse_value`.

**4.5 Overfitting is the named risk.** *"Hold out by document, and ensure variant
drawings are in the holdout, or you'll measure memorization not generalization."*
The frozen split already forces all 18 variants into test, so train on **train**
only, tune on **dev**, and touch **test once**.

### The one piece of prep that is useful whichever rung is chosen

Both few-shot exemplars and LoRA need the same artifact: a **training-pair
builder** — gold fields → target transcription string, plus the crop. Its
correctness is provable with **zero client data**, by round-tripping through the
existing parser: render a synthetic gold row to text, parse it back with
`parse_value`, and the fields must match. That is a TDD-able property and it is
the natural first task of either rung.

Also note the NDA shape of this work: training pairs *are* gold values, so the
dataset must be built and stay under the protected root, never enter an AI
context, and never be committed. Only aggregate counts about it can be reported.

---

## 5. Traps and tools added this session

* **`app/eval/gate.py`** — the reproduction gate, extracted so a missing
  comparison point fails loudly instead of silently skipping (findings §8.1). Now
  used by both the control arm and any re-score.
* **`app/eval/orphan.py`** — tells a dead arm from a dead connection.
  `run_experiment_gpu.sh` reported `ARM FAILED` when only its ssh had died and the
  container was `Up 7 hours` and still working. On predict failure it now branches
  three ways, and **breaks** rather than continuing when a container is still
  alive, because `continue` would send the next arm onto a card the orphan still
  holds — a guaranteed Tesseract fallback.
* **`score --reparse-check`** — prices a parser change from stored dumps in a CPU
  second instead of a 9 h arm. Gate: on an unmodified parser `identical` must
  equal `n_pairs` (verified 308/308).
* **Prompt variant registry** (`vlm_backend.py`) — `SINDRI_READ_PROMPT` /
  `SINDRI_DETECT_PROMPT` select variants, the name lands in `RunConfig.extra`, and
  an unknown name **raises** rather than silently running base under a treatment
  arm's name. `prompt_sha256` hashes the *effective* prompts; with no env set it
  is still `aa7659f1929184ea`, pinned by a test.
* **Hardened verdict rule** (`experiment.py`) — now also requires recall to hold
  (at `compare_runs`' own 0.005 threshold, so the two tools stop disagreeing) and
  the gain to be robust across all six weightings, with *unmeasured* counting as
  not passing.
* **The digest-key trap.** The pre-commit hook blocks any staged `.json`
  containing `"upper_tol"`/`"lower_tol"` as quoted tokens. Aggregate keys are
  therefore namespaced (`field:lower_tol`, `fields:char_type+nominal`), with a
  test tying the digest to the hook's contract. Do not reach for
  `SINDRI_ALLOW_DATA_COMMIT` — every future digest commit would then need it.
* **The GPU host is unreliable, not just slow.** In one evening it dropped an ssh
  channel mid-run at load 204, killed a container two documents from the end, and
  left the network entirely for ~14 hours without rebooting (uptime survived). The
  mitigations that paid off were **resume** (18 of 20 documents preserved) and
  **a fresh run name per arm**.
* Wall clock under load 201: 12–28 min per document, and **46 min** for the
  14457×2384 pt sheet that clamps to 109 dpi.

---

## 6. Measured dead ends — do not retry

Existing list is `CLAUDE.md` §3 (render resolution / pixel budget, maximum-
cardinality matching, filtering predictions to `score_kinds`, the `merge_adjacent`
knobs, detect tile size). This session adds two:

* **The read prompt, toward callout selection.** `readcenter` lost and its target
  bucket was provably untouched (64 → 64) on a bit-identical detection substrate.
* **The detect prompt, toward tighter boxes.** `detectbox` is not robust, destroys
  legitimate non-`dimension` matches, and moved its target bucket the wrong way.
  Note it also demonstrates that suppressing non-`dimension` detections is the
  `score_kinds` dead end wearing a different hat.

---

## 7. Recommended next sequence

1. **Bank §3.1 and §3.2** — free, and §3.1 alone is 12× detectbox's headline.
2. **Build the training-pair builder** (§4, last part) — needed by few-shot *and*
   LoRA, provable with no client data.
3. **Run the few-shot arm** — the untested half of Rung 2, one GPU arm, no
   training stack. Two instruction changes lost; examples are the open question.
4. **Only then decide Rung 3**, and if you climb it, LoRA the **7B** first per
   §4.2. Treat "LoRA the 72B" as Rung 4-adjacent: it needs a second weight format,
   a separate training image, and ~26 h of GPU for train-split crops before any
   training starts.
