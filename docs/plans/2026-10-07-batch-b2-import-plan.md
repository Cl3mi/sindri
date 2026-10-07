# Batch B2 — Import (chunked upload, ZIP, staging) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow `context-management:milestone-checkpoints`: the CONTROLLER checkpoints `docs/plans/2026-10-07-batch-b2-import-plan.state.md` at each marker. Implementers never edit that file.

**Goal:** Drawings arrive through a chunked, resumable upload protocol into a draft batch; a completed upload becomes staged documents (one per PDF, a ZIP unpacked server-side, path-safe) carrying pages / oversized / unreadable / duplicate facts and an Include default; "Start batch" queues the included rows and drops the rest. The interim B1 UI uploads its single file through the same protocol as a draft batch of one.

**Architecture:** Migration 2 adds the `upload` table and the staging columns of `document`. `app/batch/repo.py` gains the index side (draft batches, upload rows, staging rows, Include, Start, discard) with every transition guarded in one transaction; `app/batch/uploads.py` (new) owns the file side (part files fsynced per chunk, completion with full SHA-256, hard-linked or unpacked into `docs/<id>/input.pdf`, a free-disk guard, startup recovery); `app/batch/unzip.py` (new) classifies ZIP entries; `app/batch/pdf_facts.py` (new) measures a PDF through `app.pipeline.render.effective_dpi`, the same function the harness's probe asks. `app/static/js/upload.js` is a pure chunked-upload client (injected `fetch`), node-tested against an in-memory server with failures injected.

**Tech Stack:** Python 3 / FastAPI / sqlite3 / zipfile / PyMuPDF / pytest; vanilla ES modules + `node --test`.

**Design:** `docs/plans/2026-10-07-batch-async-review-design.md` — §0 (binding decisions; B2 touches 1, 10, 17, 19, 21, 22, 26, 27, 29), §1 (data model), §2 (upload, staging), §5 (errors, tests). B1 as built: `docs/plans/2026-10-07-batch-b1-core-plan.state.md` — this plan builds on the code B1 actually produced, not on the B1 plan text.

**Rules that bind every task** (from CLAUDE.md — read §1 and §4 there):
* TDD strictly: failing test → watch it fail → minimal code → pass → commit. One task, one commit (a review fix may be a second commit).
* Never read client data; never put the protected path in a command or a subagent prompt; single, unpiped, unchained shell commands (no `|`, `&&`, `;`, `>`).
* **The Bash guard denies any command string containing a bare `.pdf`.** Never type a file name with that extension in a command, a `-k` filter or a commit message. Test PDFs are built in code (`tests/batch/pdfs.py`) or reached through the `sample_pdf` fixture, never named on the command line.
* Never use bare `git stash` (the stash stack is shared across worktrees). `git add` and `git commit` are separate calls.
* Server logs carry ids (first 8 chars), states, counts and exception TYPES only — never exception messages, file names, ZIP entry paths or values. Error replies are fixed sentences.
* `app/batch/` and `app/review/` must never import `app.eval` (`tests/review/test_product_boundary.py` globs every module in both).
* No upload size limit (decision 29). The free-disk reserve is a guard, not a limit.
* Tests use temp dirs only; `tests/conftest.py` already isolates `SINDRI_DATA_DIR` / `SINDRI_REVIEW_DIR` per test.
* Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` (or the implementing model's accurate name).
* Full suite before every commit: `python -m pytest -q`. Baseline before B2: **1483 passed, 2 skipped** (node tests run inside pytest as one test, `tests/test_js_state.py`).

**Data-integrity tasks** (the controller uses Opus for implementation AND review): Tasks 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12. Tasks 2 and 13 may use a smaller model.

**Revised after an independent review** (accepted by the controller): recovery runs only under the single-worker lock (C1); the chunk PUT is bounded (I1); a pure-JS SHA-256 fallback for the fingerprint (I2); a time-based retry budget and draft memory so a re-selected file resumes (I3); a Start whose reply was lost is recognised (I4); the interim-UI change and the route removal are separate tasks (I5); plus the minor fixes (markers around Start/Discard, resume bumps `updated_at` and matches `last_modified`, empty-draft cleanup, unreadable replies, fixed-sentence 422s, UTC fallback name, rowid ordering for duplicates).

**Decisions made while planning** (the design is silent or B1's code forced a choice; each is justified where it lands):
1. **Completion is an explicit, idempotent `POST /api/uploads/{id}/complete`**, not implied by the last PUT: a lost completion reply is retried and answered with the stored result.
2. **Resume key = (batch, fingerprint, name, folder, size, and `last_modified` when sent)** among unfinished uploads, not the fingerprint alone: two identical files in two folders of one selection, uploading in parallel, would otherwise share one upload and one of them would silently vanish. Resuming after a closed tab therefore means re-selecting the files into the same draft: B3's staging view continues a draft, and the interim UI remembers the draft of an interrupted file in `localStorage` (Task 11). A resume bumps `updated_at`, so the 7-day sweep never removes an upload that is being resumed.
3. **`duplicate_of` is computed live** (the earliest other document with the same SHA-256 by insertion order, i.e. rowid — the same order the staging-time check sees; wall-clock `created_at` can step backwards), but the Include default is decided once, at staging. If the original is removed, the badge goes; the user's Include choice stays theirs.
4. **Unreadable PDFs are staged rows** (`unreadable = 1`, never includable, dropped by Start) instead of B1's 400: design §2 lists "unreadable PDF" as a staging badge, so the user sees what was refused and why.
5. **A completed PDF is hard-linked** from its part file into `docs/<id>/input.pdf` (copy fallback): no second copy of a large file, and the part survives intact until the index rows commit, so a failed completion is simply retried.
6. **Crash safety by marker, not by inference:** every `docs/<id>/` whose row is being created (a completion) or removed (Start's dropped rows, Discard) carries `.importing` across the index transaction. Startup deletes only dirs that carry the marker AND have no row, and strips the marker from dirs that do — a lost or replaced index can never make the sweep delete real drawings. **The sweep runs only inside `Worker.start()`, right after the single-process lock is taken**: a second process that fails to get the lock never sweeps, because the first process's in-flight imports look exactly like crash leftovers.
7. **`POST /api/upload` and `POST /api/documents/{id}/enqueue` are REMOVED** (Task 12, after Task 11 has moved the interim UI off them). Kept, they would be a second door into the queue that bypasses Start's Include rules (an unreadable or unconfirmed out-of-scope file queued one by one), and B1's upload created non-draft batches that have no staging semantics at all. `Repo.create_document` and `Repo.enqueue` stay as internal primitives the worker and lifecycle tests build fixtures with; `enqueue` refuses a draft batch's drawing from Task 4 on.
8. **Migration 2 turns B1's never-started staged drawings into drafts**, so after `/enqueue` goes they can still be started; their Include follows the B2 default, and `oversized` stays NULL (never measured — 0 would claim it was).
9. **Start is refused while any upload of the batch is unfinished**, and with nothing included; both leave everything unchanged.
10. **Stale cleanup** (unfinished uploads untouched for 7 days, stray part files, empty drafts older than 7 days) runs at startup and on every `POST /api/batches`; B4's daily retention sweep can call the same method.
11. **A chunk PUT is bounded at `MAX_CHUNK` (64 MiB)**: a missing or larger `Content-Length` is 413 before anything is read, and the body is read with the same bound. This bounds one REQUEST's memory, not a file — files of any size still arrive in as many chunks as they need (decision 29 holds). The client sends 8 MiB.
12. **The import routes answer a malformed request with a fixed sentence (422)**: FastAPI's default 422 body echoes the offending input, which here is a client file or folder name.

---

## File structure

| File | Responsibility |
|---|---|
| `app/batch/db.py` | + migration 2: `upload` table, `document.upload_id/size/unreadable/included`, indexes, B1 staged rows → drafts |
| `app/batch/paths.py` | + `uploads/` dir, `part_path()`, `safe_rel_parts()` |
| `app/batch/pdf_facts.py` (new) | `pdf_facts()` → readable / pages / oversized; `EXTRACT_DPI` |
| `app/batch/staging.py` (new) | pure staging rules + API row shapes: `out_of_scope`, `default_included`, `staging_row`, `staging_counts`, `upload_view`, `batch_view`, `clean_batch_name` |
| `app/batch/repo.py` | + batches, upload rows, staging rows, Include, Start, discard, recovery queries; `UnknownBatch`, `UnknownUpload`, `UploadConflict` |
| `app/batch/uploads.py` (new) | `Uploads`: create / put / complete / delete, disk guard, recovery and stale cleanup; `DiskFull`, `StorageFault` |
| `app/batch/unzip.py` (new) | `classify()` ZIP entries → `PdfEntry` / `Ignored` / None |
| `app/batch/worker.py` | + a `startup` hook run right after the single-process lock is taken (recovery) |
| `app/batch/runtime.py` | + `Uploads`, `SINDRI_MIN_FREE_MB`, recovery handed to the worker's `startup` hook |
| `app/main.py` | `/api/batches*`, `/api/uploads*` (bounded PUT), `/include`, `/start`; `extra` in the error handler; fixed-sentence 422 on import routes; disk in `/api/health`; `/api/upload` + `/enqueue` removed |
| `app/static/js/upload.js` (new) | chunked resumable upload client with a time-based retry budget, pure-JS SHA-256 fallback, `importFiles` (3 lanes), `batchNameFor`, `relPathOf`, `fingerprint`, draft memory for resuming |
| `app/static/js/badges.js` (new) | staging badge texts from a staging row |
| `app/static/js/api.js`, `main.js`, `index.html`, `styles/components.css` | batch endpoints; interim UI = batch of one through the protocol |
| `tests/batch/pdfs.py` (new) | PDF bytes built in code for tests |
| `tests/batch/test_pdf_facts.py`, `test_repo_staging.py`, `test_uploads.py`, `test_unzip.py` (new) | unit tests |
| `tests/test_api_import.py` (new) | import API tests |
| `tests/batch/test_runtime.py` | + recovery only under the worker lock |
| `tests/js/upload.test.mjs`, `tests/js/badges.test.mjs` (new) | node tests |
| `tests/conftest.py` | + `SINDRI_MIN_FREE_MB=0` per test |

---

### Task 1: Migration 2, upload paths, path safety

**Data-integrity: yes** (schema migration of a deployed index).

**Files:**
- Modify: `app/batch/db.py`, `app/batch/paths.py`
- Test: `tests/batch/test_db.py`

- [ ] **Step 1: Write the failing tests** — in `tests/batch/test_db.py`, change the import `from app.batch.paths import DataDir` to `from app.batch.paths import DataDir, safe_rel_parts`, then append:

```python
def test_migration_2_adds_uploads_and_the_staging_columns(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "upload" in tables
    doc_cols = {r[1] for r in conn.execute("PRAGMA table_info(document)")}
    assert {"upload_id", "size", "unreadable", "included"} <= doc_cols
    up_cols = {r[1] for r in conn.execute("PRAGMA table_info(upload)")}
    assert {"id", "batch_id", "name", "rel_path", "size", "last_modified", "fingerprint",
            "kind", "received", "state", "ignored", "created_at", "updated_at"} <= up_cols


def test_migration_2_turns_a_b1_staged_drawing_into_a_draft_import(tmp_path, monkeypatch):
    """B1 staged an upload as a NON-draft batch of one and queued it with
    /enqueue. From B2 on a drawing is queued only by starting a draft, so a
    drawing staged under B1 and never started must become a draft -- or
    nothing could ever start it. Its Include follows the B2 default (on for
    one page, off for multi-page); `oversized` stays NULL because B1 never
    measured it, and 0 would claim it had. `unreadable` is truthfully 0: B1
    refused every PDF it could not parse."""
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:1])
    db.migrate(conn)
    t = "2026-10-07T00:00:00.000000+00:00"
    for bid, did, state, pages in [("b1", "d1", "staged", 1), ("b2", "d2", "staged", 3),
                                   ("b3", "d3", "queued", 1)]:
        conn.execute("INSERT INTO batch (id, name, draft, created_at) VALUES (?, 'x', 0, ?)", (bid, t))
        conn.execute("INSERT INTO document (id, batch_id, file_name, pages, state, created_at,"
                     " updated_at) VALUES (?, ?, 'x', ?, ?, ?, ?)", (did, bid, pages, state, t, t))
    # NOT monkeypatch.undo(): that would also undo conftest's per-test dirs.
    monkeypatch.setattr(db, "MIGRATIONS", full)
    assert db.migrate(conn) == len(full)
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT d.id, d.included, d.unreadable, d.oversized, b.draft"
        " FROM document d JOIN batch b ON b.id = d.batch_id")}
    assert (rows["d1"]["draft"], rows["d1"]["included"]) == (1, 1)
    assert (rows["d2"]["draft"], rows["d2"]["included"]) == (1, 0)
    assert (rows["d3"]["draft"], rows["d3"]["included"]) == (0, None)
    assert {r["unreadable"] for r in rows.values()} == {0}
    assert {r["oversized"] for r in rows.values()} == {None}


def test_part_path_refuses_anything_but_a_hex_id(tmp_path):
    data = DataDir(tmp_path)
    assert data.part_path("a" * 32) == tmp_path / "uploads" / ("a" * 32 + ".part")
    for bad in ["../x", "A" * 32, "", None]:
        with pytest.raises(ValueError):
            data.part_path(bad)


@pytest.mark.parametrize("name,parts", [
    ("", []),
    ("a.pdf", ["a.pdf"]),
    ("Teile/Welle/w1.pdf", ["Teile", "Welle", "w1.pdf"]),
    ("Teile\\Welle\\w1.pdf", ["Teile", "Welle", "w1.pdf"]),
    ("./a//b/", ["a", "b"]),
])
def test_safe_rel_parts_splits_a_relative_path(name, parts):
    assert safe_rel_parts(name) == parts


@pytest.mark.parametrize("name", [
    "/etc/x.pdf", "\\x.pdf", "C:\\x.pdf", "c:x.pdf", "a/../../x.pdf", "..",
    "a\\..\\x.pdf", "a\x00b.pdf", "a\nb.pdf", None,
])
def test_safe_rel_parts_refuses_a_path_that_could_escape(name):
    """Absolute, drive-relative, climbing, or carrying control characters
    (a NUL truncates a name in C code; a newline forges a manifest line)."""
    assert safe_rel_parts(name) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: FAIL — `ImportError: cannot import name 'safe_rel_parts'` (collection error).

- [ ] **Step 3: Implement**

`app/batch/db.py` — append a second entry to `MIGRATIONS` (after the first string, inside the list; never edit migration 1):

```python
    # 2: chunked uploads and staging (batch B2, design §1-§2)
    """
    CREATE TABLE upload (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES batch(id),
        name TEXT NOT NULL,
        rel_path TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL,
        last_modified INTEGER,
        fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL,
        received INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        ignored TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX upload_resume ON upload(batch_id, fingerprint);
    ALTER TABLE document ADD COLUMN upload_id TEXT;
    ALTER TABLE document ADD COLUMN size INTEGER;
    ALTER TABLE document ADD COLUMN unreadable INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE document ADD COLUMN included INTEGER;
    CREATE INDEX document_sha256 ON document(sha256);
    CREATE INDEX document_batch ON document(batch_id);
    UPDATE batch SET draft = 1
        WHERE id IN (SELECT batch_id FROM document WHERE state = 'staged');
    UPDATE document SET included = CASE WHEN pages = 1 THEN 1 ELSE 0 END
        WHERE state = 'staged';
    """,
```

and extend the module docstring's last sentence with: "Migration 2 also moves B1's never-started staged drawings into draft batches, because from B2 on only Start queues a staged drawing."

`app/batch/paths.py` — replace the module with:

```python
"""Where a deployment keeps its drawings (batch design §1).

    <root>/sindri.db             the index (db.py)
    <root>/docs/<id>/            input.pdf, page.png, result.json, exports/
    <root>/uploads/<id>.part     an upload still arriving (uploads.py)

Everything under it is CLIENT DATA under CLAUDE.md §1."""
import re
from pathlib import Path
from typing import List, Optional

# Document ids are uuid4().hex -- the same ids the review store keys its
# sessions by. Anything else is refused before a path is built from it, so
# an id can never walk out of docs/. Upload and batch ids use the same shape.
DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_rel_parts(name) -> Optional[List[str]]:
    """The components of a client-supplied relative path -- a ZIP entry
    name, or a folder upload's webkitRelativePath -- or None if it is unsafe:
    absolute, drive-relative, climbing with `..`, or carrying control
    characters. Backslashes count as separators (Windows ZIP tools write
    them); empty and `.` components are dropped.

    Nothing is ever WRITTEN to a path built from these names -- an imported
    file always lands in docs/<new id>/input.pdf -- so this is not what keeps
    a hostile ZIP inside the data dir. It exists because the folder is shown
    to the user and becomes a folder inside the mass-export ZIP (design §4)."""
    if not isinstance(name, str):
        return None
    s = name.replace("\\", "/")
    if s.startswith("/") or _DRIVE.match(s) or _CONTROL.search(s):
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return parts


class DataDir:
    def __init__(self, root):
        self.root = Path(root)
        self.docs = self.root / "docs"
        self.docs.mkdir(parents=True, exist_ok=True)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.root / "sindri.db"

    def doc_dir(self, doc_id) -> Path:
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            raise ValueError("malformed document id")
        return self.docs / doc_id

    def part_path(self, upload_id) -> Path:
        if not isinstance(upload_id, str) or not DOC_ID_RE.match(upload_id):
            raise ValueError("malformed upload id")
        return self.uploads / f"{upload_id}.part"
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1501 passed, 2 skipped** (+18).
```bash
git add app/batch/db.py app/batch/paths.py tests/batch/test_db.py
```
```bash
git commit -m "batch: migration 2 for uploads and staging; upload paths and path safety" -m "The upload table holds chunk progress; document gains the staging facts (size, unreadable, included, upload_id). B1's never-started staged drawings become drafts, because from B2 on only Start queues a staged drawing. safe_rel_parts refuses absolute, climbing and control-character paths before they are shown or exported." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: What staging knows about a PDF

**Data-integrity: no** (read-only measurement; a smaller model is fine).

**Files:**
- Create: `app/batch/pdf_facts.py`, `tests/batch/pdfs.py`, `tests/batch/test_pdf_facts.py`
- Modify: `app/main.py` (`_extract` reads `EXTRACT_DPI`)

- [ ] **Step 1: Write the failing tests**

`tests/batch/pdfs.py`:
```python
"""PDF bytes built in code, so no test needs a fixture file named on the
command line (the Bash guard refuses a bare PDF extension there) and no test
can ever pick up a client drawing. MuPDF writes a fresh document id on every
save, so two calls never return the same bytes: a test that needs two
IDENTICAL files reuses one call's result."""
import fitz


def pdf_bytes(pages: int = 1, width: float = 595, height: float = 842) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(pages):
            doc.new_page(width=width, height=height)
        return doc.tobytes()
    finally:
        doc.close()


def encrypted_pdf_bytes() -> bytes:
    """Needs a user password to open -- extraction could never read it."""
    doc = fitz.open()
    try:
        doc.new_page()
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                           owner_pw="owner", user_pw="user")
    finally:
        doc.close()
```

`tests/batch/test_pdf_facts.py`:
```python
"""Staging's facts about a PDF (batch design §2, decision 26): page count,
whether sheet 1 would be render-clamped, and whether it can be read at all."""
import pytest

from app.batch.pdf_facts import EXTRACT_DPI, PdfFacts, pdf_facts
from app.pipeline.render import effective_dpi
from tests.batch.pdfs import encrypted_pdf_bytes, pdf_bytes


def write(tmp_path, data):
    p = tmp_path / "input.pdf"
    p.write_bytes(data)
    return p


def test_a_one_page_sheet_is_in_scope(tmp_path):
    assert pdf_facts(write(tmp_path, pdf_bytes())) == PdfFacts(readable=True, pages=1, oversized=False)


def test_pages_are_counted(tmp_path):
    assert pdf_facts(write(tmp_path, pdf_bytes(pages=3))).pages == 3


def test_a_sheet_the_renderer_would_clamp_is_oversized(tmp_path):
    facts = pdf_facts(write(tmp_path, pdf_bytes(width=7000, height=5000)))
    assert facts.readable and facts.oversized


@pytest.mark.parametrize("w,h", [(595, 842), (3370, 2384), (7000, 5000)])   # A4, A0, 2.5 x 1.7 m
def test_oversized_is_the_probe_rule_asked_of_the_renderer(tmp_path, w, h):
    """The harness calls a sheet render-clamped when
    effective_dpi(w, h, 300) < 300 - 1.0 (app/eval/balloons.py). Staging must
    answer the same for the same sheet, or the badge and the scope policy's
    published numbers would disagree about one drawing."""
    expected = effective_dpi(w, h, 300) < 299.0
    assert pdf_facts(write(tmp_path, pdf_bytes(width=w, height=h))).oversized == expected


@pytest.mark.parametrize("data", [b"", b"not a pdf at all", encrypted_pdf_bytes()],
                         ids=["empty", "garbage", "password"])
def test_an_unreadable_file_is_unreadable_never_an_error(tmp_path, data):
    assert pdf_facts(write(tmp_path, data)) == PdfFacts(readable=False)


def test_extraction_runs_at_the_dpi_oversized_is_measured_at(monkeypatch, tmp_path):
    """`oversized` means "read below the resolution extraction asks for";
    if the two dpi values ever drifted apart the badge would lie."""
    import app.main as main
    seen = {}

    def fake_extract(pdf_path, work_dir, dpi, backend, progress):
        seen["dpi"] = dpi
    monkeypatch.setattr(main, "extract", fake_extract)
    main._extract(tmp_path / "x", tmp_path, lambda *a, **k: None)
    assert seen["dpi"] == EXTRACT_DPI == 300
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_pdf_facts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.pdf_facts'`.

- [ ] **Step 3: Implement**

`app/batch/pdf_facts.py`:
```python
"""What staging needs to know about a PDF before anyone decides to extract
it (batch design §2, decision 26): how many pages it has, whether the
renderer would clamp sheet 1 below the extraction dpi, and whether it can be
read at all.

`oversized` is the SAME question the harness's probe asks
(app/eval/balloons.py: effective_dpi(...) < dpi - 1.0), asked of the same
public function, app.pipeline.render.effective_dpi -- so the product's badge
and the published scope numbers cannot disagree about a drawing. app/batch
must not import app.eval, and the arithmetic is not copied: it is asked of
the renderer, which owns it."""
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF

from app.pipeline.render import effective_dpi

# The dpi the worker extracts at -- app.main._extract reads it from here, so
# "oversized" cannot drift from what extraction actually asks for.
EXTRACT_DPI = 300
# The probe's tolerance: a fraction of a dpi short is not a clamp.
_CLAMP_TOLERANCE_DPI = 1.0


@dataclass(frozen=True)
class PdfFacts:
    readable: bool
    pages: Optional[int] = None
    oversized: Optional[bool] = None


def pdf_facts(path) -> PdfFacts:
    """Never raises for a bad file: anything MuPDF cannot open, a document
    that needs a user password, or one with no pages is `readable=False` --
    extraction would fail on it the same way. (A PDF encrypted with an EMPTY
    user password opens without one, extracts fine, and is readable.)"""
    try:
        with fitz.open(path) as doc:
            if doc.needs_pass or doc.page_count < 1:
                return PdfFacts(readable=False)
            rect = doc[0].rect          # render_page reads sheet 1's rect too
            dpi = effective_dpi(rect.width, rect.height, EXTRACT_DPI)
            return PdfFacts(readable=True, pages=doc.page_count,
                            oversized=dpi < EXTRACT_DPI - _CLAMP_TOLERANCE_DPI)
    except Exception:
        # Not logged here: the caller logs ids and counts, never this file.
        return PdfFacts(readable=False)
```

