import asyncio
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import List, Optional
import fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
from app.review.store import ReviewStore
from PIL import Image

app = FastAPI(title="Sindri")

_SESSIONS = Path(tempfile.gettempdir()) / "sindri_sessions"
_SESSIONS.mkdir(exist_ok=True)

# session ids are uuid4().hex — reject anything else so it can't be used to
# escape the sessions directory when building file paths.
_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")


def _session_dir(session_id: str) -> Path:
    if not _SESSION_RE.match(session_id):
        raise HTTPException(status_code=404, detail="unknown session")
    return _SESSIONS / session_id

# Load the OCR backend ONCE at startup (the VLM model is multi-GB — never
# reload it per request) and reuse it for every upload.
_BACKEND = get_backend()


def _review_store_from_env():
    """The durable review store, or None when SINDRI_REVIEW_DIR is unset or
    unwritable. None is not an error: the app still reviews and exports, it
    just records nothing -- and a session recorded nowhere is never graded,
    rather than graded wrong (design §6)."""
    root = os.environ.get("SINDRI_REVIEW_DIR")
    if not root:
        return None
    try:
        return ReviewStore(Path(root))
    except OSError:
        return None


_REVIEW_STORE = _review_store_from_env()


def _review_consent() -> bool:
    return os.environ.get("SINDRI_REVIEW_CONSENT", "").lower() in ("1", "true", "yes")


class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]
    notes: Optional[NoteBlock] = None
    marks: Optional[MarkBlock] = None
    title_block: List[TitleField] = []


class ReadRegionRequest(BaseModel):
    session_id: str
    box: List[float]        # [x0, y0, x1, y1] image-space pixels


@app.get("/api/health")
def health():
    status = backend_status()
    status["ocr_backend_active"] = type(_BACKEND).__name__
    status["review_logging"] = _REVIEW_STORE is not None
    return status


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Save the PDF to a fresh session and return its metadata. Extraction is
    a separate, explicitly-started step (`POST /api/extract/{session_id}`)."""
    session_id = uuid.uuid4().hex
    work = _SESSIONS / session_id
    work.mkdir(parents=True, exist_ok=True)
    pdf_path = work / "input.pdf"
    pdf_path.write_bytes(await file.read())
    try:
        with fitz.open(pdf_path) as doc:
            pages = doc.page_count
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=400, detail="could not read the PDF")
    return {"session_id": session_id, "fileName": file.filename, "pages": pages}


class _Cancelled(Exception):
    """Raised inside the extraction worker when the client disconnects, to
    unwind the pipeline cooperatively at the next progress emit."""


@app.post("/api/extract/{session_id}")
async def extract_endpoint(session_id: str, request: Request):
    """Run extraction for an already-uploaded session and stream pipeline
    progress as Server-Sent Events. The final result is a terminal `result`
    (or `error`) event."""
    work = _session_dir(session_id)
    pdf_path = work / "input.pdf"
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="unknown session")

    events: "queue.Queue" = queue.Queue()
    _DONE = object()
    cancel = threading.Event()

    def worker():
        def progress(step, detail="", current=None, total=None):
            if cancel.is_set():
                raise _Cancelled()
            events.put(("progress", {
                "step": step, "detail": detail,
                "current": current, "total": total,
            }))
        try:
            result = extract(pdf_path, work_dir=work, dpi=300,
                             backend=_BACKEND, progress=progress)
            # Dumped once in json mode so the proposal written to the review
            # store is byte-identical to the rows the UI receives below.
            rows = [r.model_dump(mode="json") for r in
                    [*result.characteristics, *result.suggestions]]
            writer = None
            if _REVIEW_STORE is not None:
                try:
                    writer = _REVIEW_STORE.create(
                        session_id, build_header(pdf_path, _review_consent()),
                        {"rows": rows})
                except Exception as e:
                    # Logging is best-effort by design (§6): a session
                    # recorded nowhere is never graded, but an extraction
                    # lost to a capture fault costs the reviewer the whole
                    # drawing, which is strictly worse -- so this catches
                    # everything, not just ReviewError/OSError (build_header
                    # itself can raise, e.g. a PyMuPDF RuntimeError, and that
                    # must not turn a finished extraction into an SSE error).
                    # Exception TYPE only, never str(e): a PyMuPDF or journal
                    # message can carry client text (CLAUDE.md §1).
                    logging.getLogger(__name__).warning(
                        "review capture skipped for session %s: %s",
                        session_id[:8], type(e).__name__)
                    writer = None
            events.put(("result", {
                "session_id": session_id,
                "image_url": f"/api/image/{session_id}",
                # Suggestions ride in the same list, flagged, so the reviewer
                # edits them with the existing table; the client keeps them out
                # of exports and so does _exportable below.
                "rows": rows,
                "notes": result.notes.model_dump() if result.notes is not None else None,
                "marks": result.marks.model_dump() if result.marks is not None else None,
                "title_block": [t.model_dump() for t in result.title_block],
                "writer": writer,
                "review_logging": writer is not None,
            }))
        except _Cancelled:
            # client went away — drop the session and stop quietly
            shutil.rmtree(work, ignore_errors=True)
        except RuntimeError as e:
            events.put(("error", {"detail": str(e)}))
        except Exception:
            events.put(("error", {"detail": "could not read the PDF"}))
        finally:
            events.put((_DONE, None))

    async def stream():
        threading.Thread(target=worker, daemon=True).start()
        loop = asyncio.get_event_loop()
        while True:
            try:
                kind, payload = await loop.run_in_executor(
                    None, lambda: events.get(timeout=0.25))
            except queue.Empty:
                if await request.is_disconnected():
                    cancel.set()
                    break
                continue
            if kind is _DONE:
                break
            yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.delete("/api/session/{session_id}")
def delete_session(session_id: str):
    """Discard an uploaded session (cancelled confirm, or stopped extraction).
    Idempotent: a missing directory is not an error; a malformed id is 404
    via `_session_dir`."""
    work = _session_dir(session_id)
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": True}


@app.get("/api/image/{session_id}")
def image(session_id: str):
    png = _session_dir(session_id) / "page.png"
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
    work = _session_dir(req.session_id)
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
    work = _session_dir(req.session_id)
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
    work = _session_dir(req.session_id)
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
