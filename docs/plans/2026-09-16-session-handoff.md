# Session handoff — the crop win, the test split, and what the metric was hiding

Written 2026-09-16. **Read `CLAUDE.md` first, then this.** Supersedes
`docs/plans/2026-09-09-session-handoff.md` entirely; older handoffs are one or
more policies behind.

**One line:** the campaign's central finding is settled — **the INPUT to the
read holds the remaining quality, not the model doing the reading** — and acting
on it shipped a free win (`_CROP_PAD` 6 → 24, production 133.93 → **131.87** on
dev). Meanwhile the frozen test split was scored for the first time and says the
dev numbers everyone has been quoting are **optimistic by +31.25**.

---

## 0. Verified state

```bash
cd /home/clemi/mci/sindri/.claude/worktrees/eval-harness
python -m pytest -q                          # 861 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -c "from app.eval.runner import _prompt_sha256; print(_prompt_sha256())"
                                             # aa7659f1929184ea — must not move
```

Branch `worktree-eval-harness`, **89 commits ahead of origin, unpushed**. Both
H100s idle. `SCHEMA_VERSION` = 1. Split frozen at `6d174d5e4f1b9228`.

**NDA unchanged.** `score` / `compare` / `summary` / `probe` / `ingest` are the
operator's to run. **You do not need to paste their output**: the batch writes
every digest into `docs/eval/`, which is in the repo and is the sanctioned view.
Say "done" and the agent reads them.

---

## 1. The campaign's central finding, now measured from three directions

**The crop the reader is handed dominates read accuracy. The reader's own
weights barely matter.**

| evidence | effect on `field_acc` |
|---|---|
| degrade the boxes (32B detector, same 72B reader) | **−0.206** |
| change the reader on fixed boxes (32B → 72B) | +0.013 |
| best fine-tune on fixed boxes (`read-lora-v1`) | +0.039 |
| **give the same reader more context (`_CROP_PAD` 6 → 24)** | **+0.049** |

The cheapest lever beat the trained adapter, and the adapter needs an NF4 base
costing +6.35 to serve on a deployment route still blocked three failures deep.

**Corollary, and it is why the dose response turned:** a *better-informed*
reader is not uniformly better. Going 24 → 48 raised `field_acc` again (0.5426)
and **cost more** (132.73), because three rows moved into fully-correct and
three into `escaped_error`. Confidence outran accuracy, and a silent error is
worth 5 where the flag it displaced was worth 1. The hybrid found the same thing
from the other side: a stronger reader on bad crops removes the warning without
fixing the value.

---

## 2. What shipped

**`_CROP_PAD = 24`** (2026-09-16). Dev production **133.93 → 131.87**, better
under 6 of 6 weightings, `field_acc` 0.4798 → 0.5291, `escaped_rate` down.
`ci95 [−5.33, 0.47]` spans zero, so it is **robust but not significant at 15
documents** — the same footing `loraread` is on. Quote it that way.

Detection came back **bit-identical** (`n_pred` 569, `missed` 88,
`false_detection` 346), which makes it the cleanest single-variable arm in the
campaign.

`active_crop_knobs` compares against a **frozen `_BASELINE_CROP_KNOBS` of
6/40/3.0**, not the current default, so a pad-24 run records its knobs out loud
and `SINDRI_CROP_PAD=6` still reproduces every older dump config-and-all.

---

## 3. What the test split said, and what the metric was hiding

**165.18 against dev's 133.93.** recall 0.6301 vs 0.7170, `field_acc` 0.3804 vs
0.4798, missed 37.0% vs 28.3%. Full writeup:
`docs/plans/2026-09-15-test-split-result.md`.

Three things must travel with that number:

1. **It is not a comparison** (different doc set, no `ci95`), and n = 11.
2. **The test split is deliberately adversarial** — `splits.py` forces the
   structurally atypical `variants` into it; 6 of its 19 predicted documents are
   multi-page against 1 of 20 in dev.
3. **About a third of the gap is a pricing artefact.** A misread on a LOCATED
   gold row pairs geometrically and costs 5; the identical misread on an
   UNLOCATED row cannot pair and costs 10. Bounded: dev 133.93 → 130.27, test
   165.18 → 151.09, gap −31.25 → **−20.8**.

