"""The import API (batch design §2): draft batches and the chunked upload
protocol over HTTP. Test PDFs are built in code (tests/batch/pdfs.py)."""
import asyncio
import hashlib
import inspect
import json
import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.batch.pdfs import pdf_bytes

client = TestClient(main.app)
CHUNK = 200


def new_batch(name="Lieferung"):
    r = client.post("/api/batches", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def new_upload(batch_id, data, name="a.pdf", rel_path=""):
    r = client.post("/api/uploads", json={
        "batch_id": batch_id, "name": name, "size": len(data), "rel_path": rel_path,
        "fingerprint": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    return r.json()


def put(upload_id, offset, chunk):
    return client.put(f"/api/uploads/{upload_id}?offset={offset}", content=chunk)


def send(batch_id, data, name="a.pdf", rel_path=""):
    up = new_upload(batch_id, data, name, rel_path)
    for off in range(0, len(data), CHUNK):
        assert put(up["upload_id"], off, data[off:off + CHUNK]).status_code == 200
    return up["upload_id"]


def import_file(data, name="a.pdf", batch_id=None):
    batch_id = batch_id or new_batch()["id"]
    r = client.post(f"/api/uploads/{send(batch_id, data, name)}/complete")
    assert r.status_code == 200, r.text
    return batch_id, r.json()


def test_a_file_uploaded_in_chunks_becomes_a_staged_drawing():
    data = pdf_bytes()
    batch_id, done = import_file(data)
    (row,) = done["documents"]
    assert client.get(f"/api/documents/{row['id']}").json()["state"] == "staged"
    view = client.get(f"/api/batches/{batch_id}").json()
    assert view["batch"]["draft"] is True
    assert [d["id"] for d in view["documents"]] == [row["id"]]
    c = view["counts"]
    assert (c["documents"], c["included"], c["excluded"]) == (1, 1, 0)
    assert (main.runtime().data.doc_dir(row["id"]) / "input.pdf").read_bytes() == data


def test_a_wrong_offset_answers_409_with_where_the_server_stands():
    data = pdf_bytes()
    up = new_upload(new_batch()["id"], data)
    r = put(up["upload_id"], CHUNK, data[CHUNK:2 * CHUNK])
    assert r.status_code == 409
    # The whole body: the fixed sentence AND the resync fields, nothing else.
    assert r.json() == {"detail": "offset does not match what the server holds",
                        "received": 0, "state": "receiving"}


def test_a_chunk_resent_after_a_lost_reply_is_409_and_written_once():
    data = pdf_bytes()
    up = new_upload(new_batch()["id"], data)
    assert put(up["upload_id"], 0, data[:CHUNK]).json() == {"received": CHUNK}
    r = put(up["upload_id"], 0, data[:CHUNK])
    assert (r.status_code, r.json()["received"]) == (409, CHUNK)
    part = main.runtime().data.part_path(up["upload_id"])
    assert part.read_bytes() == data[:CHUNK]


def test_re_posting_the_same_file_resumes_it():
    data = pdf_bytes()
    batch_id = new_batch()["id"]
    up = new_upload(batch_id, data)
    put(up["upload_id"], 0, data[:CHUNK])
    again = new_upload(batch_id, data)
    assert (again["upload_id"], again["received"], again["resumed"]) == (up["upload_id"], CHUNK, True)


def test_low_disk_is_507_and_health_reports_the_free_space(monkeypatch):
    rt = main.runtime()
    monkeypatch.setattr(rt.uploads, "min_free_bytes", 1 << 62)
    r = client.post("/api/uploads", json={"batch_id": new_batch()["id"], "name": "a.pdf",
                                          "size": 10, "fingerprint": "f" * 64})
    assert (r.status_code, r.json()["detail"]) == (507, "not enough free disk space on the server")
    health = client.get("/api/health").json()
    assert isinstance(health["disk_free_mb"], int) and health["min_free_mb"] == (1 << 62) // (1 << 20)


def test_every_test_runs_without_the_disk_reserve():
    assert main.runtime().uploads.min_free_bytes == 0


@pytest.mark.parametrize("method,path", [
    ("put", "/api/uploads/nope?offset=0"), ("post", "/api/uploads/nope/complete"),
    ("delete", "/api/uploads/nope"), ("put", f"/api/uploads/{'0' * 32}?offset=0"),
    ("post", f"/api/uploads/{'0' * 32}/complete"),
    ("get", "/api/batches/nope"), ("get", f"/api/batches/{'0' * 32}"),
])
def test_malformed_and_unknown_ids_are_404(method, path):
    kw = {"content": b"x"} if method == "put" else {}
    assert getattr(client, method)(path, **kw).status_code == 404


def test_an_upload_into_an_unknown_batch_is_404():
    r = client.post("/api/uploads", json={"batch_id": "0" * 32, "name": "a.pdf", "size": 1,
                                          "fingerprint": "f" * 64})
    assert r.status_code == 404


def test_a_batch_gets_a_default_name_and_can_be_renamed():
    b = client.post("/api/batches", json={}).json()
    # Only a fallback: the browser sends its own local name (upload.js).
    assert b["name"].startswith("Import ") and b["name"].endswith(" UTC") and b["draft"] is True
    r = client.patch(f"/api/batches/{b['id']}", json={"name": "  Lieferung 12 "})
    assert r.json()["name"] == "Lieferung 12"
    for bad in ["", "   ", "a\x01b"]:
        assert client.patch(f"/api/batches/{b['id']}", json={"name": bad}).status_code == 400


def test_a_batch_name_is_cleaned_on_create_too():
    """The repo stores names raw; only the API's clean_batch_name stands
    between a bidi override and the mass-export ZIP's folder names."""
    assert new_batch("  Lieferung 7 ")["name"] == "Lieferung 7"
    for bad in ["", "a‮b", "x" * 201]:
        r = client.post("/api/batches", json={"name": bad})
        assert (r.status_code, r.json()) == (400, {"detail": "not a usable batch name"})


def test_batches_are_listed_with_their_counts():
    batch_id, _ = import_file(pdf_bytes())
    listed = {b["id"]: b for b in client.get("/api/batches").json()["batches"]}
    assert (listed[batch_id]["documents"], listed[batch_id]["uploading"]) == (1, 0)


def test_an_unfinished_upload_can_be_removed():
    data = pdf_bytes()
    up = new_upload(new_batch()["id"], data)
    assert client.delete(f"/api/uploads/{up['upload_id']}").status_code == 200
    assert not main.runtime().data.part_path(up["upload_id"]).exists()
    assert client.delete(f"/api/uploads/{up['upload_id']}").status_code == 200   # idempotent


def test_errors_and_logs_never_carry_the_file_name(caplog):
    caplog.set_level(logging.DEBUG)
    r = client.post("/api/uploads", json={"batch_id": new_batch()["id"], "name": "Geheim.exe",
                                          "size": 1, "fingerprint": "f" * 64})
    assert r.status_code == 400 and "Geheim" not in r.text
    import_file(pdf_bytes(), name="Geheim-Flansch.pdf")
    assert "Geheim" not in "\n".join(rec.getMessage() for rec in caplog.records)


def test_a_chunk_over_the_bound_is_413_before_anything_is_written(monkeypatch):
    """MAX_CHUNK bounds one request's memory, not a file's size: lowered
    here so the test need not send 64 MiB."""
    import app.batch.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "MAX_CHUNK", 10)
    up = new_upload(new_batch()["id"], b"x" * 20)
    r = put(up["upload_id"], 0, b"x" * 11)
    assert (r.status_code, r.json()["detail"]) == (413, "chunk too large or of unknown length")
    assert main.runtime().data.part_path(up["upload_id"]).read_bytes() == b""
    assert put(up["upload_id"], 0, b"x" * 10).json() == {"received": 10}


def test_a_chunk_of_unknown_length_is_413():
    """A streamed body with no Content-Length could be any size."""
    up = new_upload(new_batch()["id"], b"x" * 20)
    r = client.put(f"/api/uploads/{up['upload_id']}?offset=0", content=iter([b"x" * 5]))
    assert r.status_code == 413
    assert main.runtime().data.part_path(up["upload_id"]).read_bytes() == b""


def _pieces(*pieces, disconnect=False):
    """Body messages; with `disconnect`, the client goes away after the last
    piece instead of finishing the body."""
    msgs = [{"type": "http.request", "body": p,
             "more_body": disconnect or i < len(pieces) - 1}
            for i, p in enumerate(pieces)]
    return msgs + ([{"type": "http.disconnect"}] if disconnect else [])


def _raw_put(path, query, headers, messages):
    """Drive the ASGI app directly: an HTTP client always sends an honest
    Content-Length and never vanishes mid-body, and the handler must cope
    with both. Returns (status, json body, messages never received)."""
    sent = []
    queue = list(messages)

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send_(msg):
        sent.append(msg)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "PUT", "scheme": "http", "path": path, "raw_path": path.encode(),
             "root_path": "", "query_string": query.encode(),
             "headers": [(k.encode(), v.encode()) for k, v in headers],
             "client": ("test", 1), "server": ("test", 80)}
    asyncio.run(main.app(scope, receive, send_))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body), queue


