import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.pipeline.detect import Detection
from tests.conftest import StubVLMBackend

client = TestClient(app)


def save_pdf(test_client, path, filename="sample.pdf"):
    """POST a PDF to /api/upload (save only) and return its metadata."""
    with open(path, "rb") as f:
        r = test_client.post("/api/upload",
                             files={"file": (filename, f, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def upload_pdf(test_client, path, filename="sample.pdf"):
    """Upload, queue and extract a PDF through the background worker -- run
    inline, so the test is deterministic -- then open it for review. Returns
    the /open payload (session_id, image_url, rows, notes, marks,
    title_block, writer, events, last_seq)."""
    import app.main as main
    meta = save_pdf(test_client, path, filename)
    sid = meta["session_id"]
    assert test_client.post(f"/api/documents/{sid}/enqueue").status_code == 200
    assert main.runtime().worker.run_once() == sid
    doc = test_client.get(f"/api/documents/{sid}").json()
    assert doc["state"] == "ready", doc
    r = test_client.post(f"/api/documents/{sid}/open")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1",
    )
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


def test_every_test_gets_its_own_data_and_review_dir(tmp_path):
    import app.main as main
    rt = main.runtime()
    assert rt.data.root == tmp_path / "data"
    assert rt.store.root == tmp_path / "data" / "reviews"


def test_upload_returns_rows_and_image(sample_pdf, stub_backend):
    data = upload_pdf(client, sample_pdf)
    assert "session_id" in data
    assert len(data["rows"]) >= 1
    assert data["rows"][0]["source"] == "auto"
    assert data["rows"][0]["id"]
    assert data["image_url"].startswith("/api/image/")


def test_queued_drawings_report_their_queue_position(sample_pdf, stub_backend):
    a = save_pdf(client, sample_pdf)["session_id"]
    b = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{a}/enqueue")
    view = client.post(f"/api/documents/{b}/enqueue").json()
    assert (view["state"], view["queue_position"]) == ("queued", 2)
    assert client.get(f"/api/documents/{a}").json()["queue_position"] == 1


def test_extraction_without_detection_backend_fails_the_document(sample_pdf, monkeypatch):
    import app.main as main

    class ReadOnly:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)
    monkeypatch.setattr("app.main._BACKEND", ReadOnly())
    sid = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    main.runtime().worker.run_once()
    doc = client.get(f"/api/documents/{sid}").json()
    assert (doc["state"], doc["error_type"]) == ("failed", "RuntimeError")
    assert client.post(f"/api/documents/{sid}/open").status_code == 409


def test_extraction_cancels_when_event_set(sample_pdf, stub_backend, tmp_path):
    import threading
    from app.batch.worker import Cancelled
    from app.pipeline.extract import extract

    cancel = threading.Event()
    cancel.set()  # already cancelled before the first progress emit
    seen = []

    def progress(step, detail="", current=None, total=None):
        seen.append(step)
        if cancel.is_set():
            raise Cancelled()

    with pytest.raises(Cancelled):
        extract(sample_pdf, work_dir=tmp_path, dpi=300,
                backend=stub_backend, progress=progress)
    # it unwound at the very first emit ("render"), never reaching "place"
    assert seen == ["render"]
    assert "place" not in seen


def test_upload_returns_metadata_without_extracting(sample_pdf, stub_backend):
    import app.main as main
    meta = save_pdf(client, sample_pdf)
    assert meta["fileName"] == "sample.pdf" and meta["pages"] >= 1
    work = main.runtime().data.doc_dir(meta["session_id"])
    assert (work / "input.pdf").is_file()
    assert not (work / "page.png").exists()
    assert client.get(f"/api/documents/{meta['session_id']}").json()["state"] == "staged"


@pytest.mark.parametrize("method,route", [
    ("post", "enqueue"), ("post", "open"), ("post", "cancel"), ("post", "retry")])
def test_document_routes_reject_a_malformed_id(method, route):
    assert getattr(client, method)(f"/api/documents/not-a-valid-uuid/{route}").status_code == 404


def test_an_unknown_document_is_404():
    assert client.get("/api/documents/" + "0" * 32).status_code == 404


def test_cancel_and_retry_a_queued_drawing(sample_pdf, stub_backend):
    import app.main as main
    sid = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    assert client.post(f"/api/documents/{sid}/cancel").json()["state"] == "cancelled"
    assert main.runtime().worker.run_once() is None
    assert client.post(f"/api/documents/{sid}/retry").json()["state"] == "queued"
    assert main.runtime().worker.run_once() == sid


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
    # session id must be 32 lowercase hex chars to pass _doc_dir validation
    session = "5e5537e7ae00000000000000000000ae"
    work = main._doc_dir(session)
    work.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), "white").save(work / "page.png")

    resp = client.post("/api/read_region",
                       json={"session_id": session, "box": [10, 10, 80, 40]})
    assert resp.status_code == 200
    row = resp.json()
    assert row["needs_review"] is True
    assert row["review_reasons"] == ["empty read"]


