# GPU Direction Run — Findings

Executed `docs/plans/2026-08-19-gpu-direction-run.md` on 2026-08-20/21. Four arms,
one detection knob each, dev split, frame-corrected gold. Commit `660f462`.

Read `CLAUDE.md` first for the hard rules. This document is the evidence for the
direction call, written so that no part of the run has to be re-derived or
re-spent. Prior evidence lives in `docs/plans/2026-08-20-session-handoff.md`;
where this run contradicts it, §6 says so explicitly.

**The answer in one line:** every detection lever tested loses, the committed
defaults are locally optimal on all three knobs, and the remaining problem is
reading quality, not finding things. Next lever is Rung 2 prompts.

---

## 1. Validity — why these numbers can be trusted

Read this before quoting anything below. Three of the four arms would be
meaningless without it.

**The control arm reproduced the committed baseline exactly.** Same code, no knob
changed, fresh run name `exp-control`:

* all **20 of 20** per-document review-cost deltas exactly `0.0`
* `mean_delta 0.0`, `ci95 [0.0, 0.0]`, `b_better_fraction 0.0` across all six
  weightings
* `mean_review_cost`, `micro_recall`, `micro_precision`, `escaped_rate` identical
  to **full float precision** (`0.6457023060796646`, `0.3710843373493976`,
  `0.27044025157232704`)
* `taxonomy`, `missed_diagnosis`, `error_causes`, `pred_kinds`, `match_params`
  and `weights` byte-identical

So the predict path has not drifted since the baseline was taken, and any
non-zero delta in a treatment arm is causal — consistent with the zero noise
floor recorded in `CLAUDE.md` §5 (greedy VLM decoding, `do_sample=False`,
`top_k=1`).

**What was held constant across all four arms**, verified from the digests:

| invariant | value |
|---|---|
| `splits_hash` | `6d174d5e4f1b9228` (frozen) |
| `SCHEMA_VERSION` | 1 (never bumped) |
| `weights` | `miss=10, escaped=5, false=2, flag=1` |
| `match_params` | identical — `mode=geometry`, `assignment=greedy`, `reconcile_frames=none`, `max_geo_frac=0.1`, `value_bonus=0.35`, `misplaced_frac=0.04`, `value_sim_min=0.6`, `score_kinds=['dimension']` |
| `prompt_sha256` | `aa7659f1929184ea` — one value across all arms |
| `n_gold` | 477 over 20 documents |
| `frame_mismatch` | `n_docs_affected=0`, `n_docs_not_measured=0` on every arm |

`match_params` being identical is what makes the arms comparable at all — it is
the guard that would have made `compare_runs` refuse. `prompt_sha256` being
constant is what makes this a *detection* experiment: no arm changed the prompt.

**Every arm passes the conservation identities** (taxonomy sums to `n_gold`,
`pred_kinds` to `n_pred`, per-kind `matched + false == pred`, `missed_diagnosis`
to `missed`). The `frame_mismatch` staleness tell reads 0 on all four, so no
digest is a re-summarised stale report.

**Only one knob moved per arm** — confirmed from `config.extra`, which is now
recorded (it was `{}` on the pre-`44e95e0` baseline):

| arm | tile | tile_overlap | merge_x_tol | merge_y_gap | merge_max_lines |
|---|---|---|---|---|---|
| control | 1024 | 0.15 | 20 | 20 | 2 |
| tightmerge | 1024 | 0.15 | 20 | **8** | 2 |
| nomerge | 1024 | 0.15 | 20 | 20 | **1** |
| finetiles | **768** | 0.15 | 20 | 20 | 2 |

---

## 2. Full metrics

### 2.1 Headline

| arm | n_pred | cost | recall | precision | escaped_rate | field_acc |
|---|---|---|---|---|---|---|
| control | 830 | **174.30** | 0.6457 | 0.3711 | 0.2704 | 0.3636 |
| tightmerge | 845 | 174.65 | 0.6499 | 0.3669 | 0.2704 | 0.3645 |
| nomerge | 908 | 179.90 | 0.6625 | 0.3480 | 0.2914 | 0.3513 |
| finetiles | 833 | 179.30 | 0.6415 | 0.3673 | 0.3040 | 0.2974 |

