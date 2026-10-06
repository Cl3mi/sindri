# Verifier arm — design and registration (frozen before any verdict exists)

Registered 2026-10-07, before the verifier has produced a single verdict on any
split. Operator decisions: build it alongside the suggestion tray; the keep
rule in §5 ("rise where there is room, hold elsewhere").

## 1. Why

After the phantom drops (measured: `r5-control`, `r5-controltest`), dev ships 78
values unchecked: 41 correct, 7 wrong and **30 phantoms**, all read at
confidence ≥ 0.99. No confidence rule can reach those 30. Train has only 16 such
phantoms among 196 delivered values. **Dev's 30 are never inspected
individually**: a dev profile would make dev selection data. The fix is
selected on train and validated on dev.

## 2. Mechanism (frozen)

A gold-free third drop stage, applied only to rows that would still ship
unflagged after stages 1 and 2. For each, the serving 72B is asked one yes/no
question about a **context crop**:

* **Crop:** the detected box expanded by its own width on the left and right and
  by its own height above and below (3× the box in each axis), with a minimum
  context of 200 px per axis, clamped to the page. The box is outlined in red
  (RGB 255,0,0, 3 px) on a copy. The long edge is capped exactly as the read is
  (`_cap_long_edge`). Rationale: the read crop is tight on purpose, to read
  digits. "Is this a characteristic?" is a question of context.
* **Prompt (exact text):**
  "The red rectangle marks one region detected on a technical drawing. Is the
  content inside the red rectangle a dimension or tolerance callout (a measured
  size with or without tolerance) that a quality inspector would number with an
  inspection balloon? Answer with exactly one word: yes or no."
* **Score:** one decoding step (greedy, `max_new_tokens=1`, output scores).
  `P_yes` = summed softmax probability of the token ids for "yes" and "Yes";
  `P_no` likewise for "no"/"No". `verifier_p = P_yes / (P_yes + P_no)`. If
  that sum is below 1e-6 or anything is non-finite, `verifier_p = None`: the
  row is never dropped for it, and it is counted.
* **Isolation:** the prompt lives outside the hashed read/detect prompts, so
  `prompt_sha256` stays `aa7659f1929184ea` and every existing run stays
  comparable. Its own hash is recorded as `RunConfig.extra["verifier_prompt"]`.

## 3. How verdicts are produced

A new runner command, `verify`, runs on the GPU host. It loads a run's dumps,
applies today's post-read code (`reapply_current_code`, all active stages),
renders each page at the dump's resolution (refusing a page whose scale does
not match), asks the verifier about every deliverable row, and writes the
ORIGINAL dumps plus `verifier_p` on those rows to a new run directory.
`verifier_p` is restored by `reapply_current_code`, so re-scoring keeps it.

| stage | source run | output run | split |
|---|---|---|---|
| `verifytrain` | `r3-trainpredict` | `v1-train` | train |
| `verifydev` | `r5-control` | `v1-dev` | dev |
| `verifytest` | `r5-controltest` | `v1-test` | test |

## 4. Candidates (gold-free; fire only on unflagged rows with a verdict)

| rule | drops an unflagged row when |
|---|---|
| `verifier_below_050` | `verifier_p < 0.50` |
| `verifier_below_070` | `verifier_p < 0.70` |
| `verifier_below_090` | `verifier_p < 0.90` |

Each is priced by `score --drop-check` against the CURRENT policy (stages 1 and
2) as its base, i.e. exactly as a third stage would run.

## 5. Keep rule (binding) and selection

**Selection:** among candidates that pass on train, the highest train delivered
precision. Ties go to more delivered correct values, then to listed order. Only
the selected candidate is priced on dev and test, and there is no second pick.

**Keep iff:**

1. delivered precision **rises strictly** on train and on dev;
2. delivered precision **does not fall** on test. Equal passes. Test ships 24
   values with 1 wrong and 0 phantoms, so there is no room for a strict rise
   there; dropping a correct value without removing a wrong one fails;
3. matched-only precision does not fall on any split;
4. the delivered set is not empty on any split.

Recall, cost and the six weightings are reported, not gated (precision at any
recall). Once the suggestion tray ships, dropped rows are shown to the reviewer
rather than lost.

## 6. Predictions

* Train (196 = 174/6/16): the selected threshold removes 8-14 of the 16
  phantoms and 3-15 correct values; delivered precision 0.888 → 0.90-0.94.
* Dev (78 = 41/7/30): delivered precision 0.526 → **0.60-0.75**; correct values
  lost 1-6. Some phantoms are real dimensions the client chose not to balloon,
  and a yes/no question cannot see that choice, so a dent is expected, not zero.
* Test (24 = 23/1/0): no change, or a fall. The verifier will most likely
  accept the one wrong row, which is a real characteristic misread. **The main
  risk to the keep rule is test**: it passes only if no correct value is
  dropped there.
* `verifier_p = None` on fewer than 2% of rows on every split.
