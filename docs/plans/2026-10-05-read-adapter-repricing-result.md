# Read-adapter re-pricing — result

Run 2026-10-05. DERIVED: `score --reapply-policy`, scoped dev (15 documents),
crop pad 6. Registered: `2026-10-05-read-adapter-repricing-prediction.md`
(`e355d1f`), before any score ran. Plan: `2026-10-05-read-adapter-repricing-plan.md`.

## 1. Verdict: CLOSE — and the adapter is now HARMFUL, not merely small

Under the shipped policy `read-lora-v1` **loses on both stacks**: **+3.33**
(NF4, ci95 [+0.87, +6.33]) and **+4.27** (vLLM, ci95 [+0.73, +8.07]), both
significant, better under **0 of 6** weightings, auto-accept precision
**0.750 → 0.660** and **0.739 → 0.641**. Of the four binding conditions only
"rate rising strictly" passed (+0.0064 / +0.0032). Do not build a serving path
for this adapter, and do not run `mergedcontrol` / `loramerged`.

Under the old policy the same dumps gave −3.40 and −2.47. **The sign flipped
because of the policy, not the adapter.**

## 2. The mechanism: the adapter turns missing tolerances into wrong ones, and `no_tolerance` can only see the first

| scoped dev, reapplied | nf4control | loraread | vllmcontrol | vllmlora |
|---|---|---|---|---|
| correct | 66 | 68 | 65 | 66 |
| flagged_error | 97 | **77** | 94 | **71** |
| flagged_correct | 42 | 47 | 42 | 49 |
| **escaped_error** | 22 | **35** | 23 | **37** |
| missing:lower_tol | 54 | 24 | 48 | 19 |
| missing:upper_tol | 36 | 19 | 31 | 15 |
| wrong:lower_tol | 28 | 51 | 31 | 51 |
| wrong:upper_tol | 22 | 34 | 27 | 38 |
| spurious:upper_tol | 12 | 27 | 9 | 26 |
| wrong in `upper_tol+lower_tol` only | 10 | 21 | 7 | 17 |
| dropped_tolerances rows | 57 | 24 | 50 | 19 |

The adapter does what CLAUDE.md §2 already recorded: it learned to emit
tolerance-SHAPED output, because `render_target` renders an explicit `+x -y`
whenever gold has one. That cut `missing:*_tol` by about 47 rows and raised
`wrong:*_tol` + `spurious:*_tol` by about 50.

**Under the old policy that trade was nearly free.** A dimension with a missing
tolerance was not flagged, so "missing → wrong" moved a row from escaped to
escaped, and the adapter's real fixes paid on top. **Under the shipped policy it
is the worst trade available.** `no_tolerance` flags a dimension with no
tolerance read, so the control's missing-tolerance rows are caught for 1. The
adapter replaces them with a plausible wrong tolerance, the flag no longer
fires, and they ship for 5. `flagged_error` −20/−23 and `escaped_error`
+13/+14 are that conversion.

Cost reconciles exactly (missed and false detections unchanged on NF4; vLLM
misses one unlocated row through the value channel):

    NF4 : 5(+13) + 1(−20 + 5)          = +50  ÷15 = +3.33
    vLLM: 5(+14) + 1(−23 + 7) + 10(+1) = +64  ÷15 = +4.27

**The general lesson: a gold-free flag rule and a fine-tune that removes the
rule's trigger are not independent.** A rule that keys on the ABSENCE of a
field is defeated by any model change that fills the field, right or wrong.
Every future read-stage arm must be priced under the active rules, never against
an old-policy number.

## 3. The five quantities

| quantity | pair | old policy (scoped) | reapplied | ci95 | better | warnings |
|---|---|---|---|---|---|---|
| G_nf4 | loraread − nf4control | −3.40 | **+3.33** | [+0.87, +6.33] | 0/6 | none |
| G_vllm | vllmlora − vllmcontrol | −2.47 | **+4.27** | [+0.73, +8.07] | 0/6 | none |
| O_nf4 | nf4control − awqcontrol | +3.54 | **+0.20** | [−7.40, +7.13] | 2/6 | base model differs (expected) |
| O_vllm | vllmcontrol − awqcontrol | +7.27 | **+6.07** | [−1.27, +13.20] | 0/6 | none |
| U | lora72bnf4 − nf4control | (unscoped only) | **+17.20** | [+7.87, +26.67] | 0/6 | none |