`field_acc` = field accuracy on matched rows =
`(correct + flagged_correct) / (n_gold - missed)`. It is the condition-2 guard:
the fraction of rows the matcher paired that actually carry the right value.

### 2.2 Taxonomy (n_gold = 477)

| | control | tightmerge | nomerge | finetiles |
|---|---|---|---|---|
| missed | 169 | 167 | **161** | 171 |
| escaped_error | 129 | 129 | 139 | **145** |
| flagged_error | 67 | 68 | 66 | 70 |
| flagged_correct | 40 | 40 | 43 | **27** |
| correct | 72 | 73 | 68 | **64** |
| false_detection | 522 | 535 | **592** | 527 |
| *matched* | 308 | 310 | 316 | 306 |

### 2.3 Missed diagnosis — the routing view

| | control | tightmerge | nomerge | finetiles |
|---|---|---|---|---|
| contended | 82 | 80 | 74 | **66** |
| isolated | 74 | 74 | 74 | **90** |
| unlocated | 13 | 13 | 13 | 15 |

### 2.4 Error causes and geometry quality

| | control | tightmerge | nomerge | finetiles |
|---|---|---|---|---|
| misparse | 52 | 52 | 54 | 57 |
| misread | 144 | 145 | 151 | **158** |
| misplaced_matches | 80 | 82 | 84 | 86 |

### 2.5 Prediction kinds

| kind | control | tightmerge | nomerge | finetiles |
|---|---|---|---|---|
| dimension | 589 | 598 | 649 | 607 |
| theoretical | 102 | 105 | 109 | **79** |
| gdt | 57 | 57 | 60 | 55 |
| note | 54 | 57 | 62 | **47** |
| surface | 21 | 21 | 22 | **29** |
| material | 7 | 7 | 6 | **16** |

False detections by kind:

| kind | control | tightmerge | nomerge | finetiles |
|---|---|---|---|---|
| dimension | 342 | 349 | 394 | 356 |
| theoretical | 85 | 88 | 92 | 66 |
| note | 49 | 52 | 57 | 38 |
| gdt | 26 | 26 | 28 | 29 |
| surface | 14 | 14 | 16 | 23 |
| material | 6 | 6 | 5 | 15 |

Matched by prediction kind (note that non-`dimension` kinds contribute real
matches — this is why filtering to `score_kinds` is a measured dead end):

| kind | control | tightmerge | nomerge | finetiles |
|---|---|---|---|---|
| dimension | 247 | 249 | 255 | 251 |
| gdt | 31 | 31 | 32 | 26 |
| theoretical | 17 | 17 | 17 | 13 |
| surface | 7 | 7 | 6 | 6 |
| note | 5 | 5 | 5 | 9 |
| material | 1 | 1 | 1 | 1 |

### 2.6 Statistical robustness (`compare_runs`)

This table is more informative than the cost column, and it changes how
`tightmerge` should be described.

| arm | mean_delta | ci95 | significant | better under N of 6 weightings | robust | docs changed |
|---|---|---|---|---|---|---|
| control | 0.0 | [0.0, 0.0] | False | 0/6 | True | **0 / 20** |
| tightmerge | +0.35 | [-1.35, 1.9] | **False** | **2/6** | **False** | 8 / 20 |
| nomerge | +5.60 | [1.15, 9.7] | **True** | 0/6 | True | 16 / 20 |
| finetiles | +5.00 | [-1.7, 13.45] | False | 0/6 | True | **20 / 20** |

Per-weighting cost deltas:

* tightmerge `[0.35, -0.65, 0.85, 1.7, -0.1, 0.4]` — mixed sign
* nomerge `[5.6, 3.1, 7.1, 12.7, 4.4, 3.4]` — worse under all six
* finetiles `[5.0, 8.4, 3.7, 5.0, 6.95, 1.65]` — worse under all six

