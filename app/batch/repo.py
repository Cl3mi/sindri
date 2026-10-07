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
returns are read inside that same transaction, never re-read afterwards."""
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
