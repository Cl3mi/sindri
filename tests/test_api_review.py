"""Review capture through the API (design §2, §6)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.detect import Detection
from app.review.store import ReviewStore
from tests.conftest import StubVLMBackend
from tests.test_api import parse_sse, save_pdf

client = TestClient(app)


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1")
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ReviewStore(tmp_path / "reviews")
    monkeypatch.setattr("app.main._REVIEW_STORE", s)
    return s


def extract(sample_pdf):
    meta = save_pdf(client, sample_pdf)
    _p, result, error = parse_sse(client.post(f"/api/extract/{meta['session_id']}"))
    assert error is None
    return result


def test_extract_writes_the_proposal_server_side(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    d = store.root / result["session_id"]
    proposal = json.loads((d / "proposal.json").read_text())
    assert [r["id"] for r in proposal["rows"]] == [r["id"] for r in result["rows"]]
    assert json.loads((d / "header.json").read_text())["review_schema_version"] == 1
    assert result["writer"] and result["review_logging"] is True


def test_extract_without_a_store_still_works_and_says_logging_is_off(
        sample_pdf, stub_backend, monkeypatch):
    monkeypatch.setattr("app.main._REVIEW_STORE", None)
    result = extract(sample_pdf)
    assert result["writer"] is None and result["review_logging"] is False


def test_health_reports_review_logging(store):
    assert client.get("/api/health").json()["review_logging"] is True


def test_consent_comes_from_the_environment(sample_pdf, stub_backend, store, monkeypatch):
    monkeypatch.setenv("SINDRI_REVIEW_CONSENT", "1")
    result = extract(sample_pdf)
    header = json.loads((store.root / result["session_id"] / "header.json").read_text())
    assert header["consent"] is True


def test_a_review_capture_fault_does_not_fail_the_extraction(
        sample_pdf, stub_backend, store, monkeypatch):
    """build_header can raise (e.g. a PyMuPDF fault unrelated to the already-
    finished extraction); logging is best-effort (§6) -- a session recorded
    nowhere is never graded, but an extraction lost to a logging fault costs
    the reviewer the drawing, which is strictly worse."""
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("app.main.build_header", boom)
    result = extract(sample_pdf)
    assert result is not None
    assert result["writer"] is None and result["review_logging"] is False


def test_events_round_trip_and_seal(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    sid, writer, rows = result["session_id"], result["writer"], result["rows"]
    rid = rows[0]["id"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 1, "type": "edit_cell", "id": rid, "field": "nominal",
         "old": rows[0]["nominal"], "new": "7"},
        {"seq": 2, "type": "accept", "ids": [rid]}]})
    assert r.status_code == 200 and r.json() == {"contiguous": 2}
    final = [{**rows[0], "nominal": "7"}, *rows[1:]]
    r = client.post(f"/api/session/{sid}/seal", json={
        "writer": writer, "final_seq": 2, "rows": final, "reviewed_ids": [rid]})
    assert r.status_code == 200
    assert r.json() == {"revision": 1, "replay_ok": True, "review_logging": True}


def test_events_with_a_stale_writer_are_409(sample_pdf, stub_backend, store):
    sid = extract(sample_pdf)["session_id"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": "x", "events": []})
    assert r.status_code == 409
    assert "opened elsewhere" in r.json()["detail"]


def test_seal_with_a_gap_is_409(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    sid, writer = result["session_id"], result["writer"]
    client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 2, "type": "accept", "ids": []}]})
    r = client.post(f"/api/session/{sid}/seal", json={
        "writer": writer, "final_seq": 2, "rows": result["rows"], "reviewed_ids": []})
    assert r.status_code == 409
    assert "missing events" in r.json()["detail"]


def test_events_without_a_store_reports_logging_off(sample_pdf, stub_backend, monkeypatch):
    monkeypatch.setattr("app.main._REVIEW_STORE", None)
    result = extract(sample_pdf)
    r = client.post(f"/api/session/{result['session_id']}/events", json={
        "writer": "", "events": []})
    assert r.status_code == 200
    assert r.json() == {"contiguous": None, "review_logging": False}


def test_a_malformed_event_is_422_with_its_reason(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    sid, writer = result["session_id"], result["writer"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 1, "type": "teleport"}]})
    assert r.status_code == 422
    assert "unknown event type" in r.json()["detail"]


def test_malformed_session_id_is_404(store):
    r = client.post("/api/session/not-a-hex-id/events", json={"writer": "x", "events": []})
    assert r.status_code == 404


def test_seal_without_a_store_reports_logging_off(sample_pdf, stub_backend, monkeypatch):
    monkeypatch.setattr("app.main._REVIEW_STORE", None)
    result = extract(sample_pdf)
    r = client.post(f"/api/session/{result['session_id']}/seal", json={
        "writer": "", "final_seq": 0, "rows": result["rows"], "reviewed_ids": []})
    assert r.status_code == 200
    assert r.json() == {"revision": None, "review_logging": False}


def test_review_store_from_env_refuses_an_unwritable_dir(tmp_path, monkeypatch):
    """/api/health's `review_logging` flag must never claim logging is on for
    a directory the process cannot actually write to -- a read-only mount or
    a permissions slip would otherwise silently drop every review record
    while the UI's log-pill stays hidden, saying nothing is wrong."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits; cannot exercise this as root")
    from app.main import _review_store_from_env
    d = tmp_path / "reviews"
    d.mkdir()
    os.chmod(d, 0o500)   # read + execute, no write
    try:
        monkeypatch.setenv("SINDRI_REVIEW_DIR", str(d))
        assert _review_store_from_env() is None
    finally:
        os.chmod(d, 0o700)   # tmp_path cleanup needs write access back
