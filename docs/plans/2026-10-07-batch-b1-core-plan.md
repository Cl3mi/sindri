# Batch B1 — Core (queue, worker, resume, server-side export) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow `context-management:milestone-checkpoints`: checkpoint `docs/plans/2026-10-07-batch-b1-core-plan.state.md` at each marker.

**Goal:** Extraction runs in a background worker that needs no open browser; a drawing is reviewed later by reopening it from the server, exactly where the reviewer stopped; exports are rendered server-side from the sealed revision.

**Architecture:** A new product package `app/batch/` holds a SQLite index (`db.py`, `repo.py`) of documents and their lifecycle, a data-dir layout (`paths.py`), one extraction `Worker` thread and a `Runtime` that wires them with the existing phase-1 `ReviewStore`, which gains `open()` (writer rotation + net journal) and `revision()`. `app/main.py` drops the tab-bound SSE extraction, `/tmp` sessions and browser-row exports; the existing UI keeps working as an interim "batch of one" (upload → Start → poll → open), and a reload resumes the drawing from `#doc=<id>`.

**Tech Stack:** Python 3 / FastAPI / sqlite3 (stdlib) / pytest; vanilla ES modules + `node --test`.

**Design:** `docs/plans/2026-10-07-batch-async-review-design.md` (read §0 and §1-§5 before starting). B2-B4 get their own plans after B1 lands.

**Rules that bind every task** (from CLAUDE.md — read §1 and §4 there):
* TDD strictly: failing test → watch it fail → minimal code → pass → commit. One task, one commit (a review fix may be a second commit).
* Never read client data; never put the protected path in a command or a subagent prompt; single, unpiped, unchained shell commands.
* Server logs carry ids (first 8 chars), states and exception TYPES only — never exception messages, file names or values.
* `app/batch/` and `app/review/` must never import `app.eval`.
* Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` (or the implementing model's accurate name).
* Full suite before every commit: `python -m pytest -q`. Baseline before B1: **1388 passed, 2 skipped**.

**Clarification of design §5 (decided while planning):** a crash/restart mid-extraction is requeued until `attempts` reaches 2 (decision 6); an *exception* from `extract()` fails the document immediately with Retry, because decoding is greedy and deterministic — a second identical attempt would cost ~15 GPU minutes to fail the same way.

---

## File structure

| File | Responsibility |
|---|---|
| `app/batch/__init__.py` | package docstring (product code, never imports `app.eval`) |
| `app/batch/paths.py` | `DataDir`: data-dir layout, document-id validation |
| `app/batch/db.py` | `connect()`, numbered `MIGRATIONS`, `migrate()` |
| `app/batch/repo.py` | `Repo`: guarded state transitions, queue order, pause flag |
| `app/batch/worker.py` | `Worker`, `Cancelled`, `result_payload()` |
| `app/batch/runtime.py` | `Runtime`: wires data dir, db, repo, review store, worker |
| `app/review/store.py` | + `open()`, `revision()`, `write_json_atomic` |
| `app/main.py` | lazy `runtime()`, lifespan, document endpoints, `/open`, server-side export |
| `app/static/js/state.js` | + `opFromEvent()`, `restoreSession()` |
| `app/static/js/journal.js` | + `resume()`; logging-off branch removed |
| `app/static/js/finish.js` | journal mandatory; `exportAll(snapshot, revision)` |
| `app/static/js/api.js` | document endpoints; SSE + browser-row export removed |
| `app/static/js/main.js`, `index.html` | interim flow: queue, poll, open, `#doc=` resume |
| `tests/batch/*` | new unit tests |
| `tests/conftest.py` | every test gets its own data + review dir |

---

### Task 1: Data dir and SQLite schema

**Files:**
- Create: `app/batch/__init__.py`, `app/batch/paths.py`, `app/batch/db.py`
- Create: `tests/batch/__init__.py` (empty), `tests/batch/test_db.py`
- Modify: `tests/review/test_product_boundary.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_db.py`:
```python
"""The document index (batch design §1). Migrations are numbered and atomic:
a crash mid-migration must leave the previous schema, never half of the new
one stamped with the new version."""
import sqlite3

import pytest

from app.batch import db
from app.batch.paths import DataDir


def test_migrate_creates_the_schema_and_stamps_its_version(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert db.migrate(conn) == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"batch", "document", "setting"} <= tables


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    assert db.migrate(conn) == len(db.MIGRATIONS)


def test_wal_mode_so_readers_never_wait_on_the_worker(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_failed_migration_leaves_the_previous_version(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [
        "CREATE TABLE one (x INTEGER);",
        "CREATE TABLE two (x INTEGER); CREATE TABLE broken (;",
    ])
    conn = db.connect(tmp_path / "s.db")
    with pytest.raises(sqlite3.Error):
        db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "two" not in tables


def test_a_newer_schema_than_the_code_is_refused(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        db.migrate(conn)


def test_doc_dir_refuses_anything_but_a_hex_id(tmp_path):
    data = DataDir(tmp_path)
    assert data.doc_dir("a" * 32) == tmp_path / "docs" / ("a" * 32)
    for bad in ["../x", "A" * 32, "a" * 31, "", None]:
        with pytest.raises(ValueError):
            data.doc_dir(bad)
```

Modify `tests/review/test_product_boundary.py`: replace the `REVIEW_DIR = ...` line and the test function with:
```python
APP = Path(__file__).resolve().parents[2] / "app"
# Every product package that ships to the client.
PRODUCT_DIRS = [APP / "review", APP / "batch"]
```
and
```python
def test_no_product_module_imports_app_eval():
    offenders = []
    for d in PRODUCT_DIRS:
        for path in sorted(d.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _imports_app_eval(tree):
                offenders.append(f"{d.name}/{path.name}")
    assert offenders == []
```
(keep the module docstring; add one sentence: "app/batch/ is held to the same rule.")

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_db.py tests/review/test_product_boundary.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch'`.

- [ ] **Step 3: Implement**

`app/batch/__init__.py`:
```python
"""Batch import, background extraction and later review
(docs/plans/2026-10-07-batch-async-review-design.md).

Product code: it ships to the client and must never import app.eval
(tests/review/test_product_boundary.py)."""
```

`app/batch/paths.py`:
```python
"""Where a deployment keeps its drawings (batch design §1).

    <root>/sindri.db        the index (db.py)
    <root>/docs/<id>/       input.pdf, page.png, result.json, exports/

Everything under it is CLIENT DATA under CLAUDE.md §1."""
import re
from pathlib import Path

# Document ids are uuid4().hex -- the same ids the review store keys its
# sessions by. Anything else is refused before a path is built from it, so
# an id can never walk out of docs/.
DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class DataDir:
    def __init__(self, root):
        self.root = Path(root)
        self.docs = self.root / "docs"
        self.docs.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.root / "sindri.db"

    def doc_dir(self, doc_id) -> Path:
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            raise ValueError("malformed document id")
        return self.docs / doc_id
```

`app/batch/db.py`:
```python
"""SQLite index of batches and documents (batch design §1).

One connection per process, shared by the API threads and the worker under
Repo's lock. WAL so a list request never waits on the worker's writes;
synchronous=FULL because a drawing the server said it queued must survive
power loss, like an acknowledged journal event does. Schema changes are
numbered migrations keyed on PRAGMA user_version -- append an entry, never
edit one that has shipped."""
import sqlite3

MIGRATIONS = [
    # 1: batches, documents, settings
    """
    CREATE TABLE batch (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        draft INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE document (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES batch(id),
        file_name TEXT NOT NULL,
        rel_path TEXT NOT NULL DEFAULT '',
        sha256 TEXT,
        pages INTEGER,
        oversized INTEGER,
        allowed_out_of_scope INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        error_type TEXT,
        final_revision INTEGER,
        sealed_seq INTEGER NOT NULL DEFAULT 0,
        exported_at TEXT,
        exported_revision INTEGER,
        created_at TEXT NOT NULL,
        queued_at TEXT,
        started_at TEXT,
        ready_at TEXT,
        updated_at TEXT NOT NULL,
        purged_at TEXT
    );
    CREATE INDEX document_queue ON document(state, priority DESC, queued_at);
    CREATE TABLE setting (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def connect(path) -> sqlite3.Connection:
    # isolation_level=None: transactions are explicit (Repo._tx), so nothing
    # commits behind a caller's back. check_same_thread=False because the
    # worker thread shares this connection under Repo's lock.
    conn = sqlite3.connect(str(path), isolation_level=None,
                           check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > len(MIGRATIONS):
        raise RuntimeError(
            f"database schema {have} is newer than this code ({len(MIGRATIONS)})")
    for n, sql in enumerate(MIGRATIONS[have:], start=have + 1):
        # One script, one transaction: a crash or a bad statement leaves the
        # previous version intact, never half a schema stamped as the new one.
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {n};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/batch/test_db.py tests/review/test_product_boundary.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest -q` → baseline + 6 passed, 2 skipped (the boundary test is renamed, not added).
```bash
git add app/batch/__init__.py app/batch/paths.py app/batch/db.py tests/batch/__init__.py tests/batch/test_db.py tests/review/test_product_boundary.py
git commit -m "batch: data dir layout and a migrated SQLite index"
```

---

### Task 2: Document lifecycle repository

