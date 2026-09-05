# Sindri — working notes for agents

Auto-balloons engineering drawings: detect inspection characteristics with a VLM,
read their values, and score the result against client gold in expected **reviewer
effort** rather than raw accuracy. `app/pipeline/` is the product; `app/eval/` is
the measurement harness. They are deliberately separate — eval never imports
pipeline internals that move under tuning.

## 1. Hard rules — the client corpus is under NDA

A guard hook (`~/.claude/hooks/sindri-guard.py`) enforces this. It is not
advisory, and it has been right every single time it fired.

* Protected root: `/home/clemi/sindri-client-data`. Full rationale in
  `docs/eval/DATA-HANDLING.md`.
* Only the reviewed CLI may touch it: `python3 -m app.eval.runner
  <probe|headers|ingest|split|predict|score|compare|summary|variants>`, plus
  `setup_client_data.py` and `sync_client_data.sh`.
* **Single, unpiped, unchained commands only.** These are all denied and each one
  has actually cost time here: `cmd | tail`, `cmd && cmd`, `cmd > file`, a heredoc
  whose body mentions the root, and `ls` on the root itself. Run the sanctioned
  command bare, then post-process in a *separate* call that names no protected
  path.
* A bare `.pdf` anywhere in a command string is denied. Put throwaway scripts in
  the scratchpad and run them by path.
* Never read `*.pdf`, spreadsheets, `*.gold.json`, `*.pred.json`,
  `*.report.json`, `doc_id_map*`. Use `runner summary` — it is the only
  sanctioned view of a run: aggregate metrics, salted ids, no client values.
* Corpus layout (you cannot `ls` it; this is from `setup_client_data.py`):
  `corpus/{originals,stamped,excel}` + `gold/ runs/ reports/ meta/`.
  `originals` = clean drawings the pipeline reads. `stamped` = ballooned
  drawings gold positions come from. **They are different files.**
* **Never widen the guard.** Re-verify after any change:
  `bash ~/.claude/hooks/test-sindri-guard.sh` → `32 passed, 0 failed`.

## 2. Where things stand

**Read `docs/plans/2026-08-30-session-handoff.md` first — it is the current state
of play.** Everything below is the durable summary.

Branch `worktree-eval-harness`, PR #2. Suite: **626 passed, 2 skipped** (the 2
skips need `RUN_GPU_TESTS=1` on a GPU host). `SCHEMA_VERSION` = 1 — do not bump
it. Split frozen at `6d174d5e4f1b9228` — do not regenerate it.

Rung-0 baseline (dev split, 20 docs), the current reference:
`mean_review_cost=173.05 micro_recall=0.646 micro_precision=0.371`,
`field_acc=0.3799`, `escaped_rate=0.2600`,
`missed=169 (contended 82 / isolated 74 / unlocated 13)`, `false_detection=522`.

