"""Draw numbered balloons + leader lines onto a copy of the source PDF.

Coordinates on Characteristic rows are image-space pixels at render dpi; convert
back to PDF points by dividing by scale = dpi/72. The source PDF is never
mutated — a new file is written to out_path.
"""
from pathlib import Path
import fitz

_BLUE = (0.0, 0.3, 0.8)
_RADIUS = 9.0      # balloon radius in PDF points


_MARGIN = 2.5      # clear space between a leader line and the callout it points at (pt)


def _leader_segment(b, box, radius, margin):
    """Leader from the balloon's rim to just outside the callout box.

    `b` is the balloon centre, `box` the callout (x0, y0, x1, y1), all in PDF
    points. The line starts on the circle's edge (so it never crosses the number
    inside) and stops `margin` short of the box edge (so it never touches the
    callout's glyphs). Returns None when the balloon is too close to the box
    for a visible line."""
    x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    dx, dy = cx - b.x, cy - b.y
    dist = (dx * dx + dy * dy) ** 0.5
    if dist == 0:
        return None
    # Slab clip of the ray b -> centre against the inflated box: t at which the
    # ray first enters it (b is outside, so the entry parameter is the larger of
    # the per-axis entry times).
    t_enter = 0.0
    for o, d, lo, hi in ((b.x, dx, x0, x1), (b.y, dy, y0, y1)):
        if d == 0:
            if not lo <= o <= hi:
                return None
            continue
        ta, tb = (lo - o) / d, (hi - o) / d
        t_enter = max(t_enter, min(ta, tb))
    if t_enter >= 1.0:
        return None
    start = radius / dist                      # rim of the balloon, as a ray fraction
    if t_enter <= start:
        return None                            # circle already touches the margin
    return (fitz.Point(b.x + dx * start, b.y + dy * start),
            fitz.Point(b.x + dx * t_enter, b.y + dy * t_enter))


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
        b_pt = to_pt(bx, by)
        box_pt = [v / scale for v in c.target_region]
        seg = _leader_segment(b_pt, box_pt, _RADIUS, _MARGIN)
        if seg:
            page.draw_line(seg[0], seg[1], color=_BLUE, width=1.0)
        page.draw_circle(b_pt, _RADIUS, color=_BLUE, width=1.5)
        page.insert_text(fitz.Point(b_pt.x - 5, b_pt.y + 4), str(c.pos),
                         fontsize=10, color=_BLUE)

    doc.save(out_path)
    doc.close()
    return out_path