`app/main.py` — add `from app.batch.pdf_facts import EXTRACT_DPI` beside the other `app.batch` imports, and change `_extract` to:
```python
def _extract(pdf_path, work_dir, progress):
    # Late-bound on purpose: tests monkeypatch `extract` and `_BACKEND` on
    # this module, and the worker must see those patches. EXTRACT_DPI is the
    # dpi staging's `oversized` badge is measured at (app/batch/pdf_facts.py).
    return extract(pdf_path, work_dir=work_dir, dpi=EXTRACT_DPI, backend=_BACKEND,
                   progress=progress)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch/test_pdf_facts.py tests/review/test_product_boundary.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1511 passed, 2 skipped** (+10).
```bash
git add app/batch/pdf_facts.py app/main.py tests/batch/pdfs.py tests/batch/test_pdf_facts.py
```
```bash
git commit -m "batch: measure pages, oversized and readability for staging" -m "Oversized is the harness probe's rule asked of render.effective_dpi, at the dpi extraction uses (one constant, read by app.main), so the staging badge and the published scope numbers cannot disagree. An unopenable or password-protected file is unreadable, never an exception." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: The index side of an import — draft batches, upload rows, staged documents

**Data-integrity: yes** (upload offsets, completion and staging are single guarded transactions).

**Files:**
- Create: `app/batch/staging.py`, `tests/batch/test_repo_staging.py`
- Modify: `app/batch/repo.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_repo_staging.py`:
```python
"""The index side of an import (batch design §1-§2): draft batches, upload
rows with their offsets, and the staged documents a completed upload
becomes. Every step is one guarded transaction, so a retried or racing
request is told where things stand instead of overwriting them."""
import itertools
import sqlite3
import uuid

import pytest

from app.batch import db
from app.batch.repo import InvalidTransition, Repo, UnknownBatch, UnknownUpload, UploadConflict


@pytest.fixture
def repo(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tick = itertools.count()
    return Repo(conn, clock=lambda: f"2026-10-07T00:00:{next(tick):09d}")


def doc(n, **kw):
    d = {"id": f"{n:032x}", "file_name": f"d{n}.pdf", "rel_path": "", "size": 10,
         "sha256": f"{n:064x}", "pages": 1, "oversized": False, "unreadable": False}
    d.update(kw)
    return d


def upload(repo, batch_id, name="a.pdf", rel_path="", size=10, fingerprint=None):
    row, _ = repo.create_upload(batch_id=batch_id, name=name, rel_path=rel_path, size=size,
                                last_modified=None, fingerprint=fingerprint or "f" * 64,
                                kind="pdf")
    return row


def stage(repo, batch_id, *docs, ignored=()):
    """An upload driven through the protocol's index steps, staging `docs`."""
    up = upload(repo, batch_id, fingerprint=uuid.uuid4().hex * 2)
    repo.upload_received(up["id"], 0, up["size"])
    repo.begin_completion(up["id"])
    return repo.finish_completion(up["id"], list(docs), list(ignored))


def test_a_new_batch_is_a_draft(repo):
    b = repo.create_batch("Lieferung 12")
    assert (b["name"], b["draft"]) == ("Lieferung 12", True)
    assert repo.get_batch(b["id"]) == b
    with pytest.raises(UnknownBatch):
        repo.get_batch("0" * 32)


def test_re_selecting_the_same_file_resumes_its_unfinished_upload(repo):
    b = repo.create_batch("b")["id"]
    first = upload(repo, b)
    repo.upload_received(first["id"], 0, 4)
    again, resumed = repo.create_upload(batch_id=b, name="a.pdf", rel_path="", size=10,
                                        last_modified=None, fingerprint="f" * 64, kind="pdf")
    assert (again["id"], again["received"], resumed) == (first["id"], 4, True)


def test_two_identical_files_in_two_folders_never_share_an_upload(repo):
    """Uploaded in parallel, a shared upload would interleave two clients'
    chunks and one of the two files would silently never arrive."""
    b = repo.create_batch("b")["id"]
    assert upload(repo, b, rel_path="x")["id"] != upload(repo, b, rel_path="y")["id"]


def test_a_file_changed_since_is_a_new_upload_when_the_browser_says_so(repo):
    """Same name, size and fingerprint but a different modification time:
    the bytes in between may differ, so it must not continue the old
    upload. A browser that sends no time matches on the rest."""
    b = repo.create_batch("b")["id"]
    kw = dict(batch_id=b, name="a.pdf", rel_path="", size=10, fingerprint="f" * 64, kind="pdf")
    first, _ = repo.create_upload(last_modified=1, **kw)
    changed, resumed = repo.create_upload(last_modified=2, **kw)
    assert changed["id"] != first["id"] and resumed is False
    unknown, resumed = repo.create_upload(last_modified=None, **kw)
    assert (unknown["id"], resumed) == (first["id"], True)


def test_a_resume_counts_as_activity(repo):
    """Or the 7-day sweep could remove an upload while it is being resumed."""
    b = repo.create_batch("b")["id"]
    first = upload(repo, b)
    again, _ = repo.create_upload(batch_id=b, name="a.pdf", rel_path="", size=10,
                                  last_modified=None, fingerprint="f" * 64, kind="pdf")
    assert again["updated_at"] > first["updated_at"]


def test_a_finished_upload_is_never_resumed(repo):
    b = repo.create_batch("b")["id"]
    up = upload(repo, b)
    repo.upload_received(up["id"], 0, 10)
    repo.begin_completion(up["id"])
    repo.finish_completion(up["id"], [doc(1)], [])
    assert upload(repo, b)["id"] != up["id"]


def test_a_started_batch_takes_no_uploads(repo):
    b = repo.create_batch("b")["id"]
    repo._conn.execute("UPDATE batch SET draft = 0 WHERE id = ?", (b,))
    with pytest.raises(InvalidTransition):
        upload(repo, b)


def test_the_offset_moves_only_from_where_it_stands(repo):
    """Two writers racing the same chunk: the second finds the offset
    already moved and is told the real one (409 + received), never
    overwrites it."""
    up = upload(repo, repo.create_batch("b")["id"])
    assert repo.upload_received(up["id"], 0, 4) == 4
    with pytest.raises(UploadConflict) as e:
        repo.upload_received(up["id"], 0, 4)
    assert e.value.status == 409
    assert e.value.extra == {"received": 4, "state": "receiving"}
    with pytest.raises(UnknownUpload):
        repo.upload_received("0" * 32, 0, 4)


def test_completion_needs_every_byte(repo):
    up = upload(repo, repo.create_batch("b")["id"])
    repo.upload_received(up["id"], 0, 4)
    with pytest.raises(UploadConflict) as e:
        repo.begin_completion(up["id"])
    assert e.value.extra == {"received": 4, "state": "receiving"}


def test_completion_stages_the_documents_and_finishes_the_upload_together(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), doc(2, rel_path="Teile"), ignored=[{"name": "x.txt", "reason": "not_pdf"}])
    assert [r["id"] for r in rows] == [doc(1)["id"], doc(2)["id"]]
    assert {r["state"] for r in rows} == {"staged"} and rows[1]["rel_path"] == "Teile"
    up = repo.get_upload(rows[0]["upload_id"])
    assert up["state"] == "complete"


def test_a_failed_staging_leaves_neither_rows_nor_a_finished_upload(repo):
    """Rows and the upload's `complete` commit together: a fault in the
    middle (here a duplicate id) leaves no half-imported batch, and the
    upload can go back to receiving and be completed again."""
    b = repo.create_batch("b")["id"]
    up = upload(repo, b)
    repo.upload_received(up["id"], 0, 10)
    repo.begin_completion(up["id"])
    with pytest.raises(sqlite3.IntegrityError):
        repo.finish_completion(up["id"], [doc(1), doc(1)], [])
    assert repo._conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 0
    assert repo.get_upload(up["id"])["state"] == "completing"
    repo.abort_completion(up["id"])
    assert repo.get_upload(up["id"])["state"] == "receiving"


@pytest.mark.parametrize("kw,included", [
    ({}, True),
    ({"pages": 3}, False),
    ({"oversized": True}, False),
    ({"unreadable": True, "pages": None, "oversized": None}, False),
    ({"oversized": None}, True),      # never measured is not out of scope
])
def test_include_starts_on_only_for_a_normal_file(repo, kw, included):
    (row,) = stage(repo, repo.create_batch("b")["id"], doc(1, **kw))
    assert row["included"] is included


def test_a_later_copy_is_a_duplicate_excluded_by_default_and_the_original_is_not(repo):
    a = repo.create_batch("Januar")["id"]
    b = repo.create_batch("Februar")["id"]
    (orig,) = stage(repo, a, doc(1, sha256="c" * 64))
    (copy,) = stage(repo, b, doc(2, sha256="c" * 64))
    assert (orig["duplicate_of"], orig["included"]) == (None, True)
    assert copy["included"] is False
    assert copy["duplicate_of"] == {"id": orig["id"], "file_name": "d1.pdf", "batch_id": a,
                                    "batch_name": "Januar", "state": "staged"}


def test_two_identical_files_in_one_upload(repo):
    first, second = stage(repo, repo.create_batch("b")["id"],
                          doc(1, sha256="c" * 64), doc(2, sha256="c" * 64))
    assert first["duplicate_of"] is None and second["duplicate_of"]["id"] == first["id"]


def test_duplicate_of_follows_the_original_but_the_choice_stays(repo):
    """Live: once the original is removed the badge goes. The Include box
    does not flip back on by itself -- that was decided at staging, and
    from then on it is the user's."""
    b = repo.create_batch("b")["id"]
    orig, copy = stage(repo, b, doc(1, sha256="c" * 64), doc(2, sha256="c" * 64))
    repo.delete(orig["id"])
    (row,) = repo.staging(b)["documents"]
    assert (row["id"], row["duplicate_of"], row["included"]) == (copy["id"], None, False)


def test_staging_counts_reconcile(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1), doc(2, pages=2), doc(3, unreadable=True, pages=None, oversized=None),
          doc(4, sha256=f"{1:064x}"))
    upload(repo, b, name="late.pdf")
    view = repo.staging(b)
    c = view["counts"]
    assert c == {"documents": 4, "included": 1, "excluded": 3, "unreadable": 1,
                 "duplicates": 1, "out_of_scope": 1, "uploading": 1}
    assert c["included"] + c["excluded"] == c["documents"] == len(view["documents"])
    assert [u["state"] for u in view["uploads"]] == ["complete", "receiving"]


def test_a_repeated_completion_reports_what_it_staged(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), ignored=[{"name": "x.txt", "reason": "not_pdf"}])
    done = repo.completed_upload(rows[0]["upload_id"])
    assert [r["id"] for r in done["documents"]] == [rows[0]["id"]]
    assert done["upload"]["ignored"] == [{"name": "x.txt", "reason": "not_pdf"}]


def test_delete_upload_removes_only_an_unfinished_one(repo):
    b = repo.create_batch("b")["id"]
    up = upload(repo, b)
    assert repo.delete_upload(up["id"])["id"] == up["id"]
    assert repo.delete_upload(up["id"]) is None
    rows = stage(repo, b, doc(1))
    with pytest.raises(InvalidTransition):
        repo.delete_upload(rows[0]["upload_id"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_repo_staging.py -q`
Expected: FAIL — `ImportError: cannot import name 'UnknownBatch' from 'app.batch.repo'`.

- [ ] **Step 3: Implement**

`app/batch/staging.py`:
```python
"""Staging rules (batch design §2, decisions 19 and 26) and the shapes the
staging API returns.

One module, so the Include default the repo STORES and the flags the API
SHOWS come from the same functions: a row can never be excluded by default
for a reason the staging view does not display."""
import json
import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_BATCH_NAME = 200


def out_of_scope(pages, oversized) -> bool:
    """Multi-page (only sheet 1 is read) or oversized (sheet 1 is read at a
    reduced resolution) -- decision 26. Unknown (None) is NOT out of scope:
    that is a row staged before B2, whose sheet was never measured."""
    return bool((pages or 0) > 1 or oversized)


def default_included(*, unreadable, pages, oversized, duplicate) -> bool:
    """On for a normal file. Off for an out-of-scope file and for a
    duplicate, which the user may still include; off for an unreadable PDF,
    which can never be included."""
    return not (unreadable or duplicate or out_of_scope(pages, oversized))


def clean_batch_name(name):
    """The name stripped, or None if it cannot be a batch name: empty,
    longer than MAX_BATCH_NAME, or carrying control characters (it becomes a
    folder name in the mass-export ZIP, design §4)."""
    if not isinstance(name, str):
        return None
    s = name.strip()
    if not s or len(s) > MAX_BATCH_NAME or _CONTROL.search(s):
        return None
    return s


def batch_view(b) -> dict:
    return {"id": b["id"], "name": b["name"], "draft": bool(b["draft"]),
            "created_at": b["created_at"]}


def upload_view(u) -> dict:
    return {"id": u["id"], "name": u["name"], "rel_path": u["rel_path"],
            "size": u["size"], "received": u["received"], "state": u["state"],
            "kind": u["kind"], "ignored": json.loads(u["ignored"]) if u["ignored"] else []}


def staging_row(r) -> dict:
    """A document row joined with its duplicate's facts (Repo._STAGING_SELECT)."""
    dup = None
    if r["dup_id"] is not None:
        dup = {"id": r["dup_id"], "file_name": r["dup_file_name"],
               "batch_id": r["dup_batch_id"], "batch_name": r["dup_batch_name"],
               "state": r["dup_state"]}
    return {
        "id": r["id"], "batch_id": r["batch_id"], "upload_id": r["upload_id"],
        "file_name": r["file_name"], "rel_path": r["rel_path"], "size": r["size"],
        "pages": r["pages"],
        "oversized": None if r["oversized"] is None else bool(r["oversized"]),
        "unreadable": bool(r["unreadable"]),
        "out_of_scope": out_of_scope(r["pages"], r["oversized"]),
        "duplicate_of": dup,
        "included": bool(r["included"]),
        "allowed_out_of_scope": bool(r["allowed_out_of_scope"]),
        "state": r["state"],
    }


def staging_counts(rows, uploads) -> dict:
    """Cross-checkable by construction: included + excluded == documents ==
    len(rows), and every other count is a subset of rows (or of uploads)."""
    included = sum(r["included"] for r in rows)
    return {
        "documents": len(rows),
        "included": included,
        "excluded": len(rows) - included,
        "unreadable": sum(r["unreadable"] for r in rows),
        "duplicates": sum(r["duplicate_of"] is not None for r in rows),
        "out_of_scope": sum(r["out_of_scope"] for r in rows),
        "uploading": sum(u["state"] != "complete" for u in uploads),
    }
```

`app/batch/repo.py`:

1. Extend the module docstring with a paragraph:
```
Imports (B2): a draft batch receives uploads; a completed upload's documents
are staged in the SAME transaction that marks the upload complete, so a crash
leaves all of them or none. Upload offsets move by a guarded UPDATE on the
old offset, exactly like state transitions.
```
2. Add `import json` to the imports, and
```python
from app.batch.staging import batch_view, default_included, staging_counts, staging_row, upload_view
```
3. After `class InvalidTransition`, add:
```python
class UnknownBatch(BatchError):
    status = 404


class UnknownUpload(BatchError):
    status = 404


class UploadConflict(InvalidTransition):
    """A 409 that says where the upload really stands. `extra` (numbers and a
    state name only) rides in the reply body -- app.main's handler merges it
    -- so the client resyncs from the server's offset instead of guessing
    (batch design §2)."""

    def __init__(self, message, *, received, state):
        super().__init__(message)
        self.extra = {"received": received, "state": state}


# A document with the facts of its duplicate: the EARLIEST other document
# with the same bytes, in any batch and any state (decision 19). Computed on
# every read, so the badge follows the original if it is removed. "Earliest"
# is insertion order (rowid), the order _stage's check saw when it set the
# Include default -- not created_at, a wall clock that can step backwards.
# (SQLite hands a new row max(rowid) + 1, so rowid order among existing rows
# is insertion order even after deletes.)
_STAGING_SELECT = """
    SELECT d.*, o.id AS dup_id, o.file_name AS dup_file_name, o.state AS dup_state,
           o.batch_id AS dup_batch_id, ob.name AS dup_batch_name
    FROM document d
    LEFT JOIN document o ON o.id = (
        SELECT x.id FROM document x
        WHERE x.sha256 = d.sha256 AND x.rowid < d.rowid
        ORDER BY x.rowid LIMIT 1)
    LEFT JOIN batch ob ON ob.id = o.batch_id
"""
```
4. Inside `class Repo`, after `_row`, add the helpers:
```python
    @staticmethod
    def _batch(c, batch_id) -> dict:
        row = c.execute("SELECT * FROM batch WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            raise UnknownBatch(f"no batch {str(batch_id)[:8]}")
        return dict(row)

    @classmethod
    def _draft(cls, c, batch_id) -> dict:
        b = cls._batch(c, batch_id)
        if not b["draft"]:
            raise InvalidTransition("this import has already started; add files to a new one")
        return b

    @staticmethod
    def _upload(c, upload_id) -> dict:
        row = c.execute("SELECT * FROM upload WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise UnknownUpload(f"no upload {str(upload_id)[:8]}")
        return dict(row)

    @staticmethod
    def _staging_rows(c, where: str, params) -> list:
        return [staging_row(dict(r)) for r in c.execute(
            f"{_STAGING_SELECT} WHERE {where} ORDER BY d.rowid", tuple(params))]

    def _stage(self, c, batch_id, docs, upload_id) -> list:
        """Insert staged rows inside the caller's transaction."""
        self._draft(c, batch_id)
        ids = []
        for d in docs:
            # Asked BEFORE this row's insert, so of two identical files the
            # first is the original and only the second starts excluded.
            duplicate = d["sha256"] is not None and c.execute(
                "SELECT 1 FROM document WHERE sha256 = ? LIMIT 1", (d["sha256"],)).fetchone() is not None
            included = default_included(unreadable=d["unreadable"], pages=d["pages"],
                                        oversized=d["oversized"], duplicate=duplicate)
            now = self._clock()
            c.execute(
                "INSERT INTO document (id, batch_id, upload_id, file_name, rel_path, size,"
                " sha256, pages, oversized, unreadable, included, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?)",
                (d["id"], batch_id, upload_id, d["file_name"], d["rel_path"], d["size"],
                 d["sha256"], d["pages"],
                 None if d["oversized"] is None else int(bool(d["oversized"])),
                 int(bool(d["unreadable"])), int(included), now, now))
            ids.append(d["id"])
        return ids
```
5. Under `# ----- reads`, add:
```python
    def get_batch(self, batch_id) -> dict:
        with self._lock:
            return batch_view(self._batch(self._conn, batch_id))

    def get_upload(self, upload_id) -> dict:
        with self._lock:
            return self._upload(self._conn, upload_id)

    def staging(self, batch_id) -> dict:
        """Everything the staging view shows for one batch."""
        with self._lock:
            b = self._batch(self._conn, batch_id)
            rows = self._staging_rows(self._conn, "d.batch_id = ?", (batch_id,))
            uploads = [upload_view(dict(u)) for u in self._conn.execute(
                "SELECT * FROM upload WHERE batch_id = ? ORDER BY created_at, rowid", (batch_id,))]
        return {"batch": batch_view(b), "documents": rows, "uploads": uploads,
                "counts": staging_counts(rows, uploads)}

    def completed_upload(self, upload_id) -> dict:
        """What a repeated /complete answers: the documents it staged (those
        the user has not removed since) and what it ignored."""
        with self._lock:
            u = self._upload(self._conn, upload_id)
            rows = self._staging_rows(self._conn, "d.upload_id = ?", (upload_id,))
        return {"upload": upload_view(u), "documents": rows}
```
6. Under `# ----- writes`, add:
```python
    def create_batch(self, name: str) -> dict:
        """A draft: staged files wait here until Start (decision 10)."""
        now = self._clock()
        with self._tx() as c:
            batch_id = uuid.uuid4().hex
            c.execute("INSERT INTO batch (id, name, draft, created_at) VALUES (?, ?, 1, ?)",
                      (batch_id, name, now))
            return batch_view(self._batch(c, batch_id))

    def create_upload(self, *, batch_id, name, rel_path, size, last_modified, fingerprint, kind):
        """(row, resumed). An unfinished upload of the same file -- same
        fingerprint, name, folder and size (and modification time, when the
        browser sends one), in the same batch -- is returned instead of a new
        one: re-selecting the files after a closed tab resumes it (decision
        21). The folder is part of the key, so two identical files in two
        folders never share an upload. A resume counts as activity: it
        bumps updated_at, or the 7-day sweep could remove an upload in the
        middle of being resumed."""
        now = self._clock()
        with self._tx() as c:
            self._draft(c, batch_id)
            row = c.execute(
                "SELECT * FROM upload WHERE batch_id = ? AND fingerprint = ? AND name = ?"
                " AND rel_path = ? AND size = ? AND state != 'complete'"
                " AND (? IS NULL OR last_modified = ?)"
                " ORDER BY rowid LIMIT 1",
                (batch_id, fingerprint, name, rel_path, size,
                 last_modified, last_modified)).fetchone()
            if row is not None:
                c.execute("UPDATE upload SET updated_at = ? WHERE id = ?", (now, row["id"]))
                return self._upload(c, row["id"]), True
            upload_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO upload (id, batch_id, name, rel_path, size, last_modified,"
                " fingerprint, kind, received, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'receiving', ?, ?)",
                (upload_id, batch_id, name, rel_path, size, last_modified, fingerprint,
                 kind, now, now))
            return self._upload(c, upload_id), False

    def upload_received(self, upload_id, offset: int, received: int) -> int:
        """Move the offset once the bytes are on disk. Guarded on the OLD
        offset: of two writers racing the same chunk only one moves it, the
        other gets UploadConflict with the real offset."""
        with self._tx() as c:
            cur = c.execute(
                "UPDATE upload SET received = ?, updated_at = ?"
                " WHERE id = ? AND state = 'receiving' AND received = ?",
                (received, self._clock(), upload_id, offset))
            if cur.rowcount == 0:
                u = self._upload(c, upload_id)
                raise UploadConflict("the upload has moved on", received=u["received"],
                                     state=u["state"])
        return received

    def reset_received(self, upload_id, received: int) -> None:
        """The part file holds less than the index counted (lost or cut
        short outside this process): only bytes on disk count."""
        with self._tx() as c:
            c.execute("UPDATE upload SET received = ?, updated_at = ?"
                      " WHERE id = ? AND state = 'receiving'",
                      (received, self._clock(), upload_id))

    def begin_completion(self, upload_id) -> None:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE upload SET state = 'completing', updated_at = ?"
                " WHERE id = ? AND state = 'receiving' AND received = size",
                (self._clock(), upload_id))
            if cur.rowcount == 0:
                u = self._upload(c, upload_id)
                raise UploadConflict("the upload is not complete", received=u["received"],
                                     state=u["state"])

    def finish_completion(self, upload_id, docs, ignored) -> list:
        """Stage the upload's documents and mark it complete in ONE
        transaction: a crash leaves all rows or none. (The files of "none"
        carry an .importing marker and are swept at startup, uploads.py.)"""
        with self._tx() as c:
            u = self._upload(c, upload_id)
            if u["state"] != "completing":
                raise UploadConflict("the upload is not being completed",
                                     received=u["received"], state=u["state"])
            ids = self._stage(c, u["batch_id"], docs, upload_id)
            c.execute("UPDATE upload SET state = 'complete', ignored = ?, updated_at = ?"
                      " WHERE id = ?", (json.dumps(ignored), self._clock(), upload_id))
            if not ids:
                return []
            return self._staging_rows(c, f"d.id IN ({', '.join('?' * len(ids))})", ids)

    def abort_completion(self, upload_id) -> None:
        """A completion that failed before its rows committed: the upload is
        whole again and /complete may be retried."""
        with self._tx() as c:
            c.execute("UPDATE upload SET state = 'receiving', updated_at = ?"
                      " WHERE id = ? AND state = 'completing'", (self._clock(), upload_id))

    def delete_upload(self, upload_id):
        """An unfinished upload removed by the user or gone stale; returns
        its row, or None if unknown (idempotent). A complete upload is
        refused: its drawings are removed one by one."""
        with self._tx() as c:
            row = c.execute("SELECT * FROM upload WHERE id = ?", (upload_id,)).fetchone()
            if row is None:
                return None
            if row["state"] == "complete":
                raise InvalidTransition("this upload is already imported; remove its drawings instead")
            c.execute("DELETE FROM upload WHERE id = ?", (upload_id,))
            return dict(row)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch/test_repo_staging.py tests/batch/test_repo.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1533 passed, 2 skipped** (+22).
```bash
git add app/batch/staging.py app/batch/repo.py tests/batch/test_repo_staging.py
```
```bash
git commit -m "batch: draft batches, upload rows and staged documents in the index" -m "Upload offsets move by an UPDATE guarded on the old offset, so a racing or retried chunk is told the real offset. A completion stages its documents and marks the upload complete in one transaction. Duplicates are found by full-file hash across all batches and shown live; the Include default (off for out-of-scope, duplicates and unreadable files) comes from the same staging rules the view reports." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Staging decisions — Include, Start, discard

