import re
import tempfile
import uuid
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.models import Characteristic
from app.pipeline.extract import extract
from app.pipeline.ocr import get_backend, backend_status
from app.excel import write_workbook
from app.pipeline.parser import parse_value
from app.pipeline.place import place_balloons
from app.pipeline.ballooned_pdf import render_ballooned_pdf
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


class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]


class ReadRegionRequest(BaseModel):
    session_id: str
    box: List[float]        # [x0, y0, x1, y1] image-space pixels


@app.get("/api/health")
def health():
    status = backend_status()
    status["ocr_backend_active"] = type(_BACKEND).__name__
    return status


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    work = _SESSIONS / session_id
    work.mkdir(parents=True, exist_ok=True)
    pdf_path = work / "input.pdf"
    pdf_path.write_bytes(await file.read())
    try:
        rows = extract(pdf_path, work_dir=work, dpi=300, backend=_BACKEND)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="could not read the PDF")
    return JSONResponse({
        "session_id": session_id,
        "image_url": f"/api/image/{session_id}",
        "rows": [r.model_dump() for r in rows],
    })


@app.get("/api/image/{session_id}")
def image(session_id: str):
    png = _session_dir(session_id) / "page.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(png, media_type="image/png")


@app.post("/api/export")
def export(req: ExportRequest):
    work = _session_dir(req.session_id)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "inspection.xlsx"
    write_workbook(req.rows, out)
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
    place_balloons([c])
    return c.model_dump()


@app.post("/api/export/pdf")
def export_pdf(req: ExportRequest):
    work = _session_dir(req.session_id)
    src = work / "input.pdf"
    if not src.is_file():
        raise HTTPException(status_code=404, detail="unknown session")
    out = work / "ballooned.pdf"
    render_ballooned_pdf(src, req.rows, dpi=300, out_path=out)
    return FileResponse(out, media_type="application/pdf", filename="ballooned.pdf")


# static UI mounted last so /api/* takes precedence
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
