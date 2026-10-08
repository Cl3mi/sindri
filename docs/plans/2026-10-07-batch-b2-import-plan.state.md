# State: Batch B2 — Import (chunked upload, ZIP, staging)

Plan: `docs/plans/2026-10-07-batch-b2-import-plan.md` (written `fb0d5fb`,
revised after an Opus plan review `725f957`)
Milestone: ui (task 13 of 13) — B2 COMPLETE; next is B3 (`docs/plans/2026-10-08-batch-b3-ui-plan.md`)

## Completed task IDs
- Task 1 — migration 2, upload paths, path safety (`0d977f2`, `6bb924f`)
- Task 2 — pdf_facts: pages, oversized, readable (`47bae99`)
- Task 3 — index side: draft batches, upload rows, staged documents (`dd33516`, `f8cee58`)
- Task 4 — staging decisions: Include, Start, discard (`e5e5de7`, `3ee3070`)
- Task 5 — file side of the upload protocol (`fee8816`, `f7bb361`)
- Task 6 — import API over HTTP (`4c13d87`, `e2c2e8e`)
- Task 7 — ZIP imports (`5fd6263`, `e3fadf0`, `bf6c5a6`, `7f8b8c2`)
- Task 8 — staging API: include, start, discard (`2882dc7`, `67b987c`)
- Task 9 — recovery and stale cleanup (`008518d`, `8225080`, `918a2d7`)
- Task 10 — chunked upload client + badges (`ae2824b`, `d9c120d`, `c923996`, `bc5f98a`)
- Task 11 — interim UI through the protocol (`2a560c4`, `1e3abd6`)
- Task 12 — single-shot routes removed; Start takes expected_ids (`d0f3764`)
- Task 13 — CLAUDE.md B2 paragraph + real-browser drive (`fe157ad`); this checkpoint

## Final result (B2 complete)
Verified: suite **1802 passed, 2 skipped**; node 159; guard **32 passed, 0
failed**. Real-browser drive (throwaway, stub backend, node playwright-core +
cached chromium): **10/10 PASS** — upload → confirm → Start → extracted; a
dropped first PUT retried; every PUT failing → draft remembered across a
reload → re-select resumes into the same batch; duplicate badge + "Include
and start"; multi-page warning stores `allowed_out_of_scope`; a damaged PDF
toasts; Cancel deletes batch and drawing; plain HTTP over the LAN (no
`crypto.subtle`) uploads via the JS SHA-256; a 9 MiB file reloaded after its
first 8 MiB chunk resumes and stages once at full size; a ZIP of 2 PDFs + a
.txt stages 2 rows with `ignored_total` 1. From Task 10 on, Opus subagents hit
a weekly limit (resets 2026-10-13); Tasks 12-13 and their reviews ran on
Sonnet.

## What's done (as of the staging-api checkpoint)
The whole server side of B2: draft batches, resumable chunked uploads,
idempotent completion of PDFs and ZIPs into staged documents (hard link +
`.importing` marker), Include / Start / Discard over HTTP with marker-guarded
snapshots, and startup recovery + stale cleanup that can only delete
marked-and-rowless dirs. Every task passed a spec + quality review on Opus
(Task 2 on Sonnet); every reviewer-reproduced bug is fixed with a regression
test. Verified: suite **1798 passed, 2 skipped**. The interim UI still uploads
through B1's `/api/upload` + `/enqueue` (moved onto the protocol in Task 11,
removed in Task 12).

## What's next
B3 — plan `docs/plans/2026-10-08-batch-b3-ui-plan.md` (written `eecdd2c`,
under review), then B4.

## Decisions / deviations since the plan was written
- **Plan review (before execution):** C1 recovery runs only while holding the
  worker's single-process lock; MAX_CHUNK 413; pure-JS SHA-256 fallback when
  `crypto.subtle` is missing; ~10 min time-based retry budget and discard only
  on fatal errors; lost reply on Start → check `getDocument`; Task 11 split
  into 11 (UI) and 12 (route removal). The draft memory for resume uses
  **localStorage**, not sessionStorage, so a closed tab resumes (decision 21).
- **Plan test counts** are low by the review fixes: real totals are recorded
  per task above; the plan's final 1638 will not match.
