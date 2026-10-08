import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from tests.test_api import save_pdf, start_pdf, stub_backend

client = TestClient(app)


def _ready(sample_pdf, stub_backend, filename="a.pdf"):
    meta = save_pdf(client, sample_pdf, filename)
    sid = meta["session_id"]
    start_pdf(client, sid)
    assert main.runtime().worker.run_once() == sid
    return sid


def test_list_documents_returns_every_drawing_with_progress_and_cursor(sample_pdf, stub_backend):
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    r = client.get("/api/documents")
    assert r.status_code == 200, r.text
    body = r.json()
    (row,) = [d for d in body["documents"] if d["id"] == sid]
    assert row["state"] == "ready"
    assert row["flagged_total"] is not None   # backfilled on this read
    assert body["cursor"]
    assert body["queue"]["paused"] is False


def test_list_documents_since_cursor_narrows_to_later_changes(sample_pdf, stub_backend):
    _ready(sample_pdf, stub_backend, "a.pdf")
    cursor = client.get("/api/documents").json()["cursor"]
    sid2 = _ready(sample_pdf, stub_backend, "b.pdf")
    r = client.get("/api/documents", params={"since": cursor})
    ids_ = {d["id"] for d in r.json()["documents"]}
    assert sid2 in ids_


def test_cursor_has_a_safety_margin_so_a_near_simultaneous_write_is_not_skipped(sample_pdf, stub_backend):
    """The route must read the clock BEFORE running its query and hand back
    that time minus a margin, not a cursor derived from the rows it happened
    to see. Otherwise a write that commits in the gap between the clock read
    and the query's own scan -- stamped with a time earlier than a
    naively-computed "now" cursor -- would never satisfy a later `since`
    poll's `updated_at >= since` and the row would be silently dropped
    forever (batch design decision 11)."""
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    cursor = client.get("/api/documents").json()["cursor"]
    cursor_dt = datetime.fromisoformat(cursor)
    # cursor must sit strictly in the past relative to the moment it was
    # issued -- that gap IS the margin this test depends on.
    assert cursor_dt < datetime.now(cursor_dt.tzinfo)
    # Simulate a write that committed between the previous poll's clock read
    # and its query -- stamped just inside the margin window, "before" the
    # real current time but at or after the cursor already handed out.
    stamped = (cursor_dt + timedelta(seconds=1)).isoformat(timespec="microseconds")
    repo = main.runtime().repo
    with repo._lock:
        # Autocommit (db.connect's isolation_level=None): this one statement
        # is its own transaction, so no explicit COMMIT is needed or valid.
        repo._conn.execute(
            "UPDATE document SET updated_at = ? WHERE id = ?", (stamped, sid))
    r = client.get("/api/documents", params={"since": cursor})
    assert sid in {d["id"] for d in r.json()["documents"]}


def test_move_to_front_refuses_an_extracting_drawing(sample_pdf):
    meta = save_pdf(client, sample_pdf, "a.pdf")
    sid = meta["session_id"]
    start_pdf(client, sid)
    # start_pdf only leaves the drawing `queued` -- move_to_front ALLOWS
    # that state (it is the only one it makes sense for). claim_next() moves
    # it to `extracting` without running the stub worker loop, which is the
    # state this test is actually named for.
    main.runtime().repo.claim_next()
    r = client.post(f"/api/documents/{sid}/move_to_front")
    assert r.status_code == 409, r.text


def test_move_to_front_bumps_a_queued_drawing_ahead(sample_pdf):
    m1 = save_pdf(client, sample_pdf, "a.pdf")
    m2 = save_pdf(client, sample_pdf, "b.pdf")
    start_pdf(client, m1["session_id"])
    start_pdf(client, m2["session_id"])
    r = client.post(f"/api/documents/{m2['session_id']}/move_to_front")
    assert r.status_code == 200, r.text
    assert main.runtime().repo.claim_next()["id"] == m2["session_id"]


def test_reextract_refused_once_there_is_a_review_event(sample_pdf, stub_backend):
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    doc = client.post(f"/api/documents/{sid}/open").json()
    client.post(f"/api/session/{sid}/events",
               json={"writer": doc["writer"],
                     "events": [{"seq": 1, "type": "accept", "ids": []}]})
    r = client.post(f"/api/documents/{sid}/reextract")
    assert r.status_code == 409, r.text
    # The refusal must never touch result.json: the drawing stays reviewable.
    assert (main.runtime().data.doc_dir(sid) / "result.json").is_file()
    assert client.post(f"/api/documents/{sid}/open").status_code == 200


def test_reextract_requeues_an_untouched_ready_drawing(sample_pdf, stub_backend):
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    r = client.post(f"/api/documents/{sid}/reextract")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "queued"
    assert not (main.runtime().data.doc_dir(sid) / "result.json").is_file()


