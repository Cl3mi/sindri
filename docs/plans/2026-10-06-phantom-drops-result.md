# Phantom drops — result: KEPT and shipped as drop stage 2

2026-10-06. Registration: `2026-10-06-phantom-drops-registration.md`
(committed before any pricing). Design: `2026-10-06-phantom-arm-design.md`.
Profile: `docs/eval/phantom-profile-train.json`. Digests:
`docs/eval/drop-check-{train,dev,test}.json`,
`docs/eval/reapply-phantom-{dev,test}-summary.json`.

## 1. Decision

The registered selection rule picked **`conf_below_099 + material_kind +
tight_cluster`** on train, and it passed the registered keep rule on dev and on
test. **It ships** as `ACTIVE_DROP_STAGES[1]`.

**MEASURED on both splits (2026-10-06):** native predict runs on current code,
`r5-control` (dev) and `r5-controltest` (test), reproduce the derived digests
**identically on all 37 aggregates each**: cost, taxonomy, delivered counts,
every precision, every diagnostic. Registered beforehand in `run_gpu_queue.sh`
(`700319a`). Digests: `docs/eval/r5control-scoped-summary.json`,
`docs/eval/r5controltest-scoped-summary.json`. **`r5-control` (117.87) and
`r5-controltest` (148.82) are now the dev and test controls.**

The numbers below were first DERIVED:
CPU re-scores of native dev/test dumps (`r4-control`, `r4-controltest`) and the
pad-6 train dumps (`r3-trainpredict`), through today's post-read code. The
reapply path was proven exact by `r4-control`, and it is proven again here: the
shipped pipeline code reproduces the priced arm to the row on dev and test (§4).

## 2. The result per split, against each split's own control

| | train (select) | dev (validate) | test (confirm) |
|---|---|---|---|
| **delivered precision** | 0.577 → **0.888** | 0.419 → **0.526** | 0.536 → **0.958** |
| matched-only precision | 0.755 → 0.967 | 0.805 → 0.854 | 0.684 → 0.958 |
| delivered: correct / wrong / phantom | 274/89/112 → 174/6/16 | 70/17/80 → 41/7/30 | 52/24/21 → 23/1/0 |
| delivered values | 475 → 196 | 167 → 78 | 97 → 24 |
| correct values lost | 100 | 29 | 29 |
| auto-accept rate | — | 0.225 → 0.132 | 0.178 → 0.079 |
| recall | 0.728 → 0.645 | 0.717 → 0.666 | 0.630 → 0.569 |
| review cost (reported, not gated) | 113.04 → 119.37 | 118.73 → 117.87 | 149.82 → 148.82 |
| better under weightings | 1/6 | 5/6 | 5/6 |

All six weightings on dev (control → arm): 118.73 → 117.87, 180.80 → 188.60,
88.27 → 82.73, 173.13 → 164.07, 87.73 → 87.60, 51.60 → 50.13. On test: 149.82
→ 148.82, 254.55 → 263.64, 98.55 → 91.45, 190.55 → 182.64, 121.27 → 115.73,
59.09 → 57.91. The misses-dominate weighting (20/8/2/1) is the one that
worsens, as expected from a drop rule.

## 3. Predictions vs measured

| registered | measured | |
|---|---|---|
| selected: a `conf_below_099` joint, train 0.87-0.91 | the full joint, 0.888 | IN |
| train correct lost ≈ 100 | 100 | IN |
| dev 0.70-0.92, correct lost 20-35 | **0.526**, 29 | precision OUT (low), loss IN |
| test 0.65-0.92, correct lost 15-30 | **0.958**, 29 | precision OUT (high), loss IN |
| matched precision rises on every split | rises on all three | IN |

**Dev fell well short of its band.** 30 dev phantoms were read at confidence ≥
0.99, so no confidence rule can reach them, against 16 such phantoms on train's
much larger set. On dev the client still gets fewer than 53% true values among
what ships unchecked. Test went the other way: every phantom there was below
0.99 or in a cluster, and 23 of the 24 delivered values are correct.

## 4. Exactness and the staging fix

* `score --reapply-policy` through the SHIPPED code gives dev 41/7/30 (78
  delivered, 117.87) and test 23/1/0 (24, 148.82), identical to the priced arm.
  Two independent paths agree to the row.
* **The drops ship as a second STAGE, not appended to the first.** Stage 2 was
  priced on dumps `contained_duplicate` had already thinned. In a single pass
  `tight_cluster` would also see the duplicate that rule removes and delete the
  box that survived it, an outcome nobody priced. A test pins the difference.
* The flat `ACTIVE_DROP_RULES` constant is gone. Five tests monkeypatched it,
  and after staging they would have patched nothing silently. They now patch
  `ACTIVE_DROP_STAGES`, and a stale patch fails loudly.
* `drop_check` pins its control to the registered stage-1 base, so re-running
  it after the ship still prices against the policy it was registered on (dev
  control 0.4192 reproduced after the change).

## 5. What this costs, stated plainly

The product now ships far fewer values unchecked: dev 167 → 78, test 97 → 24.
29 correct values per split that were auto-accepted are now dropped entirely:
they are neither delivered nor shown for review. That is the operator's
"precision at any recall". The auto-accept rate roughly halves on dev and falls
by more than half on test.

## 6. Open items

1. ~~GPU confirmation~~ DONE: `r5-control` / `r5-controltest` reproduced §2
   identically on all 37 aggregates each.
2. **The 30 high-confidence dev phantoms** are what remains between dev and
   "every value true", and they are exactly what approach B (a VLM verifier
   asking "is this a ballooned characteristic?") would target. Profile them
   (train only) before registering it.
3. **Reviewer-facing consequence:** dropped rows vanish silently. Showing them
   to the reviewer as "low-confidence detections, not ballooned" is a product
   decision that would keep precision and recover the lost recall. That's
   separate process work.
