# State: Batch B3 — UI (router, work list, staging, review bar, resume, tabs, banner)

Plan: `docs/plans/2026-10-08-batch-b3-ui-plan.md` (written `eecdd2c`, revised
after a plan review `ef12d95`; one review finding carried as an execution
requirement for Task 13, below)
Milestone: staging-review-bar-banners (task 15 of 16) — next is Task 16
(CLAUDE.md + real-browser drive), milestone docs-and-e2e

## Completed task IDs
- Task 1 — migration 4: nullable flagged_total/flagged_open/unchecked (`153dfdc`)
- Task 2 — ReviewStore.progress()/has_events() (`1af5080`)
- Task 3 — has_events counts sealed revisions (`3f31e5c`); Repo update_progress / move_to_front / reextract (`38dd524`)
- Task 4 — worker rolling-median ETA + progress at ready (`0f5823a`, `3ea2317`)
- Task 5 — Repo list_documents / queue_summary (`f575447`, `bb57308`)
- Task 6 — GET /api/documents + queue-control routes (`6d5f2d8`, `2112c66`, `c9764a6`)
- Task 7 — router.js incl. legacy `#doc=` links (`9c78cab`, `4983212`)
- Task 8 — worklist.js pure helpers (`9a68525`)
- Task 9 — api.js work-list calls + badges.workListBadges (`e628665`)
- Task 10 — work-list screen (`84168f4`, `6c1f370`)
- Task 11 — reviewbar.js pure navigation (`fd478cc`)
- Task 12 — staging view (`30ec03a`, `96c69d8`, `2c94ada`)
- Task 13 — router wiring, review bar DOM, jump palette (`6bad1bf`, `d4a0b5d`, `db72339`)
- Task 14 — two-tab read-only banner (`9bbaba0`)
- Task 15 — lagging-journal banner with retry countdown (`d5ee9b3`)

Node tests: 240. Suite: 1850 passed, 2 skipped. Guard 32/0.

## What's done
Server side of B3: a work-list read path (stable rowid order, inclusive
since-cursor taken before the query minus 5 s, lazily persisted progress
counts), queue summary with a rolling-median ETA, move-to-front, pause/resume,
and a re-extract that can never discard human review work or strand a drawing
without its result. Verified at Task 6: suite **1850 passed, 2 skipped**;
guard 32/0. Opus subagents are on a weekly limit until 2026-10-13, so B3 runs
on Sonnet implementers and reviewers.

## What's next
Finish milestone router-and-worklist (Task 10 review), then Tasks 11-15 and the
docs + E2E task.

## Decisions / deviations since the plan was written
- **Plan review fixes (before execution):** programmatic navigation must call
  the router itself (`router.navigate(url, {setUrl, dispatch})`, main.js
  `go()`), because `history.replaceState` never fires `hashchange`;
  progress mirrors `state.js` exactly (a confirmed suggestion counts by its own
  `needs_review`); list order is `ORDER BY rowid` (stable), `updated_at` only
  filters the since-cursor; the review bar is hidden for drawings that are not
  reviewable; full refresh every 20th poll (deletions are invisible to the
  cursor).
- **EXECUTION REQUIREMENT for Task 13 (from the plan re-review):**
  `routeFromHash`'s re-entrancy guard must QUEUE the latest navigation, not drop
  it — `if (navigating) { rerouteRequested = true; return; }` with a
  `do { … } while (rerouteRequested)` loop that re-reads the hash each pass;
  and `await stagingView.load(...)` in the staging branch.
- **has_events()** is True for any journal entry OR any sealed revision (a
  session sealed with zero net events must still count as reviewed).
- **Worker ETA:** median of the last 20 successful extraction wall times
  (monotonic clock), `eta_seconds(queued)` = median × queued (cumulative), None
  until a sample exists; the sample buffer is lock-guarded (API threads read
  it). Progress is persisted at ready and never fails the drawing.