def test_reextract_refused_while_extracting(sample_pdf):
    meta = save_pdf(client, sample_pdf, "a.pdf")
    sid = meta["session_id"]
    start_pdf(client, sid)
    main.runtime().repo.claim_next()
    r = client.post(f"/api/documents/{sid}/reextract")
    assert r.status_code == 409, r.text


def test_reextract_refused_when_the_drawing_is_the_workers_current_job(sample_pdf, stub_backend):
    """A `ready` drawing can never actually BE worker.current in real use
    (current is only set while claim_next has it `extracting`) -- this test
    drives that defensively, by forcing the flag directly, because the
    REQUIRED review explicitly calls for covering this branch on its own
    rather than relying on it being implied by the state check."""
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    main.runtime().worker._current = sid
    try:
        r = client.post(f"/api/documents/{sid}/reextract")
        assert r.status_code == 409, r.text
    finally:
        main.runtime().worker._current = None


def test_reextract_restores_the_result_on_any_repo_failure_not_only_a_refusal(
        sample_pdf, stub_backend, monkeypatch):
    """CRITICAL fix: the old `except BatchError` left `result.json` stashed
    and the drawing still `ready` -- openable with NO result file at all --
    for any OTHER exception (a locked database, a disk fault, ...). The
    rename-back must run for literally any failure from repo.reextract."""
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    repo = main.runtime().repo
    original = repo.reextract

    def boom(doc_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "reextract", boom)
    with pytest.raises(sqlite3.OperationalError):
        client.post(f"/api/documents/{sid}/reextract")
    assert (main.runtime().data.doc_dir(sid) / "result.json").is_file()
    assert not (main.runtime().data.doc_dir(sid) / "result.reextract.json").exists()
    # Restore the real method directly -- monkeypatch.undo() would also roll
    # back conftest's SINDRI_DATA_DIR/SINDRI_REVIEW_DIR env patches shared on
    # the same fixture instance, pointing the next runtime() build at /data.
    monkeypatch.setattr(repo, "reextract", original)
    assert client.post(f"/api/documents/{sid}/open").status_code == 200


def test_reextract_then_worker_run_once_produces_a_fresh_proposal(sample_pdf, stub_backend):
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    assert client.post(f"/api/documents/{sid}/reextract").status_code == 200
    assert main.runtime().worker.run_once() == sid
    assert client.get(f"/api/documents/{sid}").json()["state"] == "ready"
    assert (main.runtime().data.doc_dir(sid) / "result.json").is_file()
    opened = client.post(f"/api/documents/{sid}/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["rows"]


def test_pause_then_resume_the_queue():
    assert client.post("/api/queue/pause").json() == {"paused": True}
    assert client.get("/api/documents").json()["queue"]["paused"] is True
    assert client.post("/api/queue/resume").json() == {"paused": False}
    assert client.get("/api/documents").json()["queue"]["paused"] is False


def test_queue_summary_carries_an_eta_once_the_worker_has_a_sample(sample_pdf, stub_backend):
    body = client.get("/api/documents").json()
    assert body["queue"]["eta_seconds"] is None   # no drawing finished yet this test
    _ready(sample_pdf, stub_backend, "a.pdf")
    body = client.get("/api/documents").json()
    assert body["queue"]["eta_seconds"] is not None


def test_new_routes_reject_a_malformed_document_id():
    assert client.post("/api/documents/not-a-valid-uuid/move_to_front").status_code == 404
    assert client.post("/api/documents/not-a-valid-uuid/reextract").status_code == 404


def test_post_events_succeeds_even_if_progress_recompute_faults(sample_pdf, stub_backend, monkeypatch):
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    doc = client.post(f"/api/documents/{sid}/open").json()

    def boom(_sid):
        raise OSError("disk fault")

    monkeypatch.setattr(main.runtime().store, "progress", boom)
    r = client.post(f"/api/session/{sid}/events",
                    json={"writer": doc["writer"],
                          "events": [{"seq": 1, "type": "accept", "ids": []}]})
    assert r.status_code == 200, r.text


def test_seal_succeeds_even_if_progress_recompute_faults(sample_pdf, stub_backend, monkeypatch):
    sid = _ready(sample_pdf, stub_backend, "a.pdf")
    doc = client.post(f"/api/documents/{sid}/open").json()

    def boom(_sid):
        raise OSError("disk fault")

    monkeypatch.setattr(main.runtime().store, "progress", boom)
    r = client.post(f"/api/session/{sid}/seal",
                    json={"writer": doc["writer"], "final_seq": 0,
                          "rows": doc["rows"], "reviewed_ids": []})
    assert r.status_code == 200, r.text
