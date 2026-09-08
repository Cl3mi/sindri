# Deployment routes for `read-lora-v1` — what each buys, and the fallback order

Written 2026-09-08, after autoawq was abandoned (see
`2026-09-07-rung3-loraread-result.md` §5b-ii). Read that first.

---

## 0. The question that matters most, answered first

**Would route B (vLLM native LoRA) bring a major benefit to the quality of value
extraction and ballooning?**

**Value extraction: yes, but it is not a NEW benefit — it is the benefit already
measured, finally delivered on the base production serves.**
**Ballooning: no. Categorically none. Neither route moves it by a single row.**

Both halves need the evidence spelled out, because the distinction decides
whether this work is worth more GPU days.

### Why extraction improves, and by exactly how much

Four things determine extraction quality here. Route B changes exactly one.

| | changed by B? | measured |
|---|---|---|
| the adapter's weights | **no** — same `read-lora-v1` | `field_acc` +0.0389, `escaped_error` 123 → 100, `dropped_tolerances` 95 → 33 |
| the base's quantisation | **yes** — NF4 → AWQ | NF4 costs **+6.35** review cost vs AWQ |
| the prompts | no — frozen at `aa7659f1929184ea` | — |
| decoding | no — greedy, deterministic | — |

So B's ceiling is `170.05 − 4.40 ≈ **165.6**`, and **A's ceiling is identical**.
Neither route makes the model read better than `r3-loraread` already showed it
can; they remove the NF4 tax that currently makes that gain unshippable
(172.00 is +1.95 *worse* than production's 170.05).

**If you are hoping B unlocks extraction quality beyond the −4.40, it does not.**
More extraction quality requires a better adapter — retrained targets, more
pairs — not a better way to serve this one.

### Why ballooning does not move at all

Ballooning is detection plus placement. The adapter is deliberately *excluded*
from both, and `r3-loraread` proves the exclusion held:

* `n_pred` exactly **926**, matching its control;
* `dimension`, `gdt`, `surface`, `material` bit-identical in **both** matched and
  false counts (253/356, 31/33, 4/22, 1/18);
* `missed_isolated` frozen at **75**; clamped-sheet recall identical to four
  decimals (0.436 / 0.7259 vs 0.7242);
* balloon placement is a pure geometric function of `target_region` —
  `place.py` contains **zero** references to `kind`, `subtype` or read text.

The single row that did move (`false_detection` 607 → 608) travelled the
*scoring* value channel (`matching.py:82`), not detection.

Serving mechanism cannot change what the adapter is scoped away from. **A and B
are two delivery mechanisms for one already-measured read-stage gain.**

### What would actually move ballooning

Recorded here because it is the more valuable question and neither route
addresses it:

1. **A detect-scoped adapter.** The void arm `lora72bnf4` (adapter over the
   whole model) moved `missed_isolated` **75 → 43** — the only thing in the
   entire campaign that has ever moved isolated misses, which Rung 1 had
   recorded as "provably untouched" by every knob and prompt. It cost
   `false_detection` 607 → 931 because a *read*-trained adapter was steering
   detection. An adapter trained **on detection targets** is the untested
   experiment, and it is the one with a mechanism behind it.
2. **Oversized sheets.** 4 of 20 documents cost **283.75** review vs 141.62 at
   **0.371** recall vs 0.728. Tiling or per-sheet handling has never been built
   (handoff §1), and it is the largest unexplored lever in the corpus.

---

## 1. The routes

### B — vLLM with native LoRA (primary)

Serve `read-lora-v1` at runtime on Qwen's **official AWQ** checkpoint.

**For.** Qwen's AWQ is untouched, so no home-grown quantisation risk at all —
the failure mode route A must control for does not exist here. vLLM's
per-request `LoRARequest` maps *exactly* onto the read/detect scoping already in
`VLMBackend._base_weights()`: detection issues requests with no adapter, reads
issue them with one. No merge, no 137 GB checkpoint, no quantisation run.

**Against.**
* **A quantisation mismatch of unknown size.** `read-lora-v1` was trained
  against the **NF4** base; B applies those deltas to **AWQ** weights.
  `CLAUDE.md` §1 warns about mixing two quantisations, and the arm that was
  meant to measure it (`lora72bawq`) never ran because PEFT could not attach.
  B does not remove that question — it is the first chance to answer it.
* **It replaces the serving stack.** A new backend, confidence recovered from
  logprobs rather than `output_scores`, per-request adapter selection, and the
  five prompts re-plumbed without moving `prompt_sha256`.
* **AWQ + multimodal + LoRA in combination is unverified.** Our adapter targets
  `q/k/v/o` in the language model, which is the set vLLM supports — plausible,
  but it must be proven before any 9 h run.
* **It needs its own control.** vLLM's kernels are not transformers' kernels, so
  `r3-awqcontrol` (170.05) is **not** a valid control for a vLLM run. B costs
  two runs, exactly like A.

### A — merge into bf16 and re-quantise (parallel, and the first fallback)

**For.** The theoretically clean path: the adapter is folded into **bf16**, the
true weights NF4 was approximating, so no quantisation mismatch survives into
the served model. Serving is unchanged — the pinned transformers 4.49.0 /
autoawq 0.2.8 image keeps running exactly as it does today. Both 137 GB merges
are **already on disk**, so only quantisation remains.

**Against.** Needs a quantiser that can handle a 72B VL model in 80 GB.
autoawq cannot (§5b-ii) and is deprecated. `llm-compressor` — the vLLM
project's own successor to it — is the candidate, unproven here. The resulting
checkpoint is calibrated by us, not Qwen, so `mergedcontrol` must measure that
overhead before any arm delta is readable.

---

## 2. Fallback order, if B does not work

1. **A with `llm-compressor`.** Both merges are done; this is the shortest path
   to a shippable checkpoint, and being implemented in parallel so it is ready
   rather than started cold. Its zero-scale control is mandatory and runs first.
2. **A with a second quantiser.** If `llm-compressor` also cannot fit the model,
   the remaining options are GPTQ via the same toolchain, or quantising on two
   cards. Note `quantizer.py:133` already spreads blocks across visible GPUs
   (`"cuda:" + str(i % device_count)`), which halves per-card accumulation —
   ~88 GiB per card by the measured rate, so still short on its own, but it
   composes with a working offload.
3. **Ship NF4 + the scoped adapter as a pilot.** `r3-loraread` is real and
   robust: −4.40 against its control, `field_acc` 0.3730 → **0.4119**,
   `escaped_rate` 0.2579 → **0.2096** — the best field accuracy and the lowest
   silent-error rate of any configuration measured. It costs +1.95 review cost
   against production **and** ~2.3× inference wall-clock. Defensible for a pilot
   where silent errors matter more than throughput; poor for production.
4. **Bank the finding and stop.** The campaign's result stands without any
   deployment change: the read adapter works, scoping is what made it
   measurable, and the remaining levers are the two in §0 — a detect-scoped
   adapter and oversized-sheet handling — both worth more than this last −4.40.

**Do not** re-attempt autoawq (§5b-ii), and do not fix a quantisation OOM by
cutting `max_calib_samples` or `max_calib_seq_len`.

---

## 3. Running A and B on one card each

The two are independent: A needs a card for quantisation then for its predicts,
B needs one for its control and arm predicts. Nothing is shared but the models
volume, and A only writes under `/models/merged`.

The ordering constraint is not the hardware, it is the controls. **Neither arm
may be read before its own control has run**: `r3-mergedcontrol` for A,
a vLLM-base run for B. Both controls are measured against `r3-awqcontrol`
(170.05) to price the route's own overhead, and only then is the arm compared
against its control to price the adapter.

Judge every one of them on `field_acc` and `escaped_rate` alongside review cost.
Review cost alone has been wrong three times on this corpus, once by
`experiment.py` itself.
