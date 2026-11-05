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
| H8 UI backlog | OPEN | needs a real browser (see below) |
| H9 `--split` | done for the detectable flag | score's `--split` defaults to `None` (read as dev), so `--split dev` with `--reviewer-grade` is refused. `reweight`'s per-row ratio weighting stays an approximation, as documented |

## Left as designed, not bugs

- A container restart DURING a load can delete a half-copied drawing: the startup
  sweep removes marked, rowless dirs on purpose; re-running the load is the remedy.
- A later ordinary import of the same bytes shows as a duplicate of the campaign
  copy: `duplicate_of` is the same-SHA rule, and a campaign copy is the same drawing.

## Why H8 is still open

Both quirks live in `viewer.js` / the reason popover and only show in a real
browser; the node tests reach pure modules only. Shipping a UI change without a test
that failed first would break the project's TDD rule, and Playwright is not
installed here (only a cached Chromium). Fix when a browser harness is available:
(1) wheel-zoom during a balloon drag: `dragging.lastView` is a viewport point from
before the zoom, so `viewToPlan` on release uses the new transform; re-take it from
the pointer on release. (2) the first click after Enter-to-commit only dismisses the
popover: find what still holds it open after the commit.

## H6 wire change

`POST /api/session/{id}/seal` takes an optional `t_ms`; `finish.js` sends
`journal.elapsedMs()`. Old clients omit it and get `approve_t_ms: null`.
