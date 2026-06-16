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
