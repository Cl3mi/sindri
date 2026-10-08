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
