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


def test_including_an_out_of_scope_file_records_the_permission():
    _, done = import_file(pdf_bytes(pages=3))
    (row,) = done["documents"]
    assert row["included"] is False
    r = client.post(f"/api/documents/{row['id']}/include", json={"included": True})
    assert (r.json()["included"], r.json()["allowed_out_of_scope"]) == (True, True)


@pytest.mark.parametrize("value", ["no", "false", 0, 1, None])
def test_include_takes_only_a_real_boolean(value):
    """Lax pydantic reads "no", "false" and 0 as False and 1 as True -- an
    Include choice (and for an out-of-scope file, a permission) the user
    never sent. Refused, and the row keeps its own."""
    _, done = import_file(pdf_bytes())
    doc_id = done["documents"][0]["id"]
    r = client.post(f"/api/documents/{doc_id}/include", json={"included": value})
    assert r.status_code == 422
    batch = client.get(f"/api/documents/{doc_id}").json()["batch_id"]
    (row,) = client.get(f"/api/batches/{batch}").json()["documents"]
    assert row["included"] is True


def test_an_unreadable_pdf_cannot_be_included():
    _, done = import_file(b"not a pdf at all")
    r = client.post(f"/api/documents/{done['documents'][0]['id']}/include", json={"included": True})
    assert r.status_code == 409


def test_start_queues_the_included_and_drops_the_rest():
    batch_id = new_batch()["id"]
    _, ok = import_file(pdf_bytes(), batch_id=batch_id)
    _, multi = import_file(pdf_bytes(pages=2), name="b.pdf", batch_id=batch_id)
    _, bad = import_file(b"not a pdf at all", name="c.pdf", batch_id=batch_id)
    keep = ok["documents"][0]["id"]
    drop = [multi["documents"][0]["id"], bad["documents"][0]["id"]]
    r = client.post(f"/api/batches/{batch_id}/start")
    assert r.json() == {"queued": 1, "dropped": 2, "documents": [keep]}
    doc = client.get(f"/api/documents/{keep}").json()
    assert (doc["state"], doc["queue_position"]) == ("queued", 1)
    for d in drop:
        assert client.get(f"/api/documents/{d}").status_code == 404
        assert not main.runtime().data.doc_dir(d).exists()
    assert client.get(f"/api/batches/{batch_id}").json()["batch"]["draft"] is False


def test_start_wakes_the_worker(monkeypatch):
    woke = []
    monkeypatch.setattr(main.runtime().worker, "wake", lambda: woke.append(1))
    batch_id, _ = import_file(pdf_bytes())
    client.post(f"/api/batches/{batch_id}/start")
    assert woke == [1]


def test_start_is_refused_with_nothing_included_or_while_uploading():
    batch_id, done = import_file(pdf_bytes(pages=2))
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 409
    client.post(f"/api/documents/{done['documents'][0]['id']}/include", json={"included": True})
    new_upload(batch_id, pdf_bytes(), name="late.pdf")
    r = client.post(f"/api/batches/{batch_id}/start")
    assert r.status_code == 409 and "uploading" in r.json()["detail"]
    assert client.get(f"/api/batches/{batch_id}").json()["batch"]["draft"] is True


def test_start_carries_the_permission_onto_every_out_of_scope_drawing():
    batch_id, done = import_file(pdf_bytes(width=7000, height=5000))
    doc_id = done["documents"][0]["id"]
    client.post(f"/api/documents/{doc_id}/include", json={"included": True})
    client.post(f"/api/batches/{batch_id}/start")
    (row,) = client.get(f"/api/batches/{batch_id}").json()["documents"]
    assert (row["state"], row["out_of_scope"], row["allowed_out_of_scope"]) == ("queued", True, True)