**Files:**
- Create: `app/batch/repo.py`, `tests/batch/test_repo.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_repo.py`:
```python
"""Document lifecycle (batch design §1-§2). Every transition is guarded by
the states it may leave, so an API cancel racing the worker's finish has
exactly one winner and the loser is told, never silently overwritten."""
import itertools

import pytest

from app.batch import db
from app.batch.repo import InvalidTransition, Repo, UnknownDocument


def ids(n):
    return [f"{i:032x}" for i in range(1, n + 1)]


@pytest.fixture
def repo(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tick = itertools.count()
    return Repo(conn, clock=lambda: f"2026-10-07T00:00:{next(tick):09d}")


def staged(repo, doc_id, name="a.pdf"):
    return repo.create_document(doc_id, name, pages=1, sha256="0" * 64)


def queued(repo, doc_id):
    staged(repo, doc_id)
    repo.enqueue(doc_id)


def test_an_upload_is_a_staged_batch_of_one_named_after_the_file(repo):
    d = staged(repo, ids(1)[0], "Flansch.pdf")
    assert d["state"] == "staged" and d["attempts"] == 0
    name = repo._conn.execute("SELECT name FROM batch WHERE id = ?", (d["batch_id"],)).fetchone()[0]
    assert name == "Flansch.pdf"


def test_claim_takes_priority_first_then_first_in_first_out(repo):
    a, b, c = ids(3)
    for d in (a, b, c):
        queued(repo, d)
    repo._conn.execute("UPDATE document SET priority = 1 WHERE id = ?", (c,))
    assert [repo.claim_next()["id"] for _ in range(3)] == [c, a, b]
    assert repo.claim_next() is None


def test_claim_marks_extracting_and_counts_the_attempt(repo):
    (a,) = ids(1)
    queued(repo, a)
    d = repo.claim_next()
    assert d["state"] == "extracting" and d["attempts"] == 1 and d["started_at"]


def test_a_paused_queue_claims_nothing_until_resumed(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.set_paused(True)
    assert repo.paused() and repo.claim_next() is None
    repo.set_paused(False)
    assert repo.claim_next()["id"] == a


def test_a_transition_from_the_wrong_state_is_refused(repo):
    (a,) = ids(1)
    staged(repo, a)
    with pytest.raises(InvalidTransition, match="staged"):
        repo.mark_ready(a)
    with pytest.raises(UnknownDocument):
        repo.mark_ready("f" * 32)


def test_a_cancel_beats_a_late_finish(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_cancelled(a)
    with pytest.raises(InvalidTransition):
        repo.mark_ready(a)
    assert repo.get(a)["state"] == "cancelled"
    repo.mark_cancelled(a)   # idempotent


def test_an_interrupted_run_is_requeued_until_its_second_attempt(repo):
    a, b = ids(2)
    for d in (a, b):
        queued(repo, d)
    repo.claim_next()
    repo.claim_next()
    repo._conn.execute("UPDATE document SET attempts = 2 WHERE id = ?", (b,))
    assert repo.requeue_interrupted() == {"requeued": 1, "failed": 1}
    assert repo.get(a)["state"] == "queued"
    assert (repo.get(b)["state"], repo.get(b)["error_type"]) == ("failed", "interrupted")


def test_retry_resets_attempts_and_error(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_failed(a, "RuntimeError")
    repo.retry(a)
    d = repo.get(a)
    assert (d["state"], d["attempts"], d["error_type"]) == ("queued", 0, None)


def test_review_events_move_ready_or_finished_into_review_only_past_the_seal(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.events_appended(a, 1)
    assert repo.get(a)["state"] == "in_review"
    repo.mark_finished(a, revision=1, final_seq=5)
    repo.events_appended(a, 5)       # a resend of sealed events: still finished
    assert repo.get(a)["state"] == "finished"
    repo.events_appended(a, 6)       # a new edit after reopening
    assert repo.get(a)["state"] == "in_review"
    assert repo.get(a)["final_revision"] == 1


def test_finish_straight_from_ready_is_allowed(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.mark_finished(a, revision=1, final_seq=0)
    assert repo.get(a)["state"] == "finished"


def test_queue_position_counts_who_runs_first(repo):
    a, b, c = ids(3)
    for d in (a, b, c):
        queued(repo, d)
    repo._conn.execute("UPDATE document SET priority = 1 WHERE id = ?", (c,))
    assert [repo.queue_position(d) for d in (a, b, c)] == [2, 3, 1]
    repo.claim_next()
    assert repo.queue_position(c) is None


def test_delete_refuses_a_running_extraction_and_drops_an_empty_batch(repo):
    a, b = ids(2)
    queued(repo, a)
    staged(repo, b)
    repo.claim_next()
    with pytest.raises(InvalidTransition):
        repo.delete(a)
    batch = repo.get(b)["batch_id"]
    assert repo.delete(b) is True
    assert repo._conn.execute("SELECT COUNT(*) FROM batch WHERE id = ?", (batch,)).fetchone()[0] == 0
    assert repo.delete(b) is False


def test_mark_exported_records_the_revision_without_changing_state(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.mark_finished(a, revision=2, final_seq=3)
    repo.mark_exported(a, 2)
    d = repo.get(a)
    assert (d["state"], d["exported_revision"]) == ("finished", 2) and d["exported_at"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_repo.py -q` → FAIL, `No module named 'app.batch.repo'`.

- [ ] **Step 3: Implement** `app/batch/repo.py`:
```python
"""Document lifecycle in the index (batch design §1-§2).

    staged -> queued -> extracting -> ready -> in_review -> finished
                                  \\-> failed / cancelled      ^   |
                                                  reopen: finished -> in_review

Every transition is ONE guarded UPDATE (`WHERE state IN (...)`), so two
threads racing -- an API cancel against the worker finishing, say -- cannot
both win: the loser's rowcount is 0 and it gets InvalidTransition instead of
silently overwriting the winner."""
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Optional

# Crash/restart retries (decision 6). An exception from extract() is NOT
# retried automatically: decoding is greedy and deterministic, so a second
# identical run would spend ~15 GPU minutes failing the same way.
MAX_ATTEMPTS = 2


class BatchError(Exception):
    status = 400


class UnknownDocument(BatchError):
    status = 404


class InvalidTransition(BatchError):
    status = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repo:
    def __init__(self, conn, clock=_now):
        self._conn = conn
        self._clock = clock
        # One connection shared by API threads and the worker; sqlite3
        # objects are not safe to use from two threads at once.
        self._lock = threading.Lock()

    @contextmanager
    def _tx(self):
        with self._lock:
            # IMMEDIATE takes the write lock up front, so a claim's SELECT and
            # its UPDATE see the same queue.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _move(self, c, doc_id, from_states: Iterable[str], to: str, **fields) -> None:
        from_states = tuple(from_states)
        sets = "".join(f", {k} = ?" for k in fields)
        cur = c.execute(
            f"UPDATE document SET state = ?, updated_at = ?{sets} "
            f"WHERE id = ? AND state IN ({', '.join('?' * len(from_states))})",
            (to, self._clock(), *fields.values(), doc_id, *from_states))
        if cur.rowcount == 0:
            row = c.execute("SELECT state FROM document WHERE id = ?", (doc_id,)).fetchone()
            if row is None:
                raise UnknownDocument(f"no document {str(doc_id)[:8]}")
            raise InvalidTransition(
                f"document is {row['state']}, not {' or '.join(from_states)}")

    # ----- reads ---------------------------------------------------------
    def get(self, doc_id) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            raise UnknownDocument(f"no document {str(doc_id)[:8]}")
        return dict(row)

    def queue_position(self, doc_id) -> Optional[int]:
        """1 = runs next. None unless the document is queued."""
        with self._lock:
            me = self._conn.execute(
                "SELECT state, priority, queued_at, rowid FROM document WHERE id = ?",
                (doc_id,)).fetchone()
            if me is None or me["state"] != "queued":
                return None
            ahead = self._conn.execute(
                "SELECT COUNT(*) FROM document WHERE state = 'queued' AND ("
                " priority > ? OR (priority = ? AND (queued_at < ?"
                " OR (queued_at = ? AND rowid < ?))))",
                (me["priority"], me["priority"], me["queued_at"],
                 me["queued_at"], me["rowid"])).fetchone()[0]
        return ahead + 1

    def paused(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM setting WHERE key = 'paused'").fetchone()
        return row is not None and row["value"] == "1"

    # ----- writes --------------------------------------------------------
    def set_paused(self, on: bool) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO setting (key, value) VALUES ('paused', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      ("1" if on else "0",))

    def create_document(self, doc_id, file_name, pages, sha256, batch_name=None) -> dict:
        """A single upload is a batch of one (decision 17); B2 adds real batches."""
        now = self._clock()
        with self._tx() as c:
            batch_id = uuid.uuid4().hex
            c.execute("INSERT INTO batch (id, name, draft, created_at) VALUES (?, ?, 0, ?)",
                      (batch_id, batch_name or file_name, now))
            c.execute(
                "INSERT INTO document (id, batch_id, file_name, sha256, pages, state,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'staged', ?, ?)",
                (doc_id, batch_id, file_name, sha256, pages, now, now))
        return self.get(doc_id)

    def enqueue(self, doc_id) -> None:
        with self._tx() as c:
            self._move(c, doc_id, ("staged",), "queued", queued_at=self._clock())

    def claim_next(self) -> Optional[dict]:
        with self._tx() as c:
            p = c.execute("SELECT value FROM setting WHERE key = 'paused'").fetchone()
            if p is not None and p["value"] == "1":
                return None
            row = c.execute(
                "SELECT id FROM document WHERE state = 'queued' "
                "ORDER BY priority DESC, queued_at, rowid LIMIT 1").fetchone()
            if row is None:
                return None
            now = self._clock()
            c.execute("UPDATE document SET state = 'extracting', attempts = attempts + 1,"
                      " started_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
        return self.get(row["id"])

    def mark_ready(self, doc_id) -> None:
        with self._tx() as c:
            self._move(c, doc_id, ("extracting",), "ready",
                       ready_at=self._clock(), error_type=None)

    def mark_failed(self, doc_id, error_type: str) -> None:
        with self._tx() as c:
            self._move(c, doc_id, ("extracting",), "failed", error_type=error_type)

    def mark_cancelled(self, doc_id) -> None:
        with self._tx() as c:
            self._move(c, doc_id, ("queued", "extracting", "cancelled"), "cancelled")

    def retry(self, doc_id) -> None:
        with self._tx() as c:
            self._move(c, doc_id, ("failed", "cancelled"), "queued",
                       attempts=0, error_type=None, queued_at=self._clock())

    def requeue_interrupted(self, max_attempts: int = MAX_ATTEMPTS) -> dict:
        """At worker start: a row still `extracting` was cut off by a crash or
        restart. Run it again unless it already used its attempts."""
        now = self._clock()
        with self._tx() as c:
            failed = c.execute(
                "UPDATE document SET state = 'failed', error_type = 'interrupted',"
                " updated_at = ? WHERE state = 'extracting' AND attempts >= ?",
                (now, max_attempts)).rowcount
            requeued = c.execute(
                "UPDATE document SET state = 'queued', updated_at = ? "
                "WHERE state = 'extracting'", (now,)).rowcount
        return {"requeued": requeued, "failed": failed}

    def events_appended(self, doc_id, max_seq: int) -> None:
        """The reviewer acted. Only an event past the last seal counts: a
        resend of already-sealed events must not reopen a finished drawing."""
        with self._tx() as c:
            c.execute("UPDATE document SET state = 'in_review', updated_at = ? "
                      "WHERE id = ? AND state IN ('ready', 'finished') AND sealed_seq < ?",
                      (self._clock(), doc_id, max_seq))

    def mark_finished(self, doc_id, revision: int, final_seq: int) -> None:
        with self._tx() as c:
            self._move(c, doc_id, ("ready", "in_review", "finished"), "finished",
                       final_revision=revision, sealed_seq=final_seq)

    def mark_exported(self, doc_id, revision: int) -> None:
        with self._tx() as c:
            c.execute("UPDATE document SET exported_at = ?, exported_revision = ? "
                      "WHERE id = ?", (self._clock(), revision, doc_id))

    def delete(self, doc_id) -> bool:
        with self._tx() as c:
            row = c.execute("SELECT state, batch_id FROM document WHERE id = ?",
                            (doc_id,)).fetchone()
            if row is None:
                return False
            if row["state"] == "extracting":
                raise InvalidTransition("cancel the extraction before removing the drawing")
            c.execute("DELETE FROM document WHERE id = ?", (doc_id,))
            c.execute("DELETE FROM batch WHERE id = ? AND NOT EXISTS "
                      "(SELECT 1 FROM document WHERE batch_id = ?)",
                      (row["batch_id"], row["batch_id"]))
        return True
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/batch/test_repo.py -q` → all pass.

- [ ] **Step 5: Full suite, commit**
```bash
git add app/batch/repo.py tests/batch/test_repo.py
git commit -m "batch: document lifecycle with guarded state transitions"
```

---

