# Session handoff — Rung 3 closed, and what the pipeline actually costs a reviewer

Written 2026-09-05. **Read `CLAUDE.md` first, then this.** Supersedes
`docs/plans/2026-08-30-session-handoff.md` where they disagree.

**One line:** Rung 3 is closed — the LoRA loses on review cost (+10.00) but is
the first arm ever to move recall and isolated misses, and the arm was
confounded by a scoping bug that is cheap to fix. Meanwhile the honest
production number is that **the pipeline extracts 1 in 4 callouts correctly and
silently ships a wrong value for roughly 1 in 4.**

---

## 0. Verified state — run these first

```bash
cd /home/clemi/mci/sindri/.claude/worktrees/eval-harness
python -m pytest -q                          # 626 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -m app.eval.experiment               # arm table (SEE §6 — its control is WRONG for NF4 arms)
```

Branch `worktree-eval-harness`, PR #2, HEAD `6d91b56`, pushed, tree clean.
`SCHEMA_VERSION` = 1 (never bump). Split frozen at `6d174d5e4f1b9228`.

**NDA, unchanged and non-negotiable.** Never read client PDFs, spreadsheets,
`*.gold.json`, `*.pred.json`, `*.report.json`. Only
`python3 -m app.eval.runner <subcommand>` may touch the protected root, as
single unpiped unchained commands. Pulls (`sync_client_data.sh`) are the
operator's to run. In this session the `score`/`compare`/`summary` commands also
began hitting **permission-layer declines** — if that recurs, hand the exact
command to the operator rather than retrying; they run fine from a human shell.
A heredoc whose body names the protected root is also refused, so write long
docs with the file-writing tool, not `cat <<EOF`.

---

## 1. THE PRODUCTION NUMBER — what a reviewer actually faces today

Measured on `r3-awqcontrol` (current production path: 72B AWQ,
`review.LOW_CONF=0.8`), dev split, 20 documents, 477 scored gold callouts.

**Per document there are ~23.9 real inspection characteristics. Of those:**

| outcome | share of gold | per doc | what the reviewer must do |
|---|---|---|---|
| right **and** unflagged | **16.1%** | 3.85 | nothing — the only free rows |
| right but flagged | 8.4% | 2.00 | re-check, then accept (wasted effort) |
| wrong **and** flagged | 17.2% | 4.10 | correct it — the system asked for help |
| **wrong and SILENT** | **22.9%** | **5.45** | **nothing warns them. Ships wrong.** |
| missed entirely | 35.4% | 8.45 | find and add by hand |
| — | — | **26.10** | **delete spurious detections** |

**Headline fault rate:** of the 308 callouts the pipeline *finds*, **62% have at
least one wrong field** (`field_acc` = 0.3799). End to end, **24.5% of gold
callouts come out fully correct**; only **16.1% come out correct without also
being flagged**.

**The number to quote to a client is 22.9%** — the share of all characteristics
for which the system emits a *wrong value with no warning*. Everything else is
correct, flagged, or visibly absent. This is `escaped_rate`, and it is what the
review-cost model is built to punish (w=5 vs w=1 for a flagged error).

**So today this is not automation. It is a first-pass draft a human must fully
verify.** Nothing in this corpus supports unattended use.

### Where the effort concentrates — the most actionable split in the data

| | docs | mean review cost | recall | isolated misses |
|---|---|---|---|---|
| render-**clamped** sheets | 4 | **283.75** | 0.371 | 16 |
| unclamped sheets | 16 | 141.62 | 0.728 | 58 |

Four oversized drawings (effective 109/208/225/225 dpi after the 80 MP render
budget clamps them) cost **2× the review effort at half the recall**. If the
client's real corpus has that ratio, **handling oversized sheets is worth more
than improving the model.** This is NOT "raise the render budget" — that was
measured and lost (`CLAUDE.md` §3). It means tiling or per-sheet handling, which
has never been built.

---

## 2. What works, what does not, and what is closed

### Works, measured, keep

* **72B AWQ zero-shot as the base.** Every alternative measured worse.
* **`review.LOW_CONF = 0.8`.** −3.00 review cost for nothing given up. The
  0.6–0.8 confidence band held 18 matched pairs, **100% wrong, zero correct**.
  Confirmed by a fresh predict run, not just arithmetic.
* **char_type canonicalisation by word containment** (`normalize.canon_char_type`).
  −1.25, and it closed a bug class: gold labels are German and compound
  ("Durchmesser", "Diameter MIN", "Ebenheit 0,05 zu C") while the parser emits
  English constants.
