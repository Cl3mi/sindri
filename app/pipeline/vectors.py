import fitz


def extract_segments(pdf_path, scale: float, page_index: int = 0):
    """Return straight line segments as (x0,y0,x1,y1) in image space."""
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    segments = []
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] == "l":           # ("l", p1, p2)
                p1, p2 = item[1], item[2]
                segments.append((p1.x * scale, p1.y * scale,
                                 p2.x * scale, p2.y * scale))
            elif item[0] == "re":        # rectangle -> 4 edges
                r = item[1]
                pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for i in range(4):
                    a, b = pts[i], pts[(i + 1) % 4]
                    segments.append((a[0]*scale, a[1]*scale, b[0]*scale, b[1]*scale))
    doc.close()
    return segments