### Task 3: Review store — reopen with writer rotation, read a sealed revision

**Files:**
- Modify: `app/review/store.py`
- Test: `tests/review/test_store.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/review/test_store.py`):
```python
# ----- reopen (batch design §3) -----------------------------------------

def test_open_rotates_the_writer_so_the_older_tab_is_refused(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    opened = store.open(SID)
    assert opened["writer"] != writer
    with pytest.raises(StaleWriter):
        store.append(SID, writer, [ev(2, "accept", ids=["b"])])
    assert store.append(SID, opened["writer"], [ev(2, "accept", ids=["b"])]) == 2


def test_open_returns_the_proposal_the_net_events_and_the_last_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "delete_row", id="b"),
                               ev(3, "retract", target=2)])
    opened = store.open(SID)
    assert [r["id"] for r in opened["proposal"]] == ["a", "b"]
    assert [e["seq"] for e in opened["events"]] == [1]
    assert opened["last_seq"] == 3


def test_open_after_a_seal_continues_from_the_sealed_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"]),
                               ev(3, "retract", target=2)])
    rows = [{**row("a"), "reviewed": True}, row("b")]
    store.seal(SID, writer, 3, rows, ["a"])
    opened = store.open(SID)
    assert opened["last_seq"] == 3
    assert [e["seq"] for e in opened["events"]] == [1]


def test_open_drops_events_stranded_above_a_gap(store, writer):
    """An event above a hole was never acknowledged (acks are contiguous), so
    its tab still held it -- and that tab is the one this open just replaced.
    Left on disk it would collide with the new tab's seq numbers, and append
    keeps the FIRST write of a seq, silently discarding the new edit."""
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(3, "delete_row", id="b")])
    opened = store.open(SID)
    assert opened["last_seq"] == 1
    assert store.append(SID, opened["writer"], [ev(2, "accept", ids=["b"]),
                                                ev(3, "unaccept", ids=["b"])]) == 3
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert [json.loads(l)["type"] for l in lines] == ["accept", "accept", "unaccept"]


def test_open_an_unknown_session_is_refused(store):
    with pytest.raises(UnknownSession):
        store.open(SID)


def test_revision_reads_a_sealed_revision_or_the_latest(store, writer):
    store.seal(SID, writer, 0, [row("a"), row("b")], [])
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    store.seal(SID, writer, 1, [{**row("a"), "reviewed": True}, row("b")], ["a"])
    assert store.revision(SID)["revision"] == 2
    assert store.revision(SID, 1)["reviewed_ids"] == []
    with pytest.raises(UnknownSession):
        store.revision(SID, 3)
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/review/test_store.py -q` → the 6 new tests fail with `AttributeError: 'ReviewStore' object has no attribute 'open'`.

- [ ] **Step 3: Implement** in `app/review/store.py`.

Below `_write_json`, add the public alias the batch worker uses (one atomic-write implementation, not two):
```python
# Public name for app.batch, which writes result.json with the same
# durability guarantees as a review record.
write_json_atomic = _write_json
```

Append to `ReviewStore` after `seal`:
```python
    def open(self, sid: str) -> dict:
        """Reopen a session for review (batch design §3): a NEW writer token,
        so whichever tab held the old one is refused from now on (latest
        opener wins, decision 25), plus everything the browser needs to
        rebuild the review -- the proposal, the net events, and the seq to
        continue numbering from."""
        with self._lock:
            d = self._dir(sid)
            self._repair_torn_tail(d)
            through = self._sealed_through(d)
            events = self._events(d)
            top = contiguous_seq({e["seq"] for e in events}, start=through)
            kept = sorted((e for e in events if e["seq"] <= top), key=lambda e: e["seq"])
            if len(kept) != len(events):
                # Above a hole: never acknowledged, owned by the tab this open
                # replaces, and would collide with the new tab's numbering.
                _atomic_write(d / "events.jsonl",
                              "".join(json.dumps(e) + "\n" for e in kept))
            try:
                net = net_events(kept)
                proposal = json.loads(
                    (d / "proposal.json").read_text(encoding="utf-8"))["rows"]
            except JournalError as exc:
                raise JournalInvalid(str(exc)) from exc
            except (KeyError, TypeError) as exc:
                raise JournalInvalid(f"corrupt review record: {exc}") from exc
            writer = secrets.token_hex(16)
            _atomic_write(d / "writer", writer)
            return {"writer": writer, "proposal": proposal, "events": net, "last_seq": top}

    def revision(self, sid: str, n: int = None) -> dict:
        """A sealed revision -- the latest when `n` is None. Exports are
        rendered from this, never from rows a browser sends (batch design §4)."""
        with self._lock:
            d = self._dir(sid)
            if n is None:
                n = self._revisions(d)
            p = d / "sealed" / f"r{n}.json"
            if not isinstance(n, int) or n < 1 or not p.is_file():
                raise UnknownSession(f"no sealed revision r{n} for session {sid[:8]}")
            return json.loads(p.read_text(encoding="utf-8"))
```

Also update the module docstring's layout block: after the `writer` line change the comment to `the session's single-writer token (rotated by open())`.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/review -q` → all pass.

- [ ] **Step 5: Full suite, commit**
```bash
git add app/review/store.py tests/review/test_store.py
git commit -m "review: reopen a session with a fresh writer, read a sealed revision"
```

---

### Task 4: Extraction worker

**Files:**
- Create: `app/batch/worker.py`, `tests/batch/test_worker.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_worker.py`:
```python
"""The background extraction worker (batch design §1, §5). Driven through
run_once() so every test is deterministic; start()/stop() are tested only for
the single-worker lock and the startup requeue."""
import json

import pytest

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Cancelled, Worker
from app.models import Characteristic, ExtractionResult
from app.review.store import ReviewStore

A = "a" * 32


class Harness:
    def __init__(self, tmp_path):
        self.data = DataDir(tmp_path / "data")
        conn = db.connect(self.data.db_path)
        db.migrate(conn)
        self.repo = Repo(conn)
        self.store = ReviewStore(tmp_path / "reviews")
        self.extract_calls = 0
        self.on_extract = lambda progress: None
        self.header = lambda pdf: {"h": 1}
        self.worker = Worker(self.repo, self.data, self.store,
                             self._extract, lambda pdf: self.header(pdf))

    def _extract(self, pdf_path, work_dir, progress):
        self.extract_calls += 1
        progress("render")
        self.on_extract(progress)
        progress("place")
        return ExtractionResult(characteristics=[Characteristic(pos=1, id="r1", nominal="20")])

    def queue(self, doc_id=A):
        d = self.data.doc_dir(doc_id)
        d.mkdir(parents=True)
        (d / "input.pdf").write_bytes(b"%PDF-1.4")
        self.repo.create_document(doc_id, "a.pdf", 1, "0" * 64)
        self.repo.enqueue(doc_id)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_a_queued_drawing_is_extracted_and_becomes_reviewable(h):
    h.queue()
    assert h.worker.run_once() == A
    assert h.repo.get(A)["state"] == "ready"
    result = json.loads((h.data.doc_dir(A) / "result.json").read_text())
    assert [r["id"] for r in result["rows"]] == ["r1"]
    proposal = json.loads((h.store.root / A / "proposal.json").read_text())
    assert proposal["rows"] == result["rows"]


def test_an_empty_queue_runs_nothing(h):
    assert h.worker.run_once() is None


def test_progress_is_visible_while_running_and_cleared_after(h):
    seen = {}
    h.on_extract = lambda progress: seen.update(h.worker.progress.get(A) or {})
    h.queue()
    h.worker.run_once()
    assert seen["step"] == "render"
    assert A not in h.worker.progress


def test_an_extraction_exception_fails_the_drawing_by_type_only(h):
    def boom(progress):
        raise RuntimeError("client text that must never be stored")
    h.on_extract = boom
    h.queue()
    h.worker.run_once()
    d = h.repo.get(A)
    assert (d["state"], d["error_type"]) == ("failed", "RuntimeError")
    assert not (h.store.root / A).exists()


def test_a_cancel_stops_the_run_at_the_next_progress_report(h):
    def cancel_then_continue(progress):
        h.repo.mark_cancelled(A)     # what POST /cancel does while it runs
        progress("detect")
    h.on_extract = cancel_then_continue
    h.queue()
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "cancelled"
    assert not (h.data.doc_dir(A) / "result.json").exists()


def test_a_cancel_that_arrives_after_the_last_report_still_wins(h):
    h._extract_orig = h._extract

    def finish_then_cancel(pdf_path, work_dir, progress):
        r = h._extract_orig(pdf_path, work_dir, progress)
        h.repo.mark_cancelled(A)
        return r
    h.worker.extract_fn = finish_then_cancel
    h.queue()
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "cancelled"


def test_a_review_store_fault_keeps_the_extraction_and_retry_skips_the_gpu(h):
    """result.json is written first, so a drawing whose review record could
    not be created is retried from it -- not re-extracted for ~15 GPU minutes."""
    def bad_header(pdf):
        raise OSError("disk")
    h.header = bad_header
    h.queue()
    h.worker.run_once()
    d = h.repo.get(A)
    assert (d["state"], d["error_type"]) == ("failed", "review_store:OSError")
    assert (h.data.doc_dir(A) / "result.json").is_file()
    h.header = lambda pdf: {"h": 1}
    h.repo.retry(A)
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "ready"
    assert h.extract_calls == 1


def test_a_second_worker_on_the_same_data_dir_refuses_to_start(h):
    other = Worker(h.repo, h.data, h.store, h._extract, lambda pdf: {})
    h.worker.start()
    try:
        with pytest.raises(RuntimeError, match="single worker"):
            other.start()
    finally:
        h.worker.stop()


def test_start_requeues_a_run_cut_off_by_a_restart(h):
    h.queue()
    h.repo.claim_next()          # extracting when the process died
    h.repo.set_paused(True)      # keep the loop from running it during the test
    h.worker.start()
    try:
        assert h.repo.get(A)["state"] == "queued"
    finally:
        h.worker.stop()


def test_cancelled_is_an_exception_the_pipeline_can_raise_through():
    assert issubclass(Cancelled, Exception)
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/batch/test_worker.py -q` → FAIL, `No module named 'app.batch.worker'`.

