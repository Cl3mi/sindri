# The CROP-CONTEXT arm — registered prediction, written BEFORE the run

Written 2026-09-14, before any GPU time. Decision and ordering:
`docs/plans/2026-09-14-next-steps-decision.md`. Mechanism source:
`docs/plans/2026-09-11-hybrid-arm-result.md`.

**The arm.** `SINDRI_CROP_PAD` 6 -> 24 px — 0.5 mm -> 2 mm of context at 300 dpi
around each read box. Nothing else moves: same 72B AWQ checkpoint, same image,
same prompts (`aa7659f1929184ea`), no adapter, one card. Stage `cropctx`, run
`r3-cropctx`, judged against `r3-awqcontrol-scoped` (133.93).

---

## 1. The mechanism, and why it is not another knob

`CLAUDE.md` §2 forbids proposing a knob "without a mechanism that predicts which
bucket moves and why". Three measurements give one.

**(a) The crop dominates read accuracy.** `r3-hybrid`: hold the reader, change
the boxes, `field_acc` 0.4798 -> 0.2739 (**-0.206**). Hold the boxes, change the
reader: **+0.013**. The best reader change ever measured on fixed boxes,
`read-lora-v1`: **+0.039**.

**(b) Every box lever tested so far was about WHICH boxes exist** — tile size,
both merge knobs, render resolution, the detect prompt, the read prompt. None
was about what crop a box becomes. `_CROP_PAD`, `_MIN_CROP_H`, `_MAX_UPSCALE`
and `tighten_to_ink` have no digest, no §3 entry and no arm.

**(c) The LoRA's failure mode is this hypothesis's strongest evidence.** Trained
to read tolerances, the adapter moved `dropped_tolerances` 95 -> 33 and
`missing:*_tol` -101 — but `wrong:*_tol` **+88**. It learned to emit
tolerance-SHAPED output, not to read tolerances. **A reader that could SEE the
tolerance would have converted missing into CORRECT; one that cannot can only
convert missing into WRONG.** That is what a crop with the tolerance line cut
off looks like from the reader's side.

And the geometry fits: `tighten_to_ink` shrinks the detector's box to its ink,
then 6 px — half a millimetre — is added back. A tolerance stacked under a
nominal sits 2-3 mm away, which is 24-35 px at 300 dpi. The current pad cannot
reach it; 24 px is the smallest dose that can.

---

## 2. The target bucket — the real gate

Production (`r3-awqcontrol-scoped`, 15 documents, 311 gold, 223 matched):

| bucket | production |
|---|---|
| `dropped_tolerances.rows` | **48** |
| `missing:lower_tol` | **45** |
| `missing:upper_tol` | **28** |
| rows wrong ONLY in tolerances | **30 of 223 (13.5%)** |

**These must fall. If they do not, the arm is a loss whatever review cost does.**
That is the rule that settled `readcenter`: it cost +0.90 and its target bucket,
`misread.misplaced`, was provably untouched at 64 -> 64, so the hypothesis was
refuted regardless of the cost number. Same standard here.

---

## 3. The prediction

**Identity gate (the arm is VOID if this fails): `n_pred == 569`, exactly.**
The crop is derived AFTER detection — `detect_regions` sees the full tile and
never the read crop — so detection cannot move. Per-kind matched/false counts on
`dimension`/`gdt`/`surface`/`material` should also hold, with the caveat
`CLAUDE.md` records: `false_detection` may move by ~1 because `matching.py`
gives a `value_bonus` on a parsed nominal and matches `unlocated` gold by value
alone, and `extract.py` relabels `note` -> `theoretical` on the READ. `n_pred`
itself is exact.

**Ceiling: 30 rows, worth about -6.80/doc.** All 30 tolerance-only-wrong rows
recovered, split by production's escaped/flagged ratio (71/45 of 116 wrong
rows, 61% escaped): ~18 x (escaped -> correct, -5) + ~12 x (flagged_error ->
flagged_correct, -1) = -102, / 15 = **-6.80**. That would be the largest win in
the campaign — bigger than `read-lora-v1`'s -4.40 — and `field_acc` would go
0.4798 -> 0.6143.

**Central estimate: -2.30/doc (a third of the ceiling), band [-6.80, +3.00].**
The positive end is the damage risk below. The uncertainty is deliberately wide:
what fraction of those 30 rows lost their tolerance to a clipped crop rather
than to a misread is exactly what is unmeasured, and it is the question the arm
exists to answer.

**Damage counter, registered: `misread.misplaced` (35 in production).** More
context means more neighbouring callouts inside the crop, which is the failure
`readcenter` targeted. That arm found the model is NOT confused about which
callout to read — its target bucket was untouched — which is weak evidence that
extra context is tolerable. Weak because `readcenter` changed the PROMPT, not
the crop, so this is an inference and not a measurement. If `misread.misplaced`
rises materially while the tolerance buckets fall, the mechanism is real but the
dose is too large, and 12 px is the next dose rather than a retreat.

---

## 4. How it is judged

Not on review cost alone — `CLAUDE.md` §4, and cost has been wrong three times
on this corpus. In order:

1. `n_pred == 569`. Void otherwise.
2. `dropped_tolerances`, `missing:lower_tol`, `missing:upper_tol` all down.
   Refuted otherwise, whatever the cost says.
3. `field_acc` up, `escaped_rate` not up.
4. Review cost, with `ci95` and the 6-weighting robustness check.

## 5. Running it

Both cards are free, and the other overnight job needs one:

```bash
ssh 4mehpc4_3 "tmux new -d -s awqtest '~/sindri/run_gpu_queue.sh 0 awqtest'"
ssh 4mehpc4_3 "tmux new -d -s cropctx '~/sindri/run_gpu_queue.sh 1 cropctx'"
```

Then, on the operator's machine:

```bash
./sync_client_data.sh pull 4mehpc4_3 '~/sindri-eval-data' r3-cropctx "$HOME/sindri-client-data"
./sync_client_data.sh pull 4mehpc4_3 '~/sindri-eval-data' r3-awqtest "$HOME/sindri-client-data"
./rescore_onepage.sh r3-cropctx
SPLIT=test ./rescore_onepage.sh r3-awqtest
```

`r3-awqtest` is scored on the frozen test split and has no control: it is a
standalone generalization number, and `_check_comparable` will refuse it against
any dev report — correctly, because it is a different document set.
