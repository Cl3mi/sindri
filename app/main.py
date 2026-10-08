import json
import logging
import os
import re
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, List, Optional
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.types import Scope
from starlette.responses import Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StringConstraints
from app.models import Characteristic
from app.pipeline.extract import extract
from app.pipeline.ocr import get_backend, backend_status
from app.pipeline.parser import parse_value
from app.pipeline import policy_rules as pr
from app.pipeline.review import review_flags
from app.pipeline.place import place_balloons
from app.batch.export_render import InputUnavailable, exportable, render_export
from app.review.header import build_header
from app.review.store import ReviewError
from app.batch import uploads as batch_uploads
from app.batch.paths import DOC_ID_RE
from app.batch.repo import BatchError, InvalidTransition
from app.batch.runtime import Runtime
from app.batch.pdf_facts import EXTRACT_DPI
from app.batch.staging import clean_batch_name, out_of_scope
from PIL import Image

log = logging.getLogger(__name__)

# Load the OCR backend ONCE at startup (the VLM model is multi-GB — never
# reload it per request) and reuse it for every upload.
_BACKEND = get_backend()


def _review_consent() -> bool:
    return os.environ.get("SINDRI_REVIEW_CONSENT", "").lower() in ("1", "true", "yes")


def _extract(pdf_path, work_dir, progress):
    # Late-bound on purpose: tests monkeypatch `extract` and `_BACKEND` on
    # this module, and the worker must see those patches. EXTRACT_DPI is the
    # dpi staging's `oversized` badge is measured at (app/batch/pdf_facts.py).
    return extract(pdf_path, work_dir=work_dir, dpi=EXTRACT_DPI, backend=_BACKEND,
                   progress=progress)


def _header(pdf_path):
    return build_header(pdf_path, _review_consent())


_RUNTIME: Optional[Runtime] = None
# Set when the lifespan shuts down, cleared when the next one starts. A
# request arriving in between must not lazily build a fresh Runtime: its
# worker would never start, so drawings queued there would never extract.
_SHUT_DOWN = False
# Two first requests racing on a cold start would otherwise each build a
# Runtime -- two connections, two workers' worth of state, one leaked.
_RUNTIME_LOCK = threading.Lock()


def runtime() -> Runtime:
    """Built on first use from SINDRI_DATA_DIR / SINDRI_REVIEW_DIR. Lazy, so
    importing this module (every API test does) never touches a real data
    dir; tests/conftest.py points both at a per-test tmp dir."""
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _SHUT_DOWN:
            raise HTTPException(status_code=503, detail="the server is shutting down")
        if _RUNTIME is None:
            _RUNTIME = Runtime.from_env(_extract, _header)
        return _RUNTIME


@asynccontextmanager
async def lifespan(app):
    global _RUNTIME, _SHUT_DOWN
    with _RUNTIME_LOCK:
        _SHUT_DOWN = False
    rt = runtime()   # a bad data/review dir fails the boot, not the first drawing
    try:
        rt.start()
    except BaseException:
        # Otherwise the half-booted runtime (open index, no worker) would be
        # what every later request -- or the next boot attempt -- gets.
        with _RUNTIME_LOCK:
            _RUNTIME = None
        rt.close()
        raise
    try:
        yield
    finally:
        with _RUNTIME_LOCK:
            _SHUT_DOWN = True
            _RUNTIME = None
        rt.close()


app = FastAPI(title="Sindri", lifespan=lifespan)


@app.exception_handler(BatchError)
@app.exception_handler(ReviewError)
async def _domain_error(request, exc):
    # Safe to echo: these messages carry ids[:8], state names and fixed
    # sentences, never client text. `extra` carries numbers and a state name
    # only (an upload's received offset), so a client can resync from it.
    return JSONResponse({"detail": str(exc), **getattr(exc, "extra", {})},
                        status_code=exc.status)


# Routes whose request bodies carry client file and folder names -- and the
# Include toggle, part of the same staging surface, so the import UI meets
# one 422 shape.
_IMPORT_PATHS = ("/api/uploads", "/api/batches")
_INCLUDE_PATH = re.compile(r"\A/api/documents/[^/]+/include\Z")


