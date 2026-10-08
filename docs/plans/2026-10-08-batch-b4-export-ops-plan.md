# Batch B4 — Export & ops (mass export, retention, access token, compose/docs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow `context-management:milestone-checkpoints`: the CONTROLLER checkpoints `docs/plans/2026-10-08-batch-b4-export-ops-plan.state.md` at each marker. Implementers never edit that file.

**Goal:** Close out the batch/async-review project. A selection, a batch, or a filtered set of finished drawings exports as one ZIP (per-drawing xlsx + ballooned PDF, laid out by batch/folder, plus a `manifest.csv` that accounts for every drawing asked for, including the ones skipped) built on its own thread so it never queues behind the GPU worker; a drawing's files are deleted automatically a configurable number of days after its last export, leaving a tombstone row that can never be reopened or re-exported, while its review record is never touched; an optional shared access token gates every `/api` route; and the compose file, `Dockerfile` and `CLAUDE.md` describe the finished deployment.

**Architecture:** The single-document export path (`app/main.py`'s `_render_export`, built in B1) moves to `app/batch/export_render.py` unchanged in behaviour, so a mass export and a lone download call the *same* function — one cache, one lock, one set of bytes — rather than two renderers that happen to agree today. `app/batch/exports.py` adds a small job table and runs each mass export on its own `threading.Thread`, never touching the GPU worker's queue or lock. `app/batch/housekeeping.py` is a second always-on background thread (started only by the process that won the worker's single-process lock, the same guarantee B2's import recovery relies on) that purges old exported drawings and deletes expired export ZIPs. `app/auth.py` is a small, dependency-free constant-time token gate wired in as FastAPI middleware. Nothing here touches the GPU extraction path at all.

**Tech Stack:** Python 3 / FastAPI / sqlite3 / zipfile / csv / pytest; vanilla ES modules + `node --test`.

**Design:** `docs/plans/2026-10-07-batch-async-review-design.md` — §0 (binding decisions; B4 touches 7, 14, 15, 20, 23), §4 (mass export, retention), §1 (access token, compose/env), §5 (errors, testing — export determinism, retention tombstones, token middleware, product boundary). B1 as built: `docs/plans/2026-10-07-batch-b1-core-plan.state.md`. B2 as built: `docs/plans/2026-10-07-batch-b2-import-plan.state.md` (carries three notes addressed to B4, reproduced below). B3 as planned: `docs/plans/2026-10-08-batch-b3-ui-plan.md` — **B3 is being revised concurrently with this plan; do not edit it.** Re-read its state file (`docs/plans/2026-10-08-batch-b3-ui-plan.state.md`, written once B3 ships) before starting Milestone 4, and treat every B3 module/function name used below as "as planned, confirm against what actually shipped."

**Rules that bind every task** (from CLAUDE.md — read §1 and §4 there):
* TDD strictly: failing test → watch it fail → minimal code → pass → commit. One task, one commit (a review fix may be a second commit).
* Never read client data; never put the protected path (`/home/clemi/sindri-client-data`) in a command or a subagent prompt; single, unpiped, unchained shell commands (no `|`, `&&`, `;`, `>`).
* **The Bash guard denies any command string containing a bare `.pdf`.** Never type a file name with that extension in a command, a `-k` filter or a commit message. Test PDFs are built in code or reached through the `sample_pdf` fixture, never named on the command line.
* Never use bare `git stash` (the stash stack is shared across worktrees and other sessions may be using it). `git add` and `git commit` are separate calls.
* Never `git checkout`/`restore`/`reset --hard` without checking `git status` first.
* Server logs carry ids (first 8 chars), states, counts and exception TYPES only — never exception messages, file names, or values. Error replies are fixed sentences. **The token itself is never logged, never echoed in an error body, and never appears in a URL** (it travels as a JSON body, a header, or a cookie only).
* `app/batch/` and `app/review/` must never import `app.eval` (`tests/review/test_product_boundary.py` globs every module in both — every new file in this plan lives under `app/batch/` for exactly this reason, except `app/auth.py`, which is a root-level module like `app/models.py`).
* Invisible/control characters inside a test string are written as `\u` escapes (e.g. `" "`), never pasted literally.
* Tests use temp dirs only; `tests/conftest.py` already isolates `SINDRI_DATA_DIR` / `SINDRI_REVIEW_DIR` per test.
* Commit trailer: `Co-Authored-By: <the implementing model's accurate name> <noreply@anthropic.com>`.
* Full suite before every commit: `python -m pytest -q`. **The baseline below is B3's own measured count (2026-10-08): 1802 passed, 2 skipped, node 159 (run inside pytest's `tests/test_js_state.py`).** B3 is landing concurrently and will move this number before B4 starts — re-measure with a bare `python -m pytest -q` as Task 1's first step and use that count as this plan's true baseline; do not trust the number above once B3 has merged.
* `node --test "tests/js/*.test.mjs"` must report the same node-test count as the number inside `tests/test_js_state.py`'s pytest result, run as a separate check after any JS task.
* Never edit `docs/plans/2026-10-08-batch-b4-export-ops-plan.state.md` — the controller checkpoints it.

**Data-integrity tasks** (the controller uses its strongest available model for implementation AND review): Tasks 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11. Tasks 12-14 may use a smaller model.

