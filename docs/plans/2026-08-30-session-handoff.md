# Session handoff — Rung 3 mid-campaign, GPU phase done, blocked on a data decision

Written 2026-08-30. Read `CLAUDE.md` first, then this. Supersedes
`docs/plans/2026-08-27-session-handoff.md` where they disagree.

**One line:** Rung 2 is closed (7 arms, 7 losses). Rung 3's unattended GPU phase
finished cleanly — all three runs, 20 h 24 m, zero failures. Two of three are
scored. The next step needs a decision only the data owner can make.

---

## 1. Verified state

Branch `worktree-eval-harness`, PR #2, HEAD **`6a32408`**, pushed, working tree
clean.

```bash
python -m pytest -q                          # 560 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -m app.eval.experiment               # arm decision table
```

`SCHEMA_VERSION` = 1 (never bump). Split frozen at `6d174d5e4f1b9228`
(train 60 / dev 20 / test 20, seed 13; all 18 variant drawings forced into test).

**Comparison points, all three measured:**

| run | model | cost | recall | `field_acc` | note |
|---|---|---|---|---|---|
| `baseline-dev` | 72B **AWQ** | **174.30** | 0.6457 | 0.3636 | the frozen reference |
| `r3-awqgate-dev` | 72B AWQ (new image) | 174.30 | 0.6457 | 0.3636 | **gate: 20/20 deltas exactly 0.0** |
| `r3-base72bnf4-dev` | 72B **NF4** | 181.05 | 0.6688 | 0.3574 | NF4 costs **+6.75** |

An older `245.30 / 0.350` is in git history; it measured a coordinate bug. Never
quote it.

## 2. What the GPU phase established

Both queues ran detached under tmux on `4mehpc4_3`, 20 h 24 m wall clock, and
**every stage finished with zero failures and zero Tesseract fallbacks.**

**2.1 The dependency change is safe.** `peft` and `bitsandbytes` were added to
`requirements-gpu.txt` for Rung 3's serving path — the image every prior
measurement rests on. Re-running the AWQ path in that image reproduced the
baseline to full float precision (174.30, recall `0.6457023060796646`,
escaped_rate `0.27044025157232704`, all 20 per-document deltas exactly `0.0`).
The frozen baseline survives and every Rung-2 conclusion still stands. This was
the campaign's largest single risk, and its failure mode was silence.

**2.2 NF4 costs 6.75 review-cost points.** Significant, `ci95 [0.15, 13.75]`,
worse under all six weightings, robust. It became *more eager and less precise*:
96 more predictions of which **85 were false**, 11 misses recovered but converted
into silent errors (+16 escaped). The sharpest number is `correct` — right *and*
unflagged — **exactly unchanged at 72**. Noise concentrates in non-`dimension`
kinds (`note` +36 preds/+27 false, `material` +12/+12 all false), i.e. coarser
quantisation degrading the detect prompt's JSON discipline rather than perception.

**Why that control mattered:** without it, a `lora72b` result of 178 would have
read as "the LoRA is worse than baseline" when it would in fact have been "the
LoRA recovered 3 of the 6.75 points quantisation costs".

**2.3 Detection dominates cost, not reading.** Measured on dev: detection-only
took 10 m 55 s and 23 m 45 s for documents 2 and 3, against a full-predict median
of ~16 min. Document 3's detection *alone* exceeded that median. Reads are about a
third of per-document time — the opposite of what the plan assumed. So
`--detect-only` is NOT used, and **cutting detection cost (fewer/cheaper tiles,
lower `max_new_tokens`) would shorten every run**, which is worth knowing well
beyond Rung 3.

**2.4 An independent replication.** The dropped-tolerance winnability ratio is
**61% on both quantisations** (AWQ 80 rows/49 distinct; NF4 95/58). The finding
that those tolerances really are printed per callout — rather than inherited from
an ISO 2768 table — now holds on a different numeric substrate.

## 3. What is on the host, ready to use

`4mehpc4_3`, `~/sindri-eval-data/runs/`:

| run | dumps | purpose | pulled? |
|---|---|---|---|
| `r3-trainpredict` | **60** | the boxes Rung 3's training crops come from | **no** |
| `r3-awqgate` | 20 | reproduction gate | yes, scored |
| `r3-base72bnf4` | 20 | NF4 control | yes, scored |

Images: `sindri-gpu` (**pre-change, no peft** — used for the train pass),
`sindri-gpu-nf4` (peft + bitsandbytes), `sindri-train` (LoRA training).
Both GPUs idle. Logs with on-disk timestamps at `~/rung3-logs/`.

Model cache is on `/data` — 7.0 TB, 6.1 TB free — holding the 72B AWQ *and* the
145 GB bf16 72B already downloaded.

## 4. THE BLOCKER — a data-owner decision, not an engineering one

Building training pairs needs gold, which is local-only. Training needs those
pairs on the GPU host. **Pushing them means the inspection VALUES leave this
machine** — the drawings already there are images; these are the numbers.

`docs/eval/BASELINE-RUNBOOK.md` states the rule: copying confidential data to
another machine is the data owner's decision, not something the harness does
silently. Nothing past this point can proceed without it.

What is ready the moment it is granted:
* `app/train/targets.py` — gold → target text, all 8 shapes round-trip through
  `parse_value` (verified before the spec was written)
* `app/train/dataset.py` — writes (crop, target) pairs under the protected root,
  using the pipeline's own `tighten_to_ink` + `_prep_crop`, reporting **counts
  only**
* `train_lora.py` + `Dockerfile.train` — 4-bit NF4 LoRA, **gated and passing**:
  the 72B loads at **38.8 GB** on one H100 and read a synthetic callout as
  `'Ø20 +0.1 -0.1'`

## 5. Next steps, in order

1. **Get the §4 decision.** Everything else waits on it.
2. Pull `r3-trainpredict` (60 dumps) locally.
3. Build pairs with the scratchpad script in
   `docs/plans/2026-08-27-rung3-lora-plan.md` Task 11 Step 2. Report only
   `docs, pairs, unrenderable, no_gold, no_box`. **A large `unrenderable` count is
   a finding, not a nuisance.**
4. Push pairs (§4), train the adapter on one H100 in `sindri-train`.
5. **Run TWO arms, not one** — this is a change from the original plan, and the
   reason is §2.2:

   | arm | compared against | isolates |
   |---|---|---|
   | `lora72b-nf4` | `r3-base72bnf4` (181.05) | the **fine-tune**, cleanly — same base, adapter the only difference |
   | `lora72b-awq` | `baseline-dev` (174.30) | **adding an adapter to production**, cleanly — same base, adapter the only difference |

   Both are single-variable comparisons. The second is more decision-relevant (the
   product serves AWQ) and dodges the 6.75-point tax, but its adapter is trained
   on NF4 and served on AWQ — a mismatch whose cost is *unknown* and which one arm
   measures rather than argues about.

6. Judge with `python3 -m app.eval.experiment`: cost down **and** `field_acc` not
   down >0.02 **and** `escaped_rate` not up >0.02 **and** recall held **and**
   robust across all six weightings — plus, for a reading arm, `field_acc` must
   actually **rise**, and the targeted bucket (`wrong:nominal` 102,
   `wrong:char_type` 115) must move.

## 6. Two GPU-free wins still unbanked

Both were parked so they could not confound an arm. Together worth ~4× any arm
measured so far, for no GPU.

* **`review.LOW_CONF` 0.6 → 0.8 — worth −3.00 cost.** Every one of the 24 matched
  pairs below confidence 0.8 is wrong, and the `0.6–0.8` band holds 18 rows at a
  **100% error rate with zero correct rows**. Raising it flags the 15 that
  currently escape and flags no correct row: cost 174.30 → 171.30, `escaped_rate`
  0.2704 → 0.2390, `field_acc` unchanged. Derived exactly from stored
  confidences, not estimated. Caveat: n=24, so the *rate* is uncertain even though
  the −3.00 is exact for these dumps.
