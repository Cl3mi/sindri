# Rung 3, second arm: the read-scoped adapter WINS

Written 2026-09-07. Supersedes the "Rung 3 is closed, the adapter loses" verdict
in `docs/plans/2026-09-05-session-handoff.md` §3 and `CLAUDE.md` §2.

**One line:** scoping the LoRA to the read pass turned a +10.00 loss into a
**−4.40 win** — the first arm of the campaign to win — but 172.00 is still
**+1.95 worse than the 170.05 production ships**, because the NF4 base it must
be served on costs +6.35 on its own.

---

## 1. The result

`r3-loraread` vs its matched control `r3-nf4control`. Same base, same image, same
split, same code except the scoping.

| | control | **loraread** | void arm (`lora72bnf4`) |
|---|---|---|---|
| `mean_review_cost` | 176.40 | **172.00** | 186.40 |
| `field_acc` | 0.3730 | **0.4119** | 0.3778 |
| `escaped_rate` | 0.2579 | **0.2096** | 0.2222 |
| `correct` | 77 | **84** | 88 |
| `escaped_error` | 123 | **100** | 106 |
| `flagged_error` | 77 | 87 | 118 |
| `missed` | 158 | 159 | 117 |
| `micro_recall` | 0.6688 | 0.6667 | 0.7547 |
| `n_pred` | 926 | **926** | 1291 |
| `false_detection` | 607 | 608 | 931 |

`mean_delta −4.40`, `ci95 [−7.9, −1.2]` (excludes zero), better under **6 of 6**
weightings, `warnings: []`.

Cost reconciles exactly:
`10(+1) + 5(−23) + 2(+1) + 1(+15) = −88`, ÷20 = **−4.40**.

**The entire win is 23 silent errors converted, bought with 15 extra flags.**
That is the trade `weights.json` was written to reward. It clears every condition
`CLAUDE.md` §4 imposes: cost falls, field accuracy *rises*, `escaped_rate` falls,
recall holds, robust across every weighting.

## 2. The scoping gate

Registered before the run: `n_pred` returns to exactly **926** and
`false_detection` to **607**.

**`n_pred` = 926, exact.** Stronger evidence still: `dimension`, `gdt`, `surface`
and `material` are bit-identical in *both* matched and false counts
(253/356, 31/33, 4/22, 1/18), `missed_isolated` is frozen at 75, and
clamped-sheet recall is unchanged to four decimals. Detection ran on the
control's weights. The scoping worked.

**`false_detection` came in at 608, not 607. The registered reasoning was wrong,
not the arm.** Matching is not purely geometric:

* `matching.py:82` subtracts `value_bonus` (0.35) when a prediction's parsed
  `nominal` equals gold's.
* `matching.py:68-76` matches a gold row whose balloon position was never
  recovered — `missed_diagnosis.unlocated` — by **value similarity alone**.

So read text reaches the matcher through `nominal`. The channel is fully
accounted for: 25 predictions moved `note` → `theoretical`, because
`extract.py:251` relabels a theoretical box whose *read* matches `_NOTE_REF_RE`,
which changes the `hint` given to `parse_value` and therefore the `nominal`. Only
those two kinds moved anywhere; only `unlocated` changed (16 → 17) while
`contended` and `isolated` stayed at 67 and 75. One unlocated gold row lost its
value-similar partner.

**The sound detection-identity gate, for future arms:** `n_pred` exact, **plus**
per-kind `matched`/`false` identity on the kinds not subject to read-driven
relabelling. That is what actually held here.

## 3. What scoping did not fix

* **The tolerance-shape overcorrection persists** — it is a property of the
  training targets, not of the scoping. `missing:*_tol` −101 combined, but
  `wrong:*_tol` +88 and `spurious:*_tol` +23: close to a wash on raw counts.
  `render_target` emits an explicit `+x -y` whenever gold has one, so the model
  learned to always emit tolerance-shaped output. Fixing this means changing the
  targets, not the serving.
* **The named target moved by half.** `Distance→Diameter` 7 → 2, but
  `Diameter→Distance` unchanged at **11**. `wrong:char_type` 106 → 95 overall.
* **Oversized sheets are untouched**, as they must be: clamped recall 0.436 both
  sides, `missed_isolated` 15/60 both sides. Handoff §1 still holds — 4 of 20
  documents cost ~2× the effort at half the recall, and that remains the largest
  unexplored lever.

## 4. Why this is not yet shippable