- [ ] **Step 3: Implement** `app/batch/worker.py`:
```python
"""The extraction worker (batch design §1): one drawing at a time, in the
background, with no browser attached.

A cancel is read from the index at every progress report, not from an
in-memory flag: a flag set for a QUEUED drawing would otherwise outlive the
cancel and kill that drawing's run the moment a later Retry started it."""
import fcntl
import json
import logging
import threading
from typing import Callable, Optional

from app.batch.repo import InvalidTransition
from app.review.store import write_json_atomic

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised from the progress callback to unwind extract() cooperatively."""


def result_payload(result) -> dict:
    """Dumped once in json mode, so proposal.json and every later /open serve
    byte-identical rows. Suggestions ride in the same list, flagged
    (suggestion-tray design)."""
    return {
        "rows": [r.model_dump(mode="json")
                 for r in [*result.characteristics, *result.suggestions]],
        "notes": result.notes.model_dump(mode="json") if result.notes is not None else None,
        "marks": result.marks.model_dump(mode="json") if result.marks is not None else None,
        "title_block": [t.model_dump(mode="json") for t in result.title_block],
    }


class Worker:
    def __init__(self, repo, data, store, extract_fn: Callable, header_fn: Callable,
                 idle_wait: float = 2.0):
        self.repo = repo
        self.data = data
        self.store = store
        self.extract_fn = extract_fn     # (pdf_path, work_dir, progress) -> ExtractionResult
        self.header_fn = header_fn       # (pdf_path) -> review header dict
        self.idle_wait = idle_wait
        self.progress = {}               # doc_id -> latest progress report (UI only, never logged)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._lockfile = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> Optional[str]:
        doc = self.repo.claim_next()
        if doc is None:
            return None
        self._run(doc["id"])
        return doc["id"]

    def _run(self, doc_id: str) -> None:
        d = self.data.doc_dir(doc_id)
        result_path = d / "result.json"

        def progress(step, detail="", current=None, total=None):
            if self.repo.get(doc_id)["state"] == "cancelled":
                raise Cancelled()
            self.progress[doc_id] = {"step": step, "detail": detail,
                                     "current": current, "total": total}

        try:
            if not result_path.is_file():
                result = self.extract_fn(d / "input.pdf", d, progress)
                # FIRST, and atomically: a later review-store fault is then
                # retried from this file without re-running the GPU.
                write_json_atomic(result_path, result_payload(result))
        except Cancelled:
            self._settle(doc_id, self.repo.mark_cancelled)
            return
        except Exception as e:
            # TYPE only: a pipeline message can carry client text (CLAUDE.md §1).
            log.warning("extraction failed for %s: %s", doc_id[:8], type(e).__name__)
            self._settle(doc_id, self.repo.mark_failed, type(e).__name__)
            return
        finally:
            self.progress.pop(doc_id, None)
        try:
            rows = json.loads(result_path.read_text(encoding="utf-8"))["rows"]
            self.store.create(doc_id, self.header_fn(d / "input.pdf"), {"rows": rows})
        except Exception as e:
            # The journal is mandatory (decision 30): a drawing that cannot be
            # reviewed durably is never shown as ready.
            log.warning("review record failed for %s: %s", doc_id[:8], type(e).__name__)
            self._settle(doc_id, self.repo.mark_failed, f"review_store:{type(e).__name__}")
            return
        self._settle(doc_id, self.repo.mark_ready)

    def _settle(self, doc_id, transition, *args) -> None:
        try:
            transition(doc_id, *args)
        except InvalidTransition:
            # Cancelled while it ran: the user's decision stands.
            log.info("document %s changed state while extracting; left as is", doc_id[:8])

    # ----- background loop ----------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        # Cross-process guard: a second uvicorn worker would run a second
        # extraction loop on the same GPU and the same queue.
        f = open(self.data.root / "worker.lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise RuntimeError(
                "another process already runs the extraction worker for this data "
                "dir; run uvicorn as a single worker") from None
        self._lockfile = f
        moved = self.repo.requeue_interrupted()
        if moved["requeued"] or moved["failed"]:
            log.info("startup: requeued %d, failed %d interrupted extraction(s)",
                     moved["requeued"], moved["failed"])
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sindri-worker", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception as e:
                log.error("worker loop: %s", type(e).__name__)
                ran = None
            if ran is None:
                self._wake.wait(self.idle_wait)
                self._wake.clear()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            # A drawing mid-extraction is NOT waited for: it stays
            # `extracting` and the next start() requeues it.
            self._thread.join(timeout)
            self._thread = None
        if self._lockfile is not None:
            self._lockfile.close()
            self._lockfile = None
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/batch -q` → all pass.

- [ ] **Step 5: Full suite, commit**
```bash
git add app/batch/worker.py tests/batch/test_worker.py
git commit -m "batch: one background extraction worker with crash requeue and cancel"
```

> **Milestone: core** - state must be checkpointed here before continuing.

---

### Task 5: Runtime, and the API switched to the queue (+ `/open`)

This is the integration task (use a standard/strong model). It replaces `/tmp` sessions, the optional review store and the SSE extraction.

**Files:**
- Create: `app/batch/runtime.py`, `tests/batch/test_runtime.py`
- Modify: `app/main.py`, `tests/conftest.py`, `tests/test_api.py`, `tests/test_api_review.py`

- [ ] **Step 1: Write the failing tests**

`tests/batch/test_runtime.py`:
```python
"""Runtime wiring (batch design §1, decision 30: the journal is mandatory)."""
import os

import pytest

from app.batch.runtime import Runtime


def make(tmp_path, **kw):
    return Runtime(kw.get("data", tmp_path / "data"), kw.get("reviews", tmp_path / "reviews"),
                   extract_fn=lambda *a: None, header_fn=lambda pdf: {})


def test_from_env_keeps_reviews_under_the_data_dir_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("SINDRI_REVIEW_DIR", raising=False)
    rt = Runtime.from_env(lambda *a: None, lambda pdf: {})
    try:
        assert rt.store.root == tmp_path / "d" / "reviews"
        assert rt.data.db_path.is_file()
    finally:
        rt.close()


def test_an_unwritable_review_dir_fails_loudly(tmp_path):
    """The journal is product state now: a deployment that cannot write it
    must fail at boot, not silently review drawings nobody can resume."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    d = tmp_path / "reviews"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        with pytest.raises(OSError):
            make(tmp_path, reviews=d)
    finally:
        os.chmod(d, 0o700)
```

`tests/conftest.py`: replace the `_no_ambient_review_store` fixture with:
```python
@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """Every test gets its own data and review dir. app.main builds its
    Runtime lazily from SINDRI_DATA_DIR / SINDRI_REVIEW_DIR, so without this a
    developer with those exported (e.g. for the real app) would have ordinary
    tests write synthetic drawings and review records into a real -- possibly
    client-data -- directory. Looked up via sys.modules so a module that never
    touches app.main doesn't pay to load the OCR backend."""
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SINDRI_REVIEW_DIR", str(tmp_path / "data" / "reviews"))
    mod = sys.modules.get("app.main")
    if mod is not None:
        monkeypatch.setattr(mod, "_RUNTIME", None)
    yield
    if mod is not None and mod._RUNTIME is not None:
        mod._RUNTIME.close()
```

`tests/test_api.py` — replace the helpers and the tests named below; leave every other test as it is.

Replace `parse_sse` and `upload_pdf` with:
```python
def upload_pdf(test_client, path, filename="sample.pdf"):
    """Upload, queue and extract a PDF through the background worker -- run
    inline, so the test is deterministic -- then open it for review. Returns
    the /open payload (session_id, image_url, rows, notes, marks,
    title_block, writer, events, last_seq)."""
    import app.main as main
    meta = save_pdf(test_client, path, filename)
    sid = meta["session_id"]
    assert test_client.post(f"/api/documents/{sid}/enqueue").status_code == 200
    assert main.runtime().worker.run_once() == sid
    doc = test_client.get(f"/api/documents/{sid}").json()
    assert doc["state"] == "ready", doc
    r = test_client.post(f"/api/documents/{sid}/open")
    assert r.status_code == 200, r.text
    return r.json()
```

Replace `test_an_ambient_review_dir_is_never_written_by_an_ordinary_test` with:
```python
def test_every_test_gets_its_own_data_and_review_dir(tmp_path):
    import app.main as main
    rt = main.runtime()
    assert rt.data.root == tmp_path / "data"
    assert rt.store.root == tmp_path / "data" / "reviews"
```

Replace `test_extract_streams_progress_events` with:
```python
def test_queued_drawings_report_their_queue_position(sample_pdf, stub_backend):
    a = save_pdf(client, sample_pdf)["session_id"]
    b = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{a}/enqueue")
    view = client.post(f"/api/documents/{b}/enqueue").json()
    assert (view["state"], view["queue_position"]) == ("queued", 2)
    assert client.get(f"/api/documents/{a}").json()["queue_position"] == 1
```

Replace `test_extract_without_detection_backend_streams_error` with:
```python
def test_extraction_without_detection_backend_fails_the_document(sample_pdf, monkeypatch):
    import app.main as main

    class ReadOnly:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)
    monkeypatch.setattr("app.main._BACKEND", ReadOnly())
    sid = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    main.runtime().worker.run_once()
    doc = client.get(f"/api/documents/{sid}").json()
    assert (doc["state"], doc["error_type"]) == ("failed", "RuntimeError")
    assert client.post(f"/api/documents/{sid}/open").status_code == 409
```

In `test_extraction_cancels_when_event_set`: replace `import app.main as main` with `from app.batch.worker import Cancelled` and both `main._Cancelled` with `Cancelled`.

Replace `test_upload_returns_metadata_without_extracting` with:
```python
def test_upload_returns_metadata_without_extracting(sample_pdf, stub_backend):
    import app.main as main
    meta = save_pdf(client, sample_pdf)
    assert meta["fileName"] == "sample.pdf" and meta["pages"] >= 1
    work = main.runtime().data.doc_dir(meta["session_id"])
    assert (work / "input.pdf").is_file()
    assert not (work / "page.png").exists()
    assert client.get(f"/api/documents/{meta['session_id']}").json()["state"] == "staged"
```

Replace `test_extract_rejects_bad_session_id` and `test_extract_unknown_session_returns_404` with:
```python
@pytest.mark.parametrize("method,route", [
    ("post", "enqueue"), ("post", "open"), ("post", "cancel"), ("post", "retry")])
def test_document_routes_reject_a_malformed_id(method, route):
    assert getattr(client, method)(f"/api/documents/not-a-valid-uuid/{route}").status_code == 404


def test_an_unknown_document_is_404():
    assert client.get("/api/documents/" + "0" * 32).status_code == 404


def test_cancel_and_retry_a_queued_drawing(sample_pdf, stub_backend):
    import app.main as main
    sid = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    assert client.post(f"/api/documents/{sid}/cancel").json()["state"] == "cancelled"
    assert main.runtime().worker.run_once() is None
    assert client.post(f"/api/documents/{sid}/retry").json()["state"] == "queued"
    assert main.runtime().worker.run_once() == sid
```

In `test_read_region_sets_needs_review_on_empty_read` and `test_read_region_applies_active_flag_rules_like_the_pipeline`, replace `work = main._session_dir(session)` with `work = main._doc_dir(session)`.

Replace `test_delete_session_removes_dir`, `test_delete_missing_session_is_idempotent` and `test_delete_rejects_bad_session_id` with:
```python
def test_delete_document_removes_its_files_and_row(sample_pdf):
    import app.main as main
    sid = save_pdf(client, sample_pdf)["session_id"]
    work = main.runtime().data.doc_dir(sid)
    assert work.is_dir()
    assert client.delete(f"/api/documents/{sid}").status_code == 200
    assert not work.exists()
    assert client.get(f"/api/documents/{sid}").status_code == 404


def test_delete_missing_document_is_idempotent():
    assert client.delete("/api/documents/" + "0" * 32).status_code == 200


def test_delete_rejects_bad_document_id():
    assert client.delete("/api/documents/not-a-valid-uuid").status_code == 404
```