- **Migration 2:** Include of migrated staged rows mirrors the staging default
  exactly (pages > 1 or oversized → 0; an earlier row with the same SHA-256 in
  any state → 0; else 1). SQL comments explain DEFAULT 0 / NULL choices and the
  missing FK on `upload_id`. `db.py` says rowid is data: a rebuild of
  `document`/`upload` must copy rowid explicitly (duplicate order and queue
  order inside an import depend on it).
- **Path safety:** `safe_rel_parts` NFC-normalises, refuses components Windows
  turns into `..` (only dots/spaces, leading/trailing spaces) and any char in
  Unicode categories Cc/Cf/Zl/Zp (`paths.has_refused_chars`, shared with batch
  names and upload names). `DOC_ID_RE` / `FINGERPRINT_RE` are `\A..\Z` with
  `fullmatch`.
- **Repo:** `upload_received` only advances within `size`; `reset_received`
  only moves down (an upward/equal reset is a silent no-op — the first-chunk
  reset to 0 must not fail). `delete_untouched_upload` removes a row only if
  never resumed or fed. Start/Discard (`start_batch`/`delete_batch`) refuse a
  draft holding a row no longer staged, read the clock inside the
  transaction, refuse an included out-of-scope row without its stored
  permission, and take `expected_ids` — they refuse ("the import changed") if
  the draft's id set differs from the caller's snapshot. **Task 8's
  `Uploads.start_batch`/`discard_batch` must pass the snapshot it marked as
  `expected_ids`** (the plan text calls them without it).
- **Uploads (file side):** names NFC + refused chars; bool sizes refused;
  `size < 2**63`, `last_modified` int within ±2**53 or None; a failed new
  create never touches another tab's resumed row/part; `put`/`create` reset the
  index down to the bytes actually on disk (no zero-filled holes); an
  over-long part is replaced by a new inode and answered 409 at 0; `put`
  refuses a part with `st_nlink > 1` (never write through to a staged
  drawing); hard-link fallback to copy only for EXDEV/EPERM/EMLINK/ENOTSUP/
  EOPNOTSUPP; failure cleanup removes contents, then the marker, then the dir.
- **Import API:** PUT refuses a missing/malformed/negative/over-cap
  Content-Length with 413 before reading, reads with a hard bound, calls
  `runtime()` before reading, absorbs a client disconnect as 400 (nothing
  written). Fixed-sentence 422 on `/api/uploads*` and `/api/batches*`;
  `StrictInt` sizes. 409 bodies carry `{detail, received, state}`. Batch names
  go through `clean_batch_name` on create and rename. A negative
  `SINDRI_MIN_FREE_MB` fails the boot; `/api/health` reports disk numbers and
  survives a `disk_usage` fault.
- **ZIP (Task 7):** only ZIP_STORED / ZIP_DEFLATED entries are opened —
  bzip2/LZMA/zstd are memory bombs zipfile decompresses without an output
  limit (a 536-byte bzip2 entry → +1 GiB RSS) and are listed as
  `unsupported_compression`. Entries are streamed in 1 MiB pieces, hashed and
  disk-guarded on real bytes. A damaged entry (zlib/LZMA/zstd error, errno-less
  OSError) is ignored as `unreadable_entry`; an OSError with an errno is a disk
  fault and rolls the whole completion back. Entries sharing a local header
  (Python 3.14 warns with the entry NAME on stderr) and entries whose
  header_offset points outside the file are never opened. Ignored names pass
  through `paths.shown_name` (NFC, U+FFFD for refused chars, 255 cap); the
  ignored list keeps 1000 entries plus `ignored_total` / `ignored_by_reason`
  (in `/complete` and staging views — **B3 must show them**). Backslash
  "folders" only when size 0. Test strings spell invisible chars as `\u`
  escapes.
- **Start/Discard (Task 8):** `Uploads._guarded` reads the batch first (a
  non-draft never gets markers), snapshots the ids, marks their dirs (a dir
  deleted concurrently is skipped), calls the repo with `expected_ids`,
  unmarks on any refusal (409, no retry) and removes dropped/discarded dirs
  marker-last on success. A per-batch lock (held by `complete()` around its
  commit + unmark) closes a marker race; lock order is upload → batch → repo,
  never reversed. Include takes a `StrictBool`; `/include` gets the fixed 422.
  A worker-wake failure after a committed Start still answers 200.
