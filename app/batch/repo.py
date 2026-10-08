"""Document lifecycle in the index (batch design §1-§2).

    staged -> queued -> extracting -> ready -> in_review -> finished
                |            |          |         ^            |
                |            |          +---------+-- reopen --+
                |            +-> failed
                +------------+-> cancelled
    failed / cancelled -- retry --> queued

Every guarded transition is ONE UPDATE (`WHERE state IN (...)`), so two
threads racing -- an API cancel against the worker finishing, say -- cannot
both win: the loser's rowcount is 0 and it gets InvalidTransition instead of
silently overwriting the winner. claim_next, requeue_interrupted and delete
read and then write; they are safe because both happen inside one locked
BEGIN IMMEDIATE transaction that no other writer can enter. Rows a method
returns are read inside that same transaction, never re-read afterwards.

Imports (B2): a draft batch receives uploads; a completed upload's documents
are staged in the SAME transaction that marks the upload complete, so a crash
leaves all of them or none. Upload offsets move by a guarded UPDATE on the
old offset, exactly like state transitions."""
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.batch.staging import (batch_view, default_included, out_of_scope, staging_counts,
                               staging_row, upload_view)

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


# A draft IDLE long enough to go (its last change, not its creation: a draft
# emptied today is not abandoned) and empty: no drawing, and no UNFINISHED upload
# (a complete upload row is bookkeeping and goes with it). A draft with
# staged drawings is kept however old: it may be continued later (design §2).
_EMPTY_DRAFT = ("draft = 1 AND COALESCE(updated_at, created_at) < ?"
                " AND NOT EXISTS (SELECT 1 FROM document d WHERE d.batch_id = batch.id)"
                " AND NOT EXISTS (SELECT 1 FROM upload u WHERE u.batch_id = batch.id"
                " AND u.state != 'complete')")


