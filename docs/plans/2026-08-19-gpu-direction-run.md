# GPU Direction Run — Protocol

> **For whoever runs this:** everything is already built and tested. This is an
> execution protocol, not an implementation plan. Steps use checkbox (`- [ ]`)
> syntax. Run them in order and stop where a gate says stop.

**Goal:** one comprehensive GPU session that answers two questions — *is the
pipeline working after the gold-geometry fix?* and *which of the three remaining
levers is worth pursuing?*

**Approach:** four arms, one knob each, all selectable by environment variable so
no arm needs a rebuild. A control arm re-runs the current code unchanged and must
reproduce the committed baseline exactly; that is a hard gate before any treatment
arm is interpreted. A decision table then applies a verdict rule that cannot be
fooled by recall bought at the cost of correctness.

**Tech:** `run_experiment_gpu.sh` (orchestration), `app/eval/experiment.py`
(decision table), Qwen2.5-VL-72B-AWQ in the `sindri-gpu` podman image.

---

## 0. What you are walking into

### 0.1 The data rules are enforced, not advisory

Client drawings and inspection sheets are under NDA and a guard hook
(`~/.claude/hooks/sindri-guard.py`) blocks every agent from reading them.

* Protected root: `/home/clemi/sindri-client-data`.
* Only the reviewed CLI may touch it: `python -m app.eval.runner
  <probe|headers|ingest|split|predict|score|compare|summary|variants>`, plus
  `setup_client_data.py` and `sync_client_data.sh` — and **only as single,
  unpiped, unchained commands**. `cmd | head`, `cmd && cmd`, `cmd > file` and
  heredocs that mention the root are all denied. This bit me four times; the
  guard is right every time.
* Never read `*.pdf`, spreadsheets, `*.gold.json`, `*.pred.json`,
  `*.report.json`, or `doc_id_map*`. Read `runner summary` output instead.
* `ls` on the protected root is denied too. The layout is documented in
  `setup_client_data.py`: `corpus/{originals,stamped,excel}` plus `gold/ runs/
  reports/ meta/`.
* **Do not widen the guard.** Verify it with
  `bash ~/.claude/hooks/test-sindri-guard.sh` (32 cases) after any change.

Everything below is either a sanctioned `runner` command or touches only
values-blind digests under `docs/eval/`.

### 0.2 State at the time of writing

Branch `worktree-eval-harness`, PR #2. Suite **441 passed, 2 skipped** (the 2
skips are `tests/test_detect_gpu.py`, which need `RUN_GPU_TESTS=1` on a GPU host).
Guard 32/32. `SCHEMA_VERSION` = 1. Split frozen at `6d174d5e4f1b9228`.

The Rung-0 baseline, after the gold-geometry fix:

```
n_docs=20  n_gold=477  n_pred=830
mean_review_cost=174.30  micro_recall=0.646  micro_precision=0.371
escaped_rate=0.270
taxonomy: missed=169  false_detection=522  escaped_error=129
          flagged_error=67  flagged_correct=40  correct=72
missed_diagnosis: contended=82  isolated=74  unlocated=13
```

It was **245.30 / 0.350** before the fix. That older number is in git history and
in `docs/eval/render150-*.json`; it was measuring a coordinate bug, not the model.

### 0.3 Three findings that shape this run

1. **The noise floor is exactly zero.** Sixteen documents whose input did not
   change returned per-document review-cost deltas of exactly `0.0` across a GPU
   device change, because the VLM decodes greedily (`do_sample=False`,
   `top_k=1`). So **one arm per hypothesis is enough — no repeats**, and any
   non-zero delta is causal. This is why the control arm is a usable gate.

2. **Resolution is not the constraint.** Raising the render pixel budget
   80 → 150 MP un-clamped two sheets to full 300 dpi and changed nothing:
   isolated misses 251 → 252, `mean_delta +1.65` review cost, worse under all six
   weightings. Reverted. **Do not re-attempt dpi or pixel-budget levers**, and do
   not build the 606 MP tiled-rendering path on the assumption that resolution
   recovers misses.

3. **Recall can be bought dishonestly, and was.** Maximum-cardinality matching
   recovered 26 misses by destroying 27 correct pairings — field accuracy on
   matched rows fell 36.4% → 25.4%, misplaced pairs rose 80 → 126, and review
   cost *fell*. A cost-only rule would have called that an improvement. The
   decision table in §3 encodes the guard against it. The mode survives as
   `--assignment max_cardinality`, a diagnostic, never a default.

### 0.4 What the arms test

