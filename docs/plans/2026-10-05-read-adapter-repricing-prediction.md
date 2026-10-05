# Re-pricing `read-lora-v1` — registered predictions (before any score runs)

Registered 2026-10-05, BEFORE `score --reapply-policy` was run on any of the
six dump sets below. Design and binding decision rule:
`2026-10-05-read-adapter-repricing-design.md` §2, restated here so this file
stands alone.

## 1. Protocol

All scoped dev (`--max-pages 1 --dpi 300 --exclude-clamped`, 15 documents),
all `--reapply-policy` (DERIVED), all crop pad 6.

Old-policy scoped numbers, seen before registering:

| run | cost | escaped | n_pred | false | isolated |
|---|---|---|---|---|---|
| r3-awqcontrol | 133.93 | — | 569 | 346 | — |
| r3-nf4control | 137.47 | 81 | 597 | 370 | 60 |
| r3-loraread | 134.07 | 69 | 597 | 370 | 60 |
| r3-vllmcontrol | 141.20 | 75 | 620 | 396 | 57 |
| r3-vllmlora | 138.73 | 64 | 620 | 397 | 57 |
| r3-lora72bnf4 | (only unscoped, 20 docs: 186.40 vs 176.40) | 106 | 1291 | 931 | 43 |

**Build a serving path iff G_nf4 AND G_vllm each:** cost ≤ −1.50/doc; better
under 6/6 weightings; auto-accept precision does not fall; auto-accept rate
rises strictly. Otherwise close the deployment as a dead end. U never
authorises the merge route as staged.

## 2. Predictions

| # | quantity | old policy | predicted | band |
|---|---|---|---|---|
| P1 | G_nf4 (loraread − nf4control) | −3.40 | **−1.0** | [−1.8, −0.3] |
| P2 | G_vllm (vllmlora − vllmcontrol) | −2.47 | **−0.8** | [−1.6, −0.2] |
| P3 | \|G_nf4 − G_vllm\| | 0.93 | ≤ 0.6 | shrinks with G |
| P4 | O_nf4 (nf4control − awqcontrol) | +3.54 | **+2.5** | [+1.0, +4.0] |
| P5 | O_vllm (vllmcontrol − awqcontrol) | +7.27 | **+5.5** | [+3.5, +7.5] |
| P6 | U (lora72bnf4 − nf4control) | (+10.00 unscoped) | **≥ +6** | loses, 0/6 better |
| P7 | U's false-detection increase removed by `contained_duplicate` | — | **< 25%** | |
| P8 | escaped_error, arm vs control, both pairs | −12 / −11 | falls by 3-8 | |
| P9 | auto-accept rate, arm vs control, both pairs | — | rises, +0.01 to +0.03 | |
| P10 | n_pred, arm vs control, both scoped pairs | equal | equal ±1 | value channel only |

**Predicted verdict: CLOSE.** P1/P2 put G above −1.50 on at least one stack.

## 3. Mechanisms the predictions assume

* **P1/P2 shrink:** the adapter's old win was escaped → flagged on rows that
  `nondim_kind` and `no_tolerance` now flag anyway. Once both sides flag those
  rows the conversion is worth 0 (r3-tallpad lesson). What survives are rows
  that become fully correct (old +7/+8) and dimension rows with a tolerance read
  where the adapter fixes a value that still escapes on the control.
* **P3:** same adapter, same targets, greedy decoding. Agreement under the old
  policy (0.25 on the full split, 0.93 scoped) is the reference. The gap should
  shrink roughly in proportion to G. A larger gap would be a stack effect on
  which rows the adapter fixes, and it is reported, not explained away.
* **P4/P5 shrink, P5 less:** NF4's overhead was split between reads and
  detections. vLLM's overhead was mostly false detections (+97 on the full
  split), which no active rule except `contained_duplicate` can touch, and that
  rule removes only 7-11% of false detections on dev.
* **P6/P7:** `false_diagnosis` puts the bulk of false detections near gold that
  paired elsewhere, or far from gold. Duplicates of a match are only 7-11% of
  them, so a read-trained adapter steering detection keeps most of its +324.
* **P9:** fully-correct rows rise in both arms and the flag rules are
  identical on both sides, so the rate can only rise from corrected unflagged
  rows. If it does not rise, the adapter's surviving gain is all flagged rows
  and is worth nothing to the product.

## 4. What would surprise, and what it would mean

* **G ≤ −1.5 on both stacks:** the flag rules and the adapter target different
  rows. Build the scoped path, and expect the pad-24 native run to come in
  smaller (G is a probable upper bound; see the design §1).
* **G passes on one stack only:** the rule fails. Report the stack effect; do
  not build.
* **P7 ≥ 50%:** the unscoped adapter's false detections are mostly duplicates.
  A detect-trained adapter gets its own brainstorm.