In `test_export_includes_title_block_sheet`, replace `(main._SESSIONS / session_id).mkdir(parents=True, exist_ok=True)` with `main.runtime().data.doc_dir(session_id).mkdir(parents=True, exist_ok=True)` (Task 7 rewrites this test).

`tests/test_api_review.py` — rewrite the head and the tests named below:
```python
"""Review capture through the API (HITL design §2; batch design §3-§4: the
journal is mandatory product state)."""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.pipeline.detect import Detection
from tests.conftest import StubVLMBackend
from tests.test_api import save_pdf, upload_pdf

client = TestClient(app)


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1")
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


def extract(sample_pdf):
    return upload_pdf(client, sample_pdf)


def test_extract_writes_the_proposal_server_side(sample_pdf, stub_backend):
    result = extract(sample_pdf)
    d = main.runtime().store.root / result["session_id"]
    proposal = json.loads((d / "proposal.json").read_text())
    assert [r["id"] for r in proposal["rows"]] == [r["id"] for r in result["rows"]]
    assert json.loads((d / "header.json").read_text())["review_schema_version"] == 1
    assert result["writer"] and result["events"] == [] and result["last_seq"] == 0


def test_consent_comes_from_the_environment(sample_pdf, stub_backend, monkeypatch):
    monkeypatch.setenv("SINDRI_REVIEW_CONSENT", "1")
    result = extract(sample_pdf)
    header = json.loads((main.runtime().store.root / result["session_id"] / "header.json").read_text())
    assert header["consent"] is True


def test_a_review_store_fault_fails_the_drawing_and_retry_skips_the_gpu(
        sample_pdf, stub_backend, monkeypatch):
    calls = {"n": 0}
    real_extract = main.extract
    real_build_header = main.build_header

    def counting(*a, **k):
        calls["n"] += 1
        return real_extract(*a, **k)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(main, "extract", counting)
    monkeypatch.setattr(main, "build_header", boom)
    sid = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    main.runtime().worker.run_once()
    doc = client.get(f"/api/documents/{sid}").json()
    assert (doc["state"], doc["error_type"]) == ("failed", "review_store:RuntimeError")
    # NOT monkeypatch.undo(): that would also undo conftest's per-test dirs.
    monkeypatch.setattr(main, "build_header", real_build_header)
    client.post(f"/api/documents/{sid}/retry")
    main.runtime().worker.run_once()
    assert client.get(f"/api/documents/{sid}").json()["state"] == "ready"
    assert calls["n"] == 1


def test_health_reports_the_worker():
    assert "worker_running" in client.get("/api/health").json()
```

In the remaining tests of this file, drop the `store` fixture argument; in `test_events_round_trip_and_seal` expect `r.json() == {"revision": 1, "replay_ok": True}`. Delete `test_extract_without_a_store_still_works_and_says_logging_is_off`, `test_health_reports_review_logging`, `test_a_review_capture_fault_does_not_fail_the_extraction`, `test_events_without_a_store_reports_logging_off`, `test_seal_without_a_store_reports_logging_off` and `test_review_store_from_env_refuses_an_unwritable_dir` (superseded by `tests/batch/test_runtime.py`).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/batch/test_runtime.py tests/test_api.py tests/test_api_review.py -q`
Expected: FAIL (`No module named 'app.batch.runtime'`, missing routes).

- [ ] **Step 3: Implement**

`app/batch/runtime.py`:
```python
"""Wires a deployment together (batch design §1): data dir, index, review
store and the extraction worker."""
import os
from pathlib import Path

from app.batch.db import connect, migrate
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Worker
from app.review.store import ReviewStore


def _probe_writable(root: Path) -> None:
    # mkdir on an existing read-only mount succeeds silently; only a real
    # write proves the journal can be kept -- and it is mandatory now
    # (decision 30), so failing here fails the boot, loudly.
    probe = root / f".write-probe-{os.getpid()}"
    probe.write_text("", encoding="utf-8")
    probe.unlink()


class Runtime:
    def __init__(self, data_dir, review_dir, extract_fn, header_fn):
        self.data = DataDir(data_dir)
        self.conn = connect(self.data.db_path)
        migrate(self.conn)
        self.repo = Repo(self.conn)
        self.store = ReviewStore(Path(review_dir))
        _probe_writable(self.store.root)
        self.worker = Worker(self.repo, self.data, self.store, extract_fn, header_fn)

    @classmethod
    def from_env(cls, extract_fn, header_fn) -> "Runtime":
        data = Path(os.environ.get("SINDRI_DATA_DIR") or "/data")
        reviews = Path(os.environ.get("SINDRI_REVIEW_DIR") or data / "reviews")
        return cls(data, reviews, extract_fn, header_fn)

    def start(self) -> None:
        self.worker.start()

    def close(self) -> None:
        self.worker.stop()
        self.conn.close()
```

`app/main.py` — apply these edits:

1. Imports: remove `asyncio`, `queue`, `re`, `tempfile`, `threading`, `StreamingResponse`, `ReviewStore`; add `hashlib`, `from contextlib import asynccontextmanager`, `from app.batch.repo import BatchError`, `from app.batch.runtime import Runtime`; keep `ReviewError` from `app.review.store`.

2. Delete `_SESSIONS`, `_SESSION_RE`, `_session_dir`, `_review_store_from_env`, `_REVIEW_STORE`, `_Cancelled`, the whole `extract_endpoint`, and `delete_session`. Move `app = FastAPI(...)` below the new runtime block.

3. After `_BACKEND = get_backend()` and `_review_consent`, add:
```python
def _extract(pdf_path, work_dir, progress):
    # Late-bound on purpose: tests monkeypatch `extract` and `_BACKEND` on
    # this module, and the worker must see those patches.
    return extract(pdf_path, work_dir=work_dir, dpi=300, backend=_BACKEND,
                   progress=progress)


def _header(pdf_path):
    return build_header(pdf_path, _review_consent())


_RUNTIME: Optional[Runtime] = None


def runtime() -> Runtime:
    """Built on first use from SINDRI_DATA_DIR / SINDRI_REVIEW_DIR. Lazy, so
    importing this module (every API test does) never touches a real data
    dir; tests/conftest.py points both at a per-test tmp dir."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime.from_env(_extract, _header)
    return _RUNTIME


@asynccontextmanager
async def lifespan(app):
    global _RUNTIME
    rt = runtime()   # a bad data/review dir fails the boot, not the first drawing
    rt.start()
    try:
        yield
    finally:
        rt.close()
        _RUNTIME = None


app = FastAPI(title="Sindri", lifespan=lifespan)


@app.exception_handler(BatchError)
@app.exception_handler(ReviewError)
async def _domain_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def _doc_dir(doc_id: str) -> Path:
    try:
        return runtime().data.doc_dir(doc_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="unknown document")


def _doc_view(doc_id: str) -> dict:
    rt = runtime()
    doc = rt.repo.get(doc_id)
    return {"id": doc["id"], "file_name": doc["file_name"], "pages": doc["pages"],
            "state": doc["state"], "error_type": doc["error_type"],
            "final_revision": doc["final_revision"],
            "exported_revision": doc["exported_revision"],
            "queue_position": rt.repo.queue_position(doc_id),
            "progress": rt.worker.progress.get(doc_id)}
```

4. `health()`: replace the `review_logging` line with `status["worker_running"] = runtime().worker.running`.

5. Replace `upload` with:
```python
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Store a PDF as a staged drawing -- a batch of one until B2 brings real
    batches. Extraction is queued separately and runs in the background
    worker, so no browser has to stay open (batch design §1)."""
    rt = runtime()
    doc_id = uuid.uuid4().hex
    work = rt.data.doc_dir(doc_id)
    work.mkdir(parents=True)
    pdf_path = work / "input.pdf"
    digest = hashlib.sha256()
    with open(pdf_path, "wb") as out:
        while chunk := await file.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    try:
        with fitz.open(pdf_path) as doc:
            pages = doc.page_count
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=400, detail="could not read the PDF")
    name = file.filename or "drawing.pdf"
    rt.repo.create_document(doc_id, name, pages, digest.hexdigest())
    return {"session_id": doc_id, "fileName": name, "pages": pages}
```

6. Add the document routes after `upload`:
```python
@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    _doc_dir(doc_id)
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/enqueue")
def enqueue_document(doc_id: str):
    _doc_dir(doc_id)
    rt = runtime()
    rt.repo.enqueue(doc_id)
    rt.worker.wake()
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/cancel")
def cancel_document(doc_id: str):
    """Queued: never runs. Extracting: the worker unwinds at its next
    progress report (it reads this state, not a flag)."""
    _doc_dir(doc_id)
    runtime().repo.mark_cancelled(doc_id)
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/retry")
def retry_document(doc_id: str):
    _doc_dir(doc_id)
    rt = runtime()
    rt.repo.retry(doc_id)
    rt.worker.wake()
    return _doc_view(doc_id)


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    """Remove a drawing that is not being extracted. Its review record stays
    (review records are never removed with the files). Idempotent."""
    work = _doc_dir(doc_id)
    runtime().repo.delete(doc_id)
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/open")
def open_document(doc_id: str):
    """Everything the browser needs to rebuild the review exactly where it
    stopped, plus a fresh writer token: whichever tab held the old one is
    refused from now on (batch design §3)."""
    work = _doc_dir(doc_id)
    rt = runtime()
    doc = rt.repo.get(doc_id)
    if doc["state"] not in ("ready", "in_review", "finished"):
        raise HTTPException(status_code=409, detail=f"drawing is {doc['state']}, not ready for review")
    result = json.loads((work / "result.json").read_text(encoding="utf-8"))
    opened = rt.store.open(doc_id)
    return {"session_id": doc_id, "image_url": f"/api/image/{doc_id}",
            "fileName": doc["file_name"], "state": doc["state"],
            "final_revision": doc["final_revision"],
            "rows": opened["proposal"], "notes": result["notes"],
            "marks": result["marks"], "title_block": result["title_block"],
            "writer": opened["writer"], "events": opened["events"],
            "last_seq": opened["last_seq"]}