| | cost | field_acc | escaped_rate |
|---|---|---|---|
| `r3-awqcontrol` — **production today** | **170.05** | 0.3799 | 0.2285 |
| `r3-nf4control` | 176.40 | 0.3730 | 0.2579 |
| `r3-loraread` | 172.00 | **0.4119** | **0.2096** |

NF4 costs **+6.35** against production; the adapter recovers **−4.40**; net
**+1.95 worse than what ships today**. Deploying this arm as-is would be a
regression on review cost — while simultaneously having the best field accuracy
and the lowest silent-error rate of the three.

That is the case for **merge-and-requantise** (handoff §4 route 1), now built:
`merge_lora.py` plus the `loramerged` / `mergedcontrol` queue stages. It removes
the +6.35 and the inference penalty. If the −4.40 survives the round trip onto
the AWQ base, the result lands near **165.6** — a production win rather than a
moral one.

**`mergedcontrol` runs first and is not optional.** The merged checkpoint is not
one any AWQ number was measured on, so a delta against `r3-awqcontrol` would
confound the adapter with the round trip. The zero-scale control must reproduce
**170.05**; if it does not, nothing from `loramerged` is attributable.

## 5. Corpus policy change: one-sheet drawings only

**Decision (2026-09-07, operator):** score and train on single-sheet drawings
only.

`render_page` takes `page_index=0` and nothing else, so a gold characteristic
printed on sheet 2 has always been a miss no model could recover. Those rows cost
`w=10` each — the heaviest weight — for a reason no arm can address, and they sit
in the denominator of every recall number in the campaign.

Implemented as `score --pdfs <dir> --max-pages 1`, **off by default**:
`baseline-dev` 173.05, `r3-awqcontrol` 170.05, `r3-nf4control` 176.40 and
`r3-loraread` 172.00 were all scored over the whole dev split, and filtering by
default would silently redefine all four. `RunReport.max_pages` records the
ceiling; `None` means unfiltered, never 1.

Comparability needs no new guard — `report._check_comparable` already raises
`doc set differs`, so a filtered report cannot be quietly compared against an
unfiltered one. **Every arm must be re-scored under the same setting before its
delta means anything**, which is why the four numbers above stay the reference
until a filtered control exists.

Training needs no separate filter: `build_pairs` consumes matched rows from a
scored report, so a report scored with `--max-pages 1` yields one-sheet pairs
only.

### Corpus page counts — PENDING

`probe --summary` already reports `multi_page_docs` and `pages_per_doc`; no code
was needed. The command hit the permission layer for the agent, so the operator
runs it:

```bash
python3 -m app.eval.runner probe <corpus>/originals --summary
```

**TODO: record `n_docs`, `multi_page_docs` and `pages_per_doc` here**, for the
whole corpus and for the dev split, so the size of what the filter removes is on
the record rather than assumed.

## 5b. IN FLIGHT overnight 2026-09-08 — read this first in the morning

Two chains launched detached in `tmux` **on the GPU host**, one per card, at
~00:20 UTC. `KillUserProcesses=no` is confirmed, so they survive every logout.

| tmux | card | chain | logs (`~/rung3-logs/`) |
|---|---|---|---|
| `mc` | 0 | merge zero-scale → quantise → predict `r3-mergedcontrol` | `merge-zero-scale.log`, `r3-mergedcontrol.log`, `chain-control.log` |
| `ma` | 1 | merge `read-lora-v1` → quantise → predict `r3-loramerged` | `merge-read-lora-v1.log`, `r3-loramerged.log`, `chain-arm.log` |

Rough shape: merge ~45 min, quantise several hours, predict ~9 h. Expect the
predicts to finish late morning, not at dawn. Both are resumable — re-running
the same chain skips completed stages via `.complete` markers.

**Check on waking:**

```bash
ssh 4mehpc4_3 'tmux ls; tail -3 ~/rung3-logs/chain-control.log ~/rung3-logs/chain-arm.log'
ssh 4mehpc4_3 'nvidia-smi --query-gpu=index,memory.used --format=csv,noheader'
```

`CHAIN_EXIT=0` in both chain logs means the predicts finished; anything else
names the stage that failed and the chain can simply be re-launched.

**Then, and in this order:**

1. Pull both runs and score them. **Correction to how this was first written
   here:** "`r3-mergedcontrol` must reproduce 170.05" is the wrong gate, and
   stating it that way would have thrown away a valid arm. 170.05 was measured
   on **Qwen's official AWQ checkpoint**; `r3-mergedcontrol` is **our own AWQ
   quantisation** of the bf16 base, over autoawq's generic `pileval`
   calibration. Two different quantisations of the same weights are not
   expected to be numerically identical, so a gap is a measurement, not a bug.
