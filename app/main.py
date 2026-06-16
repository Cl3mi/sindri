import tempfile
import uuid
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.models import Characteristic
from app.pipeline.extract import extract
from app.excel import write_workbook

app = FastAPI(title="Sindri")

_SESSIONS = Path(tempfile.gettempdir()) / "sindri_sessions"
_SESSIONS.mkdir(exist_ok=True)


class ExportRequest(BaseModel):
    session_id: str
    rows: List[Characteristic]


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    work = _SESSIONS / session_id
    work.mkdir(parents=True, exist_ok=True)
    pdf_path = work / "input.pdf"
    pdf_path.write_bytes(await file.read())
    rows = extract(pdf_path, work_dir=work, dpi=300)
    return JSONResponse({
        "session_id": session_id,
        "image_url": f"/api/image/{session_id}",
        "rows": [r.model_dump() for r in rows],
    })


@app.get("/api/image/{session_id}")
def image(session_id: str):
    png = _SESSIONS / session_id / "page.png"
    return FileResponse(png, media_type="image/png")


@app.post("/api/export")
def export(req: ExportRequest):
    work = _SESSIONS / req.session_id
    work.mkdir(parents=True, exist_ok=True)
    out = work / "inspection.xlsx"
    write_workbook(req.rows, out)
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="inspection.xlsx",
    )


# static UI mounted last so /api/* takes precedence
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
