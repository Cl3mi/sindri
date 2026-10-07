# State: Batch B1 — Core (queue, worker, resume, server-side export)

Plan: `docs/plans/2026-10-07-batch-b1-core-plan.md`
Milestone: api (task 7 of 11) — next is milestone ui (Tasks 8-11)

## Completed task IDs
- Task 1 — data dir + migrated SQLite index (`ce10e56`)
- Task 2 — document lifecycle repo (`2bf93b9`, `2693024`, `a4696d3`)
- Task 3 — ReviewStore.open() / revision() (`3d7715e`, `278b22c`)
- Task 4 — extraction worker (`e5f4110`, `a75da2d`, `63c7c06`)
- Task 5 — Runtime + API on the queue + `/open` (`348f7d0`, `ffff519`)
- Task 6 — journal drives the drawing's state (`bae5564`, `12b52b1`)
- Task 7 — export only from a sealed revision (`3d640c5`, `56ede9e`, `d98812b`)

## What's done
The server side of B1 is complete: uploads stage a document, the background
worker extracts it, `/open` resumes it with a rotated writer, events/seal move
it through in_review/finished, and exports render server-side from the sealed
revision (cached per revision under a lock). Every task passed a spec review
and an Opus code-quality review; every reviewer-reproduced bug is fixed with a
regression test. Suite: 1481 passed, 2 skipped. **The browser UI is broken
from Task 5 until Tasks 9/10** (it still calls the removed SSE / browser-row
export routes) — that is the plan's sequencing.

## What's next
Task 8 (rebuild a review in the browser from `/open`, resume parity), Task 9
(journal mandatory in the client, api.js on the new endpoints), Task 10
(interim UI: queue, poll, open, `#doc=` resume), Task 11 (deployment, docs,
Playwright drive against a stub backend), then the ui milestone checkpoint and
the guard re-check.

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
- **API (Task 5, review fixes):** `DELETE` and `/retry` refuse the worker's
  current job (409). `/open` reads `result.json` before rotating the writer;
  missing/corrupt files → 409 "drawing files unavailable". Upload is a sync
  handler; a write fault is 507 (ENOSPC) / 500, only a parse failure is 400.
  `runtime()` is built under a lock and refuses (503) after lifespan shutdown.
  `Runtime` probes the review dir before opening SQLite; `close()` keeps the
  connection while a timed-out worker loop still runs.
- **Journal → state (Task 6):** events/seal for a removed row → 404 before any
  journal write; seal refused (409) unless ready/in_review/finished, checked
  before `store.seal`; `events_appended` receives append's acknowledged
  `contiguous`, not the request's max seq. Known, documented windows: a delete
  between the check and the seal leaves an orphan sealed revision in the kept
  review dir; an edit between `store.seal` and `mark_finished` leaves
  `finished` with an unsealed edit until the next event; a Finish retried after
  `mark_finished` fails writes a second revision.
- **Export (Task 7):** row checked before any path is created (no orphan
  `docs/<id>/`); a gone doc dir, missing/corrupt `result.json` or `input.pdf`
  → 409; any other render fault → 500 "export failed". Cache rN.* rendered
  once under a per-(doc, rev, kind) lock with a unique temp file.
  Content-Disposition is sanitised (control chars, quotes, separators) with an
  RFC 5987 `filename*`. `?rev=0` → 404. `mark_exported` runs on every hit
  (a re-download is an export). The cache is not invalidated if exporter code
  changes.
- **For Task 11:** `Runtime.from_env` defaults to `/data`; document
  `SINDRI_DATA_DIR` for local dev.
- Known and deferred: `mark_exported` / `events_appended` are silent on an
  unknown id (API checks existence); retry-from-cancelled is safe only with a
  single worker; `mark_finished` doesn't enforce monotonic revisions; cancel
  latency can be ~10 min because detection emits no progress (UI copy must not
  promise an immediate stop).