2. Read the two comparisons for what each actually answers:
   * **`r3-mergedcontrol` vs `r3-awqcontrol` (170.05)** — what our quantisation
     pipeline costs against Qwen's official one. Pure overhead, and it is the
     number that decides whether this deployment route is attractive at all.
   * **`r3-loramerged` vs `r3-mergedcontrol`** — the adapter's effect on the
     production serving path. **This is the experiment**, and the only
     comparison the arm may be judged on.
3. The shipping question is neither of those on its own: it is
   **`r3-loramerged` against 170.05**, because that is what production serves
   today. `r3-loraread` already showed the scoped adapter is worth −4.40 on the
   NF4 base; if our quantisation overhead is smaller than that, the route wins,
   and if it is larger, the overhead eats the fine-tune and the answer is to
   improve calibration (or obtain Qwen's) rather than to abandon the adapter.
4. Then `./rescore_onepage.sh`, which also collects the pending page counts.

### 5b-i. The first attempt failed. Two causes, both recorded

The 2026-09-07 launch lost **both** cards ~26 minutes in, before either predict
started. The merges succeeded and both 137 GB checkpoints survived, so only
quantisation had to be redone — `merge_lora.py --quantise-only` exists for
exactly that, because `preflight` otherwise refuses a non-empty `--out`.

1. **`device_map="auto"` — the real one.** `AutoAWQForCausalLM.from_pretrained`
   defaults to it, which placed ~57.5 GiB of the 72B on the card and left ~21
   GiB for calibration activations; `_compute_best_scale` then wanted 9.22 GiB
   with 6.24 GiB free. autoawq's quantizer moves each block to the device
   itself (`quantize/quantizer.py:137`), so a model this size is **meant** to
   sit on CPU while one block at a time visits the GPU. The host has 1007 GB of
   RAM. `AWQ_LOAD = {"device_map": "cpu"}`.
2. **`n_parallel_calib_samples` is not usable here.** The first fix attempt
   chunked calibration; it splits the hidden states but **not** the mrope
   position embeddings, so Qwen2.5-VL dies in
   `apply_multimodal_rotary_pos_emb` — "tensor a (8) must match tensor b (59)".

**Do not fix a quantisation OOM by cutting `max_calib_samples` or
`max_calib_seq_len`.** Both degrade the scale estimates, and the control is
being compared against a checkpoint Qwen calibrated properly. Fix memory by
placement. With the model on CPU the cards sit at ~40 GiB of 79 and autoawq's
defaults run unmodified at ~81 s/block, ~1h50m for 80 blocks.

### 5c. A trap this run walked into, recorded before it is forgotten

The first `merge_lora.py` as committed **could not have worked**. autoawq 0.2.8
ships `awq/models/qwen2vl.py` and no `qwen2_5_vl` one, while this base is
`model_type: qwen2_5_vl` — so quantisation would have failed *after* the 137 GB
load, the fold and the save. 0.2.9 adds the wrapper but imports `qwen3.py` at
init, needing transformers ≥ 4.51; and `awq.quantize.scale` imports
`PytorchGELUTanh`, which transformers removed after 4.51.x. **The window is
exactly 4.51.3**, pinned in `Dockerfile.quant`.

Quantisation therefore runs in its own image and **the serving image does not
move** — it stays at transformers 4.49.0 / autoawq 0.2.8, because 4.50+ breaks
AWQ dispatch for Qwen2.5-VL at inference. `assert_quantisable()` now refuses
before the load; it found this in seconds instead of hours.

## 6. Next steps, in order

1. **Record the page counts** (§5) and re-score the standing runs with
   `--max-pages 1` to establish filtered references. Cheap, no GPU — but note
   every quoted number changes, so do it as one batch and re-derive
   `r3-loraread` vs `r3-nf4control` under the filter before quoting −4.40 again.
2. **Run `mergedcontrol`, and gate on 170.05.** No GPU work after it is worth
   anything until it passes.
3. **Run `loramerged`.** Judge against `r3-mergedcontrol` only.
4. **Fix the tolerance targets** if the merged arm proves out — `render_target`
   teaching the model to always emit `+x -y` is now the largest identified
   read-stage fault, worth more than any remaining serving question.
5. **Oversized sheets** (handoff §1) remain the largest unexplored lever overall.
6. **Re-derive `weights.json` with the client.** Every conclusion here is
   conditional on `miss=10, escaped=5, false=2, flag=1`.