def test_discarding_an_import_removes_its_files_and_parts():
    batch_id, done = import_file(pdf_bytes())
    up = new_upload(batch_id, pdf_bytes(), name="late.pdf")
    rt = main.runtime()
    doc_dir = rt.data.doc_dir(done["documents"][0]["id"])
    part = rt.data.part_path(up["upload_id"])
    assert doc_dir.is_dir() and part.exists()
    assert client.delete(f"/api/batches/{batch_id}").status_code == 200
    assert not doc_dir.exists() and not part.exists()
    assert client.get(f"/api/batches/{batch_id}").status_code == 404
    assert client.delete(f"/api/batches/{batch_id}").status_code == 200       # idempotent


def test_a_started_import_cannot_be_discarded():
    batch_id, _ = import_file(pdf_bytes())
    client.post(f"/api/batches/{batch_id}/start")
    assert client.delete(f"/api/batches/{batch_id}").status_code == 409


def test_start_with_the_draft_the_client_saw_starts_it():
    """The interim UI checks the draft holds exactly the drawing on screen,
    then starts it. Sending that id set lets the server do the check at the
    moment of Start, under the batch's lock."""
    batch_id, done = import_file(pdf_bytes())
    doc_id = done["documents"][0]["id"]
    r = client.post(f"/api/batches/{batch_id}/start", json={"expected_ids": [doc_id]})
    assert r.json() == {"queued": 1, "dropped": 0, "documents": [doc_id]}
    assert client.get(f"/api/documents/{doc_id}").json()["state"] == "queued"


def test_start_refuses_a_draft_that_gained_a_row_since_the_client_looked():
    """The race the UI's own check leaves open: another tab (or an earlier
    attempt's retried completion) stages a second copy between the UI's
    GET and its Start. Start would queue it unseen. Refused, changing
    nothing -- not even a marker."""
    rt = main.runtime()
    batch_id, first = import_file(pdf_bytes())
    _, late = import_file(pdf_bytes(), name="b.pdf", batch_id=batch_id)
    seen = first["documents"][0]["id"]
    ids = [seen, late["documents"][0]["id"]]
    r = client.post(f"/api/batches/{batch_id}/start", json={"expected_ids": [seen]})
    assert (r.status_code, r.json()) == (409, {"detail": "the import changed — reload it"})
    view = client.get(f"/api/batches/{batch_id}").json()
    assert view["batch"]["draft"] is True
    assert [(d["id"], d["state"]) for d in view["documents"]] == [(i, "staged") for i in ids]
    for i in ids:
        d = rt.data.doc_dir(i)
        assert (d / "input.pdf").exists() and not (d / ".importing").exists()


@pytest.mark.parametrize("body", [
    {"expected_ids": "0" * 32},                 # not a list
    {"expected_ids": ["Geheim.pdf"]},           # not an id
    {"expected_ids": ["0" * 31]},
    {"expected_ids": ["A" * 32]},               # ids are lower-case hex
    {"expected_ids": [7]},
    {"expectedIds": ["0" * 32]},                # misspelt: would silently drop the check
])
def test_a_malformed_start_request_gets_the_fixed_sentence_and_starts_nothing(body):
    batch_id, done = import_file(pdf_bytes())
    r = client.post(f"/api/batches/{batch_id}/start", json=body)
    assert (r.status_code, r.json()) == (422, {"detail": "request rejected: malformed fields"})
    assert "Geheim" not in r.text
    assert client.get(f"/api/documents/{done['documents'][0]['id']}").json()["state"] == "staged"


def test_the_document_view_names_its_batch():
    batch_id, done = import_file(pdf_bytes())
    assert client.get(f"/api/documents/{done['documents'][0]['id']}").json()["batch_id"] == batch_id


def test_include_start_and_discard_reject_malformed_ids():
    assert client.post("/api/documents/nope/include", json={"included": True}).status_code == 404
    assert client.post("/api/batches/nope/start").status_code == 404
    assert client.delete("/api/batches/nope").status_code == 404


