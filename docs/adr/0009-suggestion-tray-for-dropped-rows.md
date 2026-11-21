# 0009. Rows removed by drop stage 2 and later are kept as unexported suggestions in the reviewer UI

Status: Accepted
Date: 2026-10-07

## Context

Drop stage 2 (`conf_below_099 + material_kind + tight_cluster`) raised delivered
precision (dev 0.419 → 0.526, test 0.536 → 0.958) by deleting rows that would otherwise
ship unflagged. The price was that 29 correct values per split vanished: neither delivered
nor shown for review (`2026-10-06-phantom-drops-result.md` §5, open item 3). The operator
wanted them back in front of the reviewer without giving up the precision gain.

Constraints: the client ranks precision over recall, so an unconfirmed value must never be
exported; the scorer must keep measuring exactly what shipped; and the existing review UI
should not grow a second panel.

## Decision

Rows dropped by stage 2 **and any later stage** go to `ExtractionResult.suggestions`, not
`characteristics` (stage-1 `contained_duplicate` rows still vanish — a duplicate of a
balloon is not a suggestion). They appear in the existing table and on the drawing in
violet (`◌ –`, dashed `?` balloon), edited inline as usual, behind a fourth filter pill
"Low confidence" (key `4`). A suggestion is confirmed by Accept or `Y` **only inside that
filter**, and is **never exported unconfirmed** — the UI and the server both filter.

## Alternatives considered

- **Keep the rows deleted** — the first shipped behaviour; it traded 29 correct values per
  split for precision with no way to recover them.
- **A separate panel for dropped rows** — rejected by the operator from the mockups in
  favour of reusing the table and drawing (`2026-10-07-suggestion-tray-design.md`).
- **Amber, as for "needs review"** — amber already means "needs review, but exported", so
  violet marks the different state "not exported until confirmed".
- **`Y` confirming suggestions on every filter** — rejected so one keystroke on the default
  view can never export unchecked values.
- **A VLM verifier as a stage 3** — closed at train selection (see
  [0003](0003-flag-and-drop-by-gold-free-policy-stages.md)); had it shipped, the tray would
  have taken it automatically, since it accepts every stage from 2 on.

## Consequences

Easier: the precision numbers stay exactly as measured until a person confirms a value;
the reviewer recovers lost recall; re-scoring `r5-control` / `r5-controltest` through the
tray code was identical on all 40 digest keys; and the tray's outcomes are gradable
(`suggestion_confirmed` / `_edited` / `_deleted` / `_left`), so the campaign can answer
whether stage 2 is right in production.

Accepted costs:

- **Two row populations in one list.** `renumber()` numbers non-suggested rows only;
  suggestions have `pos = 0`; rows ride in the same payload flagged `suggested`.
  `reapply_current_code` must start from `characteristics + suggestions`, or offline
  re-pricing would silently differ from what the pipeline did.
- **A subtle UI invariant** — `Y` on All/Review/OK must leave suggestions untouched; this is
  pinned by node tests (`tests/js/`).
- **Defence in depth** — the server filters `suggested: true` even if the client sent it, so
  a client bug cannot export unchecked values.
- Reviewers see extra rows; the campaign reports tray outcomes separately so they are not
  mistaken for baseline rows.

## Lessons

- A measurement-only change can still ship product behaviour: the tray changed nothing the
  scorer reads, so no GPU run was needed, only an identity re-score.
- The review-grade logic ([0002](0002-journal-and-sealed-revisions-as-source-of-truth.md))
  treats suggestions as their own grade class; that came from this design's insistence
  that suggestions never count as delivered, matched or false.