### 2.7 Cost reconciles from the taxonomy deltas alone

A cross-check in the house style — the headline cost delta must be derivable
from the taxonomy without trusting the cost field:

```
tightmerge  10(-2) + 5(0)  + 2(+13) + 1(+1)  = +7   / 20 docs = +0.35  ✓
nomerge     10(-8) + 5(+10)+ 2(+70) + 1(+2)  = +112 / 20 docs = +5.60  ✓
finetiles   10(+2) + 5(+16)+ 2(+5)  + 1(-10) = +100 / 20 docs = +5.00  ✓
```

All three reconcile exactly. `flag` delta is
`(flagged_error + flagged_correct)` differenced against control.

### 2.8 Render clamp interaction

Four sheets clamp under the 80 MP budget (109, 208, 225, 225 dpi); 16 render at
full 300 dpi. This split is where the `finetiles` mechanism shows itself.

| arm | clamped (n=4) recall / cost / isolated | unclamped (n=16) recall / cost / isolated |
|---|---|---|
| control | 0.3705 / 284.75 / **16** | 0.7281 / 146.69 / **58** |
| tightmerge | 0.3752 / 283.25 / 16 | 0.7293 / 147.50 / 58 |
| nomerge | 0.3940 / 286.00 / 16 | 0.7367 / 153.38 / 58 |
| finetiles | 0.4037 / 288.00 / **28** | 0.7107 / 152.12 / **62** |

---

## 3. Verdicts

The rule (`app/eval/experiment.py`): an arm wins only if **cost falls**, **and**
matched-row field accuracy does not fall more than 0.02, **and** `escaped_rate`
does not rise more than 0.02. Conditions 2 and 3 are not decoration — condition 2
catches recall bought by breaking correct pairs, condition 3 protects against
silent wrong values reaching the customer, which is worse than flagged ones.

| arm | cost | cond 2 (field_acc) | cond 3 (escaped) | verdict |
|---|---|---|---|---|
| tightmerge | +0.35 ✗ | +0.0009 ✓ | +0.0000 ✓ | **no** — fails cost only |
| nomerge | +5.60 ✗ | -0.0123 ✓ | +0.0210 ✗ | **no** |
| finetiles | +5.00 ✗ | **-0.0662 ✗** | **+0.0336 ✗** | **no** — fails all three |

Deltas are quoted exactly as `app/eval/experiment.py` prints them, which
differences the 4-decimal rounded values. Recomputing from full precision can
differ by one in the last digit (finetiles `field_acc` is -0.0663 and
`escaped_rate` +0.0335 that way). The conclusions are nowhere near a threshold,
so it makes no difference to any verdict — but do not treat the discrepancy as a
transcription error.

**Two arms would have been misread by a simpler rule.** `nomerge` *raised recall*
by +0.0168 and still loses. `finetiles` has a *lower* cost than `nomerge` yet is
far worse on quality. A cost-only or recall-only rule picks the wrong arm here;
this is the second time on this corpus that has been true (max-cardinality
matching was the first).

---

## 4. The merge lever is the wrong lever, not mistuned

Both merge arms lose, and the *shape* of the loss closes the lever rather than
inviting a sweep.

With `miss=10` and `false=2`, break-even is **5.0 false detections per recovered
miss**. Measured:

| arm | dose | contended recovered | false added | rate |
|---|---|---|---|---|
| tightmerge | `merge_y_gap` 20 → 8 | 2 | +13 | **6.50** |
| nomerge | `merge_max_lines` 2 → 1 | 8 | +70 | **8.75** |

The rate is above break-even at both doses **and worsens monotonically as merging
is reduced**. An intermediate setting therefore approaches 6.50 from above and
never reaches 5.0. This closes the protocol's "if `nomerge` wins, tune
`MERGE_MAX_LINES`/`Y_GAP` properly" branch as well — there is no sweep worth
running, and the reason is arithmetic rather than a single measurement.

