"""Review capture through the API (design §2, §6)."""
import json

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
