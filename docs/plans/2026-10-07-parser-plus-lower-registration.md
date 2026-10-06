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