| arm | env | targets | hypothesis |
|---|---|---|---|
| `control` | — | none | the predict path still reproduces the committed baseline |
| `nomerge` | `SINDRI_MERGE_MAX_LINES=1` | 82 contended | `merge_adjacent` collapses sibling callouts into one detection |
| `tightmerge` | `SINDRI_MERGE_Y_GAP=8` | 82 contended | same, softer — merge less rather than not at all |
| `finetiles` | `VLM_TILE=768` | 74 isolated | a finer detect grid finds callouts the coarse one skips |

`nomerge` is the sharpest test available. The contended bucket means a prediction
sat inside a gold row's gate and the matcher gave it to a neighbour; that happens
when two callouts share one detection. Disabling stacking merges should split them
back apart. Expect false detections to *rise* — an unmerged tolerance-over-nominal
pair becomes two predictions — which is exactly the trade the verdict rule weighs.

Everything is one knob per arm on purpose. Two knobs cannot be attributed.

---

## 1. Pre-flight (local, ~2 minutes)

- [ ] **Step 1: Confirm the tree is green and pushed**

```bash
cd /home/clemi/mci/sindri/.claude/worktrees/eval-harness
python -m pytest -q
```
Expected: `441 passed, 2 skipped`. Zero failures.

```bash
bash ~/.claude/hooks/test-sindri-guard.sh
```
Expected: `guard: 32 passed, 0 failed`.

```bash
git status --short && git log --oneline -1
```
Expected: clean tree, and the HEAD commit already pushed — the GPU host builds
from `origin/worktree-eval-harness`, so an unpushed commit means the host runs
different code than you tested.

- [ ] **Step 2: Confirm the baseline digest is the post-fix one**

```bash
python3 -m app.eval.experiment
```
Expected: a one-row table reading
`control  174.3  0.6457  0.3636  169  82  74  522  80`
and `no treatment arms found — nothing to decide yet`.

If cost reads `245.3`, the digest is the pre-fix one — stop and re-score before
going near the GPU.

- [ ] **Step 3: Check the card is actually free**

```bash
ssh -o BatchMode=yes 4mehpc4_3 'nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv'
```
The script pins `nvidia.com/gpu=1`. If GPU 1 shows more than a few hundred MiB in
use, either wait or re-point with `GPU=nvidia.com/gpu=0`. A 72B AWQ load into an
occupied card falls back to Tesseract, which then fails every document — loudly,
but only after you have queued the whole run.

---

## 2. Run it (GPU host, hours)

- [ ] **Step 1: Launch, control arm first**

```bash
cd /home/clemi/mci/sindri/.claude/worktrees/eval-harness
./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' control
```

Budget roughly 4–5 h for 20 documents at the 80 MP budget. Run it under `nohup`,
`tmux`, or a background task — not a foreground shell you might lose.

Watch for, in order:
* `code at <sha>` — must match your local HEAD.
* `image built`.
* `WARNING: no persistent doc-id salt … throwaway` — **expected and correct**.
  The container mints and destroys its own salt, so the ids in this log join to
  nothing. Per-document facts come from `runner summary`.
* `[n/20] <hash> dpi=300` per document. Four sheets legitimately report less
  (109, 208, 225, 225) — those are clamped by the 80 MP render budget and that is
  the intended, reverted-to behaviour.
* `reproduction gate OK: all per-document deltas are exactly 0.0`.

- [ ] **Step 2: STOP if the reproduction gate fails**

If you see `REPRODUCTION GATE FAILED`, do not run the treatment arms and do not
interpret anything. It means the predict path drifted from the committed baseline.
The dumps are *not* expected to be byte-identical — the committed ones predate
`RunConfig.extra` — but the **scores** must be, because scoring is deterministic
here. Investigate the drift first.

- [ ] **Step 3: Run the treatment arms**

```bash
./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' nomerge tightmerge finetiles
```

A failing arm is logged and skipped; the others continue. `finetiles` is the
slowest — a 768 px grid is roughly 1.8× the tiles of 1024 px, so allow extra time.

Each arm writes `docs/eval/exp-<arm>-summary.json` (values-blind, committable) and
`docs/eval/exp-<arm>-vs-control.json`.

---

## 3. Read the direction (local, minutes)

- [ ] **Step 1: Print the decision table**

```bash
python3 -m app.eval.experiment
```

It prints one row per arm — cost, recall, field accuracy on matched rows, missed,
contended, isolated, false detections, misplaced — then a verdict per arm.

An arm **wins only if all three hold**:
* `mean_review_cost` went down, **and**
* field accuracy on matched rows did not fall more than 0.02, **and**
* `escaped_rate` did not rise more than 0.02.