**Data-integrity: yes** (the only door from staged to queued).

**Files:**
- Modify: `app/batch/repo.py`
- Test: `tests/batch/test_repo_staging.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_repo_staging.py`:

```python
def test_a_batch_can_be_renamed_and_listed(repo):
    b = repo.create_batch("Import 2026-10-07 14:30")["id"]
    assert repo.rename_batch(b, "Lieferung 12")["name"] == "Lieferung 12"
    stage(repo, b, doc(1))
    upload(repo, b, name="late.pdf")
    (listed,) = repo.list_batches()
    assert (listed["id"], listed["documents"], listed["uploading"], listed["draft"]) == (b, 1, 1, True)


def test_including_an_out_of_scope_file_is_the_stored_permission(repo):
    (row,) = stage(repo, repo.create_batch("b")["id"], doc(1, pages=4))
    assert (row["included"], row["allowed_out_of_scope"]) == (False, False)
    row = repo.set_included(row["id"], True)
    assert (row["included"], row["allowed_out_of_scope"]) == (True, True)
    row = repo.set_included(row["id"], False)
    assert (row["included"], row["allowed_out_of_scope"]) == (False, False)


def test_an_unreadable_pdf_can_never_be_included(repo):
    (row,) = stage(repo, repo.create_batch("b")["id"],
                   doc(1, unreadable=True, pages=None, oversized=None))
    with pytest.raises(InvalidTransition, match="unreadable"):
        repo.set_included(row["id"], True)
    assert repo.staging(row["batch_id"])["documents"][0]["included"] is False


def test_include_is_refused_once_the_import_has_started(repo):
    b = repo.create_batch("b")["id"]
    (row,) = stage(repo, b, doc(1))
    repo.start_batch(b)
    with pytest.raises(InvalidTransition):
        repo.set_included(row["id"], False)


def test_start_queues_the_included_and_drops_the_rest(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), doc(2, pages=3), doc(3), doc(4, pages=2),
                 doc(5, unreadable=True, pages=None, oversized=None))
    repo.set_included(rows[3]["id"], True)              # an out-of-scope file, explicitly
    out = repo.start_batch(b)
    ids = [r["id"] for r in rows]
    assert out == {"queued": [ids[0], ids[2], ids[3]], "dropped": [ids[1], ids[4]]}
    assert len(out["queued"]) + len(out["dropped"]) == len(rows)
    assert repo.get_batch(b)["draft"] is False
    view = repo.staging(b)
    assert [r["state"] for r in view["documents"]] == ["queued"] * 3
    assert view["uploads"] == []
    # Every queued out-of-scope drawing carries the permission (decision 26).
    assert all(r["allowed_out_of_scope"] for r in view["documents"] if r["out_of_scope"])
    # The worker takes them in staging order.
    assert [repo.claim_next()["id"] for _ in range(3)] == out["queued"]


def test_start_is_refused_while_a_file_is_still_uploading(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1))
    upload(repo, b, name="late.pdf")
    with pytest.raises(InvalidTransition, match="uploading"):
        repo.start_batch(b)
    assert repo.get_batch(b)["draft"] is True
    assert repo.staging(b)["documents"][0]["state"] == "staged"


def test_start_is_refused_with_nothing_included(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1, pages=2))
    with pytest.raises(InvalidTransition, match="nothing"):
        repo.start_batch(b)
    assert repo.staging(b)["counts"]["documents"] == 1


def test_a_batch_starts_once(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1))
    repo.start_batch(b)
    with pytest.raises(InvalidTransition):
        repo.start_batch(b)
    with pytest.raises(UnknownBatch):
        repo.start_batch("0" * 32)


def test_discarding_a_draft_removes_its_rows(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1))
    late = upload(repo, b, name="late.pdf")
    out = repo.delete_batch(b)
    assert out == {"documents": [rows[0]["id"]], "uploads": [rows[0]["upload_id"], late["id"]]}
    assert repo.delete_batch(b) is None
    with pytest.raises(UnknownBatch):
        repo.get_batch(b)


def test_a_started_batch_cannot_be_discarded(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1))
    repo.start_batch(b)
    with pytest.raises(InvalidTransition):
        repo.delete_batch(b)


def test_removing_the_last_staged_drawing_keeps_the_draft(repo):
    """A draft may still receive files and is continued later (design §2);
    it goes only when discarded, or when it is empty and a week old."""
    b = repo.create_batch("b")["id"]
    (row,) = stage(repo, b, doc(1))
    assert repo.delete(row["id"]) is True
    assert repo.get_batch(b)["draft"] is True


def test_a_drawing_in_a_draft_is_never_queued_one_by_one(repo):
    """Start is where the Include rules live. A per-drawing enqueue would let
    an unreadable or an unconfirmed out-of-scope file past them."""
    (row,) = stage(repo, repo.create_batch("b")["id"], doc(1))
    with pytest.raises(InvalidTransition, match="start"):
        repo.enqueue(row["id"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_repo_staging.py -q`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'rename_batch'` (and the others).

- [ ] **Step 3: Implement** in `app/batch/repo.py`:

1. Add `out_of_scope` to the `app.batch.staging` import.
2. Under `# ----- reads`, add:
```python
    def list_batches(self) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT b.*,"
                " (SELECT COUNT(*) FROM document d WHERE d.batch_id = b.id) AS documents,"
                " (SELECT COUNT(*) FROM upload u WHERE u.batch_id = b.id"
                "  AND u.state != 'complete') AS uploading"
                " FROM batch b ORDER BY b.created_at, b.rowid").fetchall()
        return [{**batch_view(r), "documents": r["documents"], "uploading": r["uploading"]}
                for r in rows]
```
3. Under `# ----- writes`, add:
```python
    def rename_batch(self, batch_id, name: str) -> dict:
        with self._tx() as c:
            self._batch(c, batch_id)
            c.execute("UPDATE batch SET name = ? WHERE id = ?", (name, batch_id))
            return batch_view(self._batch(c, batch_id))

    def set_included(self, doc_id, included: bool) -> dict:
        with self._tx() as c:
            r = c.execute(
                "SELECT d.state, d.unreadable, d.pages, d.oversized, b.draft"
                " FROM document d JOIN batch b ON b.id = d.batch_id WHERE d.id = ?",
                (doc_id,)).fetchone()
            if r is None:
                raise UnknownDocument(f"no document {str(doc_id)[:8]}")
            if r["state"] != "staged" or not r["draft"]:
                raise InvalidTransition(
                    "only a drawing in an import that has not started can be included or excluded")
            if included and r["unreadable"]:
                raise InvalidTransition("an unreadable PDF can never be included")
            # Including an out-of-scope file IS the explicit permission
            # (decision 26); it is stored so the work list and the manifest
            # can say so. Excluding takes it back.
            allowed = bool(included) and out_of_scope(r["pages"], r["oversized"])
            c.execute("UPDATE document SET included = ?, allowed_out_of_scope = ?, updated_at = ?"
                      " WHERE id = ?", (int(bool(included)), int(allowed), self._clock(), doc_id))
            return self._staging_rows(c, "d.id = ?", (doc_id,))[0]

    def start_batch(self, batch_id) -> dict:
        """Queue the included rows, drop the excluded ones, end the draft --
        one transaction (design §2 "Start batch"). Refused, changing nothing,
        while a file is still uploading or when nothing is included. Returns
        the ids queued and dropped; the caller removes the dropped files."""
        now = self._clock()
        with self._tx() as c:
            if not self._batch(c, batch_id)["draft"]:
                raise InvalidTransition("this import has already started")
            if c.execute("SELECT 1 FROM upload WHERE batch_id = ? AND state != 'complete' LIMIT 1",
                         (batch_id,)).fetchone():
                raise InvalidTransition("files are still uploading; wait for them or remove them")
            rows = c.execute("SELECT id, included, unreadable FROM document WHERE batch_id = ?"
                             " ORDER BY rowid", (batch_id,)).fetchall()
            go = [r["id"] for r in rows if r["included"] and not r["unreadable"]]
            if not go:
                raise InvalidTransition("nothing is included")
            keep = set(go)
            drop = [r["id"] for r in rows if r["id"] not in keep]
            for doc_id in go:
                # One queued_at for the whole import; claim_next breaks the
                # tie by rowid, which is staging order.
                self._move(c, doc_id, ("staged",), "queued", queued_at=now)
            c.executemany("DELETE FROM document WHERE id = ?", [(i,) for i in drop])
            c.execute("DELETE FROM upload WHERE batch_id = ?", (batch_id,))
            c.execute("UPDATE batch SET draft = 0 WHERE id = ?", (batch_id,))
        return {"queued": go, "dropped": drop}

    def delete_batch(self, batch_id):
        """Discard an import that has not started: its staged rows, its
        uploads and the batch. Returns the ids whose files the caller must
        remove; None if unknown (idempotent)."""
        with self._tx() as c:
            row = c.execute("SELECT draft FROM batch WHERE id = ?", (batch_id,)).fetchone()
            if row is None:
                return None
            if not row["draft"]:
                raise InvalidTransition("only an import that has not started can be discarded")
            docs = [r["id"] for r in c.execute(
                "SELECT id FROM document WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()]
            ups = [r["id"] for r in c.execute(
                "SELECT id FROM upload WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()]
            c.execute("DELETE FROM document WHERE batch_id = ?", (batch_id,))
            c.execute("DELETE FROM upload WHERE batch_id = ?", (batch_id,))
            c.execute("DELETE FROM batch WHERE id = ?", (batch_id,))
        return {"documents": docs, "uploads": ups}
```
4. Replace `enqueue` with:
```python
    def enqueue(self, doc_id) -> None:
        """Queue one staged drawing of a batch that is NOT a draft (the
        worker and lifecycle tests build such fixtures with create_document).
        A draft's drawings are queued only by start_batch, where the Include
        rules live -- one by one, an unreadable or an unconfirmed
        out-of-scope file could be queued past them."""
        with self._tx() as c:
            r = c.execute("SELECT b.draft FROM document d JOIN batch b ON b.id = d.batch_id"
                          " WHERE d.id = ?", (doc_id,)).fetchone()
            if r is not None and r["draft"]:
                raise InvalidTransition("start the import to queue its drawings")
            self._move(c, doc_id, ("staged",), "queued", queued_at=self._clock())
```
5. In `delete`, replace the batch DELETE with:
```python
            # A started batch goes with its last drawing. A draft stays: it
            # may still receive files and is continued later (design §2);
            # it goes when discarded, or when empty and a week old (uploads.py).
            c.execute("DELETE FROM batch WHERE id = ? AND draft = 0 AND NOT EXISTS "
                      "(SELECT 1 FROM document WHERE batch_id = ?)",
                      (row["batch_id"], row["batch_id"]))
```
and add to its docstring: "Removing the last staged drawing of a draft keeps the draft."

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch -q`
Expected: all pass (B1's worker and lifecycle tests use non-draft batches, so the enqueue guard does not touch them).

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1545 passed, 2 skipped** (+12).
```bash
git add app/batch/repo.py tests/batch/test_repo_staging.py
```
```bash
git commit -m "batch: include, start and discard an import" -m "Start queues the included rows in staging order and drops the rest in one transaction, refused while files are still uploading or nothing is included. Including an out-of-scope file stores the permission; an unreadable PDF can never be included. A draft's drawings can no longer be queued one by one, which would bypass those rules, and removing a draft's last drawing keeps the draft." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 5: The file side of the upload protocol

**Data-integrity: yes** (offset protocol, fsync ordering, completion and hashing).

**Files:**
- Create: `app/batch/uploads.py`, `tests/batch/test_uploads.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_uploads.py`:
```python
"""The file side of the upload protocol (batch design §2): part files
written at the server's offset and fsynced before the index moves, a
completion that verifies, hashes and stages, and a free-disk reserve."""
import errno
import hashlib
import logging
import os
import sqlite3

import pytest

from app.batch import db
from app.batch import uploads as uploads_mod
from app.batch.paths import DataDir
from app.batch.repo import (BatchError, InvalidTransition, Repo, UnknownBatch,
                            UnknownUpload, UploadConflict)
from app.batch.uploads import IMPORTING_MARKER, DiskFull, Uploads
from tests.batch.pdfs import pdf_bytes

CHUNK = 100


class H:
    def __init__(self, tmp_path):
        self.data = DataDir(tmp_path / "data")
        conn = db.connect(self.data.db_path)
        db.migrate(conn)
        self.repo = Repo(conn)
        self.free = 10 ** 12
        self.uploads = Uploads(self.repo, self.data, min_free_bytes=1000,
                               free_bytes=lambda path: self.free)
        self.batch = self.repo.create_batch("b")["id"]

    def create(self, data, name="a.pdf", rel_path="", batch=None):
        return self.uploads.create(batch_id=batch or self.batch, name=name, size=len(data),
                                   last_modified=None, rel_path=rel_path,
                                   fingerprint=hashlib.sha256(data).hexdigest())

    def send(self, data, **kw):
        up = self.create(data, **kw)
        for off in range(up["received"], len(data), CHUNK):
            self.uploads.put(up["upload_id"], off, data[off:off + CHUNK])
        return up["upload_id"]

    def imported(self, data, **kw):
        return self.uploads.complete(self.send(data, **kw))

    def doc_dirs(self):
        return sorted(p.name for p in self.data.docs.iterdir())


@pytest.fixture
def h(tmp_path):
    return H(tmp_path)


def test_create_starts_at_zero_with_an_empty_part_file(h):
    up = h.create(b"x" * 10)
    assert (up["received"], up["state"], up["resumed"]) == (0, "receiving", False)
    assert h.data.part_path(up["upload_id"]).read_bytes() == b""


def test_re_selecting_the_file_resumes_at_the_server_offset(h):
    data = pdf_bytes()
    up = h.create(data)
    h.uploads.put(up["upload_id"], 0, data[:CHUNK])
    again = h.create(data)
    assert (again["upload_id"], again["received"], again["resumed"]) == (up["upload_id"], CHUNK, True)


@pytest.mark.parametrize("kw", [
    {"name": "a.exe"}, {"name": "a/b.pdf"}, {"name": "a\x01.pdf"}, {"name": "  "},
    {"fingerprint": "xyz"}, {"size": -1}, {"rel_path": "../out"},
], ids=["extension", "separator", "control", "blank", "fingerprint", "size", "rel_path"])
def test_create_refuses_what_cannot_be_imported(h, kw):
    args = dict(batch_id=h.batch, name="a.pdf", size=10, last_modified=None, rel_path="",
                fingerprint="f" * 64)
    args.update(kw)
    with pytest.raises(BatchError) as e:
        h.uploads.create(**args)
    # A fixed sentence: never the client's file name echoed back.
    assert e.value.status == 400 and "a.exe" not in str(e.value)


def test_unknown_and_started_batches_are_refused(h):
    with pytest.raises(UnknownBatch):
        h.create(b"x", batch="0" * 32)
    h.repo._conn.execute("UPDATE batch SET draft = 0")
    with pytest.raises(InvalidTransition):
        h.create(b"x")


def test_a_chunk_at_the_wrong_offset_is_409_with_the_true_offset(h):
    data = pdf_bytes()
    up = h.create(data)
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(up["upload_id"], CHUNK, data[CHUNK:2 * CHUNK])
    assert e.value.extra == {"received": 0, "state": "receiving"}


def test_a_retried_chunk_after_a_lost_reply_is_not_written_twice(h):
    """The reply to a PUT is lost and the client sends the same chunk again.
    The server already holds it: 409 with the advanced offset, and the file
    holds the chunk once."""
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    assert h.uploads.put(uid, 0, data[:CHUNK]) == {"received": CHUNK}
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(uid, 0, data[:CHUNK])
    assert e.value.extra["received"] == CHUNK
    assert h.data.part_path(uid).read_bytes() == data[:CHUNK]


def test_a_chunk_is_fsynced_before_the_index_moves(h, monkeypatch):
    """A restart loses at most the chunk in flight (decision 21). Were the
    index moved first, a crash could leave it counting bytes the disk never
    got."""
    order = []
    real_fsync, real_received = os.fsync, h.repo.upload_received
    monkeypatch.setattr(uploads_mod.os, "fsync",
                        lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(h.repo, "upload_received",
                        lambda *a: (order.append("index"), real_received(*a))[1])
    uid = h.create(b"x" * 10)["upload_id"]
    order.clear()
    h.uploads.put(uid, 0, b"x" * 10)
    assert order == ["fsync", "index"]


def test_a_chunk_past_the_declared_size_or_an_empty_one_is_400(h):
    uid = h.create(b"x" * 10)["upload_id"]
    for body in (b"x" * 11, b""):
        with pytest.raises(BatchError) as e:
            h.uploads.put(uid, 0, body)
        assert e.value.status == 400
    assert h.repo.get_upload(uid)["received"] == 0


def test_a_lost_part_file_restarts_the_upload_from_zero(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    h.data.part_path(uid).unlink()
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(uid, CHUNK, data[CHUNK:2 * CHUNK])
    assert e.value.extra == {"received": 0, "state": "receiving"}
    assert h.uploads.put(uid, 0, data[:CHUNK]) == {"received": CHUNK}


def test_low_disk_is_507_and_writes_nothing(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.free = 1000 + CHUNK - 1          # this chunk would dip one byte into the reserve
    with pytest.raises(DiskFull) as e:
        h.uploads.put(uid, 0, data[:CHUNK])
    assert e.value.status == 507
    assert h.repo.get_upload(uid)["received"] == 0
    assert h.data.part_path(uid).read_bytes() == b""
    with pytest.raises(DiskFull):
        h.create(b"y" * 200)
    # The refused upload left no row behind.
    assert h.repo._conn.execute("SELECT COUNT(*) FROM upload").fetchone()[0] == 1


@pytest.mark.parametrize("err,status", [(errno.ENOSPC, 507), (errno.EIO, 500)])
def test_a_write_fault_is_reported_by_kind_and_moves_nothing(h, monkeypatch, err, status):
    uid = h.create(b"x" * 10)["upload_id"]

    def failing(fd):
        raise OSError(err, "boom", "/secret/path")
    monkeypatch.setattr(uploads_mod.os, "fsync", failing)
    with pytest.raises(BatchError) as e:
        h.uploads.put(uid, 0, b"x" * 10)
    assert e.value.status == status and "secret" not in str(e.value)
    assert h.repo.get_upload(uid)["received"] == 0


def test_complete_stages_a_pdf_with_its_hash_pages_and_bytes(h):
    data = pdf_bytes(pages=1)
    uid = h.send(data, rel_path="Kunde/Teile")
    done = h.uploads.complete(uid)
    (row,) = done["documents"]
    assert (done["state"], done["ignored"]) == ("complete", [])
    assert (row["file_name"], row["rel_path"], row["size"], row["pages"]) == (
        "a.pdf", "Kunde/Teile", len(data), 1)
    assert row["included"] and not row["unreadable"] and row["oversized"] is False
    d = h.data.doc_dir(row["id"])
    assert (d / "input.pdf").read_bytes() == data
    assert h.repo.get(row["id"])["sha256"] == hashlib.sha256(data).hexdigest()
    assert not (d / IMPORTING_MARKER).exists()
    assert not h.data.part_path(uid).exists()


def test_complete_is_idempotent(h):
    uid = h.send(pdf_bytes())
    first = h.uploads.complete(uid)
    assert h.uploads.complete(uid) == first
    assert len(h.doc_dirs()) == 1


def test_complete_before_every_byte_is_409_with_the_offset(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    with pytest.raises(UploadConflict) as e:
        h.uploads.complete(uid)
    assert e.value.extra == {"received": CHUNK, "state": "receiving"}


def test_complete_trusts_the_file_over_the_index(h):
    """The index counted bytes the part file no longer holds (cut short
    outside this process). Only bytes on disk count: resend from there."""
    data = pdf_bytes()
    uid = h.send(data)
    with open(h.data.part_path(uid), "r+b") as f:
        f.truncate(CHUNK)
    with pytest.raises(UploadConflict) as e:
        h.uploads.complete(uid)
    assert e.value.extra == {"received": CHUNK, "state": "receiving"}
    for off in range(CHUNK, len(data), CHUNK):
        h.uploads.put(uid, off, data[off:off + CHUNK])
    assert h.uploads.complete(uid)["state"] == "complete"


def test_a_failed_completion_leaves_no_drawing_and_can_be_retried(h, monkeypatch):
    """The files are written first, each marked .importing; when the index
    transaction then fails, the files go, the upload is whole again, and a
    retry completes it."""
    data = pdf_bytes()
    uid = h.send(data)
    real = h.repo.finish_completion
    seen = {}

    def failing(upload_id, docs, ignored):
        seen["marked"] = [(h.data.doc_dir(d["id"]) / IMPORTING_MARKER).exists() for d in docs]
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(h.repo, "finish_completion", failing)
    with pytest.raises(sqlite3.OperationalError):
        h.uploads.complete(uid)
    assert seen["marked"] == [True]
    assert h.doc_dirs() == []
    up = h.repo.get_upload(uid)
    assert (up["state"], up["received"]) == ("receiving", len(data))
    assert h.data.part_path(uid).read_bytes() == data
    monkeypatch.setattr(h.repo, "finish_completion", real)
    assert len(h.uploads.complete(uid)["documents"]) == 1


@pytest.mark.parametrize("data", [b"not a pdf at all", b""], ids=["garbage", "empty"])
def test_an_unreadable_file_is_staged_but_never_included(h, data):
    (row,) = h.imported(data)["documents"]
    assert (row["unreadable"], row["included"], row["pages"]) == (True, False, None)


@pytest.mark.parametrize("kw", [{"pages": 2}, {"width": 7000, "height": 5000}],
                         ids=["multi-page", "oversized"])
def test_an_out_of_scope_file_is_staged_excluded(h, kw):
    (row,) = h.imported(pdf_bytes(**kw))["documents"]
    assert (row["out_of_scope"], row["included"], row["allowed_out_of_scope"]) == (True, False, False)


def test_a_second_copy_is_a_duplicate_excluded_by_default(h):
    data = pdf_bytes()
    (first,) = h.imported(data, name="a.pdf")["documents"]
    (second,) = h.imported(data, name="b.pdf")["documents"]
    assert first["included"] and second["duplicate_of"]["id"] == first["id"]
    assert second["included"] is False


def test_delete_removes_an_unfinished_upload_and_its_part(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.delete(uid)
    assert not h.data.part_path(uid).exists()
    with pytest.raises(UnknownUpload):
        h.repo.get_upload(uid)
    done = h.imported(data)
    with pytest.raises(InvalidTransition):
        h.uploads.delete(done["upload_id"])


def test_logs_carry_ids_and_counts_never_a_file_name(h, caplog):
    caplog.set_level(logging.DEBUG)
    uid = h.send(pdf_bytes(), name="Geheim-Flansch.pdf", rel_path="Kunde Meier")
    h.uploads.complete(uid)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert uid[:8] in text
    assert "Geheim" not in text and "Meier" not in text and uid not in text
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_uploads.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.uploads'`.