* **The measurement harness itself.** Two controls in this campaign hit
  predictions registered *before* they ran, to the decimal. That is what makes
  every delta here attributable.

### Closed dead ends — do not re-attempt (full list in `CLAUDE.md` §3)

Render resolution/pixel budget · max-cardinality matching · filtering to
`score_kinds` · both `merge_adjacent` knobs · detect tile size · the read prompt
toward callout selection · the detect prompt toward tighter boxes ·
`--detect-only` for cheap crops · **the char_type bucket as a synonym-map
problem** · **serving a LoRA on AWQ** (§4).

### The residual, routed

* `misread` 140 vs `misparse` 51 — reading dominates parsing.
* **64 of 140 misread rows are `misplaced`** — the pair sits further from its
  balloon than `misplaced_frac`, so the model may have read a *different*
  callout perfectly. No prompt or LoRA fixes those; it is a pairing problem.
* 80 rows dropped a tolerance the drawing prints (61% distinct values across 18
  documents, so they are real per-callout tolerances, not an inherited ISO 2768
  table — verified independently on two quantisations).
* 49 rows have **all four fields wrong** — the box is probably cutting the callout.
* Missed: 82 contended / 74 isolated / 13 unlocated.
* False detections are dominated by `dimension` (342 of 522) — the detector
  over-fires on exactly the kind that matters.

---

## 3. Rung 3 result in full

`lora72bnf4` vs `r3-nf4control` (176.40) — matched control, same base, same
image, same code, adapter the only difference.

**VERDICT: NO.** 176.40 → **186.40 (+10.00)**, better under 2 of 6 weightings,
`ci95 [-3.95, 23.9]` spans zero.

