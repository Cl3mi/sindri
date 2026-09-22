# The `theoretical` parser fix is MEASURED and REVERTED — bound exactly zero

Measured 2026-09-22 on the dev split under the shipped config. Prediction and
decision rule registered beforehand in `874771c`'s commit message and in
`docs/plans/2026-09-21-session-handoff.md` §2. Reverted at `89e0375`.

**One line:** the edit did precisely what it claimed and changed precisely the
rows it targeted, and it is worth **nothing** — which is the finding, because it
means those rows carry a *second* fault underneath the one that was removed.

---

## 1. The numbers

`score --reparse-check` over `r3-cropctx`, dev split, `--max-pages 1 --dpi 300
--exclude-clamped`. Both runs re-score identically (131.87 / recall 0.717 /
`escaped_rate` 0.206), as they must — `--reparse-check` does not alter the
written report, and `score` reads parsed fields off the dumps rather than
re-parsing them. The whole measurement is in the counts.

| | `n_pairs` | `identical` | `would_fix` | `would_break` | `still_wrong` | `still_correct` |
|---|---|---|---|---|---|---|
| parser as it was (gate) | 223 | **223** | 0 | 0 | 105 | 118 |
| with `874771c` | 223 | **214** | **0** | **0** | 105 | 118 |

**The baseline gate held exactly**: `identical == n_pairs` with an unmodified
parser, so every stored field is reproducible from `raw_text` and the offline
reconstruction is sound. Without that, the second row means nothing.

**`identical` fell by exactly 9** — the count of `theoretical` rows on this
split, term for term. So the edit reached every row it was aimed at and no other
row in the corpus, which is as clean a single-variable result as the crop arm's
bit-identical detection.