def test_a_lying_content_length_cannot_carry_more_than_the_bound(monkeypatch):
    """Declares 5 bytes, sends 16 in pieces: the read stops at the first
    piece past the bound -- the last is never even received -- and nothing
    reaches the part file."""
    import app.batch.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "MAX_CHUNK", 10)
    up = new_upload(new_batch()["id"], b"x" * 20)
    last = _pieces(b"x" * 4, b"x" * 4, b"x" * 4, b"y" * 4)[-1]
    status, body, unread = _raw_put(
        f"/api/uploads/{up['upload_id']}", "offset=0", [("content-length", "5")],
        _pieces(b"x" * 4, b"x" * 4, b"x" * 4, b"y" * 4))
    assert (status, body) == (413, {"detail": "chunk too large or of unknown length"})
    assert unread[:1] == [last]
    assert main.runtime().data.part_path(up["upload_id"]).read_bytes() == b""


def test_a_client_gone_mid_chunk_is_a_quiet_400_and_writes_nothing(caplog):
    """A dropped connection is routine on the resumable path (a closed lid,
    a flaky link): no 500, no traceback, and the offset stays where it was
    so the client resumes from it."""
    caplog.set_level(logging.DEBUG)
    up = new_upload(new_batch()["id"], b"x" * 20)
    status, body, _ = _raw_put(f"/api/uploads/{up['upload_id']}", "offset=0",
                               [("content-length", "10")], _pieces(b"x" * 4, disconnect=True))
    assert (status, body) == (400, {"detail": "the chunk did not arrive whole"})
    assert main.runtime().data.part_path(up["upload_id"]).read_bytes() == b""
    assert main.runtime().repo.get_upload(up["upload_id"])["received"] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_shutting_down_server_refuses_a_chunk_before_reading_it(monkeypatch):
    """503 before up to MAX_CHUNK bytes are read for nothing."""
    up = new_upload(new_batch()["id"], b"x" * 20)
    monkeypatch.setattr(main, "_SHUT_DOWN", True)
    msgs = _pieces(b"x" * 4, b"x" * 4)
    status, _, unread = _raw_put(f"/api/uploads/{up['upload_id']}", "offset=0",
                                 [("content-length", "8")], msgs)
    assert status == 503 and len(unread) == 2


