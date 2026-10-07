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

from app.batch.staging import batch_view, default_included, staging_counts, staging_row, upload_view

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
            return self._row(c, doc_id)

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

    def delete(self, doc_id) -> bool:
        """Remove a drawing and, if it was the last, its batch. An unknown id
        returns False on purpose: removing is idempotent, so a repeated
        request after a lost response is not an error.

        Only the `extracting` state is refused here. That is NOT enough on
        its own: cancel is cooperative, so the worker's run keeps going
        briefly after the row already says `cancelled`. The API must also
        refuse to delete the worker's current job until the run returns."""
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
