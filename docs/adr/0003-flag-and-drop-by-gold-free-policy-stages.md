# 0003. Flag and drop rows by gold-free policy rules, applied as ordered stages, with drops routed to a suggestion tray

Status: Accepted
Date: 2026-09-25 (rules); 2026-10-06 (drop stage 2); 2026-10-07 (tray)

## Context

The client ranks precision over recall: every delivered value must be true, and that
matters more than finding every value (operator, 2026-10-05). The review-cost weights
(miss 10 > escaped 5 > false 2) encode the opposite, and the model's read quality was
largely out of reach: the box/crop work showed that input framing dominates read accuracy
and that most reader-side levers were spent (`CLAUDE.md` §2-§3).

What remained was **telling the reviewer which rows to distrust**. Two scoring blind
spots made that hard to judge:

- Flagging more always lowers review cost (flag 1 < escaped 5): flagging every matched
  row cost 223 vs 406 on dev and automated nothing. So cost alone is gameable and
  auto-accept precision and rate were added as guards.
- Matched-only precision hid most of the problem. *Delivered* precision, which also counts
  false detections shipped unflagged ("phantoms"), was dev 0.419 / test 0.536, against
  matched-only 0.805 / 0.684.

## Decision

Post-read, **gold-free rules** in `app/pipeline/policy_rules.py` flag or drop rows, and
only rules that passed the registered protocol ship: selected on train, validated on dev,
confirmed on test, under every guard and 6/6 weightings, predictions registered before
pricing.

- **Flags (active):** `nondim_kind`, `no_tolerance`.
- **Drops are ordered stages** (`ACTIVE_DROP_STAGES`), each judged on what the previous
  left: stage 1 `contained_duplicate`; stage 2 `conf_below_099 + material_kind +
  tight_cluster`, each firing only on a row that would ship unflagged. **Flags precede
  drops**, because stage 2 reads `needs_review`.
- **Rows dropped by stage 2 and later go to `ExtractionResult.suggestions`** (the
  suggestion tray), never exported until a reviewer confirms them. Stage-1 duplicates
  simply vanish. The scorer reads `characteristics` only.

## Alternatives considered

Each was priced and failed (`docs/plans/2026-09-25-policy-arms-result.md` §2):

- Flags `tall_box`, `diameter_sign`, `asymmetric_tol`, `multiline`, `gdt_guessed`
  (a strict subset of `nondim_kind`); drops `empty_read`, `no_digit`, `note_kind`,
  `theoretical_kind`, `repeated_value_nearby`, `theoretical_no_nominal`. `tall_box` cut
  cost 7-11 but flagged 15-61 correct rows (rate guard). Kind-drops are the closed
  `score_kinds` family again.
- OCR proposals for isolated misses: CPU gate 6 of 58 against a registered GO of ≥ 12.
- A yes/no VLM verifier for phantoms: 191 of 196 delivered train values got P(yes) ≥ 0.9,
  including 14 of 16 phantoms (`2026-10-07-verifier-result.md`).
- Deleting dropped rows outright was the first shipped behaviour; the tray replaced it
  (`2026-10-07-suggestion-tray-design.md`).

## Consequences

Measured effect: dev review cost 131.87 → 118.73 with escaped errors 64 → 17 and
auto-accept precision 0.53 → 0.80 (stage 1 + flags); stage 2 raised delivered precision
train 0.577 → 0.888, dev 0.419 → 0.526, test 0.536 → 0.958. Offline pricing is exact:
`--reapply-policy` reproduced `r4-control`, `r5-control` and `r5-controltest` on every
digest aggregate.

Accepted costs:

- Stage 2 cut the auto-accept rate (dev 0.225 → 0.132, test 0.178 → 0.079) and dropped 29
  correct values per split; the tray exists to give those back to a human.
- 30 dev phantoms read at ≥ 0.99, out of reach of any confidence rule. They look like
  real callouts because they likely are: dimensions the client chose not to balloon.
- Stages must stay stages. A single pass would let `tight_cluster` see the duplicate
  stage 1 removes and delete the surviving box — an outcome nobody priced; a test pins it.
- Dev numbers were optimistic: the same policy gives test 149.82 vs dev 118.73.

## Lessons

- **A flag rule keyed on a field being absent is defeated by any model change that fills
  the field.** `read-lora-v1` turned `no_tolerance` flags into silent wrong values
  (+3.33/+4.27 cost under the shipped policy) — price every read-stage arm under the
  active rules.
- **Fixing a read on a row the reviewer was already going to check saves nothing**
  (flagged_error and flagged_correct both cost 1); only converting an escaped error pays.
- **Register the mechanism a gate assumes, not only its threshold** and use a damage
  counter the treatment can move: the registered `no_tolerance` negative control was
  refuted (it flags 1 correct row on train) and was kept because it was honest.
- Related: [0007](0007-counts-only-digests-and-hardened-outputs.md) (how these numbers are
  published), [0006](0006-pipeline-and-eval-kept-separate.md).