def test_read_region_applies_active_flag_rules_like_the_pipeline(monkeypatch):
    """extract() runs _apply_active_policy after review_flags (app/pipeline/
    extract.py); a manual read through /api/read_region skipped that step
    entirely, so the same row read manually vs auto-ballooned disagreed on
    whether it needs review. diameter_sign fires on raw_text alone (no kind
    needed), so it exercises the wiring independent of ACTIVE_FLAG_RULES'
    current membership."""
    import app.pipeline.policy_rules as pr
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("diameter_sign",))

    import app.main as main
    from PIL import Image

    monkeypatch.setattr("app.main._BACKEND", StubVLMBackend(text="Ø20"))

    session = "5e5537e7ae00000000000000000000af"
    work = main._doc_dir(session)
    work.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), "white").save(work / "page.png")

    resp = client.post("/api/read_region",
                       json={"session_id": session, "box": [10, 10, 80, 40]})
    assert resp.status_code == 200
    row = resp.json()
    assert row["needs_review"] is True
    assert pr.FLAG_REASONS["diameter_sign"] in row["review_reasons"]


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


def test_delete_document_removes_its_files_and_row(sample_pdf):
    import app.main as main
    sid = save_pdf(client, sample_pdf)["session_id"]
    work = main.runtime().data.doc_dir(sid)
    assert work.is_dir()
    assert client.delete(f"/api/documents/{sid}").status_code == 200
    assert not work.exists()
    assert client.get(f"/api/documents/{sid}").status_code == 404


def test_delete_refuses_the_drawing_the_worker_is_still_running(
        sample_pdf, stub_backend, monkeypatch):
    """Cancel is cooperative: the row says `cancelled` at once, but the run
    goes on until its next progress report. The repo allows deleting a
    cancelled row, so the API must refuse the worker's CURRENT job itself --
    otherwise the files would be removed under a run that is still writing.
    The fake extract cancels and tries to delete from inside the run, which
    is exactly that window, with no threads or timing involved."""
    import app.main as main
    seen = {}

    def extract_that_is_cancelled_midway(*a, **k):
        sid = seen["sid"]
        assert client.post(f"/api/documents/{sid}/cancel").json()["state"] == "cancelled"
        r = client.delete(f"/api/documents/{sid}")
        seen["status"], seen["body"] = r.status_code, r.json()
        seen["dir_kept"] = main.runtime().data.doc_dir(sid).is_dir()
        raise RuntimeError("never reached a result")
    monkeypatch.setattr(main, "extract", extract_that_is_cancelled_midway)
    sid = seen["sid"] = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    assert main.runtime().worker.run_once() == sid
    assert (seen["status"], seen["dir_kept"]) == (409, True), seen["body"]
    assert sid not in seen["body"]["detail"]
    # Once the run has returned, the cancelled drawing can be removed.
    assert client.get(f"/api/documents/{sid}").json()["state"] == "cancelled"
    assert client.delete(f"/api/documents/{sid}").status_code == 200