- [ ] **Step 3: Implement** `app/batch/uploads.py`:

```python
"""Chunked, resumable upload protocol (batch design §2) -- the file side;
the index side is Repo.

    create   -> an `upload` row in a draft batch and an empty part file. An
                unfinished upload of the same file is RETURNED instead, with
                its offset: re-selecting the files resumes (decision 21).
    put      -> bytes at `offset`, accepted only when offset == received,
                fsynced BEFORE the index moves -- so a retry after a lost
                reply is answered 409 + the true offset, never written twice,
                and a restart loses at most the chunk in flight.
    complete -> verify the size, then stage: a PDF becomes one staged
                document (full SHA-256, pages, oversized, unreadable).
                Idempotent: a repeated call returns what was staged.

A free-disk reserve (SINDRI_MIN_FREE_MB) answers 507 at every step that
writes, so an import can never fill the disk under a review journal. It is a
guard, not a limit: no file is refused for its size (decision 29).

Logs: ids[:8], counts and exception TYPES only -- never a file name or a
folder (CLAUDE.md §1)."""
import errno
import hashlib
import logging
import os
import re
import shutil
import threading
import uuid
from typing import Optional

from app.batch.paths import DOC_ID_RE, safe_rel_parts
from app.batch.pdf_facts import pdf_facts
from app.batch.repo import BatchError, UnknownBatch, UnknownUpload, UploadConflict

log = logging.getLogger(__name__)

MIB = 1024 * 1024
# The most one PUT may carry. It bounds the memory of ONE REQUEST, not the
# size of a file: a file of any size arrives in as many chunks as it needs
# (decision 29 -- no upload limits). The browser sends 8 MiB (upload.js).
MAX_CHUNK = 64 * MIB
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
# Marks a docs/<id>/ written by a completion whose index rows have not
# committed yet. Only a marked dir with no row is ever swept at startup, so
# a lost or replaced index can never make the sweep delete real drawings.
IMPORTING_MARKER = ".importing"
_COPY_PIECE = MIB
# The name is stored and shown, and becomes a file name in the mass-export
# ZIP (design §4); a separator or control character has no business in it.
_NAME_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]")
# Importable kinds by extension. The browser filters too; this is the
# server's word.
_KINDS = {".pdf": "pdf"}


class DiskFull(BatchError):
    status = 507

    def __init__(self):
        super().__init__("not enough free disk space on the server")


class StorageFault(BatchError):
    status = 500

    def __init__(self):
        super().__init__("could not store the upload")


def free_bytes(path) -> int:
    return shutil.disk_usage(path).free


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while piece := f.read(_COPY_PIECE):
            h.update(piece)
    return h.hexdigest()


def _fsync_dir(path) -> None:
    # A new file's NAME is durable only once its directory is synced.
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path) -> None:
    with open(path, "rb+") as f:
        os.fsync(f.fileno())


def upload_kind(name: str) -> Optional[str]:
    return _KINDS.get(os.path.splitext(name)[1].lower())


class Uploads:
    def __init__(self, repo, data, min_free_bytes: int, free_bytes=free_bytes):
        self.repo = repo
        self.data = data
        self.min_free_bytes = min_free_bytes
        self._free = free_bytes
        # One lock per upload, held across check-offset / write / fsync /
        # move-offset and across completion: a client retry racing its own
        # first attempt is serialised, so the check and the write cannot
        # interleave. Grows by one small lock per upload, like app.main's
        # export locks (single process, decision 5).
        self._locks = {}
        self._locks_guard = threading.Lock()

    def _lock(self, upload_id) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(upload_id, threading.Lock())

    def _part(self, upload_id):
        try:
            return self.data.part_path(upload_id)
        except ValueError:
            raise UnknownUpload("no such upload") from None

    def free_mb(self) -> int:
        return self._free(self.data.root) // MIB

    def guard_disk(self, need: int = 0) -> None:
        """507 when writing `need` more bytes would leave less than the
        reserve."""
        if self._free(self.data.root) - need < self.min_free_bytes:
            raise DiskFull()

    @staticmethod
    def _storage_error(e: OSError, upload_id: str) -> BatchError:
        # TYPE and id[:8] only: an OSError's text names the path (CLAUDE.md §1).
        log.warning("upload %s not stored: %s", upload_id[:8], type(e).__name__)
        return DiskFull() if e.errno == errno.ENOSPC else StorageFault()

    # ----- create ---------------------------------------------------------
    def create(self, *, batch_id, name, size, last_modified, rel_path, fingerprint) -> dict:
        if not isinstance(name, str) or not name.strip() or _NAME_UNSAFE.search(name):
            raise BatchError("not a usable file name")
        name = name.strip()
        kind = upload_kind(name)
        if kind is None:
            raise BatchError("only PDF files can be imported")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BatchError("size must be a whole number of bytes")
        if not isinstance(fingerprint, str) or not FINGERPRINT_RE.match(fingerprint):
            raise BatchError("fingerprint must be 64 lowercase hex characters")
        parts = safe_rel_parts(rel_path or "")
        if parts is None:
            raise BatchError("unsafe folder path")
        if not isinstance(batch_id, str) or not DOC_ID_RE.match(batch_id):
            raise UnknownBatch("no such batch")
        row, resumed = self.repo.create_upload(
            batch_id=batch_id, name=name, rel_path="/".join(parts), size=size,
            last_modified=last_modified, fingerprint=fingerprint, kind=kind)
        uid = row["id"]
        try:
            # What is still to come, not the whole file: a resumed upload
            # needs room only for the rest.
            self.guard_disk(row["size"] - row["received"])
        except DiskFull:
            if not resumed:
                self.repo.delete_upload(uid)
            raise
        if not resumed:
            part = self.data.part_path(uid)
            try:
                with open(part, "xb"):
                    pass
                _fsync_dir(part.parent)
            except OSError as e:
                self.repo.delete_upload(uid)
                part.unlink(missing_ok=True)
                raise self._storage_error(e, uid) from None
        log.info("upload %s %s at %d of %d bytes", uid[:8],
                 "resumed" if resumed else "created", row["received"], row["size"])
        return {"upload_id": uid, "received": row["received"], "state": row["state"],
                "resumed": resumed}

    # ----- put ------------------------------------------------------------
    def put(self, upload_id, offset: int, body: bytes) -> dict:
        part = self._part(upload_id)
        with self._lock(upload_id):
            up = self.repo.get_upload(upload_id)
            if up["state"] != "receiving":
                raise UploadConflict("the upload is no longer receiving",
                                     received=up["received"], state=up["state"])
            if offset != up["received"]:
                raise UploadConflict("offset does not match what the server holds",
                                     received=up["received"], state=up["state"])
            if not body:
                raise BatchError("empty chunk")
            if offset + len(body) > up["size"]:
                raise BatchError("chunk runs past the declared file size")
            if not part.exists():
                # The part vanished (removed by hand, lost with a disk) while
                # the index still counts its bytes: they are gone, so the
                # honest offset is 0.
                self.repo.reset_received(upload_id, 0)
                if offset != 0:
                    raise UploadConflict("the server lost this upload's data; start again",
                                         received=0, state="receiving")
            self.guard_disk(len(body))
            try:
                with open(part, "r+b" if part.exists() else "wb") as f:
                    f.seek(offset)
                    f.write(body)
                    # Bytes past this chunk can only be a write whose offset
                    # move never committed (a crash between fsync and index);
                    # they are not the file's.
                    f.truncate()
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                raise self._storage_error(e, upload_id) from None
            try:
                received = self.repo.upload_received(upload_id, offset, offset + len(body))
            except UnknownUpload:
                part.unlink(missing_ok=True)       # discarded while we wrote
                raise
        return {"received": received}

    # ----- complete -------------------------------------------------------
    def complete(self, upload_id) -> dict:
        part = self._part(upload_id)
        with self._lock(upload_id):
            up = self.repo.get_upload(upload_id)
            if up["state"] == "complete":
                return self._completed(upload_id)
            if up["state"] != "receiving" or up["received"] != up["size"]:
                raise UploadConflict("the upload is not complete",
                                     received=up["received"], state=up["state"])
            held = part.stat().st_size if part.exists() else 0
            if held != up["size"]:
                # The index counted bytes the file does not hold: only bytes
                # on disk count, and the client resends from there.
                held = min(held, up["size"])
                self.repo.reset_received(upload_id, held)
                raise UploadConflict("the server holds less of this file than it counted",
                                     received=held, state="receiving")
            self.repo.begin_completion(upload_id)
            created = []
            try:
                docs, ignored = self._stage_files(part, up, created)
                if created:
                    _fsync_dir(self.data.docs)
                rows = self.repo.finish_completion(upload_id, docs, ignored)
            except BaseException as e:
                for d in created:
                    shutil.rmtree(d, ignore_errors=True)
                try:
                    self.repo.abort_completion(upload_id)
                except Exception as abort_error:
                    log.warning("upload %s: completion not rolled back: %s",
                                upload_id[:8], type(abort_error).__name__)
                if isinstance(e, OSError):
                    raise self._storage_error(e, upload_id) from None
                raise
            for d in created:
                (d / IMPORTING_MARKER).unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        log.info("upload %s complete: %d staged, %d ignored",
                 upload_id[:8], len(rows), len(ignored))
        return {"upload_id": upload_id, "state": "complete", "documents": rows,
                "ignored": ignored}

    def _completed(self, upload_id) -> dict:
        done = self.repo.completed_upload(upload_id)
        return {"upload_id": upload_id, "state": "complete", "documents": done["documents"],
                "ignored": done["upload"]["ignored"]}

    def _stage_files(self, part, up, created):
        """(documents to stage, ignored entries) for one completed upload."""
        return [self._stage_pdf(part, up, created)], []

    def _new_doc_dir(self, created):
        doc_id = uuid.uuid4().hex
        d = self.data.doc_dir(doc_id)
        d.mkdir()
        created.append(d)
        # FIRST, before any content: until the rows commit this dir belongs
        # to no row, and only a marked dir is ever swept.
        (d / IMPORTING_MARKER).write_bytes(b"")
        return doc_id, d

    def _stage_pdf(self, part, up, created) -> dict:
        doc_id, d = self._new_doc_dir(created)
        target = d / "input.pdf"
        try:
            # A hard link: no second copy of a large file, and the part
            # stays whole until the rows commit, so a failed completion is
            # simply retried.
            os.link(part, target)
        except OSError:
            # A filesystem without hard links (some network mounts).
            self.guard_disk(up["size"])
            shutil.copyfile(part, target)
            _fsync_file(target)
        _fsync_dir(d)
        facts = pdf_facts(target)
        return {"id": doc_id, "file_name": up["name"], "rel_path": up["rel_path"],
                "size": up["size"], "sha256": sha256_file(target), "pages": facts.pages,
                "oversized": facts.oversized, "unreadable": not facts.readable}

    # ----- delete ---------------------------------------------------------
    def delete(self, upload_id) -> None:
        """An unfinished upload the user removed. Idempotent; a complete one
        is refused by the repo (its drawings are removed one by one)."""
        part = self._part(upload_id)
        with self._lock(upload_id):
            self.repo.delete_upload(upload_id)
            part.unlink(missing_ok=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch/test_uploads.py tests/review/test_product_boundary.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1575 passed, 2 skipped** (+30).
```bash
git add app/batch/uploads.py tests/batch/test_uploads.py
```
```bash
git commit -m "batch: chunked upload protocol, file side" -m "A chunk is accepted only at the server's offset and fsynced before the index moves, so a retry after a lost reply is a 409 with the true offset and a restart loses at most one chunk. Completion trusts the bytes on disk over the index, hashes the full file and hard-links it into the drawing's dir, marked .importing until its rows commit. A free-disk reserve answers 507 at every writing step; it is a guard, not a size limit." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Import API — batches and uploads over HTTP