```

7. `post_events` and `seal_session`: replace the `_session_dir(...)` call with `_doc_dir(...)`, delete the `if _REVIEW_STORE is None` branches and the `try/except ReviewError` wrappers (the exception handler maps them), and use `runtime().store`. `post_events` returns `{"contiguous": ...}`; `seal_session` returns `out`.

8. `image`, `read_region`, `export`, `export_pdf`: replace `_session_dir(` with `_doc_dir(`.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/batch tests/test_api.py tests/test_api_review.py -q` → all pass. Then `python -m pytest -q` → all pass. Then grep that nothing references the removed names:
Run: `grep -rn "_REVIEW_STORE\|_SESSIONS\|_session_dir\|/api/extract" app tests`
Expected: no output.

- [ ] **Step 5: Commit**
```bash
git add app/batch/runtime.py app/main.py tests/batch/test_runtime.py tests/conftest.py tests/test_api.py tests/test_api_review.py
git commit -m "api: extraction runs in the background worker; drawings reopen from the server"
```

---

### Task 6: The journal drives the drawing's state

**Files:**
- Modify: `app/main.py` (`post_events`, `seal_session`)
- Test: `tests/test_api_review.py` (append)

- [ ] **Step 1: Write the failing tests**
```python
def doc_state(sid):
    return client.get(f"/api/documents/{sid}").json()


def test_a_first_review_event_moves_ready_into_review(sample_pdf, stub_backend):
    result = extract(sample_pdf)
    sid, writer = result["session_id"], result["writer"]
    client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 1, "type": "accept", "ids": []}]})
    assert doc_state(sid)["state"] == "in_review"


def test_seal_finishes_the_drawing_and_a_later_edit_reopens_it(sample_pdf, stub_backend):
    result = extract(sample_pdf)
    sid, writer, rows = result["session_id"], result["writer"], result["rows"]
    r = client.post(f"/api/session/{sid}/seal", json={
        "writer": writer, "final_seq": 0, "rows": rows, "reviewed_ids": []})
    assert r.status_code == 200
    d = doc_state(sid)
    assert (d["state"], d["final_revision"]) == ("finished", 1)
    reopened = client.post(f"/api/documents/{sid}/open").json()
    client.post(f"/api/session/{sid}/events", json={"writer": reopened["writer"], "events": [
        {"seq": 1, "type": "accept", "ids": []}]})
    assert doc_state(sid)["state"] == "in_review"
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_api_review.py -q` → the 2 new tests FAIL (state stays `ready`).

- [ ] **Step 3: Implement** in `app/main.py`:

`post_events` body becomes:
```python
    _doc_dir(session_id)
    rt = runtime()
    contiguous = rt.store.append(session_id, req.writer, req.events)
    seqs = [e["seq"] for e in req.events if isinstance(e, dict) and isinstance(e.get("seq"), int)]
    if seqs:
        rt.repo.events_appended(session_id, max(seqs))
    return {"contiguous": contiguous}
```
`seal_session` body becomes:
```python
    _doc_dir(session_id)
    rt = runtime()
    out = rt.store.seal(
        session_id, req.writer, req.final_seq,
        [r.model_dump(mode="json") for r in req.rows], req.reviewed_ids)
    rt.repo.mark_finished(session_id, out["revision"], req.final_seq)
    return out
```

- [ ] **Step 4: Run** — `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**
```bash
git add app/main.py tests/test_api_review.py
git commit -m "api: review events and seals move the drawing through review and finish"
```

---

### Task 7: Export only from a sealed revision

**Files:**
- Modify: `app/main.py`, `tests/test_api.py`

- [ ] **Step 1: Write the failing tests** — in `tests/test_api.py`, delete `test_export_xlsx_roundtrip`, `test_export_pdf_roundtrip`, `test_export_rejects_path_traversal_session_id`, `test_export_includes_title_block_sheet`, `test_export_request_accepts_marks_field` and `test_exports_drop_unconfirmed_suggestions_server_side`, and add:
```python
def finished(test_client, sample_pdf, rows_fn=lambda rows: rows):
    """Extract, then seal revision 1 with `rows_fn(rows)` as the final rows."""
    up = upload_pdf(test_client, sample_pdf)
    rows = rows_fn(up["rows"])
    r = test_client.post(f"/api/session/{up['session_id']}/seal", json={
        "writer": up["writer"], "final_seq": 0, "rows": rows,
        "reviewed_ids": []})
    assert r.status_code == 200, r.text
    return up["session_id"]


def test_export_xlsx_from_the_sealed_revision(sample_pdf, stub_backend):
    sid = finished(client, sample_pdf)
    r = client.get(f"/api/documents/{sid}/export/xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert 'filename="sample.xlsx"' in r.headers["content-disposition"]
    assert client.get(f"/api/documents/{sid}").json()["exported_revision"] == 1


def test_export_pdf_from_the_sealed_revision(sample_pdf, stub_backend):
    sid = finished(client, sample_pdf)
    r = client.get(f"/api/documents/{sid}/export/pdf?rev=1")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert 'filename="sample_ballooned.pdf"' in r.headers["content-disposition"]


def test_an_unfinished_drawing_cannot_be_exported(sample_pdf, stub_backend):
    sid = upload_pdf(client, sample_pdf)["session_id"]
    assert client.get(f"/api/documents/{sid}/export/xlsx").status_code == 409


def test_export_rejects_an_unknown_kind_and_a_malformed_id(sample_pdf, stub_backend):
    sid = finished(client, sample_pdf)
    assert client.get(f"/api/documents/{sid}/export/docx").status_code == 404
    assert client.get("/api/documents/not-a-valid-uuid/export/xlsx").status_code == 404


def test_a_repeated_export_is_byte_identical(sample_pdf, stub_backend):
    """Rendered once per revision and cached, so the single export and B4's
    mass export of the same revision are the same bytes."""
    sid = finished(client, sample_pdf)
    a = client.get(f"/api/documents/{sid}/export/xlsx").content
    b = client.get(f"/api/documents/{sid}/export/xlsx").content
    assert a == b


def test_export_carries_the_title_block(sample_pdf, monkeypatch, tmp_path):
    from openpyxl import load_workbook
    from app.models import Characteristic, ExtractionResult, TitleField
    import app.main as main
    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, id="a", nominal="20")],
        title_block=[TitleField(label="Sheet / Blatt", label_en="Sheet",
                                label_de="Blatt", value="1/1")]))
    sid = finished(client, sample_pdf)
    out = tmp_path / "got.xlsx"
    out.write_bytes(client.get(f"/api/documents/{sid}/export/xlsx").content)
    wb = load_workbook(out)
    assert wb["Title Block"].cell(2, 3).value == "1/1"


@pytest.mark.parametrize("kind", ["xlsx", "pdf"])
def test_exports_drop_unconfirmed_suggestions_server_side(
        kind, sample_pdf, stub_backend, monkeypatch):
    """Defence in depth: a suggested row in the sealed revision is still not
    exported. An unconfirmed value in the client's Excel or ballooned PDF is
    exactly the failure the phantom drops exist to prevent."""
    import app.main as main
    seen = {}

    def fake_xlsx(rows, out, **kw):
        seen["rows"] = list(rows)
        out.write_bytes(b"x")

    def fake_pdf(src, rows, dpi, out_path):
        seen["rows"] = list(rows)
        out_path.write_bytes(b"%PDF")

    monkeypatch.setattr(main, "write_workbook", fake_xlsx)
    monkeypatch.setattr(main, "render_ballooned_pdf", fake_pdf)
    sid = finished(client, sample_pdf,
                   lambda rows: rows + [{**rows[0], "id": "s", "suggested": True}])
    assert client.get(f"/api/documents/{sid}/export/{kind}").status_code == 200
    assert seen["rows"] and all(not r.suggested for r in seen["rows"])
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_api.py -q` → new tests FAIL (404 on `/export`).

- [ ] **Step 3: Implement** in `app/main.py`: delete `ExportRequest`, `export` and `export_pdf` (keep `from app.models import Characteristic, NoteBlock, MarkBlock, TitleField` — `_render_export` uses all four). Add after `_exportable`:
```python
_EXPORT_KINDS = {
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "pdf": ("application/pdf", "_ballooned.pdf"),
}


def _render_export(work: Path, rev: int, kind: str, sealed: dict) -> Path:
    """Render revision `rev` once and cache it, so a repeated or mass export
    of the same revision is the same bytes (batch design §4). The rows come
    from the SEALED revision, never from a browser."""
    out = work / "exports" / f"r{rev}.{kind}"
    if out.is_file():
        return out
    out.parent.mkdir(exist_ok=True)
    rows = _exportable([Characteristic.model_validate(r) for r in sealed["rows"]])
    # Temp name keeps the real extension: openpyxl and PyMuPDF both choose
    # behaviour by it. Renamed into place only once complete.
    tmp = out.with_name(f"r{rev}.tmp.{kind}")
    if kind == "xlsx":
        result = json.loads((work / "result.json").read_text(encoding="utf-8"))
        write_workbook(
            rows, tmp,
            notes=NoteBlock.model_validate(result["notes"]) if result["notes"] else None,
            marks=MarkBlock.model_validate(result["marks"]) if result["marks"] else None,
            title_block=[TitleField.model_validate(t) for t in result["title_block"]])
    else:
        render_ballooned_pdf(work / "input.pdf", rows, dpi=300, out_path=tmp)
    tmp.replace(out)
    return out


@app.get("/api/documents/{doc_id}/export/{kind}")
def export_document(doc_id: str, kind: str, rev: Optional[int] = None):
    work = _doc_dir(doc_id)
    if kind not in _EXPORT_KINDS:
        raise HTTPException(status_code=404, detail="unknown export kind")
    rt = runtime()
    doc = rt.repo.get(doc_id)
    rev = rev or doc["final_revision"]
    if not rev:
        raise HTTPException(status_code=409, detail="finish the review before exporting")
    sealed = rt.store.revision(doc_id, rev)
    path = _render_export(work, rev, kind, sealed)
    rt.repo.mark_exported(doc_id, rev)
    media, suffix = _EXPORT_KINDS[kind]
    return FileResponse(path, media_type=media,
                        filename=Path(doc["file_name"]).stem + suffix)
```

- [ ] **Step 4: Run** — `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**
```bash
git add app/main.py tests/test_api.py
git commit -m "api: export renders from the sealed revision, never from browser rows"
```

> **Milestone: api** - state must be checkpointed here before continuing.

---

### Task 8: Rebuild a review in the browser from the server (resume parity)

**Files:**
- Modify: `app/static/js/state.js`, `app/static/js/journal.js`
- Modify: `tests/js/fuzz_journal.mjs`, `tests/review/test_journal_replay_parity.py`
- Test: `tests/js/journal.test.mjs` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/journal.test.mjs`:
```js
test('restoreSession replays server events silently and keeps undo working', () => {
  load([row('a'), row('b')]);
  const emitted = [];
  const off = S.on('op', (e) => emitted.push(e));
  const pairs = S.restoreSession(
    { session_id: 's', image_url: '', rows: [row('a'), row('b')], notes: null },
    [{ seq: 1, type: 'edit_cell', id: 'a', field: 'nominal', old: '1', new: '9' },
     { seq: 4, type: 'accept', ids: ['a'] }]);
  off();
  assert.equal(emitted.length, 0);                 // already journaled: never re-sent
  assert.deepEqual(pairs.map(([, s]) => s), [1, 4]);
  assert.equal(S.state.rows.find((r) => r.id === 'a').nominal, '9');
  assert.equal(S.state.rows.find((r) => r.id === 'a').reviewed, true);
  assert.equal(S.canUndo(), true);
  S.undo();
  assert.equal(S.state.rows.find((r) => r.id === 'a').reviewed, false);
});

