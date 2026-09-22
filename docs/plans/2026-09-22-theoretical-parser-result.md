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

It is a labelling collision **on top of** a read failure. The collision was real
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
as a scoring artefact — it is a genuine read failure that a scoring artefact was
sitting in front of.

**Left open**, and now the better-shaped question: the 9 rows are wrong in
`nominal` and/or the tolerances. `subtype` already records that the callout was
boxed and the box means *untoleranced*, so a row whose gold carries tolerances is
a different kind of disagreement from one whose nominal is misread. Splitting
those two would say whether this bucket belongs with the read-quality work or is
a gold-shape mismatch. It is GPU-free from the same dumps.

**Unchanged by all of this**: `gdt` — 17 rows at 0.1765, the largest
non-dimension bucket and now the only one of the three with an unexamined
mechanism. The handoff's §4 list stands.

---

## 5. Provenance

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
