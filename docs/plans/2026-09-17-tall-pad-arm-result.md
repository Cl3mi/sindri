# The HEIGHT-DEPENDENT PAD arm — mechanism confirmed, trade refused

Measured 2026-09-17. Prediction registered before the run:
`docs/plans/2026-09-16-tall-pad-arm-prediction.md`. Run `r3-tallpad`
(`SINDRI_CROP_PAD_TALL=48`, threshold 120 px), 20 documents, judged against
`r3-cropctx` (131.87).

**One line: the most precisely targeted arm in the campaign — every band except
the one aimed at came back bit-identical — and it LOSES, 132.53 against 131.87,
worse under 0 of 6 weightings. The prediction said this would happen and named
why.**

---

## 1. The gates, in the registered order

**Gate 1 — identity: PASSED.** `n_pred` 569, `missed` 88, `false_detection` 346,
identical to the control. The crop is downstream of detection at every dose and
stayed that way.

**Gate 2 — target bucket: PASSED, surgically.**

| band | control | tallpad | |
|---|---|---|---|
| `<28` | 2 / 0.500 | 2 / 0.500 | identical |
| `28-40` | 7 / 0.714 | 7 / 0.714 | identical |
| `40-80` | 87 / 0.598 | 87 / 0.598 | identical |
| `80-120` | 33 / 0.273 | 33 / 0.273 | identical |
| **`120-200`** | **65 / 0.523** | **65 / 0.585** | **+0.062** |
| `>=200` | 29 / 0.586 | 29 / 0.586 | identical |

**Only the targeted band moved, and every other band is bit-identical.** The
registered prediction was 0.560–0.580; measured 0.585, marginally above it. No
arm in this campaign has ever hit its target this cleanly.

**Gate 3 — `escaped_rate` not up: FAILED.** 0.2058 → **0.2122**
(`escaped_error` 64 → 66). `field_acc` did rise, 0.5291 → **0.5471**.

**Gate 4 — cost: 132.53, +0.667.** `ci95 [0.0, 1.533]`, not significant, and
**better under 0 of 6 weightings**, robust. No `compare_runs` warnings.

---

## 2. Why a real read improvement cost money

The taxonomy moved in exactly two flows:

| | control | tallpad | |
|---|---|---|---|
| `escaped_error` | 64 | 66 | **+2** |
| `correct` | 73 | 71 | −2 |
| `flagged_correct` | 45 | 51 | **+6** |
| `flagged_error` | 41 | 35 | **−6** |
| **fully correct** | **118** | **122** | **+4** |

* **6 rows moved `flagged_error` → `flagged_correct`** — reads genuinely fixed,
  still flagged.
* **2 rows moved `correct` → `escaped_error`** — reads broken, and unflagged.

Cost: `5(+2) + 1(−6) + 1(+6) = +10`, ÷15 = **+0.667**.

**The structural point, and it generalises well beyond this arm:**
`flagged_error` and `flagged_correct` BOTH cost 1. **Fixing a read on a row the
reviewer was already going to check saves nothing.** Only converting an
*escaped* error pays — which is precisely what `read-lora-v1`'s win was made of
(23 silent errors converted for 15 extra flags).

So the arm fixed six rows the reviewer would have caught anyway, and broke two
the reviewer would not. **More context fixed what was already flagged and broke
what was not.**

That is the risk the prediction registered verbatim: *"a global pad 48 raised
`escaped_error` by 3 while raising accuracy … this arm gives 48 to only 64 of
223 rows, so the same effect should be roughly a third the size, but it is the
same effect and it may eat the gain."* Predicted ~1 extra silent error; measured
2. And the registered conclusion applies as written: **the mechanism is
confirmed and the trade is not worth taking at today's weights**, which puts
`weights.json` at the centre for the third time.

**Nothing ships.** The configuration stays pad 24 flat.

---

## 3. The band composition — and my hypothesis was wrong

Read from the same digest, per band (control):

| band | n | composition |
|---|---|---|
| `40-80` | 87 | dimension 80, gdt 5, surface 1, theoretical 1 — **92% dimension** |
| **`80-120`** | 33 | **dimension 13, gdt 8, theoretical 7, surface 3, note 2** |
| `120-200` | 65 | dimension 59, gdt 3, note 2, theoretical 1 — **91% dimension** |
| `>=200` | 29 | dimension 28, gdt 1 — **97% dimension** |

**`>=200` is NOT gdt/note.** The registered hypothesis — that the right arm of
the U recovers because those boxes go through `_GDT_PROMPT` / `_NOTES_PROMPT`
rather than the one-line dimension prompt — is **refuted**. They are 28 plain
dimension boxes that read at 0.586 and are completely context-insensitive.

**`80-120` is the reframing worth having.** It is the only mixed band on the
page: **61% non-dimension** (8 gdt + 7 theoretical + 3 surface + 2 note of 33),
against 91–97% dimension everywhere else. And it is the worst band at 0.242.

**So the bottom of the U is a KIND effect wearing a height costume.** 80-120 px
is simply the size of a GD&T frame or a surface-finish symbol, and those read
badly for reasons that have nothing to do with how tall the crop is — which is
exactly why padding could not touch it.

---

## 4. What this opens

**Field accuracy BY KIND is the diagnostic nobody has.** `matched_by_pred_kind`
gives counts and `read_accuracy_by_crop_height` now gives composition, but no
aggregate says how well each KIND reads. The evidence that it matters is now
direct: the worst band on the page is the one where non-dimension kinds
concentrate.

It is GPU-free — `pred_kind` is already on every matched pair — and it is the
same shape of question that the crop-height table answered for free two days
ago, including the possibility of closing a direction at no cost.

**What this does NOT license:**

* **Not more pad doses.** Three global doses and one height-dependent dose have
  now mapped the curve. The lever is understood and it is spent.
* **Not the `>=200` band.** 28 dimension boxes reading at 0.586, insensitive to
  every pad tried. Nothing points at a mechanism there.
* **Not a flagging change to rescue this arm.** Re-tuning `review.LOW_CONF` so
  the six fixed rows stop being flagged would be tuning the metric to fit the
  treatment, and the 0.6 → 0.8 move was already measured on its own evidence.