- **Recovery (Task 9):** runs from a `startup` hook inside `Worker.start()`
  under the flock, once per worker instance, before `requeue_interrupted`;
  each step is guarded separately. The sweep deletes a `docs/<id>` dir only if
  marked, rowless (any state) and its marker predates the process; a marked
  dir with a row only loses its marker; an unmarked rowless dir is only
  `rmdir`ed if empty and older than the process. Stray parts are only
  unlinked, under their upload lock, never one of a receiving/completing
  upload. Only `receiving` uploads go stale (7 days, age re-checked under the
  lock). Empty drafts are judged on idleness via `batch.updated_at`
  (**migration 3**; every draft write bumps it) and deleted under the batch
  lock. `POST /api/batches` runs the stale cleanup only, never the docs sweep.
  One clock: `Repo.now()`.
- **Migrations are append-only even before they ship** — a review caught that
  editing migration 2 in place would break any data dir already at version 2;
  the batch column is migration 3.
- **Upload client (Task 10):** `upload.js` — 8 MiB chunks over 3 lanes;
  fingerprint = SHA-256 over size + first/last MiB with a pure-JS fallback
  when `crypto.subtle` is missing (plain HTTP); each request retries non-fatal
  failures for ~10 min (waits capped, abortable), a PUT attempt times out at
  max(120 s, bytes / 16 KiB/s) so slow links still import; 409 resyncs to the
  server's `received` (may go down); a 200 with an invalid body counts as a
  lost reply; 408/429 retried, 400/404/413/422/507 fatal; an unreadable file
  is fatal with `reason: 'file_unreadable'`; an import stops starting new
  files only after a whole budget with no progress from any lane, and stopped
  files say why ('not attempted: <reason>'); cancel never reads unstarted
  files. `isImportable` mirrors the server (NFC + trim, PDF/ZIP).
- **Interim UI (Task 11):** decision logic in `importone.js` (node-tested).
  The draft is remembered in localStorage per file (name+size+lastModified)
  from before the first byte until Start/Cancel; a remembered draft that is
  gone/started falls back once to a fresh draft; a non-fatal failure keeps the
  draft so re-selecting resumes; a remembered draft's staging view is reused
  when it holds a completed upload matching name, size, empty folder AND
  fingerprint (`upload_view` now exposes `fingerprint`) — all matching
  uploads are considered, the last staged row wins; Start is refused unless
  the draft holds exactly the shown row; `forgetDraftsOf` clears memory for a
  drawing reached by link; a read failure keeps a remembered draft. File
  pickers clear their value so re-selecting the same file fires.
- **Task 12:** `POST /api/upload` and `/enqueue` removed (`Repo.create_document`
  / `enqueue` remain as building blocks; nothing in app/ calls them).
  `POST /api/batches/{id}/start` takes optional `{"expected_ids": [...]}`
  (strict 32-hex, `extra="forbid"`, fixed 422) checked under the batch lock
  before marking → 409 "the import changed — reload it" with nothing changed.
  The interim UI sends `[w.id]`.
- **Known, left for B3/B4:** best-effort discards can leave a draft holding a
  staged row that the empty-draft sweep never removes (B3 draft list / B4
  retention); an in-flight upload can't be cancelled in the interim UI; the
  same "import changed" message covers two different race windows.
- **Deferred / known:** a part with `st_nlink > 1` while still receiving (two
  independent faults) answers 500 until restart; `delete_untouched_upload`
  relies on a non-constant clock; resume key is asymmetric on
  `last_modified` (NULL then a value → a new upload); B4's export must still
  sanitise batch/folder names for Windows (names ending in ".", NTFS reserved
  names, `/`, `\`, NFKC look-alike separators like U+2215 or fullwidth dots)
  and de-duplicate identical drawing names; duplicate order and the queue
  tiebreak depend on rowid (VACUUM/rebuilds must preserve it). For B3: a
  many-entry ZIP is one long `/complete` request (progress UI); an empty
  archive should say so; a zip bomb answers 507 on every retry, so offer
  delete; interim Cancel discards the whole draft — B3 must remove single
  rows from multi-row drafts.