**`would_fix - would_break = 0 - 0 = 0.**

---

## 2. Why zero, and why that is the interesting part

A row counts as fixed only when `char_type` matches gold **and** `nominal`,
`upper_tol` and `lower_tol` all match. The handoff bounded the ceiling at 4 of
the 9 before the run — 2 rows have gold `Flatness`, 3 have no mappable gold type
— and the measured answer is that none of those 4 flipped either. Their values
are wrong too.

**So the standing claim is refuted.** CLAUDE.md said of this bucket:

> **It is a labelling collision, not a read failure.**

It is a labelling collision **on top of** a second fault (§5 places that
fault in detection rather than the read). The collision was real
— `char_type = THEORETICAL` against a gold vocabulary with no such value made
the rows unscoreable *by construction*, exactly as diagnosed — but it was
**masking** a second, independent fault rather than substituting for one.
Removing it exposes the rows to scoring and they are still wrong.

This is the same shape as `r3-tallpad`'s lesson from the other direction. There,
the fix landed on rows the reviewer was already checking, so it saved nothing.
Here, the fix removes one of *two* reasons a row is wrong, so it saves nothing.
**A correctness fix pays only when it is the LAST fault on the row**, and
neither the bucket's read accuracy (0.0000) nor its escaped-error share (8 of 9)
could distinguish "wrong for one reason" from "wrong for two".

`reparse.py` is what made that distinguishable at all, in CPU seconds, without a
GPU arm. This is the second time it has closed a parser question for free.

---

## 3. The decision, and the one thing the bound cannot see

The rule was registered in the commit itself — *"Revert this single commit if
the bound is not positive"* — and the bound is not positive. Reverted.

**But the bound's premise is not complete, and this is stated rather than
buried.** `874771c` carried a second effect on a path `reparse.py` cannot
reach:

* `reparse.py` re-parses **predictions** only.
* `targets.render_target` verifies through `parse_value` under
  `hint="theoretical"` (`_verified`). Before the change that always returned
  `THEORETICAL`, which equals no gold `char_type`, so **every boxed gold row
  raised `UnrenderableRow` and was dropped from the TRAINING target set.**

That effect is real, and the revert gives it back up. It was judged not worth
holding an unmeasured parser change for, because the LoRA route is blocked three
failures deep and `read-lora-v1`'s +0.039 is already smaller than the free crop
win. **If a future adapter needs boxed callouts in its targets, cherry-pick
`874771c` back** — it stays in history for that, and the training-target claim
should be measured on its own terms (count of `UnrenderableRow("not_round_tripping")`
rows on a train-split build, with and without), not inferred from this bound.

**The generalisable rule:** `would_fix - would_break` prices a parser change on
the PREDICTION path only. A parser edit also moves `app/train/targets.py`, and
those two paths need two measurements. Do not read a zero bound as "this change
does nothing".

---

## 4. What this closes and what it leaves

**Closed.** The `theoretical` bucket is not reachable by a char_type fix. Do not
re-propose one, and do not read `read_accuracy_by_kind`'s `theoretical: 0.0000`
as a scoring artefact. It is a genuine failure that a scoring artefact was
sitting in front of, and §5 places it in detection rather than the read.

**Answered the same day — see §5.** The open question was which fields the 9
rows get wrong. The answer is: nearly all of them.

---

## 5. Which fields, per kind — the aggregate that would have priced this first

Built after the arm (`5262672`): `read_accuracy_by_kind` now carries
`wrong_fields`, the signature histogram of each kind's wrong rows. Counts of
field names only. Shipped config, dev, scoped:

| kind | n | wrong | char_type ONLY | all four wrong | escaped |
|---|---|---|---|---|---|
| `dimension` | 189 | 74 | 3 | 16 | 38 |
| `gdt` | 17 | 14 | **8** | 1 | 11 |
| `theoretical` | 9 | 9 | **0** | **7** | 8 |
| `surface` | 4 | 4 | 0 | 3 | 4 |
| `note` | 4 | 4 | 0 | 1 | 3 |

It reconciles both ways: per kind to the kind's wrong rows, and across kinds
to the global `field_failure_signatures` (char_type-only 3 + 8 = 11, all-four
16 + 1 + 7 + 3 + 1 = 28). The regenerated digest differs from the committed one
by the new keys only.

**`theoretical` is not a read problem at all.** No row is wrong in char_type
alone, so no char_type fix could have freed one. That is the zero bound,
explained completely. **Seven of nine are wrong in all four fields**, and
`nominal` is wrong in all nine. A row wrong in every field is not a boxed
dimension read slightly wrong. It looks like a prediction paired with the
wrong gold row, or a callout that is not a dimension. The detector agrees:
**54 of 63 `theoretical` predictions (86%) are false detections**, against 55%
for `dimension`. So this bucket belongs with detection quality, and the parser
was never going to reach it. That is a reading of aggregates rather than a
measurement of mechanism, and it is stated as such.

**`gdt` is the first bucket where a single-field fix passes the last-fault
test.** 8 of its 14 wrong rows are wrong in `char_type` and nothing else, so
fixing char_type alone would make each one fully correct. It has 11 escaped
errors across 14 wrong rows, so at most 3 wrong rows are flagged, and **at
least 5 of those 8 are escaped**. That is the bucket that pays. Priced at
today's weights, that is at least 5 × (5 − 0) ÷ 15 = **−1.67** review cost, up
to 8 × 5 ÷ 15 = −2.67 if all 8 are escaped. That is the size of the crop win
(−2.07), with no GPU.

**What is not known is which side of that `char_type` is wrong**, and it
decides the fix. `char_type_confusion` shows `unmapped(none) -> Flatness` 7
times. That means gold labels with no word the synonym map recognises, set
against a predicted `Flatness`. And `parser._gdt_type` **defaults to
`Flatness`** when it recognises no GD&T symbol. So each of those rows is one of:

* gold's label is a GD&T type the map does not know. Then this is the
  synonym-map territory §3 closed, reopened for a new reason: the closure was
  "adding words moved nothing because whole-label matching hid them", and that
  matching is now fixed.
* the parser's `Flatness` is its default, not a reading. Then the fault is
  `_gdt_type`, which is a parser change that `--reparse-check` prices in CPU
  seconds.

**Telling those apart means looking at gold's labels, which are client text.**
That is the operator's job, not an agent's. An agent-safe step first:
`char_type_confusion` restricted to `gdt`'s char_type-only rows. It is the
same closed vocabulary and says how many of the 8 are `unmapped(none)`.

---

## 6. Provenance

* Run: `r3-cropctx`, dev split, scope policy `--max-pages 1 --dpi 300
  --exclude-clamped`, 15 of 20 documents.
* Reports written: `reparse-probe`, `reparse-probe-theoretical` (identical by
  construction; the counts above are the result, not the reports).
* Suite at `d1cca01` (change in place): **889 passed, 2 skipped**. After the
  revert: **883 passed, 2 skipped** — the 6 tests `874771c` added, removed with
  it. The 6 `TesseractNotFoundError` failures recorded in the handoff were a
  missing binary on the writing machine, not code; `tesseract 5.5.3` is present
  here.
* `_prompt_sha256` `aa7659f1929184ea`, unchanged on both sides — the parser does
  not touch prompts, recorded so the next session does not re-check.
* Guard verified before any corpus command: `32 passed, 0 failed`.
