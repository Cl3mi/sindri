# `fill_general_tolerance` — result: KILLED at the train gate, nothing shipped

Run 2026-10-05. Registration: `2026-10-05-general-tolerance-registration.md`
(committed before any split was touched). Digest:
`docs/eval/gentol-check-train.json`. Command: `score --gentol-check` on
`r3-trainpredict`, train split, scoped (`--max-pages 1 --dpi 300
--exclude-clamped`), 49 documents. Every number below is CPU-derived from stored
dumps through today's post-read code. No GPU run was involved.

## 1. Decision

**Gate FAILED on train → kill rule → nothing ships.** Stage 3 was not entered:
no pipeline change, no flag-pass move, no dev/test pricing. `git diff 92cf984 --
app/pipeline app/models.py` is empty. The fill survives only as measurement code
in `app/eval/general_tolerance.py`, and a test fails if any pipeline module
imports it. **Dev and test were never scored with the fill, so both splits stay
unseen for any future re-registration.**

## 2. The registered gate

| | value |
|---|---|
| would_fix (flagged_error → correct) | **12** |
| would_break (flagged → escaped_error) | **63** |
| n = fix + break | 75 |
| break rate | 0.840, Wilson 95% upper **0.906** |
| would_break_upper95 = U·n | 67.95 |
| gate `fix ≥ 4·U·n` | 12 ≥ 271.8 → **FAIL** |
| gate `fix ≥ MIN_FIX (20)` | 12 ≥ 20 → **FAIL** |
| worst document removed (13 breaks, 0 fixes) | fix 12, break 50, U 0.886 → **FAIL** |
| max share of breaks in one document | 0.206 |
| reconciliation (attributed transitions = re-scored cost) | **holds** |

It fails on both conditions, and it fails without its worst document. No
reading of this is borderline.

## 3. Coverage

| | docs |
|---|---|
| class found, title-block grid | 29 |
| class found, notes | 0 |
| class found, loose text | 0 |
| conflict | 0 |
| no class anywhere | 20 |
| class histogram | m: 29 |

59% of train documents name an ISO 2768 class, always `m`, always in the title
grid. The registration predicted 60-85%, mostly `m`, mostly title.

## 4. Every row, by outcome

| outcome | all predictions | matched |
|---|---|---|
| filled | 178 | 93 |
| has_tolerance | 584 | 479 |
| not_dimension | 238 | 72 |
| no_class | 490 | 219 |
| no_nominal | 17 | 7 |
| table_none (outside ISO 2768-1) | 13 | 8 |
| fit_guard (ISO 286 notation on the read) | 8 | 1 |

85 of the 178 filled rows are false detections. Filling them costs nothing,
because a false detection is priced the same either way.

## 5. Transitions of the 93 matched, filled rows

| control → arm | rows | cost each |
|---|---|---|
| flagged_error → correct | 12 | −1 |
| flagged_error → escaped_error | 63 | +4 |
| flagged_error → flagged_correct | 5 | 0 |
| flagged_error → flagged_error | 13 | 0 |

Only **17 of 93** (the 12 fixes plus the 5 still flagged for another reason)
carry the ISO 2768-m value in gold.

## 6. Break breakdown (registered first-match order)

| category | rows |
|---|---|
| fit_notation | 0 |
| other:field | 0 |
| other:gold_no_tolerance | 16 |
| other:class_or_table | 2 |
| **printed_missed** | **45** |
| heads: printed_missed / fit_notation / other | 45 / 0 / 18 |

Validity check, added after the gate result (it cannot change the gate, only
whether the breakdown can be trusted). The shape of gold's tolerance on the 45
`printed_missed` rows:

| shape | rows |
|---|---|
| one-sided (only an upper or only a lower bound) | 25 |
| symmetric, a different magnitude from the table | 11 |
| asymmetric | 9 |
| **the table's magnitude, written in another shape** | **0** |

So `printed_missed` is **not** a format artefact. Those rows carry tolerances
that really are different: limits, one-sided bounds, and per-callout values the
read did not deliver.

## 7. Slices

By kind (char_type of the read):