def test_start_and_discard_leave_no_marker_behind_refused_or_not():
    """Both mark the dirs whose rows they may remove, so a crash after the
    commit leaves those sweepable (Task 9) -- and must take the markers off
    again on every path that does not end in removing the dir."""
    rt = main.runtime()
    batch_id, done = import_file(pdf_bytes(pages=2))
    doc_id = done["documents"][0]["id"]
    d = rt.data.doc_dir(doc_id)
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 409     # nothing included
    assert not (d / ".importing").exists()
    client.post(f"/api/documents/{doc_id}/include", json={"included": True})
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 200
    assert d.is_dir() and not (d / ".importing").exists()
    assert client.delete(f"/api/batches/{batch_id}").status_code == 409         # started
    assert not (d / ".importing").exists()


def _start_or_discard(step, batch_id):
    return (client.post(f"/api/batches/{batch_id}/start") if step == "start"
            else client.delete(f"/api/batches/{batch_id}"))


@pytest.mark.parametrize("step", ["start", "discard"])
def test_every_dir_is_marked_before_the_transaction(monkeypatch, step):
    """A crash after the commit must find every removed row's dir marked
    (the sweep's only licence to delete) -- so the markers are on disk when
    the repo is called, and the repo is handed the snapshot that was
    marked, so it can refuse a draft that no longer matches it."""
    rt = main.runtime()
    batch_id = new_batch()["id"]
    ids = [import_file(pdf_bytes(pages=p), name=f"{p}.pdf", batch_id=batch_id)[1]
           ["documents"][0]["id"] for p in (1, 2)]
    seen = {}
    name = "start_batch" if step == "start" else "delete_batch"
    real = getattr(rt.repo, name)

    def spy(batch, expected_ids=None):
        seen["marked"] = [(rt.data.doc_dir(i) / ".importing").exists() for i in ids]
        seen["expected"] = list(expected_ids)
        return real(batch, expected_ids=expected_ids)
    monkeypatch.setattr(rt.repo, name, spy)
    assert _start_or_discard(step, batch_id).status_code == 200
    assert seen == {"marked": [True, True], "expected": ids}


@pytest.mark.parametrize("step", ["start", "discard"])
def test_a_row_staged_after_the_snapshot_refuses_and_changes_nothing(monkeypatch, caplog, step):
    """The snapshot is what was marked. A row it does not hold has an
    unmarked dir that a crash after the commit would leak forever, so the
    step is refused (the UI reloads, nothing retries silently), its markers
    come off, and every row, dir and the draft stay as they were."""
    caplog.set_level(logging.DEBUG)
    rt = main.runtime()
    batch_id, first = import_file(pdf_bytes(), name="Geheim-1.pdf")
    _, late = import_file(pdf_bytes(pages=2), name="Geheim-2.pdf", batch_id=batch_id)
    ids = [first["documents"][0]["id"], late["documents"][0]["id"]]
    monkeypatch.setattr(rt.repo, "batch_document_ids", lambda b: ids[:1])
    r = _start_or_discard(step, batch_id)
    assert (r.status_code, r.json()) == (409, {"detail": "the import changed — reload it"})
    view = client.get(f"/api/batches/{batch_id}").json()
    assert view["batch"]["draft"] is True
    assert [(d["id"], d["state"]) for d in view["documents"]] == [(i, "staged") for i in ids]
    for i in ids:
        d = rt.data.doc_dir(i)
        assert (d / "input.pdf").exists() and not (d / ".importing").exists()
    assert "Geheim" not in r.text + "\n".join(rec.getMessage() for rec in caplog.records)