Two further facts:

* **`isolated` is exactly 74 in control, tightmerge and nomerge.** Merge knobs
  provably cannot affect coverage. Useful as a structural sanity check on any
  future merge-adjacent arm: if `isolated` moves, something other than merging
  changed.
* **`tightmerge` is best described as a no-op, not a small loss.** `ci95`
  `[-1.35, 1.9]` spans zero, `significant: False`, better under 2 of 6
  weightings, `robust: False`, `escaped_rate` identical to four decimals,
  `field_acc` +0.0009, only 8 of 20 documents changed at all. It is
  statistically indistinguishable from control. Do not record it as evidence
  that "slightly less merging helps a little" — it is evidence that the knob
  does nothing measurable at that dose.

---

## 5. The tile lever is dead, and failed opposite to the hypothesis

The hypothesis was that a finer detect grid (768 px vs 1024 px) would find
callouts the coarse grid skips, cutting the 74 isolated misses. The opposite
happened.

**It found nothing.** `n_pred` moved 830 → 833 (+3). `missed` went **up** (+2)
and recall **down** (-0.0042). What actually changed was the *routing*:
`contended` -16, `isolated` **+16**, `unlocated` +2. Sixteen gold rows that
previously had a prediction inside their gate no longer had one — a finer grid
made coverage **worse**, not better.

**It degraded reading substantially.** Field accuracy on matched rows fell
0.3636 → 0.2974, which is **-0.0662, or 3.3× the 0.02 tolerance** — by far the
largest quality regression in the run:

* `correct` -8 and `flagged_correct` -13 → **21 fewer correct-value rows**
* `escaped_error` +16, `escaped_rate` +0.0336 (the worst of the three arms)
* `misread` +14, `misparse` +5 — perception, not parsing
* `misplaced_matches` +6 — the pairs it did make sit further from the balloon

**Two independent signals point at the same mechanism** (this part is inference,
not measurement, and should be treated as a hypothesis if anyone revisits tiling):

1. **The kind distribution shifts sharply**, which pure geometry would not
   explain: `theoretical` 102 → 79 (-23), `material` 7 → 16 (+9), `surface`
   21 → 29 (+8), `note` 54 → 47 (-7). Distinguishing an untoleranced basic
   dimension from a material or surface callout needs surrounding context; a
   smaller tile has less of it.
2. **The isolated regression concentrates on the clamped sheets.** Of the +16
   isolated misses, **+12 land on the 4 clamped sheets** (16 → 28) and only +4 on
   the 16 unclamped ones (58 → 62) — **+3.00 per clamped sheet vs +0.25 per
   unclamped sheet, a 12× difference**. Low-dpi rasters have the least pixel
   detail to begin with, so they are hurt most by cutting the tile smaller.

Coherent story: 768 px tiles split callouts across tile seams, producing worse
boxes, which yields worse read crops and worse kind classification, and the
damage is worst where effective resolution is already lowest.

`unlocated` moving 13 → 15 fits the same story: rows with no gold position are
matched on *value*, so degraded reading costs two of them their match.

**Note this is a different lever from the already-dead render-resolution one.**
Render pixel budget (80 → 150 MP) was measured harmful separately (handoff §2.1).
Tile size at fixed render budget is now also measured harmful. Both directions of
the resolution family are closed, from opposite ends.

---

## 6. What this changes about previously held beliefs

**Corrected — the contended bucket is mostly not a merge artefact.** The
2026-08-20 handoff (§2.2) reasoned from the max-cardinality failure that because
stealing a prediction from one gold row to give its neighbour breaks a correct
pair, "there is only one detection where two callouts exist —
`merge_adjacent` collapsing siblings", and concluded "the contended bucket is a
detection problem". Measured: that explains **8 of 82**. The other 74 survive
with stacking merges effectively disabled. The inference was reasonable and is
now superseded; the contended remainder is a matcher/geometry question, not a
merge question.

