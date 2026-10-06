# Parser fix: a positive second tolerance — registration (before pricing)

Registered 2026-10-07, before `score --reparse-check` has been run with the fix.

**Bug.** `parse_value("20 +0,2 +0,1")` returns `lower_tol = "-+0,1"`: a second
signed tolerance is prefixed with "-" even when it carries its own "+". A
drawing writes "+0,2 +0,1" when both limits lie above the nominal (20,1 to
20,2). Found in the 2026-09-25 policy-arms review and parked.

**Fix.** A second signed tolerance keeps its own sign: "+0,1" → "0,1" (the
scorer canonicalises a leading "+" away), "-0,1" → "-0,1" as today. Nothing
else in the parser changes.

**Mechanism and predictions.** Only rows whose raw text holds two
"+"-prefixed tolerances move, and today their lower tolerance is malformed, so
they can never score correct.

* `would_break` = 0 on train, dev and test. A row the fix changes was wrong
  before it, so it cannot be broken.
* `would_fix` 0-3 per split. Such tolerances are rare, and the fix pays only
  when it is the row's LAST fault (CLAUDE.md §2).
* The reparse gate holds: `identical == n_pairs` on the unmodified parser.

**Keep rule (binding).** Keep iff `would_break == 0` on all three splits. A
pure correctness fix needs no positive `would_fix` to be kept: it also makes
such rows renderable as training targets
(`targets._verified` currently refuses them).

## Result (2026-10-07) — KEPT

`--reparse-check` with the fix, against the same command's baseline on the
unmodified parser:

| split | n_pairs | identical | would_fix | would_break |
|---|---|---|---|---|
| train (`r3-trainpredict`) | 880 | 859 → 859 | 6 → 6 | 0 → 0 |
| dev (`r5-control`) | 207 | 207 → 207 | 0 → 0 | 0 → 0 |
| test (`r5-controltest`) | 166 | 166 → 166 | 0 → 0 | 0 → 0 |

Train's 6 are the earlier Ø-zone fix (those dumps predate it), unchanged by
this one. **No matched row in any split was read with two "+" tolerances**, so
the fix moves no current metric (`would_fix` 0, below the predicted 0-3; the
keep rule needs only `would_break == 0`, which holds everywhere). What it
changes: such a reading no longer yields a malformed lower tolerance, and
`targets.render_target` now renders a positive lower tolerance with its "+"
("5 +0,3 +0,1"), so that gold shape round-trips as a training target instead of
being refused.