test('resume continues numbering and retracts a restored op by its server seq', () => {
  load([row('a')]);
  const pairs = S.restoreSession(
    { session_id: 's', image_url: '', rows: [row('a')], notes: null },
    [{ seq: 3, type: 'accept', ids: ['a'] }]);
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.resume('s', 'w', 5, pairs);
  const off = S.on('op', (e) => j.record(e));
  S.undo();
  S.apply(S.opBulkReview(['a'], true));
  off();
  assert.deepEqual(j.pending.map((e) => [e.seq, e.type, e.target]),
                   [[6, 'retract', 3], [7, 'accept', undefined]]);
  assert.equal(j.acked, 5);
});
```
(`createJournal` is already imported in that file; if not, add `const { createJournal } = await import('../../app/static/js/journal.js');`.)

In `tests/js/fuzz_journal.mjs`, add a resume mode. After the `pick` helper add:
```js
// Mirror of app/review/journal.py net_events, for the simulated reload.
function netEvents(events) {
  const live = new Map();
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    if (e.type === 'retract') live.delete(e.target);
    else live.set(e.seq, e);
  }
  return [...live.values()];
}
const RESUME = process.argv[4] === 'resume';
```
Wrap the existing random-step loop body in a function `function randomSteps(steps, ids, log)` (same body, uses the module-level `S`, `rnd`, `pick`, `addN`) and, in the per-case loop, replace the inline loop with:
```js
  randomSteps(steps, ids, log);
  let events = j.pending;
  let restoredRows = null;
  let restoredReviewed = null;
  if (RESUME) {
    // Simulated reload: rebuild from what the server holds, then keep going
    // -- including undos that reach back into restored operations.
    off();
    const before = j.pending;
    const pairs = S.restoreSession(
      { session_id: 's' + c, image_url: '', rows: JSON.parse(JSON.stringify(proposal)), notes: null },
      netEvents(before));
    restoredRows = JSON.parse(JSON.stringify(S.state.rows));
    restoredReviewed = S.state.rows.filter((r) => r.reviewed).map((r) => r.id);
    const j2 = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0, wallNow: () => '' });
    j2.resume('s' + c, 'w', j.lastSeq, pairs);
    const off2 = S.on('op', (e) => j2.record(e));
    log.push('--reload--');
    randomSteps(2 + Math.floor(rnd() * 10), ids, log);
    off2();
    events = [...before, ...j2.pending];
  } else {
    off();
  }
```
Declare `let before = j.pending;` next to `let events` (so it exists in both modes) and drop the `const` from `const before` inside the `if`. Push `events`, `events_before_reload: before`, `restored_rows: restoredRows`, `restored_reviewed: restoredReviewed` in the case record (replacing `events: j.pending`). Remove the original standalone `off();` call that followed the loop. The non-resume mode's output is otherwise unchanged, so the existing parity test keeps working.

In `tests/review/test_journal_replay_parity.py` add:
```python
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_review_rebuilt_from_the_server_matches_and_keeps_replaying():
    """Batch design §3: reopening a drawing rebuilds the review through
    state.js from the proposal + net events. The rebuilt state must equal what
    the server replays, and edits after the reload -- undos of restored
    operations included -- must still replay to the final rows."""
    r = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "fuzz_journal.mjs"), str(SEED), str(N_SESSIONS), "resume"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    cases = json.loads(r.stdout)
    assert len(cases) == N_SESSIONS
    for c in cases:
        for e in c["events"]:
            _check_event_shape(e)
            validate_event(e)
        # 1. what the browser rebuilt equals the server's replay of what it held
        rebuilt = mismatches(replay(c["proposal"], net_events(c["events_before_reload"])),
                             c["restored_rows"], c["restored_reviewed"])
        assert rebuilt == [], (c["case"], "rebuild", rebuilt, c["log"])
        # 2. edits after the reload (undos of restored ops included) still replay
        final = mismatches(replay(c["proposal"], net_events(c["events"])),
                           c["rows"], c["reviewed_ids"])
        assert final == [], (c["case"], "after reload", final, c["log"])
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_js_state.py tests/review/test_journal_replay_parity.py -q` → FAIL (`S.restoreSession is not a function`).

- [ ] **Step 3: Implement**

`app/static/js/state.js`, after `opBulkReview`:
```js
// ===== Rebuilding a review from the server (batch design §3) ==========
// An op from its journal event: the same constructors the reviewer's own
// actions use, so a restored op undoes and redoes exactly like a live one.
export function opFromEvent(e) {
  switch (e.type) {
    case 'add_row': return opAddRow(JSON.parse(JSON.stringify(e.row)));
    case 'delete_row': return opDeleteRow(e.id);
    case 'move_row': return opMoveRow(e.id, e.xy);
    case 'edit_cell': return opEditCell(e.id, e.field, e.new);
    case 'accept': return opBulkReview(e.ids, true);
    case 'unaccept': return opBulkReview(e.ids, false);
    case 'confirm_suggestions': return opConfirmSuggestions(e.ids);
    default: throw new Error(`cannot restore a ${e.type} event`);
  }
}

// Load the proposal, then replay the server's NET events. Silently: they
// are already in the journal, so nothing goes out on the 'op' bus. Each op
// lands on the undo stack, so undo reaches back across a reload; redo
// history is not restored. Returns [op, seq] pairs for journal.resume().
export function restoreSession(payload, events) {
  setSession(payload);
  const pairs = [];
  for (const e of events) {
    const op = opFromEvent(e);
    op.do();
    undoStack.push(op);
    pairs.push([op, e.seq]);
  }
  emit('change');
  emit('history');
  return pairs;
}
```

`app/static/js/journal.js`: in `start()` replace the comment block above `gen++` with:
```js
    // A fresh session numbers from 1; a reopened one goes through resume().
```
and add after `start`:
```js
  // Continue a reopened session (batch design §3): number after the
  // server's last seq, treat everything up to it as acknowledged, and map
  // each restored op to its server seq so undoing it retracts the right
  // event.
  function resume(sid, w, lastSeq, opSeqs = []) {
    start(sid, w);
    seq = lastSeq;
    acked = lastSeq;
    for (const [op, s] of opSeqs) seqOf.set(op, s);
  }
```
and add `resume,` to the returned object after `start,`.

- [ ] **Step 4: Run** — `python -m pytest tests/test_js_state.py tests/review/test_journal_replay_parity.py -q` → pass; then `python -m pytest -q`.

- [ ] **Step 5: Commit**
```bash
git add app/static/js/state.js app/static/js/journal.js tests/js/journal.test.mjs tests/js/fuzz_journal.mjs tests/review/test_journal_replay_parity.py
git commit -m "ui: rebuild a review from the server's journal, with undo across the reload"
```

---

### Task 9: The journal is mandatory in the client

**Files:**
- Modify: `app/static/js/journal.js`, `app/static/js/finish.js`, `app/static/js/api.js`
- Test: `tests/js/journal.test.mjs`, `tests/js/finish.test.mjs`

- [ ] **Step 1: Write the failing tests**

`tests/js/finish.test.mjs`: delete the two tests `'logging switches off during the flush: …'` and `'logging off: no flush-gated seal, exports still run'`; change the harness's `exportAll` to record its arguments:
```js
    exportAll: async (s, revision) => {
      calls.exports++;
      calls.exportedRevision = revision;
      if (fails > 0) { fails--; throw new Error('export failed'); }
    },
```
and the seal stub's return to `{ revision: ++rev, replay_ok: true }`. Add:
```js
test('a drawing without a review log cannot be finished', async () => {
  const { finisher, calls } = harness({ journal: fakeJournal({ active: false }) });
  await assert.rejects(finisher.run(snap()), /reopen/);
  assert.equal(calls.seals.length, 0);
  assert.equal(calls.exports, 0);
});

test('export receives the revision it must render', async () => {
  const { finisher, calls } = harness();
  await finisher.run(snap());
  assert.equal(calls.exportedRevision, 1);
});
```

`tests/js/journal.test.mjs`: delete `'a null ack with review_logging:false switches logging off instead of throwing'` and `'onLoggingOff defaults to a no-op when the caller does not pass one'`; rename `'a plain malformed ack (no review_logging:false) still rejects'` to `'a null ack rejects'` (body unchanged).

- [ ] **Step 2: Run** — `python -m pytest tests/test_js_state.py -q` → FAIL (`a drawing without a review log` resolves instead of rejecting; `exportedRevision` undefined).

- [ ] **Step 3: Implement**

`finish.js` — replace the body of `run` between `running = true; try {` and `} finally {` with:
```js
      const { sessionId, rows, reviewedIds } = snapshot;
      if (sealed && sealed.sessionId !== sessionId) sealed = null;
      // The journal is mandatory (batch design decision 30): a review the
      // server cannot replay is never exported.
      if (!journal.active) throw new Error('This drawing has no review log — reopen it to continue.');
      // Read once, with the snapshot: rows and final_seq must describe the
      // same moment. An edit made mid-run lands above it, and the server
      // refuses that seal rather than recording a replay mismatch.
      const seq = journal.lastSeq;
      const acked = await journal.flush();
      if (acked < seq) throw new Error('The review log is still saving — try again in a moment.');
      let revision;
      if (sealed && sealed.seq === seq) {
        revision = sealed.revision;
      } else {
        const res = await seal(sessionId, {
          writer: journal.writer, final_seq: seq, rows, reviewed_ids: reviewedIds,
        });
        revision = res.revision;
        sealed = { sessionId, seq, revision };
      }
      await exportAll(snapshot, revision);
      return { status: 'done', revision };
```
Update the file's header comment: "Finish & Export … flush the journal, seal the review, export THAT revision from the server."

`journal.js` — remove the `onLoggingOff` parameter and the whole `if (review_logging === false) { … }` block; the `.then` destructures `({ contiguous })` only.

`api.js` — delete `runExtraction`, `parseEvent`, `deleteSession`, `exportFile`; add:
```js
async function jsonOrThrow(res, fallback) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: fallback }));
    throw new Error(err.detail || fallback);
  }
  return res.json();
}

const docUrl = (id, tail = '') => `/api/documents/${id}${tail}`;

export const getDocument = async (id) => jsonOrThrow(await fetch(docUrl(id)), 'Drawing not found');
export const enqueueDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/enqueue'), { method: 'POST' }), 'Could not queue the drawing');
export const cancelDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/cancel'), { method: 'POST' }), 'Could not cancel');
export const retryDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/retry'), { method: 'POST' }), 'Could not retry');
export const openDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/open'), { method: 'POST' }), 'Could not open the drawing');

// Fire-and-forget removal of a staged drawing the user backed out of.
export async function deleteDocument(id) {
  if (!id) return;
  try { await fetch(docUrl(id), { method: 'DELETE' }); } catch { /* best effort */ }
}

// Download a sealed revision's file; the server names it after the drawing.
export async function downloadExport(id, kind, revision) {
  const res = await fetch(docUrl(id, `/export/${kind}?rev=${revision}`));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Export failed' }));
    throw new Error(err.detail || 'Export failed');
  }
  const name = /filename="?([^"]+)"?/.exec(res.headers.get('content-disposition') || '')?.[1]
    || `drawing.${kind}`;
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
```
`main.js` still imports the deleted names after this commit, so the browser UI is broken between Task 9 and Task 10. That is accepted: the suite does not load `main.js`, and Task 10 follows immediately. Never deploy between the two.

- [ ] **Step 4: Run** — `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**
```bash
git add app/static/js/journal.js app/static/js/finish.js app/static/js/api.js tests/js/journal.test.mjs tests/js/finish.test.mjs
git commit -m "ui: the review journal is mandatory; Finish exports the sealed revision"
```

---

### Task 10: Interim UI — queue, wait, open, resume after reload