**Carried from B2** (from the B2 running log, items addressed to B4 — `paths.safe_rel_parts`'s own docstring repeats the first one verbatim):
* **Windows-unsafe names were never handled**: a component made only of dots/trailing dots or spaces, NTFS-reserved names (`CON`, `NUL`, `COM1`, …), `:` (alternate data streams), stray `/` or `\`, and NFKC look-alike separators (the fullwidth solidus `U+FF0F`, which NFKC does fold to `/`, and `U+2215` DIVISION SLASH, which does **not** and must be treated explicitly) all pass through `safe_rel_parts`/`clean_batch_name` unchanged, because nothing wrote a client-supplied name to a real filesystem path until this plan's ZIP does. Task 3 closes this for every component the mass export itself builds.
* **De-duplicate identical drawing names** inside one export (Task 3's `unique_export_path`, suffix `" (2)"`, `" (3)"`, …).
* **Duplicate order and the queue tiebreak depend on `rowid`**: the export job table added in Task 1 needs no such ordering (jobs are read by id, never listed), so this does not recur here — noted only so a future migration that rebuilds `document`/`upload` still remembers it.
* **Best-effort discards can leave a draft holding a staged row the empty-draft sweep never removes.** Not a B4 concern: that sweep only ever touches `draft` batches, and retention (Task 7) only ever touches `finished` ones — the two cannot collide, and this plan does not change `cleanup_stale`.

**Decisions made while planning** (the design is silent or ambiguous; each is justified where it lands):
1. **The single-document export path moves to `app/batch/export_render.py`, unchanged, and `app.main._render_export` becomes a direct alias to it** (`_render_export = render_export`), so the three existing tests that monkeypatch `app.main.write_workbook` / `app.main.render_ballooned_pdf` must be updated to patch the new module instead — a monkeypatch of the old name would now silently do nothing rather than failing loudly, which would be worse. This is the mechanism behind design §4's "one export path" / "byte-identical" requirement: a mass export and a lone download call the identical function, under the identical per-`(doc, rev, kind)` lock and cache.
2. **A mass-export job is a DB row (`export` table), not an in-memory dict.** A poll from any tab must see the same progress, and a server restart mid-build must show `failed` rather than a `running` job frozen forever with no worker left to finish it (Task 7's retention/zip-cleanup thread does not resurrect a job; Task 9 covers interrupted jobs explicitly).
3. **Mass export is a plain `threading.Thread` per job, not a queue.** The design's "own CPU thread, never behind the GPU queue" rules out the extraction `Worker`; it does not ask for a second work queue, and an operator triggering two mass exports at once (rare, since they are selections over one drawing list) just gets two threads racing the filesystem, which `zipfile`'s own locking and distinct temp files already make safe.
4. **The manifest's one `skip_reason` column carries three different kinds of value**, because the design lists exactly these columns and none of them is "note": blank for a clean export, one of `not_finished` / `failed` / `purged` for a drawing never sealed, `export_error: <Type>` for a sealed drawing whose render failed, and the literal sentence `reopened — later changes not included` for a drawing that **was** exported from its last sealed revision despite being currently reopened (decision 16: reopening a finished drawing moves it to `in_review`, not back to "unfinished" — `final_revision` staying non-`None` is what still makes it exportable, exactly as the existing single-document export route already treats it, checking `final_revision` only and never the current state).
5. **Eligibility for export is `final_revision is not None`, matching the already-shipped single-document route exactly** — not "current state is `finished`". This is what makes a reopened drawing's last sealed revision exportable as the design asks, with no special case: the row's `final_revision` already means "the last Finish", and `in_review` due to a reopen does not clear it.
6. **Retention purges by writing `state = 'purged'` and `purged_at` in one guarded `UPDATE … WHERE state = 'finished' AND exported_at IS NOT NULL AND exported_at <= cutoff`, and only deletes the drawing's files after that UPDATE's rowcount confirms it won.** Order matters: marking first means a concurrent `/open` or `/export` request that lands in between sees `purged` (410) the instant the row says so, never a request that passed the state check and then found its files gone underneath it. A rowcount of 0 (reopened, re-exported, already purged, or deleted in the meantime) purges nothing — no files are touched.
7. **Retention and the export-ZIP cleanup run from one extra background thread (`Housekeeping`), owned by `Runtime`, started only after `Worker.start()` returns successfully.** That is the same single-process guarantee B2's import recovery leans on (`Worker.start()` holds `worker.lock` for the life of the process), reused without touching `Worker` itself: `Housekeeping` has nothing to do with the GPU queue and does not need to be `Worker`'s `startup` hook, which already has a narrower, one-shot job (`uploads.recover()`). It sweeps once immediately (satisfying "one at startup") and then on a fixed interval, short enough that a day-old drawing does not wait long after the configured retention window, without needing any "has a day passed" bookkeeping of its own — a sweep that finds nothing to do is cheap.
8. **A purged or never-finished drawing's `/open` and `/export` routes answer `410 Gone`**, a new, more specific reply than the `409` those routes already give for "not ready yet" — chosen over reusing 409 because a tombstone is a *permanent*, not a transient, refusal, and the UI's retry logic for the two must differ (409 says "try again shortly"; 410 says "this is gone for good").
9. **The access-token gate exempts `/api/auth` (how a token is ever supplied) and `/api/health`** (operational monitoring must work identically with or without a token configured; its body already carries only counts and type names under CLAUDE.md §1, so publishing it needs no secret) **and nothing else under `/api/`.** Static files (`/`, everything the SPA serves) stay open regardless: they are application code, not client data, and gating them would only break the token-prompt UI's own ability to load.
10. **The token cookie has no `Secure` flag.** B2's real-browser drive already exercises "plain HTTP over the LAN" as a supported deployment shape (no `crypto.subtle`, falls back to pure-JS SHA-256); a `Secure` cookie would silently never be sent on that exact path, which is worse than the (already-accepted, LAN-only) risk of a token readable by anyone who can already read the LAN's HTTP traffic. `HttpOnly` and `SameSite=Strict` are set regardless — the token is still inaccessible to any script and never sent cross-site.
11. **`windows_safe_component` rewrites a name rather than refusing it.** A mass export must account for every drawing it was asked to export (the manifest, decision 4); refusing an entire drawing because a title block's filename happens to collide with an NTFS-reserved device name would be a worse failure than quietly suffixing it.

---

## File structure

| File | Responsibility |
|---|---|
| `app/batch/db.py` | + migration (confirm the number at implementation time — see Task 1): `export` table (mass-export jobs) |
| `app/batch/export_render.py` (new) | `render_export`, `InputUnavailable`, `exportable` — moved from `app/main.py`, unchanged behaviour |
| `app/batch/names.py` (new) | `windows_safe_component`, `unique_export_path` |
| `app/batch/repo.py` | + `create_export`, `update_export_progress`, `finish_export`, `get_export`, `UnknownExport`; `purge_candidates`, `purge` |
| `app/batch/exports.py` (new) | `Exports`: `start`, `status`, `_run` (thread body), `_export_one` (one manifest row, never raises), `delete_expired_zips` |
| `app/batch/housekeeping.py` (new) | `Housekeeping`: `sweep_retention`, `purge_one`, `sweep_export_zips`, `start`/`stop` (its own daemon thread) |
| `app/batch/paths.py` | + `DataDir.exports`, `DataDir.export_zip_path` |
| `app/batch/runtime.py` | + `Exports`, `Housekeeping`, `SINDRI_RETENTION_DAYS` |
| `app/auth.py` (new) | `configured_token`, `token_matches`, `extract_token`, `requires_token`, cookie/header constants |
| `app/main.py` | `_render_export` alias; `POST /api/exports`, `GET /api/exports/{id}`, `GET /api/exports/{id}/download`; 410 for a purged/never-finished drawing in `/open` and `/export`; token middleware + `POST /api/auth` |
| `app/static/js/auth.js` (new) | `isApiPath`, `needsToken`, `submitToken`, `installAuthGate` (pure, injected `fetch`) + DOM prompt |
| `app/static/js/massexport.js` (new) | `exportStatusText`, `pollIntervalMs`, `downloadUrl` (pure) + the work list's "Export selected" wiring |
| `app/static/js/api.js` | + `requestExport`, `getExport` |
| `docker-compose.yml` | + `SINDRI_RETENTION_DAYS`, `SINDRI_ACCESS_TOKEN` (commented, off by default) |
| `CLAUDE.md` | B4 paragraph |
| `tests/batch/test_db.py` | + the new migration |
| `tests/batch/test_export_render.py` (new) | the moved render path |
| `tests/batch/test_names.py` (new) | Windows-safe naming |
| `tests/batch/test_repo.py` | + export job CRUD, `purge_candidates`/`purge` |
| `tests/batch/test_exports.py` (new) | mass export engine, incl. export-determinism |
| `tests/batch/test_housekeeping.py` (new) | retention + zip cleanup |
| `tests/test_api.py` | 3 monkeypatch targets updated; new export/retention/410 routes |
| `tests/test_auth.py` (new) | token gate |
| `tests/js/auth.test.mjs`, `massexport.test.mjs` (new) | pure-logic node tests |

---

## Milestone 1: mass-export-server

### Task 1: Migration — the `export` job table

**Data-integrity: yes** (schema migration of a deployed index).

Before writing anything, confirm the true starting point:

Run: `python -m pytest -q`
Expected: whatever B3 actually left the suite at — write the number down, it replaces every count quoted later in this plan from here on (each later task's "Expected: N passed" is this number plus however many new tests that task added; recompute as you go rather than trusting the plan's arithmetic if B3 added more than planned).

**Files:**
- Modify: `app/batch/db.py`
- Test: `tests/batch/test_db.py`

- [ ] **Step 1: Write the failing test** — append to `tests/batch/test_db.py`:

```python
def test_the_export_job_migration_adds_the_table(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "export" in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(export)")}
    assert {"id", "document_ids", "state", "done", "total",
            "error_type", "created_at", "updated_at"} <= cols


def test_migrating_twice_is_a_no_op(tmp_path):
    """Confirms the migration count this plan's code was actually written
    against -- a mismatch here means B3's migration landed with a different
    number than this plan assumed, and this task's migration must be
    renumbered before anything else in this file is trusted."""
    conn = db.connect(tmp_path / "s.db")
    first = db.migrate(conn)
    second = db.migrate(conn)
    assert first == second == len(db.MIGRATIONS)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: FAIL — `sqlite3.OperationalError: no such table: export`

- [ ] **Step 3: Implement** — first run `python -c "from app.batch import db; print(len(db.MIGRATIONS))"` to get the current count `N` (at B3's current code it is expected to be 4: 1 batches/documents, 2 uploads/staging, 3 batch idleness, 4 B3's work-list progress columns). Append migration `N + 1` to `app/batch/db.py`'s `MIGRATIONS` list:

```python
    # N+1: mass-export jobs (batch B4, design §4). A row per POST
    # /api/exports, not an in-memory dict: a poll from any tab must see the
    # same progress, and a server restart mid-build must show `failed`
    # rather than a `running` job nothing will ever finish (app/batch/exports.py
    # Task 9 requeues/fails these at startup the same way requeue_interrupted
    # does for extraction).
    """
    CREATE TABLE export (
        id TEXT PRIMARY KEY,
        document_ids TEXT NOT NULL,        -- JSON list, the request's own order
        state TEXT NOT NULL,               -- running, done, failed
        done INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL,
        error_type TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
```

Run `python -c "from app.batch import db; print(len(db.MIGRATIONS))"` again to confirm it is now `N + 1`, and if it differs from what the test above measured as "current", update the comment's `N+1` label to match — the code is correct either way (`MIGRATIONS.append(...)` always targets the next slot), only the comment must not lie about its own number.

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 2 passed, 2 skipped

```bash
git add app/batch/db.py tests/batch/test_db.py
git commit -m "$(cat <<'EOF'
batch: add the mass-export job table

A mass export's progress must survive a poll from any tab and read as
`failed`, not silently frozen, if the server restarts mid-build -- an
in-memory dict could do neither.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `export_render.py` — move the single-export path

**Data-integrity: yes** (must reproduce B1's export bytes exactly; three existing tests' monkeypatch targets move with it).

**Files:**
- Create: `app/batch/export_render.py`
- Modify: `app/main.py`
- Test: `tests/batch/test_export_render.py` (new), `tests/test_api.py` (3 monkeypatch targets)

- [ ] **Step 1: Write the failing test** — create `tests/batch/test_export_render.py`:

```python
"""The single export path B1 built in app/main.py, moved here unchanged so
B4's mass export (app/batch/exports.py) and a lone download call the exact
same function -- one cache, one lock, one set of bytes, not two renderers
that happen to agree today (batch design §4's export-determinism
requirement)."""
import app.main as main
from app.batch.export_render import InputUnavailable, render_export


def test_main_s_render_export_is_this_module_s_render_export():
    """The alias IS the function, not a wrapper around it: a test or a
    caller that monkeypatches one sees it from the other."""
    assert main._render_export is render_export


def test_a_missing_document_directory_raises_input_unavailable(tmp_path):
    sealed = {"rows": [], "sealed_at": "2026-10-08T00:00:00+00:00"}
    try:
        render_export("x" * 32, tmp_path / "gone", 1, "xlsx", sealed)
        assert False, "expected InputUnavailable"
    except InputUnavailable:
        pass
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_export_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.export_render'`

- [ ] **Step 3: Move the code.** Create `app/batch/export_render.py`:

```python
"""The single place a sealed review revision becomes an xlsx or a ballooned
PDF (batch design §4). Moved here from app/main.py unchanged: app.main's
single-document export route and app.batch.exports' mass export both call
render_export, under the SAME per-(doc, rev, kind) cache and lock, so a
drawing exported alone and the same drawing inside a mass-export ZIP are
byte-identical by construction, not by coincidence."""
import json
import os
import tempfile
import threading
from pathlib import Path

import fitz  # PyMuPDF

from app.excel import write_workbook
from app.models import Characteristic, MarkBlock, NoteBlock, TitleField
from app.pipeline.ballooned_pdf import render_ballooned_pdf


def exportable(rows):
    """Confirmed rows only. The UI already omits unconfirmed suggestions; this
    is the server-side guarantee (docs/plans/2026-10-07-suggestion-tray-design.md)."""
    return [r for r in rows if not r.suggested]


class InputUnavailable(Exception):
    """One of the export's INPUTS -- the sealed rows, result.json, or
    input.pdf -- is missing or unreadable, as opposed to a bug in the
    exporter itself (app.excel / app.pipeline.ballooned_pdf): that is a bug
    and must 500, not be reported as "the files are gone"."""


# One lock per (doc_id, rev, kind): two requests racing the FIRST render of a
# revision -- one from a lone download, one from a mass export building the
# same drawing into a ZIP -- must not share a fixed temp name.
_EXPORT_LOCKS: dict = {}
_EXPORT_LOCKS_GUARD = threading.Lock()


def _export_lock(key) -> threading.Lock:
    with _EXPORT_LOCKS_GUARD:
        return _EXPORT_LOCKS.setdefault(key, threading.Lock())


def render_export(doc_id: str, work: Path, rev: int, kind: str, sealed: dict) -> Path:
    """Render revision `rev` once and cache it under docs/<id>/exports/, so a
    repeated or mass export of the same revision is the same bytes. The rows
    come from the SEALED revision, never from a browser. Never invalidated if
    the exporter's own code changes later; delete docs/<id>/exports/ by hand
    to force a rebuild."""
    out = work / "exports" / f"r{rev}.{kind}"
    if out.is_file():
        return out
    with _export_lock((doc_id, rev, kind)):
        if out.is_file():      # a concurrent first render finished while we waited
            return out
        if not work.is_dir():
            raise InputUnavailable("document directory is gone")
        out.parent.mkdir(exist_ok=True)
        try:
            rows = exportable([Characteristic.model_validate(r) for r in sealed["rows"]])
            notes = marks = title_block = None
            src = None
            if kind == "xlsx":
                result = json.loads((work / "result.json").read_text(encoding="utf-8"))
                notes = NoteBlock.model_validate(result["notes"]) if result["notes"] else None
                marks = MarkBlock.model_validate(result["marks"]) if result["marks"] else None
                title_block = [TitleField.model_validate(t) for t in result["title_block"]]
            else:
                src = work / "input.pdf"
                if not src.is_file():
                    raise InputUnavailable("input.pdf is missing")
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise InputUnavailable from e

        fd, tmp_name = tempfile.mkstemp(
            dir=out.parent, prefix=f"r{rev}.", suffix=f".tmp.{kind}")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            if kind == "xlsx":
                write_workbook(rows, tmp, notes=notes, marks=marks, title_block=title_block)
            else:
                try:
                    render_ballooned_pdf(src, rows, dpi=300, out_path=tmp)
                except (fitz.FileDataError, fitz.FileNotFoundError) as e:
                    raise InputUnavailable from e
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        return out
```

Now edit `app/main.py`: delete the moved definitions (`_exportable`, `_EXPORT_LOCKS`, `_EXPORT_LOCKS_GUARD`, `_export_lock`, `_InputUnavailable`, `_render_export`, and the now-unused `fitz`/`tempfile`/`threading`/`write_workbook`/`render_ballooned_pdf`/`NoteBlock`/`MarkBlock`/`TitleField`/`Characteristic` imports **if and only if** nothing else in the file still needs them — `Characteristic` is still used by `ReadRegionRequest`'s caller and `SealRequest`, so keep it; `NoteBlock`/`MarkBlock`/`TitleField` are used nowhere else, drop them; `fitz`/`tempfile`/`threading` are each used nowhere else in `app/main.py` after the move, drop them too; `write_workbook`/`render_ballooned_pdf` likewise). Replace with:

```python
from app.batch.export_render import InputUnavailable, exportable, render_export

# Kept as a module attribute so existing callers (and tests) that reach it as
# `main._render_export` keep working: it is the SAME function object, not a
# wrapper, so a monkeypatch of app.batch.export_render.write_workbook (the
# function's own module) is what now affects it -- a monkeypatch of
# app.main.write_workbook no longer exists to find.
_render_export = render_export
```

and change the route body's two references:

```python
    try:
        path = _render_export(doc_id, work, rev, kind, sealed)
    except InputUnavailable as e:
```

(was `except _InputUnavailable as e`) and the export route's `_exportable` caller is gone (it lived only inside `_render_export`); nothing else in `app/main.py` called `_exportable` directly, so no other change is needed there.

Now update the three tests in `tests/test_api.py` whose monkeypatch target moved. Find:

```python
    monkeypatch.setattr(main, "write_workbook", fake_xlsx)
    monkeypatch.setattr(main, "render_ballooned_pdf", fake_pdf)
```

replace with:

```python
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook", fake_xlsx)
    monkeypatch.setattr(export_render, "render_ballooned_pdf", fake_pdf)
```

Find (the 500-path test):

```python
    monkeypatch.setattr(main, "write_workbook", broken)
```

replace with:

```python
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook", broken)
```

Find (the concurrency test):

```python
    monkeypatch.setattr(main, "write_workbook", slow_write)
```

replace with:

```python
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook", slow_write)
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_export_render.py tests/test_api.py -q`
Expected: PASS, none of the three previously-passing tests now silently pass for the wrong reason — confirm by temporarily reverting one monkeypatch line and re-running to see it fail, then restore it.

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4 passed (2 new in `test_export_render.py`, 2 from Task 1), 2 skipped

```bash
git add app/batch/export_render.py app/main.py tests/batch/test_export_render.py tests/test_api.py
git commit -m "$(cat <<'EOF'
batch: move the single-export render path to app/batch/export_render.py

B4's mass export needs the exact function a lone download uses, under the
same cache and lock -- not a second renderer that happens to agree today.
app.main._render_export is now a direct alias, so the only change outside
app/batch is three tests' monkeypatch target.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `names.py` — Windows-safe export naming

**Data-integrity: yes** (names written into a ZIP members list are effectively permanent once downloaded; a collision silently drops a file).

**Files:**
- Create: `app/batch/names.py`
- Test: `tests/batch/test_names.py` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/batch/test_names.py`:

```python
"""Windows-safe file/folder names for the mass-export ZIP (batch design §4).
paths.safe_rel_parts already keeps a folder name free of control, format and
line-break characters (B2); its own docstring lists three Windows traps it
deliberately leaves unhandled, because nothing wrote a client-supplied name
to a real filesystem path until this export does. This module closes them
for every component the export itself builds -- a batch name, a folder from
an upload's rel_path, a file stem -- never for a path that is merely stored
or shown elsewhere."""
import pytest

from app.batch.names import unique_export_path, windows_safe_component


@pytest.mark.parametrize("name,expected", [
    ("Flansch", "Flansch"),
    ("a:b", "a_b"),
    ("a/b\\c", "a_b_c"),
    ("a<b>c", "a_b_c"),
    ('a"b|c?d*e', "a_b_c_d_e"),
    ("trailing.", "trailing_"),
    ("trailing ", "trailing_"),
    ("", "_"),
])
def test_windows_safe_component_replaces_unsafe_characters(name, expected):
    assert windows_safe_component(name) == expected


@pytest.mark.parametrize("name", ["CON", "con", "NUL.txt", "COM1", "LPT9"])
def test_windows_safe_component_suffixes_a_reserved_device_name(name):
    out = windows_safe_component(name)
    assert out != name
    assert out.rstrip("_").upper().split(".")[0] in {
        "CON", "NUL", "COM1", "LPT9"} or out.startswith(name.split(".")[0])


def test_windows_safe_component_folds_the_fullwidth_solidus_via_nfkc():
    # U+FF0F FULLWIDTH SOLIDUS has an NFKC compatibility decomposition to
    # ordinary '/' -- left alone it would silently create an extra folder
    # level the user never typed.
    assert windows_safe_component("a／b") == "a_b"


def test_windows_safe_component_folds_the_division_slash_explicitly():
    # U+2215 DIVISION SLASH has NO NFKC decomposition to '/' -- it must be
    # caught by name, not by normalisation, or it passes through untouched.
    assert windows_safe_component("a∕b") == "a_b"


def test_windows_safe_component_is_never_empty():
    assert windows_safe_component(None) == "_"
    assert windows_safe_component("   ") == "_"


def test_unique_export_path_joins_folder_and_suffixes_on_collision():
    used = set()
    first = unique_export_path(used, ["Batch 1"], "w1", ".xlsx")
    second = unique_export_path(used, ["Batch 1"], "w1", ".xlsx")
    assert first == "Batch 1/w1.xlsx"
    assert second == "Batch 1/w1 (2).xlsx"
    assert used == {first, second}


def test_unique_export_path_with_no_folder():
    used = set()
    assert unique_export_path(used, [], "w1", ".pdf") == "w1.pdf"
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_names.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.names'`

- [ ] **Step 3: Implement** — create `app/batch/names.py`:

```python
"""Windows-safe file and folder names for the mass-export ZIP (batch design
§4). app/batch/paths.py's safe_rel_parts already keeps a folder name free of
control, format and line-break characters, and its own docstring names three
traps it deliberately leaves open -- no client-supplied name was ever
written to a real filesystem path until this export's ZIP. This module
closes them for every component the export itself builds."""
import re
import unicodedata

# CON, PRN, AUX, NUL, COM1-9, LPT1-9 -- reserved with or without an
# extension (Windows treats "NUL.txt" the same as "NUL").
_RESERVED = re.compile(r"(?i)\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|\Z)")
# ':' starts an NTFS alternate data stream; '<>"|?*' and the two path
# separators cannot appear in a single Windows path component at all.
_WINDOWS_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Look-alike separators Unicode does NOT fold via NFKC (the fullwidth
# solidus U+FF0F does fold, to ordinary '/', and is caught by _WINDOWS_UNSAFE
# after normalisation below -- these do not, and must be named explicitly).
_LOOKALIKE_SEPARATORS = str.maketrans({"∕": "/", "⁄": "/"})


def windows_safe_component(name) -> str:
    """One path component (a batch name, a folder from rel_path, a file
    stem) made safe to write as-is on Windows. Rewrites rather than refuses:
    a mass export must account for every drawing it was asked for (its
    manifest), and refusing one because its title block happens to collide
    with a reserved device name would be a worse failure than suffixing it.

    Order: look-alike separators first (so DIVISION SLASH becomes an
    ordinary '/' before anything else looks at it), then NFKC (folds the
    fullwidth solidus and fullwidth dot to their ASCII forms), then the
    unsafe-character sweep, then trailing dot/space stripping -- marked with
    a trailing '_' when it actually removed something, so "trailing." and
    "trailing" can never collide silently inside one export -- then the
    reserved-name check."""
    s = (name or "").translate(_LOOKALIKE_SEPARATORS)
    s = unicodedata.normalize("NFKC", s)
    s = _WINDOWS_UNSAFE.sub("_", s)
    stripped = s.rstrip(" .")
    if stripped != s and stripped:
        s = stripped + "_"
    else:
        s = stripped
    if not s:
        s = "_"
    if _RESERVED.match(s):
        s = s + "_"
    return s


def unique_export_path(used: set, folder_parts: list, stem: str, ext: str) -> str:
    """`folder/stem.ext`, suffixed " (2)", " (3)", ... the first time that
    exact path has already been used in THIS export. `used` is mutated: the
    caller passes the same set across every document and both of its own
    files, so a drawing's xlsx and its own ballooned PDF, and two different
    drawings that sanitise to the same name, never collide."""
    folder = "/".join(p for p in folder_parts if p)
    base = f"{folder}/{stem}" if folder else stem
    candidate = f"{base}{ext}"
    n = 2
    while candidate in used:
        candidate = f"{base} ({n}){ext}"
        n += 1
    used.add(candidate)
    return candidate
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_names.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4 + 11 passed, 2 skipped

```bash
git add app/batch/names.py tests/batch/test_names.py
git commit -m "$(cat <<'EOF'
batch: Windows-safe names for the mass-export ZIP

safe_rel_parts/clean_batch_name never had to handle NTFS-reserved names,
trailing dots/spaces, or look-alike separators, because nothing wrote a
client-supplied name to a real filesystem path until this ZIP does.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Repo — export job CRUD

**Data-integrity: yes** (the only record of a mass export's progress and outcome).

**Files:**
- Modify: `app/batch/repo.py`
- Test: `tests/batch/test_repo.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_repo.py`:

```python
from app.batch.repo import UnknownExport


def test_create_export_stores_the_document_ids_in_order(repo):
    job = repo.create_export(["b", "a", "c"])
    assert job["document_ids"] == ["b", "a", "c"]
    assert (job["state"], job["done"], job["total"]) == ("running", 0, 3)


def test_update_export_progress_advances_done(repo):
    job = repo.create_export(["a", "b"])
    repo.update_export_progress(job["id"], 1)
    assert repo.get_export(job["id"])["done"] == 1


def test_finish_export_records_state_and_error_type(repo):
    job = repo.create_export(["a"])
    repo.finish_export(job["id"], "failed", error_type="OSError")
    got = repo.get_export(job["id"])
    assert (got["state"], got["error_type"]) == ("failed", "OSError")


def test_get_export_of_an_unknown_id_is_404(repo):
    with pytest.raises(UnknownExport):
        repo.get_export("f" * 32)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_repo.py -q`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'create_export'`

- [ ] **Step 3: Implement** — in `app/batch/repo.py`, add near the other `Unknown*` classes:

```python
class UnknownExport(BatchError):
    status = 404
```

and add these methods after `mark_exported` (before `delete`):

```python
    @staticmethod
    def _export_row(c, export_id) -> dict:
        row = c.execute("SELECT * FROM export WHERE id = ?", (export_id,)).fetchone()
        if row is None:
            raise UnknownExport(f"no export {str(export_id)[:8]}")
        d = dict(row)
        d["document_ids"] = json.loads(d["document_ids"])
        return d

    def create_export(self, document_ids: list) -> dict:
        """A mass-export job (batch design §4): a row of its own, so a poll
        from any tab sees the same progress and a server restart mid-build
        reads as `failed`, not frozen (app/batch/exports.py's startup
        recovery, Task 9)."""
        now = self._clock()
        export_id = uuid.uuid4().hex
        with self._tx() as c:
            c.execute(
                "INSERT INTO export (id, document_ids, state, done, total,"
                " created_at, updated_at) VALUES (?, ?, 'running', 0, ?, ?, ?)",
                (export_id, json.dumps(list(document_ids)), len(document_ids), now, now))
            return self._export_row(c, export_id)

    def update_export_progress(self, export_id, done: int) -> None:
        with self._tx() as c:
            c.execute("UPDATE export SET done = ?, updated_at = ? WHERE id = ?",
                      (done, self._clock(), export_id))

    def finish_export(self, export_id, state: str, error_type: str = None) -> None:
        with self._tx() as c:
            c.execute("UPDATE export SET state = ?, error_type = ?, updated_at = ?"
                      " WHERE id = ?", (state, error_type, self._clock(), export_id))

    def get_export(self, export_id) -> dict:
        with self._lock:
            return self._export_row(self._conn, export_id)

    def running_exports(self) -> list:
        """At startup: jobs a crashed process left `running` forever
        (app/batch/exports.py's recovery marks each one `failed`, since the
        thread that was building it is gone -- there is nothing to resume a
        ZIP build from)."""
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                "SELECT id FROM export WHERE state = 'running'").fetchall()]
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_repo.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4 + 11 + 5 passed, 2 skipped

```bash
git add app/batch/repo.py tests/batch/test_repo.py
git commit -m "$(cat <<'EOF'
batch: add export-job CRUD to the repo

A mass export's progress and outcome live in the index, not an in-memory
dict, so a poll from any tab and a server restart both see the truth.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `exports.py` — the mass-export engine

**Data-integrity: yes** (builds the ZIP a reviewer takes as the deliverable; a per-drawing fault must not fail the job, and a render fault must still land in the manifest).

**Files:**
- Create: `app/batch/exports.py`
- Modify: `app/batch/paths.py` (DataDir gets an `exports` dir)
- Test: `tests/batch/test_exports.py` (new), `tests/batch/test_db.py` (`DataDir` addition)

- [ ] **Step 1: Write the failing tests.** First, append to `tests/batch/test_db.py`:

```python
def test_data_dir_has_an_exports_subdirectory(tmp_path):
    data = DataDir(tmp_path)
    assert data.exports.is_dir()
    assert data.export_zip_path("a" * 32) == data.exports / ("a" * 32 + ".zip")
```

Then create `tests/batch/test_exports.py`:

```python
"""Mass export (batch design §4): a ZIP of many drawings' files plus a
manifest, built on its own thread so it never queues behind the GPU worker,
rendered through the SAME function (app.batch.export_render.render_export)
a lone download uses."""
import csv
import io
import json
import zipfile

import pytest

from app.batch import db
from app.batch.exports import Exports
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.review.store import ReviewStore


@pytest.fixture
def rig(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    repo = Repo(conn)
    data = DataDir(tmp_path / "data")
    store = ReviewStore(tmp_path / "reviews")
    return repo, data, store, Exports(repo, data, store)


def _finished_document(repo, data, store, doc_id, *, rows, batch_name="Flansch"):
    """A drawing sealed at revision 1, with the files export_render needs."""
    doc = repo.create_document(doc_id, "w1.pdf", pages=1, sha256=None, batch_name=batch_name)
    repo.enqueue(doc_id)
    repo.claim_next()
    d = data.doc_dir(doc_id)
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps(
        {"notes": None, "marks": None, "title_block": []}), encoding="utf-8")
    (d / "input.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    repo.mark_ready(doc_id)
    writer = store.create(doc_id, {}, {"rows": rows})
    store.seal(doc_id, writer, 0, rows, [])
    repo.mark_finished(doc_id, 1, 0)
    return doc["batch_id"]


def test_mass_export_builds_a_zip_with_a_manifest(rig, monkeypatch):
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])   # synchronous for the test
    status = repo.get_export(job["id"])
    assert status["state"] == "done"
    assert status["done"] == status["total"] == 1
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        names = set(zf.namelist())
        assert "manifest.csv" in names
        assert any(n.endswith(".xlsx") for n in names)
        assert any(n.endswith("_ballooned.pdf") for n in names)
        rows_out = list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))
    assert rows_out[0]["skip_reason"] == ""
    assert rows_out[0]["batch"] == "Flansch"
    assert rows_out[0]["revision_exported"] == "1"


