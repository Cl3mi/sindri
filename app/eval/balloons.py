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


def _shape_rects(page, curves_only: bool = False) -> List[fitz.Rect]:
    """Rects of drawings that could be a balloon outline.

    The outline is NOT necessarily a circle: this client's stamping tool draws
    a diamond from four straight lines (confirmed against the real corpus,
    2026-08-17). Accept any small, roughly square closed shape — bezier-based
    (circle) or 3-8 straight segments (diamond/triangle/square). Requiring
    curves rejected every balloon in the delivery.

    `curves_only` keeps the old circle-only count for diagnostics.
    """
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if abs(r.width - r.height) > max(r.width, r.height) * 0.35:
            continue                     # not symmetric enough for a balloon
        if not (MIN_R_PT * 2 <= r.width <= MAX_R_PT * 2):
            continue
        items = d["items"]
        has_curve = any(item[0] == "c" for item in items)
        if curves_only:
            if has_curve:
                out.append(r)
            continue
        n_lines = sum(1 for item in items if item[0] == "l")
        # 'qu' is how PyMuPDF reports a closed 4-point polyline — i.e. exactly
        # the diamond this client stamps; 're' is a square balloon.
        has_quad = any(item[0] in ("qu", "re") for item in items)
        if has_curve or has_quad or 3 <= n_lines <= 8:
            out.append(r)
    return out


def _circle_rects(page) -> List[fitz.Rect]:
    """Circle-only shapes, kept so `probe` can report both counts."""
    return _shape_rects(page, curves_only=True)


# Plausible balloon numbers. Excludes 0 and anything year- or part-number
# sized, which is what stray digits on a drawing usually are.
MIN_BALLOON_NO = 1
MAX_BALLOON_NO = 999
_MIN_SHAPE_HITS = 1       # fall back only when shape matching finds NOTHING:
                          # one real outline means outlines are readable here


def _balloons_from_words(page) -> List[Balloon]:
    """Every plausible digit word IS a balloon.

    Correct for this corpus because the stamped drawings are flattened prints:
    the CAD geometry became outlines, so the only text left on the page is what
    the stamping tool added. Validated against the sheets — the recovered
    numbers should match the Excel's Pos column (see ingest's join_rate)."""
    out = []
    for w in page.get_text("words"):
        text = w[4].strip()
        if not text.isdigit():
            continue
        number = int(text)
        if not (MIN_BALLOON_NO <= number <= MAX_BALLOON_NO):
            continue
        out.append(Balloon(
            number=number,
            center_pt=((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0),
            radius_pt=max(w[2] - w[0], w[3] - w[1]) / 2.0))
    return out


def recover_balloons(pdf_path, page_index: int = 0,
                     strategy: str = "auto") -> List[Balloon]:
    """strategy: 'shape' (digit inside a closed outline), 'text' (any plausible
    digit word), or 'auto' — shape first, falling back to text when the
    outlines are not recoverable as closed paths."""
    if strategy not in ("auto", "shape", "text"):
        raise ValueError(f"unknown balloon strategy {strategy!r}")
    if strategy == "text":
        doc = fitz.open(pdf_path)
        try:
            return _dedupe(_balloons_from_words(doc[page_index]))
        finally:
            doc.close()

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        shapes = _shape_rects(page)
        words = page.get_text("words")   # (x0, y0, x1, y1, text, ...)
        balloons = []
        for r in shapes:
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
        unique = _dedupe(balloons)
        if strategy == "auto" and len(unique) < _MIN_SHAPE_HITS:
            return _dedupe(_balloons_from_words(page))
        return unique
    finally:
        doc.close()


def _dedupe(balloons: List[Balloon]) -> List[Balloon]:
    """Drop repeats from doubled vector strokes / overlapping text runs."""
    seen, unique = set(), []
    for b in balloons:
        key = (b.number, round(b.center_pt[0]), round(b.center_pt[1]))
        if key not in seen:
            seen.add(key)
            unique.append(b)
    return unique


_WIDTH_BUCKETS = (4, 8, 16, 24, 32, 48, 96)


def _bucket(width: float) -> str:
    for edge in _WIDTH_BUCKETS:
        if width < edge:
            return f"<{edge}"
    return f">={_WIDTH_BUCKETS[-1]}"


def shape_report(pdf_path, page_index: int = 0) -> dict:
    """Calibration diagnostic for balloon recovery.

    Answers, without exposing any content: are the balloon numbers real text?
    how big are the candidate outlines? what primitive are they drawn with?
    and do the digits actually land inside one? Everything here is a count or a
    size bucket — never a value or a filename."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        digits = [w for w in page.get_text("words") if w[4].strip().isdigit()]
        near_square, kinds, widths = [], {}, {}
        for d in page.get_drawings():
            r = d["rect"]
            if r.width <= 0 or r.height <= 0:
                continue
            if abs(r.width - r.height) > max(r.width, r.height) * 0.35:
                continue
            if r.width > 200:
                continue
            near_square.append(r)
            widths[_bucket(r.width)] = widths.get(_bucket(r.width), 0) + 1
            for item in d["items"]:
                kinds[item[0]] = kinds.get(item[0], 0) + 1
        inside = 0
        for w in digits:
            centre = fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            if any(r.contains(centre) for r in near_square):
                inside += 1
        heights = sorted(round(w[3] - w[1], 1) for w in digits)
        return {
            "digit_words": len(digits),
            "digit_words_in_shape": inside,
            "near_square_shapes": len(near_square),
            "item_kinds": kinds,
            "shape_widths": widths,
            "digit_height_median": heights[len(heights) // 2] if heights else 0,
        }
    finally:
        doc.close()


def _annot_facts(page) -> dict:
    """Annotations are invisible to get_drawings()/get_text(): a stamping tool
    (Bluebeam, Adobe, …) adds balloons as annotation objects, so a page can look
    empty of balloons while being fully ballooned."""
    types, numbered = {}, 0
    try:
        annots = list(page.annots() or [])
    except Exception:
        return {"n_annots": 0, "annot_types": {}, "n_annot_numbers": 0}
    for a in annots:
        try:
            name = a.type[1] if isinstance(a.type, (tuple, list)) else str(a.type)
        except Exception:
            name = "unknown"
        types[name] = types.get(name, 0) + 1
        content = ((a.info or {}).get("content") or "").strip()
        if content.isdigit():
            numbered += 1
    return {"n_annots": len(annots), "annot_types": types,
            "n_annot_numbers": numbered}


def probe_pdf(pdf_path, page_index: int = 0) -> dict:
    """Day-one encoding inspection for one client PDF. Cheap, no model."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        circles = _circle_rects(page)
        shapes = _shape_rects(page)
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
            "n_shapes": len(shapes),
            "n_words": len(page.get_text("words")),
            "has_images": len(page.get_images()) > 0,
            **_annot_facts(page),
            "n_balloons": len(balloons),
            "numbers": numbers,
            "duplicate_numbers": dupes,
            "gaps": [n for n in range(1, gap_ceiling + 1) if n not in num_set],
        }
    finally:
        doc.close()
