# The HYBRID arm — the result, against the prediction registered before it

Measured 2026-09-11. The prediction, written before the code existed, is
`docs/plans/2026-09-09-hybrid-arm-prediction.md`. Run `r3-hybrid`: 20 documents
predicted, 0 failed, 09:11:20Z → 18:20:28Z (9 h 09 m, ~27.5 min/doc), 32B AWQ
localising on card 0 and 72B AWQ transcribing on card 1.

**One line: the hybrid loses by +40.00, and the reason it loses is the finding.
Box framing, not the reader, dominates read accuracy — by about five times.**

---

## 1. The verdict

`r3-hybrid-scoped` vs `r3-awqcontrol-scoped`, 15 documents, 311 gold values:

| | production | hybrid | delta |
|---|---|---|---|
| review cost | 133.93 | **173.93** | **+40.00** |
| ci95 | | | [26.07, 56.27], significant |
| weightings B better | | | **0 of 6**, robust |
| recall | 0.7170 | 0.7749 | +0.058 |
| missed | 88 | **70** | **−18** |
| silently wrong | 71 | **105** | **+34** |
| fully-correct values | 107 | **66** | **−41** |
| field_acc | 0.4798 | **0.2739** | **−0.206** |
| n_pred | 569 | 890 | +321 |
| false_detection | 346 | 649 | +303 |

**It fails on the product goal independently of `weights.json`.** The stated aim
is fewer missed and fewer silently-wrong values. The hybrid buys 18 fewer manual
additions by shipping **34 more wrong values with no warning**, and delivers
**41 fewer correct values** overall. No reweighting rescues that: it is worse on
one of the two goal metrics and on the count of values the reviewer can actually
keep.

---

## 2. The gates

**`n_pred == 890`: PASSED, exactly.** Detection is a pure function of the detect
model and it ran on the 32B. `missed` 70, `false_detection` 649 and recall
0.7749 also came back identical to `r3-32bawq` to the last row.

**`field_acc >= 0.40`: TRIPPED — 0.2739. The gate was wrong, not the arm.**

That gate said "a hybrid landing near 0.26 did not route reads to the 72B". The
premise is independently testable and it is false:

* If the reads had gone to the 32B, this run would be **bit-identical** to
  `r3-32bawq` — same boxes, same reader, greedy decoding, and CLAUDE.md §5
  records that identical inputs give per-document deltas of exactly 0.0. It is
  not identical: `escaped_error` 105 vs 100, `flagged_error` 70 vs 78, `correct`
  50 vs 48, `flagged_correct` 16 vs 15, cost 173.93 vs 172.73. **That is a
  measurement, not an argument.**
* Every dump records `serving_backend=hybrid`,
  `detect_model=Qwen/Qwen2.5-VL-32B-Instruct-AWQ`,
  `model_id=Qwen/Qwen2.5-VL-72B-Instruct-AWQ`.
* Card 1 held 41.7 GB of weights and rose to 66.5 GB during the run: the 72B was
  generating.

**The lesson is about gates.** That one encoded an assumption — that read
quality follows the reader — and tested it without meaning to. A gate is only as
sound as its premise, so a tripped gate has two possible causes and the arm's
own data has to decide which. Register the mechanism a gate assumes, not only
the threshold.

---

## 3. What actually happened: the boxes, not the reader

Three runs, two of which share a detector and two of which share a reader:

| | boxes from | read by | matched | fully correct | field_acc |
|---|---|---|---|---|---|
| `r3-awqcontrol` | 72B | 72B | 223 | 107 | 0.4798 |
| `r3-32bawq` | 32B | 32B | 241 | 63 | 0.2614 |
| `r3-hybrid` | 32B | **72B** | 241 | 66 | 0.2739 |

* **Hold the reader, change the boxes: −0.206.**
* **Hold the boxes, change the reader: +0.013.**
* The best reader change ever measured on fixed boxes — `read-lora-v1`, the only
  win in the campaign — was **+0.039**.

So box framing outweighs the reader's own weights by roughly 5x against the best
fine-tune and 16x against swapping 32B for 72B. **The 72B reading the 32B's
boxes recovers three rows. Three.**