Reapplied absolute costs: awqcontrol 120.93, nf4control 121.13, loraread
124.47, vllmcontrol 127.00, vllmlora 131.27, lora72bnf4 138.33 (plain-scored
145.20). For reference, r4-control is 118.73 at pad 24.

## 4. Predictions vs measured

| # | predicted | band | measured | |
|---|---|---|---|---|
| P1 G_nf4 | −1.0 | [−1.8, −0.3] | +3.33 | OUT |
| P2 G_vllm | −0.8 | [−1.6, −0.2] | +4.27 | OUT |
| P3 \|gap\| | ≤ 0.6 | | 0.93 | OUT |
| P4 O_nf4 | +2.5 | [+1.0, +4.0] | +0.20 | OUT |
| P5 O_vllm | +5.5 | [+3.5, +7.5] | +6.07 | IN |
| P6 U | ≥ +6, 0/6 | | +17.20, 0/6 | IN |
| P7 dup-absorbed share | < 25% | | −0.5% (−1 of +182) | IN |
| P8 escaped, arm − control | falls 3-8 | | +13 / +14 | OUT |
| P9 auto-accept rate | +0.01..+0.03 | | +0.0064 / +0.0032 | OUT |
| P10 n_pred | equal ±1 | | 0 / −1 | IN |

* **P1, P2, P8 refute the registered mechanism** (prediction §3, "the shrink").
  It assumed the flag rules and the adapter reach the SAME rows, so the
  adapter's conversions would be neutralised. They reach the same rows
  adversarially instead: the adapter removes the trigger `no_tolerance` fires
  on. The predicted verdict (CLOSE) held, for a different and stronger reason
  than the one registered.
* **P3:** the gap stayed at exactly 0.93. It did not shrink because G did not
  shrink; it changed sign.
* **P4 refutes "NF4's overhead shrinks but stays".** It nearly vanished:
  +0.20, 2/6, ci95 spanning zero. Most of NF4's old +3.54 was rows that are now
  flagged on both sides. NF4 serving is roughly cost-neutral against AWQ under
  the shipped policy, but still ~2.3× the wall-clock, and with nothing to serve
  on it, the finding has no use today.
* **P9:** the rate rose only through the +2 / +1 fully-correct rows, as the
  mechanism said; it was simply too small.

## 5. What this does and does not license

* **CLOSED:** deploying `read-lora-v1` by any route (NF4, vLLM, merged).
  Every route serves an adapter that is worth +3.3 to +4.3 under the shipped
  policy before any serving overhead.
* **Caveats, which do NOT reopen it:** DERIVED, dev only, pad 6. Pad 24 makes
  the control read tolerances only slightly better (`dropped_tolerances` 45 in
  r4-control at pad 24 against 48 for the same AWQ detections at pad 6), so the
  trigger the adapter removes is still there at pad 24, and the adapter's fault is in its TARGETS and survives any pad.
  A native run would cost two GPU runs to re-measure a sign that is
  significant on two independent stacks.
* **NOT closed:** a re-trained adapter. Fixing `render_target` so a target never
  renders a tolerance the drawing does not print (CLAUDE.md §2, the "largest
  identified read-stage fault") is the precondition. Any new adapter must be
  priced under the active rules, with `escaped_error` as its damage counter,
  which moved +13/+14 here and is clearly responsive.
* **The merge route's scoping loss stands whatever the adapter.** A merged
  checkpoint runs detection through the adapter; see §6.

## 6. U and the detect-trained adapter

The unscoped arm (what a merged checkpoint does) costs **+17.20** under the
shipped policy, against +10.00 unscoped under the old one. It finds more:
isolated misses 60 → 39, missed 84 → 64, recall 0.730 → 0.794. But it adds
**+183 false detections**, and `contained_duplicate` absorbs **none** of the
increase (it removed 5 of the arm's false detections and 6 of the control's).
Its escaped errors also rise 22 → 41.

So P7 is far below the 50% that would have justified a brainstorm: the extra
detections are not duplicates of matches, consistent with `false_diagnosis`
(near-gold 233, far-numeric 258 of 547). **A detect-trained adapter inherits
this evidence against it** as far as it goes: the one adapter that steered
detection bought 21 isolated misses for 183 false detections, at 8.7 per
recovered miss against a break-even of 5.0. That does not rule out an adapter
trained ON detection targets, which has never been built. It does mean any such
proposal must carry a mechanism for precision, not just recall.
