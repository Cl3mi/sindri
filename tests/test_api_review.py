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


def test_events_round_trip_and_seal(sample_pdf, stub_backend):
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
    assert r.json() == {"revision": 1, "replay_ok": True}


def test_events_with_a_stale_writer_are_409(sample_pdf, stub_backend):
    sid = extract(sample_pdf)["session_id"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": "x", "events": []})
    assert r.status_code == 409
    assert "opened elsewhere" in r.json()["detail"]


def test_seal_with_a_gap_is_409(sample_pdf, stub_backend):
    result = extract(sample_pdf)
    sid, writer = result["session_id"], result["writer"]
    client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 2, "type": "accept", "ids": []}]})
    r = client.post(f"/api/session/{sid}/seal", json={
        "writer": writer, "final_seq": 2, "rows": result["rows"], "reviewed_ids": []})
    assert r.status_code == 409
    assert "missing events" in r.json()["detail"]


def test_a_malformed_event_is_422_with_its_reason(sample_pdf, stub_backend):
    result = extract(sample_pdf)
    sid, writer = result["session_id"], result["writer"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 1, "type": "teleport"}]})
    assert r.status_code == 422
    assert "unknown event type" in r.json()["detail"]


def test_malformed_session_id_is_404():
    r = client.post("/api/session/not-a-hex-id/events", json={"writer": "x", "events": []})
    assert r.status_code == 404
