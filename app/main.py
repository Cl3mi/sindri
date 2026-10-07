import errno
import hashlib
import json
import logging
import os
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope
from starlette.responses import Response
from pydantic import BaseModel
from app.models import Characteristic, NoteBlock, MarkBlock, TitleField
from app.pipeline.extract import extract
from app.pipeline.ocr import get_backend, backend_status
from app.excel import write_workbook
from app.pipeline.parser import parse_value
from app.pipeline import policy_rules as pr
from app.pipeline.review import review_flags
from app.pipeline.place import place_balloons
from app.pipeline.ballooned_pdf import render_ballooned_pdf
from app.review.header import build_header
from app.review.store import ReviewError
from app.batch.repo import BatchError, InvalidTransition
from app.batch.runtime import Runtime
from PIL import Image

log = logging.getLogger(__name__)

# Load the OCR backend ONCE at startup (the VLM model is multi-GB — never
# reload it per request) and reuse it for every upload.
_BACKEND = get_backend()


def _review_consent() -> bool:
    return os.environ.get("SINDRI_REVIEW_CONSENT", "").lower() in ("1", "true", "yes")


def _extract(pdf_path, work_dir, progress):
    # Late-bound on purpose: tests monkeypatch `extract` and `_BACKEND` on
    # this module, and the worker must see those patches.
    return extract(pdf_path, work_dir=work_dir, dpi=300, backend=_BACKEND,
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
    # sentences, never client text.
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def _doc_dir(doc_id: str) -> Path:
    try:
        return runtime().data.doc_dir(doc_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="unknown document")


def _doc_view(doc_id: str) -> dict:
    rt = runtime()
    doc = rt.repo.get(doc_id)
    return {"id": doc["id"], "file_name": doc["file_name"], "pages": doc["pages"],
            "state": doc["state"], "error_type": doc["error_type"],
            "final_revision": doc["final_revision"],
            "exported_revision": doc["exported_revision"],
            "queue_position": rt.repo.queue_position(doc_id),
            "progress": rt.worker.progress_of(doc_id)}


class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]
    notes: Optional[NoteBlock] = None
    marks: Optional[MarkBlock] = None
    title_block: List[TitleField] = []


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


@app.get("/api/health")
def health():
    status = backend_status()
    status["ocr_backend_active"] = type(_BACKEND).__name__
    status["worker_running"] = runtime().worker.running
    return status


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    """Store a PDF as a staged drawing -- a batch of one until B2 brings real
    batches. Extraction is queued separately and runs in the background
    worker, so no browser has to stay open (batch design §1).

    A sync handler on purpose: file, PyMuPDF and SQLite work all block, and
    FastAPI runs a plain `def` in its threadpool instead of on the event loop."""
    rt = runtime()
    doc_id = uuid.uuid4().hex
    work = rt.data.doc_dir(doc_id)
    pdf_path = work / "input.pdf"
    digest = hashlib.sha256()
    try:
        work.mkdir(parents=True)
        with open(pdf_path, "wb") as out:
            while chunk := file.file.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
    except OSError as e:
        # The server could not store it -- not the user's file at fault. A
        # fixed sentence: an OSError's own text names the path. TYPE only in
        # the log, like everywhere else (CLAUDE.md §1).
        shutil.rmtree(work, ignore_errors=True)
        log.warning("upload %s not stored: %s", doc_id[:8], type(e).__name__)
        if e.errno == errno.ENOSPC:
            raise HTTPException(status_code=507, detail="not enough disk space to store the drawing")
        raise HTTPException(status_code=500, detail="could not store the drawing")
    try:
        with fitz.open(pdf_path) as doc:
            pages = doc.page_count
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=400, detail="could not read the PDF")
    name = file.filename or "drawing.pdf"
    try:
        rt.repo.create_document(doc_id, name, pages, digest.hexdigest())
    except BaseException:
        # No row, so nothing would ever list or remove these files.
        shutil.rmtree(work, ignore_errors=True)
        raise
    return {"session_id": doc_id, "fileName": name, "pages": pages}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    _doc_dir(doc_id)
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/enqueue")
def enqueue_document(doc_id: str):
    _doc_dir(doc_id)
    rt = runtime()
    rt.repo.enqueue(doc_id)
    rt.worker.wake()
    return _doc_view(doc_id)


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
            "rows": opened["proposal"], **extras,
            "writer": opened["writer"], "events": opened["events"],
            "last_seq": opened["last_seq"]}


@app.post("/api/session/{session_id}/events")
def post_events(session_id: str, req: EventsRequest):
    """Append reviewer operations. Idempotent by seq, so the UI simply resends
    everything the reply says is not yet held contiguously.

    The row is looked up BEFORE anything reaches the journal: a stale tab
    holding a writer for a drawing whose row was removed (the review dir
    deliberately survives a delete, CLAUDE.md §1) must get 404, not a journal
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
    keeps anyway (CLAUDE.md §1) -- so nothing is lost, it is just unlisted."""
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
    return out


@app.get("/api/image/{session_id}")
def image(session_id: str):
    png = _doc_dir(session_id) / "page.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(png, media_type="image/png")


def _exportable(rows):
    """Confirmed rows only. The UI already omits unconfirmed suggestions; this
    is the server-side guarantee, because an unconfirmed value in the client's
    Excel or ballooned PDF is exactly the failure the drops exist to prevent
    (docs/plans/2026-10-07-suggestion-tray-design.md)."""
    return [r for r in rows if not r.suggested]


@app.post("/api/export")
def export(req: ExportRequest):
    work = _doc_dir(req.session_id)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "inspection.xlsx"
    write_workbook(_exportable(req.rows), out, notes=req.notes, marks=req.marks,
                   title_block=req.title_block)
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="inspection.xlsx",
    )


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


@app.post("/api/export/pdf")
def export_pdf(req: ExportRequest):
    work = _doc_dir(req.session_id)
    src = work / "input.pdf"
    if not src.is_file():
        raise HTTPException(status_code=404, detail="unknown session")
    out = work / "ballooned.pdf"
    render_ballooned_pdf(src, _exportable(req.rows), dpi=300, out_path=out)
    return FileResponse(out, media_type="application/pdf", filename="ballooned.pdf")


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