**What is NOT explained away: `field_acc` 0.4798 → 0.3804**, computed on matched
rows only. Read quality really is worse on unfamiliar templates — exactly what
§1 predicts, since an unfamiliar layout yields a worse crop.

**Gold coverage, measured corpus-wide.** 621 rows have no usable position = 491
with no balloon at all + 130 on a later page. By kind: `note` 401,
**`dimension` 201**, unknown 19 — and only dimension is scored, so **8.1% of
scored gold has no position**. Dev is at 3.9%, test at 11.0%: **dev is the
outlier**. The value-matching fallback rescues **1 row in 12 on dev and 1 in 32
on test** — 0 or 1 across all ten scored runs, so it is effectively dead and no
model change moves it.

---

## 4. What the client should be told

* **Scope: 75 of 99 drawings** (75.8%). 19 oversized, 8 multi-page, 3 both.
  The OVERSIZED exclusion is the larger one by 2.4x and every earlier statement
  of scope missed it.
* **Quality is a RANGE, not a number**: ~132 review cost on typical single-sheet
  drawings, ~165 on structurally atypical ones. Never the dev figure alone.
* **A corpus-representative estimate is worse than dev** even before template
  difficulty: dev sits 13 gold rows below the corpus unlocated rate, worth
  ~+8.7/doc on its own.
* **Excluded is not "fails"** — the 19 oversized drawings still produce output at
  0.371 recall against 0.728, about half as good rather than nothing.

---

## 5. Next steps, in order

1. **Re-derive `weights.json` with the client.** Zero GPU, and it is now the
   single highest-value item twice over: it decides whether `read-lora-v1` ever
   ships (it needs a silent error to cost ≥9.4× a wasted re-check against
   today's 5×), AND it prices the flagged/silent trade that turned the crop dose
   response. Two open questions, one number.
2. **Size the crop-resolution lever before running it. The diagnostic is BUILT
   (2026-09-16) and needs one re-score to fill in.** `read_accuracy_by_crop_height`
   splits field accuracy on matched rows by the crop's height in render pixels,
   bucketed at the knobs' own boundaries (`<28` the patch floor, `28-40` upscaled,
   `40-80` passed through, `>=80`). Run `./rescore_onepage.sh` and read it:
   - short buckets materially worse → `_MIN_CROP_H` / `_MAX_UPSCALE` have a
     mechanism and a predicted bucket, and an arm can be registered;
   - flat across buckets → **the whole crop-resolution family is refuted for
     free**, with no GPU night spent.
   `boxes.tighten_to_ink`'s `pad=3` is a CONTEXT lever, not a resolution one, so
   it belongs with `_CROP_PAD` — and that curve is already peaked, which argues
   against it.
3. **Re-measure the crop win on test** once (2) settles. A −2.07 that exists
   only on the tuned split is worth much less than one that survives where
   layouts are unfamiliar — and §1 predicts the effect should be LARGER there.
4. Then, unchanged from the 2026-09-14 decision doc: unblock route A serving,
   multi-page coverage (+8 drawings), tiled rendering (+19).

---

## 6. Traps added since the last handoff

All are in `CLAUDE.md` §5 with full detail; listed here so nobody rediscovers
them:

* **The deploy recipe worked once and then blocked itself.** Keep the host's
  HEAD **detached**; a checked-out `from-operator` refuses the next push.
* **A partial run used to score silently.** `score` now refuses; the warning that
  should have caught it fired identically on every healthy run, so it could never
  carry the signal.
* **A gold document with no drawing is not an unfinished run** — and the first
  version of that guard asserted the timing cause and sent the operator to wait
  for a finished run.
* **A NaN confidence makes a dump WRITE-ONLY** and dodges flagging on the way,
  because every comparison against NaN is False. Nine GPU hours were
  unscoreable. `float` in a pydantic model is not a promise that it round-trips.
* **A measured arm left out of the default batch gets a STALE digest** while
  everything around it refreshes.
