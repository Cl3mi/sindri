"""Campaign drawings through the API (campaign design §2-§3; plan C1
decision 11): the client grades them; nobody exports, re-extracts or
removes them, on any instance."""
import json

from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.models import Characteristic

client = TestClient(app)
DOC = "c" * 32


def load_campaign_doc(sample_pdf, doc_id=DOC, campaign="pilot-1"):
    """What app.campaign.load does, minus the bundle: files in place, row
    created and queued, worker run once."""
    rt = main.runtime()
    d = rt.data.doc_dir(doc_id)
    d.mkdir(parents=True)
    (d / "input.pdf").write_bytes(sample_pdf.read_bytes())
    rows = [Characteristic(pos=1, id="r1", nominal="20", balloon_xy=(10, 10),
                           target_region=(1, 1, 5, 5)).model_dump(mode="json")]
    (d / "result.json").write_text(json.dumps(
        {"rows": rows, "notes": None, "marks": None, "title_block": []}))
    (d / "campaign.json").write_text(json.dumps({"campaign": campaign, "group": "great"}))
    (d / "page.png").write_bytes(b"")
    rt.repo.create_campaign_document(doc_id, campaign, "x.pdf", 1, "1" * 64)
    rt.repo.enqueue(doc_id)
    rt.worker.run_once()
    return doc_id


def approve(doc_id):
    opened = client.post(f"/api/documents/{doc_id}/open").json()
    res = client.post(f"/api/session/{doc_id}/seal", json={
        "writer": opened["writer"], "final_seq": 0, "rows": opened["rows"],
        "reviewed_ids": []})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_doc_view_names_the_campaign(sample_pdf):
    load_campaign_doc(sample_pdf)
    assert client.get(f"/api/documents/{DOC}").json()["campaign"] == "pilot-1"


def test_the_work_list_rows_name_the_campaign(sample_pdf):
    """The review screen decides Approve vs Finish & Export from the SAME
    list the work list polls (main.js currentDocIsCampaign), so the row must
    carry the campaign; without it every campaign drawing would offer an
    export the server then refuses."""
    load_campaign_doc(sample_pdf)
    rows = client.get("/api/documents").json()["documents"]
    assert [r["campaign"] for r in rows if r["id"] == DOC] == ["pilot-1"]


def test_a_campaign_drawing_can_be_approved_but_not_exported(sample_pdf):
    load_campaign_doc(sample_pdf)
    assert approve(DOC)["revision"] == 1
    res = client.get(f"/api/documents/{DOC}/export/xlsx")
    assert res.status_code == 409 and "campaign" in res.json()["detail"]


def test_a_campaign_drawing_is_never_re_extracted_or_removed(sample_pdf):
    load_campaign_doc(sample_pdf)
    assert client.post(f"/api/documents/{DOC}/reextract").status_code == 409
    assert client.delete(f"/api/documents/{DOC}").status_code == 409
    assert main.runtime().repo.get(DOC)["state"] == "ready"


def test_mass_export_skips_a_campaign_drawing(sample_pdf):
    load_campaign_doc(sample_pdf)
    approve(DOC)
    row = main.runtime().exports._export_one(None, DOC, set())
    assert row["skip_reason"] == "campaign"


def test_campaign_only_closes_uploads_and_reports_itself(monkeypatch):
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    assert client.post("/api/batches", json={}).status_code == 403
    assert client.get("/api/health").json()["campaign_only"] is True


def test_campaign_only_still_serves_the_review(sample_pdf, monkeypatch):
    load_campaign_doc(sample_pdf)
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    assert approve(DOC)["revision"] == 1


def test_campaign_only_hides_the_api_schema(monkeypatch):
    """Off campaign-only, /openapi.json is a developer convenience; on the
    public host it is a map of every route, and must answer 403."""
    assert client.get("/openapi.json").status_code == 200
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 403, path


OTHER = "d" * 32


def make_ordinary_doc(doc_id=OTHER):
    """A drawing of someone else's ordinary batch on the same instance."""
    rt = main.runtime()
    d = rt.data.doc_dir(doc_id)
    d.mkdir(parents=True)
    (d / "page.png").write_bytes(b"")
    rt.repo.create_document(doc_id, "other", 1, "2" * 64)
    return doc_id


def test_campaign_only_lists_only_campaign_drawings_and_batches(sample_pdf, monkeypatch):
    """H4: an instance is meant to hold ONE client's campaign, but the review
    reads were not scoped to it, so any other batch on the volume was
    readable by a reviewer. Closed now, not just documented."""
    load_campaign_doc(sample_pdf)
    make_ordinary_doc()
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    assert [r["id"] for r in client.get("/api/documents").json()["documents"]] == [DOC]
    assert {b["campaign"] for b in client.get("/api/batches").json()["batches"]} == {"pilot-1"}


def test_campaign_only_refuses_every_id_route_of_a_non_campaign_drawing(sample_pdf, monkeypatch):
    load_campaign_doc(sample_pdf)
    make_ordinary_doc()
    batch = main.runtime().repo.get(OTHER)["batch_id"]
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    for method, path in [("GET", f"/api/documents/{OTHER}"), ("GET", f"/api/image/{OTHER}"),
                         ("POST", f"/api/documents/{OTHER}/open"),
                         ("POST", f"/api/session/{OTHER}/events"),
                         ("POST", f"/api/session/{OTHER}/seal"),
                         ("GET", f"/api/batches/{batch}")]:
        res = client.request(method, path, json={} if method == "POST" else None)
        assert res.status_code == 404, (method, path, res.status_code)
    # ...and the campaign's own drawing is untouched by the scoping.
    assert client.get(f"/api/documents/{DOC}").status_code == 200


def test_scoping_is_off_on_an_ordinary_instance(sample_pdf):
    load_campaign_doc(sample_pdf)
    make_ordinary_doc()
    assert {r["id"] for r in client.get("/api/documents").json()["documents"]} == {DOC, OTHER}
    assert client.get(f"/api/documents/{OTHER}").status_code == 200


def test_read_region_is_rate_limited_per_session_on_a_campaign_instance(sample_pdf, monkeypatch):
    """H4: /api/read_region runs CPU OCR and basic auth is the only guard on
    the public host; a stuck client or a leaked password could pin the CPU."""
    from types import SimpleNamespace
    from PIL import Image
    load_campaign_doc(sample_pdf)
    Image.new("RGB", (40, 40), "white").save(main.runtime().data.doc_dir(DOC) / "page.png")
    monkeypatch.setattr(main, "read_best_rotation", lambda backend, crop: SimpleNamespace(
        text="12.5", confidence=0.9, angle=0))
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    monkeypatch.setenv("SINDRI_READ_REGION_PER_MIN", "3")
    from app.campaign import policy
    policy.reset_read_region_limit()
    body = {"session_id": DOC, "box": [0, 0, 10, 10]}
    codes = [client.post("/api/read_region", json=body).status_code for _ in range(5)]
    assert 429 not in codes[:3] and codes[3:] == [429, 429]
    # another session has its own budget
    load_campaign_doc(sample_pdf, doc_id="e" * 32)
    Image.new("RGB", (40, 40), "white").save(main.runtime().data.doc_dir("e" * 32) / "page.png")
    assert client.post("/api/read_region", json={**body, "session_id": "e" * 32}).status_code == 200
