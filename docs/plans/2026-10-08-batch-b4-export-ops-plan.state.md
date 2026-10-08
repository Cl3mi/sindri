# State: Batch B4 — mass export, retention, access token, deployment

Plan: `docs/plans/2026-10-08-batch-b4-export-ops-plan.md` (written `48b358e`,
revised after a plan review `3a2fc84`; one re-check finding carried as an
execution requirement for Tasks 6/8, below)
Milestone: ui-compose-docs-e2e (task 15 of 15) — **B4 COMPLETE, and with it
the batch/async-review project (B1-B4)**. Next is HITL phase 2 (its own
plan). Nothing is pushed.

## Completed task IDs
- Task 1 — migration 5: the `export` job table (`e359edc`)
- Task 2 — single-export render path moved to `app/batch/export_render.py`,
  `app.main._render_export` is an alias (`15dc242`)
- Task 3 — `app/batch/names.py` Windows-safe export names (`61811b0`, `bc09ec1`)
- Task 4 — Repo export-job CRUD (`b2f9053`, `056e96f`)
- Task 5 — `app/batch/exports.py` mass-export engine (`3adf9e5`, `cc29f4c`)
- Task 6 — `/api/exports` routes + interrupted-export recovery at startup
  (`58983a4`, `6cdcfaa`)
- Task 7 — Repo `purge_candidates` / guarded `purge` (`f1dd82c`)
- Task 8 — `app/batch/housekeeping.py` retention + export-ZIP sweep thread
  (`d020fb9`)
- Task 9 — purged drawings answer 410 on open/export (`7eec43c`)
- Task 10 — `app/auth.py` constant-time token gate (`8cbd3da`)
- Task 11 — token middleware + `POST /api/auth` (`8bc8e55`)
- Task 12 — token prompt UI (`5f0a0fc`)
- Task 13 — work-list mass export, retention 410 copy, purged badge
  (`c2445da`, `2447248`)
- Task 14 — compose, Dockerfile, CLAUDE.md B4 paragraph (`734d954`,
  `abc5a45`)
- Task 15 — real-browser drive; the one bug it found fixed (`718efa5`)

Suite: 1946 passed, 2 skipped (verified by the controller at close).
Node tests: 272. Guard 32/0. `docker compose config` valid.

## Real-browser drive (node Playwright, scratch data dir, synthetic drawings)
All PASS on the final run:
- **Mass export:**
  - select 2 finished + 1 unfinished, then Export selected; the button shows
    "Exporting n / 3…" and is disabled, then re-enables;
  - the ZIP downloads and holds both drawings' workbook + ballooned file;
  - the manifest has 3 rows (blank, blank, `not_finished`);
  - the mass-export member bytes equal the single-drawing export bytes.
- **Retention:**
  - `exported_at` backdated 40 days in the scratch index, then a restart
    with 30 days; the startup sweep purges it;
  - the drawing dir is gone and the review dir kept;
  - the "Deleted (retention)" chip shows;
  - a direct `#/doc/<id>` shows the retention sentence with no retry and
    lands on the work list;
  - bulk Remove deletes the tombstone.
- **Access token:**
  - the prompt appears on load;
  - a wrong token shows "Incorrect token." inline;
  - the right token sets an HttpOnly/SameSite=Strict cookie and loads the
    work list;
  - a reload does not re-prompt;
  - `/api/health` is open, and `/api/documents` without the token is 401. Node tests: 249. Guard 32/0.

## What's done
A selection of drawings exports server-side as one ZIP plus a manifest, on
its own thread (never behind the GPU worker), through the SAME render
function, cache and lock the single-drawing download uses. A job's
progress and outcome live in the index; a job a crash left `running` is
marked failed at startup; the ZIP is built in a temp file and renamed only on
success. B3 is complete (`c2e5211`).

Retention: a finished drawing exported more than `SINDRI_RETENTION_DAYS`
(default 30, 0 = off, negative refuses the boot) ago is marked `purged` by
one guarded UPDATE and only then has its drawing dir deleted; its review
record (a separate tree) is never touched. The sweep runs once at start and
hourly, on its own thread, started only after the worker has the
single-process lock.

Access token: `SINDRI_ACCESS_TOKEN` unset (or blank) = open as before; set =
every `/api/*` except `/api/auth` and `/api/health` answers 401 `token
required` unless the `x-sindri-token` header or the `sindri_token` cookie
matches. `POST /api/auth` sets that cookie (HttpOnly, SameSite=Strict, no
Secure — LAN plain HTTP, decision 10, session cookie) only on a match.

