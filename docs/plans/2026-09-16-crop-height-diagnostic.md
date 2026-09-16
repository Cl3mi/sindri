# Read accuracy against crop height — one family refuted, a better one opened

Measured 2026-09-16 from `read_accuracy_by_crop_height`, built the same day for
exactly this decision. **No GPU time was spent.** Dev split, 223 matched rows,
`r3-awqcontrol` unless stated.

**One line: the crop-RESOLUTION family (`_MIN_CROP_H`, `_MAX_UPSCALE`) is
refuted for free — it reaches 4% of rows and the effect runs the wrong way — and
the same table shows where the remaining read quality actually is: TALL boxes,
56% of matched rows, reading at 0.40.**

---

## 1. The refutation

| crop height | n | `field_acc` |
|---|---|---|
| `<28` px (below the patch floor) | 2 | 0.0 |
| `28-40` px (below `_MIN_CROP_H`, upscaled) | 7 | 0.7143 |
| `40-80` px | 88 | 0.5909 |
| **`>=80` px** | **126** | **0.3968** |

Two independent reasons the resolution knobs are dead:

1. **Reach.** Only **9 of 223 rows (4.0%)** sit below `_MIN_CROP_H` at all.
   Raising the floor cannot touch the other 96%, and 7 of those 9 already read
   at 0.71 — better than any other bucket.
2. **Direction.** Accuracy **falls** as crops get taller: 0.71 → 0.59 → 0.40.
   "More pixels helps the reader" is not merely small here, it is backwards.

`_MIN_CROP_H` and `_MAX_UPSCALE` are therefore **closed**, and so is the premise
that would have justified them. This is the outcome the diagnostic was built to
make cheap: `CLAUDE.md` §2 requires a bucket that predicts the move before a
knob is proposed, and the bucket said no.

*(The `<28` row is 2 samples and means nothing on its own — Qwen refuses crops
below its patch factor and `_prep_crop` upscales to clear it, so this bucket
should be near-empty and is.)*

---

## 2. What the same table found instead

**126 of 223 matched rows (56.5%) are boxes ≥80 px tall, and they read at
0.3968 against 0.5909 for the 40-80 px band.** That is the single largest
identified read-quality deficit on the corpus, and it is a property of the BOX,
not of the model.

At 300 dpi, 80 px is 6.8 mm. A single-line callout is 3-4 mm, so `>=80` means a
box spanning **more than one line** — a nominal with stacked tolerances, a
multi-cell GD&T frame, or a box that swallowed a neighbour.

### The crop-pad win was almost entirely in that bucket

| crop height | pad 6 | pad 24 | pad 48 | delta |
|---|---|---|---|---|
| `40-80` | 0.5909 | 0.5977 | 0.6023 | **+0.011** |
| **`>=80`** | 0.3968 | 0.4724 | 0.4921 | **+0.095** |

**This says which way tall boxes are wrong.** If they were wrong by being
over-inclusive — swallowing a neighbouring callout — then adding MORE context
would make them worse. It makes them better, monotonically, and it is still
improving at pad 48. **Tall boxes are being CLIPPED, not over-filled.**

It also explains the dose response cleanly. Pad 48 keeps helping the tall bucket
(0.4921) while the 40-80 band has flattened, but the global cost rises anyway
because the extra confidence converts flagged errors into silent ones. **The
optimum is different for tall boxes than for the rest, and a single global pad
has to split the difference.**

---

## 3. The lead this opens, and what it still needs

**A height-dependent pad.** Pad proportional to box height, or simply a larger
pad above a height threshold. The evidence for it is unusually direct:

* the bucket it targets is 56% of matched rows at the worst accuracy on the page;
* the mechanism is measured, not assumed — padding helps tall boxes and is still
  helping at 48;
* the reason a global pad cannot capture it is measured too — 40-80 flattened by
  24 while `>=80` had not.

**Predicted bucket, if it is registered:** `>=80` accuracy rises toward its pad-48
value (0.49+) while `40-80` stays at its pad-24 value (~0.598), and
`escaped_rate` does NOT rise, because the rows gaining context are the ones that
were clipped rather than the ones at risk of pulling in a neighbour.

**Damage counter — and this time it must be one that moves.** §4 of the working
notes now requires checking that the counter responded to a previous dose.
`misplaced_matches` was flat at 44 → 42 → 42 across all three pads, so it is
disqualified. **`escaped_error` is the counter**: it moved 71 → 64 → 67 across
the same doses, which is exactly the responsiveness the rule asks for.

**What is still missing before an arm can be registered:** nothing about the
mechanism, but the threshold is a guess. 80 px is the bucket boundary, not a
measured knee. A finer height histogram — or the same table with 60/100/140
boundaries — would place it, and that is another GPU-free re-score.

---

## 4. What this does NOT license

* **Not the merge knobs.** Tall boxes come partly from `merge_adjacent`, and
  reducing merging was measured at two doses and lost (6.50 and 8.75 false
  detections per recovered miss). The tension is now visible — **merging buys
  detection and costs reading** — but the cost verdict on that trade stands.
* **Not `readcenter` again.** Naming the centre callout in the prompt was
  measured and lost, and its target bucket was provably untouched. This is a
  geometry lever, not a prompt one.
* **Not `tighten_to_ink`'s `pad=3`.** It is the same CONTEXT lever as
  `_CROP_PAD`, whose curve is already peaked globally. It would only make sense
  as part of the height-dependent proposal, not beside it.