def test_mass_export_skips_an_unfinished_drawing_without_failing_the_job(rig, monkeypatch):
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    repo.create_document("b" * 32, "w2.pdf", pages=1, sha256=None)   # left `staged`

    job = exports.start(["a" * 32, "b" * 32])
    exports._run(job["id"], ["a" * 32, "b" * 32])
    assert repo.get_export(job["id"])["state"] == "done"
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        rows_out = {r["file"] or r["skip_reason"]: r
                    for r in csv.DictReader(io.StringIO(zf.read("manifest.csv").decode()))}
    reasons = [r["skip_reason"] for r in rows_out.values()]
    assert "not_finished" in reasons
    assert sum(1 for r in reasons if r == "") == 1


def test_a_drawing_that_fails_to_render_lands_in_the_manifest_not_the_job(rig, monkeypatch):
    """One drawing's failure must not fail the whole job (batch design §5)."""
    repo, data, store, exports = rig
    import app.batch.export_render as export_render

    def broken(rows, out, **kw):
        raise TypeError("boom")
    monkeypatch.setattr(export_render, "write_workbook", broken)
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])
    assert repo.get_export(job["id"])["state"] == "done"
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        (manifest_row,) = list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))
    assert manifest_row["skip_reason"] == "export_error: TypeError"


