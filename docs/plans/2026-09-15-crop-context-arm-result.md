# The CROP-CONTEXT arm — the result, and the dose that follows

Measured 2026-09-15. Prediction registered before the run:
`docs/plans/2026-09-14-crop-context-arm-prediction.md`. Run `r3-cropctx`,
`SINDRI_CROP_PAD` 6 -> 24 px, 20 documents predicted, judged against
`r3-awqcontrol-scoped` (133.93).

**One line: it WINS — 131.87, -2.07, better under 6 of 6 weightings — and
`field_acc` +0.049 is the largest read-quality move the campaign has produced,
from a constant rather than a model.**

---

## 1. The gates

**Identity gate `n_pred == 569`: PASSED, exactly.** And more than passed —
detection came back **bit-identical**: `missed` 88, `false_detection` 346,
`micro_recall` 0.7170, `pred_kinds`, `matched_by_pred_kind` and
`missed_diagnosis` (contended 19 / isolated 58 / unlocated 11) all match
`r3-awqcontrol` term for term. Even `loraread`'s gate moved
`false_detection` by one; this one moved nothing. **Everything that changed is
read stage**, which makes this the cleanest single-variable arm in the campaign.

**Target bucket: PASSED, but narrowly.**

| bucket | production | cropctx | |
|---|---|---|---|
| `dropped_tolerances.rows` | 48 | **45** | -3 |
| `missing:lower_tol` | 45 | **43** | -2 |
| `missing:upper_tol` | 28 | **24** | -4 |

All three fell, so the registered hypothesis is not refuted. But the ceiling was
30 tolerance-only-wrong rows and only **4** of them were recovered
(`fields:lower_tol` + `upper_tol+lower_tol` + `upper_tol`: 30 -> 26).

**Damage counter: did NOT fire.** `misplaced_matches` 44 -> **42**, and
`misread.misplaced` 35 -> 33. More context did not pull neighbouring callouts
into the crop at this dose. That was the registered risk and it is absent, which
is what licenses a larger dose (§4).

---

## 2. The result

| | production | cropctx | delta |
|---|---|---|---|
| review cost | 133.93 | **131.87** | **-2.07** |
| weightings B better | | | **6 of 6**, robust |
| `field_acc` | 0.4798 | **0.5291** | **+0.049** |
| fully-correct values | 107 | **118** | **+11** |
| silently wrong | 71 | **64** | **-7** |
| `escaped_rate` | 0.2283 | **0.2058** | -0.023 |
| wrong rows | 116 | **105** | -11 |
| missed / n_pred / false | 88 / 569 / 346 | 88 / 569 / 346 | **0** |

Cost reconciles exactly: `5(-7) + 1(+4) = -31`, / 15 = **-2.07**. **The whole win
is 7 silent errors converted for 4 extra flags** — structurally the same shape as
`read-lora-v1`'s win (23 converted for 15 flags), at a fraction of the cost.

Per-weighting deltas, computed from the taxonomy: `[-2.07, -3.47, -1.60, -1.80,
-3.47, -0.67]`. Negative under all six, so `b_better_fraction` 1.0 and robust.
**The comparison file must still be generated** — `rescore_onepage.sh` had no
`compare_pair` for this arm on its first score, which is the gap the 32B sat in
for a session. Added 2026-09-15; re-run the batch for `ci95`.

**+0.049 field accuracy beats `read-lora-v1`'s +0.039**, which took a trained
adapter, an NF4 base costing +6.35 to serve, and a deployment route still
blocked three failures deep. This is `_CROP_PAD = 24`.

---

## 3. What the result actually says

**The registered mechanism is real but is the smaller half.** The hypothesis was
"a tight crop clips the tolerance line stacked under the nominal, and padding
reaches it". Of the 11 rows that became fully correct, about **4** are
tolerance-only rows; the other **7** were wrong in other fields. `wrong:nominal`
58 -> 55, `wrong:char_type` 57 -> 54, `wrong:upper_tol` 26 -> 19,
`wrong:lower_tol` 30 -> 24, all-four-wrong 31 -> 28.

So the effect is broader than tolerance recovery: **more context makes the
reader better across every field**, and tolerances are simply where it was most
visible in advance. Stating it the other way round, which is the useful form:
the crop was starving the reader of context generally, and 18 extra pixels —
1.5 mm at 300 dpi — bought a fifth of the campaign's total remaining read
errors.

**It corroborates the hybrid's finding from the opposite direction.**
`r3-hybrid` showed that degrading the boxes costs -0.206 field accuracy while
changing the reader buys +0.013. This shows that improving what the reader sees
*of the same boxes* buys +0.049. Both say the same thing: **the input to the
read is where the remaining quality is, not the model doing the reading.**

**And it is shippable today.** It is a constant. No new checkpoint, no serving
stack, no quantisation, no adapter, no deployment risk — and detection is
bit-identical, so nothing downstream of it changes either.

---

## 4. The next dose, registered BEFORE it runs

`SINDRI_CROP_PAD` 24 -> **48** px (4 mm at 300 dpi). Stage `cropctx48`, run
`r3-cropctx48`, judged against `r3-awqcontrol-scoped`, one card.

**Why a second dose rather than shipping 24 and stopping.** The registered
damage did not appear — `misplaced_matches` went DOWN — so the mechanism has not
yet met its limit, and 4 of 30 recoverable tolerance rows says the dose is
nowhere near saturating. The merge knobs were measured at two doses for exactly
this reason, and that is what showed no intermediate setting could win. Here the
two points so far (6, 24) give a direction; a third says whether the curve is
monotone or peaked.

**Identity gate: `n_pred == 569` again.** The crop is derived after detection, so
this must hold at every dose. If it moves, something other than the crop changed.

**Prediction.** If context is the mechanism, `field_acc` rises again with
diminishing returns — call it **0.54-0.56** — while `misplaced_matches` begins to
rise as neighbouring callouts enter the crop. If 24 was already the peak,
`field_acc` falls back toward 0.50 and `misplaced_matches` rises sharply.
Central estimate **-1.0 further** (cost ~130.9), band [-3.5, +2.0].

**Decision rule, registered now, so the answer is not chosen after the fact:**

* `field_acc` up AND `misplaced_matches` <= 46 -> take 48, and test 96 next.
* `field_acc` up but `misplaced_matches` > 46 -> the mechanism has met its
  limit; 24 ships and the pad family is closed.
* `field_acc` down -> 24 ships and the pad family is closed.

**Ship 24 regardless of how 48 turns out.** It is measured, robust under 6 of 6
weightings, and free; holding it back pending a second dose would be trading a
banked win for a maybe.

---

## 5. What this does NOT license

* **Not a return to detection knobs.** Detection was bit-identical here. Tile
  size, the merge knobs and render resolution remain closed.
* **Not `detectbox`.** Prompting the detector for tighter boxes was measured and
  lost. This arm moved the crop taken FROM the box, not the box.
* **Not the other crop knobs yet.** `_MIN_CROP_H` (40) and `_MAX_UPSCALE` (3.0)
  are untested, and so is `boxes.tighten_to_ink`'s own `pad=3`. Each is a
  separate variable and none should ride along with a pad dose.