def test_a_completion_cannot_slip_between_the_snapshot_and_the_commit(monkeypatch):
    """A completion commits its rows and only THEN takes its own markers
    off. Were a Start or Discard to snapshot and mark in between, that late
    unlink would strip a marker it had just written, and a crash after its
    commit would leave an unmarked, rowless dir nothing ever removes. So a
    completion's commit-and-unmark and a Start/Discard's snapshot-to-commit
    exclude each other."""
    import threading
    rt = main.runtime()
    batch_id = new_batch()["id"]
    uid = send(batch_id, pdf_bytes())
    real_finish = rt.repo.finish_completion
    results = {}

    def finish_then_race(upload_id, docs, ignored):
        rows = real_finish(upload_id, docs, ignored)
        t = threading.Thread(target=rt.uploads.discard_batch, args=(batch_id,))
        t.start()
        t.join(0.3)
        # Not "is the thread alive": after its commit the discard waits on
        # the UPLOAD's lock to remove the part anyway. What must not have
        # happened yet is its snapshot-to-commit -- the rows still stand.
        results["rows_meanwhile"] = rt.repo.batch_document_ids(batch_id)
        results["thread"] = t
        return rows
    monkeypatch.setattr(rt.repo, "finish_completion", finish_then_race)
    done = client.post(f"/api/uploads/{uid}/complete").json()
    results["thread"].join(5)
    assert not results["thread"].is_alive()
    assert results["rows_meanwhile"] == [done["documents"][0]["id"]]
    # The discard ran once the completion was done, saw its row, removed it.
    assert not rt.data.doc_dir(done["documents"][0]["id"]).exists()
    assert client.get(f"/api/batches/{batch_id}").status_code == 404


def test_a_drawing_removed_while_marking_is_a_clean_409_not_a_500(monkeypatch):
    """DELETE /api/documents/{id} removes the row, then the dir. Landing
    between _mark's is_dir() and its marker write, the write finds no dir:
    that is the import changing, not the disk failing -- the dir is skipped
    and the snapshot check refuses (409), never a StorageFault (500)."""
    from pathlib import Path
    rt = main.runtime()
    batch_id = new_batch()["id"]
    _, a = import_file(pdf_bytes(), batch_id=batch_id)
    _, b = import_file(pdf_bytes(), name="b.pdf", batch_id=batch_id)
    keep, victim = a["documents"][0]["id"], b["documents"][0]["id"]
    real_doc_dir = rt.data.doc_dir

    class Racy(type(Path())):
        def is_dir(self, *args, **kw):
            ok = super().is_dir(*args, **kw)
            if self.name == victim:
                client.delete(f"/api/documents/{victim}")   # the user removes it now
            return ok
    monkeypatch.setattr(rt.data, "doc_dir", lambda i: Racy(real_doc_dir(i)))
    r = client.post(f"/api/batches/{batch_id}/start")
    monkeypatch.setattr(rt.data, "doc_dir", real_doc_dir)
    assert (r.status_code, r.json()) == (409, {"detail": "the import changed — reload it"})
    assert not (rt.data.doc_dir(keep) / ".importing").exists()
    assert client.get(f"/api/batches/{batch_id}").json()["batch"]["draft"] is True


@pytest.mark.parametrize("step,detail", [
    ("start", "this import has already started"),
    ("discard", "only an import that has not started can be discarded")])
def test_a_started_import_is_refused_without_marking_the_workers_dirs(monkeypatch, step, detail):
    """A started batch's drawings are the worker's. Marking them would put
    a crash-sweep marker on a dir mid-extraction for nothing: the refusal
    is certain, so it comes first and no marker is ever written."""
    rt = main.runtime()
    batch_id, done = import_file(pdf_bytes())
    assert client.post(f"/api/batches/{batch_id}/start").status_code == 200
    calls = []
    real_mark = rt.uploads._mark
    monkeypatch.setattr(rt.uploads, "_mark", lambda *a: calls.append(a) or real_mark(*a))
    r = _start_or_discard(step, batch_id)
    assert (r.status_code, r.json()) == (409, {"detail": detail})
    assert calls == []
    assert not (rt.data.doc_dir(done["documents"][0]["id"]) / ".importing").exists()


def test_a_malformed_include_request_gets_the_fixed_sentence():
    _, done = import_file(pdf_bytes())
    r = client.post(f"/api/documents/{done['documents'][0]['id']}/include",
                    json={"included": "Kunde Meier"})
    assert (r.status_code, r.json()) == (422, {"detail": "request rejected: malformed fields"})


