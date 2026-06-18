"""Draw numbered balloons + leader lines onto a copy of the source PDF.

Coordinates on Characteristic rows are image-space pixels at render dpi; convert
back to PDF points by dividing by scale = dpi/72. The source PDF is never
mutated — a new file is written to out_path.
"""
from pathlib import Path
import fitz

_BLUE = (0.0, 0.3, 0.8)
_RADIUS = 9.0      # balloon radius in PDF points


def render_ballooned_pdf(src_pdf, rows, dpi: int = 300, out_path=None, page_index: int = 0):
    out_path = Path(out_path)
    scale = dpi / 72.0
    doc = fitz.open(src_pdf)
    page = doc[page_index]
    rect = page.rect

    def to_pt(x, y):
        px = min(max(x / scale, rect.x0), rect.x1)
        py = min(max(y / scale, rect.y0), rect.y1)
        return fitz.Point(px, py)

    for c in rows:
        if not c.balloon_xy or not c.target_region:
            continue
        bx, by = c.balloon_xy
        tx = (c.target_region[0] + c.target_region[2]) / 2.0
        ty = (c.target_region[1] + c.target_region[3]) / 2.0
        b_pt, t_pt = to_pt(bx, by), to_pt(tx, ty)

        page.draw_line(b_pt, t_pt, color=_BLUE, width=1.0)
        page.draw_circle(b_pt, _RADIUS, color=_BLUE, width=1.5)
        page.insert_text(fitz.Point(b_pt.x - 5, b_pt.y + 4), str(c.pos),
                         fontsize=10, color=_BLUE)

    doc.save(out_path)
    doc.close()
    return out_path
