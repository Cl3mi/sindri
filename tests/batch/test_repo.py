"""Document lifecycle (batch design §1-§2). Every transition is guarded by
the states it may leave, so an API cancel racing the worker's finish has
exactly one winner and the loser is told, never silently overwritten."""
import itertools
import sqlite3
import threading

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


def test_a_failed_write_rolls_back_whole_and_leaves_the_repo_usable(repo):
    """create_document inserts a batch THEN a document. A duplicate id fails
    the second insert; the batch row must go with it (no orphan batch), and
    the shared connection must not be left inside a dead transaction, or
    every later BEGIN on it -- the worker's included -- would fail."""
    (a,) = ids(1)
    staged(repo, a)
    with pytest.raises(sqlite3.IntegrityError):
        staged(repo, a, "again.pdf")
    assert repo._conn.execute("SELECT COUNT(*) FROM batch").fetchone()[0] == 1
    assert not repo._conn.in_transaction
    repo.enqueue(a)
    assert repo.get(a)["state"] == "queued"


class _FailingCommit:
    """A connection whose next COMMIT fails, as on a full disk or an I/O
    error. sqlite3.Connection's methods cannot be patched, so wrap it."""

    def __init__(self, conn):
        self._conn, self.fail_next = conn, True

    def execute(self, sql, *args):
        if sql == "COMMIT" and self.fail_next:
            self.fail_next = False
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_commit_does_not_wedge_the_shared_connection(tmp_path):
    """If COMMIT raises and the transaction is left open, every later
    BEGIN IMMEDIATE fails ("cannot start a transaction within a
    transaction") and a transient disk fault stops the worker for good."""
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    repo = Repo(_FailingCommit(conn))
    (a,) = ids(1)
    with pytest.raises(sqlite3.OperationalError):
        staged(repo, a)
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM batch").fetchone()[0] == 0
    staged(repo, a)
    assert repo.get(a)["state"] == "staged"


def test_concurrent_claims_hand_each_document_to_exactly_one_thread(repo):
    """The API threads and the worker share one connection under Repo's
    lock; a claim's SELECT and UPDATE must not interleave with another's."""
    docs = ids(20)
    for d in docs:
        queued(repo, d)
    got, start = [], threading.Barrier(8)

    def worker():
        start.wait()
        while (d := repo.claim_next()) is not None:
            got.append(d["id"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == docs
    assert {repo.get(d)["attempts"] for d in docs} == {1}
