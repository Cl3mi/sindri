# State: Batch B4 — mass export, retention, access token, deployment

Plan: `docs/plans/2026-10-08-batch-b4-export-ops-plan.md` (written `48b358e`,
revised after a plan review `3a2fc84`; one re-check finding carried as an
execution requirement for Tasks 6/8, below)
Milestone: mass-export-server (task 6 of 15) — next is Task 7 (purge),
milestone retention after Task 9

## Completed task IDs
- Task 1 — migration 5: the `export` job table (`e359edc`)
- Task 2 — single-export render path moved to `app/batch/export_render.py`,
  `app.main._render_export` is an alias (`15dc242`)
- Task 3 — `app/batch/names.py` Windows-safe export names (`61811b0`, `bc09ec1`)
- Task 4 — Repo export-job CRUD (`b2f9053`, `056e96f`)
- Task 5 — `app/batch/exports.py` mass-export engine (`3adf9e5`, `cc29f4c`)
- Task 6 — `/api/exports` routes + interrupted-export recovery at startup
  (`58983a4`, `6cdcfaa`)

Suite: 1914 passed, 2 skipped. Node tests: 249. Guard 32/0.

## What's done
A selection of drawings exports server-side as one ZIP plus a manifest, on
its own thread (never behind the GPU worker), through the SAME render
function, cache and lock the single-drawing download uses. A job's
progress and outcome live in the index; a job a crash left `running` is
marked failed at startup; the ZIP is built in a temp file and renamed only on
success. B3 is complete (`c2e5211`).

## What's next
Tasks 7-9 (purge with tombstones, housekeeping thread, 410 for purged
drawings), then the access token (10-11), then the UI, compose and docs, and
the real-browser drive (12-15).

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
- **Commit-message hook:** it also rejects a bare `.xlsx` / `.csv` in commit
  prose, like `.pdf` in commands — word messages generically.