def test_discarding_forgets_the_batch_lock():
    """One lock per batch would otherwise outlive every discarded (or
    never-existing) batch for the life of the process."""
    rt = main.runtime()
    batch_id, _ = import_file(pdf_bytes())
    assert client.delete(f"/api/batches/{batch_id}").status_code == 200
    assert batch_id not in rt.uploads._batch_locks
    unknown = "0" * 32
    assert client.delete(f"/api/batches/{unknown}").status_code == 200
    assert unknown not in rt.uploads._batch_locks


def test_a_failing_wake_does_not_fail_a_committed_start(monkeypatch, caplog):
    """Start has committed when the worker is woken; a 500 then would tell
    the user it failed and invite a retry that is refused. The worker's
    own poll picks the queue up anyway."""
    caplog.set_level(logging.DEBUG)
    rt = main.runtime()

    def boom():
        raise RuntimeError("Geheim")
    monkeypatch.setattr(rt.worker, "wake", boom)
    batch_id, _ = import_file(pdf_bytes())
    r = client.post(f"/api/batches/{batch_id}/start")
    assert (r.status_code, r.json()["queued"]) == (200, 1)
    assert "Geheim" not in "\n".join(rec.getMessage() for rec in caplog.records)


class _Crash(Exception):
    """Stands for the process dying at that point."""


def test_a_crash_after_start_commits_leaves_only_sweepable_dirs(monkeypatch):
    """Start removes rows first, files second. Dying in between leaves the
    dropped drawing's dir with its marker and no row (swept at the next
    start) and the queued one's dir marked with its row (only unmarked)."""
    rt = main.runtime()
    batch_id = new_batch()["id"]
    _, keep = import_file(pdf_bytes(), batch_id=batch_id)
    _, drop = import_file(pdf_bytes(pages=2), name="b.pdf", batch_id=batch_id)
    keep_dir = rt.data.doc_dir(keep["documents"][0]["id"])
    drop_dir = rt.data.doc_dir(drop["documents"][0]["id"])

    def crash(*a, **k):
        raise _Crash()
    # The first file step after Start's commit is taking the queued rows'
    # markers off. (A context, not undo(): undo would also take back
    # conftest's per-test data dir.)
    with monkeypatch.context() as m, pytest.raises(_Crash):
        m.setattr(rt.uploads, "_unmark", crash)
        client.post(f"/api/batches/{batch_id}/start")
    assert (drop_dir / ".importing").exists() and (keep_dir / ".importing").exists()
    # The next process starts after the crash: the markers predate it.
    import time
    from app.batch import uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "PROCESS_STARTED", time.time() + 1)
    assert rt.uploads.sweep_unfinished_imports() == 1
    assert not drop_dir.exists()
    assert keep_dir.is_dir() and not (keep_dir / ".importing").exists()
    assert client.get(f"/api/documents/{keep['documents'][0]['id']}").json()["state"] == "queued"


def test_a_new_import_sheds_week_old_unfinished_uploads():
    """A server that is never restarted still sheds them."""
    old_batch = new_batch()["id"]
    up = new_upload(old_batch, pdf_bytes())
    main.runtime().conn.execute("UPDATE upload SET updated_at = '2026-01-01T00:00:00.000000+00:00'")
    new_batch()
    assert client.get(f"/api/batches/{old_batch}").json()["uploads"] == []
    assert not main.runtime().data.part_path(up["upload_id"]).exists()


def test_a_new_import_never_sweeps_marked_dirs():
    """While requests run, an in-flight completion's dirs look exactly like
    crash leftovers: only the startup hook, under the worker lock, sweeps."""
    in_flight = main.runtime().data.doc_dir("a" * 32)
    in_flight.mkdir()
    (in_flight / ".importing").write_bytes(b"")
    new_batch()
    assert (in_flight / ".importing").exists()


def test_a_failing_cleanup_does_not_refuse_a_new_import(monkeypatch, caplog):
    def boom():
        raise OSError("/data/Kunde")
    monkeypatch.setattr(main.runtime().uploads, "cleanup_stale", boom)
    assert new_batch()["draft"] is True
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "OSError" in text and "Kunde" not in text
