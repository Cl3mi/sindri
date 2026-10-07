# Batch import, async extraction, review later — design

Date: 2026-10-07. Status: APPROVED (operator, section by section).
Builds on: `2026-10-07-hitl-review-grading-design.md` (phase 1 shipped, PR #3).
Precedes: HITL phase 2 (forcing UX + audit sampling), which lands on the
navigation model defined here.

## Goal

Today a drawing lives and dies with one browser tab: `POST /api/extract/{id}`
streams SSE, a disconnect cancels the run and deletes the session, uploads sit
in `/tmp/sindri_sessions`, and nothing serialises GPU use. Extraction takes
~10-20 min per drawing on the GPU host.

The goal is an **asynchronous compute capability with human-in-the-loop review
at a later, uncertain time**: import tens to hundreds of drawings at once, let
one worker extract them in the background with no browser open, then review
them one after another — or in any order — over days, resuming exactly where
the reviewer stopped, and export per drawing or in bulk.

## 0. Operator decisions

| # | Question | Decision |
|---|---|---|
| 1 | Import size and shape | Tens to hundreds of PDFs; added as single files, a folder, or a ZIP; uploads resumable |
| 2 | Users | One person imports and reviews |
| 3 | Identity | None — shared workspace (keeps the HITL "no reviewer identity" rule) |
| 4 | Concurrency | Exactly one extraction at a time, FIFO plus priority |
| 5 | Queue tech | In-process worker thread + SQLite under the data dir; single uvicorn process |
| 6 | Crash policy | Running job requeued at startup; failed after 2 attempts |
| 7 | Retention | Files purged N days after export (default 30, configurable, 0 = off); review records kept |
| 8 | Live updates | Polling every 3 s while visible |
| 9 | Home screen | Work list of all drawings |
| 10 | Start of extraction | Staged first, then explicit "Start batch" |
| 11 | After Finish | Auto-open the next ready drawing |
| 12 | Resume | Exact, from the server (proposal + journal replay, undo stack restored) |
| 13 | Finish | "Finish" seals and moves on; "Finish & Export" also downloads |
| 14 | Mass export | ZIP: per-drawing xlsx + ballooned PDF + `manifest.csv` |
| 15 | Mass-export scope | Finished (sealed) drawings only; others listed as skipped |
| 16 | Reopen | Allowed; next Finish seals a new revision |
| 17 | Single-drawing flow | Unified: one drawing = a batch of one |
| 18 | Queue controls | Cancel/remove, retry failed, move to front, pause/resume queue, **skip for now**, **free jump to any ready drawing** |
| 19 | Duplicates | Content hash; warn in staging, excluded by default, user decides |
| 20 | Purge scope | Files only; review records never purged by this |
| 21 | Resumable depth | Chunked; network/server failures resume at the last chunk; a closed tab resumes when the same files are re-selected |
| 22 | ZIP | Unpacked server-side, PDFs only, path-safe |
| 23 | Hosting | Undecided — optional shared access token, default off |
| 24 | Ordering vs HITL | This first, then HITL phase 2 |
| 25 | Two tabs | Latest opener takes over; the older tab turns read-only |
| 26 | Multi-page / oversized | Warn with "extraction quality may be poor"; excluded unless the user explicitly includes the file |
| 27 | Batch names | Auto (ZIP/folder name or date-time), editable |
| 28 | Re-extract | Only for drawings with no review events |
| 29 | Upload limits | None (path safety and a free-disk guard are not limits) |
| 30 | Journal role | **Mandatory product state** — review logging can no longer be off |
| 31 | Handoff | This design + a detailed plan for B1; B2-B4 plans written just in time |

## 1. Architecture and data model

Approach A (chosen over a filesystem-only index and over moving the journal
into SQLite): **SQLite is the index; blobs stay on disk; the phase-1 review
store is unchanged and keyed by the same id.**

```
browser ──chunked upload──▶ /api/uploads/*      ──▶ /data/docs/<doc>/input.pdf
        ──staging/start───▶ /api/batches/*      ──▶ sindri.db (batch, document, upload, export)
        ──poll 3 s────────▶ /api/documents?…    ◀── worker thread (1, FIFO + priority)
        ──open/review─────▶ /api/documents/<id>/open → proposal + net journal + writer
        ──events/seal─────▶ phase-1 endpoints, unchanged (doc id == session id)
        ──export──────────▶ /api/documents/<id>/export/*, /api/exports (ZIP)
```

* `SINDRI_DATA_DIR` (default `/data`) holds `sindri.db` (WAL), `docs/<id>/`
  (`input.pdf`, `page.png`, `result.json`, `exports/rN.*`), `uploads/`,
  `exports/`. `/tmp/sindri_sessions` goes away.
* `SINDRI_REVIEW_DIR` (default `<data>/reviews`) keeps phase-1 semantics.
* **`document`**: `id` (32-hex, = today's session id), `batch_id`,
  `file_name`, `rel_path`, `sha256`, `pages`, `oversized`,
  `allowed_out_of_scope`, `state`, `priority`, `attempts`, `error_type`,
  `flagged_open`, `flagged_total`, `unchecked`, `final_revision`,
  `exported_at`, `exported_revision`, `created_at`, `queued_at`,
  `updated_at`, `purged_at`.
* **States:** `staged → queued → extracting → ready → in_review → finished`;
  plus `failed`, `cancelled`, `purged`. `finished → in_review` on reopen.
  Export is a timestamp, not a state.
* **`batch`**: `id`, `name`, `draft` (staging), `created_at`.
  **`upload`**: chunk progress. **`export`**: mass-export jobs.
* Migrations: `PRAGMA user_version` + numbered migration functions.
* **Worker:** one daemon thread at app start. It claims a job in a
  transaction (`ORDER BY priority DESC, queued_at`), runs today's `extract()`,
  writes `result.json` atomically, creates the review session (header +
  proposal, phase-1 call), then sets `ready`. Pause is a DB flag checked
  between jobs. Cancel mid-run reuses the cooperative `_Cancelled` raised from
  the progress callback. Startup asserts a single process.
* **Optional access token:** `SINDRI_ACCESS_TOKEN` set → every `/api` route
  requires it (header or cookie set once by the token prompt). Unset → open.

## 2. Upload, staging, queue

**Chunked upload** (own protocol; tus rejected — weak Python server side, new
dependency):

* `POST /api/uploads {batch_id, name, size, last_modified, rel_path,
  fingerprint}` → `{upload_id, received}`. The fingerprint is SHA-256 over size
  + first and last MiB (browsers cannot hash a file incrementally). An
  unfinished upload with the same fingerprint is returned with its offset —
  that is how re-selecting the same files resumes.
* `PUT /api/uploads/{id}?offset=N` with an 8 MiB body: accepted only when
  `offset == received` (else 409 with the current offset), fsynced before the
  reply, so retries are idempotent and a restart loses at most one chunk.
* Completion: verify size, compute full SHA-256 (duplicate detection), then
  * PDF → `staged` document with `pages` and `oversized` from
    `render.effective_dpi()` (the same function `probe` uses);
  * ZIP → unpacked server-side, streaming; absolute paths, `..` and symlinks
    refused; non-PDFs listed as ignored; every PDF → staged document with its
    folder path.
* No size limits. A free-disk guard (`SINDRI_MIN_FREE_MB`, default 2048)
  answers 507 so a full disk can never corrupt a review journal.
* Browser: 3 parallel uploads; folders via `webkitdirectory`. Unfinished
  uploads older than 7 days are removed.

**Staging** (a draft batch): rows with name, folder, pages, size, progress or
status, and badges — *multi-page: only sheet 1 is read; extraction quality may
be poor*, *oversized: read at reduced resolution; extraction quality may be
poor*, *duplicate of "X" (batch B, state)*, *unreadable PDF* (can never be
included). An **Include** checkbox defaults on for normal files and off for
out-of-scope and duplicates; including an out-of-scope file is the explicit
permission and is stored (`allowed_out_of_scope`). **Start batch** queues the
included rows and drops the excluded ones. A draft can be continued later.

**Queue controls:**

| Action | From | Effect |
|---|---|---|
| Move to front | queued | priority = max + 1 |
| Cancel | queued, extracting | → cancelled (cooperative mid-run) |
| Retry | failed, cancelled | → queued, attempts reset |
| Re-extract | ready with no review events | → queued, old proposal discarded |
| Remove | not extracting | files + row deleted; a review record stays |
| Pause / resume | global | worker finishes the current drawing, then waits |

Queue header: "Extracting 3 / 37 · ~2 h 10 m left" (rolling median of recent
extraction times × queued, labelled as an estimate).

## 3. Work list, review navigation, resume

* **Hash router**, vanilla ES modules as today: `#/` work list,
  `#/staging/<batch>`, `#/doc/<id>` the existing review screen. Back and reload
  work; every drawing has a bookmarkable link.
* **Work list:** checkbox, file name (+ folder), batch, state chip, flagged
  `open/total`, unchecked, last change, badges (exported rN, out-of-scope,
  failed with error type). Filters: batch, state, name search, a "needs me"
  preset (ready + in review). Bulk: move to front, cancel, retry, remove,
  export selected. Header: queue line, pause/resume, **+ Import**. Polling:
  `GET /api/documents?since=<cursor>` every 3 s while visible, paused while
  hidden.
* **Review bar:** `← Work list · Batch "…" · 5 of 37 · ‹ Prev · Skip for now › ·
  Jump to… (Ctrl+K) · Finish · Finish & Export`. Prev/Skip move through the
  batch's ready and in-review drawings in list order; skip changes nothing.
  Jump is a type-to-filter switcher over all ready/in-review drawings.
  After Finish: next ready in the batch, then in any batch; if none is ready
  but some are extracting, "Next drawing ready in ≈ N min"; if all are done,
  "Batch complete — Export batch". Leaving a drawing awaits the journal flush;
  on failure, "changes not yet saved — retry / leave anyway".
* **Resume:** `POST /api/documents/{id}/open` → proposal, net journal events,
  next seq, a **new writer token** (rotation, deferred from phase 1), notes,
  marks, title block, revision info. The client replays the events through
  `state.js`'s own ops, rebuilding table and undo stack (redo is not
  restored). The first appended event moves `ready → in_review`.
* **Two tabs:** opening rotates the writer, so the older tab's next flush gets
  `409 StaleWriter`. It turns read-only with "This drawing was opened in another
  tab or browser — reload to continue here", including how many unsaved events
  (at most the 500 ms debounce window) were not kept. No merging.
* **Batch of one:** Import → one staging row → Start; if the user is still on
  the list when it is ready and has not opened another drawing, it opens.

## 4. Finish, export, mass export, retention

* **Finish** = phase-1 seal (still gated on unresolved flagged rows) →
  `finished`, `final_revision = N` → open the next drawing. **Finish & Export**
  additionally downloads revision N's two files.
* **One export path:** `GET /api/documents/{id}/export/{xlsx|pdf}?rev=N`
  (default latest sealed) renders from the sealed rows (`sealed/rN.json`
  already stores them) plus `result.json` (notes, marks and title block pass
  through the UI unedited), cached as `docs/<id>/exports/rN.*`. The browser-row
  endpoints `POST /api/export` and `/api/export/pdf` are removed.
  `_exportable()` stays as the server-side filter.
* **Mass export:** `POST /api/exports {document_ids}` (UI fills them from a
  selection, batch, or filter) → its own CPU thread, never behind the GPU
  queue → `exports/<id>.zip`, progress polled. Layout
  `<batch>/<rel folder>/<stem>.xlsx` and `<stem>_ballooned.pdf`, collisions
  suffixed ` (2)`, plus `manifest.csv`: file, folder, batch, state, revision
  exported, sealed_at, rows exported, out-of-scope allowed, skip reason (`not
  finished`, `failed`, `purged`) or `export_error: <type>`. Unfinished
  drawings are never exported; a reopened drawing exports its last sealed
  revision with "reopened — later changes not included". Exporting sets
  `exported_at` and `exported_revision`; the list turns "exported rN" amber
  when a newer revision exists. ZIPs are deleted after 24 h (rebuildable).
* **Retention:** a daily sweep (and one at startup) deletes the directory of
  every `finished` drawing exported more than `SINDRI_RETENTION_DAYS` (30) days
  ago; the row stays as a `purged` tombstone (no reopen, no re-export).
  Unexported or unfinished work is never purged. Review records are never
  touched.
* **Journal is mandatory (decision 30).** It is now the single source of
  review truth for resume, finish and export. This **amends the HITL design's
  §6**: the "logging off" mode and its UI pill are removed, and a journal fault
  is a visible error rather than a silent downgrade. Extraction itself stays
  protected: the worker writes `result.json` first, so a review-store fault
  makes the drawing `failed (review_store)`, and Retry rebuilds the proposal
  from `result.json` without re-running the GPU. `SINDRI_REVIEW_CONSENT` still
  governs only what may ever leave the site.

## 5. Errors, testing, phasing

**Errors:** extraction exceptions are recorded by **type only** (CLAUDE.md
§1); 2 attempts, then `failed` with Retry. Crash mid-run → requeued at startup.
Journal write failing during review → persistent "log is lagging" banner with
retry countdown (phase-1 deferral), navigation guarded. Disk low → 507 on
upload/unpack; free space in `/api/health`. Unsafe/non-PDF ZIP entries →
listed as ignored. Corrupt/encrypted PDF → `unreadable`, cannot be included.
One drawing failing in a mass export → its manifest row says so; the rest
builds. Token set and missing → 401 → token prompt.

**Testing** (TDD, one task one commit):

* Python: SQLite repo and migrations; worker against a deterministic **stub
  backend** (FIFO + priority, pause, cancel mid-run, crash requeue, retry
  limit, review-store failure path); upload protocol (offset mismatch,
  fingerprint resume, lost chunk reply, disk guard); ZIP safety (zip-slip,
  absolute paths, symlinks, nested folders, non-PDFs); export determinism
  (single export byte-identical to its copy in a ZIP); retention tombstones;
  token middleware; product boundary (no `app.eval` imports).
* `conftest.py`: from "force the store to None" to "every test gets its own
  temp data and review dirs" — an ambient `SINDRI_DATA_DIR` /
  `SINDRI_REVIEW_DIR` can still never be written by the suite.
* Node: router and navigation order (prev, skip, next after Finish, all
  done), chunked upload client (pure, injected `fetch`), two-tab takeover,
  and **resume parity** — extend the existing fuzz so state rebuilt through
  `state.js` from an `/open` payload equals Python `replay()` and the
  pre-reload browser state.
* End to end: a throwaway Playwright drive (scratchpad, stub backend): import a
  ZIP → extract → review one drawing halfway → reload → resume → finish → skip
  one → mass export → check the manifest.

**Phasing** (one plan each, written just in time):

1. **B1 Core** — data dir, SQLite, document/batch model, worker + queue,
   mandatory journal, `/open` with writer rotation, server-side single export;
   shipped behind the *current* UI as an interim (one drawing = a batch of
   one, started without a staging step — staging arrives in B2/B3).
2. **B2 Import** — chunked resumable upload, ZIP/folder, duplicates, scope
   badges, staging API.
3. **B3 UI** — router, work list, staging view, review bar, resume, two tabs,
   lagging banner.
4. **B4 Export & ops** — mass export, retention, access token, compose and
   docs.

Then HITL phase 2.

## Data handling

Everything under `SINDRI_DATA_DIR` and `SINDRI_REVIEW_DIR` on a deployment is
client data under CLAUDE.md §1: server logs carry ids, states, counts and
exception types only — never file names, cell values, or exception messages.
The test suite never touches a real data or review dir. File names appear in
the UI, the manifest and the database on the client's own server, which is
where they already live.