- **list_documents:** `since` is INCLUSIVE (`updated_at >= since`); LIKE search
  escapes `%`/`_`; the route takes the cursor from the repo clock BEFORE the
  query minus 5 s; clients merge by id.
- **Re-extract (route):** refused if the worker is on it, if not `ready`, or if
  reviewed — the check and the transition run inside
  `ReviewStore.unless_reviewed(sid, fn)` under the STORE lock (so a concurrent
  append cannot slip between them; lock order store → repo, never reversed).
  `result.json` is renamed to `result.reextract.json` first and restored on ANY
  failure (`except BaseException`); after a successful transition the stash is
  deleted, so the worker re-extracts from scratch. A crash mid-way is healed by
  `Runtime._recover_reextract_stashes` in the new
  `Runtime._run_startup_recovery()` (worker startup hook; each recovery step
  guarded separately — **B4's interrupted-export recovery must be added there**).
- **Progress recompute** after `/events` and `/seal` catches
  `(ReviewError, OSError, ValueError, KeyError, TypeError)` and never fails the
  committed request.
- **router.js** also maps legacy `#doc=<id>` bookmarks (B1/B2) to the doc route.
- **Work list (Task 10):** every server string rendered via textContent (the
  plan's sample used innerHTML); aria-labels on search, filters and
  checkboxes; bulk actions normalise thrown AND `{ok:false}` results
  (`deleteDocument` never throws) and report per status, 409 → "still
  extracting — cancel it first"; selections pruned on every render; one
  refresh in flight at a time (a second request re-runs as a full reload);
  folder import via a separate `webkitdirectory` picker (isImportable filter,
  batchNameFor, relPathOf); post-import navigation uses `replaceState` until
  Task 13 provides `go()`; both screens are hidden until Task 13 wires the
  dispatcher (a blank body in between is expected).
- **Staging view (Task 12):** rows built with textContent (the first version
  interpolated names into innerHTML — fixed); unfinished uploads listed with
  Remove upload; `startBlocker` mirrors the server's start refusals; Start is
  disabled before its await and sends `expected_ids`; 409 → reload with
  "already started" vs "import changed" copy; refused removals are toasted
  (`removalMessage` — the api's delete calls never throw); Add files / Add
  folder into the open draft with live progress and a run-wide Cancel
  (`importFiles` has one signal per run) that deletes the cancelled uploads
  (matched by name+size — narrow risk: another tab's identical file in the same
  draft); empty-archive message; aria-labels.
- **Router wiring (Task 13):** `go()` dispatches; routing serialised by the
  tested `createSerialRunner` (queues the latest, never overlaps, releases on
  error); leaving a drawing awaits `flushBeforeLeaving` ("stay" restores the
  hash without re-opening); re-selecting the drawing already open is a no-op
  (`shouldReopen`) and the Jump palette excludes it — a re-open would rotate
  the writer and drop unflushed events; Prev/Skip/Jump and any doc navigation
  are blocked while a Finish is sealing; after Finish: next drawing, or "Next
  drawing ready in ≈ N min" (per-drawing estimate, `nextReadyCopy`), or "Batch
  complete"; in-page Jump palette (Ctrl+K); batch-of-one auto-open re-checks
  before navigating.
- **Banners (Tasks 14-15):** a persistent stale banner (with the unsaved count)
  replaces B1's toast and makes the tab read-only via `state.readOnly`, gated
  in `apply`/`undo`/`redo` (every edit path incl. keyboard shortcuts); a
  lagging banner counts down to the real scheduled retry, has Retry now, and
  hides when the journal catches up; both are cleared when another drawing
  opens or the drawing closes.
- **Review bar (Task 11):** `skip()` falls back to another batch's reviewable
  drawing when the current batch has none left (same as after Finish). Task
  13 renders `nextAfterFinish`'s `extracting` status as "Next drawing ready in
  ≈ N min" from the queue's per-drawing estimate.
