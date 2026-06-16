import re
from typing import List
from PIL import Image
from app.models import Characteristic
from app.pipeline.ocr.base import OCRBackend

_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\b\s*(.*)$")


def split_note_rows(raw: str) -> List[Characteristic]:
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        m = _ROW_RE.match(line)
        if not m:
            continue
        rows.append(Characteristic(
            pos=int(m.group(1)),
            char_type="Note",
            nominal=m.group(2).strip(),
            raw_text=line,
            confidence=0.5,
        ))
    return rows


def extract_notes(image: Image.Image, region, backend: OCRBackend) -> List[Characteristic]:
    """region = (x0,y0,x1,y1) image-space box of the notes table."""
    crop = image.crop(region)
    result = backend.read_region(crop)
    rows = split_note_rows(result.text)
    for r in rows:
        r.confidence = min(r.confidence, result.confidence or 0.5)
    return rows