def test_delete_missing_document_is_idempotent():
    assert client.delete("/api/documents/" + "0" * 32).status_code == 200


def test_delete_rejects_bad_document_id():
    assert client.delete("/api/documents/not-a-valid-uuid").status_code == 404


@pytest.mark.parametrize("path", ["/", "/js/main.js", "/js/api.js"])
def test_static_assets_force_revalidation(path):
    """Static HTML/JS must carry Cache-Control: no-cache so a rebuilt backend
    is never paired with a stale cached frontend (the source of the
    `payload.rows.map` crash after a container rebuild)."""
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert "no-cache" in r.headers.get("cache-control", "")


def test_export_includes_title_block_sheet(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from openpyxl import load_workbook
    import uuid

    client = TestClient(main.app)
    session_id = uuid.uuid4().hex
    main.runtime().data.doc_dir(session_id).mkdir(parents=True, exist_ok=True)

    payload = {
        "session_id": session_id,
        "rows": [],
        "notes": None,
        "title_block": [
            {"label": "Sheet / Blatt", "label_en": "Sheet", "label_de": "Blatt",
             "value": "1/1"}
        ],
    }
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 200
    out = tmp_path / "got.xlsx"
    out.write_bytes(resp.content)
    wb = load_workbook(out)
    assert "Title Block" in wb.sheetnames
    assert wb["Title Block"].cell(2, 3).value == "1/1"


def test_export_request_accepts_marks_field():
    """The export endpoint's request body must accept a `marks` field of
    type Optional[MarkBlock] — symmetric with the existing `notes` field."""
    from app.main import ExportRequest
    from app.models import MarkBlock

    req = ExportRequest(session_id="abc", rows=[], notes=None, marks=None)
    assert req.marks is None

    block = MarkBlock(region=(0, 0, 1, 1), marks=[])
    req2 = ExportRequest(session_id="abc", rows=[], marks=block)
    assert req2.marks == block


def test_upload_returns_marks_field(monkeypatch, sample_pdf):
    """Extraction result carries `marks` alongside `notes`."""
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import (
        Characteristic, ExtractionResult, MarkBlock, Mark,
    )

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, char_type="Distance", nominal="1,2")],
        marks=MarkBlock(region=(0, 0, 10, 10),
                        marks=[Mark(pos=101, text_en="A", text_de="B"),
                               Mark(pos=102, text_en="C", text_de="D")])
    ))
    test_client = TestClient(main.app)
    data = upload_pdf(test_client, sample_pdf, filename="x.pdf")
    assert "marks" in data and data["marks"] is not None
    mark_positions = [m["pos"] for m in data["marks"]["marks"]]
    assert mark_positions == [101, 102]


def test_upload_returns_null_marks_when_extract_returns_none(monkeypatch, sample_pdf):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult

    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1)], marks=None))
    test_client = TestClient(main.app)
    data = upload_pdf(test_client, sample_pdf, filename="x.pdf")
    assert data["marks"] is None


# --- the suggestion tray (docs/plans/2026-10-07-suggestion-tray-design.md) ---

def test_extract_payload_merges_suggestions_flagged(monkeypatch, sample_pdf):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult
    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, id="a", nominal="20")],
        suggestions=[Characteristic(pos=0, id="b", nominal="7",
                                    suggested=True)]))
    data = upload_pdf(TestClient(main.app), sample_pdf, filename="x.pdf")
    assert [(r["id"], r["suggested"]) for r in data["rows"]] == \
        [("a", False), ("b", True)]