**It was 174.30 / 0.3636 until 2026-09-02**, when a char_type scoring bug was
fixed (see §3's note on the synonym map). Same dumps, corrected scoring; every
arm was re-scored the same way and **no verdict flipped**. Numbers quoted from
before that date are one policy behind — check the date before comparing.

An older `245.30 / 0.350` appears in git history and in `docs/eval/render150-*`.
That number was measuring a coordinate bug, not the model. Do not quote it as the
baseline.

**Rung 1 and Rung 2 are closed: seven arms, seven losses.** Every detection knob
and both prompt levers were tested and all lost — and neither prompt arm moved the
bucket it targeted. Treat the committed configuration as tuned, and do not propose
another knob or prompt without a mechanism that predicts which bucket moves and
why. Evidence: `docs/plans/2026-08-21-direction-run-findings.md` (detection) and
`docs/plans/2026-08-24-rung2-reading-quality.md` (prompts, plus the Phase A
diagnostics that route the residual).

**Rung 3 (LoRA on the read stage) is in flight.** The 72B trains at 4-bit NF4 in
**38.8 GB on one H100** — gate passed. Its GPU phase is done: the dependency
change to the inference image is proven safe (AWQ reproduces the baseline to full
float precision, 20/20 deltas `0.0`), and serving on NF4 costs **+6.75** review
cost, so a LoRA served that way must recover 6.75 before reaching parity with
production. Plan: `docs/plans/2026-08-27-rung3-lora-plan.md`; design:
`docs/plans/2026-08-27-rung3-lora-design.md`.

**The data-owner decision is GRANTED** (2026-09-02): rendered target values may
be pushed to the GPU host for training. The adapter `read-lora-v1` is TRAINED
(2026-09-04) from 731 verified pairs — 645 train / 86 validation over 47 / 7
documents, r=8, best checkpoint chosen on a by-document holdout at epoch 2
(`eval_loss` 0.2951 → **0.2816** → 0.2876, so epoch 3 was already overfitting
while train loss kept falling to 0.21). Both arms are registered and running.

**Both GPU-free wins from handoff §6 are banked**, and only one paid what was
predicted:

* `review.LOW_CONF` 0.6 → 0.8: **−3.00**, exactly as derived, and now **measured**
  by a fresh predict run (`r3-awqcontrol`, 2026-09-03). The 0.6–0.8 band was 18
  matched pairs, 100% wrong, zero correct.

  **So there are two AWQ reference numbers and they differ only by this
  threshold.** `baseline-dev` = 173.05 is the pre-change scoring reference;
  **`r3-awqcontrol` = 170.05 is what current code produces**, and it is what a
  new arm must be compared against. Runs are told apart by
  `config.extra.review_low_conf`, absent on every dump predicted before
  2026-09-02. The gate result: cost −3.00 with recall, `n_pred`, `missed`,
  `false_detection`, `correct`, `field_acc` and every field aggregate
  **bit-identical**; only 15 rows moved escaped → flagged. That also
  re-confirms decoding determinism on a run taken 9.5 h later.

  The NF4 side has the same pair and passed the same way: `r3-base72bnf4` =
  179.80 is pre-change, **`r3-nf4control` = 176.40 is current code**, delta
  −3.40 over exactly 17 rows, `field_acc` 0.3730 both sides, and `micro_recall`,
  `n_pred`, `missed`, `false_detection`, `field_failures`,
  `field_failure_modes` and `char_type_confusion` all bit-identical. So BOTH
  quantisations hit a pre-registered prediction to the decimal.
* The `char_type` rows: predicted ~−4.6, **measured −1.25**. The premise was
  wrong; see §3.

## 3. Measured dead ends — do not retry these

Each was implemented, measured, and reverted or rejected. Re-deriving them costs
GPU days.

* **Render resolution / pixel budget.** 80 → 150 MP un-clamped two sheets to full
  300 dpi and changed nothing: isolated misses 251 → 252, review cost *worse*
  under all six weightings. Reverted. Also do not build tiled rendering on the
  premise that resolution recovers misses.
* **Maximum-cardinality matching.** Recovered 26 misses by destroying 27 correct
  pairings; field accuracy on matched rows fell 36.4% → 25.4% while review cost
  *fell*. Kept only as the `--assignment max_cardinality` diagnostic. Never the
  default.
* **Filtering predictions to `score_kinds`.** Would delete 61 of 308 matches that
  non-`dimension` kinds legitimately make, because gold's `dimension` bucket
  includes GD&T and surface callouts (`normalize._DIMENSION_WORDS`).
* **The `merge_adjacent` knobs, in either direction.** Break-even is 5.0 false
  detections per recovered miss (`miss=10`, `false=2`). Measured 6.50 at
  `merge_y_gap` 20→8 and 8.75 at `merge_max_lines` 2→1 — above break-even at both
  doses and *worsening* as merging is reduced, so no intermediate setting wins.
  Also: only 8 of 82 contended misses are merge artefacts, and `isolated` is
  provably untouched by merge knobs (74 in all three arms).
* **Detect tile size (`VLM_TILE=768`).** Found nothing (`n_pred` 830→833, `missed`
  *up* 2, recall *down*), moved 16 gold rows contended→isolated, and cost
  −0.0662 field accuracy on matched rows plus +0.0336 `escaped_rate` — the worst
  arm measured. Damage concentrates 12× on the render-clamped sheets. This is a
  *different* lever from the render pixel budget above; both ends of the
  resolution family are now closed.
* **The read prompt, toward callout selection** (`readcenter`, +0.90 cost,
  `field_acc` −0.0162). Detection came back bit-identical, so it was an isolated
  test of the read prompt — and its target bucket, `misread.misplaced`, was
  **provably untouched at 64 → 64**. Naming the centre callout recovered none of
  them, so those rows are not an ambiguity about *which* callout to read.
* **The detect prompt, toward tighter boxes** (`detectbox`). `experiment.py` once
  called it a WIN on a −0.25 cost delta; it is not. Not robust, `ci95` spanning
  zero, both `compare_runs` guards fired, it destroyed 18 legitimate
  `gdt`/`theoretical` matches by the `score_kinds` mechanism above, and its target
  bucket moved the **wrong way** (49 → 54). It also shows that suppressing
  non-`dimension` detections is that same dead end wearing a different hat.
  Under the corrected char_type policy it no longer has even a nominal cost win:
  **+0.75, better under 3 of 6 weightings** (was −0.25 and 4 of 6).
* **Serving a LoRA adapter on the AWQ base. Not a loss — an IMPOSSIBILITY.**
  `lora72bawq` was the "deployment question" arm: attach `read-lora-v1` to what
  production actually serves. On a clean card (`memory.used=1 MiB`) PEFT refused
  at load:

      ValueError: Target module WQLinear_GEMM(in_features=8192, out_features=8192,
      bias=True, w_bit=4, group_size=128) is not supported. Currently, only the
      following modules are supported: torch.nn.Linear, torch.nn.Embedding, ...

  autoawq replaces every `q_proj`/`k_proj`/`v_proj`/`o_proj` with
  `WQLinear_GEMM`, and PEFT can only inject into the module types listed above.
  The later `device_map contains a CPU or disk device` errors in that log are
  DOWNSTREAM: the failed first attempt still held ~40 GB, so `device_map="auto"`
  spilled to CPU and AWQ refuses that. Do not chase the device_map message.

  So the design's open question — "the mismatch's size is unknown and one arm
  measures it" — has an answer, and it is that the mismatch cannot be measured
  this way at all. Deploying a LoRA means one of: serve the NF4 base (pay the
  measured +6.75 review cost AND ~2.3x inference wall-clock), merge the adapter
  into bf16 and re-quantise to AWQ, or move to a serving stack with native LoRA
  support. All three are decisions, not experiments.
* **The `char_type` bucket as a synonym-map problem.** `wrong:char_type` is the
  largest single failure mode (115 of 308 matched pairs), and the standing
  hypothesis was that gold's German labels were missing from
  `normalize.CHAR_TYPE_SYNONYMS`. Adding the obvious words (breite/höhe/tiefe/
  dicke, rundheit, parallelität) moved **exactly nothing** — a re-score was
  byte-identical. The real fault was that the map matched the WHOLE label while
  `char_type_kind` matches on word containment, so compound gold labels
  ("Diameter MIN") were admitted to scoring by one function and judged unequal by
  the other. Fixing that is worth **−1.25, not the ~−4.6 the handoff estimated**:
  it corrects 16 of the 115 rows, of which only 5 were wrong in `char_type` ALONE
  and so became fully correct. **Do not propose more synonym entries.** The
  residual is real: `Diameter→Distance` 11 and `Distance→Diameter` 11, a
  symmetric confusion over the leading Ø that `parser.py` infers Diameter from —
  a read-stage fault, which is Rung 3's target. `char_type_confusion` in the
  digest is the aggregate that settles this; read it before touching the map.
* **`predict --detect-only` as a way to cheapen the crop pass.** Detection is
  ~2/3 of per-document cost, not the reads: detection-only measured 10 m 55 s and
  23 m 45 s on dev documents 2 and 3 against a full-predict median of ~16 min, and
  document 3's detection alone exceeded that median. Kept as a diagnostic because
  it produced the measurement. **The corollary is useful though:** cutting
  detection cost — fewer or cheaper tiles, a lower `max_new_tokens`, since the
  returned JSON arrays are short — would shorten *every* run, whereas the read
  stage has little left to give.

## 4. Conventions — match these, they are load-bearing

* **TDD, strictly.** Failing test → watch it fail → minimal implementation →
  watch it pass → commit. One task, one commit. The tests here document *why* a
  behaviour exists, not just that it does; write the docstring accordingly.
* **Every aggregate must reconcile against a count that already exists.** The
  digest's identities (taxonomy sums to `n_gold`, `pred_kinds` to `n_pred`,
  per-kind `matched + false == pred`, `missed_diagnosis` to `missed`) are the
  acceptance bar. An aggregate that cannot be cross-checked is a number asking to
  be trusted.