No node test can load `main.js` (it touches the DOM), so this task is verified by the browser drive in Task 11. Keep it a faithful wiring of tested modules.

**Files:**
- Modify: `app/static/js/main.js`, `app/static/index.html`

- [ ] **Step 1: Edit `index.html`** — delete the `<span class="status-pill warn" id="log-pill" …>…</span>` element.

- [ ] **Step 2: Rewrite the affected parts of `main.js`**

Imports become:
```js
import { state, on, setSession, restoreSession, clearSession, undo, redo,
         canUndo, canRedo, progress, canFinish } from './state.js';
import { savePdf, getDocument, enqueueDocument, cancelDocument, openDocument,
         deleteDocument, downloadExport, health, postEvents, beaconEvents,
         sealSession } from './api.js';
```
Journal and finisher:
```js
const journal = createJournal({
  send: (sid, writer, events) => postEvents(sid, writer, events),
});
let flushTimer = null;
let flushBackoff = 500;

const finisher = createFinisher({
  journal,
  canFinish,
  seal: (sid, body) => sealSession(sid, body),
  exportAll: async (s, revision) => {
    await downloadExport(s.sessionId, 'xlsx', revision);
    await downloadExport(s.sessionId, 'pdf', revision);
  },
});
```
Replace `pendingUpload` / `extractAbort` with:
```js
// The drawing this tab is waiting on (staged or extracting). Extraction runs
// on the server; closing the tab no longer stops it (batch design §1).
let waitingOn = null;     // { id, fileName }
let pollTimer = null;
```
`init()`: add `resumeFromHash();` as the last line. Add:
```js
// #doc=<id> keeps the drawing addressable, so a reload -- or coming back
// tomorrow with the same link -- reopens it where the reviewer stopped.
function setHash(id) { history.replaceState(null, '', id ? `#doc=${id}` : '#'); }
function hashDoc() { return /^#doc=([0-9a-f]{32})$/.exec(location.hash)?.[1] || null; }

async function resumeFromHash() {
  const id = hashDoc();
  if (!id) return;
  try {
    const doc = await getDocument(id);
    routeDocument(doc);
  } catch {
    setHash(null);
  }
}

function routeDocument(doc) {
  waitingOn = { id: doc.id, fileName: doc.file_name };
  if (doc.state === 'staged') showConfirm(doc.file_name, doc.pages);
  else if (doc.state === 'queued' || doc.state === 'extracting') { showExtracting(doc.file_name); pollUntilReady(); }
  else if (['ready', 'in_review', 'finished'].includes(doc.state)) loadForReview(doc.id);
  else {
    toast({ kind: 'error', title: `Drawing ${doc.state}`,
            msg: doc.error_type ? `Extraction failed (${doc.error_type}).` : 'Upload it again to retry.' });
    waitingOn = null;
    setHash(null);
    backToEmpty();
  }
}
```
`wireHeader` file-close handler: confirm text becomes `'Close this drawing? Your review is saved and reopens from its link.'`; after `clearSession();` add `setHash(null);`. Remove both `log-pill` lines from the `on('session', …)` handler.

`handleFile`: replace the `extractAbort` guard with `if (waitingOn && !state.sessionId) { toast(... 'A drawing is already waiting', 'Stop it before opening another drawing.'); return; }`; after `savePdf` succeeds:
```js
    waitingOn = { id: meta.session_id, fileName: meta.fileName || file.name };
    setHash(meta.session_id);
    showConfirm(waitingOn.fileName, meta.pages);
```
`cancelConfirm`:
```js
function cancelConfirm() {
  if (waitingOn) deleteDocument(waitingOn.id);
  waitingOn = null;
  setHash(null);
  backToEmpty();
}
```
Replace `startExtraction` and `stopExtraction` with:
```js
async function startExtraction() {
  if (!waitingOn) return;
  document.getElementById('plan-confirm').hidden = true;
  showExtracting(waitingOn.fileName);
  try {
    await enqueueDocument(waitingOn.id);
    pollUntilReady();
  } catch (err) {
    hideExtracting();
    backToEmpty();
    toast({ kind: 'error', title: 'Could not queue', msg: String(err.message || err) });
  }
}

// Poll, not stream: the run belongs to the server, and a poll has nothing
// to reconnect after a sleep or a dropped network (decision 8).
function pollUntilReady() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!waitingOn) return;
    let doc;
    try {
      doc = await getDocument(waitingOn.id);
    } catch {
      pollUntilReady();
      return;
    }
    if (doc.state === 'queued') {
      document.getElementById('ex-detail').textContent =
        doc.queue_position > 1 ? `Waiting — ${doc.queue_position - 1} drawing(s) ahead` : 'Waiting for the worker…';
      pollUntilReady();
    } else if (doc.state === 'extracting') {
      if (doc.progress) onExtractProgress(doc.progress);
      pollUntilReady();
    } else {
      extractStepsDone();
      hideExtracting();
      routeDocument(doc);
    }
  }, 1500);
}

async function stopExtraction() {
  clearTimeout(pollTimer);
  if (waitingOn) {
    try { await cancelDocument(waitingOn.id); } catch { /* already finished or gone */ }
    deleteDocument(waitingOn.id);
  }
  waitingOn = null;
  setHash(null);
  hideExtracting();
  backToEmpty();
}

async function loadForReview(id) {
  setBusy('Opening drawing…');
  try {
    const data = await openDocument(id);
    // Nothing may run between restoring and resuming: an op applied in
    // between would never be journaled (see the phase-1 note on start()).
    const pairs = restoreSession(data, data.events);
    journal.resume(data.session_id, data.writer, data.last_seq, pairs);
    waitingOn = null;
    setHash(id);
    hideExtracting();
    const n = data.rows.length;
    toast({ kind: 'ok', title: `Loaded ${data.fileName}`,
            msg: data.events.length ? `Review restored (${data.events.length} change${data.events.length === 1 ? '' : 's'})`
                                    : `${n} characteristic${n === 1 ? '' : 's'} extracted` });
  } catch (err) {
    toast({ kind: 'error', title: 'Could not open', msg: String(err.message || err) });
    backToEmpty();
  } finally {
    setIdle();
  }
}
```
`stopExtraction` removes the drawing after cancelling — the interim UI has no list to come back to (B3 adds one).

`setSession` is no longer imported by `main.js` once `restoreSession` is used; drop it from the import if unused.

In `finish()`, the success toast becomes `msg: \`Review sealed as revision r${res.revision}\``.

In `wireJournal`, the 409 "opened elsewhere" case: in `scheduleFlush`'s catch, if `/opened elsewhere/.test(err.message)` then `toast({ kind: 'warn', title: 'Opened in another tab', msg: 'Reload to continue here.' })` and do not reschedule (B3 replaces this with the read-only banner).

- [ ] **Step 3: Run** — `python -m pytest -q` → all pass (static-asset tests still find `/js/main.js`). Then check nothing references removed API names:
Run: `grep -n "runExtraction\|exportFile\|deleteSession\|log-pill\|review_logging" app/static -r`
Expected: no output.

- [ ] **Step 4: Commit**
```bash
git add app/static/js/main.js app/static/index.html
git commit -m "ui: queue a drawing, wait for the worker, reopen it from its link"
```

---

### Task 11: Deployment, docs, and a real-browser check

**Files:**
- Modify: `docker-compose.yml`, `CLAUDE.md`, `docs/plans/2026-10-07-batch-b1-core-plan.state.md`

- [ ] **Step 1: `docker-compose.yml`** — in `environment`, add `- SINDRI_DATA_DIR=/data` above the review dir line, and change the comment to:
```yaml
      # Drawings, the queue index and review records
      # (docs/plans/2026-10-07-batch-async-review-design.md). Client data:
      # never inside the image, never in /tmp. Run a single uvicorn worker.
```

- [ ] **Step 2: `CLAUDE.md` §2** — after the REVIEW CAPTURE paragraph add:
```markdown
**BATCH B1: EXTRACTION RUNS IN THE BACKGROUND** (`docs/plans/2026-10-07-batch-async-review-design.md`).
`app/batch/` (product code; the boundary test forbids `app.eval` imports)
holds a SQLite index under `SINDRI_DATA_DIR` (default `/data`), one
extraction worker thread (single process, enforced by a lock file), and the
`Runtime` that `app.main.runtime()` builds lazily. Upload stages a drawing,
`/api/documents/{id}/enqueue` queues it, the browser polls, and
`/api/documents/{id}/open` rebuilds the review from the server's journal with
a fresh writer token (latest tab wins). **The review journal is mandatory
now** — the "logging off" mode is gone and an unwritable review dir fails the
boot. Exports render from the sealed revision only. `tests/conftest.py` gives
every test its own data and review dir. B2 (import), B3 (work list), B4
(mass export, retention, token) follow, each with its own plan.
```
Also update the suite count line in §2 and §6 to the new total.

- [ ] **Step 3: Full verification**
Run: `python -m pytest -q` → note the count.
Run: `bash ~/.claude/hooks/test-sindri-guard.sh` → `32 passed, 0 failed`.

- [ ] **Step 4: Real-browser drive** (throwaway, in the scratchpad — never committed). Start the app on a stub backend with temp dirs and drive it with Playwright. The script must check:
1. Upload `sample.pdf` → confirm → Start; the overlay shows waiting/extracting, and the drawing loads with rows.
2. Edit one cell and accept one row; wait > 1 s; reload the page → the same drawing reopens from `#doc=…` with the edit and the accept present, and Undo reverts the accept.
3. Open the same `#doc=` link in a second tab, make an edit there; back in the first tab an edit shows the "Opened in another tab" toast.
4. Finish in the second tab (resolve flagged rows first) → two downloads named after the drawing; `GET /api/documents/{id}` says `finished`, `final_revision` 1, `exported_revision` 1.
5. Upload a second drawing, Start, then close the tab immediately; reopen `#doc=<id>` a few seconds later → it is ready (the worker kept going without a browser).

A stub-backend launcher (scratchpad) can be:
```python
import os, sys, tempfile
os.environ["SINDRI_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "<worktree>")
sys.path.insert(0, "<worktree>/tests")
import app.main as main
from app.pipeline.detect import Detection
from conftest import StubVLMBackend
main._BACKEND = StubVLMBackend(detections=[Detection((40, 40, 120, 70), "dimension", 0.9)])
import uvicorn
uvicorn.run(main.app, host="127.0.0.1", port=8765)
```

- [ ] **Step 5: Commit**
```bash
git add docker-compose.yml CLAUDE.md docs/plans/2026-10-07-batch-b1-core-plan.state.md
git commit -m "deploy: data dir for the queue; note batch B1 in CLAUDE.md"
```

> **Milestone: ui** - state must be checkpointed here before continuing.

---

## After B1

Write the B2 plan (chunked resumable upload, ZIP/folder, duplicates, scope badges, staging API) from design §2, against the code B1 actually produced. Things B1 leaves for later on purpose: the work list and review bar (B3), the read-only banner for a replaced tab (B3; B1 shows a toast), pause/move-to-front/re-extract endpoints (B3 — the repo already supports pause), flag counts and the ETA columns (B3 migration 2), mass export, retention and the access token (B4).
