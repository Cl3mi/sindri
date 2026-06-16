from dataclasses import dataclass
import re
import fitz

_INT_RE = re.compile(r"^\d{1,3}$")


@dataclass
class Anchor:
    number: int
    x: float        # image-space centre x
    y: float        # image-space centre y
    bbox: tuple     # image-space (x0,y0,x1,y1)


def extract_anchors(pdf_path, scale: float, page_index: int = 0):
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    anchors = []
    seen = set()
    for w in page.get_text("words"):
        x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
        token = word.strip()
        if not _INT_RE.match(token):
            continue
        n = int(token)
        if not (1 <= n <= 199):       # balloon range guard
            continue
        if n in seen:
            continue
        seen.add(n)
        anchors.append(Anchor(
            number=n,
            x=(x0 + x1) / 2 * scale,
            y=(y0 + y1) / 2 * scale,
            bbox=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
        ))
    doc.close()
    return anchors
