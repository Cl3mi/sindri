import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.pipeline.detect import Detection
from tests.conftest import StubVLMBackend

client = TestClient(app)


def parse_sse(resp):
    """Split an SSE upload response into (progress_events, result, error)."""
    progress, result, error = [], None, None
    for block in resp.text.split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if data is None:
            continue
        payload = json.loads(data)
        if event == "progress":
            progress.append(payload)
        elif event == "result":
            result = payload
        elif event == "error":
            error = payload
    return progress, result, error


def save_pdf(test_client, path, filename="sample.pdf"):
    """POST a PDF to /api/upload (save only) and return its metadata."""
    with open(path, "rb") as f:
        r = test_client.post("/api/upload",
                             files={"file": (filename, f, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def upload_pdf(test_client, path, filename="sample.pdf"):
    """Save a PDF then run extraction; return the final extraction result."""
    meta = save_pdf(test_client, path, filename)
    r = test_client.post(f"/api/extract/{meta['session_id']}")
    assert r.status_code == 200, r.text
    _progress, result, error = parse_sse(r)
    assert error is None, error
    assert result is not None
    return result


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1",
    )
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


def test_upload_returns_rows_and_image(sample_pdf, stub_backend):
    data = upload_pdf(client, sample_pdf)
    assert "session_id" in data
    assert len(data["rows"]) >= 1
    assert data["rows"][0]["source"] == "auto"
    assert data["rows"][0]["id"]
    assert data["image_url"].startswith("/api/image/")


def test_extract_streams_progress_events(sample_pdf, stub_backend):
    meta = save_pdf(client, sample_pdf)
    r = client.post(f"/api/extract/{meta['session_id']}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    progress, result, error = parse_sse(r)
    assert error is None
    assert result is not None
    steps = [p["step"] for p in progress]
    assert steps[0] == "render"
    assert "detect" in steps and "ocr" in steps and "place" in steps


def test_extract_without_detection_backend_streams_error(sample_pdf, monkeypatch):
    class ReadOnly:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)
    monkeypatch.setattr("app.main._BACKEND", ReadOnly())
    meta = save_pdf(client, sample_pdf)
    r = client.post(f"/api/extract/{meta['session_id']}")
    assert r.status_code == 200
    _progress, result, error = parse_sse(r)
    assert result is None
    assert error is not None and "VLM" in error["detail"]


def test_extraction_cancels_when_event_set(sample_pdf, stub_backend, tmp_path):
    import threading
    import app.main as main
    from app.pipeline.extract import extract

    cancel = threading.Event()
    cancel.set()  # already cancelled before the first progress emit
    seen = []

    def progress(step, detail="", current=None, total=None):
        seen.append(step)
        if cancel.is_set():
            raise main._Cancelled()

    with pytest.raises(main._Cancelled):
        extract(sample_pdf, work_dir=tmp_path, dpi=300,
                backend=stub_backend, progress=progress)
    # it unwound at the very first emit ("render"), never reaching "place"
    assert seen == ["render"]
    assert "place" not in seen


def test_upload_returns_metadata_without_extracting(sample_pdf, stub_backend):
    import app.main as main
    meta = save_pdf(client, sample_pdf)
    assert main._SESSION_RE.match(meta["session_id"])
    assert meta["fileName"] == "sample.pdf"
    assert meta["pages"] >= 1
    # save-only: the input pdf is stored but no extraction artifacts exist yet
    work = main._SESSIONS / meta["session_id"]
    assert (work / "input.pdf").is_file()
    assert not (work / "page.png").exists()


def test_extract_rejects_bad_session_id():
    # malformed id is rejected by _session_dir before any work
    assert client.post("/api/extract/not-a-valid-uuid").status_code == 404


def test_extract_unknown_session_returns_404():
    # well-formed id but no saved input.pdf
    assert client.post("/api/extract/" + "0" * 32).status_code == 404


def test_read_region_returns_parsed_characteristic(sample_pdf, stub_backend):
    up = upload_pdf(client, sample_pdf)
    r = client.post("/api/read_region",
                    json={"session_id": up["session_id"], "box": [40, 40, 200, 90]})
    assert r.status_code == 200
    row = r.json()
    assert row["source"] == "manual"
    assert row["nominal"] == "1,2"
    assert row["target_region"] == [40, 40, 200, 90]
    assert row["balloon_xy"] is not None


def test_export_xlsx_roundtrip(sample_pdf, stub_backend):
    up = upload_pdf(client, sample_pdf)
    r = client.post("/api/export",
                    json={"session_id": up["session_id"], "rows": up["rows"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(r.content) > 0


def test_export_pdf_roundtrip(sample_pdf, stub_backend):
    up = upload_pdf(client, sample_pdf)
    r = client.post("/api/export/pdf",
                    json={"session_id": up["session_id"], "rows": up["rows"]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_export_rejects_path_traversal_session_id():
    r = client.post("/api/export", json={
        "session_id": "../../../../tmp/sindri_pwn_test", "rows": [{"pos": 1}]})
    assert r.status_code == 404


def test_image_rejects_bad_session_id():
    assert client.get("/api/image/not-a-valid-uuid").status_code == 404


def test_image_missing_session_returns_404():
    assert client.get("/api/image/" + "0" * 32).status_code == 404


def test_read_region_sets_needs_review_on_empty_read(monkeypatch):
    import app.main as main
    from PIL import Image

    # empty-text stub backend (StubVLMBackend.read_region returns text="")
    monkeypatch.setattr("app.main._BACKEND", StubVLMBackend(text=""))

    # a session with a page image present (read_region requires work/page.png)
    # session id must be 32 lowercase hex chars to pass _session_dir validation
    session = "5e5537e7ae00000000000000000000ae"
    work = main._session_dir(session)
    work.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), "white").save(work / "page.png")

    resp = client.post("/api/read_region",
                       json={"session_id": session, "box": [10, 10, 80, 40]})
    assert resp.status_code == 200
    row = resp.json()
    assert row["needs_review"] is True
    assert row["review_reasons"] == ["empty read"]


def test_upload_returns_notes_field(monkeypatch, sample_pdf):
    """The upload endpoint now returns {rows, notes}; notes may be null."""
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult, NoteBlock, Note

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, char_type="Distance", nominal="1,2")],
        notes=NoteBlock(region=(0, 0, 10, 10),
                        notes=[Note(pos=101, text_en="A", text_de="B"),
                               Note(pos=1, parent_pos=101, sub_index=1,
                                    text_en="A1", text_de="A1")])
    ))
    test_client = TestClient(main.app)
    data = upload_pdf(test_client, sample_pdf, filename="x.pdf")
    assert "rows" in data and len(data["rows"]) == 1
    assert "notes" in data and data["notes"] is not None
    note_positions = [n["pos"] for n in data["notes"]["notes"]]
    assert note_positions == [101, 1]


def test_upload_returns_null_notes_when_extract_returns_none(monkeypatch, sample_pdf):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1)], notes=None))
    test_client = TestClient(main.app)
    data = upload_pdf(test_client, sample_pdf, filename="x.pdf")
    assert data["notes"] is None


def test_delete_session_removes_dir(sample_pdf):
    meta = save_pdf(client, sample_pdf)
    import app.main as main
    work = main._SESSIONS / meta["session_id"]
    assert work.is_dir()
    r = client.delete(f"/api/session/{meta['session_id']}")
    assert r.status_code == 200
    assert not work.exists()


def test_delete_missing_session_is_idempotent():
    r = client.delete("/api/session/" + "0" * 32)
    assert r.status_code == 200


def test_delete_rejects_bad_session_id():
    r = client.delete("/api/session/not-a-valid-uuid")
    assert r.status_code == 404
