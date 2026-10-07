# State: HITL Phase 1 — Capture + Finish

Plan: `docs/plans/2026-10-07-hitl-phase1-capture-plan.md`
Milestone: phase 1 complete (task 11 of 11) — next is writing the Phase 2 plan

## Completed task IDs
- Task 1 — journal primitives (`8d8466a`, `051e14d`)
- Task 2 — replay + mismatches (`24a479d`)
- Task 3 — session header (`1500c05`, `8ae51b1`)
- Task 4 — ReviewStore (`8e97074`, `4483672`, `9f94b4d`, `ea7423b`)
- Task 5 — store from env, proposal at extract, health flag (`40737d6`, `72f92fb`)
- Task 6 — `/events` and `/seal` endpoints (`bf3b1b2`, `9e4727a`)
- Task 7 — ops describe themselves, `op` bus (`274c2e8`)
- Task 8 — `journal.js` (`faf44e9`, `0783df0`, `efc0121`, `209b439`)
- Task 9 — `progress()` / `canFinish()` (`a3324d4`)
- Task 10 — Finish & Export (`6563c86`, `d001b7b`, `4e81419`)
- Task 11 — compose env + CLAUDE.md (`504eb4f`)
- Final-review fixes (`932e8da` server, `b152793` client)

## What's done
Review capture works end to end: the server writes header + proposal at
extract, the UI journals every operation (numbered, undo as retraction,
idempotent acked flush with backoff and a pagehide/close beacon), and a
single-flight Finish & Export seals a revision after replaying the net journal
on the server-held proposal. Verified: suite 1388 passed, 2 skipped; guard 32
passed, 0 failed; real-browser drive of all five plan checks (incl.
double-click Finish → exactly one r1). Every task passed spec + quality review;
store, journal and the whole phase had Opus reviews whose reproduced bugs are
fixed with regression tests. A fixed-seed fuzz (`tests/review/
test_journal_replay_parity.py`) guards the JS↔Python replay contract.

## What's next
Write the Phase 2 plan (forcing UX: review queue, per-row accept, blind panel +
audit sampling in the header, reasons, unsure) from the design doc §1, then
execute it the same way.

## Decisions / deviations since the plan was written
- `validate_event` checks each type's fields at append time (incl. `edit_cell`
  field restricted to the four value fields — a cell edit can never rewrite
  review state) and refuses a retract whose target is missing or not below its
  own seq; a bad event is a 422 traceable to one request, never a 500 at seal.
- Store hardening beyond the plan: ASCII-only journal lines split on "\n" only;
  torn-tail repair on raw text; fsync before every ack and on renames;
  `threading.Lock` around create/append/seal (single-worker assumption);
  loaded lines validated; seal catches KeyError/TypeError as a backstop for a
  corrupt proposal; writer-token edge cases → StaleWriter.
- `create()` refuses to re-create a session whose journal is non-empty; the
  extract wiring then degrades to logging off for that session.
- `seal` refuses a non-int or regressive `final_seq`.
- Review capture at extract catches ANY exception (logs exception type only)
  — a capture fault never fails an extraction. `/api/health` probes that the
  store is actually writable.
- Tests: autouse fixture keeps `app.main._REVIEW_STORE = None` unless a test
  opts in; `tests/review/test_product_boundary.py` forbids `app.review` from
  importing `app.eval`.
- Journal client: generation counter ignores replies from a previous session;
  flush chains so no event is stranded; session captured at call time;
  malformed ack rejected; an explicit `review_logging: false` reply switches
  logging off for the session (pill shown, Finish exports with revision null).
- Finish is extracted into `app/static/js/finish.js` (single-flight; never
  re-seals an unchanged journal, so a retry after a failed export re-exports
  only). The "jump to first" affordance is the switch to the Review filter.
- Deferred to Phase 2 (do NOT build earlier): writer-token rotation / `open(sid)`
  (a second-tab or reload path must not use `create()`; change the "reload to
  continue" 409 wording then); cross-process `flock`; design §6's persistent
  "log is lagging" banner (Phase 1 surfaces flush failures only at Finish);
  batch cap on very long offline queues.
- Known, accepted: a reload followed by Finish at the same seq, or a lost seal
  reply, can mint a duplicate revision (harmless for replay; Phase 3 grading
  takes the latest). Subagent commits carry a `Claude Sonnet 5` trailer
  (accurate authorship).