def test_a_reopened_drawing_exports_its_last_sealed_revision_with_a_note(rig, monkeypatch):
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"XLSX"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    repo.events_appended("a" * 32, 1)   # a new reviewer event reopens it: finished -> in_review

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        (manifest_row,) = list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))
    assert manifest_row["skip_reason"] == "reopened — later changes not included"
    assert manifest_row["file"]   # still exported, not skipped


def test_single_export_and_mass_export_are_byte_identical(rig, monkeypatch, tmp_path):
    """Export determinism (batch design §5): a drawing exported alone and
    the same drawing inside a mass-export ZIP must be the same bytes, because
    both call app.batch.export_render.render_export under the same cache."""
    repo, data, store, exports = rig
    import app.batch.export_render as export_render
    monkeypatch.setattr(export_render, "write_workbook",
                        lambda rows, out, **kw: out.write_bytes(b"deterministic xlsx"))
    monkeypatch.setattr(export_render, "render_ballooned_pdf",
                        lambda src, rows, dpi, out_path: out_path.write_bytes(b"%PDF deterministic"))
    row = {"id": "a", "pos": 1, "nominal": "20", "suggested": False}
    _finished_document(repo, data, store, "a" * 32, rows=[row])
    sealed = store.revision("a" * 32, 1)
    direct = export_render.render_export(
        "a" * 32, data.doc_dir("a" * 32), 1, "xlsx", sealed).read_bytes()

    job = exports.start(["a" * 32])
    exports._run(job["id"], ["a" * 32])
    with zipfile.ZipFile(data.export_zip_path(job["id"])) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xlsx")]
        zipped = zf.read(names[0])
    assert zipped == direct


def test_delete_expired_zips_removes_only_old_ones(rig):
    import time
    repo, data, store, exports = rig
    job = repo.create_export(["a" * 32])
    p = data.export_zip_path(job["id"])
    p.write_bytes(b"x")
    import os
    old = time.time() - 25 * 3600
    os.utime(p, (old, old))
    fresh_job = repo.create_export(["b" * 32])
    data.export_zip_path(fresh_job["id"]).write_bytes(b"y")
    removed = exports.delete_expired_zips()
    assert removed == 1
    assert not p.is_file()
    assert data.export_zip_path(fresh_job["id"]).is_file()
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_exports.py tests/batch/test_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.exports'` (and `AttributeError: 'DataDir' object has no attribute 'exports'`)

- [ ] **Step 3: Implement.** First, in `app/batch/paths.py`, extend `DataDir.__init__` and add a method:

```python
class DataDir:
    def __init__(self, root):
        self.root = Path(root)
        self.docs = self.root / "docs"
        self.docs.mkdir(parents=True, exist_ok=True)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        # Mass-export ZIPs (batch design §4): a sibling of docs/ and
        # uploads/, never inside either -- a ZIP is not one drawing's file
        # and must not be swept by anything that walks docs/<id>/.
        self.exports = self.root / "exports"
        self.exports.mkdir(parents=True, exist_ok=True)

    ...

    def export_zip_path(self, export_id) -> Path:
        if not isinstance(export_id, str) or not DOC_ID_RE.fullmatch(export_id):
            raise ValueError("malformed export id")
        return self.exports / f"{export_id}.zip"
```

Now create `app/batch/exports.py`:

```python
"""Mass export (batch design §4): a selection, a batch, or a filtered set of
finished drawings becomes one ZIP -- per-drawing xlsx + ballooned PDF laid
out by batch/folder, plus a manifest.csv that accounts for every drawing
asked for -- built on its own thread so it never queues behind the GPU
worker. Renders through app.batch.export_render.render_export, the SAME
function (and the same per-(doc, rev, kind) cache and lock) a lone download
uses, so a drawing exported alone and the same drawing inside this ZIP are
byte-identical by construction."""
import csv
import io
import logging
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from app.batch.export_render import InputUnavailable, exportable, render_export
from app.batch.names import unique_export_path, windows_safe_component
from app.batch.repo import UnknownDocument
from app.models import Characteristic

log = logging.getLogger(__name__)

MANIFEST_FIELDS = ["file", "folder", "batch", "state", "revision_exported",
                   "sealed_at", "rows_exported", "allowed_out_of_scope", "skip_reason"]

# ZIPs are rebuildable from the same selection (batch design §4): deleting
# one after a day costs nothing but a re-click, and keeping every mass
# export forever would grow without bound on a server nobody is asked to
# clean up by hand.
ZIP_TTL_SECONDS = 24 * 3600


def _manifest_csv(rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


class Exports:
    def __init__(self, repo, data, store):
        self.repo = repo
        self.data = data
        self.store = store

    def start(self, document_ids: list) -> dict:
        job = self.repo.create_export(document_ids)
        t = threading.Thread(target=self._run, args=(job["id"], list(document_ids)),
                             name=f"sindri-export-{job['id'][:8]}", daemon=True)
        t.start()
        return job

    def status(self, export_id) -> dict:
        return self.repo.get_export(export_id)

    def _run(self, export_id, document_ids) -> None:
        zip_path = self.data.export_zip_path(export_id)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.data.exports, prefix=f"{export_id}.", suffix=".tmp.zip")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            used_paths = set()
            rows = []
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, doc_id in enumerate(document_ids):
                    rows.append(self._export_one(zf, doc_id, used_paths))
                    self.repo.update_export_progress(export_id, i + 1)
                zf.writestr("manifest.csv", _manifest_csv(rows))
            tmp.replace(zip_path)       # atomic: no reader ever sees a partial ZIP
            self.repo.finish_export(export_id, "done")
        except Exception as e:
            # A fault building the ZIP ITSELF (disk full, a permissions
            # error) -- not a single drawing's render fault, which
            # _export_one already turned into a manifest row instead of
            # raising. TYPE only (CLAUDE.md §1).
            log.warning("mass export %s failed: %s", export_id[:8], type(e).__name__)
            self.repo.finish_export(export_id, "failed", error_type=type(e).__name__)
        finally:
            tmp.unlink(missing_ok=True)

    def _export_one(self, zf, doc_id, used_paths) -> dict:
        """One manifest row. Never raises: a fault here must not fail the
        whole job (batch design §5) -- it is recorded as an export_error
        skip_reason instead."""
        base = {"file": "", "folder": "", "batch": "", "state": "removed",
                "revision_exported": "", "sealed_at": "", "rows_exported": "",
                "allowed_out_of_scope": "", "skip_reason": "not_finished"}
        try:
            doc = self.repo.get(doc_id)
        except UnknownDocument:
            return base
        batch = self.repo.get_batch(doc["batch_id"])
        folder_parts = [windows_safe_component(batch["name"])]
        if doc["rel_path"]:
            folder_parts += [windows_safe_component(p) for p in doc["rel_path"].split("/") if p]
        stem = windows_safe_component(Path(doc["file_name"]).stem) or "drawing"
        base = {**base, "folder": "/".join(folder_parts), "batch": batch["name"],
                "state": doc["state"], "allowed_out_of_scope": bool(doc["allowed_out_of_scope"]),
                "skip_reason": ""}
        if doc["final_revision"] is None:
            # Never sealed: eligibility is "has a final_revision", exactly
            # the rule the single-document export route already applies,
            # never "current state is finished" (decision 5 -- a reopened
            # drawing's final_revision survives the reopen).
            reason = {"failed": "failed", "purged": "purged"}.get(doc["state"], "not_finished")
            return {**base, "skip_reason": reason}
        try:
            sealed = self.store.revision(doc_id, doc["final_revision"])
            work = self.data.doc_dir(doc_id)
            n_rows = len(exportable([Characteristic.model_validate(r) for r in sealed["rows"]]))
            xlsx_name = unique_export_path(used_paths, folder_parts, stem, ".xlsx")
            pdf_name = unique_export_path(used_paths, folder_parts, f"{stem}_ballooned", ".pdf")
            xlsx_path = render_export(doc_id, work, doc["final_revision"], "xlsx", sealed)
            pdf_path = render_export(doc_id, work, doc["final_revision"], "pdf", sealed)
            zf.write(xlsx_path, arcname=xlsx_name)
            zf.write(pdf_path, arcname=pdf_name)
            self.repo.mark_exported(doc_id, doc["final_revision"])
            # A row still `finished` was exported cleanly; any other state
            # with a final_revision got here only by being reopened after a
            # prior Finish (decision 5) -- its CURRENT content was never
            # sealed, so the file the ZIP holds is its LAST Finish, not what
            # the drawing looks like right now.
            note = "" if doc["state"] == "finished" else "reopened — later changes not included"
            return {**base, "file": Path(xlsx_name).name, "revision_exported": doc["final_revision"],
                    "sealed_at": sealed["sealed_at"], "rows_exported": n_rows, "skip_reason": note}
        except InputUnavailable as e:
            cause = type(e.__cause__ or e).__name__
            return {**base, "revision_exported": doc["final_revision"],
                    "skip_reason": f"export_error: {cause}"}
        except Exception as e:
            log.warning("mass export: drawing %s failed to render: %s",
                        doc_id[:8], type(e).__name__)
            return {**base, "revision_exported": doc["final_revision"],
                    "skip_reason": f"export_error: {type(e).__name__}"}

    def delete_expired_zips(self) -> int:
        cutoff = time.time() - ZIP_TTL_SECONDS
        n = 0
        for p in self.data.exports.glob("*.zip"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    n += 1
            except OSError as e:
                log.warning("export zip cleanup: %s", type(e).__name__)
        return n
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_exports.py tests/batch/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4 + 11 + 5 + 7 passed, 2 skipped

```bash
git add app/batch/exports.py app/batch/paths.py tests/batch/test_exports.py tests/batch/test_db.py
git commit -m "$(cat <<'EOF'
batch: build the mass-export ZIP on its own thread