| kind | fix | break | neutral | breaks by category |
|---|---|---|---|---|
| Distance | 4 | 30 | 13 | printed 24, gold-none 5, class/table 1 |
| Diameter | 0 | 19 | 2 | printed 15, gold-none 4 |
| Radius | 8 | 14 | 3 | gold-none 7, printed 6, class/table 1 |

By class: all 12 / 63 / 18 are `m`. By source: all are `title`. Radius is the
only kind where fixes are even a third of breaks.

Worst documents by breaks (salted ids, fix/break): dec10d4e 0/13, 8c9cd572 0/7,
e05e850a 0/6, a20d1e5a 0/5, acc41867 0/5, 62f9a390 0/4.

## 8. What the fill would have cost on train (re-scored, DERIVED)

| | control | arm | Δ |
|---|---|---|---|
| mean review cost | 113.04 | 117.94 | **+4.90** |
| auto-accept precision | 0.7548 | 0.6530 | **−0.1018** |
| auto-accept rate | 0.2270 | 0.2370 | +0.0100 |
| field_acc (matched) | 0.5040 | 0.5233 | +0.0193 |
| escaped_rate | 0.0737 | 0.1259 | +0.0522 |
| recall | 0.7283 | 0.7283 | 0 |
| correct / escaped / flagged_correct / flagged_error | 274 / 89 / 169 / 347 | 286 / 152 / 174 / 267 | +12 / +63 / +5 / −80 |

All six weightings, per document:

| weights (miss/escaped/false/flag) | control | arm | Δ |
|---|---|---|---|
| 10 / 5 / 2 / 1 (default) | 113.04 | 117.94 | +4.90 |
| 20 / 8 / 2 / 1 | 185.43 | 194.18 | +8.76 |
| 5 / 4 / 2 / 1 | 77.76 | 81.37 | +3.61 |
| 10 / 5 / 4 / 2 | 150.06 | 153.43 | +3.37 |
| 8 / 8 / 1 / 1 | 91.86 | 100.61 | +8.76 |
| 3 / 2 / 1 / 1 | 47.49 | 48.53 | +1.04 |

Better under **0 of 6**. The keep rule would have failed on train under every
condition except the rate.

## 9. Predictions vs measured (registration §6, not part of any rule)

| prediction | measured | |
|---|---|---|
| class on 60-85% of documents, mostly m, mostly title | 59%, all m, all title | edge / IN / IN |
| would_fix 60-110 | 12 | OUT |
| would_break 5-20, mostly printed_missed then other:field | 63; printed_missed 45, other:field 0 | OUT |
| gate passes on train | FAIL | OUT |

## 10. What this refutes

The handoff of 2026-09-26 (§3 item 5) estimated about 120 train rows "wrong only
because the ISO 2768 general tolerance was not applied". **Refuted on train.**
Among the 93 matched rows that would be filled, gold holds the general value on
17. On 45 it holds a different, printed tolerance; on 16 it holds none.
`no_tolerance` is still right to flag those rows: they are wrong. But filling
them is not the fix, because for most of them the missing tolerance was printed
at the callout and the read lost it.

## 11. What stays DERIVED

Everything here: it is a CPU re-score of stored pad-6 train dumps under today's
post-read code. No dev or test number exists for the fill, by design (kill rule).

## 12. Open items for later sessions (not worked on here)

1. **GPU confirmation of test.** The shipped policy's test number (149.73) is
   still DERIVED; `r4controltest` has never been run.
2. **Adapter retrain, classifier reuse.** `app/eval/general_tolerance.py` is
   kept for exactly this: gold == table value is the gold-free-at-inference way
   to mark a target's tolerance as general. But this result weakens the
   premise behind the earlier adapter finding: on train, most rows read
   without a tolerance carry a *printed* one in gold, so "targets render a
   general tolerance the crop cannot show" explains at most a minority. Before
   retraining, measure on the train target set how many rendered tolerances
   equal the table value, against how many are printed.
3. **Process and human-in-the-loop improvements**, such as reviewer-facing
   context for `no_tolerance` flags. These are product decisions, not
   measurements.