@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc):
    """FastAPI's default 422 body echoes the offending input; on the import
    routes that is a client file or folder name (CLAUDE.md §1). A fixed
    sentence there; the default everywhere else."""
    path = request.url.path
    if path.startswith(_IMPORT_PATHS) or _INCLUDE_PATH.fullmatch(path):
        return JSONResponse({"detail": "request rejected: malformed fields"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


def _doc_dir(doc_id: str) -> Path:
    try:
        return runtime().data.doc_dir(doc_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="unknown document")


def _batch_id(batch_id: str) -> str:
    if not DOC_ID_RE.fullmatch(batch_id or ""):
        raise HTTPException(status_code=404, detail="unknown batch")
    return batch_id


def _batch_name(name) -> str:
    # The repo stores a name as given; this is the only gate between a bidi
    # override or a control character and the mass-export ZIP's folders.
    clean = clean_batch_name(name)
    if clean is None:
        raise HTTPException(status_code=400, detail="not a usable batch name")
    return clean


def _doc_view(doc_id: str) -> dict:
    rt = runtime()
    doc = rt.repo.get(doc_id)
    return {"id": doc["id"], "file_name": doc["file_name"], "batch_id": doc["batch_id"],
            "pages": doc["pages"],
            "state": doc["state"], "error_type": doc["error_type"],
            "final_revision": doc["final_revision"],
            "exported_revision": doc["exported_revision"],
            "queue_position": rt.repo.queue_position(doc_id),
            "progress": rt.worker.progress_of(doc_id)}


class ReadRegionRequest(BaseModel):
    session_id: str
    box: List[float]        # [x0, y0, x1, y1] image-space pixels


class EventsRequest(BaseModel):
    writer: str
    events: List[dict]


class SealRequest(BaseModel):
    writer: str
    final_seq: int
    rows: List[Characteristic]       # every row incl. suggestions; extras like `reviewed` are ignored
    reviewed_ids: List[str] = []


class BatchCreate(BaseModel):
    name: Optional[str] = None


class BatchRename(BaseModel):
    name: str


class UploadCreate(BaseModel):
    batch_id: str
    name: str
    # Strict: lax pydantic reads `true` as 1, "7" as 7 and 7.0 as 7 -- a
    # size or a resume key the client never sent. Those get the fixed 422.
    size: StrictInt
    fingerprint: str
    rel_path: str = ""
    last_modified: Optional[StrictInt] = None


class IncludeRequest(BaseModel):
    included: StrictBool        # lax pydantic would read "no" or 0 as a choice


# DOC_ID_RE's shape, strict: lax pydantic would coerce a number to a string.
# pydantic-core's Rust regex has no \A/\Z; its ^ and $ are already strict
# (no match before a trailing newline, unlike Python's $).
DocId = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{32}$")]


class StartRequest(BaseModel):
    # Forbid unknown keys: a misspelt `expected_ids` would otherwise be
    # dropped silently, and with it the very check the client asked for.
    model_config = ConfigDict(extra="forbid")
    expected_ids: Optional[List[DocId]] = None


class ExportRequest(BaseModel):
    document_ids: List[DocId]


@app.get("/api/health")
def health():
    status = backend_status()
    status["ocr_backend_active"] = type(_BACKEND).__name__
    rt = runtime()
    status["worker_running"] = rt.worker.running
    # The free-disk reserve (batch design §5): what is left, and the floor
    # below which uploads answer 507.
    try:
        status["disk_free_mb"] = rt.uploads.free_mb()
    except OSError as e:
        # Health must still answer; None says "not measured", never a
        # plausible number. TYPE only: the error's text names the path.
        log.warning("health: free disk space unavailable: %s", type(e).__name__)
        status["disk_free_mb"] = None
    status["min_free_mb"] = rt.uploads.min_free_bytes // batch_uploads.MIB
    return status


@app.post("/api/batches")
def create_batch(req: BatchCreate):
    """A draft import (decision 10). The browser names it after the ZIP or
    folder, or its LOCAL date and time (decision 27). A name-less request
    gets a fallback in the server's clock, labelled UTC because the server
    cannot know the user's zone. Editable later.

    Also sheds week-old unfinished uploads, stray part files and empty
    drafts, so a server that is never restarted still does. Never the
    docs/ sweep: that runs only at worker start, under the worker lock."""
    rt = runtime()
    try:
        rt.uploads.cleanup_stale()
    except Exception as e:
        # Housekeeping: a fault in it must not refuse a new import.
        log.warning("stale upload cleanup failed: %s", type(e).__name__)
    name = (_batch_name(req.name) if req.name is not None
            else datetime.now(timezone.utc).strftime("Import %Y-%m-%d %H:%M UTC"))
    return rt.repo.create_batch(name)


@app.get("/api/batches")
def list_batches():
    return {"batches": runtime().repo.list_batches()}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    """The staging view's data: rows with their facts and Include state,
    unfinished uploads, ignored ZIP entries, and counts that reconcile."""
    return runtime().repo.staging(_batch_id(batch_id))


@app.patch("/api/batches/{batch_id}")
def rename_batch(batch_id: str, req: BatchRename):
    return runtime().repo.rename_batch(_batch_id(batch_id), _batch_name(req.name))


@app.post("/api/uploads")
def create_upload(req: UploadCreate):
    """Start -- or, for the same file in the same draft, resume -- a chunked
    upload (batch design §2). Answers the offset to send from."""
    return runtime().uploads.create(
        batch_id=req.batch_id, name=req.name, size=req.size,
        last_modified=req.last_modified, rel_path=req.rel_path,
        fingerprint=req.fingerprint)


_CHUNK_TOO_LARGE = "chunk too large or of unknown length"


@app.put("/api/uploads/{upload_id}")
async def put_upload_chunk(upload_id: str, offset: int, request: Request):
    """One chunk at `offset`. Async only to read the body; the write and its
    fsync block, so they run in the threadpool. A 409 carries `received`:
    the client continues from there, never from what it thinks it sent.

    Bounded by MAX_CHUNK: a missing or larger Content-Length is refused
    before a byte is read, and the read itself stops at the bound in case
    the header lies. That bounds one REQUEST's memory, not a file: a file
    of any size still arrives in as many chunks as it needs (decision 29)."""
    # First: a server shutting down answers 503 before reading up to
    # MAX_CHUNK bytes it would only throw away.
    rt = runtime()
    limit = batch_uploads.MAX_CHUNK          # read per call: tests lower it
    try:
        declared = int(request.headers["content-length"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=413, detail=_CHUNK_TOO_LARGE)
    if declared < 0 or declared > limit:
        raise HTTPException(status_code=413, detail=_CHUNK_TOO_LARGE)
    body = bytearray()
    try:
        async for piece in request.stream():
            body += piece
            if len(body) > limit:
                raise HTTPException(status_code=413, detail=_CHUNK_TOO_LARGE)
    except ClientDisconnect:
        # Routine on a resumable path (a closed lid, a dropped link), not a
        # fault: nothing was written and the offset has not moved, so the
        # client resumes from it. No traceback; the id only once it is known
        # to be an id, never anything the client chose.
        log.info("upload %s: client gone mid-chunk, nothing written",
                 upload_id[:8] if DOC_ID_RE.fullmatch(upload_id) else "(malformed)")
        raise HTTPException(status_code=400, detail="the chunk did not arrive whole")
    # The bytearray itself: a bytes() copy would double the request's peak.
    return await run_in_threadpool(rt.uploads.put, upload_id, offset, body)


@app.post("/api/uploads/{upload_id}/complete")
def complete_upload(upload_id: str):
    """Verify, hash and stage. Idempotent: a retry after a lost reply gets
    the same documents. A plain def: hashing and PDF inspection block."""
    return runtime().uploads.complete(upload_id)


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: str):
    runtime().uploads.delete(upload_id)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/include")
def include_document(doc_id: str, req: IncludeRequest):
    """Tick or untick a staged drawing's Include box. Including an
    out-of-scope file is the explicit permission (decision 26) and is
    stored; an unreadable PDF is refused (409)."""
    _doc_dir(doc_id)
    return runtime().repo.set_included(doc_id, req.included)


@app.post("/api/batches/{batch_id}/start")
def start_batch(batch_id: str, req: Optional[StartRequest] = None):
    """Queue the included drawings, drop the excluded ones with their files,
    end the draft (design §2). The only way a staged drawing is queued. A
    refusal (409) changes nothing; the UI reloads and the user decides
    again -- never a silent retry.

    `expected_ids`, when sent, is the drawings the client SAW in the draft:
    a draft that holds any other set (a copy staged by another tab between
    the client's look and this request) is refused with "the import
    changed", so nothing is queued that the user did not see. Optional:
    the interim UI sends it; omitted, the draft is started as it is."""
    rt = runtime()
    expected = req.expected_ids if req is not None else None
    out = rt.uploads.start_batch(_batch_id(batch_id), expected_ids=expected)
    try:
        rt.worker.wake()
    except Exception as e:
        # Start has COMMITTED: a 500 now would say it failed and invite a
        # retry that is refused. The worker's own poll finds the queue.
        log.warning("batch %s: worker not woken: %s", batch_id[:8], type(e).__name__)
    log.info("batch %s started: %d queued, %d dropped",
             batch_id[:8], len(out["queued"]), len(out["dropped"]))
    return {"queued": len(out["queued"]), "dropped": len(out["dropped"]),
            "documents": out["queued"]}


@app.delete("/api/batches/{batch_id}")
def discard_batch(batch_id: str):
    """Discard an import that has not started: its staged drawings, its
    unfinished uploads, their files. Idempotent; a started batch is 409."""
    runtime().uploads.discard_batch(_batch_id(batch_id))
    return {"ok": True}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    _doc_dir(doc_id)
    return _doc_view(doc_id)


# Subtracted from the clock read BEFORE a listing's query runs, so the
# cursor handed back is never later than a write that could still be
# in flight when that query scans the table (decision 11, §2 of task 6's
# REQUIRED review notes). The margin only ever makes the client re-receive a
# row it already has -- harmless, since worklist.js merges incoming rows by
# id -- never skip one.
_CURSOR_MARGIN = timedelta(seconds=5)


@app.get("/api/documents")
def list_documents(batch_id: Optional[str] = None, state: Optional[str] = None,
                   search: Optional[str] = None, needs_me: bool = False,
                   since: Optional[str] = None):
    """The work list (batch design §3): every drawing, or a filtered slice,
    diffable by `since` for the 3 s poll. Flagged/open/unchecked are
    backfilled lazily here for a row whose review record predates migration
    4, or whose counts a race left stale (CLAUDE.md §4: NULL means "not
    measured", and this is the one place that measures it after the fact).
    Each row's `store.progress()` call takes and releases the review store's
    own lock on its own -- this loop never holds one lock across many
    sessions at once, so a long listing cannot starve a reviewer's journal
    write."""
    rt = runtime()
    # Read the clock BEFORE the query, not after and not from the rows it
    # returns: a write that commits in the gap between this read and the
    # query's own scan must still be caught by the NEXT poll, and a cursor
    # computed only from what this poll happened to see could sit AFTER such
    # a write's timestamp and so never satisfy `updated_at >= since` again.
    cursor = rt.repo.now() - _CURSOR_MARGIN
    rows = rt.repo.list_documents(batch_id=batch_id, state=state, search=search,
                                  needs_me=needs_me, since=since)
    out = []
    for r in rows:
        if r["flagged_total"] is None and r["state"] in ("ready", "in_review", "finished"):
            try:
                counts = rt.store.progress(r["id"])
                rt.repo.update_progress(r["id"], counts)
                r = {**r, **counts}
            except (ReviewError, OSError, ValueError, KeyError, TypeError) as e:
                # Left NULL: the next review event, or another poll's
                # backfill, catches up. Never worth failing the whole list
                # over one drawing's journal. TYPE only (CLAUDE.md §1).
                log.warning("progress backfill failed for %s: %s", r["id"][:8], type(e).__name__)
        out.append({
            "id": r["id"], "file_name": r["file_name"], "rel_path": r["rel_path"],
            "batch_id": r["batch_id"], "batch_name": r["batch_name"], "state": r["state"],
            "error_type": r["error_type"], "priority": r["priority"],
            "flagged_total": r["flagged_total"], "flagged_open": r["flagged_open"],
            "unchecked": r["unchecked"],
            "out_of_scope": out_of_scope(r["pages"], r["oversized"]),
            "allowed_out_of_scope": bool(r["allowed_out_of_scope"]),
            "final_revision": r["final_revision"], "exported_revision": r["exported_revision"],
            "updated_at": r["updated_at"],
        })
    summary = rt.repo.queue_summary()
    summary["eta_seconds"] = rt.worker.eta_seconds(summary["queued"])
    return {"documents": out, "cursor": cursor.isoformat(timespec="microseconds"),
            "queue": summary}


@app.post("/api/documents/{doc_id}/move_to_front")
def move_document_to_front(doc_id: str):
    """Queue control (batch design §2): priority = current max + 1.
    `Repo.move_to_front` raises InvalidTransition (409) off any state but
    `queued` -- the domain error handler turns that into the reply."""
    _doc_dir(doc_id)
    runtime().repo.move_to_front(doc_id)
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/reextract")
def reextract_document(doc_id: str):
    """Re-run extraction for a `ready` drawing nobody has reviewed yet
    (batch design §2 queue controls): refused the instant a single review
    event exists, or while the drawing is still the worker's current job --
    Retry (failed/cancelled only) is the control for a drawing no one's work
    is at stake in; this one discards a proposal outright.

    The has_events() check and the stash-rename-plus-transition run inside
    ONE call to `store.unless_reviewed`, holding the review store's own lock
    for both: a plain "if not has_events(): ... do it ..." here would leave a
    window in which a concurrent append() lands unseen between the check and
    the transition, and a re-extract would then silently discard an event the
    reviewer believes was saved.

    The stale result is made unusable by RENAMING it out of the way, never
    by deleting it first: if `repo.reextract` -- or anything else in the
    inner function -- then raises for ANY reason (not only a BatchError: a
    locked database, a disk fault, this process being killed is the one case
    this handler cannot catch at all, which is what the startup recovery
    sweep in Runtime is for), the file is renamed straight back, so a drawing
    is never left without a result a reviewer could open. Only once the
    transition has actually committed is the stash discarded -- the worker's
    `_run` only re-extracts when `result.json` is missing, so leaving it in
    place would make a 'fresh' extraction silently replay the stale one. The
    review session itself is left for the worker's own `store.create` to
    replace when it next re-extracts this id -- `store.create` already
    discards an untouched, unsealed session outright (app/review/store.py)."""
    work = _doc_dir(doc_id)
    rt = runtime()
    doc = rt.repo.get(doc_id)
    if rt.worker.current == doc_id:
        raise InvalidTransition(
            "the extraction is still stopping; re-extract once it has")
    if doc["state"] != "ready":
        raise InvalidTransition(f"document is {doc['state']}, not ready")
    result_path = work / "result.json"
    stashed = work / "result.reextract.json"

    def _stash_and_requeue():
        try:
            result_path.rename(stashed)
        except OSError as e:
            log.warning("reextract %s: result unavailable: %s", doc_id[:8], type(e).__name__)
            raise HTTPException(status_code=409, detail="drawing files unavailable")
        try:
            rt.repo.reextract(doc_id)
        except BaseException:
            # ANY failure after the rename -- not only a refusal the domain
            # handler understands -- must put the file back. A bare except
            # is deliberate here: a drawing must never be left without a
            # result a reviewer could open, whatever broke.
            stashed.rename(result_path)
            raise
        stashed.unlink(missing_ok=True)

    rt.store.unless_reviewed(doc_id, _stash_and_requeue)
    try:
        rt.worker.wake()
    except Exception as e:
        log.warning("document %s: worker not woken: %s", doc_id[:8], type(e).__name__)
    return _doc_view(doc_id)


@app.post("/api/queue/pause")
def pause_queue():
    """Queue control (batch design §2): the worker simply stops claiming a
    new drawing; one already extracting runs to completion."""
    runtime().repo.set_paused(True)
    return {"paused": True}


@app.post("/api/queue/resume")
def resume_queue():
    rt = runtime()
    rt.repo.set_paused(False)
    try:
        rt.worker.wake()
    except Exception as e:
        log.warning("queue resume: worker not woken: %s", type(e).__name__)
    return {"paused": False}


@app.post("/api/documents/{doc_id}/cancel")
def cancel_document(doc_id: str):
    """Queued: never runs. Extracting: the worker unwinds at its next
    progress report (it reads this state, not a flag) -- which can be many
    minutes away, since detection reports nothing while it runs."""
    _doc_dir(doc_id)
    runtime().repo.mark_cancelled(doc_id)
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/retry")
def retry_document(doc_id: str):
    """Refused while the worker is still unwinding this drawing's cancelled
    run: requeued now, it would be claimed beside that run, and the old run's
    settle would quietly overwrite the new state."""
    _doc_dir(doc_id)
    rt = runtime()
    if rt.worker.current == doc_id:
        raise InvalidTransition(
            "the extraction is still stopping; retry once it has")
    rt.repo.retry(doc_id)
    rt.worker.wake()
    return _doc_view(doc_id)


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    """Remove a drawing that is not being extracted. Its review record stays
    (review records are never removed with the files). Idempotent.

    The repo refuses only an `extracting` row. Cancel is cooperative, so a
    cancelled row can still be the worker's current job for a while; removing
    its files under the running extraction is refused here too. (The worker
    also copes with a row that vanishes mid-run, but only this check keeps
    the files from being pulled out from under it.)"""
    work = _doc_dir(doc_id)
    rt = runtime()
    if rt.worker.current == doc_id:
        raise InvalidTransition(
            "the extraction is still stopping; remove the drawing once it has")
    rt.repo.delete(doc_id)
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/open")
def open_document(doc_id: str):
    """Everything the browser needs to rebuild the review exactly where it
    stopped, plus a fresh writer token: whichever tab held the old one is
    refused from now on (batch design §3)."""
    work = _doc_dir(doc_id)
    rt = runtime()
    doc = rt.repo.get(doc_id)
    if doc["state"] not in ("ready", "in_review", "finished"):
        raise HTTPException(status_code=409,
                            detail=f"drawing is {doc['state']}, not ready for review")
    # Read BEFORE store.open(): opening rotates the writer, which locks out
    # whichever tab held it, and an open refused here must not do that.
    try:
        result = json.loads((work / "result.json").read_text(encoding="utf-8"))
        extras = {"notes": result["notes"], "marks": result["marks"],
                  "title_block": result["title_block"]}
    except (OSError, ValueError, KeyError, TypeError) as e:
        # Handled, not a 500: the traceback would log the full path, and
        # with it the whole document id. TYPE and id[:8] only.
        log.warning("open %s: result unavailable: %s", doc_id[:8], type(e).__name__)
        raise HTTPException(status_code=409, detail="drawing files unavailable")
    opened = rt.store.open(doc_id)
    return {"session_id": doc_id, "image_url": f"/api/image/{doc_id}",
            "fileName": doc["file_name"], "state": doc["state"],
            "final_revision": doc["final_revision"],
            # The journal seq final_revision covers. The browser forgets what
            # it sealed on a reload, and store.seal accepts a final_seq equal
            # to the last sealed one -- so without this, a Finish with no new
            # edits after a reopen would seal a redundant revision.
            "sealed_seq": doc["sealed_seq"],
            "rows": opened["proposal"], **extras,
            "writer": opened["writer"], "events": opened["events"],
            "last_seq": opened["last_seq"]}


@app.post("/api/session/{session_id}/events")
def post_events(session_id: str, req: EventsRequest):
    """Append reviewer operations. Idempotent by seq, so the UI simply resends
    everything the reply says is not yet held contiguously.

    The row is looked up BEFORE anything reaches the journal: a stale tab
    holding a writer for a drawing whose row was removed (the review dir
    deliberately survives a delete, batch design §2 queue controls) must get 404, not a journal
    write for a document the index no longer knows about -- `events_appended`
    is a silent no-op on an unknown id, so without this check the write would
    go through and nothing would ever say so."""
    _doc_dir(session_id)             # rejects a malformed id before any path is built
    rt = runtime()
    rt.repo.get(session_id)          # 404s via UnknownDocument if the row is gone
    contiguous = rt.store.append(session_id, req.writer, req.events)
    if contiguous > 0:
        # Only what the store actually ACKNOWLEDGED moves the drawing --
        # events above a gap are unacked and the next open() trims them, so
        # crediting the request's own max seq would move the state for
        # events that never actually landed.
        rt.repo.events_appended(session_id, contiguous)
        try:
            rt.repo.update_progress(session_id, rt.store.progress(session_id))
        except (ReviewError, OSError, ValueError, KeyError, TypeError) as e:
            # The work list's counts simply stay stale until the next
            # change or the lazy backfill in list_documents() catches up --
            # never worth failing an otherwise-successful append over.
            # TYPE only (CLAUDE.md §1).
            log.warning("progress recompute failed for %s: %s", session_id[:8], type(e).__name__)
    return {"contiguous": contiguous}


@app.post("/api/session/{session_id}/seal")
def seal_session(session_id: str, req: SealRequest):
    """Seal a review revision at the client's final seq. Refuses on a gap: an
    incomplete log must never be graded (design §2).

    The index's state is checked, and the row's existence along with it,
    BEFORE store.seal runs: a sealed revision is not written to disk for a
    drawing the index does not say is ready/in_review/finished (e.g. a stale
    tab mid-retry, before the worker re-creates the review session). This is
    not an absolute "never" -- a delete can still commit between this
    `repo.get` and `store.seal`, since the two live behind separate locks
    (store lock below, repo lock inside `get`/`mark_finished`). In that race
    `mark_finished` then 404s on the now-missing row, and the revision it
    sealed sits as an orphan in the review dir, which a delete deliberately
    keeps anyway (batch design §2 queue controls) -- so nothing is lost, it is just unlisted."""
    _doc_dir(session_id)
    rt = runtime()
    doc = rt.repo.get(session_id)
    if doc["state"] not in ("ready", "in_review", "finished"):
        raise InvalidTransition(
            f"drawing is {doc['state']}, not ready for review")
    out = rt.store.seal(
        session_id, req.writer, req.final_seq,
        [r.model_dump(mode="json") for r in req.rows], req.reviewed_ids)
    # store.seal and mark_finished are not one atomic step -- the store's own
    # lock and the repo's are separate -- so an event that lands in the gap
    # between them is sealed on disk but not yet reflected in the index; the
    # drawing reads `finished` with that edit unsealed until the next new
    # event moves it back to in_review. Acceptable because the UI's Finish is
    # single-flight, so nothing else is writing events at the same moment.
    # And if mark_finished itself fails after store.seal succeeded (e.g. a
    # transient sqlite I/O error) a retried Finish calls store.seal again and
    # writes a SECOND revision -- this recovers rather than no-ops, it is not
    # idempotent.
    rt.repo.mark_finished(session_id, out["revision"], req.final_seq)
    try:
        rt.repo.update_progress(session_id, rt.store.progress(session_id))
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as e:
        # Never fails an otherwise-committed seal; TYPE only (CLAUDE.md §1).
        log.warning("progress recompute failed for %s: %s", session_id[:8], type(e).__name__)
    return out


@app.get("/api/image/{session_id}")
def image(session_id: str):
    png = _doc_dir(session_id) / "page.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(png, media_type="image/png")


_EXPORT_KINDS = {
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "pdf": ("application/pdf", "_ballooned.pdf"),
}

# CR/LF could inject a header line, a quote would close the quoted-string
# early, a path separator has no business inside a filename at all, and any
# OTHER control character (tab, 0x01, DEL, ...) is just as capable of
# confusing a header parser -- `file_name` comes straight from the untrusted
# upload (main.upload stores it raw), so both the ASCII fallback and the
# RFC 5987 form below are built from a sanitised copy, never the original
# string.
_HEADER_UNSAFE = re.compile(r'[\x00-\x1f\x7f"\\/]')


def _content_disposition(filename: str) -> str:
    """`attachment; filename="..."; filename*=UTF-8''...` for a download name
    that may not be ASCII or well-formed at all. The quoted-string fallback is
    for clients that ignore filename*; both forms use the same sanitised name,
    so neither can disagree with the other about what is being sent."""
    safe = _HEADER_UNSAFE.sub("_", filename)
    ascii_name = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe, safe='')}"


