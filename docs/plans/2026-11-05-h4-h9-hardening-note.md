# H4-H9 hardening pass (2026-11-05, branch `hardening-h4-h9`, not merged)

Closes the agent-implementable items of `2026-10-23-session-handoff.md` §3.3 that
were still open after H1-H3, plus the `verify` warning gap from the 10-30 handoff.
Each item was checked against the code first, then built test-first.

| Item | State | What changed |
|---|---|---|
| verify throwaway-id warning | done | `runner.warn_if_throwaway_ids`, shared by `predict` and `verify` |
| H4 campaign instance scope | done | `SINDRI_CAMPAIGN_ONLY` now 404s document/image/session/batch ids that are not campaign ones (middleware), filters the two list routes, and `read_region` scopes itself; `read_region` has a per-session sliding window (`SINDRI_READ_REGION_PER_MIN`, default 30, campaign instances only) |
| H5 loader/pull | done, two parts left as designed | `pull` fsyncs the archive and its parent dir; `load_bundle` holds an exclusive flock so two loaders cannot double-insert |
| H6 review-time fidelity | done | sealed revisions carry `raw_timing` (seq/type/t_ms of every event, carried across seals) and `approve_t_ms` (journal clock sent with the seal); the grader uses them via `sealed_review_time_ms` and falls back to net events for older revisions. Still a lower bound (reload gaps, idle cap) |
| H7 digest scanner | done | `app/eval/digest_scan.py`; a test scans every committed `docs/eval/*-grade.json` |
| H8 UI backlog | done (follow-up commit) | real-browser tests in `tests/browser/`; see the H8 section |
| H9 `--split` | done for the detectable flag | score's `--split` defaults to `None` (read as dev), so `--split dev` with `--reviewer-grade` is refused. `reweight`'s per-row ratio weighting stays an approximation, as documented |

## Left as designed, not bugs

- A container restart DURING a load can delete a half-copied drawing: the startup
  sweep removes marked, rowless dirs on purpose; re-running the load is the remedy.
- A later ordinary import of the same bytes shows as a duplicate of the campaign
  copy: `duplicate_of` is the same-SHA rule, and a campaign copy is the same drawing.

## H8, as built (real browser, found by reproducing first)

Both quirks only exist in a layout engine, so they are covered by
`tests/browser/test_review_ui.py`: Chromium over a live campaign-only server
and a synthetic bundle. The file skips where `playwright` or a Chromium build is
missing (set `SINDRI_CHROMIUM`; run with an interpreter that has Playwright).

1. **Wheel-zoom during a balloon drag.** The journaled position was never wrong
   (identical with and without the wheel), but zoom re-rendered every marker
   from state, so the dragged balloon jumped back to its stored place until
   release. Fix: `placeDraggedMarker` puts it back under the pointer after the
   zoom (`viewer.js`). The earlier guess that the release position was off was
   wrong; measure before fixing.
2. **First click after Enter only "dismissed" the card.** The cause is not a
   dismiss handler: the persistent reason card sits under the edited cell and
   physically covers the NEXT row's cells, so the click landed on the card.
   Fix: the card lets clicks through except on its chips and close button
   (`components.css`).

## H6 wire change

`POST /api/session/{id}/seal` takes an optional `t_ms`; `finish.js` sends
`journal.elapsedMs()`. Old clients omit it and get `approve_t_ms: null`.
