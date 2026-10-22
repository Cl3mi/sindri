# Client review campaign C2: grading and findings report — design

Status: DESIGN, approved with the operator on 2026-10-22 (brainstorm). It refines
§5-§6 of `2026-10-21-client-review-campaign-design.md`, using the C1 build
(`2026-10-21-campaign-c1-review-plan.md` and its `.state.md`, "What's next").

## Decisions

| question | decision |
|---|---|
| deliverable | a counts-only digest plus a ranked findings table in `docs/eval/` (agent-safe, what the improvement plan is built from), and an operator-only drill-down page with crops of the top examples per bucket |
| truth | the client's review is primary. Gold is a cross-check that reports disagreements and never flips a grade |
| entry point | a mode of the allowlisted `score`: `score --reviewer-grade <pulled tar>`. The guard is not widened |
| drill-down | `runner campaign-deck`, operator-only like `review-serve`, not in the agent allowlist |
| code split | a pure per-session grader in product code (`app/review/grading.py`, reused by HITL phase 3), and everything gold-, corpus- and campaign-specific in `app/eval/campaign_grade.py` |

## Inputs

- The pull tar (`app.campaign.pull`): `<campaign>/<dir>/{header.json,
  proposal.json, events.jsonl, sealed/rN.json}` plus `index.json` (documents
  with `final_revision`, and `skipped`). It is read in place; nothing is
  extracted.
- Gold (`--gold`), joined through `Anonymizer().mapping(gold ids)`, which maps
  `doc_hash` to `doc_id`.
- The selection manifest (`--manifest`): roles per hash, plus the counts of the
  pool that was eligible (`pool`), which share_select now records.

## Grading (per approved drawing; drawings without a `final_revision` are *pending*)

The grader works from `sealed/r{final_revision}.json` (rows + net_events), never
from `events.jsonl`. It gives each proposal row exactly one grade:

| proposal row | outcome | grade |
|---|---|---|
| balloon | unchanged | `confirmed` |
| balloon | a value field differs | `edited` + field list + last edit reason |
| balloon | deleted | `phantom` + delete reason |
| suggestion | confirmed, values unchanged | `suggestion_confirmed` |
| suggestion | confirmed, values changed | `suggestion_edited` |
| suggestion | deleted | `suggestion_deleted` (the drop was right) |
| suggestion | still a suggestion | `suggestion_left` |

Final rows that are not in the proposal are `missed`, a lower bound.

**Placement:** for every proposal row that survives, the bubble displacement in
px and the anchor IoU (proposal box vs final box). In mm:
`px / render_scale * 25.4 / 72`. A bubble counts as moved when the
displacement is > 0.5 mm. An anchor change counts as *nudged* when IoU ≥ 0.3,
otherwise *repointed*.

**Ungradable:** a sealed revision with `replay_mismatch_ids`, or one whose
replay(proposal, net_events) disagrees with its rows. It is reported and never
dropped.

## Metrics (per group great / problem, and pooled)

The pooled values are reweighted to the pool's great:problem ratio and labelled
that way. Per group:

- precision = confirmed / (confirmed + edited + phantom)
- unflagged precision: the same, restricted to rows that were not flagged
- flag precision = (edited + phantom among flagged) / flagged
- recall lower bound = surviving balloons / (surviving + missed)
- tray accuracy: suggestion outcome counts
- review time per drawing: the sum of `t_ms` gaps within a session from
  `events.jsonl`. A gap > 5 min counts as idle, and a decrease starts a new
  session.
- placement: the share of bubbles moved; median and p90 of the displacement in
  mm; the share > 5 mm; anchors nudged and repointed

## Gold cross-check (does not change a grade)

Proposal balloons are matched to scored gold with `match_candidates` (centres in
points via `render_scale` and `page_rect`). Each proposal row gets a gold
verdict: `gold_correct`, `gold_wrong` or `gold_phantom`. Crossing that verdict
with the client grade gives:

- **reviewer reliability:** gold_wrong & confirmed
- **beyond gold:** gold_phantom & confirmed
- **client vs gold:** gold_correct & edited

Separately, final rows are matched to gold, and gold rows matched by no final
row are counted as *neither found*.

## Ranked findings

There is one table per kind:

- **value** buckets: `fields:<signature>` edits, `phantom:<reason|none>`,
  `missed`, and suggestion outcomes;
- **placement** buckets: `bubble_moved`, `anchor_nudged`, `anchor_repointed`.

Each row gives the count, the share within its kind and the number of
drawings. A static map then gives the lever and its status (design §6).

## Reconciliation (asserted; a failure marks that drawing or the run, never silently)

- The grades partition each drawing's proposal rows.
- Every manifest hash appears in the index, among the skipped drawings, or as
  `not_in_records`.
- Group counts equal the manifest roles.

## Outputs

- The full report (per-row, values) goes to `--out` and must be inside the
  protected root. It is client data.
- The digest (counts and salted hashes, namespaced keys, notes only as a count)
  goes to `--grade-out` under `docs/eval/`.
- stdout gets one count line.

## Operator drill-down

`runner campaign-deck --report <full report> --pdfs <originals>` serves on
localhost with a session token (the review_server pattern). Per bucket it shows
the top examples: a crop rendered from the original drawing in PDF points, the
proposed and final values, the reason and the drawing's note.

## Out of scope

Any client-facing report, automatic recalibration, and live grading on the VPS.
