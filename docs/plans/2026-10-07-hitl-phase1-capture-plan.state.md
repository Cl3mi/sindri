# State: HITL Phase 1 — Capture + Finish

Plan: `docs/plans/2026-10-07-hitl-phase1-capture-plan.md`
Milestone: store (task 4 of 11)

## Completed task IDs
- Task 1 — journal primitives (`8d8466a`, `051e14d`)
- Task 2 — replay + mismatches (`24a479d`)
- Task 3 — session header (`1500c05`, `8ae51b1`)
- Task 4 — ReviewStore (`8e97074`, `4483672`, `9f94b4d`, `ea7423b`)

## What's done
`app/review/` is complete and pure-Python tested (55 tests in `tests/review/`;
full suite 1348 passed, 2 skipped). Each task passed spec review and code-quality
review; Task 4 went through an Opus review that reproduced two data-loss bugs in
the plan's own store code, now fixed with regression tests.

## What's next
Milestone group 2 — the API: Task 5 (store from env, proposal at extract, health
flag) and Task 6 (`/events`, `/seal` endpoints).

## Decisions / deviations since the plan was written
- `validate_event` also refuses a `retract` whose `target` is missing, not an
  int, or not below its own seq (append-time gate, traceable to one request).
- Store hardening beyond the plan: journal lines are written ASCII-only and split
  on "\n" only (U+2028/U+0085 in a cell value used to brick a session); the torn
  tail is repaired on raw text (a complete line missing its "\n" is kept); every
  ack is fsynced, renames are fsynced at the directory; a `threading.Lock` guards
  create/append/seal (single-worker assumption); lines loaded from disk are
  validated (corruption → 422, not 500); writer-token edge cases → StaleWriter.
- **`create()` now REFUSES to re-create a session whose journal is non-empty**
  (was: wipe any unsealed session). Task 5's extract wiring already catches
  `ReviewError` and falls back to `writer=None` (logging off for that session),
  which is the intended behaviour for a re-extraction after review started.
- `seal` refuses a non-int or regressive `final_seq`.
- Deferred to Phase 2 (do NOT build in Phase 1): writer-token rotation /
  `open(sid)` (a second-tab path must never reach for `create()`, which would be
  refused anyway); cross-process `flock`.
- Known, accepted: a retried Finish at the same `final_seq` mints a duplicate
  revision (grading takes the latest — note for Phase 3); `ReviewStore` commits
  carry a `Claude Sonnet 5` trailer (accurate authorship of subagent commits).