@pytest.mark.parametrize("endpoint", ["/api/export", "/api/export/pdf"])
def test_exports_drop_unconfirmed_suggestions_server_side(
        endpoint, sample_pdf, stub_backend, monkeypatch):
    """Defence in depth: even when a client sends a suggested row, it is not
    exported. An unconfirmed value in the client's Excel or ballooned PDF is
    exactly the failure the phantom drops exist to prevent."""
    import app.main as main
    seen = {}

    def fake_xlsx(rows, out, **kw):
        seen["rows"] = rows
        out.write_bytes(b"x")

    def fake_pdf(src, rows, dpi, out_path):
        seen["rows"] = rows
        out_path.write_bytes(b"%PDF")

    monkeypatch.setattr(main, "write_workbook", fake_xlsx)
    monkeypatch.setattr(main, "render_ballooned_pdf", fake_pdf)
    up = upload_pdf(client, sample_pdf)
    rows = up["rows"] + [{**up["rows"][0], "id": "s", "suggested": True}]
    r = client.post(endpoint, json={"session_id": up["session_id"],
                                    "rows": rows})
    assert r.status_code == 200
    # the stub reads at 0.9, so the upload itself already carries suggestions
    # (stage 2 drops them); plus the one forged above -- none may be exported
    confirmed = [row for row in up["rows"] if not row["suggested"]]
    assert any(row["suggested"] for row in up["rows"])
    assert len(seen["rows"]) == len(confirmed)
    assert all(not row.suggested for row in seen["rows"])


# --- the queue API: failure paths, lifespan (batch design §1, §3) ---

@pytest.mark.parametrize("damage", ["missing", "empty_object", "not_json"])
def test_open_without_its_result_file_is_a_clean_409(damage, sample_pdf, stub_backend):
    """A ready drawing whose result.json is gone or damaged must not become a
    500 whose traceback logs the document's full path; the body says nothing
    about the file either."""
    import app.main as main
    sid = upload_pdf(client, sample_pdf)["session_id"]
    result = main.runtime().data.doc_dir(sid) / "result.json"
    if damage == "missing":
        result.unlink()
    elif damage == "empty_object":
        result.write_text("{}")
    else:
        result.write_text("{not json")
    r = TestClient(main.app, raise_server_exceptions=False).post(f"/api/documents/{sid}/open")
    assert r.status_code == 409
    assert r.json() == {"detail": "drawing files unavailable"}


def test_a_failed_open_does_not_rotate_the_writer(sample_pdf, stub_backend):
    """Opening rotates the writer token, which locks out the tab that held it.
    An open refused for missing files must not do that for nothing."""
    import app.main as main
    up = upload_pdf(client, sample_pdf)
    sid, writer = up["session_id"], up["writer"]
    (main.runtime().data.doc_dir(sid) / "result.json").unlink()
    assert client.post(f"/api/documents/{sid}/open").status_code == 409
    r = client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 1, "type": "accept", "ids": []}]})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("err,status", [(28, 507), (5, 500)])   # ENOSPC, EIO