**New caution — `missed_diagnosis` is not a partition into stable buckets.**
`finetiles` moved 16 rows from `contended` to `isolated` with `missed` almost
unchanged. The 82/74 split describes the *current* detection density, not two
independent problems of fixed size. Any plan that budgets work as "fix the 82,
then fix the 74" is built on a moving denominator.

**Confirmed — the defaults are locally optimal.** Three independent
perturbations of the three plausible detection knobs all made things worse, so
`tile=1024, tile_overlap=0.15, merge_x_tol=20, merge_y_gap=20, merge_max_lines=2`
should be treated as a tuned configuration, not an arbitrary starting point.

**Confirmed — the verdict rule earns its keep.** It rejected an arm with higher
recall (`nomerge`) and correctly ranked a cheaper-looking arm as the worst
(`finetiles` vs `nomerge`).

---

## 7. Direction: Rung 2 prompts

The remaining problem is not finding callouts, it is reading them.

* **196 matched-but-wrong rows** (`escaped_error` 129 + `flagged_error` 67)
  against **169 missed**. The bigger bucket is already the reading one.
* `error_causes` splits it **`misread` 144 vs `misparse` 52** — roughly 3:1
  perception over parsing.
* Field accuracy on matched rows is **0.3636**: even where the pipeline pairs the
  right callout, it gets the value right barely over a third of the time.
* Every arm that perturbed detection *also* degraded reading (`misread` +1, +7,
  +14 for tightmerge, nomerge, finetiles). Reading quality is sensitive to
  upstream crop quality, which is a further argument that the marginal return is
  on the read stage.

Prompt edits are local and testable, and `prompt_sha256` is already recorded in
every digest, so a prompt arm is attributable in exactly the way these knob arms
were.

**Still open, unchanged by this run** (from handoff §5):

1. The `false_detection` metric question. 522 false detections, of which 91
   (`theoretical` 85 + `material` 6) have no possible gold counterpart, while
   GD&T and surface callouts *do* have in-scope gold. Any fix changes
   `MatchParams` and breaks comparability with this baseline — so decide it
   before, not during, a measurement campaign.
2. `976d3c0d` has no clean original, so its gold positions are dropped. Fine for
   dev (excluded), but a test-split run needs it sourced or formally excluded.
3. The contended remainder (74 rows) now has no candidate explanation. Worth a
   diagnostic pass — `misplaced_matches` is 80 at baseline, which suggests
   geometry quality is worth examining before any more detection knobs.

---

## 8. Operational facts for the next run

**Wall clock — the protocol's estimate was 2× optimistic.**

| arm | started | finished | duration |
|---|---|---|---|
| control | 2026-08-20 09:48 | 18:33 | **8 h 46 m** |
| nomerge | 18:34 | 2026-08-21 03:20 | **8 h 45 m** |
| tightmerge | 03:20 | 12:04 | **8 h 45 m** |
| finetiles | 12:04 | 21:42 | **9 h 38 m** |

Total **~35 h 54 m** for four arms. Budget **~9 h per arm**, not the 4–5 h the
plan assumed. Per document that is ~26 min.

**768 px tiles cost 1.10× wall clock, not 1.8×.** The plan reasoned from tile
count `(1024/768)² ≈ 1.78`. Actual was 9 h 38 m vs 8 h 45 m. Per-tile cost falls
as tiles shrink, so tile-count ratios badly overestimate runtime. Useful for
budgeting any future grid sweep.

**Both H100s were free; only one was usable per arm.** `run_experiment_gpu.sh`
runs arms sequentially and has no per-document sharding, so a single arm cannot
use two cards. Parallelising *across* arms works (separate `GPU=` and separate
remote root) but only helps while more than one arm remains.

### 8.1 Process defects and gotchas found while executing

Each of these cost time or nearly corrupted a result.

