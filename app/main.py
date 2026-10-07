import hashlib
import json
import os
import shutil
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


def runtime() -> Runtime:
    """Built on first use from SINDRI_DATA_DIR / SINDRI_REVIEW_DIR. Lazy, so
    importing this module (every API test does) never touches a real data
    dir; tests/conftest.py points both at a per-test tmp dir."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime.from_env(_extract, _header)
    return _RUNTIME


@asynccontextmanager
async def lifespan(app):
    global _RUNTIME
    rt = runtime()   # a bad data/review dir fails the boot, not the first drawing
    rt.start()
    try:
        yield
    finally:
        rt.close()
        _RUNTIME = None


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
async def upload(file: UploadFile = File(...)):
    """Store a PDF as a staged drawing -- a batch of one until B2 brings real
    batches. Extraction is queued separately and runs in the background
    worker, so no browser has to stay open (batch design §1)."""
    rt = runtime()
    doc_id = uuid.uuid4().hex
    work = rt.data.doc_dir(doc_id)
    work.mkdir(parents=True)
    pdf_path = work / "input.pdf"
    digest = hashlib.sha256()
    try:
        with open(pdf_path, "wb") as out:
            while chunk := await file.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
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
    _doc_dir(doc_id)
    rt = runtime()
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
    result = json.loads((work / "result.json").read_text(encoding="utf-8"))
    opened = rt.store.open(doc_id)
    return {"session_id": doc_id, "image_url": f"/api/image/{doc_id}",
            "fileName": doc["file_name"], "state": doc["state"],
            "final_revision": doc["final_revision"],
            "rows": opened["proposal"], "notes": result["notes"],
            "marks": result["marks"], "title_block": result["title_block"],
            "writer": opened["writer"], "events": opened["events"],
            "last_seq": opened["last_seq"]}


@app.post("/api/session/{session_id}/events")
def post_events(session_id: str, req: EventsRequest):
    """Append reviewer operations. Idempotent by seq, so the UI simply resends
    everything the reply says is not yet held contiguously."""
    _doc_dir(session_id)             # rejects a malformed id before any path is built
    return {"contiguous": runtime().store.append(session_id, req.writer, req.events)}


@app.post("/api/session/{session_id}/seal")
def seal_session(session_id: str, req: SealRequest):
    """Seal a review revision at the client's final seq. Refuses on a gap: an
    incomplete log must never be graded (design §2)."""
    _doc_dir(session_id)
    return runtime().store.seal(
        session_id, req.writer, req.final_seq,
        [r.model_dump(mode="json") for r in req.rows], req.reviewed_ids)


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
