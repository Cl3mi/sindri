# Phantom arm — design (approved 2026-10-06)

## Why

Under the client's priority (every delivered value true; precision at ANY
recall, operator 2026-10-06), the shipped product's weakest number is
**delivered precision**: correct / (correct + escaped + unflagged false
detections). Dev 0.419 (70 / 17 / **80 phantoms**), test 0.536 (52 / 24 / 21).
Review cost cannot see the 80, because a false detection costs 2 whether it is
flagged or not.

## Decisions (operator)

* **Approach C:** a CPU profile and gold-free drop rules first. A VLM verifier
  only if the best CPU rule leaves delivered precision clearly short.
* **Drops, not flags:** a removed phantom never reaches the drawing. The recall
  this costs is accepted.
* **No recall floor** beyond "the delivered set is not empty". Delivered correct
  values are reported next to precision in every table, so a trade toward
  "deliver almost nothing" is visible.

## Step 1 — the profile (measurement only, this step)

`score --phantom-profile --phantom-out <json>`: every UNFLAGGED prediction,
classified by its delivered outcome (correct, escaped, phantom) and sliced one
feature at a time by gold-free signals the dump already holds:

* confidence band, box height band, box width band, aspect band;
* text shape: parsed char_type, nominal digit count, decimal point, raw-text
  length band;
* distance to the nearest other prediction (fraction of the page diagonal);
* page position: edge band, title-block corner, interior;
* whether the same nominal appears on another prediction in the document.

Every feature's buckets must sum to the delivered totals in `auto_accept`
(correct, escaped, false_unflagged). That is the reconciliation identity.
Counts and slice labels only, never a value.

**Run on TRAIN ONLY** (`r3-trainpredict`, `--reapply-policy`, scoped). Dev and
test are not profiled before a rule is registered, because profiling them would
make them selection data.

## Step 2 — after the profile (registered separately, before pricing)

Candidate drop rules are picked from the train profile and registered with
predictions. Each is priced exactly on CPU and kept only if, on train
(selection), dev `r4-control` and test `r4-controltest`, each against its own
control: delivered precision rises strictly; matched-only precision does not
fall; the delivered set is not empty. Recall, cost and the six weightings are
reported, not gated.