# Kept as a module attribute so existing callers (and tests) that reach it as
# `main._render_export` keep working: it is the SAME function object, not a
# wrapper, so a monkeypatch of app.batch.export_render.write_workbook (the
# function's own module) is what now affects it -- a monkeypatch of
# app.main.write_workbook no longer exists to find.
_render_export = render_export


@app.get("/api/documents/{doc_id}/export/{kind}")
def export_document(doc_id: str, kind: str, rev: Optional[int] = None):
    """Render and cache an export of a SEALED revision -- never from rows a
    browser sends. `_doc_dir` only COMPUTES this path, never creates it; the
    row itself must exist (checked below) before any directory under docs/ is
    actually created, so an id nobody owns never gets even an empty
    exports/ folder."""
    work = _doc_dir(doc_id)
    if kind not in _EXPORT_KINDS:
        raise HTTPException(status_code=404, detail="unknown export kind")
    rt = runtime()
    doc = rt.repo.get(doc_id)
    if rev is None:
        rev = doc["final_revision"]
    if rev is None:
        raise HTTPException(status_code=409, detail="finish the review before exporting")
    sealed = rt.store.revision(doc_id, rev)
    try:
        path = _render_export(doc_id, work, rev, kind, sealed)
    except InputUnavailable as e:
        # Handled, not a 500: the traceback would log the full path, and with
        # it the whole document id. TYPE only (CLAUDE.md §1).
        log.warning("export %s r%s.%s: input unavailable: %s",
                    doc_id[:8], rev, kind, type(e.__cause__ or e).__name__)
        raise HTTPException(status_code=409, detail="drawing files unavailable")
    except Exception as e:
        # A fault in the EXPORTER, not a missing input -- a fixed sentence,
        # never the exception's own text, which could carry client data.
        log.warning("export %s r%s.%s: render failed: %s",
                    doc_id[:8], rev, kind, type(e).__name__)
        raise HTTPException(status_code=500, detail="export failed")
    rt.repo.mark_exported(doc_id, rev)
    media, suffix = _EXPORT_KINDS[kind]
    disposition = _content_disposition(Path(doc["file_name"]).stem + suffix)
    return FileResponse(path, media_type=media,
                        headers={"Content-Disposition": disposition})


