import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.pipeline.detect import Detection
from tests.conftest import StubVLMBackend

client = TestClient(app)


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1",
    )
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


def test_upload_returns_rows_and_image(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert len(data["rows"]) >= 1
    assert data["rows"][0]["source"] == "auto"
    assert data["rows"][0]["id"]
    assert data["image_url"].startswith("/api/image/")


def test_upload_without_detection_backend_returns_400(sample_pdf, monkeypatch):
    class ReadOnly:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)
    monkeypatch.setattr("app.main._BACKEND", ReadOnly())
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 400
    assert "VLM" in r.json()["detail"]


def test_read_region_returns_parsed_characteristic(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload",
                         files={"file": ("sample.pdf", f, "application/pdf")}).json()
    r = client.post("/api/read_region",
                    json={"session_id": up["session_id"], "box": [40, 40, 200, 90]})
    assert r.status_code == 200
    row = r.json()
    assert row["source"] == "manual"
    assert row["nominal"] == "1,2"
    assert row["target_region"] == [40, 40, 200, 90]
    assert row["balloon_xy"] is not None


def test_export_xlsx_roundtrip(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload",
                         files={"file": ("sample.pdf", f, "application/pdf")}).json()
    r = client.post("/api/export",
                    json={"session_id": up["session_id"], "rows": up["rows"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(r.content) > 0


def test_export_pdf_roundtrip(sample_pdf, stub_backend):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload",
                         files={"file": ("sample.pdf", f, "application/pdf")}).json()
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
    client = TestClient(main.app)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
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
    client = TestClient(main.app)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["notes"] is None
