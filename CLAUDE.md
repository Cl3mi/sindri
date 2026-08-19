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

Branch `worktree-eval-harness`, PR #2. Suite: **441 passed, 2 skipped** (the 2
skips need `RUN_GPU_TESTS=1` on a GPU host). `SCHEMA_VERSION` = 1 — do not bump
it. Split frozen at `6d174d5e4f1b9228` — do not regenerate it.

Rung-0 baseline (dev split, 20 docs):
`mean_review_cost=174.30 micro_recall=0.646 micro_precision=0.371`,
`missed=169 (contended 82 / isolated 74 / unlocated 13)`, `false_detection=522`.

An older `245.30 / 0.350` appears in git history and in `docs/eval/render150-*`.
That number was measuring a coordinate bug, not the model. Do not quote it as the
baseline.

**Next action:** `docs/plans/2026-08-19-gpu-direction-run.md` — a four-arm GPU run,
already built and tested. Deep context and every measured result:
`docs/plans/2026-08-20-session-handoff.md`.

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

## 6. Verify before claiming anything works

```bash
python -m pytest -q                          # 441 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # guard: 32 passed, 0 failed
python3 -m app.eval.experiment               # baseline / arm decision table
```