@app.post("/api/exports")
def create_export(req: ExportRequest):
    """Mass export (batch design §4): its own thread, started and returned
    immediately -- the browser polls GET /api/exports/{id} for progress."""
    return runtime().exports.start(req.document_ids)


@app.get("/api/exports/{export_id}")
def get_export_status(export_id: str):
    return runtime().exports.status(export_id)


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: str):
    rt = runtime()
    job = rt.exports.status(export_id)
    if job["state"] == "running":
        raise HTTPException(status_code=409, detail="export still building")
    if job["state"] == "failed":
        raise HTTPException(status_code=500, detail="export failed")
    path = rt.data.export_zip_path(export_id)
    if not path.is_file():
        # Deleted by the 24h cleanup (Task 7): rebuildable from the same
        # selection, so this is not a data loss, but the client must ask
        # again rather than get a 200 for a file that no longer exists.
        raise HTTPException(status_code=410, detail="export expired — request it again")
    return FileResponse(path, media_type="application/zip",
                        headers={"Content-Disposition": _content_disposition("sindri-export.zip")})


@app.post("/api/read_region")
def read_region(req: ReadRegionRequest):
    work = _doc_dir(req.session_id)
    png = work / "page.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="unknown session")
    if len(req.box) != 4:
        raise HTTPException(status_code=400, detail="box must be [x0,y0,x1,y1]")
    image = Image.open(png).convert("RGB")
    w, h = image.size
    x0, y0, x1, y1 = req.box
    box = (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise HTTPException(status_code=400, detail="degenerate box")
    crop = image.crop(box)
    try:
        ocr = _BACKEND.read_region(crop)
        text, conf = ocr.text, ocr.confidence
    except Exception:
        text, conf = "", 0.0
    c = parse_value(text)
    c.id = uuid.uuid4().hex
    c.source = "manual"
    c.target_region = box
    c.confidence = conf
    c.needs_review, c.review_reasons = review_flags(c, rotation_ambiguous=False)
    # extract() applies the ACTIVE flag rules after review_flags for every
    # auto-detected row (_apply_active_policy); a manual read through this
    # endpoint skipped that step, so the same text read manually vs
    # auto-ballooned could disagree on whether it needs review.
    extra = pr.apply_flag_rules(c, pr.ACTIVE_FLAG_RULES)
    if extra:
        c.needs_review = True
        c.review_reasons = [*c.review_reasons, *extra]
    place_balloons([c])
    return c.model_dump()


class NoCacheStaticFiles(StaticFiles):
    """Serve the SPA assets with `Cache-Control: no-cache` so browsers always
    revalidate (cheap 304 via ETag when unchanged). This guarantees that after
    a container rebuild the frontend matches the backend — a stale cached bundle
    paired with the new API contract is what produced the `payload.rows.map`
    crash on the extract flow."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# static UI mounted last so /api/* takes precedence
app.mount("/", NoCacheStaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