@pytest.mark.parametrize("field,value", [("size", True), ("size", "7"), ("size", 7.0),
                                         ("last_modified", True), ("last_modified", "7")])
def test_numbers_are_strict_on_upload_create(field, value):
    """pydantic's lax mode would take `true` as 1 and "7" as 7 -- a size or
    a resume key the client never meant. Refused with the fixed 422."""
    req = {"batch_id": new_batch()["id"], "name": "a.pdf", "size": 7, "fingerprint": "f" * 64,
           field: value}
    r = client.post("/api/uploads", json=req)
    assert (r.status_code, r.json()) == (422, {"detail": "request rejected: malformed fields"})


def test_health_survives_an_unreadable_disk(monkeypatch, caplog):
    rt = main.runtime()

    def broken(path):
        raise PermissionError("/secret/path")
    monkeypatch.setattr(rt.uploads, "_free", broken)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["disk_free_mb"] is None
    assert "secret" not in "\n".join(rec.getMessage() for rec in caplog.records)


def test_a_malformed_import_request_never_echoes_its_fields():
    """FastAPI's default 422 body repeats the input -- here a file name."""
    r = client.post("/api/uploads", json={"batch_id": new_batch()["id"], "name": ["Geheim.pdf"],
                                          "size": "Kunde Meier", "fingerprint": "f" * 64})
    assert r.status_code == 422
    assert "Geheim" not in r.text and "Meier" not in r.text
    assert r.json() == {"detail": "request rejected: malformed fields"}


def test_a_malformed_batch_request_never_echoes_its_fields():
    r = client.post("/api/batches", json={"name": ["Kunde Meier"]})
    assert (r.status_code, r.json()) == (422, {"detail": "request rejected: malformed fields"})
    r = client.put(f"/api/uploads/{'0' * 32}?offset=Meier", content=b"x")
    assert r.status_code == 422 and "Meier" not in r.text


def test_blocking_handlers_run_off_the_event_loop():
    """Hashing, PDF inspection and SQLite all block; a plain def runs in the
    threadpool. The chunk PUT is async only to read its body, and hands the
    write and fsync to the threadpool itself."""
    for handler in (main.create_upload, main.complete_upload, main.delete_upload,
                    main.create_batch, main.get_batch):
        assert not inspect.iscoroutinefunction(handler)