One drawing's render failure lands in the manifest as export_error and
the rest of the job still builds; a drawing exported alone and the same
drawing inside this ZIP come from the identical render_export call.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: API — `POST /api/exports`, status, download

**Data-integrity: yes** (the only door to the feature; must gate on the same ids the UI showed).

**Files:**
- Modify: `app/main.py`, `app/batch/runtime.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_mass_export_builds_a_zip_and_reports_progress(sample_pdf, stub_backend):
    import app.main as main
    sid = finished(client, sample_pdf)
    r = client.post("/api/exports", json={"document_ids": [sid]})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    main.runtime().exports.status(job_id)   # exists immediately, even before the thread finishes
    import time
    for _ in range(50):
        status = client.get(f"/api/exports/{job_id}").json()
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "done"
    dl = client.get(f"/api/exports/{job_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"


def test_mass_export_of_an_unknown_id_404s():
    assert client.get(f"/api/exports/{'a' * 32}").status_code == 404


def test_downloading_an_export_still_building_is_409():
    import app.main as main
    job = main.runtime().exports.repo.create_export(["x" * 32])
    r = client.get(f"/api/exports/{job['id']}/download")
    assert r.status_code == 409


def test_mass_export_request_rejects_a_malformed_id():
    r = client.post("/api/exports", json={"document_ids": ["not-hex"]})
    assert r.status_code == 422
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_api.py -k mass_export -q`
Expected: FAIL — `404 Not Found` for `POST /api/exports` (route does not exist yet)

- [ ] **Step 3: Implement.** In `app/batch/runtime.py`, wire `Exports` in (shown fully in Task 7, which also adds `Housekeeping`; for this task alone, add just the `Exports` line):

```python
from app.batch.exports import Exports
...
        self.uploads = Uploads(self.repo, self.data, min_free_bytes=min_free_mb * MIB)
        self.exports = Exports(self.repo, self.data, self.store)
```

In `app/main.py`, add near the other request models:

```python
class ExportRequest(BaseModel):
    document_ids: List[DocId]
```

and the three routes, after `export_document`:

```python
@app.post("/api/exports")
def create_export(req: ExportRequest):
    """Mass export (batch design §4): its own thread, started and returned
    immediately -- the browser polls GET /api/exports/{id} for progress."""
    return runtime().exports.start(req.document_ids)


@app.get("/api/exports/{export_id}")
def get_export_status(export_id: str):
    return runtime().exports.status(export_id)


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: str):
    rt = runtime()
    job = rt.exports.status(export_id)
    if job["state"] == "running":
        raise HTTPException(status_code=409, detail="export still building")
    if job["state"] == "failed":
        raise HTTPException(status_code=500, detail="export failed")
    path = rt.data.export_zip_path(export_id)
    if not path.is_file():
        # Deleted by the 24h cleanup (Task 7): rebuildable from the same
        # selection, so this is not a data loss, but the client must ask
        # again rather than get a 200 for a file that no longer exists.
        raise HTTPException(status_code=410, detail="export expired — request it again")
    return FileResponse(path, media_type="application/zip",
                        headers={"Content-Disposition": _content_disposition("sindri-export.zip")})
```