def test_an_upload_that_cannot_be_stored_is_not_called_a_bad_pdf(
        err, status, sample_pdf, monkeypatch):
    """A full disk is the server's problem, not the user's file: only a PDF
    that will not parse is a 400. Nothing half-written is left behind, and
    the reply is a fixed sentence (an OSError's text names the path)."""
    import app.main as main

    def failing_open(*a, **k):
        raise OSError(err, "no", "/some/path")
    docs = main.runtime().data.docs
    monkeypatch.setattr(main, "open", failing_open, raising=False)
    with open(sample_pdf, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == status
    assert "path" not in r.json()["detail"] and "PDF" not in r.json()["detail"]
    assert list(docs.iterdir()) == []


def test_an_unparseable_upload_is_a_400_and_leaves_nothing(tmp_path):
    import app.main as main
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not a pdf at all")
    with open(bad, "rb") as f:
        r = client.post("/api/upload", files={"file": ("x.pdf", f, "application/pdf")})
    assert (r.status_code, r.json()["detail"]) == (400, "could not read the PDF")
    assert list(main.runtime().data.docs.iterdir()) == []


def test_upload_runs_off_the_event_loop():
    """File, PyMuPDF and SQLite work all block; a sync handler runs in the
    threadpool instead of stalling every other request."""
    import inspect
    import app.main as main
    assert not inspect.iscoroutinefunction(main.upload)


def test_retry_refuses_the_drawing_the_worker_is_still_running(
        sample_pdf, stub_backend, monkeypatch):
    """A cancelled row can still be the worker's current run (cancel is
    cooperative). Retrying it then would requeue it while the old run is
    still unwinding -- and that run's settle would silently undo the retry or
    the cancel. Refused until the run has returned."""
    import app.main as main
    seen = {}

    def extract_that_is_cancelled_midway(*a, **k):
        sid = seen["sid"]
        client.post(f"/api/documents/{sid}/cancel")
        r = client.post(f"/api/documents/{sid}/retry")
        seen["status"], seen["body"] = r.status_code, r.json()
        raise RuntimeError("never reached a result")
    monkeypatch.setattr(main, "extract", extract_that_is_cancelled_midway)
    sid = seen["sid"] = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    assert main.runtime().worker.run_once() == sid
    assert seen["status"] == 409, seen["body"]
    assert client.get(f"/api/documents/{sid}").json()["state"] == "cancelled"
    assert client.post(f"/api/documents/{sid}/retry").json()["state"] == "queued"


def test_a_running_drawing_reports_its_progress(sample_pdf, monkeypatch):
    import app.main as main
    seen = {}

    def extract_reporting(pdf_path, work_dir, dpi, backend, progress):
        progress("detect", "tile", 1, 3)
        seen["doc"] = client.get(f"/api/documents/{seen['sid']}").json()
        raise RuntimeError("stop here")
    monkeypatch.setattr(main, "extract", extract_reporting)
    sid = seen["sid"] = save_pdf(client, sample_pdf)["session_id"]
    client.post(f"/api/documents/{sid}/enqueue")
    main.runtime().worker.run_once()
    assert seen["doc"]["state"] == "extracting"
    assert seen["doc"]["progress"] == {"step": "detect", "detail": "tile",
                                       "current": 1, "total": 3}
    assert client.get(f"/api/documents/{sid}").json()["progress"] is None


def test_the_lifespan_starts_and_stops_the_worker():
    import app.main as main
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["worker_running"] is True
    assert main._RUNTIME is None


def test_after_shutdown_requests_are_refused_not_served_by_a_dead_worker():
    """A runtime built after shutdown would accept and queue drawings that no
    worker ever extracts. Refuse instead; the next lifespan start clears it."""
    import app.main as main
    with TestClient(main.app):
        pass
    r = TestClient(main.app, raise_server_exceptions=False).get("/api/documents/" + "0" * 32)
    assert r.status_code == 503
    assert main._RUNTIME is None
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["worker_running"] is True


def test_an_unwritable_review_dir_fails_the_boot(tmp_path, monkeypatch):
    import os
    import app.main as main
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    d = tmp_path / "ro-reviews"
    d.mkdir()
    os.chmod(d, 0o500)
    monkeypatch.setenv("SINDRI_REVIEW_DIR", str(d))
    try:
        with pytest.raises(OSError):
            with TestClient(main.app):
                pass
    finally:
        os.chmod(d, 0o700)
    assert main._RUNTIME is None


def test_a_failed_worker_start_leaves_no_runtime_behind(monkeypatch):
    import app.main as main
    from app.batch.worker import Worker

    def refuse(self):
        raise RuntimeError("another process holds the worker")
    monkeypatch.setattr(Worker, "start", refuse)
    with pytest.raises(RuntimeError):
        with TestClient(main.app):
            pass
    assert main._RUNTIME is None
