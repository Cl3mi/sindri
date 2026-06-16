from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_upload_returns_rows_and_image(sample_pdf):
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert "rows" in data and len(data["rows"]) >= 22
    assert data["image_url"].startswith("/api/image/")

def test_export_roundtrip(sample_pdf):
    with open(sample_pdf, "rb") as f:
        up = client.post("/api/upload", files={"file": ("sample.pdf", f, "application/pdf")}).json()
    rows = up["rows"]
    r = client.post("/api/export", json={"session_id": up["session_id"], "rows": rows})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(r.content) > 0


def test_export_rejects_path_traversal_session_id(tmp_path):
    # a malicious session_id must not escape the sessions dir or write a file
    r = client.post("/api/export", json={
        "session_id": "../../../../tmp/sindri_pwn_test",
        "rows": [{"pos": 1}],
    })
    assert r.status_code == 404


def test_image_rejects_bad_session_id():
    r = client.get("/api/image/not-a-valid-uuid")
    assert r.status_code == 404


def test_image_missing_session_returns_404():
    # well-formed but unknown session id
    r = client.get("/api/image/" + "0" * 32)
    assert r.status_code == 404