## What's next
Nothing in B4. The operator reviews and pushes (the branch is unpushed), and
then HITL phase 2 (forcing + audit) gets its own plan. **Task 13 must also:** show a 410 on open as
"deleted by retention" with no retry (409 stays "try again"), and give the
`purged` state a work-list badge/label (none exists in `app/static/js`).

## Decisions / deviations since the plan was written
- **EXECUTION REQUIREMENT (plan re-check), applied in Task 6:** the plan's
  runtime code predates B3's `Runtime._run_startup_recovery()`.
  `exports.fail_interrupted()` is a THIRD independently guarded step there
  (beside `uploads.recover()` and `_recover_reextract_stashes()`), tested
  both ways; `Runtime.__init__` adds only `self.exports = Exports(...)`
  before the unchanged Worker line. **Task 8 adds only
  `self.housekeeping = Housekeeping(...)` the same way.**
- **Export names (Task 3):** de-duplication is case-insensitive (`used` holds
  casefolded keys; Windows file systems are case-insensitive) and every
  component is capped at 100 characters, re-stripped of trailing dots/spaces
  after the cut, with the reserved-name check on the final string.
- **Export-job transitions (Task 4) are guarded** like document ones:
  `finish_export` only from `running`, only to `done`/`failed`
  (`InvalidTransition` / `UnknownExport` otherwise); progress is a no-op off
  `running`. So a recovered `failed` job can never be resurrected as `done`.
  The migration-5 comment wrongly said Task 9 recovers jobs; corrected.
- **Mass-export engine (Task 5), review fixes:**
  - manifest cells starting with `= + - @`, tab or CR get a leading `'`
    (formula injection: operators open it in a spreadsheet program);
  - the 24 h ZIP sweep matches only final `<32-hex>.zip` names, never an
    in-progress `.tmp.zip`;
  - the temp-file creation sits inside the try, so every exit path ends
    `done`/`failed`;
  - the build thread tolerates `InvalidTransition` on its final write;
  - thread creation is injectable (`spawn=`) for synchronous tests — a
    per-export lock that leaked and served only the tests was removed.
- **Mass-export routes (Task 6):**
  - 1 to 10000 ids per request (422 otherwise), de-duplicated in order;
  - download of a running job is 409 and of a failed job 409 too (a state
    conflict, not a server fault — the plan had 500);
  - a done job whose ZIP was swept gives 410;
  - the download name is a fixed `sindri-export.zip`, never derived from a
    client name.
- **Retention (Tasks 7-9):**
  - no migration was needed (`purged_at` exists since migration 1, `state`
    has no CHECK);
  - a file-delete fault after a won purge is logged (id prefix + type) and the
    sweep moves on, where the plan's `rmtree(ignore_errors=True)` would have
    hidden it;
  - **gaps found and closed in Task 9:** `/events` had no state check, so a
    stale tab could append to a purged drawing's journal (now 409, as
    `/seal` already was); and the mass export's `purged` skip reason was
    dead code, because a tombstone keeps its `final_revision` (now checked
    first);
  - re-extract/retry/move-to-front already refuse a tombstone (409), and
    DELETE still removes it.
- **Access token (Tasks 10-11):**
  - the comparison hashes both sides (sha256) before `compare_digest`, so it
    never raises on a non-ASCII guess and leaks no length;
  - a whitespace-only token reads as unset;
  - with a token set, a trailing-slash, URL-encoded, HEAD or OPTIONS variant
    still gets 401, and `/` plus static files stay open;
  - `POST /api/auth` answers 401 even when auth is off, so it never reveals
    whether a token is configured;
  - `tests/conftest.py` unsets `SINDRI_ACCESS_TOKEN` per test.
- **Token prompt (Task 12):**
  - one wrapped global `fetch` covers api.js, upload.js, polling and the
    journal;
  - the prompt is single-flight and retries once after login;
  - a 401 from `/api/auth` itself is NOT re-intercepted (the plan's code
    would have deadlocked on a wrong token);
  - the token lives only in the password input — the HttpOnly cookie
    carries it.
- **Mass-export UI (Task 13):**
  - the download is a plain same-origin anchor navigation, not fetch + blob,
    because a ZIP can be gigabytes;
  - the button state resets in a `finally` on every outcome;
  - a 410 on open shows "This drawing's files were deleted by retention (its
    review record is kept)." with no retry;
  - purged rows get a muted "Deleted (retention)" chip and are excluded from
    "needs me".
- **Found by the drive (Task 15):** a bookmarked purged drawing never reached
  the 410 handler (routing branches on state first) and showed the generic
  toast. Fixed with a pure `unopenableDocCopy(doc)` sharing the
  retention-sentence constant — no `/open` round-trip.
- **Commit-message hook:** it also rejects a bare `.xlsx` / `.csv` in commit
  prose, like `.pdf` in commands — word messages generically.
