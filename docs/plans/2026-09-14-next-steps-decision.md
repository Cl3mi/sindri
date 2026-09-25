# Where the campaign goes after the hybrid, and why

Written 2026-09-14, after `r3-hybrid` closed the detector-swap lead
(`docs/plans/2026-09-11-hybrid-arm-result.md`). Read `CLAUDE.md` first, then
`docs/plans/2026-09-09-session-handoff.md`.

**One line:** two of the three highest-value moves are not experiments at all —
an honest generalization number and a client conversation — and the one
experiment worth running is the first lever ever aimed at the mechanism the
hybrid identified.

---

## 1. What the hybrid changed about the plan

Before it, the campaign's model of read quality was "the reader is the thing to
improve", and Rung 3 was built on that. The hybrid measured the opposite:

* hold the reader, change the boxes: `field_acc` 0.4798 -> 0.2739 (**-0.206**)
* hold the boxes, change the reader: **+0.013**
* the best reader change ever measured on fixed boxes (`read-lora-v1`): **+0.039**

So the crop the reader is handed matters ~5x more than which model reads it.
Everything below is ordered by that, plus by what a client decision actually
needs.

---

## 2. THE DECISION — what runs, in what order, and why

### First, two things that are not experiments

**(1) Score the TEST split.** `splits.py` reserves 20% as a frozen test set and
forces the structurally atypical *variant* drawings into it, explicitly "so
cross-template generalization stays visible". **It has never been predicted or
scored.** Every stage in `run_gpu_queue.sh` runs `--split dev`; every digest in
`docs/eval/` is dev. So 133.93 / 0.7170 / 28.3% missed — the numbers the client
has been shown — come from the one split that ten-plus arms have been selected
against.

This is the largest unpriced risk in the project, and it is a client risk before
it is a technical one: if the dev number does not hold on unseen templates, the
figures already presented are optimistic and we want to find that out first.
Cost: one predict run on one card, ~6.5 h.

*Free precursor, available with no GPU at all:* `r3-trainpredict` exists — 60
documents, predicted for the LoRA crop pass, never scored because "train is the
training split". Production is **zero-shot**, so train is contaminated only for
*adapter* arms; for a production number it is 60 documents never used to select
anything. Its dumps predate the `review.LOW_CONF` 0.6 -> 0.8 change, so they
score ~3 high and are not comparable to 133.93 as a delta — but they answer
"does the dev number roughly hold" for the cost of a `score` command.

**(2) Re-derive `weights.json` with the client.** Zero GPU, and it decides
whether the campaign's only win ships. `read-lora-v1` beats production once a
silent error costs **>=9.4x** a wasted re-check; today's weights assume 5x. The
hybrid's verdict was weights-independent — it failed on the product goal itself
— but the adapter's is not, and it is stuck at "-3.40, not significant, and
unshippable at today's prices". The client has now seen real output, so their
reviewers can price "a value shipped wrong with no warning" against "a spurious
box I delete" from experience rather than in the abstract.

### Then, the one experiment: CROP PREPARATION

`_CROP_PAD=6`, `_MIN_CROP_H=40`, `_MAX_UPSCALE=3.0` and `boxes.tighten_to_ink`
decide what the reader actually sees. They are tested for behaviour
(`tests/test_crop_prep.py`) and named in the 2026-07-14 handoff as pipeline
components — but there is **no digest, no §3 entry, no registered arm**. Every
box-related lever tested so far was about *which boxes exist*: tile size, the
merge knobs, render resolution, the detect prompt, the read prompt. Nothing has
tested *what crop a box becomes*.

That is now the only untested family sitting on the dominant lever, and it has a
mechanism that names its bucket in advance — which is the bar `CLAUDE.md` §2
sets for any new knob. Registered separately, before the run:
`docs/plans/2026-09-14-crop-context-arm-prediction.md`.

### Then, in descending value per unit of work

4. **Unblock route A serving** — engineering, not research. Both AWQ checkpoints
   are built and on disk; the blocker is three transformers/compressed-tensors
   failures deep. Worth ~-3.4 if `loramerged` reproduces its control.
5. **Multi-page coverage (+8 drawings, 8.1%)** — plumbing, not research.
   `render_page` already takes `page_index`; `extract()` never passes one. The
   gold for those sheets exists and is charged as misses at `w=10` today.
6. **Tiled rendering (+19 drawings, 19.2%)** — the biggest coverage prize and
   the biggest build. Note this is NOT the closed dead end: that one was raising
   the pixel budget to recover MISSES inside supported drawings (80 -> 150 MP,
   isolated misses 251 -> 252, lost). This is coverage of drawings that are out
   of claimed scope entirely, and it has never been measured.

---

## 3. What was considered and REJECTED

* **Another detector swap.** Closed by the hybrid, and the direction is worse
  than neutral: the 32B's recall advantage is recall of boxes, not values.
* **Another detection knob or prompt variant.** Seven arms, seven losses, and
  neither prompt arm moved the bucket it targeted.
* **Confidence thresholding for silent errors.** Measured: all 105 escaped
  errors sit at confidence >= 0.8. The signal is saturated.
* **Fine-tuning the DETECTOR, as Rung 3 fine-tuned the reader.** The obvious
  reading of "the detector's weights are what move boxes" — and it is blocked on
  data, not on compute: gold gives balloon POSITIONS, not callout boxes, so box
  targets would have to be synthesised from `tighten_to_ink` around each balloon
  anchor. That trains the model to reproduce a CV heuristic rather than to find
  the callout, and the heuristic is part of what is under suspicion. Revisit
  only if a source of real box targets appears.
* **Scoring dev harder.** The adapter arms lose significance at 15 documents and
  the temptation is to squeeze the dev split. Dev is exhausted as evidence; more
  documents have to come from train or test, which is what (1) does.

---

## 4. What is being built now, and why these two pieces first

Both are GPU-free, both are prerequisites, and neither commits to an outcome:

**A `test` stage in the queue, and a `SPLIT` in the re-score batch.** The queue
hard-codes `--split dev` for every stage except `trainpredict`, and
`rescore_onepage.sh` hard-codes it too. Without both, (1) cannot run at all.
`_check_comparable` will correctly refuse a test report against a dev one, so
the test number stands alone rather than as a delta — which is what it is.

**Crop knobs selected by environment and recorded in `RunConfig.extra`.** The
constants are module-level today, so an arm changing them would be invisible in
every report, and `_reusable_dump` — which compares the whole `RunConfig` —
would skip all 20 documents as "already predicted" across the change being
measured. That is the trap `CLAUDE.md` §5 records, already paid for once.

They are recorded **only when non-default**, following `serving_backend`,
`quant`, `adapter` and `detect_model`: a key present on every future dump would
change the config of every historical one and force a re-predict of the whole
corpus for no measurement.

And they flow into **training** as well as inference. `app/train/dataset.py`
imports `_CROP_PAD` and `_prep_crop` on purpose — "a training crop that differs
from an inference crop" is the failure its docstring exists to prevent — so the
resolver has to be the single source or the two silently diverge the first time
anyone changes the knob.