* **The reproduction gate can be skipped silently.** In
  `run_experiment_gpu.sh:149` the gate is nested inside
  `if [ -f "$CONTROL_REPORT" ]`. If `reports/baseline-dev.report.json` is absent,
  the control arm runs, prints no gate line, and the run proceeds as if gated.
  Verified present via `runner summary` before launching. **Worth fixing:** the
  control arm should fail loudly when its comparison point is missing.
* **A remote container survives killing the local ssh.** Killing the driver
  process left `podman` running and the GPU allocated. To actually free a card,
  identify the container by its `--out /data/runs/exp-<arm>` command
  (`podman ps --no-trunc`) and `podman kill` that ID specifically.
* **`setsid nohup cmd & echo $!` reports the wrapper PID, not the script.**
  `setsid` forks, the captured PID exits immediately, and a liveness check on it
  falsely reports the run dead. Find the real process with `pgrep -af` and note
  that it is its own session/pgroup leader, so `kill -- -<pgid>` cleans up the
  script and its children without touching a parallel instance.
* **Do not resume partial prediction dumps you cannot validate.** A killed
  container leaves its in-flight `*.pred.json` truncated, and NDA rules forbid
  reading those files to check. ~8 dumps (~4 h of GPU) were discarded rather than
  risk a truncated dump silently poisoning an arm.
* **A fresh run name per arm is mandatory**, because the container's `git_sha` is
  always `"unknown"` and resume compares the whole `RunConfig`. Detection knobs
  now live in `RunConfig.extra`, so a knob change no longer collides — but a
  re-run of the *same* arm still will.
* **The doc-id hashes in a `predict` log join to nothing** (the container mints
  and destroys its own salt). The comparable per-arm signal in the log is the dpi
  multiset — `{109, 208, 225, 225}` plus sixteen 300s — not the ids.
* **Watching a long run:** filter the log to markers rather than tailing it. A
  filter on `^===== arm`, `^\[(5|10|15|20)/20\]`, `arm .* done`, `ARM FAILED`,
  `REPRODUCTION GATE`, plus `Traceback|Tesseract|falling back|OOM|Killed` gives
  progress and every failure mode at about a dozen events per arm. `sync_client_data.sh`
  prints counts and byte totals only, never a filename, so a marker-filtered log
  is safe to read.

---

## 9. Reproducing and verifying this

```bash
python -m pytest -q                          # 441 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # guard: 32 passed, 0 failed
python3 -m app.eval.experiment               # the decision table in §2/§3
```

Identity check over every arm (all four must print `identities OK`):

```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('docs/eval/exp-*-summary.json')):
    s = json.load(open(f)); t = s['taxonomy']
    assert t['missed']+t['escaped_error']+t['flagged_error']+t['flagged_correct']+t['correct'] == s['n_gold']
    assert sum(s['pred_kinds'].values()) == s['n_pred']
    assert sum(s['false_detections_by_kind'].values()) == t['false_detection']
    assert sum(s['matched_by_pred_kind'].values()) == s['n_gold']-t['missed']
    assert sum(s['missed_diagnosis'].values()) == t['missed']
    assert s['frame_mismatch']['n_docs_affected'] == 0
    assert s['frame_mismatch']['n_docs_not_measured'] == 0
    assert s['splits_hash'] == '6d174d5e4f1b9228'
    print(f.split('/')[-1], 'identities OK')
"
```

Re-running an arm: `./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' <arm>`,
with a fresh run name if repeating one that already exists.

### Artifacts

| file | what it holds |
|---|---|
| `docs/eval/exp-control-summary.json` | control digest — reproduces `baseline-summary.json` |
| `docs/eval/exp-control-vs-control.json` | the reproduction gate evidence: 20/20 deltas `0.0` |
| `docs/eval/exp-{nomerge,tightmerge,finetiles}-summary.json` | per-arm digests |
| `docs/eval/exp-*-vs-control.json` | per-arm compare: `ci95`, significance, six-weighting sensitivity |

All are values-blind: aggregate metrics plus salted 8-hex doc ids, verified
before publishing. Full reports stay under the protected root because they embed
gold values.