| | control | LoRA |
|---|---|---|
| `micro_recall` | 0.6688 | **0.7547** (+0.086, campaign's largest) |
| `missed` | 158 | **117** |
| `missed_isolated` | 75 | **43** |
| `correct` | 77 | **88** |
| `escaped_error` | 123 | **106** |
| `false_detection` | 607 | **931** |
| `n_pred` | 926 | 1291 |

Cost reconciles exactly: `10(−41) + 5(−17) + 2(+324) + 1(+47) = +200`, ÷20 =
+10.00. **The entire loss is false detections at w=2 (+648) swamping the misses
recovered (−410).**

### The arm was confounded — it did not test the read stage

The design says "scope: the read stage only". It was not.
`vlm_backend.resolve_adapter` wraps the **whole model** in `PeftModel`, so
`detect_regions` ran through adapted weights too — `dimension` +202, `gdt` +67,
`theoretical` +52, `note` +43. A read adapter cannot do that.

### What the read stage did learn

`dropped_tolerances` 95 → **32**, `missing:upper_tol` 71 → 29,
`missing:lower_tol` 91 → 31 — the dropped-tolerance bucket cut by two thirds.
But `wrong:upper_tol` 46 → 107, `wrong:lower_tol` 48 → 130,
`spurious:upper_tol` 17 → 48. `render_target` emits an explicit `+x -y` for
every gold row that has one, so the model learned the **shape**, not merely the
normalisation the design anticipated. It now always emits tolerance-shaped
output.

### The finding worth more than the verdict

**`missed_isolated` 75 → 43.** Rung 1 threw render resolution, detect tile size
and both merge knobs at isolated misses; `CLAUDE.md` records `isolated` as
"provably untouched by merge knobs (74 in all three arms)". The adapter moved it
by 32 **as a side effect**. Isolated misses ARE movable — the lever is the
detector's **weights**, not its knobs or prompts, both of which are closed.

### Training facts

731 verified pairs → 645 train / 86 validation over 47 / 7 documents (holdout
**by document**, seed 13). r=8 on q/k/v/o, 32,768,000 trainable params (0.0446%).
`eval_loss` 0.2951 → **0.2816** → 0.2876, so **epoch 3 was already overfitting**
while train loss kept falling to 0.21; `load_best_model_at_end` selected epoch 2.
Adapter at `/models/adapters/read-lora-v1` in volume `sindri-models` on the host.

Pair-building rejected 247 of 982 matched rows, by reason: `not_round_tripping`
130, `char_type` 46, `gdt_no_hint` 40, `no_nominal` 14, `gdt_hint_mismatch` 17.
The 130 were targets that did not parse back to their own gold row — poison that
would have taught the model text the parser resolves to the wrong fields.

---

## 4. Why `lora72bawq` is impossible — and how to make it possible

### Why it failed

On a **clean card** (`memory.used=1 MiB` logged before launch), attempt 1:

```
ValueError: Target module WQLinear_GEMM(in_features=8192, out_features=8192,
  bias=True, w_bit=4, group_size=128) is not supported. Currently, only the
  following modules are supported: torch.nn.Linear, torch.nn.Embedding,
  torch.nn.Conv1d, torch.nn.Conv2d, torch.nn.Conv3d,
  transformers.pytorch_utils.Conv1D, torch.nn.MultiheadAttention.
```

autoawq replaces every `q_proj`/`k_proj`/`v_proj`/`o_proj` with its own fused
`WQLinear_GEMM` class. **PEFT injects adapters only into the module types listed
above.** A LoRA cannot be attached to an AWQ-quantised layer at all. This is a
stack limitation — no retry, bigger card or config change touches it.

**Do not chase the later error in that log.** Attempts 2 and 3 report
`device_map contains a CPU or disk device`, which is *downstream*: the failed
first attempt still held ~40 GB, so `device_map="auto"` spilled to CPU and AWQ
refuses that. The last error in a log is the one a reader believes; here it is
the misleading one.

### How to make it possible — three routes, best first

1. **Merge and re-quantise (RECOMMENDED).** `PeftModel.merge_and_unload()` folds
   the LoRA into plain bf16 weights, producing an ordinary Qwen2.5-VL checkpoint
   with no adapter at all. Then run autoawq quantisation on that merged model.
   Result: a single AWQ checkpoint with the fine-tune baked in — **no PEFT at
   serving time, no `WQLinear_GEMM` problem, and none of the 2.3× penalty**. The
   145 GB bf16 base is already downloaded on the host.
   - Cost: one merge (minutes) + one AWQ quantisation run (hours). Needs a
     calibration set; autoawq's default generic corpus is fine and involves
     **no client data**.
   - Caveat: the merged model is a *different checkpoint*, so it needs its own
     control. Before trusting any delta, confirm that merging a **zero-scale**
     adapter (or the base alone) re-quantised the same way reproduces
     `r3-awqcontrol`.
2. **Serve NF4 + adapter.** Works today; it is what `lora72bnf4` did. Costs a
   measured **+6.75** review cost (NF4 vs AWQ) **and ~2.3× inference wall-clock**
   (28 → 65 min/document, same card, same image, same host load). Acceptable for
   a pilot, poor for production.
3. **A serving stack with native LoRA** (e.g. vLLM). Unverified for
   AWQ + multimodal + LoRA in combination; treat as research, not a plan.

---

## 5. Best approach for a real client in production

**Do not deploy this as automation.** At 22.9% silent-wrong and 35.4% missed, an
unattended run hands the client a document where roughly one characteristic in
four is wrong with no indication. Deploy it as **a reviewer accelerator with
flagging turned up**, and say so explicitly in any contract.

In priority order:

1. **Ship the current AWQ + `LOW_CONF=0.8` path.** Best measured configuration
   (170.05) and the threshold change costs nothing.
2. **Consider raising `LOW_CONF` further — but only after re-deriving the
   weights.** The confidence distribution is saturated: 284 of 308 matched pairs
   sit at ≥0.8. Flagging that band too would convert 109 escaped errors into
   flagged ones at the price of flagging 117 correct rows. Under the current
   weights that is a net **loss**; under a client whose true cost of a silent
   error greatly exceeds a re-check, it is a **win**. `docs/eval/weights.json` is
   the contract — **every conclusion in this repo is conditional on
   `miss=10, escaped=5, false=2, flag=1`.** Re-derive it with the client before
   optimising anything further.
3. **Fix oversized sheets.** 4 of 20 documents cost 2× effort at half recall
   (§1). Tiling or per-sheet handling has never been built and is the largest
   unexplored lever.
4. **Then the read-only LoRA arm (§6)** — the only fine-tuning result with a
   mechanism behind it.
5. **If the LoRA proves out, merge + re-quantise to AWQ** (§4 route 1) so
   production keeps both its serving path and its speed.

**Set expectations with numbers, not adjectives:** today the tool finds ~65% of
characteristics, gets ~38% of those fully right, and needs a human to add ~8 and
delete ~26 per drawing. Its value is that it drafts the ballooning and flags its
own doubt — not that it is correct.

---

## 6. Next steps, in order

1. **The read-only adapter arm.** Scope the adapter to the read pass —
   `disable_adapter()` around `detect_regions` in `vlm_backend`. TDD it; the
   existing prompt/adapter registry tests are the pattern.
   **Falsifiable prediction, state it before running:** `n_pred` returns to
   exactly **926** and `false_detection` to **607**, bit-identical to
   `r3-nf4control`, because decoding is deterministic and detection would then
   use identical weights. **If `n_pred` is not 926 the scoping is incomplete and
   the arm is void.** Cost: one run (~9 h, or ~20 h with the adapter penalty).
2. **`experiment.py` cannot judge NF4-era arms.** It compares everything to
   `exp-control-summary.json` (AWQ, `review_low_conf` 0.6-era) and reported
   `lora72bnf4` at +13.35 when the matched-control answer is +10.00. Either teach
   it to select a control by `config.extra` (`quant`, `review_low_conf`,
   `model_id`) or stop trusting its verdict line for anything but AWQ-era arms.
   Its `knobs:` line already surfaces the mismatch to a careful reader.
3. **Land the parked parser fix.** Branch `parser-tolerance-sign` (`a2d0daf`),
   deliberately unmerged so it could not confound these arms. `20 +0,3 +0,1`
   parsed the lower bound as `-+0,1` — malformed and sign-inverted. Measured
   `would_fix 0, would_break 0` on dev, so it is a correctness fix, **not a win**,
   and must never be quoted as one.
4. **Re-derive `weights.json` with the client** (§5.2). Everything is
   conditional on it.

---

## 7. Reference — runs, numbers, tools, traps

### Runs on the GPU host (`~/sindri-eval-data/runs/`)

| run | what | state |
|---|---|---|
| `baseline` | frozen Rung-0 dumps | scored, 173.05 (pre-`LOW_CONF`) |
| `r3-awqgate` | dependency gate, 20/20 deltas 0.0 | scored |
| `r3-base72bnf4` | NF4 zero-shot, old threshold | scored, 179.80 |
| `r3-trainpredict` | 60 train docs → training crops | pulled, used |
| **`r3-awqcontrol`** | **AWQ current code — PRODUCTION reference** | scored, **170.05** |
| **`r3-nf4control`** | **NF4 current code — control for the LoRA** | scored, **176.40** |
| `r3-lora72bnf4` | the LoRA arm | scored, 186.40 |
| `r3-lora72bawq` | FAILED to load, see §4 | n/a |

### Four reference numbers — always check which policy produced one

| | pre-`LOW_CONF` | current code |
|---|---|---|
| AWQ | `baseline-dev` 173.05 | **`r3-awqcontrol` 170.05** |
| NF4 | `r3-base72bnf4` 179.80 | **`r3-nf4control` 176.40** |

Told apart by `config.extra.review_low_conf` (absent on pre-2026-09-02 dumps)
and `config.extra.quant`. An older `174.30 / 0.3636` predates the char_type fix.
`245.30 / 0.350` in git history measured a coordinate bug — **never quote it**.

### Tools built this campaign

* `run_train_lora.sh` — host-side training launcher; waits on a predecessor's
  completion **marker** (written only on success), bounded card-drain retry, CDI
  overlay, resumable, never scores. Tested by execution against stubs.
* `train_lora.py --shape-check N` — validates all 645 examples on **CPU in 19 s**
  before the 145 GB model load. Two separate bugs would have been caught by it.
* `app.train.dataset.split_by_document` / `filter_readable_crops` — pure and
  tested, deliberately outside `train_lora.py` because torch is not installed on
  the operator's machine while PIL is.
* `char_type_confusion` in the digest — reconciles to `field:char_type` and
  separates a scoring-policy gap (`unmapped(...)`) from real perception failure.
* `targets.render_target` self-verifies: it parses its own output and raises
  `not_round_tripping` rather than emit a target the parser resolves elsewhere.

### Traps that cost time here

* **Never `git checkout` on the GPU host while a queue runs** — bash reads
  scripts incrementally and can corrupt the running queue. `scp` outside
  `~/sindri` instead.
* **The host cannot fetch from GitHub.** Push over ssh to `from-operator`, then
  check out, then rebuild.
* **Do not rebuild the inference image between a control and its arm.** The arms
  deliberately reused the `sindri-gpu-nf4` build the controls ran on, so the
  adapter was the only variable.
* **The plan's Task 8 spec had never been executed** before this session:
  `train_lora.py` did not exist and `Dockerfile.train` never copied it. Two
  latent bugs (indexing the batch dim off `pixel_values`; crops narrower than the
  processor's factor of 28) each killed a run.
* **`extract._safe_read` swallows every read exception** and returns `("", 0.0)`.
  A crop the processor rejects becomes a silent empty read at inference — which
  is why prediction never crashed on the crops that killed training.
* Container `git_sha` is always `"unknown"`; always use a fresh run name per arm.
* The GPU host is unreliable, not merely slow. Long runs belong in `tmux` **on
  the host**.
