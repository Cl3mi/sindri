# State: Batch B2 — Import (chunked upload, ZIP, staging)

Plan: `docs/plans/2026-10-07-batch-b2-import-plan.md` (written `fb0d5fb`,
revised after an Opus plan review `725f957`)
Milestone: upload-protocol (task 6 of 13) — next is milestone staging-api (Tasks 7-9)

## Completed task IDs
- Task 1 — migration 2, upload paths, path safety (`0d977f2`, `6bb924f`)
- Task 2 — pdf_facts: pages, oversized, readable (`47bae99`)
- Task 3 — index side: draft batches, upload rows, staged documents (`dd33516`, `f8cee58`)
- Task 4 — staging decisions: Include, Start, discard (`e5e5de7`, `3ee3070`)
- Task 5 — file side of the upload protocol (`fee8816`, `f7bb361`)
- Task 6 — import API over HTTP (`4c13d87`, `e2c2e8e`)

## What's done
The server can take a draft batch, accept resumable chunked uploads (offset
guarded in the index, part fsynced before the index moves, bytes on disk
trusted over the index and over the client), complete them idempotently into
staged documents (hard link + `.importing` marker), and decide Include /
Start / Discard on a draft. Every task passed a spec + quality review on Opus
(Task 2 on Sonnet, not data-integrity); every reviewer-reproduced bug is fixed
with a regression test. Verified: suite **1683 passed, 2 skipped**.
The interim UI still uploads through B1's `/api/upload` + `/enqueue`
(removed in Task 12).

## What's next
Task 7 (ZIP import), Task 8 (staging API: include / start / discard routes,
`Uploads.start_batch`/`discard_batch` with `.importing` markers), Task 9
(recovery + stale cleanup), then the staging-api checkpoint.

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
- **For Task 9:** the stray-part sweep must only unlink a part, never truncate
  it (after a crash between commit and part unlink it shares its inode with a
  staged drawing).
- **For Task 11:** the draft memory should retry `/complete` with the
  remembered upload id rather than re-create the upload.
- **Deferred / known:** a part with `st_nlink > 1` while still receiving (two
  independent faults) answers 500 until restart; `delete_untouched_upload`
  relies on a non-constant clock; resume key is asymmetric on
  `last_modified` (NULL then a value → a new upload); B4's export must still
  sanitise batch/folder names for Windows (names ending in ".", NTFS reserved
  names, `/`, `\`).