The second and third conditions are not decoration. Condition 2 is what catches
recall bought by breaking correct pairs; condition 3 is handoff §6's regression
guard — a silent wrong value reaching the customer is worse than a flagged one.
An arm that lowers cost while failing either is inflating the metric, not fixing
the pipeline. **Do not pick the cheapest-looking arm; read the `why` lines.**

- [ ] **Step 2: Sanity-check the conservation identities**

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
    assert s['frame_mismatch']['n_docs_affected'] == 0, f+': gold ingested without --originals'
    assert s['frame_mismatch']['n_docs_not_measured'] == 0, f+': stale report, re-score'
    assert s['splits_hash'] == '6d174d5e4f1b9228'
    print(f.split('/')[-1], 'identities OK')
"
```
Every arm must print `identities OK`. A `frame_mismatch` failure means gold was
re-ingested without `--originals` — see §5.

- [ ] **Step 3: Record the outcome**

```bash
git add docs/eval/exp-*-summary.json docs/eval/exp-*-vs-control.json
git commit -m "docs(eval): GPU direction run — <one-line verdict>"
git push origin worktree-eval-harness
```

State the verdict in the commit body with the numbers, including for the arms that
lost. A negative arm is a result: it removes a lever from the list.

---

## 4. How to read each outcome

| what the table shows | what it means | next |
|---|---|---|
| `nomerge` wins, contended drops hard | `merge_adjacent` was collapsing sibling callouts | tune `SINDRI_MERGE_MAX_LINES`/`Y_GAP` properly, confirm on the test split |
| `nomerge` lowers cost but field accuracy falls | it split real stacks into halves that each mis-parse | try `tightmerge` instead; the merge is useful, just too eager |
| `nomerge` and `tightmerge` both lose | contended misses are genuine single detections, not merge artefacts | drop the merge lever; go to Rung 2 prompts |
| `finetiles` wins, isolated drops | the detector was under-covering at 1024 px | sweep tile size; mind the roughly linear cost in VLM calls |
| `finetiles` raises false detections more than it cuts misses | finer tiles are finding noise, not callouts | drop the tile lever |
| nothing wins | both remaining miss buckets are model capability, not plumbing | Rung 2 prompts: 196 matched-but-wrong rows, `misread` 144 vs `misparse` 52 |

The last row is a real possibility and not a failure of the run. `missed` is 169
of 477 and `error_causes` already says the larger remaining problem is reading
quality, not finding things.

---

## 5. If something looks wrong

| symptom | cause | fix |
|---|---|---|
| every document `FAILED` immediately | VLM did not load; fell back to Tesseract, and `extract` refuses to balloon without it | check GPU occupancy, re-point `GPU=` |
| all 20 documents `skipped (already predicted)` | resume matched an existing run name | use a fresh run name; knobs are in `RunConfig.extra` now so a knob change no longer collides, but a *rerun of the same arm* will |
| `frame_mismatch.n_docs_affected > 0` | gold was re-ingested without `--originals` | `python3 -m app.eval.runner ingest --pdfs .../corpus/stamped --excel .../corpus/excel --originals .../corpus/originals --out .../gold` (no `--cv`, no `--variants` — that recipe reproduces the corpus byte-identically) |
| `frame_mismatch.n_docs_not_measured > 0` | a report was re-summarised without being re-scored | re-score, do not just re-summarise. This trap cost a full analysis cycle once |
| `NOT COMPARABLE: gold differs` | gold changed since the control report | re-score both runs against the same gold |
| `NOT COMPARABLE: match params differ` | one report used `--reconcile-frames` or `--assignment` | that guard is working; score both plainly |
| `WARNING: no original for …` naming `976d3c0d` | expected — that drawing has no clean original, so its positions are dropped and it counts as `unlocated`. Outside the dev split | none |

---

## Definition of Done

- [ ] `python -m pytest -q` → `441 passed, 2 skipped`, and the guard 32/32.
- [ ] Control arm ran and the reproduction gate printed
      `all per-document deltas are exactly 0.0`.
- [ ] All three treatment arms produced `docs/eval/exp-<arm>-summary.json`, or a
      skipped arm is explained in the commit message.
- [ ] Every arm passes the §3 Step 2 identity check.
- [ ] `python3 -m app.eval.experiment` prints a verdict per arm, and the chosen
      direction is justified by the `why` line — not by cost alone.
- [ ] The outcome is committed with the numbers for winning **and** losing arms.
- [ ] `SCHEMA_VERSION` is still 1, `splits_hash` is still `6d174d5e4f1b9228`, and
      `git diff --stat` names no file under `~/.claude/hooks/`.