**And the boxes are in the right PLACE.** `misplaced_matches` is 44 of 223
(19.7%) for production and 48 of 241 (19.9%) for the hybrid — statistically the
same. The matcher is not pairing gold with distant junk; the boxes sit on the
right callout and frame it badly enough that a far better reader still cannot
recover the value. That aggregate is what separates "over-detection crowded the
matcher" from "the boxes are badly framed", and it settles it in favour of
framing.

**A better reader on bad boxes is actively harmful.** Swapping 32B → 72B on the
same crops moved 8 rows out of `flagged_error` and 5 into `escaped_error`: the
stronger model is more *confident* on a crop it is misreading, so it removes the
warning without fixing the value. At `escaped=5` against `flag=1` that is a cost
increase, and in the product it is a wrong value shipped silently instead of one
the reviewer would have checked. Every one of the 105 silent errors sits at
confidence >= 0.8, consistent with the saturated-confidence dead end.

**The detector's weights did move the bucket they were supposed to.**
`missed_diagnosis` went contended 19 → 10 and isolated 58 → 49, with `unlocated`
unchanged at 11 — exactly the signature CLAUDE.md records for detector weights,
and exactly what nothing else in the campaign has moved. The mechanism was real.
It is simply not worth what it costs: those 18 rows arrive attached to boxes
that cannot be read.

---

## 4. Scoring the prediction

| quantity | predicted | measured | |
|---|---|---|---|
| `n_pred` | 890 exact | 890 | **exact** |
| `missed` | 70 | 70 | **exact** |
| recall | 0.7749 | 0.7749 | **exact** |
| `false_detection` | 649 | 649 | **exact** |
| flagged delta | +4 | +4 | **exact** |
| `escaped_error` | 77 | **105** | **missed by 28** |
| `field_acc` | 0.44–0.50 | **0.2739** | **missed** |
| cost | 164.60 (band 160–170) | **173.93** | **outside the band** |

Cost decomposition, x15 documents: `missed −180` (predicted −180), `false +606`
(predicted +606), `flagged +4` (predicted +4), `escaped` **+170** (predicted
+30). **Three of four terms exact; the whole error is the silent-error term.**

The verdict and its robustness were predicted correctly — a loss under all six
weightings, `b_better_fraction` 0.0, robust, and the measured per-weighting
deltas [40.0, 34.8, 43.7, 80.7, 29.0, 21.4] are uniformly worse than the
predicted [30.7, 19.9, 36.3, 71.3, 14.1, 17.7]. The magnitude was
under-predicted everywhere for one reason: **the prediction assumed the silent
error rate follows the reader (31.8% per matched row) when it follows the
BOXES (43.6% measured, against the 32B's 41.5%).** That assumption is the same
one the field_acc gate encoded, and the arm falsified it twice.

---

## 5. What this closes, and what it opens

**CLOSED: the 32B as a detector, and detector-swapping generally.** The 32B's
recall advantage is recall of *boxes*, not of values. It finds 18 more gold rows
and delivers 41 fewer usable ones. Do not re-open it, and do not read
"recall 0.7749 vs 0.7170" as a quality improvement anywhere it is quoted.

**CLOSED: the hope that re-derived weights rescue it.** Measured 0 of 6
weightings, and the product-goal metrics fail regardless of price tags.

**OPEN, and now the largest identified lever on reading:** box framing. The
production 72B detector already produces the best boxes measured, so this is not
a "swap the detector" idea — `detectbox` (prompting for tighter boxes) was
measured and lost, and that remains closed. What is newly quantified is the
*size of the prize*: if boxes could be improved as much as they were degraded
here, that is worth ~0.2 field accuracy — five times the only win the campaign
has produced. Nothing in this result says how to get it.

**A cheaper question first.** The per-field failure profile of a good reader on
bad boxes is recorded here (`field_failure_signatures`: 44 of 175 wrong rows
have all four fields wrong at once, 25% — the same share as production's 31 of
116). Whether that share is what a clipped box produces, or whether it is
scale-free, is answerable from existing dumps with a new aggregate rather than a
GPU run.

**Incidental, and worth knowing:** `score` reported 80 gold documents without
dumps, i.e. **gold exists for the whole 99-document corpus**, not only the dev
split. The adapter arms lose significance at 15 documents (`loraread` ci95
[−7.67, 0.13]); a larger scored set exists for any arm that does not touch the
read adapter, since only the train split is contaminated for those.
