# State: Batch B1 — Core (queue, worker, resume, server-side export)

Plan: `docs/plans/2026-10-07-batch-b1-core-plan.md`
Milestone: ui (task 11 of 11) — B1 COMPLETE; next is the B2 (import) plan, on the operator's go

## Completed task IDs
- Task 1 — data dir + migrated SQLite index (`ce10e56`)
- Task 2 — document lifecycle repo (`2bf93b9`, `2693024`, `a4696d3`)
- Task 3 — ReviewStore.open() / revision() (`3d7715e`, `278b22c`)
- Task 4 — extraction worker (`e5f4110`, `a75da2d`, `63c7c06`)
- Task 5 — Runtime + API on the queue + `/open` (`348f7d0`, `ffff519`)
- Task 6 — journal drives the drawing's state (`bae5564`, `12b52b1`)
- Task 7 — export only from a sealed revision (`3d640c5`, `56ede9e`, `d98812b`)
- Task 8 — rebuild a review in the browser, resume parity (`baf7ccc`, `c8e988b`)
- Task 9 — journal mandatory in the client (`00a2e00`, `2d351d8`)
- Task 10 — interim UI: queue, wait, open, `#doc=` resume (`4cb05b7`, `b8c2c51`, `8356090`, `7645626`)
- Task 11 — compose data dir + CLAUDE.md (`b5532af`); this checkpoint

## What's done
B1 is complete. Extraction runs in the background worker with no browser
attached; a drawing reopens from `#doc=<id>` exactly where the reviewer
stopped (proposal + net journal replayed through state.js, undo restored,
numbering from `last_seq + 1`); the latest tab wins; Finish seals and exports
server-side from the sealed revision. Every task passed a spec review and a
code-quality review (Opus for Tasks 2-5 and every data-integrity or client
task); every reviewer-reproduced bug is fixed with a regression test.
Verified: suite **1483 passed, 2 skipped** (node tests inside pytest: 84);
guard **32 passed, 0 failed**.

**Real-browser drive (Task 11 Step 4; throwaway, scratchpad, stub backend with
a 5 s extract delay; node playwright-core + cached chromium): 5/5 PASS.**
1. Upload → confirm → Start: overlay "Extracting … stub delay", 33 rows loaded,
   URL `#doc=…`.
2. Edit + accept, reload → "Review restored (2 changes)", both present; Undo
   reverts the accept and keeps the edit.
3. Same link in a second tab + edit → first tab's next edit shows "Opened in
   another tab" (server 409 StaleWriter).
4. Finish in tab 2 → "Review sealed as revision r1", downloads `sample.xlsx`
   and `sample_ballooned` (pdf); document `finished`, `final_revision` 1,
   `exported_revision` 1.
5. Second drawing: Start, tab closed while `extracting` → API shows `ready`
   with no tab open; reopening the link loads it.

## What's next
Nothing in B1. Do not push; do not start B2 until the operator says so. B2
(chunked upload, ZIP/folder, duplicates, scope badges, staging API) gets its
own plan, written just in time from the design.

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
- **Resume (Task 8):** `restoreSession` builds every op before touching the
  session (a bad event leaves the previous drawing intact); `journal.resume`
  refuses a non-integer/negative `last_seq`; the parity fuzz asserts
  contiguous post-reload numbering, a post-seal-shaped reopen and an undo of a
  restored op.
- **Client API (Task 9):** downloads read RFC 5987 `filename*` first; a
  non-string error `detail` (FastAPI 422) reads as "request rejected (422)";
  errors carry `status`.
- **Interim UI (Task 10) — beyond the plan:** new `reopen.js`
  (`restoreReview`, `flushBeforeLeaving`, `afterFlush`) and `stop.js`
  (`nextStopStep`) so the ordering is node-tested. Before opening another
  drawing the current journal is flushed (confirm "leave anyway" beacons);
  edits made while `/open` is in flight are beaconed with the old writer.
  `/open` also returns `sealed_seq` and `finisher.seed()` makes a no-edit
  Finish after a reload re-export instead of sealing a redundant revision;
  `createExporter` skips an already-downloaded kind on retry. Stop = cancel
  (re-sent until accepted) then DELETE, waiting out 409 while the worker
  unwinds, giving up visibly with Close after 5 other failures; DELETE 404 =
  done. Upload is refused while a drawing waits, stops, resumes or opens. A
  drawing that becomes ready behind an unsaved review waits (toast shows its
  `#doc=` link) and opens once the journal drains, on close, or immediately
  if another tab took the writer. `#doc` is cleared only on a 404.
- **Task 11:** the plan had the implementer edit this state file; per the
  operator it was written by the controller instead, in its own commit.
  CLAUDE.md's REVIEW CAPTURE paragraph was corrected for the mandatory
  journal; local dev must set `SINDRI_DATA_DIR`.
- **Known, left for B3:** no `hashchange` listener; polling continues while
  the tab is hidden; a ready drawing switches the screen automatically; stale
  detection matches the server's message text; a flush that is only partly
  acknowledged with nothing waiting is retried on the next edit, not by timer.
  Lock dict for exports grows by one small lock per (drawing, revision, kind).
- Known and deferred: `mark_exported` / `events_appended` are silent on an
  unknown id (API checks existence); retry-from-cancelled is safe only with a
  single worker; `mark_finished` doesn't enforce monotonic revisions; cancel
  latency can be ~10 min because detection emits no progress (UI copy must not
  promise an immediate stop).
