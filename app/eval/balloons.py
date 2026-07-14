"""Recover the client's balloons — (number, center) — from a ballooned PDF.

Vector-first strategy (open question §3 of the handoff): client balloons are
expected to be vector circles (bezier 'c' items in get_drawings()) with a
digit-only text span centered inside. `probe_pdf` reports what a file actually
contains so day-one inspection (Task 13) can confirm or refute this per corpus;
raster-stamped balloons show up as n_circles==0 / has_images==True and would
need a raster detector (separate task, only if the probe demands it).

All coordinates are PDF points (native PyMuPDF space).
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List

import fitz

# Balloon radius window in points. Our own balloons are 9pt (ballooned_pdf.py);
# client balloons should be the same order of magnitude. Tune from probe output.
MIN_R_PT = 4.0
MAX_R_PT = 24.0


@dataclass(frozen=True)
class Balloon:
    number: int
    center_pt: tuple      # (x, y) in PDF points
    radius_pt: float


def _circle_rects(page) -> List[fitz.Rect]:
    """Rects of drawings that look like balloon circles: curve-based, roughly
    square, diameter within the balloon window."""
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if not any(item[0] == "c" for item in d["items"]):
            continue                     # rectangles/lines have no curves
        if abs(r.width - r.height) > max(r.width, r.height) * 0.25:
            continue                     # not circle-ish
        if not (MIN_R_PT * 2 <= r.width <= MAX_R_PT * 2):
            continue
        out.append(r)
    return out


def recover_balloons(pdf_path, page_index: int = 0) -> List[Balloon]:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        circles = _circle_rects(page)
        words = page.get_text("words")   # (x0, y0, x1, y1, text, ...)
        balloons = []
        for r in circles:
            inside = [w for w in words
                      if w[4].strip().isdigit()
                      and r.contains(fitz.Point((w[0] + w[2]) / 2,
                                                (w[1] + w[3]) / 2))]
            if not inside:
                continue
            # multi-word numbers (rare glyph splits): join left-to-right
            inside.sort(key=lambda w: w[0])
            number = int("".join(w[4].strip() for w in inside))
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            balloons.append(Balloon(number=number, center_pt=(cx, cy),
                                    radius_pt=r.width / 2))
        # dedupe identical (number, ~center) from doubled vector strokes
        seen, unique = set(), []
        for b in balloons:
            key = (b.number, round(b.center_pt[0]), round(b.center_pt[1]))
            if key not in seen:
                seen.add(key)
                unique.append(b)
        return unique
    finally:
        doc.close()


def probe_pdf(pdf_path, page_index: int = 0) -> dict:
    """Day-one encoding inspection for one client PDF. Cheap, no model."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        circles = _circle_rects(page)
        balloons = recover_balloons(pdf_path, page_index)
        numbers = sorted(b.number for b in balloons)
        dupes = sorted({n for n in numbers if numbers.count(n) > 1})
        num_set = set(numbers)
        # cap: one garbage number (misread digit) must not build a huge list
        gap_ceiling = min(max(numbers), 5000) if numbers else 0
        return {
            "pdf": str(Path(pdf_path).name),
            "n_drawings": len(page.get_drawings()),
            "n_circles": len(circles),
            "n_words": len(page.get_text("words")),
            "has_images": len(page.get_images()) > 0,
            "n_balloons": len(balloons),
            "numbers": numbers,
            "duplicate_numbers": dupes,
            "gaps": [n for n in range(1, gap_ceiling + 1) if n not in num_set],
        }
    finally:
        doc.close()
