import errno
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote
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


# One lock per (doc_id, rev, kind): two requests racing the FIRST export of a
# revision must not share a fixed temp name -- the loser's rename would raise
# FileNotFoundError on what was otherwise a perfectly good export, or (worse)
# a half-written file could be renamed into place and served. The dict itself
# is touched from more than one thread, hence its own small guard.
_EXPORT_LOCKS: dict = {}
_EXPORT_LOCKS_GUARD = threading.Lock()


def _export_lock(key) -> threading.Lock:
    with _EXPORT_LOCKS_GUARD:
        return _EXPORT_LOCKS.setdefault(key, threading.Lock())


class _InputUnavailable(Exception):
    """One of the export's INPUTS -- the sealed rows, result.json, or
    input.pdf -- is missing or unreadable. Raised instead of letting the
    underlying OSError/ValueError/KeyError/RuntimeError escape, so
    export_document can tell this apart from a fault in the EXPORTER itself
    (app.excel / app.pipeline.ballooned_pdf): that is a bug and must 500, not
    be reported as "the files are gone"."""


def _render_export(doc_id: str, work: Path, rev: int, kind: str, sealed: dict) -> Path:
    """Render revision `rev` once and cache it, so a repeated or mass export
    of the same revision is the same bytes (batch design §4). The rows come
    from the SEALED revision, never from a browser.

    `mark_exported` (in the caller) runs on every call, cache hit or not: a
    re-download still IS an export, and an explicit older `?rev=` moving
    `exported_revision` backward is correct -- that field means "last
    exported", not "highest ever sealed". The cached rN.* is never
    invalidated if the exporter's own code changes later; delete
    docs/<id>/exports/ by hand to force a rebuild."""
    out = work / "exports" / f"r{rev}.{kind}"
    if out.is_file():
        return out
    with _export_lock((doc_id, rev, kind)):
        if out.is_file():      # a concurrent first render finished while we waited
            return out
        if not work.is_dir():
            # The row can outlive its files (a delete race, and routinely
            # after a future retention purge). Without this check
            # `mkdir(exist_ok=True)` below would find its own parent gone
            # and raise FileNotFoundError -- an input fault, not a bug -- and
            # recreating docs/<id>/ just to hold an empty exports/ directory
            # would be exactly the orphan the row-must-exist-first rule
            # elsewhere in this module exists to avoid.
            raise _InputUnavailable("document directory is gone")
        out.parent.mkdir(exist_ok=True)
        try:
            rows = _exportable([Characteristic.model_validate(r) for r in sealed["rows"]])
            notes = marks = title_block = None
            src = None
            if kind == "xlsx":
                result = json.loads((work / "result.json").read_text(encoding="utf-8"))
                notes = NoteBlock.model_validate(result["notes"]) if result["notes"] else None
                marks = MarkBlock.model_validate(result["marks"]) if result["marks"] else None
                title_block = [TitleField.model_validate(t) for t in result["title_block"]]
            else:
                src = work / "input.pdf"
                if not src.is_file():
                    raise _InputUnavailable("input.pdf is missing")
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise _InputUnavailable from e

        # A unique name per attempt -- the single fixed temp name this used
        # to use let a second concurrent render clobber or lose the first's
        # file. Removed in `finally` whether or not the rename below ran (it
        # is already gone once `replace` succeeds; unlink then no-ops).
        fd, tmp_name = tempfile.mkstemp(
            dir=out.parent, prefix=f"r{rev}.", suffix=f".tmp.{kind}")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            if kind == "xlsx":
                write_workbook(rows, tmp, notes=notes, marks=marks, title_block=title_block)
            else:
                try:
                    render_ballooned_pdf(src, rows, dpi=300, out_path=tmp)
                except (fitz.FileDataError, fitz.FileNotFoundError) as e:
                    # PyMuPDF's own exceptions for a missing/corrupt PDF are
                    # RuntimeError subclasses, not OSError -- without this
                    # they would escape as an unhandled 500 with a traceback
                    # naming the full path.
                    raise _InputUnavailable from e
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        return out


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
    except _InputUnavailable as e:
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