**Data-integrity: yes** (the protocol's public surface; the 409 body is what clients resync from).

**Files:**
- Modify: `app/main.py`, `app/batch/runtime.py`, `tests/conftest.py`
- Create: `tests/test_api_import.py`

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py` — inside `_isolated_runtime`, after the two existing `setenv` lines, add:
```python
    # No disk reserve in tests: a developer's /tmp can have less free space
    # than the deployment default (2048 MB), and every upload test would then
    # fail with 507. The reserve itself is tested with injected free space.
    monkeypatch.setenv("SINDRI_MIN_FREE_MB", "0")
```

`tests/test_api_import.py`:
```python
"""The import API (batch design §2): draft batches and the chunked upload
protocol over HTTP. Test PDFs are built in code (tests/batch/pdfs.py)."""
import hashlib
import inspect
import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.batch.pdfs import pdf_bytes

client = TestClient(main.app)
CHUNK = 200


def new_batch(name="Lieferung"):
    r = client.post("/api/batches", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def new_upload(batch_id, data, name="a.pdf", rel_path=""):
    r = client.post("/api/uploads", json={
        "batch_id": batch_id, "name": name, "size": len(data), "rel_path": rel_path,
        "fingerprint": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    return r.json()


def put(upload_id, offset, chunk):
    return client.put(f"/api/uploads/{upload_id}?offset={offset}", content=chunk)


def send(batch_id, data, name="a.pdf", rel_path=""):
    up = new_upload(batch_id, data, name, rel_path)
    for off in range(0, len(data), CHUNK):
        assert put(up["upload_id"], off, data[off:off + CHUNK]).status_code == 200
    return up["upload_id"]


def import_file(data, name="a.pdf", batch_id=None):
    batch_id = batch_id or new_batch()["id"]
    r = client.post(f"/api/uploads/{send(batch_id, data, name)}/complete")
    assert r.status_code == 200, r.text
    return batch_id, r.json()


def test_a_file_uploaded_in_chunks_becomes_a_staged_drawing():
    data = pdf_bytes()
    batch_id, done = import_file(data)
    (row,) = done["documents"]
    assert client.get(f"/api/documents/{row['id']}").json()["state"] == "staged"
    view = client.get(f"/api/batches/{batch_id}").json()
    assert view["batch"]["draft"] is True
    assert [d["id"] for d in view["documents"]] == [row["id"]]
    c = view["counts"]
    assert (c["documents"], c["included"], c["excluded"]) == (1, 1, 0)
    assert (main.runtime().data.doc_dir(row["id"]) / "input.pdf").read_bytes() == data


def test_a_wrong_offset_answers_409_with_where_the_server_stands():
    data = pdf_bytes()
    up = new_upload(new_batch()["id"], data)
    r = put(up["upload_id"], CHUNK, data[CHUNK:2 * CHUNK])
    assert r.status_code == 409
    assert {k: r.json()[k] for k in ("received", "state")} == {"received": 0, "state": "receiving"}


def test_a_chunk_resent_after_a_lost_reply_is_409_and_written_once():
    data = pdf_bytes()
    up = new_upload(new_batch()["id"], data)
    assert put(up["upload_id"], 0, data[:CHUNK]).json() == {"received": CHUNK}
    r = put(up["upload_id"], 0, data[:CHUNK])
    assert (r.status_code, r.json()["received"]) == (409, CHUNK)
    part = main.runtime().data.part_path(up["upload_id"])
    assert part.read_bytes() == data[:CHUNK]


def test_re_posting_the_same_file_resumes_it():
    data = pdf_bytes()
    batch_id = new_batch()["id"]
    up = new_upload(batch_id, data)
    put(up["upload_id"], 0, data[:CHUNK])
    again = new_upload(batch_id, data)
    assert (again["upload_id"], again["received"], again["resumed"]) == (up["upload_id"], CHUNK, True)


def test_low_disk_is_507_and_health_reports_the_free_space(monkeypatch):
    rt = main.runtime()
    monkeypatch.setattr(rt.uploads, "min_free_bytes", 1 << 62)
    r = client.post("/api/uploads", json={"batch_id": new_batch()["id"], "name": "a.pdf",
                                          "size": 10, "fingerprint": "f" * 64})
    assert (r.status_code, r.json()["detail"]) == (507, "not enough free disk space on the server")
    health = client.get("/api/health").json()
    assert isinstance(health["disk_free_mb"], int) and health["min_free_mb"] == (1 << 62) // (1 << 20)


def test_every_test_runs_without_the_disk_reserve():
    assert main.runtime().uploads.min_free_bytes == 0


@pytest.mark.parametrize("method,path", [
    ("put", "/api/uploads/nope?offset=0"), ("post", "/api/uploads/nope/complete"),
    ("delete", "/api/uploads/nope"), ("put", f"/api/uploads/{'0' * 32}?offset=0"),
    ("post", f"/api/uploads/{'0' * 32}/complete"),
    ("get", "/api/batches/nope"), ("get", f"/api/batches/{'0' * 32}"),
])
def test_malformed_and_unknown_ids_are_404(method, path):
    kw = {"content": b"x"} if method == "put" else {}
    assert getattr(client, method)(path, **kw).status_code == 404


def test_an_upload_into_an_unknown_batch_is_404():
    r = client.post("/api/uploads", json={"batch_id": "0" * 32, "name": "a.pdf", "size": 1,
                                          "fingerprint": "f" * 64})
    assert r.status_code == 404


def test_a_batch_gets_a_default_name_and_can_be_renamed():
    b = client.post("/api/batches", json={}).json()
    # Only a fallback: the browser sends its own local name (upload.js).
    assert b["name"].startswith("Import ") and b["name"].endswith(" UTC") and b["draft"] is True
    r = client.patch(f"/api/batches/{b['id']}", json={"name": "  Lieferung 12 "})
    assert r.json()["name"] == "Lieferung 12"
    for bad in ["", "   ", "a\x01b"]:
        assert client.patch(f"/api/batches/{b['id']}", json={"name": bad}).status_code == 400


def test_batches_are_listed_with_their_counts():
    batch_id, _ = import_file(pdf_bytes())
    listed = {b["id"]: b for b in client.get("/api/batches").json()["batches"]}
    assert (listed[batch_id]["documents"], listed[batch_id]["uploading"]) == (1, 0)


def test_an_unfinished_upload_can_be_removed():
    data = pdf_bytes()
    up = new_upload(new_batch()["id"], data)
    assert client.delete(f"/api/uploads/{up['upload_id']}").status_code == 200
    assert not main.runtime().data.part_path(up["upload_id"]).exists()
    assert client.delete(f"/api/uploads/{up['upload_id']}").status_code == 200   # idempotent


def test_errors_and_logs_never_carry_the_file_name(caplog):
    caplog.set_level(logging.DEBUG)
    r = client.post("/api/uploads", json={"batch_id": new_batch()["id"], "name": "Geheim.exe",
                                          "size": 1, "fingerprint": "f" * 64})
    assert r.status_code == 400 and "Geheim" not in r.text
    import_file(pdf_bytes(), name="Geheim-Flansch.pdf")
    assert "Geheim" not in "\n".join(rec.getMessage() for rec in caplog.records)


def test_a_chunk_over_the_bound_is_413_before_anything_is_written(monkeypatch):
    """MAX_CHUNK bounds one request's memory, not a file's size: lowered
    here so the test need not send 64 MiB."""
    import app.batch.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "MAX_CHUNK", 10)
    up = new_upload(new_batch()["id"], b"x" * 20)
    r = put(up["upload_id"], 0, b"x" * 11)
    assert (r.status_code, r.json()["detail"]) == (413, "chunk too large or of unknown length")
    assert put(up["upload_id"], 0, b"x" * 10).json() == {"received": 10}


def test_a_chunk_of_unknown_length_is_413():
    """A streamed body with no Content-Length could be any size."""
    up = new_upload(new_batch()["id"], b"x" * 20)
    r = client.put(f"/api/uploads/{up['upload_id']}?offset=0", content=iter([b"x" * 5]))
    assert r.status_code == 413
    assert main.runtime().data.part_path(up["upload_id"]).read_bytes() == b""


def test_a_malformed_import_request_never_echoes_its_fields():
    """FastAPI's default 422 body repeats the input -- here a file name."""
    r = client.post("/api/uploads", json={"batch_id": new_batch()["id"], "name": ["Geheim.pdf"],
                                          "size": "Kunde Meier", "fingerprint": "f" * 64})
    assert r.status_code == 422
    assert "Geheim" not in r.text and "Meier" not in r.text
    assert r.json() == {"detail": "request rejected: malformed fields"}


def test_blocking_handlers_run_off_the_event_loop():
    """Hashing, PDF inspection and SQLite all block; a plain def runs in the
    threadpool. The chunk PUT is async only to read its body, and hands the
    write and fsync to the threadpool itself."""
    for handler in (main.create_upload, main.complete_upload, main.delete_upload,
                    main.create_batch, main.get_batch):
        assert not inspect.iscoroutinefunction(handler)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_api_import.py -q`
Expected: FAIL — `/api/batches` answers 405/404, and `main.create_upload` does not exist.

- [ ] **Step 3: Implement**

`app/batch/runtime.py`:
1. Add `from app.batch.uploads import MIB, Uploads` and, below the imports:
```python
# The free-disk reserve (batch design §2): below it, uploads and unpacking
# answer 507 so an import can never fill the disk under a review journal.
DEFAULT_MIN_FREE_MB = 2048
```
2. Change `__init__`'s signature to `def __init__(self, data_dir, review_dir, extract_fn, header_fn, min_free_mb: int = DEFAULT_MIN_FREE_MB):` and add after `self.repo = Repo(self.conn)`:
```python
        self.uploads = Uploads(self.repo, self.data, min_free_bytes=min_free_mb * MIB)
```
3. In `from_env`, before the `return`:
```python
        # A malformed value fails the boot loudly rather than running with no
        # reserve. "0" turns the guard off (the test suite does).
        min_free = int(os.environ.get("SINDRI_MIN_FREE_MB") or DEFAULT_MIN_FREE_MB)
```
and pass `min_free_mb=min_free` to `cls(...)`.

`app/main.py`:
1. Imports: change `from fastapi import FastAPI, UploadFile, File, HTTPException` to `from fastapi import FastAPI, UploadFile, File, HTTPException, Request`; add `from datetime import datetime, timezone`, `from fastapi.exceptions import RequestValidationError`, `from fastapi.exception_handlers import request_validation_exception_handler`, `from starlette.concurrency import run_in_threadpool`, `from app.batch import uploads as batch_uploads`, `from app.batch.paths import DOC_ID_RE`, `from app.batch.staging import clean_batch_name`.
2. Replace `_domain_error` with:
```python
@app.exception_handler(BatchError)
@app.exception_handler(ReviewError)
async def _domain_error(request, exc):
    # Safe to echo: these messages carry ids[:8], state names and fixed
    # sentences, never client text. `extra` carries numbers and a state name
    # only (an upload's received offset), so a client can resync from it.
    return JSONResponse({"detail": str(exc), **getattr(exc, "extra", {})},
                        status_code=exc.status)


# Routes whose request bodies carry client file and folder names.
_IMPORT_PATHS = ("/api/uploads", "/api/batches")


@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc):
    """FastAPI's default 422 body echoes the offending input; on the import
    routes that is a client file or folder name (CLAUDE.md §1). A fixed
    sentence there; the default everywhere else."""
    if request.url.path.startswith(_IMPORT_PATHS):
        return JSONResponse({"detail": "request rejected: malformed fields"}, status_code=422)
    return await request_validation_exception_handler(request, exc)
```
3. After `_doc_dir`, add:
```python
def _batch_id(batch_id: str) -> str:
    if not DOC_ID_RE.match(batch_id or ""):
        raise HTTPException(status_code=404, detail="unknown batch")
    return batch_id


def _batch_name(name) -> str:
    clean = clean_batch_name(name)
    if clean is None:
        raise HTTPException(status_code=400, detail="not a usable batch name")
    return clean
```
4. After `class SealRequest`, add the request models:
```python
class BatchCreate(BaseModel):
    name: Optional[str] = None


class BatchRename(BaseModel):
    name: str


class UploadCreate(BaseModel):
    batch_id: str
    name: str
    size: int
    fingerprint: str
    rel_path: str = ""
    last_modified: Optional[int] = None
```
5. In `health()`, before `return status`:
```python
    rt = runtime()
    # The free-disk reserve (batch design §5): what is left, and the floor
    # below which uploads answer 507.
    status["disk_free_mb"] = rt.uploads.free_mb()
    status["min_free_mb"] = rt.uploads.min_free_bytes // (1024 * 1024)
```
6. After the `upload` endpoint (it stays until Task 12), add:
```python
@app.post("/api/batches")
def create_batch(req: BatchCreate):
    """A draft import (decision 10). The browser names it after the ZIP or
    folder, or its LOCAL date and time (decision 27). A name-less request
    gets a fallback in the server's clock, labelled UTC because the server
    cannot know the user's zone. Editable later."""
    rt = runtime()
    name = (_batch_name(req.name) if req.name is not None
            else datetime.now(timezone.utc).strftime("Import %Y-%m-%d %H:%M UTC"))
    return rt.repo.create_batch(name)


@app.get("/api/batches")
def list_batches():
    return {"batches": runtime().repo.list_batches()}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    """The staging view's data: rows with their facts and Include state,
    unfinished uploads, ignored ZIP entries, and counts that reconcile."""
    return runtime().repo.staging(_batch_id(batch_id))


@app.patch("/api/batches/{batch_id}")
def rename_batch(batch_id: str, req: BatchRename):
    return runtime().repo.rename_batch(_batch_id(batch_id), _batch_name(req.name))


@app.post("/api/uploads")
def create_upload(req: UploadCreate):
    """Start -- or, for the same file in the same draft, resume -- a chunked
    upload (batch design §2). Answers the offset to send from."""
    return runtime().uploads.create(
        batch_id=req.batch_id, name=req.name, size=req.size,
        last_modified=req.last_modified, rel_path=req.rel_path,
        fingerprint=req.fingerprint)


_CHUNK_TOO_LARGE = "chunk too large or of unknown length"


@app.put("/api/uploads/{upload_id}")
async def put_upload_chunk(upload_id: str, offset: int, request: Request):
    """One chunk at `offset`. Async only to read the body; the write and its
    fsync block, so they run in the threadpool. A 409 carries `received`:
    the client continues from there, never from what it thinks it sent.

    Bounded by MAX_CHUNK: a missing or larger Content-Length is refused
    before a byte is read, and the read itself stops at the bound in case
    the header lies. That bounds one request's memory; a file of any size
    still arrives in as many chunks as it needs (decision 29)."""
    limit = batch_uploads.MAX_CHUNK          # read per call: tests lower it
    try:
        declared = int(request.headers["content-length"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=413, detail=_CHUNK_TOO_LARGE)
    if declared < 0 or declared > limit:
        raise HTTPException(status_code=413, detail=_CHUNK_TOO_LARGE)
    body = bytearray()
    async for piece in request.stream():
        body += piece
        if len(body) > limit:
            raise HTTPException(status_code=413, detail=_CHUNK_TOO_LARGE)
    rt = runtime()
    return await run_in_threadpool(rt.uploads.put, upload_id, offset, bytes(body))


@app.post("/api/uploads/{upload_id}/complete")
def complete_upload(upload_id: str):
    """Verify, hash and stage. Idempotent: a retry after a lost reply gets
    the same documents. A plain def: hashing and PDF inspection block."""
    return runtime().uploads.complete(upload_id)


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: str):
    runtime().uploads.delete(upload_id)
    return {"ok": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_api_import.py tests/test_api.py tests/batch/test_runtime.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1597 passed, 2 skipped** (+22).
```bash
git add app/main.py app/batch/runtime.py tests/conftest.py tests/test_api_import.py
```
```bash
git commit -m "api: draft batches and the chunked upload protocol" -m "POST /api/batches, GET/PATCH /api/batches/{id}, and /api/uploads (create or resume, PUT at an offset, complete, delete). A 409 carries the server's offset and state so the client resyncs; a full disk is 507 and /api/health reports the free space. A PUT is bounded at 64 MiB (one request's memory, not a file limit), and a malformed import request gets a fixed-sentence 422 instead of FastAPI's echo of the client's file name. The disk reserve comes from SINDRI_MIN_FREE_MB (default 2048) and is off in tests." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

> **Milestone: upload-protocol** - state must be checkpointed here before continuing.

---

### Task 7: ZIP imports — unpacked server-side, PDFs only, path-safe

**Data-integrity: yes** (unpacking untrusted archives into the data dir).

**Files:**
- Create: `app/batch/unzip.py`, `tests/batch/test_unzip.py`
- Modify: `app/batch/uploads.py`, `tests/batch/test_uploads.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_unzip.py`:
```python
"""ZIP entry classification (batch design §2, decision 22): PDFs only,
path-safe. Pure: built on ZipInfo objects, no archive needed (an encrypted
entry's flag cannot even be written by zipfile)."""
import stat
import zipfile

import pytest

from app.batch.unzip import Ignored, PdfEntry, classify


def info(name, *, symlink=False, encrypted=False):
    i = zipfile.ZipInfo(name)
    if symlink:
        i.external_attr = (stat.S_IFLNK | 0o777) << 16
    if encrypted:
        i.flag_bits |= 0x1
    return i


@pytest.mark.parametrize("name,file_name,folder", [
    ("w1.pdf", "w1.pdf", ""),
    ("Teile/Welle/w1.PDF", "w1.PDF", "Teile/Welle"),
    ("Teile\\Welle\\w1.pdf", "w1.pdf", "Teile/Welle"),
])
def test_a_pdf_entry_keeps_its_folder(name, file_name, folder):
    e = classify(info(name))
    assert isinstance(e, PdfEntry) and (e.file_name, e.folder) == (file_name, folder)


@pytest.mark.parametrize("name,kw,reason", [
    ("../../evil.pdf", {}, "unsafe_path"),
    ("/etc/abs.pdf", {}, "unsafe_path"),
    ("C:\\win.pdf", {}, "unsafe_path"),
    ("a\x01b.pdf", {}, "unsafe_path"),
    ("link.pdf", {"symlink": True}, "symlink"),
    ("notes.txt", {}, "not_pdf"),
    ("inner.zip", {}, "not_pdf"),
    ("__MACOSX/Teile/._w1.pdf", {}, "system_file"),
    ("Teile/._w1.pdf", {}, "system_file"),
    ("Teile/Thumbs.db", {}, "system_file"),
    ("secret.pdf", {"encrypted": True}, "encrypted_entry"),
])
def test_everything_else_is_listed_as_ignored_with_its_reason(name, kw, reason):
    assert classify(info(name, **kw)) == Ignored(name, reason)


def test_a_directory_entry_is_neither_staged_nor_listed():
    assert classify(info("Teile/")) is None
```

Append to `tests/batch/test_uploads.py` (add `import io`, `import stat`, `import zipfile` to its imports):
```python
def zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    """entries: (name or ZipInfo, data)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def symlink_entry(name):
    i = zipfile.ZipInfo(name)
    i.external_attr = (stat.S_IFLNK | 0o777) << 16
    return i


def test_a_zip_is_unpacked_into_staged_drawings_and_the_rest_listed(h):
    a, b = pdf_bytes(), pdf_bytes(pages=2)
    blob = zip_bytes([
        ("Teile/Welle/w1.pdf", a), ("Teile/Flansch/f1.pdf", b), ("Teile/Stückliste.txt", b"x"),
        ("../../evil.pdf", a), (symlink_entry("link.pdf"), "/etc/passwd"),
        ("kaputt.pdf", b"not a pdf at all"), ("Teile/", b""),
    ])
    done = h.imported(blob, name="Lieferung.zip")
    rows = {r["file_name"]: r for r in done["documents"]}
    assert set(rows) == {"w1.pdf", "f1.pdf", "kaputt.pdf"}
    assert (rows["w1.pdf"]["rel_path"], rows["w1.pdf"]["included"]) == ("Teile/Welle", True)
    assert (rows["f1.pdf"]["pages"], rows["f1.pdf"]["included"]) == (2, False)
    assert rows["kaputt.pdf"]["unreadable"] is True
    assert sorted((i["name"], i["reason"]) for i in done["ignored"]) == [
        ("../../evil.pdf", "unsafe_path"), ("Teile/Stückliste.txt", "not_pdf"),
        ("link.pdf", "symlink")]
    assert (h.data.doc_dir(rows["w1.pdf"]["id"]) / "input.pdf").read_bytes() == a
    # Nothing was written but three drawing dirs, each holding only its input.
    assert len(h.doc_dirs()) == 3
    for d in h.data.docs.iterdir():
        assert sorted(p.name for p in d.iterdir()) == ["input.pdf"]
    assert list(h.data.uploads.iterdir()) == []
    assert h.repo.staging(h.batch)["uploads"][0]["ignored"] == done["ignored"]


def test_a_zip_from_a_folder_upload_keeps_both_folders(h):
    done = h.imported(zip_bytes([("Welle/w1.pdf", pdf_bytes())]), name="z.zip", rel_path="Kunde")
    assert done["documents"][0]["rel_path"] == "Kunde/Welle"


def test_two_identical_drawings_in_one_zip(h):
    a = pdf_bytes()
    first, second = h.imported(zip_bytes([("x/a.pdf", a), ("y/a.pdf", a)]), name="z.zip")["documents"]
    assert first["included"] and second["duplicate_of"]["id"] == first["id"]
    assert second["included"] is False


def test_a_corrupt_entry_is_ignored_and_the_rest_staged(h):
    a, b = pdf_bytes(), pdf_bytes()
    blob = bytearray(zip_bytes([("good.pdf", a), ("bad.pdf", b)], compression=zipfile.ZIP_STORED))
    at = bytes(blob).index(b) + len(b) // 2
    blob[at] ^= 0xFF                      # stored data no longer matches its CRC
    done = h.imported(bytes(blob), name="z.zip")
    assert [r["file_name"] for r in done["documents"]] == ["good.pdf"]
    assert done["ignored"] == [{"name": "bad.pdf", "reason": "unreadable_entry"}]
    assert len(h.doc_dirs()) == 1


def test_a_file_named_zip_that_is_not_one_is_listed_not_staged(h):
    done = h.imported(b"not a zip", name="z.zip")
    assert (done["state"], done["documents"]) == ("complete", [])
    assert done["ignored"] == [{"name": "z.zip", "reason": "unreadable_zip"}]
    assert h.doc_dirs() == []


def test_low_disk_while_unpacking_is_507_and_leaves_nothing(h):
    blob = zip_bytes([("a.pdf", pdf_bytes()), ("b.pdf", pdf_bytes())])
    uid = h.send(blob, name="z.zip")
    h.free = 1000 + 10                    # room for the archive, not for its contents
    with pytest.raises(DiskFull):
        h.uploads.complete(uid)
    assert h.doc_dirs() == []
    assert h.repo.get_upload(uid)["state"] == "receiving"
    h.free = 10 ** 12
    assert len(h.uploads.complete(uid)["documents"]) == 2


def test_zip_entry_names_are_never_logged(h, caplog):
    caplog.set_level(logging.DEBUG)
    blob = zip_bytes([("Kunde Meier/Geheim.pdf", pdf_bytes()), ("Geheim.txt", b"x")])
    h.imported(blob, name="Vertraulich.zip")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Geheim" not in text and "Meier" not in text and "Vertraulich" not in text
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_unzip.py tests/batch/test_uploads.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch.unzip'`, and the ZIP upload tests fail with 400 "only PDF files can be imported".

- [ ] **Step 3: Implement**

`app/batch/unzip.py`:
```python
"""ZIP entries for an import (batch design §2, decision 22): PDFs only,
path-safe.

An entry is never written to a path built from its name -- every PDF is
copied to docs/<new id>/input.pdf (uploads.py) -- so a hostile name cannot
escape the data dir by construction. Names are still judged, because the
folder is shown to the user and becomes a folder in the mass-export ZIP
(design §4): an absolute, climbing or control-character path, a symlink, an
encrypted entry, an OS litter file and every non-PDF are listed as ignored,
with the reason, and never staged."""
import stat
import zipfile
from dataclasses import dataclass
from typing import Optional, Union

from app.batch.paths import safe_rel_parts

# Litter that operating systems add to archives; listed, never staged.
_SYSTEM_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


@dataclass(frozen=True)
class PdfEntry:
    info: zipfile.ZipInfo
    file_name: str
    folder: str           # posix, '' at the archive's root


@dataclass(frozen=True)
class Ignored:
    name: str             # the entry's own name, shown to the user
    reason: str           # unsafe_path | symlink | system_file | not_pdf |
                          # encrypted_entry | unreadable_entry | unreadable_zip

    def as_dict(self) -> dict:
        return {"name": self.name, "reason": self.reason}


def classify(info: zipfile.ZipInfo) -> Optional[Union[PdfEntry, Ignored]]:
    """None for a directory entry: nothing to stage and nothing to list."""
    if info.is_dir():
        return None
    name = info.filename
    if stat.S_ISLNK(info.external_attr >> 16):
        return Ignored(name, "symlink")
    parts = safe_rel_parts(name)
    if not parts:
        return Ignored(name, "unsafe_path")
    base = parts[-1]
    if parts[0] == "__MACOSX" or base.startswith("._") or base.lower() in _SYSTEM_NAMES:
        return Ignored(name, "system_file")
    if not base.lower().endswith(".pdf"):
        return Ignored(name, "not_pdf")
    if info.flag_bits & 0x1:
        return Ignored(name, "encrypted_entry")
    return PdfEntry(info, base, "/".join(parts[:-1]))
```

`app/batch/uploads.py`:
1. Add `import zipfile` and `import zlib` to the imports, and `from app.batch.unzip import Ignored, classify`.
2. Change `_KINDS` to `{".pdf": "pdf", ".zip": "zip"}` and the refusal in `create` to `raise BatchError("only PDF and ZIP files can be imported")`.
3. In the module docstring, change the `complete` paragraph's first line to: "complete -> verify the size, then stage: a PDF becomes one staged document, a ZIP one per PDF inside it (unzip.py decides which entries; everything else is listed as ignored)."
4. After `_COPY_PIECE`, add:
```python
# What reading a damaged entry can raise: a CRC mismatch, a broken deflate
# stream, a truncated archive, an unsupported compression method. Each
# makes that ONE entry ignored, never the whole import.
_ENTRY_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError,
                 RuntimeError, ValueError)


class _EntryUnreadable(Exception):
    pass
```
5. Replace `_stage_files` with:
```python
    def _stage_files(self, part, up, created):
        """(documents to stage, ignored entries) for one completed upload."""
        if up["kind"] == "zip":
            return self._stage_zip(part, up, created)
        return [self._stage_pdf(part, up, created)], []

    def _stage_zip(self, part, up, created):
        docs, ignored = [], []
        try:
            zf = zipfile.ZipFile(part)
        except (zipfile.BadZipFile, EOFError, ValueError):
            return [], [Ignored(up["name"], "unreadable_zip").as_dict()]
        with zf:
            for info in zf.infolist():
                entry = classify(info)
                if entry is None:
                    continue
                if isinstance(entry, Ignored):
                    ignored.append(entry.as_dict())
                    continue
                doc_id, d = self._new_doc_dir(created)
                target = d / "input.pdf"
                try:
                    sha256, size = self._copy_entry(zf, info, target)
                except _EntryUnreadable:
                    created.remove(d)
                    shutil.rmtree(d, ignore_errors=True)
                    ignored.append(Ignored(info.filename, "unreadable_entry").as_dict())
                    continue
                _fsync_dir(d)
                facts = pdf_facts(target)
                docs.append({
                    "id": doc_id, "file_name": entry.file_name,
                    # A ZIP picked from inside a folder upload keeps both.
                    "rel_path": "/".join(p for p in (up["rel_path"], entry.folder) if p),
                    "size": size, "sha256": sha256, "pages": facts.pages,
                    "oversized": facts.oversized, "unreadable": not facts.readable})
        return docs, ignored

    def _copy_entry(self, zf, info, target):
        """Stream one entry to `target`, hashing as it goes. The disk guard
        runs per piece: an entry's declared size can lie, and decompression
        can expand a small archive into a full disk."""
        digest = hashlib.sha256()
        size = 0
        try:
            src = zf.open(info)
        except _ENTRY_ERRORS as e:
            raise _EntryUnreadable() from e
        with src, open(target, "xb") as out:
            while True:
                try:
                    piece = src.read(_COPY_PIECE)
                except _ENTRY_ERRORS as e:
                    raise _EntryUnreadable() from e
                if not piece:
                    break
                self.guard_disk(len(piece))
                out.write(piece)
                digest.update(piece)
                size += len(piece)
            out.flush()
            os.fsync(out.fileno())
        return digest.hexdigest(), size
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch/test_unzip.py tests/batch/test_uploads.py tests/review/test_product_boundary.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1619 passed, 2 skipped** (+22).
```bash
git add app/batch/unzip.py app/batch/uploads.py tests/batch/test_unzip.py tests/batch/test_uploads.py
```
```bash
git commit -m "batch: unpack ZIP imports server-side, PDFs only, path-safe" -m "Every PDF entry is streamed to a new drawing dir and hashed on the way, never written to a path built from its name. Absolute, climbing and control-character paths, symlinks, encrypted entries, OS litter and non-PDFs are listed as ignored with a reason; a damaged entry is ignored alone. The disk guard runs per piece, because decompression can fill a disk from a small archive. Entry names are never logged." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Staging API — Include, Start, discard

**Data-integrity: yes** (the API door from staged to queued, and file removal).

**Files:**
- Modify: `app/main.py`, `app/batch/repo.py`, `app/batch/uploads.py`
- Test: `tests/test_api_import.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api_import.py`:

```python
def test_including_an_out_of_scope_file_records_the_permission():
    _, done = import_file(pdf_bytes(pages=3))
    (row,) = done["documents"]
    assert row["included"] is False
    r = client.post(f"/api/documents/{row['id']}/include", json={"included": True})
    assert (r.json()["included"], r.json()["allowed_out_of_scope"]) == (True, True)


def test_an_unreadable_pdf_cannot_be_included():
    _, done = import_file(b"not a pdf at all")
    r = client.post(f"/api/documents/{done['documents'][0]['id']}/include", json={"included": True})
    assert r.status_code == 409


def test_start_queues_the_included_and_drops_the_rest():
    batch_id = new_batch()["id"]
    _, ok = import_file(pdf_bytes(), batch_id=batch_id)
    _, multi = import_file(pdf_bytes(pages=2), name="b.pdf", batch_id=batch_id)
    _, bad = import_file(b"not a pdf at all", name="c.pdf", batch_id=batch_id)
    keep = ok["documents"][0]["id"]
    drop = [multi["documents"][0]["id"], bad["documents"][0]["id"]]
    r = client.post(f"/api/batches/{batch_id}/start")
    assert r.json() == {"queued": 1, "dropped": 2, "documents": [keep]}
    doc = client.get(f"/api/documents/{keep}").json()
    assert (doc["state"], doc["queue_position"]) == ("queued", 1)
    for d in drop:
        assert client.get(f"/api/documents/{d}").status_code == 404
        assert not main.runtime().data.doc_dir(d).exists()
    assert client.get(f"/api/batches/{batch_id}").json()["batch"]["draft"] is False


def test_start_wakes_the_worker(monkeypatch):
    woke = []
    monkeypatch.setattr(main.runtime().worker, "wake", lambda: woke.append(1))
    batch_id, _ = import_file(pdf_bytes())
    client.post(f"/api/batches/{batch_id}/start")
    assert woke == [1]


def test_start_is_refused_with_nothing_included_or_while_uploading():
    batch_id, done = import_file(pdf_bytes(pages=2))
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 409
    client.post(f"/api/documents/{done['documents'][0]['id']}/include", json={"included": True})
    new_upload(batch_id, pdf_bytes(), name="late.pdf")
    r = client.post(f"/api/batches/{batch_id}/start")
    assert r.status_code == 409 and "uploading" in r.json()["detail"]
    assert client.get(f"/api/batches/{batch_id}").json()["batch"]["draft"] is True


def test_start_carries_the_permission_onto_every_out_of_scope_drawing():
    batch_id, done = import_file(pdf_bytes(width=7000, height=5000))
    doc_id = done["documents"][0]["id"]
    client.post(f"/api/documents/{doc_id}/include", json={"included": True})
    client.post(f"/api/batches/{batch_id}/start")
    (row,) = client.get(f"/api/batches/{batch_id}").json()["documents"]
    assert (row["state"], row["out_of_scope"], row["allowed_out_of_scope"]) == ("queued", True, True)


def test_discarding_an_import_removes_its_files_and_parts():
    batch_id, done = import_file(pdf_bytes())
    up = new_upload(batch_id, pdf_bytes(), name="late.pdf")
    rt = main.runtime()
    doc_dir = rt.data.doc_dir(done["documents"][0]["id"])
    part = rt.data.part_path(up["upload_id"])
    assert doc_dir.is_dir() and part.exists()
    assert client.delete(f"/api/batches/{batch_id}").status_code == 200
    assert not doc_dir.exists() and not part.exists()
    assert client.get(f"/api/batches/{batch_id}").status_code == 404
    assert client.delete(f"/api/batches/{batch_id}").status_code == 200       # idempotent


def test_a_started_import_cannot_be_discarded():
    batch_id, _ = import_file(pdf_bytes())
    client.post(f"/api/batches/{batch_id}/start")
    assert client.delete(f"/api/batches/{batch_id}").status_code == 409


def test_a_drawing_in_a_draft_cannot_be_queued_one_by_one():
    _, done = import_file(pdf_bytes())
    assert client.post(f"/api/documents/{done['documents'][0]['id']}/enqueue").status_code == 409


def test_the_document_view_names_its_batch():
    batch_id, done = import_file(pdf_bytes())
    assert client.get(f"/api/documents/{done['documents'][0]['id']}").json()["batch_id"] == batch_id


def test_include_start_and_discard_reject_malformed_ids():
    assert client.post("/api/documents/nope/include", json={"included": True}).status_code == 404
    assert client.post("/api/batches/nope/start").status_code == 404
    assert client.delete("/api/batches/nope").status_code == 404


def test_start_and_discard_leave_no_marker_behind_refused_or_not():
    """Both mark the dirs whose rows they may remove, so a crash after the
    commit leaves those sweepable (Task 9) -- and must take the markers off
    again on every path that does not end in removing the dir."""
    rt = main.runtime()
    batch_id, done = import_file(pdf_bytes(pages=2))
    doc_id = done["documents"][0]["id"]
    d = rt.data.doc_dir(doc_id)
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 409     # nothing included
    assert not (d / ".importing").exists()
    client.post(f"/api/documents/{doc_id}/include", json={"included": True})
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 200
    assert d.is_dir() and not (d / ".importing").exists()
    assert client.delete(f"/api/batches/{batch_id}").status_code == 409         # started
    assert not (d / ".importing").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_api_import.py -q`
Expected: FAIL — `/include`, `/start` and `DELETE /api/batches/{id}` answer 404/405; `batch_id` missing from the document view.

- [ ] **Step 3: Implement**

`app/batch/repo.py` — add under `# ----- reads`:
```python
    def batch_document_ids(self, batch_id) -> list:
        """[] for an unknown batch: the callers mark files, they do not judge."""
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                "SELECT id FROM document WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()]
```

`app/batch/uploads.py` — change the comment above `IMPORTING_MARKER` to:
```python
# Marks a docs/<id>/ whose row an index transaction is creating (a
# completion) or may remove (Start's dropped rows, Discard). Only a marked
# dir with no row is ever swept at startup, and a marked dir WITH a row is
# only unmarked -- so a lost or replaced index can never make the sweep
# delete real drawings.
```
and add to `Uploads`, after `delete`:
```python
    # ----- start and discard: rows go first, files follow -------------------
    def _mark(self, doc_ids) -> list:
        """Mark each drawing dir BEFORE a transaction that may remove its
        row: a crash after the commit leaves a marked dir with no row, which
        the startup sweep removes; a crash before it leaves a marked dir
        with its row, which the sweep only unmarks."""
        marked = []
        for doc_id in doc_ids:
            d = self.data.doc_dir(doc_id)
            if d.is_dir():
                (d / IMPORTING_MARKER).write_bytes(b"")
                marked.append(d)
        return marked

    @staticmethod
    def _unmark(dirs) -> None:
        for d in dirs:
            (d / IMPORTING_MARKER).unlink(missing_ok=True)

    def start_batch(self, batch_id) -> dict:
        """Repo.start_batch, with the dropped drawings' files removed after
        the commit. Returns the repo's {queued, dropped}."""
        marked = self._mark(self.repo.batch_document_ids(batch_id))
        try:
            out = self.repo.start_batch(batch_id)
        except BaseException:
            self._unmark(marked)        # refused or failed: every row is still there
            raise
        dropped = set(out["dropped"])
        for doc_id in out["dropped"]:
            shutil.rmtree(self.data.doc_dir(doc_id), ignore_errors=True)
        self._unmark(d for d in marked if d.name not in dropped)
        return out

    def discard_batch(self, batch_id) -> None:
        """Repo.delete_batch, then the files: staged drawings' dirs and the
        part files of unfinished uploads. Idempotent for an unknown batch."""
        marked = self._mark(self.repo.batch_document_ids(batch_id))
        try:
            out = self.repo.delete_batch(batch_id)
        except BaseException:
            self._unmark(marked)
            raise
        if out is None:
            self._unmark(marked)
            return
        for uid in out["uploads"]:
            # Under the upload's lock: a chunk being written finishes first,
            # then finds its row gone and removes its part itself.
            with self._lock(uid):
                self.data.part_path(uid).unlink(missing_ok=True)
        for doc_id in out["documents"]:
            shutil.rmtree(self.data.doc_dir(doc_id), ignore_errors=True)
```

`app/main.py`:
1. In `_doc_view`, add `"batch_id": doc["batch_id"],` after `"file_name"`.
2. After `class UploadCreate`, add:
```python
class IncludeRequest(BaseModel):
    included: bool
```
3. After `delete_upload`, add:
```python
@app.post("/api/documents/{doc_id}/include")
def include_document(doc_id: str, req: IncludeRequest):
    """Tick or untick a staged drawing's Include box. Including an
    out-of-scope file is the explicit permission (decision 26) and is
    stored; an unreadable PDF is refused (409)."""
    _doc_dir(doc_id)
    return runtime().repo.set_included(doc_id, req.included)


@app.post("/api/batches/{batch_id}/start")
def start_batch(batch_id: str):
    """Queue the included drawings, drop the excluded ones with their files,
    end the draft (design §2). The only way a staged drawing is queued."""
    rt = runtime()
    out = rt.uploads.start_batch(_batch_id(batch_id))
    rt.worker.wake()
    log.info("batch %s started: %d queued, %d dropped",
             batch_id[:8], len(out["queued"]), len(out["dropped"]))
    return {"queued": len(out["queued"]), "dropped": len(out["dropped"]),
            "documents": out["queued"]}


@app.delete("/api/batches/{batch_id}")
def discard_batch(batch_id: str):
    """Discard an import that has not started: its staged drawings, its
    unfinished uploads, their files. Idempotent; a started batch is 409."""
    runtime().uploads.discard_batch(_batch_id(batch_id))
    return {"ok": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_api_import.py tests/test_api.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1631 passed, 2 skipped** (+12).
```bash
git add app/main.py app/batch/repo.py app/batch/uploads.py tests/test_api_import.py
```
```bash
git commit -m "api: include, start and discard an import" -m "Start queues the included drawings and removes the files of the dropped ones; it is the only way a staged drawing is queued, so the Include rules cannot be bypassed. Discarding a draft removes its staged files and unfinished part files. Both mark the dirs whose rows they may remove before the transaction, so a crash after the commit leaves them sweepable, and unmark on every other path. The document view now names its batch." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Recovery and stale cleanup

**Data-integrity: yes** (deletes files at startup).

Recovery runs only right after the single-process lock is taken (review C1). B1's `Worker.start()` takes the flock, requeues interrupted rows and starts the loop; on a `stop()` that timed out it KEEPS the thread and the lock, and a later `start()` refuses while that loop lives. Recovery therefore goes into the same place, as a `startup` hook called inside `start()`'s locked section: it runs exactly when this process has just become the data dir's only worker, never in a second process that failed to get the lock (whose sweep would see the first process's in-flight imports as crash leftovers), and never while an earlier loop of this process is still finishing a drawing.

**Files:**
- Modify: `app/batch/repo.py`, `app/batch/uploads.py`, `app/batch/worker.py`, `app/batch/runtime.py`, `app/main.py`
- Test: `tests/batch/test_uploads.py`, `tests/batch/test_runtime.py`, `tests/test_api_import.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/batch/test_uploads.py`:
```python
OLD = "2026-01-01T00:00:00.000000+00:00"


def test_recover_resets_an_interrupted_completion(h):
    uid = h.send(b"x" * 10)
    h.repo.begin_completion(uid)
    assert h.uploads.recover()["completions_reset"] == 1
    assert h.repo.get_upload(uid)["state"] == "receiving"


def test_the_startup_sweep_removes_only_marked_dirs_without_a_row(h):
    (row,) = h.imported(pdf_bytes())["documents"]
    kept = h.data.doc_dir(row["id"])
    (kept / IMPORTING_MARKER).write_bytes(b"")          # a crash after the commit
    orphan = h.data.doc_dir("a" * 32)
    orphan.mkdir()
    (orphan / IMPORTING_MARKER).write_bytes(b"")        # a crash before it
    unmarked = h.data.doc_dir("b" * 32)
    unmarked.mkdir()                                    # no row, no marker: never ours to judge
    assert h.uploads.sweep_unfinished_imports() == 1
    assert not orphan.exists()
    assert kept.is_dir() and not (kept / IMPORTING_MARKER).exists()
    assert unmarked.is_dir()


def test_week_old_unfinished_uploads_are_removed_and_fresh_ones_kept(h):
    old = h.create(b"x" * 10)["upload_id"]
    fresh = h.create(b"y" * 10)["upload_id"]
    h.repo._conn.execute("UPDATE upload SET updated_at = ? WHERE id = ?", (OLD, old))
    assert h.uploads.cleanup_stale()["stale_uploads"] == 1
    assert not h.data.part_path(old).exists() and h.data.part_path(fresh).exists()
    with pytest.raises(UnknownUpload):
        h.repo.get_upload(old)


def test_an_upload_resumed_after_it_was_found_stale_is_kept(h):
    """Listed as stale, then resumed before its lock was taken: the delete
    re-checks the age inside its transaction."""
    uid = h.create(b"x" * 10)["upload_id"]
    assert h.repo.delete_upload(uid, older_than=OLD) is None
    assert h.repo.get_upload(uid)["state"] == "receiving"


def test_part_files_no_unfinished_upload_owns_are_removed(h):
    live = h.create(b"x" * 10)["upload_id"]
    stray = h.data.part_path("c" * 32)
    stray.write_bytes(b"left over")
    done = h.imported(b"z" * 10)
    leftover = h.data.part_path(done["upload_id"])      # a crash after its commit left it
    leftover.write_bytes(b"z" * 10)
    assert h.uploads.cleanup_stale()["orphan_parts"] == 2
    assert h.data.part_path(live).exists() and not stray.exists() and not leftover.exists()


def test_week_old_empty_drafts_are_removed(h):
    old_empty = h.repo.create_batch("old")["id"]
    old_full = h.repo.create_batch("full")["id"]
    h.imported(pdf_bytes(), batch=old_full)
    h.repo._conn.execute("UPDATE batch SET created_at = ? WHERE id IN (?, ?)",
                         (OLD, old_empty, old_full))
    assert h.uploads.cleanup_stale()["empty_drafts"] == 1
    assert {b["id"] for b in h.repo.list_batches()} == {h.batch, old_full}


def test_a_draft_emptied_by_hand_goes_with_its_finished_upload_rows(h):
    """Every drawing removed, only the COMPLETE upload row left: that is an
    empty draft too. An unfinished upload still keeps it."""
    emptied = h.repo.create_batch("emptied")["id"]
    (row,) = h.imported(pdf_bytes(), batch=emptied)["documents"]
    h.repo.delete(row["id"])
    waiting = h.repo.create_batch("waiting")["id"]
    h.create(b"x" * 10, batch=waiting)
    h.repo._conn.execute("UPDATE batch SET created_at = ? WHERE id IN (?, ?)",
                         (OLD, emptied, waiting))
    assert h.uploads.cleanup_stale()["empty_drafts"] == 1
    assert {b["id"] for b in h.repo.list_batches()} == {h.batch, waiting}
    assert h.repo._conn.execute("SELECT COUNT(*) FROM upload WHERE batch_id = ?",
                                (emptied,)).fetchone()[0] == 0
```

Append to `tests/batch/test_runtime.py` (add `import fcntl` to its imports):
```python
def test_start_sweeps_unfinished_imports_before_the_worker_runs(tmp_path):
    rt = make(tmp_path)
    orphan = rt.data.doc_dir("a" * 32)
    orphan.mkdir()
    (orphan / ".importing").write_bytes(b"")
    try:
        rt.start()
        assert not orphan.exists()
    finally:
        rt.close()


def test_a_failed_recovery_does_not_keep_the_worker_from_starting(tmp_path, monkeypatch):
    """Recovery only removes leftovers; a fault in it must not hold up every
    queued drawing."""
    rt = make(tmp_path)

    def boom():
        raise OSError("disk")
    monkeypatch.setattr(rt.uploads, "recover", boom)
    try:
        rt.start()
        assert rt.worker.running
    finally:
        rt.close()


def test_recovery_runs_only_under_the_single_worker_lock(tmp_path):
    """A second process that cannot take the lock must not sweep: the first
    process's in-flight imports are marked exactly like crash leftovers, and
    sweeping them would lose a user's file mid-import."""
    rt = make(tmp_path)
    in_flight = rt.data.doc_dir("a" * 32)
    in_flight.mkdir()
    (in_flight / ".importing").write_bytes(b"")
    holder = open(rt.data.root / "worker.lock", "w")      # the other process
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RuntimeError, match="another process"):
            rt.start()
        assert (in_flight / ".importing").exists()
    finally:
        holder.close()
        rt.close()
```

Append to `tests/test_api_import.py`:
```python
class _Crash(Exception):
    """Stands for the process dying at that point."""


def test_a_crash_after_start_commits_leaves_only_sweepable_dirs(monkeypatch):
    """Start removes rows first, files second. Dying in between leaves the
    dropped drawing's dir with its marker and no row (swept at the next
    start) and the queued one's dir marked with its row (only unmarked)."""
    import shutil
    rt = main.runtime()
    batch_id = new_batch()["id"]
    _, keep = import_file(pdf_bytes(), batch_id=batch_id)
    _, drop = import_file(pdf_bytes(pages=2), name="b.pdf", batch_id=batch_id)
    keep_dir = rt.data.doc_dir(keep["documents"][0]["id"])
    drop_dir = rt.data.doc_dir(drop["documents"][0]["id"])
    real_rmtree = shutil.rmtree

    def crash(*a, **k):
        raise _Crash()
    monkeypatch.setattr(shutil, "rmtree", crash)
    with pytest.raises(_Crash):
        client.post(f"/api/batches/{batch_id}/start")
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    assert (drop_dir / ".importing").exists() and (keep_dir / ".importing").exists()
    assert rt.uploads.sweep_unfinished_imports() == 1
    assert not drop_dir.exists()
    assert keep_dir.is_dir() and not (keep_dir / ".importing").exists()
    assert client.get(f"/api/documents/{keep['documents'][0]['id']}").json()["state"] == "queued"


def test_a_new_import_sheds_week_old_unfinished_uploads():
    """A server that is never restarted still sheds them."""
    old_batch = new_batch()["id"]
    up = new_upload(old_batch, pdf_bytes())
    main.runtime().conn.execute("UPDATE upload SET updated_at = '2026-01-01T00:00:00.000000+00:00'")
    new_batch()
    assert client.get(f"/api/batches/{old_batch}").json()["uploads"] == []
    assert not main.runtime().data.part_path(up["upload_id"]).exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_uploads.py tests/batch/test_runtime.py tests/test_api_import.py -q`
Expected: FAIL — `AttributeError: 'Uploads' object has no attribute 'recover'` (and `delete_upload() got an unexpected keyword argument 'older_than'`).

- [ ] **Step 3: Implement**

`app/batch/repo.py`:
1. Replace `delete_upload` with:
```python
    def delete_upload(self, upload_id, older_than=None):
        """An unfinished upload removed by the user or gone stale; returns
        its row, or None if unknown (idempotent). A complete upload is
        refused: its drawings are removed one by one. With `older_than`
        (the stale sweep), an upload completed or touched since it was
        listed is left alone -- the age is re-checked in this transaction."""
        with self._tx() as c:
            row = c.execute("SELECT * FROM upload WHERE id = ?", (upload_id,)).fetchone()
            if row is None:
                return None
            if older_than is not None and (row["state"] == "complete"
                                           or row["updated_at"] >= older_than):
                return None
            if row["state"] == "complete":
                raise InvalidTransition("this upload is already imported; remove its drawings instead")
            c.execute("DELETE FROM upload WHERE id = ?", (upload_id,))
            return dict(row)
```
2. Add, under `# ----- reads`:
```python
    def stale_uploads(self, cutoff: str) -> list:
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                "SELECT id FROM upload WHERE state != 'complete' AND updated_at < ?"
                " ORDER BY updated_at", (cutoff,)).fetchall()]

    def open_upload_ids(self) -> set:
        with self._lock:
            return {r["id"] for r in self._conn.execute(
                "SELECT id FROM upload WHERE state != 'complete'").fetchall()}

    def document_ids(self) -> set:
        with self._lock:
            return {r["id"] for r in self._conn.execute("SELECT id FROM document").fetchall()}
```
3. Add, under `# ----- writes`:
```python
    def reset_completing(self) -> int:
        """At startup: a completion cut off by a crash. Its rows never
        committed (one transaction), so the upload is whole again and
        /complete may be retried."""
        with self._tx() as c:
            return c.execute("UPDATE upload SET state = 'receiving', updated_at = ?"
                             " WHERE state = 'completing'", (self._clock(),)).rowcount

    def delete_empty_drafts(self, cutoff: str) -> int:
        """Drafts created before `cutoff` that hold no drawing and no
        UNFINISHED upload -- with their complete upload rows, in the same
        transaction (a draft whose drawings were all removed still has
        those). A draft with staged drawings is kept however old: it may be
        continued later (design §2)."""
        with self._tx() as c:
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM batch WHERE draft = 1 AND created_at < ?"
                " AND NOT EXISTS (SELECT 1 FROM document d WHERE d.batch_id = batch.id)"
                " AND NOT EXISTS (SELECT 1 FROM upload u WHERE u.batch_id = batch.id"
                "                 AND u.state != 'complete')", (cutoff,)).fetchall()]
            for batch_id in ids:
                c.execute("DELETE FROM upload WHERE batch_id = ?", (batch_id,))
                c.execute("DELETE FROM batch WHERE id = ?", (batch_id,))
        return len(ids)
```

`app/batch/uploads.py`:
1. Add `from datetime import datetime, timedelta, timezone` and, after `IMPORTING_MARKER`:
```python
# Design §2: unfinished uploads older than this are removed. Empty drafts
# go after the same age.
STALE_AFTER = timedelta(days=7)
```
2. Add to `Uploads`, after `discard_batch`:
```python
    # ----- recovery and housekeeping --------------------------------------
    def recover(self) -> dict:
        """Only from Worker.start(), right after this process took the
        single-worker lock (Runtime wires it as the worker's `startup`
        hook), and so before any request is served. Never call it while
        requests run: an in-flight import looks exactly like a leftover."""
        out = {"completions_reset": self.repo.reset_completing(),
               "imports_swept": self.sweep_unfinished_imports()}
        out.update(self.cleanup_stale())
        if any(out.values()):
            log.info("startup: %s", ", ".join(f"{k} {v}" for k, v in out.items()))
        return out

    def sweep_unfinished_imports(self) -> int:
        """Remove docs/<id>/ dirs a completion created but never committed (a
        crash between unpacking and the index transaction): marker present
        AND no row. A marked dir that HAS a row only loses its marker (the
        crash came after the commit). A dir without a marker is never
        touched whatever the index says, so a lost or replaced index can
        never make this delete real drawings. Startup only: while requests
        run, an in-flight completion's dirs look exactly like these."""
        known = self.repo.document_ids()
        swept = 0
        for d in self.data.docs.iterdir():
            marker = d / IMPORTING_MARKER
            if not DOC_ID_RE.match(d.name) or not marker.exists():
                continue
            if d.name in known:
                marker.unlink(missing_ok=True)
            else:
                shutil.rmtree(d, ignore_errors=True)
                swept += 1
        return swept

    def cleanup_stale(self, now=None) -> dict:
        """Unfinished uploads untouched for STALE_AFTER (row and part), part
        files no unfinished upload owns, and drafts left empty as long. Safe
        while requests run: parts are listed BEFORE the owners are read, and
        a part file is created only after its row commits -- so a part that
        exists when listed has an owner the later read sees."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - STALE_AFTER).isoformat(timespec="microseconds")
        uploads = 0
        for uid in self.repo.stale_uploads(cutoff):
            with self._lock(uid):
                if self.repo.delete_upload(uid, older_than=cutoff) is not None:
                    self.data.part_path(uid).unlink(missing_ok=True)
                    uploads += 1
        found = list(self.data.uploads.glob("*.part"))
        live = self.repo.open_upload_ids()
        parts = 0
        for p in found:
            if p.stem not in live:
                p.unlink(missing_ok=True)
                parts += 1
        drafts = self.repo.delete_empty_drafts(cutoff)
        return {"stale_uploads": uploads, "orphan_parts": parts, "empty_drafts": drafts}
```

`app/batch/worker.py`:
1. Add a keyword to `Worker.__init__` — signature `def __init__(self, repo, data, store, extract_fn: Callable, header_fn: Callable, idle_wait: float = 2.0, startup: Optional[Callable] = None):` — and store it after `self.idle_wait = idle_wait`:
```python
        # Run inside start()'s locked section, right after the flock is
        # taken: the one moment this process is known to be the data dir's
        # only worker. Runtime passes import recovery (uploads.recover).
        self.startup = startup
```
2. In `start()`, the `try:` block that follows the flock begins with `moved = self.repo.requeue_interrupted()`. Insert before that line, inside the same `try:`:
```python
            if self.startup is not None:
                # Recovery only deletes leftovers of interrupted imports; a
                # fault in it must not keep the worker -- and every queued
                # drawing -- from starting. TYPE only (CLAUDE.md §1).
                try:
                    self.startup()
                except Exception as e:
                    log.warning("startup recovery failed: %s", type(e).__name__)
```
(The `except BaseException: f.close(); raise` around it stays as it is, so the lock is still released if anything else in that block fails.)

`app/batch/runtime.py` — change the line that builds the worker to:
```python
        # Recovery runs inside Worker.start(), under the single-process lock
        # (review C1): a second process that fails to get the lock never
        # sweeps the first one's in-flight imports. A lambda, so the bound
        # method is looked up at start time.
        self.worker = Worker(self.repo, self.data, self.store, extract_fn, header_fn,
                             startup=lambda: self.uploads.recover())
```
(`self.uploads` is created before this line in Task 6, right after `self.repo`. `Runtime.start()` stays `self.worker.start()`.)

`app/main.py` — in `create_batch`, after `rt = runtime()`:
```python
    try:
        rt.uploads.cleanup_stale()
    except Exception as e:
        # Housekeeping: a fault in it must not refuse a new import.
        log.warning("stale upload cleanup failed: %s", type(e).__name__)
```
and add to its docstring: "Also sheds week-old unfinished uploads, so a server that is never restarted still does."

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch tests/test_api_import.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1643 passed, 2 skipped** (+12).
```bash
git add app/batch/repo.py app/batch/uploads.py app/batch/worker.py app/batch/runtime.py app/main.py tests/batch/test_uploads.py tests/batch/test_runtime.py tests/test_api_import.py
```
```bash
git commit -m "batch: recover interrupted imports and shed stale uploads" -m "Recovery runs inside Worker.start right after the single-process lock is taken, so a second process never sweeps the first one's in-flight imports. An interrupted completion is reset, and only drawing dirs marked .importing with no row are removed, so a lost index can never make the sweep delete real drawings. Unfinished uploads untouched for 7 days, stray part files and week-old empty drafts are removed at startup and on every new import. A recovery fault never keeps the worker from starting." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

> **Milestone: staging-api** - state must be checkpointed here before continuing.

---

### Task 10: The chunked upload client and the staging badges

**Data-integrity: yes** (the client half of the offset protocol).

**Files:**
- Create: `app/static/js/upload.js`, `app/static/js/badges.js`, `tests/js/upload.test.mjs`, `tests/js/badges.test.mjs`
- Modify: `app/static/js/api.js`

- [ ] **Step 1: Write the failing tests**

`tests/js/upload.test.mjs`:
```js
// The chunked upload client (app/static/js/upload.js) against an in-memory
// server that keeps the protocol of app/batch/uploads.py, with lost
// replies, dropped requests and server errors injected (batch design §5).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

const {
  uploadFile, importFiles, fingerprint, sha256Hex, relPathOf, batchNameFor, UploadError,
  RETRY_BUDGET_MS, MAX_WAIT_MS, recallDraft, rememberDraft, forgetDraft,
} = await import('../../app/static/js/upload.js');

const bytes = (n, seed = 7) => Uint8Array.from({ length: n }, (_, i) => (i * seed + 3) % 251);
function fileOf(data, name = 'a.pdf', relative = '') {
  const f = new File([data], name, { lastModified: 1 });
  if (relative) Object.defineProperty(f, 'webkitRelativePath', { value: relative });
  return f;
}
const noSleep = async () => {};
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

// The server's rules: a chunk only at the offset it holds (else 409 + that
// offset); complete only with every byte; the same unfinished file resumes.
function fakeServer({ fault = () => null, onComplete = null } = {}) {
  const uploads = new Map();
  const calls = [];
  let n = 0;
  let written = 0;
  async function fetch(url, { method, body } = {}) {
    const u = new URL(url, 'http://sindri');
    calls.push({ method, path: u.pathname, offset: u.searchParams.get('offset') });
    if (method === 'POST' && u.pathname === '/api/uploads') {
      const req = JSON.parse(body);
      if (!/\.(pdf|zip)$/i.test(req.name)) {
        return json(400, { detail: 'only PDF and ZIP files can be imported' });
      }
      for (const [id, up] of uploads) {
        if (up.state === 'receiving' && up.batch_id === req.batch_id && up.name === req.name
            && up.fingerprint === req.fingerprint && up.rel_path === req.rel_path
            && up.size === req.size
            && (req.last_modified == null || up.last_modified === req.last_modified)) {
          return json(200, { upload_id: id, received: up.received, state: up.state, resumed: true });
        }
      }
      const id = `u${++n}`;
      uploads.set(id, { ...req, received: 0, state: 'receiving', bytes: new Uint8Array(req.size) });
      return json(200, { upload_id: id, received: 0, state: 'receiving', resumed: false });
    }
    let m = /^\/api\/uploads\/(\w+)$/.exec(u.pathname);
    if (method === 'PUT' && m) {
      const up = uploads.get(m[1]);
      const offset = Number(u.searchParams.get('offset'));
      const f = fault({ offset, puts: calls.filter((c) => c.method === 'PUT').length });
      if (f === 'drop-before') throw new TypeError('network down');
      if (typeof f === 'number') return json(f, { detail: 'injected' });
      if (up.state !== 'receiving' || offset !== up.received) {
        return json(409, { detail: 'offset does not match what the server holds',
                           received: up.received, state: up.state });
      }
      const data = new Uint8Array(await body.arrayBuffer());
      up.bytes.set(data, offset);
      up.received += data.length;
      written += data.length;
      if (f === 'drop-after') throw new TypeError('reply lost');
      return json(200, { received: up.received });
    }
    m = /^\/api\/uploads\/(\w+)\/complete$/.exec(u.pathname);
    if (method === 'POST' && m) {
      const up = uploads.get(m[1]);
      const forced = onComplete && onComplete(up);
      if (forced) return forced;
      if (up.received !== up.size) {
        return json(409, { detail: 'the upload is not complete', received: up.received, state: up.state });
      }
      up.state = 'complete';
      return json(200, { upload_id: m[1], state: 'complete',
                         documents: [{ id: `d-${m[1]}`, file_name: up.name }], ignored: [] });
    }
    return json(404, { detail: 'no such route' });
  }
  return { fetch, uploads, calls, get written() { return written; } };
}

const puts = (server) => server.calls.filter((c) => c.method === 'PUT').map((c) => Number(c.offset));

test('sends the file in chunks at the server offset and completes', async () => {
  const data = bytes(300);
  const server = fakeServer();
  const seen = [];
  const done = await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch,
    sleep: noSleep, chunkSize: 64, onProgress: (sent, total) => seen.push([sent, total]) });
  assert.equal(done.state, 'complete');
  assert.equal(done.documents[0].file_name, 'a.pdf');
  assert.deepEqual(puts(server), [0, 64, 128, 192, 256]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
  assert.deepEqual(seen.at(-1), [300, 300]);
});

test('a lost reply is resynced from the 409, so no byte lands twice', async () => {
  const data = bytes(300);
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? 'drop-after' : null) });
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 64, 64, 128, 192, 256]);
  assert.equal(server.written, 300);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a request that never arrived is sent again at the same offset', async () => {
  const data = bytes(300);
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? 'drop-before' : null) });
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 64, 64, 128, 192, 256]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('server errors are retried with growing waits', async () => {
  const waits = [];
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 || k === 3 ? 500 : null) });
  await uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, chunkSize: 64,
    sleep: async (ms) => { waits.push(ms); } });
  assert.deepEqual(waits, [500, 1000]);
  assert.equal(server.uploads.get('u1').state, 'complete');
});