* **The 23 `char_type`-only rows — ~−4.6 cost.** Look at
  `app/eval/normalize.py:CHAR_TYPE_SYNONYMS` and `app/pipeline/parser.py`;
  `score --reparse-check` prices the parser half in a CPU second.
  **Warning:** `CHAR_TYPE_SYNONYMS` is *scoring* policy and `compare_runs` has no
  fingerprint for it — a change there must re-score both sides and say so loudly,
  or it silently credits itself.

## 7. Tools built this campaign, and the traps they encode

* **`app/eval/gate.py`** — the reproduction gate; absent/unreadable/empty all fail
  loudly. Used by the control arm and by every re-score.
* **`app/eval/orphan.py`** — tells a dead arm from a dead connection. Fired
  correctly in production. Its no-container message deliberately does **not** say
  "the arm failed": `--rm` removes the container on a clean exit too, so "died"
  and "finished unobserved" are indistinguishable from outside.
* **`run_gpu_queue.sh`** — host-side detached queue: resumable via `.complete`
  markers, stops on failure rather than running later stages against missing
  inputs, refuses an occupied card, carries the CDI podman-unshare overlay, and
  **never scores** (gold is not on the host). Tested by *executing* it against
  stubbed `podman`/`nvidia-smi` — structural tests had passed on a version whose
  `{ … } | tee` wrapper made `break`/`continue`/`FAILED+=` no-ops in a subshell.
* **`score --reparse-check`** — prices a parser change from stored dumps in a CPU
  second. Gate: `identical == n_pairs` (verified 308/308).
* **Prompt/adapter/quant registries** in `vlm_backend.py` — selected by env, names
  recorded in `RunConfig.extra`, and an unknown name **raises** rather than
  silently serving the base under a treatment arm's name.
* **Hardened verdict rule** in `experiment.py` — also requires recall held (at
  `compare_runs`' own 0.005 threshold) and robustness across all six weightings,
  with *unmeasured* counting as not passing.

### Traps that cost time here

* **The digest-key trap.** The pre-commit hook blocks any staged `.json`
  containing `"upper_tol"`/`"lower_tol"` as quoted tokens. Aggregate keys are
  therefore namespaced (`field:lower_tol`, `fields:char_type+nominal`). Do **not**
  reach for `SINDRI_ALLOW_DATA_COMMIT` — every future digest commit would need it.
* **The guard denies more than it looks like.** `cmd | head`, `a && b`, `> file`,
  a bare `.pdf` anywhere in a command string, and `git add <file-whose-contents-
  mention-the-protected-root>` are all refused. Run sanctioned commands bare, and
  split edits from `git add` into separate calls.
* **`sync_client_data.sh` cannot be run by an agent** — it names the protected root
  and is not in the guard's allowlist regex. Pulls are the operator's to run.
* **The host is unreliable, not just slow.** 24+ users, load 80–200, and in one
  evening it dropped an ssh channel mid-run, killed a container two documents from
  the end, and left the network for ~14 h without rebooting. `tmux` survives
  (`KillUserProcesses=false`; another user's server has 123 days' uptime).
* **My stall detector cries wolf.** `run_gpu_queue.sh`'s watchdog fires when no log
  has been written for 90 min — which is also what a *finished* queue looks like.
  It nagged six times after both queues completed. Suppress it once a
  `queue finished` line exists for every launched queue.

## 8. Measured dead ends — do not retry

`CLAUDE.md` §3 has the originals (render resolution/pixel budget,
maximum-cardinality matching, filtering predictions to `score_kinds`, the
`merge_adjacent` knobs, detect tile size). This campaign adds:

* **The read prompt toward callout selection** (`readcenter`). Lost, and its target
  bucket was *provably untouched* — `misread.misplaced` 64 → 64 on a bit-identical
  detection substrate.
* **The detect prompt toward tighter boxes** (`detectbox`). `experiment.py` called
  it a WIN; it is not. Not robust (4 of 6 weightings, `ci95` spanning zero), both
  `compare_runs` guards fired, it destroyed 18 legitimate `gdt`/`theoretical`
  matches by the documented `score_kinds` mechanism, and its target bucket moved
  the **wrong way** (49 → 54).
* **`--detect-only` for the crop pass** (§2.3). Kept as a diagnostic, since it
  produced the measurement, but dead for its original purpose.