`DocId` is already defined earlier in `app/main.py` (B2's strict 32-hex pattern); `ExportRequest` reuses it so a malformed id gets the same fixed 422 shape every import route already gives — confirm `_IMPORT_PATHS` in the validation-error handler does NOT need `/api/exports` added: that handler exists to hide a client FILE NAME from a 422 body, and `document_ids` are already-validated hex ids, never client text, so the default FastAPI 422 body is fine here and no change to `_validation_error` is needed.

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_api.py -k mass_export -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4 + 11 + 5 + 7 + 4 passed, 2 skipped

```bash
git add app/main.py app/batch/runtime.py tests/test_api.py
git commit -m "$(cat <<'EOF'
batch: expose mass export over HTTP

POST /api/exports starts a job on its own thread and returns immediately;
GET .../download refuses a still-building job (409) or an expired one
(410, rebuildable from the same selection) rather than serving nothing.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

## Milestone 2: retention

### Task 7: Repo — `purge_candidates`, `purge`

**Data-integrity: yes** (irreversible deletion; the guard against a race with reopen/re-export).

**Files:**
- Modify: `app/batch/repo.py`
- Test: `tests/batch/test_repo.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_repo.py`:

```python
def test_purge_candidates_only_lists_finished_and_exported(repo):
    (a,) = ids(1)
    staged(repo, a)
    assert repo.purge_candidates("2099-01-01") == []


def test_purge_turns_a_finished_exported_drawing_into_a_tombstone(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.mark_finished(a, 1, 0)
    repo.mark_exported(a, 1)
    repo._conn.execute("UPDATE document SET exported_at = '2020-01-01T00:00:00' WHERE id = ?", (a,))
    assert repo.purge_candidates("2099-01-01") == [a]
    assert repo.purge(a, "2099-01-01") is True
    row = repo.get(a)
    assert row["state"] == "purged" and row["purged_at"]


def test_purge_refuses_a_drawing_reopened_since_its_export(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.mark_finished(a, 1, 0)
    repo.mark_exported(a, 1)
    repo._conn.execute("UPDATE document SET exported_at = '2020-01-01T00:00:00' WHERE id = ?", (a,))
    repo.events_appended(a, 1)   # reopened: finished -> in_review
    assert repo.purge(a, "2099-01-01") is False
    assert repo.get(a)["state"] == "in_review"


def test_purge_never_touches_an_unexported_drawing(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.mark_finished(a, 1, 0)
    # never exported
    assert repo.purge_candidates("2099-01-01") == []
    assert repo.purge(a, "2099-01-01") is False
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_repo.py -k purge -q`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'purge_candidates'`

- [ ] **Step 3: Implement** — in `app/batch/repo.py`, add after `running_exports`:

```python
    def purge_candidates(self, cutoff: str) -> list:
        """Finished, exported drawings whose last export is older than
        `cutoff`. A listing only: purge() re-checks the identical condition
        inside its own guarded UPDATE, so a race between this read and the
        sweep reaching a given id (a reopen, a fresh export) simply leaves
        that row alone instead of purging stale information."""
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                "SELECT id FROM document WHERE state = 'finished'"
                " AND exported_at IS NOT NULL AND exported_at <= ?"
                " ORDER BY exported_at, rowid", (cutoff,)).fetchall()]

    def purge(self, doc_id, cutoff: str) -> bool:
        """Turn a finished, exported-long-enough-ago drawing into a
        tombstone: state and a timestamp only (decisions 7, 14, 15, 20) --
        file_name, batch_id, and every progress count stay exactly as they
        were, because the work list still shows the row with nothing left to
        open or export. False if the row no longer matches this EXACT
        condition (reopened, re-exported more recently, already purged, or
        gone) -- the caller (Housekeeping) deletes files only when this
        returns True, so a stale candidate list can never cost a file that a
        human has since touched again."""
        with self._tx() as c:
            cur = c.execute(
                "UPDATE document SET state = 'purged', purged_at = ? WHERE id = ?"
                " AND state = 'finished' AND exported_at IS NOT NULL AND exported_at <= ?",
                (self._clock(), doc_id, cutoff))
            return cur.rowcount == 1
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_repo.py -k purge -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4 + 11 + 5 + 7 + 4 + 4 passed, 2 skipped

```bash
git add app/batch/repo.py tests/batch/test_repo.py
git commit -m "$(cat <<'EOF'
batch: add retention's purge query and guarded transition to the repo

Marking the row purged happens before any file is deleted, and only a
successful, re-checked transition authorises the delete -- a drawing
reopened or re-exported since the candidate list was read is left alone.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `housekeeping.py` — retention sweep + export-ZIP cleanup

**Data-integrity: yes** (the thread that actually deletes client files).

**Files:**
- Create: `app/batch/housekeeping.py`
- Modify: `app/batch/runtime.py`
- Test: `tests/batch/test_housekeeping.py` (new), `tests/batch/test_runtime.py`

- [ ] **Step 1: Write the failing tests** — create `tests/batch/test_housekeeping.py`:

```python
"""Retention (batch design §4): a daily sweep (and one at startup) deletes
the files of every finished, exported-long-enough-ago drawing, leaving a
`purged` tombstone row; review records are never touched by this file."""
import itertools

import pytest

from app.batch import db
from app.batch.housekeeping import Housekeeping
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.review.store import ReviewStore


@pytest.fixture
def rig(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tick = itertools.count()
    clock = {"now": "2026-10-08T00:00:00.000000+00:00"}
    repo = Repo(conn, clock=lambda: clock["now"])
    data = DataDir(tmp_path / "data")
    store = ReviewStore(tmp_path / "reviews")

    class FakeExports:
        def __init__(self):
            self.deleted = 0

        def delete_expired_zips(self):
            self.deleted += 1
            return 0

    exports = FakeExports()
    hk = Housekeeping(repo, data, exports, retention_days=30)
    return repo, data, store, exports, hk, clock


def _old_finished_drawing(repo, data, doc_id, exported_at):
    repo.create_document(doc_id, "w.pdf", pages=1, sha256=None)
    repo.enqueue(doc_id)
    repo.claim_next()
    repo.mark_ready(doc_id)
    repo.mark_finished(doc_id, 1, 0)
    repo.mark_exported(doc_id, 1)
    repo._conn.execute("UPDATE document SET exported_at = ? WHERE id = ?",
                       (exported_at, doc_id))
    d = data.doc_dir(doc_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "input.pdf").write_bytes(b"x")


def test_sweep_retention_purges_an_old_exported_drawing_and_deletes_its_files(rig):
    repo, data, store, exports, hk, clock = rig
    doc_id = "a" * 32
    _old_finished_drawing(repo, data, doc_id, "2020-01-01T00:00:00.000000+00:00")
    result = hk.sweep_retention()
    assert result["purged"] == 1
    assert repo.get(doc_id)["state"] == "purged"
    assert not data.doc_dir(doc_id).exists()


def test_sweep_retention_leaves_a_recent_export_alone(rig):
    repo, data, store, exports, hk, clock = rig
    doc_id = "a" * 32
    _old_finished_drawing(repo, data, doc_id, clock["now"])
    result = hk.sweep_retention()
    assert result["purged"] == 0
    assert repo.get(doc_id)["state"] == "finished"
    assert data.doc_dir(doc_id).exists()


def test_sweep_retention_is_off_at_zero_days(rig):
    repo, data, store, exports, hk, clock = rig
    hk.retention_days = 0
    doc_id = "a" * 32
    _old_finished_drawing(repo, data, doc_id, "2020-01-01T00:00:00.000000+00:00")
    assert hk.sweep_retention() == {"purged": 0}
    assert repo.get(doc_id)["state"] == "finished"


def test_purge_one_marks_before_deleting_so_a_failed_mark_deletes_nothing(rig):
    repo, data, store, exports, hk, clock = rig
    doc_id = "a" * 32
    _old_finished_drawing(repo, data, doc_id, "2020-01-01T00:00:00.000000+00:00")
    repo.events_appended(doc_id, 1)   # reopened in between: purge() must refuse
    assert hk.purge_one(doc_id, "2099-01-01") is False
    assert data.doc_dir(doc_id).exists()


def test_start_runs_one_sweep_immediately_and_stops_cleanly(rig):
    repo, data, store, exports, hk, clock = rig
    hk.start()
    try:
        assert exports.deleted >= 1   # the immediate sweep already ran
    finally:
        hk.stop(timeout=2.0)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_housekeeping.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.housekeeping'`

- [ ] **Step 3: Implement** — create `app/batch/housekeeping.py`:

```python
"""Retention and export-ZIP cleanup (batch design §4): a second always-on
background thread, alongside the extraction Worker, owned by Runtime and
started only after Worker.start() has won the single-process lock -- the
same cross-process guarantee B2's import recovery relies on, reused without
touching Worker itself (Housekeeping has nothing to do with the GPU queue).
It sweeps once immediately, satisfying "one at startup", then on a fixed
interval -- short enough that an old drawing does not wait long past its
configured retention window, with no separate "has a day passed" bookkeeping
needed, because a sweep that finds nothing to purge is cheap."""
import logging
import shutil
import threading
from datetime import timedelta

log = logging.getLogger(__name__)

# How often the loop wakes to check. Not "daily" literally -- see the
# module docstring: there is no harm in checking more often than that.
_TICK_SECONDS = 3600


class Housekeeping:
    def __init__(self, repo, data, exports, retention_days: int, tick_seconds=_TICK_SECONDS):
        self.repo = repo
        self.data = data
        self.exports = exports
        self.retention_days = retention_days
        self._tick = tick_seconds
        self._thread = None
        self._stop = threading.Event()

    def sweep_retention(self) -> dict:
        """0 = off (decision 7): no cutoff is computed, nothing is purged."""
        if self.retention_days <= 0:
            return {"purged": 0}
        cutoff = (self.repo.now() - timedelta(days=self.retention_days)).isoformat(
            timespec="microseconds")
        purged = sum(1 for doc_id in self.repo.purge_candidates(cutoff)
                    if self.purge_one(doc_id, cutoff))
        return {"purged": purged}

    def purge_one(self, doc_id, cutoff) -> bool:
        """Mark the row purged FIRST, delete its files SECOND -- never the
        other way round (repo.purge's own docstring explains why: a request
        racing this sweep must see `purged` the instant the row says so,
        never pass a state check and then find its files gone). Review
        records are never touched here -- they live under SINDRI_REVIEW_DIR,
        a separate tree this module never imports a path into."""
        if not self.repo.purge(doc_id, cutoff):
            return False
        shutil.rmtree(self.data.doc_dir(doc_id), ignore_errors=True)
        return True

    def sweep_export_zips(self) -> int:
        return self.exports.delete_expired_zips()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._run_once()
        self._thread = threading.Thread(
            target=self._loop, name="sindri-housekeeping", daemon=True)
        self._thread.start()

    def _run_once(self) -> None:
        try:
            self.sweep_retention()
        except Exception as e:
            log.warning("retention sweep failed: %s", type(e).__name__)
        try:
            self.sweep_export_zips()
        except Exception as e:
            log.warning("export zip cleanup failed: %s", type(e).__name__)

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            self._run_once()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_housekeeping.py -q`
Expected: PASS

- [ ] **Step 5: Wire it into `Runtime`.** In `app/batch/runtime.py`:

```python
from app.batch.housekeeping import Housekeeping

# How long a finished, exported drawing's files are kept before retention
# deletes them (decision 7); 0 turns retention off.
DEFAULT_RETENTION_DAYS = 30


class Runtime:
    def __init__(self, data_dir, review_dir, extract_fn, header_fn,
                 min_free_mb: int = DEFAULT_MIN_FREE_MB,
                 retention_days: int = DEFAULT_RETENTION_DAYS):
        if min_free_mb < 0:
            raise ValueError("the free-disk reserve cannot be negative")
        if retention_days < 0:
            raise ValueError("the retention window cannot be negative")
        ...
        self.uploads = Uploads(self.repo, self.data, min_free_bytes=min_free_mb * MIB)
        self.exports = Exports(self.repo, self.data, self.store)
        self.housekeeping = Housekeeping(self.repo, self.data, self.exports,
                                         retention_days=retention_days)
        self.worker = Worker(...)   # unchanged

    @classmethod
    def from_env(cls, extract_fn, header_fn) -> "Runtime":
        data = Path(os.environ.get("SINDRI_DATA_DIR") or "/data")
        reviews = Path(os.environ.get("SINDRI_REVIEW_DIR") or data / "reviews")
        min_free = int(os.environ.get("SINDRI_MIN_FREE_MB") or DEFAULT_MIN_FREE_MB)
        retention_days = int(os.environ.get("SINDRI_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
        return cls(data, reviews, extract_fn, header_fn,
                  min_free_mb=min_free, retention_days=retention_days)

    def start(self) -> None:
        self.worker.start()
        # Only after the worker holds its single-process lock: a second
        # uvicorn worker that failed to get it never starts housekeeping
        # either, so two processes can never purge the same drawing twice.
        self.housekeeping.start()

    def close(self) -> None:
        self.housekeeping.stop()
        self.worker.stop()
        if not self.worker.running:
            self.conn.close()
```

Append to `tests/batch/test_runtime.py`:

```python
def test_runtime_rejects_a_negative_retention_window(tmp_path):
    with pytest.raises(ValueError):
        Runtime(tmp_path / "data", tmp_path / "reviews", lambda *a, **k: None,
               lambda *a: None, retention_days=-1)


def test_runtime_starts_and_stops_housekeeping_with_the_worker(tmp_path):
    rt = Runtime(tmp_path / "data", tmp_path / "reviews", lambda *a, **k: None,
                lambda *a: None, retention_days=0)
    rt.start()
    try:
        assert rt.housekeeping._thread is not None
    finally:
        rt.close()
    assert rt.housekeeping._thread is None
```

- [ ] **Step 6: Run to confirm pass**

Run: `python -m pytest tests/batch/test_housekeeping.py tests/batch/test_runtime.py -q`
Expected: PASS

- [ ] **Step 7: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4+11+5+7+4+4+6+2 passed, 2 skipped

```bash
git add app/batch/housekeeping.py app/batch/runtime.py tests/batch/test_housekeeping.py tests/batch/test_runtime.py
git commit -m "$(cat <<'EOF'
batch: run retention and export-zip cleanup from their own thread

Started only once the extraction worker holds its single-process lock,
so two uvicorn workers can never purge the same drawing twice. Marks a
drawing purged before deleting its files, never the other way round.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: API — 410 for a purged (or never-finished) drawing

**Data-integrity: yes** (the user-visible half of the tombstone guarantee: no reopen, no export).

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_opening_a_purged_drawing_is_410(sample_pdf, stub_backend):
    import app.main as main
    sid = finished(client, sample_pdf)
    rt = main.runtime()
    rt.repo._conn.execute(
        "UPDATE document SET exported_at = '2020-01-01T00:00:00.000000+00:00' WHERE id = ?",
        (sid,))
    assert rt.repo.purge(sid, "2099-01-01") is True
    r = client.post(f"/api/documents/{sid}/open")
    assert r.status_code == 410


def test_exporting_a_purged_drawing_is_410(sample_pdf, stub_backend):
    import app.main as main
    sid = finished(client, sample_pdf)
    rt = main.runtime()
    rt.repo._conn.execute(
        "UPDATE document SET exported_at = '2020-01-01T00:00:00.000000+00:00' WHERE id = ?",
        (sid,))
    assert rt.repo.purge(sid, "2099-01-01") is True
    r = client.get(f"/api/documents/{sid}/export/xlsx")
    assert r.status_code == 410
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_api.py -k purged -q`
Expected: FAIL — both currently answer 409 ("drawing is purged, not ready for review") / 409 ("finish the review before exporting"), not 410.

- [ ] **Step 3: Implement** — in `app/main.py`, `open_document`:

```python
    doc = rt.repo.get(doc_id)
    if doc["state"] == "purged":
        # A tombstone (batch design §4, decision 8): permanently gone, not
        # merely "not ready yet" -- a different HTTP status so the UI's
        # retry logic for the two cases does not conflate them.
        raise HTTPException(status_code=410, detail="drawing was purged after retention")
    if doc["state"] not in ("ready", "in_review", "finished"):
        raise HTTPException(status_code=409,
                            detail=f"drawing is {doc['state']}, not ready for review")
```

and `export_document`:

```python
    doc = rt.repo.get(doc_id)
    if doc["state"] == "purged":
        raise HTTPException(status_code=410, detail="drawing was purged after retention")
    if rev is None:
        rev = doc["final_revision"]
    if rev is None:
        raise HTTPException(status_code=409, detail="finish the review before exporting")
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_api.py -k purged -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 4+11+5+7+4+4+6+2+2 passed, 2 skipped

```bash
git add app/main.py tests/test_api.py
git commit -m "$(cat <<'EOF'
batch: a purged drawing answers 410, not 409, on open and export

A tombstone is a permanent refusal; 409 ("not ready yet") invites a
retry that can never succeed. The UI's two recovery paths now differ.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

## Milestone 3: access token

### Task 10: `app/auth.py` — the token gate

**Data-integrity: yes** (a security boundary; a timing leak or a logged token is the failure mode this task exists to prevent).

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_auth.py` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/test_auth.py`:

```python
"""Optional shared access token (batch design §1, decision 23). Unset
SINDRI_ACCESS_TOKEN -> open, as today. Set -> every /api/* route except the
login route and /api/health requires it, by header or by the cookie a
successful login sets once."""
from app import auth


def test_configured_token_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("SINDRI_ACCESS_TOKEN", raising=False)
    assert auth.configured_token() is None


def test_configured_token_is_none_for_an_empty_string(monkeypatch):
    """An accidentally-empty env var must not turn into "any empty string
    authenticates" -- that would be a silent, token-shaped open door."""
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "")
    assert auth.configured_token() is None


def test_configured_token_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "secret123")
    assert auth.configured_token() == "secret123"


def test_token_matches_is_true_only_for_the_exact_value():
    assert auth.token_matches("a", "a") is True
    assert auth.token_matches("a", "b") is False
    assert auth.token_matches("a", "ab") is False


def test_token_matches_refuses_a_non_string():
    assert auth.token_matches(None, "a") is False
    assert auth.token_matches(123, "a") is False


def test_requires_token_gates_api_routes_but_not_the_login_route_or_health():
    assert auth.requires_token("/api/documents") is True
    assert auth.requires_token(auth.LOGIN_PATH) is False
    assert auth.requires_token("/api/health") is False
    assert auth.requires_token("/") is False
    assert auth.requires_token("/static/main.js") is False


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


def test_extract_token_prefers_the_header_over_the_cookie():
    req = _FakeRequest(headers={auth.TOKEN_HEADER: "h"}, cookies={auth.TOKEN_COOKIE: "c"})
    assert auth.extract_token(req) == "h"


def test_extract_token_falls_back_to_the_cookie():
    req = _FakeRequest(cookies={auth.TOKEN_COOKIE: "c"})
    assert auth.extract_token(req) == "c"


def test_extract_token_is_none_with_neither():
    assert auth.extract_token(_FakeRequest()) is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: Implement** — create `app/auth.py`:

```python
"""Optional shared access token for a deployment reachable beyond one desk
(batch design §1, decision 23). Deliberately dependency-free and separate
from app/batch and app/review -- it is not part of either's product
boundary, and nothing here ever imports app.eval."""
import hmac
import os

TOKEN_COOKIE = "sindri_token"
TOKEN_HEADER = "x-sindri-token"
LOGIN_PATH = "/api/auth"
# Operational monitoring must behave identically whether or not a token is
# configured, and its body already carries only counts and type names
# (CLAUDE.md §1) -- publishing it needs no secret (decision 9).
HEALTH_PATH = "/api/health"


def configured_token():
    """The deployment's secret, or None if the feature is off. Read fresh
    on every call, never cached: tests set/unset the env var per case, and a
    cached value from import time would carry over between them. An EMPTY
    string reads as None too -- an accidentally-blank env var must not turn
    into "any empty string authenticates"."""
    t = os.environ.get("SINDRI_ACCESS_TOKEN")
    return t if t else None


def token_matches(given, expected) -> bool:
    """Constant-time: a token is a secret, and a length- or
    prefix-leaking compare would turn a string of 401s into a timing
    oracle for guessing it one byte at a time."""
    if not isinstance(given, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(given, expected)


def extract_token(request):
    """The header first (what a non-browser caller sends), the cookie
    otherwise (what the browser's own token prompt sets once)."""
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header
    return request.cookies.get(TOKEN_COOKIE)


def requires_token(path: str) -> bool:
    """Every /api/* route except the login route itself (how a token is
    ever supplied) and /api/health (decision 9). Static files (everything
    else) stay open regardless -- they are application code, not client
    data, and gating them would break the token prompt's own ability to
    load in the first place."""
    return path.startswith("/api/") and path not in (LOGIN_PATH, HEALTH_PATH)
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_auth.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + … + 10 passed, 2 skipped

```bash
git add app/auth.py tests/test_auth.py
git commit -m "$(cat <<'EOF'
auth: add the optional shared access token's pure logic

Constant-time compare, an empty env var reads as "not configured" (never
as "any empty string authenticates"), and the login route plus
/api/health are the only two gated paths left open.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Wire the token gate into `app/main.py`

**Data-integrity: yes** (the middleware is the enforcement; a bug here is an open door or a bricked server).

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_a_configured_token_gates_api_routes(monkeypatch):
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "secret123")
    assert client.get("/api/batches").status_code == 401
    r = client.get("/api/batches", headers={"x-sindri-token": "secret123"})
    assert r.status_code == 200


def test_health_and_login_stay_open_when_a_token_is_configured(monkeypatch):
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "secret123")
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth", json={"token": "wrong"}).status_code == 401


def test_logging_in_sets_a_cookie_that_then_authenticates(monkeypatch):
    monkeypatch.setenv("SINDRI_ACCESS_TOKEN", "secret123")
    r = client.post("/api/auth", json={"token": "secret123"})
    assert r.status_code == 200
    assert "sindri_token" in r.cookies
    r2 = client.get("/api/batches")   # TestClient carries cookies across calls
    assert r2.status_code == 200


def test_no_token_configured_means_open_as_today(monkeypatch):
    monkeypatch.delenv("SINDRI_ACCESS_TOKEN", raising=False)
    assert client.get("/api/batches").status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_api.py -k "token or login" -q`
Expected: FAIL — `/api/batches` answers 200 regardless of the header (no gate installed yet) and `/api/auth` 404s (route does not exist).

- [ ] **Step 3: Implement** — in `app/main.py`, add near the top-level imports:

```python
from app import auth
```

and, right after `app = FastAPI(...)` (before the exception handlers, so a 401 from the gate is not itself re-wrapped by them):

```python
@app.middleware("http")
async def _token_gate(request: Request, call_next):
    """Checked fresh per request (auth.configured_token reads the env var
    live), so a deployment can be gated or ungated without a restart in
    tests, and so a real deployment's single source of truth is the
    environment it actually runs with."""
    token = auth.configured_token()
    if token is not None and auth.requires_token(request.url.path):
        if not auth.token_matches(auth.extract_token(request), token):
            return JSONResponse({"detail": "token required"}, status_code=401)
    return await call_next(request)


class AuthRequest(BaseModel):
    token: str


@app.post("/api/auth")
def authenticate(req: AuthRequest, response: Response):
    """Set the cookie once; every later /api/* call from this browser then
    authenticates by cookie, with no header to manage client-side. No
    `secure` flag (decision 10): a deployment reachable only over plain HTTP
    on a LAN -- already a supported shape, see B2's real-browser drive --
    would otherwise never actually send the cookie, silently worse than the
    (already-accepted) risk of a token readable on that same LAN's wire."""
    token = auth.configured_token()
    if token is None or not auth.token_matches(req.token, token):
        # Never say which: that would tell an attacker whether a token is
        # even configured, and the body never echoes the guess (CLAUDE.md §1).
        raise HTTPException(status_code=401, detail="incorrect token")
    response.set_cookie(auth.TOKEN_COOKIE, req.token, httponly=True, samesite="strict")
    return {"ok": True}
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_api.py -k "token or login" -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + … + 4 passed, 2 skipped. Also run the guard, since this task touches security-relevant surface in the same repo the hook protects:

Run: `bash ~/.claude/hooks/test-sindri-guard.sh`
Expected: 32 passed, 0 failed

```bash
git add app/main.py tests/test_api.py
git commit -m "$(cat <<'EOF'
auth: wire the optional access-token gate into the app

Unset SINDRI_ACCESS_TOKEN -> open, as today. Set -> every /api/* route
except /api/auth and /api/health requires the token, by header or by
the cookie POST /api/auth sets once.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

## Milestone 4: UI, compose, docs, E2E

### Task 12: UI — token prompt on 401

**Data-integrity: no.**

**Files:**
- Create: `app/static/js/auth.js`
- Test: `tests/js/auth.test.mjs` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/js/auth.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isApiPath, needsToken, installAuthGate } from '../../app/static/js/auth.js';

test('isApiPath recognises an API url and not a static one', () => {
  assert.equal(isApiPath('/api/batches'), true);
  assert.equal(isApiPath('/static/main.js'), false);
  assert.equal(isApiPath('http://localhost/api/health'), true);
});

test('needsToken is true only for a 401', () => {
  assert.equal(needsToken({ status: 401 }), true);
  assert.equal(needsToken({ status: 200 }), false);
  assert.equal(needsToken({ status: 409 }), false);
});

test('installAuthGate retries once after a successful prompt', async () => {
  let calls = 0;
  const fakeFetch = async (url) => {
    calls += 1;
    if (calls === 1) return { status: 401, url };
    return { status: 200, url };
  };
  let prompted = 0;
  const prompt = async () => {
    prompted += 1;
    return true;   // the prompt "succeeded"
  };
  installAuthGate(prompt, fakeFetch);
  const res = await globalThis.fetch('/api/batches');
  assert.equal(res.status, 200);
  assert.equal(prompted, 1);
  assert.equal(calls, 2);
});

test('installAuthGate surfaces the original 401 if the prompt is cancelled', async () => {
  const fakeFetch = async (url) => ({ status: 401, url });
  const prompt = async () => false;   // cancelled
  installAuthGate(prompt, fakeFetch);
  const res = await globalThis.fetch('/api/batches');
  assert.equal(res.status, 401);
});

test('installAuthGate never prompts for a non-API 401 (there is none, but defensively)', async () => {
  let prompted = 0;
  const fakeFetch = async () => ({ status: 401, url: '/static/missing.js' });
  installAuthGate(async () => { prompted += 1; return true; }, fakeFetch);
  await globalThis.fetch('/static/missing.js');
  assert.equal(prompted, 0);
});

test('installAuthGate shares one in-flight prompt across concurrent 401s', async () => {
  let calls = 0;
  const fakeFetch = async (url) => {
    calls += 1;
    return calls <= 2 ? { status: 401, url } : { status: 200, url };
  };
  let prompted = 0;
  let resolvePrompt;
  const prompt = () => {
    prompted += 1;
    return new Promise((res) => { resolvePrompt = res; });
  };
  installAuthGate(prompt, fakeFetch);
  const p1 = globalThis.fetch('/api/a');
  const p2 = globalThis.fetch('/api/b');
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(prompted, 1);   // one prompt, not two, for two concurrent 401s
  resolvePrompt(true);
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/auth.test.mjs`
Expected: FAIL — cannot find module `app/static/js/auth.js`

- [ ] **Step 3: Implement** — create `app/static/js/auth.js`:

```js
// Optional shared access token (batch design §1, decision 23). A 401 from
// any /api/* call means a token is configured and this tab does not hold a
// valid one. Wrapping fetch once, here, is the single choke point every
// api.js function already goes through -- cheaper and less error-prone than
// threading a retry through each of its own dozen endpoints.

export function isApiPath(url) {
  try {
    return new URL(url, 'http://local').pathname.startsWith('/api/');
  } catch {
    return false;
  }
}

export function needsToken(res) {
  return res.status === 401;
}

export async function submitToken(token, fetchImpl = fetch) {
  const res = await fetchImpl('/api/auth', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return res.ok;
}

// promptForToken: () => Promise<boolean> -- shows the UI, resolves true once
// POST /api/auth has succeeded (the cookie is already set by then), false if
// the user gives up. realFetch: the underlying fetch to wrap (injected so
// this is testable with no DOM and no real network).
export function installAuthGate(promptForToken, realFetch) {
  const fetchImpl = realFetch || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null);
  let waiting = null;   // one prompt shared by every call that hits 401 at once
  globalThis.fetch = async (input, init) => {
    const res = await fetchImpl(input, init);
    const url = typeof input === 'string' ? input : input.url;
    if (!needsToken(res) || !isApiPath(url)) {
      return res;
    }
    if (!waiting) {
      waiting = promptForToken().finally(() => { waiting = null; });
    }
    const ok = await waiting;
    return ok ? fetchImpl(input, init) : res;   // no token given: surface the original 401
  };
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/auth.test.mjs`
Expected: PASS, 6 tests

- [ ] **Step 5: DOM wiring.** In `app/static/js/main.js`, near the application's startup code (wherever `main.js` currently does its first `api.health()` call or equivalent bootstrap), add:

```js
import { installAuthGate, submitToken } from './auth.js';

function promptForToken() {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'token-prompt-overlay';
    overlay.innerHTML = `
      <form class="token-prompt">
        <label>Access token<input type="password" name="token" autofocus></label>
        <button type="submit">Continue</button>
        <p class="token-prompt-error" hidden>Incorrect token.</p>
      </form>`;
    document.body.appendChild(overlay);
    const form = overlay.querySelector('form');
    const error = overlay.querySelector('.token-prompt-error');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const token = new FormData(form).get('token');
      const ok = await submitToken(token);
      if (ok) {
        overlay.remove();
        resolve(true);
      } else {
        error.hidden = false;
      }
    });
  });
}

installAuthGate(promptForToken);
```

Add a minimal rule to `app/static/styles/components.css` so the overlay is visible and centred (not unit-tested, matching the codebase's existing convention for CSS):

```css
.token-prompt-overlay {
  position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.5); z-index: 1000;
}
.token-prompt { background: var(--surface, #fff); padding: 1.5rem; border-radius: 6px; }
```

- [ ] **Step 6: Full suite + node count + commit**

Run: `python -m pytest -q`
Expected: baseline + … + 6 (node tests run inside `tests/test_js_state.py`), 2 skipped

Run: `node --test "tests/js/*.test.mjs"`
Expected: the same total node-test count as the number pytest just reported for `tests/test_js_state.py`

```bash
git add app/static/js/auth.js app/static/js/main.js app/static/styles/components.css tests/js/auth.test.mjs
git commit -m "$(cat <<'EOF'
ui: prompt for the access token on a 401, then retry once

One fetch wrapper, not a retry threaded through every api.js call;
concurrent 401s share a single prompt instead of stacking.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: UI — real mass export in the work list

**Data-integrity: no** (client-side only; the server-side guarantees are Tasks 1-9).

This task wires B3's work-list bulk "Export selected" action to the real endpoint. **B3 is concurrent with this plan — before writing code, read `docs/plans/2026-10-08-batch-b3-ui-plan.state.md`** (written once B3 ships) and confirm the actual names of: the work-list module (planned as `app/static/js/worklist.js`), its bulk-export handler (B3's own decision 4 describes it as a client-side loop calling the existing per-document export routes — find that function), and `api.js`'s existing export helpers. Adjust the snippets below to match whatever B3 actually shipped; the behaviour they must produce does not change.

**Files:**
- Create: `app/static/js/massexport.js`
- Modify: `app/static/js/api.js`, `app/static/js/worklist.js` (as B3 ships it)
- Test: `tests/js/massexport.test.mjs` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/js/massexport.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { exportStatusText, pollIntervalMs, downloadUrl } from '../../app/static/js/massexport.js';

test('exportStatusText describes each job state', () => {
  assert.equal(exportStatusText({ state: 'running', done: 2, total: 5 }), 'Exporting 2 / 5…');
  assert.equal(exportStatusText({ state: 'done', done: 5, total: 5 }), 'Export ready');
  assert.equal(exportStatusText({ state: 'failed' }), 'Export failed');
});

test('pollIntervalMs matches the work list\'s own poll cadence', () => {
  assert.equal(pollIntervalMs(), 3000);
});

test('downloadUrl points at the job\'s download route', () => {
  assert.equal(downloadUrl('abc123'), '/api/exports/abc123/download');
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/massexport.test.mjs`
Expected: FAIL — cannot find module `app/static/js/massexport.js`

- [ ] **Step 3: Implement** — create `app/static/js/massexport.js`:

```js
// Mass export (batch design §4): pure status text and URLs, kept separate
// from the DOM wiring below so they are node-testable with no fetch mock at
// all and reusable from the work list's own bulk-action button.

export function exportStatusText(job) {
  if (job.state === 'failed') return 'Export failed';
  if (job.state === 'done') return 'Export ready';
  return `Exporting ${job.done} / ${job.total}…`;
}

// Same cadence as the work list's own 3 s poll (design §3) -- one rhythm
// for the whole screen, not a second timer ticking at its own rate.
export function pollIntervalMs() {
  return 3000;
}

export function downloadUrl(exportId) {
  return `/api/exports/${exportId}/download`;
}
```

Add to `app/static/js/api.js`:

```js
export async function requestExport(documentIds) {
  const res = await fetch('/api/exports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_ids: documentIds }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Could not start the export' }));
    throw new Error(err.detail || 'Could not start the export');
  }
  return res.json();
}

export async function getExport(exportId) {
  const res = await fetch(`/api/exports/${exportId}`);
  if (!res.ok) throw new Error('Could not check the export');
  return res.json();
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/massexport.test.mjs`
Expected: PASS, 3 tests

- [ ] **Step 5: Replace B3's interim bulk-export handler.** In `app/static/js/worklist.js`, find the function B3's own plan describes (decision 4: "a client-side loop over the existing per-document export routes, skipping any selected drawing that is not `finished` with a toast naming how many were skipped"). Replace its body with:

```js
import { requestExport, getExport } from './api.js';
import { exportStatusText, pollIntervalMs, downloadUrl } from './massexport.js';

async function exportSelected(selectedIds) {
  if (!selectedIds.length) return;
  const job = await requestExport(selectedIds);
  const statusEl = showExportProgress(exportStatusText(job));   // reuses the work list's existing toast/banner element
  const poll = async () => {
    const current = await getExport(job.id);
    statusEl.textContent = exportStatusText(current);
    if (current.state === 'running') {
      setTimeout(poll, pollIntervalMs());
      return;
    }
    if (current.state === 'done') {
      const a = document.createElement('a');
      a.href = downloadUrl(job.id);
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  };
  poll();
}
```

(`showExportProgress` is assumed to be an existing small helper in `worklist.js`'s own toast/status plumbing from B3; if B3 shipped no such helper, add a two-line one that sets `textContent` on a status element already in `index.html`'s work-list header — do not introduce a new UI surface for this, reuse whatever B3 built for "Extracting · N queued" so the screen has one place progress text appears, not two.)

- [ ] **Step 6: Full suite + node count + commit**

Run: `python -m pytest -q`
Expected: baseline + … + 3 passed, 2 skipped

Run: `node --test "tests/js/*.test.mjs"`
Expected: matches pytest's `tests/test_js_state.py` count

```bash
git add app/static/js/massexport.js app/static/js/api.js app/static/js/worklist.js tests/js/massexport.test.mjs
git commit -m "$(cat <<'EOF'
ui: wire the work list's bulk export to the real mass-export job

Replaces B3's interim client-side per-document loop with POST
/api/exports, a 3 s poll matching the work list's own cadence, and a
download once the ZIP is ready.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Compose, Dockerfile, CLAUDE.md

**Data-integrity: no.**

**Files:**
- Modify: `docker-compose.yml`, `CLAUDE.md`

- [ ] **Step 1: There is no failing test for a compose file or docs**, so this task's "test" is: read the result back and confirm by inspection that `docker-compose.yml` still parses (`docker compose config` if Docker is available in this environment; otherwise a plain YAML parse) and that the full suite still passes (it does not touch any Python or JS this plan's other tasks test).

- [ ] **Step 2: Edit `docker-compose.yml`** — add the two new environment variables, off by default, beside the existing ones:

```yaml
services:
  sindri:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - OCR_BACKEND=tesseract
      # Drawings, the queue index and review records
      # (docs/plans/2026-10-07-batch-async-review-design.md). Client data:
      # never inside the image, never in /tmp. Run a single uvicorn worker.
      - SINDRI_DATA_DIR=/data
      - SINDRI_REVIEW_DIR=/data/reviews
      - SINDRI_REVIEW_CONSENT=0
      # Days a finished, exported drawing's files are kept before retention
      # deletes them; 0 turns retention off (batch B4 design §4).
      - SINDRI_RETENTION_DAYS=30
      # Uncomment and set to require this token on every /api/* route
      # (batch B4 design §1, decision 23). Unset = open, as today.
      # - SINDRI_ACCESS_TOKEN=
```

- [ ] **Step 3: Add the B4 paragraph to `CLAUDE.md`**, in §2, immediately after the existing "BATCH B2: IMPORT" paragraph:

```markdown
**BATCH B4: EXPORT & OPS** (`docs/plans/2026-10-08-batch-b4-export-ops-plan.md`).
A selection, a batch, or a filtered set of finished drawings exports as one
ZIP — `POST /api/exports {document_ids}` on its own thread, never behind the
GPU queue, polled at `GET /api/exports/{id}` and downloaded from
`.../download` — laid out `<batch>/<rel folder>/<stem>.xlsx` +
`<stem>_ballooned.pdf` (Windows-safe names, `app/batch/names.py`; collisions
suffixed `" (2)"`) plus a `manifest.csv` that accounts for every drawing
asked for, including the skipped ones (`not_finished` / `failed` / `purged`
/ `export_error: <type>`, or `reopened — later changes not included` for a
reopened drawing's last sealed revision). The single-document export route
and the mass export now share ONE render path
(`app/batch/export_render.py`, moved from `app/main.py`'s original B1 code
unchanged), so the two are byte-identical by construction. Export ZIPs are
deleted after 24h (rebuildable from the same selection). **Retention**: a
background thread (`app/batch/housekeeping.py`), started only once the
extraction worker holds its single-process lock, purges every finished,
exported drawing older than `SINDRI_RETENTION_DAYS` (default 30, `0` = off)
— its row becomes a `purged` tombstone (410 on open/export, never reopened,
never re-exported); review records are never touched by this. **An optional
shared access token** (`SINDRI_ACCESS_TOKEN`, unset = open) gates every
`/api/*` route except `/api/auth` (how a token is supplied) and
`/api/health`; `POST /api/auth` sets an `HttpOnly`/`SameSite=Strict` cookie
with no `Secure` flag, because a plain-HTTP LAN deployment is already a
supported shape and a `Secure` cookie would silently never be sent on it.
This closes the batch/async-review project (B1-B4); HITL phase 2 (forcing,
grading, dev validation, return bundle) is next.
```

- [ ] **Step 4: Full suite (confirms nothing else broke) + commit**

Run: `python -m pytest -q`
Expected: unchanged from Task 13's count, 2 skipped

```bash
git add docker-compose.yml CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: describe B4 (mass export, retention, token) in CLAUDE.md and compose

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Real-browser drive, guard, close-out

**Data-integrity: no** (verification only), but this is the acceptance gate for every data-integrity task above — do not skip it.

**Files:** none modified; this task runs the suite, the guard, and a throwaway end-to-end drive, and writes the milestone checkpoint is handled by the controller, not this task.

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: every count from Tasks 1-14, cumulative; confirm it matches your own running total before calling this done.

- [ ] **Step 2: Guard**

Run: `bash ~/.claude/hooks/test-sindri-guard.sh`
Expected: 32 passed, 0 failed

- [ ] **Step 3: Node count cross-check**

Run: `node --test "tests/js/*.test.mjs"`
Expected: same total as `tests/test_js_state.py`'s count inside the pytest run above.

- [ ] **Step 4: Throwaway real-browser drive** — scratchpad only, stub backend, following B1/B2/B3's own pattern (node `playwright-core` + a cached chromium, a stub extraction backend with a short artificial delay so the drive does not wait on a real VLM). Design §5's own E2E tail is the script: **import a ZIP → extract → review one drawing halfway → reload → resume → finish → skip one → mass export → check the manifest.** Concretely, drive:
  1. Start the app with `SINDRI_DATA_DIR`/`SINDRI_REVIEW_DIR` pointed at a scratch directory and the stub backend monkeypatched in (reuse whatever fixture/launcher B1-B3's own throwaway drives used — do not invent a second one).
  2. Import a ZIP of 2-3 sample PDFs (built in code, never a bare `.pdf` on the command line) → stage → Start.
  3. Let both extract (stub delay); open the first, accept a few rows, Finish it.
  4. Select both drawings in the work list → Export selected.
  5. Poll until the export job reports `done`; download the ZIP.
  6. Open the ZIP and assert: it contains one `.xlsx` and one `_ballooned.pdf` per finished drawing, a `manifest.csv` whose `skip_reason` column is empty for the finished one(s), and — if the import was 3 PDFs and only 2 were finished — `not_finished` for the third.
  7. (If a token is set for this drive) confirm a request with no cookie 401s and the token prompt flow succeeds.

Record the result (pass/fail per step) the way B1/B2's state files did; a failure here means a task above has a bug the unit tests did not catch, not that this task itself is wrong — fix forward in the task that owns the broken behaviour, with a regression test, before closing this one out.

- [ ] **Step 5: Final commit** — this task makes no code changes beyond what the drive's fixes already commit task-by-task. If the drive is clean, nothing to commit here; the controller checkpoints `docs/plans/2026-10-08-batch-b4-export-ops-plan.state.md`, not this task.

---

## After B4

The batch/async-review project (design `docs/plans/2026-10-07-batch-async-review-design.md`) is complete: import, background extraction, resumable review, single and mass export, retention, and an optional access token. What is left is **HITL phase 2** (forcing UX + audit sampling — `docs/plans/2026-10-07-hitl-review-grading-design.md`'s own phases 2-5: forcing, grading, dev validation, return bundle), which lands on the navigation model this project built and gets its own plan, written just in time, per the design's decision 24.