test('a full disk stops at once, without retrying', async () => {
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? 507 : null) });
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 }),
    (err) => err instanceof UploadError && err.status === 507 && err.fatal);
  assert.deepEqual(puts(server), [0, 64]);
});

test("a refused request reports the server's sentence and is not retried", async () => {
  const server = fakeServer();
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(10), 'notes.exe'), batchId: 'b', fetch: server.fetch, sleep: noSleep }),
    (err) => err.fatal && err.status === 400 && err.message === 'only PDF and ZIP files can be imported');
  assert.equal(server.calls.length, 1);
});

test('it keeps retrying for the time budget, then gives up -- but not for good', async () => {
  // A fake clock that the waits advance: the budget is time, not a count,
  // so a server restart taking minutes is ridden out.
  let clock = 0;
  const waits = [];
  const sleep = async (ms) => { waits.push(ms); clock += ms; };
  let down = true;
  const server = fakeServer({ fault: ({ puts: k }) => (down && k >= 2 ? 'drop-before' : null) });
  const file = fileOf(bytes(200));
  await assert.rejects(
    uploadFile({ file, batchId: 'b', fetch: server.fetch, sleep, now: () => clock, chunkSize: 64 }),
    (err) => err instanceof UploadError && !err.fatal);
  assert.ok(clock >= RETRY_BUDGET_MS && clock < RETRY_BUDGET_MS + MAX_WAIT_MS, `${clock}`);
  assert.equal(Math.max(...waits), MAX_WAIT_MS);
  down = false;
  const before = puts(server).length;
  await uploadFile({ file, batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.equal(puts(server)[before], 64);           // resumed, not restarted
  assert.deepEqual(server.uploads.get('u1').bytes, bytes(200));
});

test('a reply whose body cannot be read is treated as lost, and resynced', async () => {
  const data = bytes(200);
  const server = fakeServer();
  let mangled = false;
  const fetch = async (url, init) => {
    const res = await server.fetch(url, init);
    if (init.method === 'PUT' && !mangled) {
      mangled = true;                                  // applied, but the reply is garbage
      return { ok: true, status: 200, json: async () => { throw new SyntaxError('truncated'); } };
    }
    return res;
  };
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 0, 64, 128, 192]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a closed tab resumes: the same file finds its unfinished upload and sends only the rest', async () => {
  const data = bytes(300);
  const file = fileOf(data, 'a.pdf', 'Kunde/a.pdf');
  const server = fakeServer();
  const held = new Uint8Array(300);
  held.set(data.slice(0, 128));
  server.uploads.set('u9', { batch_id: 'b', name: 'a.pdf', size: 300, rel_path: 'Kunde',
    last_modified: 1, fingerprint: await fingerprint(file), received: 128, state: 'receiving',
    bytes: held });
  await uploadFile({ file, batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [128, 192, 256]);
  assert.deepEqual(server.uploads.get('u9').bytes, data);
});

test('a completion refused for missing bytes sends them and completes', async () => {
  const data = bytes(200);
  let lost = false;
  const server = fakeServer({ onComplete: (up) => {
    if (lost) return null;
    lost = true;
    up.received = 64;                    // the server's part file was cut short
    return json(409, { detail: 'the server holds less of this file than it counted',
                       received: 64, state: 'receiving' });
  } });
  const done = await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch,
    sleep: noSleep, chunkSize: 64 });
  assert.equal(done.state, 'complete');
  assert.deepEqual(puts(server), [0, 64, 128, 192, 64, 128, 192]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('cancel stops before the next request', async () => {
  const ctl = new AbortController();
  const server = fakeServer();
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(300)), batchId: 'b', fetch: server.fetch, sleep: noSleep,
      chunkSize: 64, signal: ctl.signal, onProgress: (sent) => { if (sent >= 64) ctl.abort(); } }),
    (err) => err.fatal && err.message === 'upload cancelled');
  assert.deepEqual(puts(server), [0]);
});

