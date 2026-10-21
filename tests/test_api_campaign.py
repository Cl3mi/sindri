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
