# r4-controltest — the shipped configuration, MEASURED on the test split

Run 2026-10-05 on GPU 0 (6 h 48 m, 19 dumps, 0 failures), scored 2026-10-06.
Prediction registered in `run_gpu_queue.sh` `stage_why` (`de424d2`). Digests:
`docs/eval/r4controltest-scoped-summary.json`,
`docs/eval/r4controltest-scoped-vs-reapply-test.json`.

**The shipped product's test number is now MEASURED: 149.82** (11 scoped
documents), against the DERIVED 149.73 it replaces. Quote the product as
**dev 118.73 / test 149.82, both measured.**

## 1. Prediction vs measured

| registered | measured | |
|---|---|---|
| missed exactly 108 (contended 39 / isolated 38 / unlocated 31) | 108 (39 / 38 / 31) | exact |
| n_pred 357 ± 5 | 354 | IN |
| cost ≈ 147.5, in [144.0, 150.5] | 149.82 | IN (point estimate off by +2.3) |
| auto-accept precision > 0.6986 | 0.6842 | **OUT** |
| auto-accept rate ≥ 0.1747 | 0.1781 | IN |

Detection reproduced exactly, so the serving stack is unchanged and the
difference from the derived number is the crop pad alone.

## 2. Against the derived number (same 11 documents)

| | derived (`r3-awqtest`, pad 6, reapplied) | measured (pad 24) | Δ |
|---|---|---|---|
| mean review cost | 149.73 | 149.82 | +0.09, ci95 [−1.55, +1.73], 3/6 weightings |
| auto-accept precision | 0.6986 | 0.6842 | −0.014 |
| auto-accept rate | 0.1747 | 0.1781 | +0.003 |
| field_acc (matched) | 0.3804 | 0.4076 | +0.027 |
| correct / flagged_correct / flagged_error / escaped | 51 / 19 / 92 / 22 | 52 / 23 / 85 / 24 | +1 / +4 / −7 / +2 |
| false detections | 173 | 170 | −3 |

`compare` warns that the review policy differs. **That warning is spurious
here:** the derived side's dumps predate the rules but were scored under them
(`reapplied_policy`). The second warning, that one side is derived, is correct.

## 3. What it means

* **Pad 24's dev gain did not generalise to test.** On dev, under this policy,
  pad 6 → 24 was −2.20 with precision 0.753 → 0.805. On test it is cost-neutral
  and precision moves the other way by 2 rows. Field accuracy rose on both
  splits. This is the confidence pattern CLAUDE.md records for the hybrid and
  `cropctx48`: more context reads more rows right and makes the reader more
  confident on some it still reads wrong. Nothing here is significant, so pad 24
  stays. It is not a candidate for change, because a test result cannot select a
  setting.
* **Under the client's stated priority (every delivered value true, over finding
  all of them), test is where the product is weakest:** auto-accept precision
  0.684 against dev's 0.805, i.e. 24 silently wrong values of 76 auto-accepted.
  22 are `dimension` rows, 2 are `material` rows (a kind no active flag rule
  covers).
* **The current precision metric undercounts what the client means.**
  `auto_accept.precision` counts only MATCHED unflagged rows. An unflagged
  false detection is a phantom value delivered silently, and it is not in that
  ratio at all. That gap is what to close next (see the session notes).