test('the fingerprint is SHA-256 over the size, the first and the last MiB', async () => {
  const MiB = 1024 * 1024;
  const data = bytes(3 * MiB);
  const expected = createHash('sha256').update(Buffer.from(`${data.length}:`))
    .update(data.subarray(0, MiB)).update(data.subarray(2 * MiB)).digest('hex');
  assert.equal(await fingerprint(fileOf(data)), expected);
});

test('without crypto.subtle (no secure context) the fingerprint is the same, in plain JS', async () => {
  // Hosting is undecided (decision 23); browsers hide crypto.subtle outside
  // https and localhost.
  const enc = new TextEncoder();
  assert.equal(sha256Hex(enc.encode('abc')),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(sha256Hex(new Uint8Array(0)),
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  for (const n of [55, 56, 64, 1000, 3 * 1024 * 1024]) {
    const file = fileOf(bytes(n));
    // null, not undefined: undefined would select the default (subtle) again.
    assert.equal(await fingerprint(file, null), await fingerprint(file, globalThis.crypto.subtle), `${n}`);
  }
});

test('the draft of an interrupted file is remembered per file, and storage faults are harmless', () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v),
                    removeItem: (k) => store.delete(k) };
  const a = fileOf(bytes(10), 'a.pdf');
  const b = fileOf(bytes(11), 'a.pdf');          // same name, different size: another file
  rememberDraft(storage, a, 'batch-1');
  assert.equal(recallDraft(storage, a), 'batch-1');
  assert.equal(recallDraft(storage, b), null);
  forgetDraft(storage, a);
  assert.equal(recallDraft(storage, a), null);
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('full'); },
                   removeItem() { throw new Error('denied'); } };
  rememberDraft(broken, a, 'batch-1');
  forgetDraft(broken, a);
  assert.equal(recallDraft(broken, a), null);
  assert.equal(recallDraft(undefined, a), null);
});

test('the fingerprint is a key for resuming, not a content hash', async () => {
  // It tells apart files that differ at either end, and deliberately not
  // ones that differ only in the middle: duplicates are found by the
  // server's full-file SHA-256, never by this.
  const MiB = 1024 * 1024;
  const a = bytes(3 * MiB);
  const end = a.slice();
  end[end.length - 1] ^= 1;
  const middle = a.slice();
  middle[Math.floor(1.5 * MiB)] ^= 1;
  assert.notEqual(await fingerprint(fileOf(a)), await fingerprint(fileOf(end)));
  assert.equal(await fingerprint(fileOf(a)), await fingerprint(fileOf(middle)));
});

test('the folder of a folder selection, and the batch name of decision 27', () => {
  assert.equal(relPathOf(fileOf(bytes(1), 'a.pdf', 'Kunde/Teile/a.pdf')), 'Kunde/Teile');
  assert.equal(relPathOf(fileOf(bytes(1))), '');
  assert.equal(batchNameFor([fileOf(bytes(1), 'Lieferung 12.ZIP')]), 'Lieferung 12');
  assert.equal(batchNameFor([fileOf(bytes(1), 'a.pdf', 'Kunde/a.pdf'),
                             fileOf(bytes(1), 'b.pdf', 'Kunde/x/b.pdf')]), 'Kunde');
  const at = new Date(2026, 9, 7, 9, 5);
  assert.equal(batchNameFor([fileOf(bytes(1))], at), 'Import 2026-10-07 09:05');
  assert.equal(batchNameFor([fileOf(bytes(1), 'a.zip'), fileOf(bytes(1))], at), 'Import 2026-10-07 09:05');
});

test('importFiles runs at most three uploads at once and one failure stops none of the others', async () => {
  const server = fakeServer();
  let active = 0;
  let peak = 0;
  const fetch = async (url, init) => {
    const u = new URL(url, 'http://sindri');
    const starting = init.method === 'POST' && u.pathname === '/api/uploads';
    if (starting) { active += 1; peak = Math.max(peak, active); }
    await new Promise((r) => setImmediate(r));       // let the other lanes run
    const res = await server.fetch(url, init);
    if ((starting && !res.ok) || (u.pathname.endsWith('/complete') && res.ok)) active -= 1;
    return res;
  };
  const files = [0, 1, 2, 3, 4, 5].map((i) => fileOf(bytes(100, i + 2), i === 2 ? 'bad.exe' : `f${i}.pdf`));
  const results = await importFiles(files, { batchId: 'b', fetch, sleep: noSleep, chunkSize: 32 });
  assert.deepEqual(results.map((r) => r.ok), [true, true, false, true, true, true]);
  assert.equal(results[2].error.status, 400);
  assert.ok(peak >= 2 && peak <= 3, `peak ${peak}`);
});
```

`tests/js/badges.test.mjs`:
```js
// Staging badge texts (batch design §2) from a staging row
// (app/batch/staging.py staging_row).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { badgesFor } = await import('../../app/static/js/badges.js');

const row = (kw = {}) => ({ pages: 1, oversized: false, unreadable: false, duplicate_of: null, ...kw });

test('a normal drawing carries no badge', () => {
  assert.deepEqual(badgesFor(row()), []);
});

test("multi-page and oversized carry the design's warnings", () => {
  assert.deepEqual(badgesFor(row({ pages: 3, oversized: true })).map((b) => b.text), [
    'multi-page: only sheet 1 is read; extraction quality may be poor',
    'oversized: read at reduced resolution; extraction quality may be poor',
  ]);
});

test('a duplicate names the original, its batch and its state', () => {
  const dup = { id: 'x', file_name: 'Flansch.pdf', batch_id: 'b', batch_name: 'Januar', state: 'in_review' };
  assert.deepEqual(badgesFor(row({ duplicate_of: dup })), [
    { kind: 'duplicate', text: 'duplicate of "Flansch.pdf" (batch Januar, in review)' }]);
});

test('an unreadable PDF says only that', () => {
  assert.deepEqual(badgesFor(row({ unreadable: true, pages: null, oversized: null })),
    [{ kind: 'unreadable', text: 'unreadable PDF' }]);
});

test('a sheet never measured (staged before B2) is not called oversized', () => {
  assert.deepEqual(badgesFor(row({ oversized: null })), []);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/js/upload.test.mjs tests/js/badges.test.mjs`
Expected: FAIL — `Cannot find module '.../app/static/js/upload.js'`.

- [ ] **Step 3: Implement**

`app/static/js/upload.js`:
```js
// Chunked, resumable upload client (batch design §2, decision 21).
//
// Pure on purpose: every request goes through an injected `fetch` and every
// wait through an injected `sleep`, so node drives it against an in-memory
// server with lost replies, dropped requests and server errors injected
// (tests/js/upload.test.mjs). The SERVER is the authority on the offset: a
// 409 carries the bytes it really holds, and the client continues from
// there -- never from what it believes it sent.

export const CHUNK_SIZE = 8 * 1024 * 1024;     // design §2: 8 MiB per PUT (server bound: 64 MiB)
export const FINGERPRINT_SPAN = 1024 * 1024;   // first and last MiB
export const PARALLEL = 3;                     // design §2: three uploads at once
// How long one request keeps retrying a dropped connection or a 5xx before
// giving up FOR NOW (non-fatal: the same file selected again resumes). Time,
// not a count: a server restart or a Wi-Fi hand-over takes minutes, and a
// count of quick retries would give up long before either is over.
export const RETRY_BUDGET_MS = 10 * 60 * 1000;
export const MAX_WAIT_MS = 20 * 1000;          // the longest single wait between retries

export class UploadError extends Error {
  // `fatal`: retrying cannot help (refused, disk full, cancelled). Not
  // fatal: the network or server is down for now, and the same file
  // selected again resumes where the server stopped.
  constructor(message, { status = 0, fatal = false } = {}) {
    super(message);
    this.name = 'UploadError';
    this.status = status;
    this.fatal = fatal;
  }
}

// SHA-256 in plain JS, for when crypto.subtle does not exist: browsers
// offer it only in a secure context (https or localhost), and how Sindri is
// hosted is undecided (decision 23). Only the fingerprint uses it -- at most
// 2 MiB, a resume key, never a duplicate check -- so speed does not matter.
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotr = (x, n) => (x >>> n) | (x << (32 - n));

export function sha256Hex(bytes) {
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  const bits = bytes.length * 8;
  view.setUint32(padded.length - 8, Math.floor(bits / 2 ** 32));
  view.setUint32(padded.length - 4, bits >>> 0);
  const w = new Uint32Array(64);
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + 4 * i);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;            // Uint32Array wraps mod 2^32
    }
    let [a, b, c, d, e, f, g, k] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (k + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      k = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += k;
  }
  return Array.from(h, (x) => x.toString(16).padStart(8, '0')).join('');
}

// SHA-256 over the size and the first and last MiB. A browser cannot hash a
// multi-GB file incrementally, and this is only the KEY that finds an
// unfinished upload of the same file again; duplicates are found by the
// server's full-file SHA-256, which never trusts this.
export async function fingerprint(file, subtle = globalThis.crypto?.subtle) {
  const head = new Uint8Array(await file.slice(0, Math.min(file.size, FINGERPRINT_SPAN)).arrayBuffer());
  const tail = new Uint8Array(await file.slice(Math.max(0, file.size - FINGERPRINT_SPAN), file.size).arrayBuffer());
  const size = new TextEncoder().encode(`${file.size}:`);
  const all = new Uint8Array(size.length + head.length + tail.length);
  all.set(size, 0);
  all.set(head, size.length);
  all.set(tail, size.length + head.length);
  if (!subtle) return sha256Hex(all);
  const digest = new Uint8Array(await subtle.digest('SHA-256', all));
  return Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
}

// Remembering which draft an interrupted file was going into, so selecting
// the same file again -- after a failure, a reload or a closed tab -- resumes
// it there (decision 21; the server only resumes within the same draft).
// `storage` is localStorage in the browser, injected so node can test it;
// every access is guarded, since storage can be full or disabled.
const draftKey = (file) => `sindri.import:${file.name}:${file.size}:${file.lastModified}`;

export function recallDraft(storage, file) {
  try { return storage?.getItem(draftKey(file)) || null; } catch { return null; }
}

export function rememberDraft(storage, file, batchId) {
  try { storage?.setItem(draftKey(file), batchId); } catch { /* resume is a convenience */ }
}

export function forgetDraft(storage, file) {
  try { storage?.removeItem(draftKey(file)); } catch { /* nothing to undo */ }
}

// A wait that ends early on cancel, so Cancel never sits out a 20 s backoff.
function abortableSleep(ms, signal) {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
  });
}

// The folder a file came from in a folder selection (webkitdirectory), ''
// for a plain file. The server judges it again (app/batch/paths.py).
export function relPathOf(file) {
  const p = file.webkitRelativePath || '';
  const i = p.lastIndexOf('/');
  return i > 0 ? p.slice(0, i) : '';
}

