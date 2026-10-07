# State: Batch B1 — Core (queue, worker, resume, server-side export)

Plan: `docs/plans/2026-10-07-batch-b1-core-plan.md`
Milestone: core (task 4 of 11) — next is milestone api (Tasks 5-7)

## Completed task IDs
- Task 1 — data dir + migrated SQLite index (`ce10e56`)
- Task 2 — document lifecycle repo (`2bf93b9`, `2693024`, `a4696d3`)
- Task 3 — ReviewStore.open() / revision() (`3d7715e`, `278b22c`)
- Task 4 — extraction worker (`e5f4110`, `a75da2d`, `63c7c06`)

## What's done
`app/batch/` exists as product code (paths, db, repo, worker) and the review
store can reopen a session with a rotated writer and read a sealed revision.
Nothing is wired into `app/main.py` yet; the old SSE flow still serves the UI.
Every task passed a spec review and an Opus code-quality review; every
reviewer-reproduced bug is fixed with a regression test. Suite: 1447 passed,
2 skipped.

## What's next
Task 5 (Runtime + API switched to the queue + `/open`) — the integration task,
Opus. Then Task 6 (journal drives state), Task 7 (export from a sealed revision),
then the api milestone checkpoint.

## Decisions / deviations since the plan was written
- **Repo `_tx`:** COMMIT sits inside the try and ROLLBACK runs only when
  `in_transaction`; the plan's version left the shared connection wedged in a
  transaction after a failed COMMIT. `claim_next` / `create_document` return
  the row read INSIDE their transaction (SELECT, not RETURNING, for older
  sqlite on GPU images). Timestamps are fixed-width microseconds.
- **Cancel-then-Remove during a run (review finding, controller decision):** no
  `cancelling` state. `repo.delete()` refuses only `extracting`; the worker
  treats a vanished row (`UnknownDocument`) as a cancel, writes nothing for it
  and purges `docs/<id>/` + its review dir if anything was already written.
  The worker exposes `current`; **Task 5's delete endpoint must refuse the
  worker's current job.**
- **Late cancel** (after the last progress report): a cancelled drawing that
  still exists keeps `result.json` (Retry skips the GPU) but gets no review
  record; a removed one gets nothing.
- **Worker lock lifecycle:** a `stop()` whose join times out keeps the thread
  and the flock (the plan's version released it, allowing a second worker to
  re-extract the same drawing); `start()` refuses while that loop lives, uses a
  per-loop stop Event, and is exception-safe after taking the flock.
- **Worker failure paths:** an unparseable `result.json` is deleted so Retry
  re-extracts; an OSError reading it fails the drawing (`review_store:<Type>`)
  and keeps the file; `_settle` logs any other transition error by type.
- **Worker API for Task 5:** use `worker.progress_of(doc_id)` (a copy), not the
  internal dict; `worker.current` for the delete guard.
- **ReviewStore.open():** nothing on disk changes beyond the torn-tail repair
  until net_events + proposal load succeed (the plan trimmed stranded events
  first, losing unacked events on a refused open). A corrupt `proposal.json`
  maps to `JournalInvalid` in open() and seal(). `revision()` refuses bool `n`.
- **For Task 8:** `/open`'s `events` have holes (e.g. net [1, 4], `last_seq`
  6). The client must continue numbering from `last_seq + 1`, never
  `max(events.seq) + 1` (append keeps the first write of a seq, so a reused seq
  is silently dropped). The parity test should cover a post-seal reopen.
- Known and deferred: `mark_exported` / `events_appended` are silent on an
  unknown id (API checks existence); retry-from-cancelled is safe only with a
  single worker; `mark_finished` doesn't enforce monotonic revisions; cancel
  latency can be ~10 min because detection emits no progress (UI copy must not
  promise an immediate stop).
