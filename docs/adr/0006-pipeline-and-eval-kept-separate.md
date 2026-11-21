# 0006. `app/pipeline` and `app/eval` are deliberately separate, and the shipped product never imports eval

Status: Accepted
Date: 2026-07-14 (eval plan); 2026-10-07 (product-boundary test)

## Context

The project is a measurement campaign as much as a product: dozens of arms priced
against client gold, with comparability guards (`MatchParams`, `splits_hash`,
`_check_comparable`) so that a delta means what it says. Pipeline internals move under
tuning (crop knobs, prompts, rules, parser), and a scorer that reaches into them would
silently change what "correct" means between runs.

A second force arrived with the review product: `app/review/`, `app/batch/` and
`app/campaign/` ship to the client and run on the client's machine or the hosted
instance. The eval package holds gold-handling code built around the client's corpus.

## Decision

Keep `app/pipeline/` (the product) and `app/eval/` (the harness) separate:

- Eval does not import pipeline internals that move under tuning. Where it needs the same
  logic it copies it locally with a comment, e.g. the numeric token shape in
  `app/eval/score.py` ("kept local so the eval package never imports pipeline internals
  that may move under tuning"). The public exceptions are narrow and named, such as
  `render.effective_dpi()`, which probe asks and `app/eval` must not copy.
- The shipped packages never import `app.eval`. `tests/review/test_product_boundary.py`
  parses every module in `app/review`, `app/batch` and `app/campaign` with `ast` (no import
  needed) and fails on any `app.eval` import. `app/auth.py` is deliberately a root-level
  module outside both.
- Product code that eval needs to *reuse* lives in the product: `app/review/grading.py`
  is product code so HITL phase 3 can grade in-house reviews with the same function.

## Alternatives considered

The documents do not record a rejected design for this boundary, only the rule itself.
Obvious alternatives, reconstructed: share a single scoring-and-parsing module between
both (rejected by the rule above, because a tuning change would shift old comparisons);
or put eval inside the product package (rejected by the distribution concern in the test's
docstring).

## Consequences

Easier: an eval result stays comparable across arms; the product's dependency surface is
small and does not carry the gold-handling code; the boundary is a structural guarantee,
checked on source rather than behaviour.

Accepted costs:

- **Duplication.** Parts of parsing/normalisation logic exist in both trees and can drift.
  The remedy used is tests that pin them together (e.g. `tests/test_place_overlap.py` pins
  balloon radius duplicated from `ballooned_pdf`). The HITL design also planned a
  product-side value normaliser pinned against `app/eval/normalize`; it was not built.
  The shipped grader (`app/review/grading.py`) instead compares the four value fields by
  exact string equality, so "edited" is the reviewer's act, not a normalised comparison.
- Eval is *blind* to anything the pipeline changes without a recorded knob: recorded
  `RunConfig.extra` fields (flag/drop rules, crop knobs against a FROZEN baseline) are
  what keep dumps comparable.
- `reparse.py` prices a parser change on the prediction path only; a parser edit also
  decides which gold rows are renderable as training targets, which the boundary makes
  invisible to it.

## Lessons

- Comparing against the *current default* instead of a frozen baseline would make a pad-24
  run look identical in `RunConfig` to every pad-6 dump on disk and `_reusable_dump` would
  reuse them across the change (`active_crop_knobs`, `_BASELINE_CROP_KNOBS`).
- The boundary enables the useful trick of re-pricing a rule from stored dumps in CPU
  seconds (`--reapply-policy`, `--policy-check`), which was exact to the decimal against
  native GPU runs (see [0003](0003-flag-and-drop-by-gold-free-policy-stages.md)).
- Related: [0007](0007-counts-only-digests-and-hardened-outputs.md).