* **A new field's default must not lie about historical data.** `Optional[...] =
  None` plus a `*_not_measured` count, never a plausible-looking `0.0`. A stale
  report once reported "all 20 frames agree" for a run where 14 disagreed, and it
  cost a full analysis cycle. See `DocScore.frame_origin_frac`.
* **Re-score, don't just re-summarise.** `runner summary` on an old report shows
  defaults for fields that report never had. If a view looks empty or perfect,
  suspect staleness first.
* **`MatchParams` is the comparability guard.** Anything that changes what a match
  *means* belongs in it, so `compare_runs` refuses a cross-mode comparison instead
  of crediting the difference as an improvement. Default to current behaviour.
* **Never judge a change on review cost alone.** An arm must also hold field
  accuracy on matched rows and not raise `escaped_rate`. `app/eval/experiment.py`
  encodes this; `weights.miss=10 > weights.escaped=5` makes cost gameable.
* **Comment the *why*, not the what.** This codebase explains the trade a line
  makes and what breaks without it. Match that density.
* Commits: imperative subject, body explaining *why*, trailer
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## 5. Traps that are structural, not mistakes

* **Container `git_sha` is always `"unknown"`** (`.git` is dockerignored). Resume
  compares the whole `RunConfig`, so before detection knobs were recorded in
  `RunConfig.extra` a re-run silently skipped all 20 documents as "already
  predicted". Always use a fresh run name per experiment arm.
* **The GPU container mints and destroys its own doc-id salt**, so ids in a
  `predict` log join to nothing. Per-document facts come from `runner summary`
  under the local salt. `predict` warns about this; the warning is correct.
* **Check which GPU is free before running** — occupancy varies between the two
  H100s, and a 72B AWQ load into an occupied card falls back to Tesseract, which
  then fails every document. MIG is not currently configured despite what the
  stale CDI spec lists.
* **Scoring is deterministic here** (greedy VLM decoding): 16 unchanged documents
  gave per-document deltas of exactly `0.0` across a GPU device change. So one arm
  per hypothesis is enough, no repeats, and any non-zero delta is causal.
* **The digest-key trap.** The pre-commit hook blocks any staged `.json` whose
  content carries `"upper_tol"` or `"lower_tol"` as a quoted token — it cannot tell
  a COUNT keyed by a field name from a VALUE stored under one. Aggregate keys are
  therefore namespaced (`field:lower_tol`, `fields:char_type+nominal`). Do **not**
  reach for `SINDRI_ALLOW_DATA_COMMIT`: every future digest commit would then need
  it, which trades a permanent hole in a data guard for six characters.
* **The Bash guard denies more shapes than it first appears.** `cmd | head`,
  `a && b`, `> file`, a bare `.pdf` anywhere in the command string, and even
  `git add <file-whose-CONTENTS-mention-the-protected-root>` are all refused. Run
  sanctioned commands bare, and split a file edit from its `git add` into separate
  calls. `sync_client_data.sh` is **not** in the allowlist regex, so pulls are the
  operator's to run, not an agent's.
* **Never `git checkout` on the GPU host while a queue is running.** Bash reads
  a script incrementally, so replacing `run_gpu_queue.sh` on disk mid-run can
  corrupt the executing queue — and a checkout is exactly how code reaches that
  host. If something must be deployed while a queue runs, `scp` it to a path
  **outside** `~/sindri` (that is why `run_train_lora.sh` takes no `$REPO` and
  can run from `~`). Note also that the host **cannot fetch from GitHub** at
  all: its remote is credential-less HTTPS and its ssh key is a deploy key for
  a different repo, so code arrives via
  `git push ssh://<host>/home/rebe_test3/sindri <branch>:refs/heads/from-operator`
  followed by a checkout of `from-operator`.
* **The GPU host is unreliable, not merely slow.** 24+ users, load 80–200. In one
  evening it dropped an ssh channel mid-run, killed a container two documents from
  the end, and left the network for ~14 h *without rebooting*. Long runs belong in
  `tmux` **on the host** (`KillUserProcesses=false` is confirmed; another user's
  server has 123 days' uptime) — see `run_gpu_queue.sh`, which is resumable via
  `.complete` markers and never scores, because gold is not there.

## 6. Verify before claiming anything works

```bash
python -m pytest -q                          # 626 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # guard: 32 passed, 0 failed
python3 -m app.eval.experiment               # baseline / arm decision table
```
