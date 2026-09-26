# Session handoff — 2026-09-26: the policy arms shipped; the cost is now almost all detection

Written 2026-09-26 for a FRESH session. **Read `CLAUDE.md` first, then this.**
Supersedes `2026-09-25-session-handoff.md`; `2026-09-17-session-handoff.md` §1
stays the reference for device setup.

**One line:** three gold-free post-read rules now ship by default (flag
non-dimension kinds, flag untoleranced dimensions, drop contained duplicates),
priced exactly on train/dev/test before being kept: **dev 131.87 → 118.73,
test 165.18 → 149.73, silent errors 64 → 17 on dev, auto-accept precision
0.53 → 0.80** — DERIVED from stored dumps until `r4-control` runs. OCR
proposals for isolated misses were closed at a CPU gate. What is left is
**87.6% detection**.

---

## 0. Verified state

```bash
python -m pytest -q                          # 1033 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -c "from app.eval.runner import _prompt_sha256; print(_prompt_sha256())"
                                             # aa7659f1929184ea (prompts unchanged)
```

Branch `worktree-eval-harness`, PR #2. Plan executed:
`docs/plans/2026-09-25-review-quality-arms-plan.md`. Predictions (registered
before pricing): `2026-09-25-policy-arms-prediction.md`. Result:
`2026-09-25-policy-arms-result.md`.

## 1. Where the review cost sits now (dev, scoped, DERIVED shipped config)

`docs/eval/reapply-dev-summary.json` — 118.73, reconstructs exactly:

| component | count | cost units | per doc | share |
|---|---|---|---|---|
| **missed** | 88 (contended 19, isolated 58, unlocated 11) | 880 | 58.67 | **49.4%** |
| **false detections** | 340 | 680 | 45.33 | **38.2%** |
| flags | 136 | 136 | 9.07 | 7.6% |
| escaped (silent) errors | 17 | 85 | 5.67 | 4.8% |

The read side is now nearly exhausted as a COST lever: escaped errors went
from 16.2% to 4.8% of cost. Any further read-quality work pays only on the 17
remaining escaped rows (the r3-tallpad lesson: fixing a flagged row saves 0).

## 2. What changed this session

1. **The guard metric.** `auto_accept` (precision/rate of the unflagged rows)
   in every digest; `experiment.verdict` refuses an arm whose precision falls
   or whose rate falls > 0.02. Needed because flag-everything beat the product
   under today's weights (223 vs 406 on dev). No historical verdict flipped.
2. **Exact policy pricing.** `app/pipeline/policy_rules.py` (registry, used by
   pipeline and eval alike), `app/eval/policy_check.py` (`score
   --policy-check`), `app/eval/reapply.py` (`score --reapply-policy`, marks
   reports/digests `reapplied_policy`). Both paths gave the same joint numbers.
3. **Kept and shipped:** `nondim_kind`, `no_tolerance`, `contained_duplicate`
   (result doc §2-§4). `no_tolerance` was registered as a NEGATIVE control and
   refuted — it flags 0-1 correct rows because gold carries the general
   tolerance on nearly every row.
4. **Not kept:** every other rule, with numbers in CLAUDE.md §3.
5. **`false_diagnosis`** — false detections are mostly callout-like text near
   or far from gold, not duplicates (7-11%).
6. **OCR proposals: NO-GO** (6 of 58 isolated misses covered; gate ≥ 12).
   Built, measured, reverted.
7. Review reasons shown to a reviewer are plain language (e.g. "no tolerance
   read — apply the general tolerance from the title block"). Manual reads
   through `/api/read_region` pass through the same active flag rules — but a
   manual read carries no detector `kind`, so `nondim_kind` and `no_tolerance`
   (which key on kind) cannot fire there today; the wiring is tested with
   `diameter_sign`.
8. Final-review hardening: `compare_runs` warns when two reports' review
   policy differs or one side is derived; `--policy-check` refuses dumps that
   already carry active rules and refuses `--reapply-policy` alongside it; an
   `extract()`-level test pins the drop-then-number order and the loose_text
   exclusion. No historical `experiment.py` verdict changed (checked against
   the table at `8cbd67a`).

## 3. Open threads — raw material for the next brainstorm

**Confirm what shipped (one GPU run, no decision needed)**
1. **Run `r4control`** (`run_gpu_queue.sh <free-gpu> r4control`; deploy per
   CLAUDE.md §5 — push to `from-operator`, detached checkout, rebuild
   `sindri-gpu-nf4`, check the free card). Registered prediction: equals
   `docs/eval/reapply-dev-summary.json` to the decimal (118.73, escaped 17,
   precision 0.8046, n_pred 563, field_acc ≈ 0.538). It also measures the
   Ø-zone parser fix natively. It becomes the control for every later arm.

**Detection (87.6%)**
2. **False detections (38.2%).** No gold-free rule reaches the bulk. Two
   different mechanisms remain: a VLM verifier pass over existing detections
   (priceable offline only after a GPU run that stores its verdicts), or the
   client's real weight for a false detection (`weights.json`, still pending —
   if deleting a phantom balloon costs less than 2, this bucket shrinks
   without any code).
3. **Isolated misses (58).** Detector weights are the only lever that ever
   moved them (Rung 3 adapter: 75 → 43). The merge-and-requantise route is
   built; `mergedcontrol` has never run. OCR proposals are closed.
4. **Unlocated gold (11 dev / 31 test)** is priced at w=10; see CLAUDE.md §2.

**Product**
5. **Fill the general tolerance from the title block.** `no_tolerance` now
   flags ~21 dev / 31 test / 120 train rows that are wrong only because the
   ISO 2768 general tolerance was not applied. Reading it from the title block
   and filling it would turn flags into correct rows (cost −1 each and a large
   auto-accept RATE gain) — the flag makes the gap visible; this would close
   it. Price it with `--reapply-policy`-style offline scoring first.
6. **Parser bug:** `"20 +0,2 +0,1"` parses to `lower_tol="-+0,1"`
   (`parser.py:~140`), found by the Task 3 reviewer. Price with
   `score --reparse-check` before fixing.

**Scoring**
7. `weights.json` with the client — now decides items 2 and the relative value
   of flags (7.6% of cost) vs misses.

## 4. Lessons worth carrying

* **Look for the loophole in the metric before optimising it.** The cost model
  rewarded flagging more; one GPU-free number (flag-everything = 223 vs 406)
  found it, and the auto-accept guard made every later decision honest.
* **Precision alone cannot see random flagging; the rate can.** The first
  guard version would have passed a rule that flags at random.
* **Select, validate and confirm on different splits, and register first.**
  Two registered expectations were wrong (`no_tolerance` as a negative control,
  proposal coverage 15-30) and the protocol caught both without harm.
* **A CPU feasibility gate is worth building before any GPU arm.** It closed
  direction 4 in minutes of CPU instead of two GPU runs.
* **Two independent code paths agreeing to the decimal** (policy_check and
  reapply) is the cheapest exactness proof there is — build the second path.