// Decision 27: a single ZIP's name, a single folder's name, else the date
// and time. Editable afterwards (PATCH /api/batches/{id}).
export function batchNameFor(files, now = new Date()) {
  if (files.length === 1 && /\.zip$/i.test(files[0].name)) return files[0].name.replace(/\.zip$/i, '');
  const tops = new Set(files.map((f) => (f.webkitRelativePath || '').split('/')[0]));
  if (files.length && tops.size === 1 && !tops.has('')) return [...tops][0];
  const pad = (n) => String(n).padStart(2, '0');
  return `Import ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} `
    + `${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

// One request, retried on a dropped connection, a reply that cannot be
// read, or a 5xx -- with growing waits, for up to RETRY_BUDGET_MS. A 409
// that says where the server stands is an ANSWER, not an error: it is
// returned for the caller to resync from.
async function request({ send, sleep, now, budgetMs, signal }, method, url, body, headers) {
  let failures = 0;
  let firstFailure = null;
  for (;;) {
    if (signal?.aborted) throw new UploadError('upload cancelled', { fatal: true });
    let res = null;
    try {
      res = await send(url, { method, body, headers, signal });
    } catch {
      if (signal?.aborted) throw new UploadError('upload cancelled', { fatal: true });
    }
    if (res) {
      if (res.ok) {
        // A body cut off mid-reply is a lost reply, not an answer: retry,
        // and the server's 409 / stored completion says what happened.
        try { return await res.json(); } catch { res = null; }
      } else {
        const detail = await res.json().catch(() => null);
        if (res.status === 409 && detail && Number.isInteger(detail.received)) return detail;
        if (res.status === 507) {
          throw new UploadError('not enough free disk space on the server', { status: 507, fatal: true });
        }
        if (res.status < 500) {
          const msg = typeof detail?.detail === 'string' ? detail.detail : `upload refused (${res.status})`;
          throw new UploadError(msg, { status: res.status, fatal: true });
        }
      }
    }
    failures += 1;
    if (firstFailure === null) firstFailure = now();
    if (now() - firstFailure >= budgetMs) {
      throw new UploadError(res ? `server error (${res.status})` : 'the server could not be reached',
        { status: res ? res.status : 0 });
    }
    await sleep(Math.min(MAX_WAIT_MS, 500 * 2 ** (failures - 1)), signal);
  }
}

// Upload one file into a draft batch; resolves the completion
// ({ upload_id, state: 'complete', documents, ignored }).
export async function uploadFile({
  file, batchId, relPath = relPathOf(file),
  fetch: send = (...a) => globalThis.fetch(...a),
  sleep = abortableSleep, now = () => Date.now(), budgetMs = RETRY_BUDGET_MS,
  chunkSize = CHUNK_SIZE, onProgress = () => {}, signal,
} = {}) {
  const call = (method, url, body, headers) =>
    request({ send, sleep, now, budgetMs, signal }, method, url, body, headers);
  const created = await call('POST', '/api/uploads', JSON.stringify({
    batch_id: batchId, name: file.name, size: file.size,
    last_modified: Number.isFinite(file.lastModified) ? file.lastModified : null,
    rel_path: relPath, fingerprint: await fingerprint(file),
  }), { 'Content-Type': 'application/json' });
  const id = created.upload_id;
  let received = created.received;
  onProgress(received, file.size);
  // A completion refused because the server holds less than it counted
  // sends the rest and tries again -- but not forever: a server that keeps
  // losing data is a fault to report.
  for (let resyncs = 0; resyncs <= 3; resyncs++) {
    while (received < file.size) {
      const end = Math.min(file.size, received + chunkSize);
      const r = await call('PUT', `/api/uploads/${id}?offset=${received}`,
        file.slice(received, end), { 'Content-Type': 'application/octet-stream' });
      // 200 and 409 both say where the server now stands. After a lost
      // reply the retry is a 409 whose `received` already counts the
      // chunk, so it is not sent again.
      received = r.received;
      onProgress(received, file.size);
    }
    const done = await call('POST', `/api/uploads/${id}/complete`);
    if (done.state === 'complete') return done;
    received = done.received;
  }
  throw new UploadError('the server keeps losing this upload', { fatal: true });
}

// Upload many files, PARALLEL at a time. One file failing never stops the
// others; each result is { ok: true, ...completion } or { ok: false, error }.
export async function importFiles(files, { parallel = PARALLEL, onFile = () => {}, ...opts } = {}) {
  const results = new Array(files.length);
  let next = 0;
  async function lane() {
    while (next < files.length) {
      const i = next++;
      try {
        const done = await uploadFile({
          ...opts, file: files[i],
          onProgress: (sent, total) => onFile(i, { sent, total }),
        });
        results[i] = { ok: true, ...done };
      } catch (error) {
        results[i] = { ok: false, error };
      }
      onFile(i, { done: true, result: results[i] });
    }
  }
  await Promise.all(Array.from({ length: Math.min(parallel, files.length) }, lane));
  return results;
}
```

`app/static/js/badges.js`:
```js
// Staging badges (batch design §2): the warning each staged row carries.
// One function, so the interim confirm screen and B3's staging view say the
// same words for the same row. The facts come from the server
// (app/batch/staging.py staging_row); this only turns them into copy.

export function badgesFor(row) {
  if (row.unreadable) {
    // Nothing else is known about a file that cannot be opened, and it can
    // never be included -- this is the only thing worth saying.
    return [{ kind: 'unreadable', text: 'unreadable PDF' }];
  }
  const out = [];
  if ((row.pages || 0) > 1) {
    out.push({ kind: 'multi_page', text: 'multi-page: only sheet 1 is read; extraction quality may be poor' });
  }
  if (row.oversized) {
    out.push({ kind: 'oversized', text: 'oversized: read at reduced resolution; extraction quality may be poor' });
  }
  if (row.duplicate_of) {
    const d = row.duplicate_of;
    out.push({ kind: 'duplicate',
               text: `duplicate of "${d.file_name}" (batch ${d.batch_name}, ${String(d.state).replace('_', ' ')})` });
  }
  return out;
}
```

`app/static/js/api.js` — after the `openDocument` export, add:
```js
// ----- Imports (batch design §2). The chunked upload itself is upload.js.
const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const createBatch = async (name) =>
  jsonOrThrow(await fetch('/api/batches', { method: 'POST', headers: JSON_HEADERS,
                                            body: JSON.stringify({ name }) }), 'Could not start an import');
export const getBatch = async (id) => jsonOrThrow(await fetch(`/api/batches/${id}`), 'Import not found');
export const startBatch = async (id) =>
  jsonOrThrow(await fetch(`/api/batches/${id}/start`, { method: 'POST' }), 'Could not start the import');
export const setIncluded = async (id, included) =>
  jsonOrThrow(await fetch(docUrl(id, '/include'), { method: 'POST', headers: JSON_HEADERS,
                                                    body: JSON.stringify({ included }) }),
              'Could not change the selection');

// Best effort, like deleteDocument: never throws; resolves { ok, status }.
export async function discardBatch(id) {
  if (!id) return { ok: false, status: 0 };
  try {
    const res = await fetch(`/api/batches/${id}`, { method: 'DELETE' });
    return { ok: res.ok, status: res.status };
  } catch {
    return { ok: false, status: 0 };
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/js/upload.test.mjs tests/js/badges.test.mjs`
Expected: 22 pass (17 upload, 5 badges).
Run: `python -m pytest tests/test_js_state.py -q`
Expected: 1 passed (every node test file, including the two new ones).

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → **1643 passed, 2 skipped** (node tests run inside the one `test_ui_state_rules`).
```bash
git add app/static/js/upload.js app/static/js/badges.js app/static/js/api.js tests/js/upload.test.mjs tests/js/badges.test.mjs
```
```bash
git commit -m "ui: chunked resumable upload client and staging badges" -m "upload.js speaks the offset protocol through an injected fetch: the server's 409 offset is authoritative, so a lost or unreadable reply never sends a chunk twice; drops and 5xx are retried with growing waits for a ten-minute budget, a full disk or a refusal stops at once, and the same file selected again resumes (its draft is remembered per file). Without crypto.subtle (no secure context) the fingerprint falls back to a plain-JS SHA-256. importFiles runs three lanes and one failure stops none of the others. badges.js turns a staging row into the design's warning texts." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Interim UI through the protocol (old routes still there)

**Data-integrity: yes** (the client flow that decides when a draft is kept for resuming and when it is discarded).

The interim screen moves onto the protocol in this task, and the old routes stay until Task 12, so the suite and the UI are green at each step. There is no new pytest here: the decisions this flow relies on are node-tested in Task 10 (`uploadFile`, the retry budget, `recallDraft` / `rememberDraft` / `forgetDraft`, `badgesFor`), and the drive in Task 13 checks the wiring.

**Files:**
- Modify: `app/static/js/main.js`, `app/static/js/api.js`, `app/static/index.html`, `app/static/styles/components.css`

- [ ] **Step 1: Implement**

`app/static/js/api.js`: delete `savePdf` (and its comment) and `enqueueDocument`. Nothing calls them after this task (the B1 state file lists `main.js` as their only caller); the server routes go in Task 12.

`app/static/index.html` — after `<div class="hint" id="cf-pages">—</div>` add:
```html
            <div class="hint cf-warn" id="cf-warn" hidden></div>
```

`app/static/styles/components.css` — after the `#plan-confirm .hint { … }` rule add:
```css
/* Staging warnings on the interim confirm screen (badges.js): multi-page,
   oversized, duplicate. Pressing "Include and start" with them showing is
   the permission. */
#plan-confirm .cf-warn {
  color: var(--warn);
  margin-top: 8px;
}
```

`app/static/js/main.js`:
1. Replace the `./api.js` import with:
```js
import { getDocument, cancelDocument, openDocument, deleteDocument, downloadExport,
         health, postEvents, beaconEvents, sealSession, createBatch, getBatch,
         startBatch, discardBatch, setIncluded } from './api.js';
import { uploadFile, batchNameFor, UploadError, recallDraft, rememberDraft,
         forgetDraft } from './upload.js';
import { badgesFor } from './badges.js';
```
2. Change `let waitingOn = null;     // { id, fileName, pages }` to
```js
let waitingOn = null;     // { id, batchId, fileName, pages, row } -- row: its staging row
let uploading = false;    // a file is on its way to the server (chunked, batch design §2)
```
3. In `routeDocument`, replace its first two lines with:
```js
  waitingOn = { id: doc.id, batchId: doc.batch_id, fileName: doc.file_name, pages: doc.pages };
  if (doc.state === 'staged') await showStaged(doc);
```
and add after `routeDocument`:
```js
// A staged drawing reached from its link: the confirm screen needs its
// staging row (badges, Include) from the import it sits in. On failure the
// tab must not keep waiting on it, or no upload would ever be accepted.
async function showStaged(doc) {
  let row;
  try {
    row = (await getBatch(doc.batch_id)).documents.find((r) => r.id === doc.id);
  } catch (err) {
    waitingOn = null;
    throw err;
  }
  if (!row) {
    waitingOn = null;
    throw Object.assign(new Error('Drawing not found'), { status: 404 });
  }
  waitingOn.row = row;
  showConfirm(row);
}
```
4. In `handleFile`, insert after the `if (waitingOn) { … return; }` block:
```js
  if (uploading) {
    toast({ kind: 'warn', title: 'A drawing is uploading', msg: 'Try again once it has arrived.' });
    return;
  }
```
and replace everything from `setBusy(\`Opening ${file.name}…\`);` to the end of the function with:
```js
  uploading = true;
  setBusy(`Uploading ${file.name}…`);
  const storage = (() => { try { return window.localStorage; } catch { return null; } })();
  const onProgress = (sent, total) =>
    setBusy(`Uploading ${file.name}… ${total ? Math.floor((100 * sent) / total) : 100}%`);
  // One drawing = a batch of one (decision 17), through the same chunked,
  // resumable protocol a folder or a ZIP uses (batch design §2). The draft
  // is remembered for this file BEFORE the first byte, so selecting the
  // same file again -- after a failure, a reload or a closed tab -- resumes
  // into the same draft at the last chunk (decision 21). localStorage, not
  // sessionStorage: a closed tab is one of the cases decision 21 names.
  const remembered = recallDraft(storage, file);
  let batchId = remembered;
  try {
    let done;
    try {
      if (!batchId) batchId = (await createBatch(batchNameFor([file]))).id;
      rememberDraft(storage, file, batchId);
      done = await uploadFile({ file, batchId, onProgress });
    } catch (err) {
      // A remembered draft that is gone (discarded, or swept after a week)
      // or already started is refused: start over in a fresh draft, once.
      const stale = remembered && batchId === remembered && err instanceof UploadError
        && err.fatal && (err.status === 404 || err.status === 409);
      if (!stale) throw err;
      forgetDraft(storage, file);
      batchId = (await createBatch(batchNameFor([file]))).id;
      rememberDraft(storage, file, batchId);
      done = await uploadFile({ file, batchId, onProgress });
    }
    forgetDraft(storage, file);
    const row = done.documents[0];
    if (!row || row.unreadable) {
      discardBatch(batchId);
      toast({ kind: 'error', title: 'Could not read the PDF',
              msg: 'The file is damaged or password-protected, so it cannot be extracted.' });
      return;
    }
    waitingOn = { id: row.id, batchId, fileName: row.file_name, pages: row.pages, row };
    setHash(row.id);
    showConfirm(row);
  } catch (err) {
    if (batchId && err instanceof UploadError && !err.fatal) {
      // The server or the network is away for now (the client already
      // retried for minutes). Keep the draft and what arrived of the file.
      toast({ kind: 'warn', title: 'Upload interrupted',
              msg: 'Select the same file again to continue where it stopped.' });
    } else {
      // Refused, disk full, cancelled, or a fault before any upload began:
      // nothing worth resuming.
      forgetDraft(storage, file);
      if (batchId) discardBatch(batchId);
      toast({ kind: 'error', title: 'Could not upload the PDF', msg: String(err.message || err) });
    }
  } finally {
    uploading = false;
    setIdle();
  }
```
5. Replace `showConfirm` with:
```js
function showConfirm(row) {
  document.getElementById('plan-empty').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('cf-file').textContent = row.file_name;
  document.getElementById('cf-pages').textContent =
    `${row.pages} page${row.pages === 1 ? '' : 's'} · ready to extract`;
  // The staging warnings (multi-page, oversized, duplicate). A file they
  // excluded by default is started with "Include and start", which says
  // what the press does: it is the explicit permission decision 26 asks for.
  const warn = document.getElementById('cf-warn');
  const badges = badgesFor(row);
  warn.textContent = badges.map((b) => b.text).join(' · ');
  warn.hidden = badges.length === 0;
  setStartLabel(row.included ? 'Start extraction' : 'Include and start');
  document.getElementById('plan-confirm').hidden = false;
}

// The button's last text node is its label (index.html: icon, then text).
function setStartLabel(text) {
  const btn = document.getElementById('cf-start');
  const node = [...btn.childNodes].reverse().find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
  if (node) node.textContent = ` ${text} `;
}
```
6. Replace `cancelConfirm` with:
```js
function cancelConfirm() {
  // The whole batch of one goes: its staged file and the draft itself.
  // (Interim only -- B3's staging view removes single rows of a larger draft.)
  if (waitingOn) discardBatch(waitingOn.batchId);
  waitingOn = null;
  backToEmptyOrCurrent();
}
```
7. Replace `startExtraction` with:
```js
async function startExtraction() {
  const w = waitingOn;
  if (!w) return;
  document.getElementById('plan-confirm').hidden = true;
  showExtracting(w.fileName);
  try {
    // Excluded by default (out of scope or a duplicate): the press of
    // "Include and start" is the user including it.
    if (w.row && !w.row.included) w.row = await setIncluded(w.id, true);
    await startBatch(w.batchId);
    if (waitingOn !== w || stopping) return;   // Stop pressed meanwhile: Stop owns it now
    pollUntilReady();
  } catch (err) {
    if (waitingOn !== w || stopping) return;
    // The reply may have been lost after the server started the import.
    // Ask before offering Start again: a drawing that is no longer staged
    // is on its way, so wait for it like any other.
    try {
      const doc = await getDocument(w.id);
      if (waitingOn !== w || stopping) return;
      if (doc.state !== 'staged') { pollUntilReady(); return; }
    } catch { /* unknown: offer Start again; a second Start is refused harmlessly */ }
    if (waitingOn !== w || stopping) return;
    hideExtracting();
    // Still staged on the server: offer it again rather than strand it.
    showConfirm(w.row);
    toast({ kind: 'error', title: 'Could not start', msg: String(err.message || err) });
  }
}
```

- [ ] **Step 2: Check and run**

Run: `node --check app/static/js/main.js`
Expected: no output.
Run: `python -m pytest -q` → **1643 passed, 2 skipped** (unchanged: nothing server-side moved).

- [ ] **Step 3: Commit**
```bash
git add app/static/js/main.js app/static/js/api.js app/static/index.html app/static/styles/components.css
```
```bash
git commit -m "ui: import a single drawing as a batch of one through the chunked protocol" -m "The interim screen uploads through upload.js into a draft remembered per file, so selecting the same file again after a failure, a reload or a closed tab resumes it; only a fatal failure discards the draft. The confirm screen shows the staging warnings and says Include and start when the file was excluded by default. A Start whose reply was lost is recognised by asking for the drawing's state. Cancel discards the draft." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: The single-shot routes go; API tests import through the protocol

**Data-integrity: yes** (removes two endpoints and moves every API test helper onto the protocol).

Why remove rather than keep `/api/upload` and `/enqueue`: kept, they are a second door into the queue that skips Start's Include rules, and B1's upload creates non-draft batches with no staging semantics. Since Task 11 nothing in the UI calls them.

**Files:**
- Modify: `app/main.py`, `tests/test_api.py`, `tests/test_api_review.py`, `tests/test_api_import.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_api.py`:
```python
def test_the_single_shot_upload_and_per_drawing_enqueue_are_gone():
    """Since B2 a drawing arrives through /api/uploads into a draft and is
    queued only by starting the draft, where the Include rules live. Asked
    of the route table: a request to an unknown id at /enqueue would 404
    whether the route existed or not."""
    routes = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    assert ("POST", "/api/upload") not in routes
    assert ("POST", "/api/documents/{doc_id}/enqueue") not in routes
    assert ("POST", "/api/batches/{batch_id}/start") in routes
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_api.py -q -k single_shot`
Expected: FAIL — `assert ('POST', '/api/upload') not in {...}`.

- [ ] **Step 3: Move the tests onto the protocol**

`tests/test_api.py`:
1. Add `import hashlib` and `from pathlib import Path` to the imports.
2. Replace `save_pdf` and `upload_pdf` with:
```python
def save_pdf(test_client, path, filename="sample.pdf"):
    """Import a PDF the way the UI does -- a draft batch of one, the chunked
    upload protocol, completion -- and return its metadata. Nothing is
    queued."""
    data = Path(path).read_bytes()
    batch = test_client.post("/api/batches", json={"name": filename}).json()
    r = test_client.post("/api/uploads", json={
        "batch_id": batch["id"], "name": filename, "size": len(data),
        "fingerprint": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]
    if data:
        assert test_client.put(f"/api/uploads/{uid}?offset=0", content=data).status_code == 200
    r = test_client.post(f"/api/uploads/{uid}/complete")
    assert r.status_code == 200, r.text
    (row,) = r.json()["documents"]
    return {"session_id": row["id"], "fileName": row["file_name"],
            "pages": row["pages"], "batch_id": batch["id"]}


def start_pdf(test_client, sid):
    """What the interim UI's "Include and start" does for its batch of one:
    include the drawing, then start the import."""
    batch_id = test_client.get(f"/api/documents/{sid}").json()["batch_id"]
    r = test_client.post(f"/api/documents/{sid}/include", json={"included": True})
    assert r.status_code == 200, r.text
    r = test_client.post(f"/api/batches/{batch_id}/start")
    assert r.status_code == 200, r.text


def upload_pdf(test_client, path, filename="sample.pdf"):
    """Import, queue and extract a PDF through the background worker -- run
    inline, so the test is deterministic -- then open it for review. Returns
    the /open payload (session_id, image_url, rows, notes, marks,
    title_block, writer, events, last_seq)."""
    import app.main as main
    meta = save_pdf(test_client, path, filename)
    sid = meta["session_id"]
    start_pdf(test_client, sid)
    assert main.runtime().worker.run_once() == sid
    doc = test_client.get(f"/api/documents/{sid}").json()
    assert doc["state"] == "ready", doc
    r = test_client.post(f"/api/documents/{sid}/open")
    assert r.status_code == 200, r.text
    return r.json()
```
3. Replace `test_queued_drawings_report_their_queue_position` with:
```python
def test_queued_drawings_report_their_queue_position(sample_pdf, stub_backend):
    a = save_pdf(client, sample_pdf)["session_id"]
    b = save_pdf(client, sample_pdf)["session_id"]     # a duplicate: start_pdf includes it
    start_pdf(client, a)
    start_pdf(client, b)
    view = client.get(f"/api/documents/{b}").json()
    assert (view["state"], view["queue_position"]) == ("queued", 2)
    assert client.get(f"/api/documents/{a}").json()["queue_position"] == 1
```
4. Every remaining line `client.post(f"/api/documents/{sid}/enqueue")` (in `test_extraction_without_detection_backend_fails_the_document`, `test_cancel_and_retry_a_queued_drawing`, `test_delete_refuses_the_drawing_the_worker_is_still_running`, `test_retry_refuses_the_drawing_the_worker_is_still_running`, `test_a_running_drawing_reports_its_progress`) becomes `start_pdf(client, sid)`. Then search the file for `/enqueue` with the Grep tool: the only hits must be inside `test_the_single_shot_upload_and_per_drawing_enqueue_are_gone`.
5. In the parametrize of `test_document_routes_reject_a_malformed_id`, delete `("post", "enqueue"), `.
6. Delete `test_an_upload_that_cannot_be_stored_is_not_called_a_bad_pdf` (parametrized: 2 cases), `test_an_unparseable_upload_is_a_400_and_leaves_nothing` and `test_upload_runs_off_the_event_loop`. Their behaviour now lives in the protocol and is tested there: a write fault is 507/500 with a fixed sentence (`tests/batch/test_uploads.py::test_a_write_fault_is_reported_by_kind_and_moves_nothing`), an unparseable PDF is staged as unreadable instead of a 400 (decision 4; `test_an_unreadable_file_is_staged_but_never_included`), and the blocking handlers are sync (`tests/test_api_import.py::test_blocking_handlers_run_off_the_event_loop`).

`tests/test_api_review.py`: change the import to `from tests.test_api import save_pdf, start_pdf, upload_pdf`, and its line `client.post(f"/api/documents/{sid}/enqueue")` to `start_pdf(client, sid)`.

`tests/test_api_import.py`: delete `test_a_drawing_in_a_draft_cannot_be_queued_one_by_one` (the route is gone; the guard itself stays covered by `tests/batch/test_repo_staging.py::test_a_drawing_in_a_draft_is_never_queued_one_by_one`).

Run: `python -m pytest tests/test_api.py tests/test_api_review.py -q`
Expected: only `test_the_single_shot_upload_and_per_drawing_enqueue_are_gone` fails — the helpers already run on routes that exist.

- [ ] **Step 4: Implement** — `app/main.py`:
1. Delete the `upload` endpoint (`@app.post("/api/upload")` and its function) and the `enqueue_document` endpoint (`@app.post("/api/documents/{doc_id}/enqueue")` and its function).
2. Imports: change the FastAPI line to `from fastapi import FastAPI, HTTPException, Request`. Remove `import errno` and `import hashlib` if the Grep tool finds no other use of `errno.` / `hashlib.` in the file (only the deleted upload used them).

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_api.py tests/test_api_review.py tests/test_api_import.py -q`
Expected: all pass.

- [ ] **Step 6: Full suite, then commit**

Run: `python -m pytest -q` → **1638 passed, 2 skipped** (−6 test items: the 2-case store-fault test, the unparseable test, the event-loop test, the `enqueue` parametrize case and the draft-enqueue API test; +1 route-table test).
```bash
git add app/main.py tests/test_api.py tests/test_api_review.py tests/test_api_import.py
```
```bash
git commit -m "api: remove the single-shot upload and the per-drawing enqueue" -m "Kept, they would be a second door into the queue that skips Start's Include rules, and the single-shot upload made non-draft batches with no staging semantics. The API test helpers now import through the chunked protocol and start a batch of one, as the interim UI does." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Docs and a real-browser check

**Data-integrity: no.**

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: `CLAUDE.md` §2.** In the BATCH B1 paragraph the phrase spans a line break. Replace exactly these two lines:
```
`Runtime` that `app.main.runtime()` builds lazily. Upload stages a drawing,
`/api/documents/{id}/enqueue` queues it, the browser polls, and
```
with:
```
`Runtime` that `app.main.runtime()` builds lazily. A drawing is imported into
a draft batch and queued by starting it (B2, below); the browser polls, and
```
After that paragraph (it ends "…each with its own plan.") add:
```markdown
**BATCH B2: IMPORT** (`docs/plans/2026-10-07-batch-b2-import-plan.md`).
Files arrive through a chunked, resumable protocol — `POST /api/uploads`
(an unfinished upload of the same file in the same draft is resumed),
`PUT /api/uploads/{id}?offset=N` (≤ 64 MiB per request; accepted only at the
server's offset, fsynced before the index moves; a mismatch is 409 with the
true offset), `POST …/complete` (idempotent) — into a DRAFT batch
(`/api/batches`). Completion hashes the whole file and stages one document
per PDF; a ZIP is unpacked server-side (PDFs only; unsafe paths, symlinks
and non-PDFs listed as ignored, never written by name). Staging rows carry
`pages`, `oversized` (`render.effective_dpi` at `pdf_facts.EXTRACT_DPI`,
the probe's rule), `unreadable` and a live `duplicate_of` (same SHA-256 in
any batch). Include starts off for out-of-scope files and duplicates and can
never be ticked for an unreadable PDF; including an out-of-scope file stores
`allowed_out_of_scope`. **`POST /api/batches/{id}/start` is the only way a
staged drawing is queued** — `/api/upload` and `/enqueue` are gone.
`SINDRI_MIN_FREE_MB` (default 2048, `0` in tests) answers 507 below the
reserve. **Recovery runs inside `Worker.start()`, under the single-process
lock**, and removes only drawing dirs marked `.importing` that have no row
(plus week-old unfinished uploads and empty drafts). The interim UI uploads
one PDF as a batch of one through `app/static/js/upload.js` and remembers
the draft per file, so re-selecting an interrupted file resumes it; B3 adds
the staging view, folders and ZIPs in the UI.
```
Update the suite count in §2 (`Suite: **1483 passed, 2 skipped**`) and in §6 (`# 1483 passed, 2 skipped`) to the total Step 2 prints.

- [ ] **Step 2: Full verification**

Run: `python -m pytest -q` → note the count (expected **1638 passed, 2 skipped**).
Run: `bash ~/.claude/hooks/test-sindri-guard.sh` → `32 passed, 0 failed`.

- [ ] **Step 3: Real-browser drive** (throwaway, in the scratchpad — never committed). Start the app on the stub backend with temp dirs (the B1 launcher from `docs/plans/2026-10-07-batch-b1-core-plan.md` Task 11 Step 4, plus `os.environ["SINDRI_MIN_FREE_MB"] = "0"`) and drive it with Playwright (node `playwright-core` + the cached chromium, as B1 did). The script refers to `tests/fixtures/` files from inside its own code, never on a command line. It must check:
1. Upload the sample drawing → the status pill counts up "Uploading …%" → the confirm screen → Start → the drawing loads with rows. `GET /api/batches/<batch>` shows it `ready`, `draft: false`.
2. Abort the first `PUT /api/uploads/*` once (Playwright `page.route`: `route.abort()` the first time, `route.continue()` after) while uploading the sample (≈ 210 KB, one chunk) → the client retries after its 500 ms wait and the upload completes; the staged row's `size` equals the file's size. (Multi-chunk resync after a lost reply is covered by the node tests with an injected fetch; a browser drive would need an 8 MiB+ file for the same path.)
3. Abort EVERY `PUT /api/uploads/*` and, after a few seconds, reload the page (the budget is ten minutes; do not wait it out) → `localStorage` holds a `sindri.import:` key for the file. Stop aborting, select the same file again → no new batch is created (`GET /api/batches` count unchanged), the upload completes into the remembered draft, and the key is gone.
4. Upload the same sample again → the confirm screen shows `duplicate of "…"` and the button reads "Include and start"; press it → it is queued (`included` true).
5. Upload a 3-page PDF built in the script → the confirm screen shows the multi-page warning; "Include and start" → `GET /api/batches/<batch>` shows `allowed_out_of_scope: true`.
6. Upload a file with a PDF name and garbage content → "Could not read the PDF"; its batch is gone (`GET` 404).
7. Upload, then Cancel on the confirm screen → its batch is gone (`GET` 404) and its drawing dir no longer exists.
8. Serve the app on a non-localhost address over plain http (e.g. the machine's LAN IP), so `crypto.subtle` is undefined → an upload still completes (the plain-JS fingerprint).

Record the results for the controller (the state file is the controller's). Fix anything that fails in a separate commit with its own regression test, then re-run.

- [ ] **Step 4: Commit**
```bash
git add CLAUDE.md
```
```bash
git commit -m "docs: note batch B2 import in CLAUDE.md" -m "The chunked protocol, staging rules, Start as the only door into the queue, the disk reserve and recovery under the worker lock, so the next session starts from the code as it is." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

> **Milestone: ui** - state must be checkpointed here before continuing.

---

## After B2

B3 (UI) builds on this: the hash router (`#/`, `#/staging/<batch>`, `#/doc/<id>`); the **staging view** over `GET /api/batches/{id}` — rows with `badgesFor()`, the Include checkbox (`POST /api/documents/{id}/include`), per-file progress from `importFiles`' `onFile`, ignored ZIP entries, rename (`PATCH`), Discard (`DELETE`) and Start; **+ Import** accepting several files, a folder (`webkitdirectory`, `relPathOf`) or a ZIP into one draft named by `batchNameFor`; continuing a draft later from `GET /api/batches` (re-selecting the same files into it resumes their unfinished uploads; `recallDraft` / `rememberDraft` extend naturally from one file to a selection); the work list, review bar, resume and two-tab read-only banner. B3's migration 3 adds the flag counts and ETA columns.

**The interim Cancel discards the WHOLE draft** (`DELETE /api/batches/{id}`), which is right only for a batch of one. B3's staging view must remove a single row with `DELETE /api/documents/{id}` (which keeps the draft) whenever the draft holds more than one, and keep Discard for the whole import.

Things B2 leaves on purpose: a single long `complete` request for a large ZIP (B3 shows "unpacking…"; B4 checks proxy timeouts once hosting is decided, together with `MAX_CHUNK`); an empty draft left behind when Stop wins a race against Start (removed by the week-old sweep); a draft whose rows are discarded concurrently with a completion committing into it can leave that completion's dirs unmarked after a crash in that window (vanishingly narrow; the dirs are rowless and inert); and the per-upload lock dict growing by one entry per upload (like the export locks). B4 (mass export, retention, access token) can call `Uploads.cleanup_stale()` from its daily sweep — never `recover()`, which belongs to the worker's lock.