def _now() -> str:
    # Wall-clock UTC. Queue order compares these as STRINGS, so they are fixed
    # width: string order equals time order by construction. Plain isoformat()
    # drops the fraction on a whole second, which today still sorts correctly
    # only because '+' < '.' -- a coincidence, not a guarantee.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Repo:
    def __init__(self, conn, clock=_now):
        self._conn = conn
        self._clock = clock
        # One connection shared by API threads and the worker. A transaction
        # belongs to the CONNECTION, not the thread: without this lock thread
        # B's statements would run inside thread A's open transaction (and
        # commit or roll back with it), and B's BEGIN would fail outright.
        self._lock = threading.Lock()

    @contextmanager
    def _tx(self):
        with self._lock:
            # IMMEDIATE takes the write lock up front, so a claim's SELECT and
            # its UPDATE see the same queue.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                # COMMIT inside the try: if it fails (full disk, I/O error)
                # the transaction is still open, and leaving it open on the
                # shared connection would make every later BEGIN fail.
                self._conn.execute("COMMIT")
            except BaseException:
                # SQLite rolls some errors back by itself; a ROLLBACK with
                # no transaction would raise and mask the original error.
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

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

    def _touch(self, c, batch_id) -> None:
        """A change to a batch's content or name: the empty-draft sweep ages
        a draft from its LAST change (_EMPTY_DRAFT)."""
        c.execute("UPDATE batch SET updated_at = ? WHERE id = ?", (self._clock(), batch_id))

    def now(self) -> datetime:
        """This repo's clock as an aware datetime: the stale sweep's cutoff
        comes from the same clock that stamps updated_at."""
        return datetime.fromisoformat(self._clock())

    @staticmethod
    def _row(c, doc_id) -> dict:
        """The row as seen inside the caller's transaction."""
        return dict(c.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone())

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
        self._touch(c, batch_id)
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

    # ----- reads ---------------------------------------------------------
    def get_batch(self, batch_id) -> dict:
        with self._lock:
            return batch_view(self._batch(self._conn, batch_id))

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

    def batch_document_ids(self, batch_id) -> list:
        """The batch's document ids in staging order; [] for an unknown
        batch: the callers mark files from this snapshot, they do not judge
        (start_batch / delete_batch do, against `expected_ids`)."""
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                "SELECT id FROM document WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()]

    def get_upload(self, upload_id) -> dict:
        with self._lock:
            return self._upload(self._conn, upload_id)

    def stale_uploads(self, cutoff: str) -> list:
        """Uploads still `receiving` and untouched since `cutoff`. Never a
        `completing` one: that is a completion in progress (it holds the
        upload's lock), and at startup recover() has reset it first. A
        listing only: the delete re-checks under the upload's lock."""
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                "SELECT id FROM upload WHERE state = 'receiving' AND updated_at < ?"
                " ORDER BY updated_at, rowid", (cutoff,)).fetchall()]

    def open_upload_ids(self) -> set:
        """Uploads whose part file is still theirs: receiving or completing."""
        with self._lock:
            return {r["id"] for r in self._conn.execute(
                "SELECT id FROM upload WHERE state != 'complete'").fetchall()}

    def has_document(self, doc_id) -> bool:
        """A row in ANY state: whatever its state, the row owns its dir."""
        with self._lock:
            return self._conn.execute("SELECT 1 FROM document WHERE id = ?",
                                      (doc_id,)).fetchone() is not None

    def empty_drafts(self, cutoff: str) -> list:
        """Drafts created before `cutoff` holding no drawing and no
        unfinished upload. A listing only: delete_empty_draft re-checks."""
        with self._lock:
            return [r["id"] for r in self._conn.execute(
                f"SELECT id FROM batch WHERE {_EMPTY_DRAFT} ORDER BY created_at, rowid",
                (cutoff,)).fetchall()]

    def staging(self, batch_id) -> dict:
        """Everything the staging view shows for one batch."""
        with self._lock:
            b = self._batch(self._conn, batch_id)
            rows = self._staging_rows(self._conn, "d.batch_id = ?", (batch_id,))
            uploads = [upload_view(dict(u)) for u in self._conn.execute(
                "SELECT * FROM upload WHERE batch_id = ? ORDER BY created_at, rowid", (batch_id,))]
        return {"batch": batch_view(b), "documents": rows, "uploads": uploads,
                "counts": staging_counts(rows, uploads)}

    def list_documents(self, *, batch_id=None, state=None, search=None,
                       needs_me=False, since=None) -> list:
        """Work-list rows (batch design §3), joined with the batch name and
        ordered by `rowid` -- the true staging/insertion order, and STABLE:
        it never changes once assigned, unlike `updated_at`, which every
        edit on a row (and only that row) moves. The review bar's
        Prev/Skip/Jump (Task 11) and Finish's chain-to-next (Task 13) all
        depend on this list's order meaning "the order drawings were staged
        in", not "the order they were last touched in" -- the latter would
        reorder the whole list out from under the reviewer on every single
        edit. `since` still filters on `updated_at` (that is genuinely when
        a row last changed; `rowid` says nothing about it) -- callers diff
        by it, they do not sort by it. The cursor is INCLUSIVE (`>=`, not
        `>`): a row stamped in the same microsecond as the cursor, or one
        whose write committed after the previous poll's query started but
        was stamped before it, must never be skipped. The caller (the
        `/api/documents` route) is expected to hand back a cursor taken
        BEFORE its own query ran, with a small safety margin subtracted, so
        the next poll's `since` never lands strictly after a row it should
        still see -- and because of that margin a poll will commonly
        re-receive a row it already has. That is harmless: the client
        merges incoming rows by id rather than only appending. `search` is
        a case-insensitive
        substring match on `file_name`; SQLite's default LIKE is
        ASCII-only case-insensitive, which is a known, honest gap for a
        single-operator local tool (a non-ASCII query simply finds fewer
        rows, it does not silently find the wrong ones). `%`/`_` in the
        query are escaped so they match literally rather than as wildcards."""
        clauses, params = [], []
        if batch_id is not None:
            clauses.append("d.batch_id = ?"); params.append(batch_id)
        if state is not None:
            clauses.append("d.state = ?"); params.append(state)
        if needs_me:
            clauses.append("d.state IN ('ready', 'in_review')")
        if search:
            esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("d.file_name LIKE ? ESCAPE '\\'")
            params.append(f"%{esc}%")
        if since is not None:
            clauses.append("d.updated_at >= ?"); params.append(since)
        where = " AND ".join(clauses) if clauses else "1=1"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT d.*, b.name AS batch_name FROM document d"
                f" JOIN batch b ON b.id = d.batch_id WHERE {where}"
                f" ORDER BY d.rowid", tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def queue_summary(self) -> dict:
        """Counts for the work list's header line; `eta_seconds` is added by
        the caller (app.main), which holds the worker, not the repo."""
        with self._lock:
            queued = self._conn.execute(
                "SELECT COUNT(*) FROM document WHERE state = 'queued'").fetchone()[0]
            extracting = self._conn.execute(
                "SELECT COUNT(*) FROM document WHERE state = 'extracting'").fetchone()[0]
            p = self._conn.execute("SELECT value FROM setting WHERE key = 'paused'").fetchone()
        return {"queued": queued, "extracting": extracting,
                "paused": p is not None and p["value"] == "1"}

    def completed_upload(self, upload_id) -> dict:
        """What a repeated /complete answers: the documents it staged (those
        the user has not removed since) and what it ignored."""
        with self._lock:
            u = self._upload(self._conn, upload_id)
            rows = self._staging_rows(self._conn, "d.upload_id = ?", (upload_id,))
        return {"upload": upload_view(u), "documents": rows}

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

    def create_batch(self, name: str) -> dict:
        """A draft: staged files wait here until Start (decision 10)."""
        now = self._clock()
        with self._tx() as c:
            batch_id = uuid.uuid4().hex
            c.execute("INSERT INTO batch (id, name, draft, created_at, updated_at)"
                      " VALUES (?, ?, 1, ?, ?)", (batch_id, name, now, now))
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
            # A new upload or a resume is activity in the draft too.
            self._touch(c, batch_id)
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
        other gets UploadConflict with the real offset. The new offset must
        also ADVANCE and stay within the declared size: past it,
        begin_completion's `received = size` could never hold, and a chunk
        that moves nothing is not a chunk."""
        with self._tx() as c:
            cur = c.execute(
                "UPDATE upload SET received = ?, updated_at = ?"
                " WHERE id = ? AND state = 'receiving' AND received = ?"
                " AND ? > received AND ? <= size",
                (received, self._clock(), upload_id, offset, received, received))
            if cur.rowcount == 0:
                u = self._upload(c, upload_id)
                raise UploadConflict("the upload has moved on", received=u["received"],
                                     state=u["state"])
        return received

    def reset_received(self, upload_id, received: int) -> None:
        """The part file holds less than the index counted (lost or cut
        short outside this process): only bytes on disk count. It only ever
        moves the offset DOWN -- it can never invent bytes. A reset to where
        the offset already stands or above it changes nothing and is not an
        error: uploads.py resets to 0 whenever the part file is missing,
        which includes the first chunk of every upload."""
        with self._tx() as c:
            c.execute("UPDATE upload SET received = ?, updated_at = ?"
                      " WHERE id = ? AND state = 'receiving' AND received > ?",
                      (received, self._clock(), upload_id, received))

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

    def reset_completing(self) -> int:
        """At startup only (under the worker lock): a completion cut off by a
        crash. Its rows never committed (one transaction), so the upload is
        whole again and /complete may be retried. While requests run, a
        `completing` row is a completion in progress, never a leftover."""
        with self._tx() as c:
            return c.execute("UPDATE upload SET state = 'receiving', updated_at = ?"
                             " WHERE state = 'completing'", (self._clock(),)).rowcount

    def delete_empty_draft(self, batch_id, cutoff: str) -> bool:
        """Remove one empty draft (see _EMPTY_DRAFT) with its complete upload
        rows, in ONE transaction that re-checks it is still empty: a file
        that arrived since keeps it. True if it went."""
        with self._tx() as c:
            if c.execute(f"SELECT 1 FROM batch WHERE id = ? AND {_EMPTY_DRAFT}",
                         (batch_id, cutoff)).fetchone() is None:
                return False
            c.execute("DELETE FROM upload WHERE batch_id = ?", (batch_id,))
            c.execute("DELETE FROM batch WHERE id = ?", (batch_id,))
            return True

    def abort_completion(self, upload_id) -> None:
        """A completion that failed before its rows committed: the upload is
        whole again and /complete may be retried."""
        with self._tx() as c:
            c.execute("UPDATE upload SET state = 'receiving', updated_at = ?"
                      " WHERE id = ? AND state = 'completing'", (self._clock(), upload_id))

    def delete_upload(self, upload_id, older_than=None):
        """An unfinished upload removed by the user or gone stale; returns
        its row, or None if unknown (idempotent). A complete upload is
        refused: its drawings are removed one by one. With `older_than`
        (the stale sweep), an upload completed or touched since it was
        listed is left alone -- the age is re-checked in this transaction,
        so a resume (which bumps updated_at) always wins."""
        with self._tx() as c:
            row = c.execute("SELECT * FROM upload WHERE id = ?", (upload_id,)).fetchone()
            if row is None:
                return None
            if older_than is not None and (row["state"] != "receiving"
                                           or row["updated_at"] >= older_than):
                # Only a `receiving` row is ever stale: `completing` is a
                # completion in progress, `complete` is imported.
                return None
            if row["state"] == "complete":
                raise InvalidTransition("this upload is already imported; remove its drawings instead")
            c.execute("DELETE FROM upload WHERE id = ?", (upload_id,))
            if older_than is None:
                # The user acted on the draft. (A stale removal is not
                # activity: it would postpone the empty-draft sweep.)
                self._touch(c, row["batch_id"])
            return dict(row)

    def delete_untouched_upload(self, upload_id) -> bool:
        """Take back a row this create inserted, but only if nobody has used
        it since: True if it was removed. Between the insert and the
        create's failure another tab may have resumed the row (a resume
        bumps updated_at) or sent bytes (received > 0) -- the upload is then
        theirs, and a failure of ours must not take it from them."""
        with self._tx() as c:
            return c.execute(
                "DELETE FROM upload WHERE id = ? AND state = 'receiving' AND received = 0"
                " AND updated_at = created_at", (upload_id,)).rowcount > 0

    def rename_batch(self, batch_id, name: str) -> dict:
        with self._tx() as c:
            self._batch(c, batch_id)
            c.execute("UPDATE batch SET name = ? WHERE id = ?", (name, batch_id))
            self._touch(c, batch_id)
            return batch_view(self._batch(c, batch_id))

    def set_included(self, doc_id, included: bool) -> dict:
        with self._tx() as c:
            r = c.execute(
                "SELECT d.state, d.batch_id, d.unreadable, d.pages, d.oversized, b.draft"
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
            self._touch(c, r["batch_id"])
            return self._staging_rows(c, "d.id = ?", (doc_id,))[0]

    @staticmethod
    def _draft_rows(c, batch_id, expected_ids) -> list:
        """A draft's documents, in staging order -- all of them `staged`,
        because only start_batch queues a draft's drawing. Start and Discard
        DELETE rows on that assumption, so it is checked, not trusted: a row
        past `staged` may be in the worker's hands, and removing it under
        the worker is the one thing neither may ever do.

        `expected_ids`, when given, is the caller's snapshot of the draft:
        it marked those drawings' folders `.importing` before calling, from
        outside this transaction. A completion that landed in between added
        a row whose folder carries no marker and would leak after a crash,
        so any difference refuses the whole step and the caller reloads."""
        rows = c.execute("SELECT id, state, included, unreadable, pages, oversized,"
                         " allowed_out_of_scope FROM document"
                         " WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()
        if expected_ids is not None and {r["id"] for r in rows} != set(expected_ids):
            raise InvalidTransition("the import changed — reload it")
        if any(r["state"] != "staged" for r in rows):
            raise InvalidTransition("this import holds a drawing that is no longer staged")
        return rows

    def start_batch(self, batch_id, expected_ids=None) -> dict:
        """Queue the included rows, drop the excluded ones, end the draft --
        one transaction (design §2 "Start batch"). Refused, changing nothing,
        while a file is still uploading, when nothing is included, or when
        the draft no longer holds exactly `expected_ids` (see _draft_rows).
        Returns the ids queued and dropped; the caller removes the dropped
        files."""
        with self._tx() as c:
            # Read under the lock: queue order is then commit order. Read
            # before it, a Start that took the clock first but committed
            # second would still queue ahead of the import before it.
            now = self._clock()
            if not self._batch(c, batch_id)["draft"]:
                raise InvalidTransition("this import has already started")
            if c.execute("SELECT 1 FROM upload WHERE batch_id = ? AND state != 'complete' LIMIT 1",
                         (batch_id,)).fetchone():
                raise InvalidTransition("files are still uploading; wait for them or remove them")
            rows = self._draft_rows(c, batch_id, expected_ids)
            go = [r["id"] for r in rows if r["included"] and not r["unreadable"]]
            if not go:
                raise InvalidTransition("nothing is included")
            # Decision 26: an out-of-scope drawing runs only with the
            # permission set_included stores alongside `included`. Checked,
            # not trusted -- a row included by any other route stops Start.
            if any(r["included"] and not r["unreadable"]
                   and out_of_scope(r["pages"], r["oversized"])
                   and not r["allowed_out_of_scope"] for r in rows):
                raise InvalidTransition(
                    "an out-of-scope drawing is included without its permission; include it again")
            keep = set(go)
            drop = [r["id"] for r in rows if r["id"] not in keep]
            for doc_id in go:
                # One queued_at for the whole import; claim_next and
                # queue_position break the tie by rowid, which is staging order.
                self._move(c, doc_id, ("staged",), "queued", queued_at=now)
            c.executemany("DELETE FROM document WHERE id = ? AND state = 'staged'",
                          [(i,) for i in drop])
            # Upload rows are the import's bookkeeping; the documents keep
            # upload_id as history (no foreign key, migration 2).
            c.execute("DELETE FROM upload WHERE batch_id = ?", (batch_id,))
            c.execute("UPDATE batch SET draft = 0 WHERE id = ?", (batch_id,))
        return {"queued": go, "dropped": drop}

    def delete_batch(self, batch_id, expected_ids=None):
        """Discard an import that has not started: its staged rows, its
        uploads and the batch. Refused, changing nothing, when the draft no
        longer holds exactly `expected_ids` (see _draft_rows). Returns the
        ids whose files the caller must remove; None if unknown (idempotent)."""
        with self._tx() as c:
            row = c.execute("SELECT draft FROM batch WHERE id = ?", (batch_id,)).fetchone()
            if row is None:
                return None
            if not row["draft"]:
                raise InvalidTransition("only an import that has not started can be discarded")
            docs = [r["id"] for r in self._draft_rows(c, batch_id, expected_ids)]
            ups = [r["id"] for r in c.execute(
                "SELECT id FROM upload WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()]
            c.execute("DELETE FROM document WHERE batch_id = ?", (batch_id,))
            c.execute("DELETE FROM upload WHERE batch_id = ?", (batch_id,))
            c.execute("DELETE FROM batch WHERE id = ?", (batch_id,))
        return {"documents": docs, "uploads": ups}

    def create_document(self, doc_id, file_name, pages, sha256, batch_name=None) -> dict:
        """A single upload is a batch of one (decision 17); B2 adds real batches."""
        now = self._clock()
        with self._tx() as c:
            batch_id = uuid.uuid4().hex
            c.execute("INSERT INTO batch (id, name, draft, created_at, updated_at)"
                      " VALUES (?, ?, 0, ?, ?)", (batch_id, batch_name or file_name, now, now))
            c.execute(
                "INSERT INTO document (id, batch_id, file_name, sha256, pages, state,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'staged', ?, ?)",
                (doc_id, batch_id, file_name, sha256, pages, now, now))
            return self._row(c, doc_id)

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
            # Read back BEFORE commit: once the lock drops, an API cancel or
            # delete can land, and a claim that succeeded must still tell the
            # worker which document it now holds.
            return self._row(c, row["id"])

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

    def update_progress(self, doc_id, counts: dict) -> None:
        """Flagged/open/unchecked counts for the work list, computed by
        ReviewStore.progress() and persisted so GET /api/documents never
        replays a journal for every drawing on every 3 s poll (batch design
        §3). Silent on an unknown id on purpose: the caller already looked
        the row up to get its counts, so a race with a delete here is a
        lost update to a row that is gone anyway, not a bug to surface."""
        with self._tx() as c:
            c.execute("UPDATE document SET flagged_total = ?, flagged_open = ?,"
                      " unchecked = ? WHERE id = ?",
                      (counts["flagged_total"], counts["flagged_open"],
                       counts["unchecked"], doc_id))

    def move_to_front(self, doc_id) -> None:
        """priority = current max + 1 (batch design §2 queue controls):
        claims next regardless of how long anything else has waited.
        Refused off any state but `queued` -- "move to the front" has no
        meaning for a drawing already running, finished, or not yet
        started."""
        with self._tx() as c:
            row = c.execute("SELECT state FROM document WHERE id = ?", (doc_id,)).fetchone()
            if row is None:
                raise UnknownDocument(f"no document {str(doc_id)[:8]}")
            if row["state"] != "queued":
                raise InvalidTransition("only a queued drawing can move to the front")
            top = c.execute("SELECT COALESCE(MAX(priority), 0) FROM document").fetchone()[0]
            c.execute("UPDATE document SET priority = ?, updated_at = ? WHERE id = ?",
                      (top + 1, self._clock(), doc_id))

    def reextract(self, doc_id) -> None:
        """Back to `queued`, discarding the old proposal (batch design §2
        queue controls): refused off any state but `ready` -- `in_review`
        or `finished` means a human has already acted, which is exactly
        what ReviewStore.has_events() (checked by the caller, app.main)
        exists to protect. Progress counts go back to NULL: the old ones
        described a proposal that no longer exists."""
        with self._tx() as c:
            self._move(c, doc_id, ("ready",), "queued", attempts=0, error_type=None,
                       queued_at=self._clock(), flagged_total=None,
                       flagged_open=None, unchecked=None)

    def delete(self, doc_id) -> bool:
        """Remove a drawing and, if it was the last, its batch. An unknown id
        returns False on purpose: removing is idempotent, so a repeated
        request after a lost response is not an error.

        Only the `extracting` state is refused here. That is NOT enough on
        its own: cancel is cooperative, so the worker's run keeps going
        briefly after the row already says `cancelled`. The API must also
        refuse to delete the worker's current job until the run returns.

        Removing the last staged drawing of a draft keeps the draft."""
        with self._tx() as c:
            row = c.execute("SELECT state, batch_id FROM document WHERE id = ?",
                            (doc_id,)).fetchone()
            if row is None:
                return False
            if row["state"] == "extracting":
                raise InvalidTransition("cancel the extraction before removing the drawing")
            c.execute("DELETE FROM document WHERE id = ?", (doc_id,))
            # A started batch goes with its last drawing. A draft stays: it
            # may still receive files and is continued later (design §2);
            # it goes when discarded, or when empty and a week old (uploads.py).
            # (Its upload rows reference it, so deleting it here would also
            # fail the upload.batch_id foreign key.)
            c.execute("DELETE FROM batch WHERE id = ? AND draft = 0 AND NOT EXISTS "
                      "(SELECT 1 FROM document WHERE batch_id = ?)",
                      (row["batch_id"], row["batch_id"]))
            # A draft emptied today starts its idle week today.
            self._touch(c, row["batch_id"])
        return True
