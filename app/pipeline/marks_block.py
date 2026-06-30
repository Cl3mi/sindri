"""Marks-block path: locate the top-right Mark/Description legend, read it as
structured bilingual data, mask it before the main detector runs so its 101…
numbers cannot be misclassified as note-ref callouts. Parallel to (and
independent of) notes_block.py."""
import re
import sys
from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image, ImageDraw

from app.models import Mark, MarkBlock


# Top-level row only. Marks table has no sub-bullets, so any "<pos>.<sub>\t…"
# line is rejected by this regex and dropped silently.
_ROW_RE = re.compile(r"^(10[0-9]|1[1-9][0-9])\t([^\t]*)\t?(.*)$")


def parse_marks_block(raw: str, region: Tuple[float, float, float, float]) -> MarkBlock:
    """Parse the tab-separated marks transcription into a MarkBlock.

    Each line is expected as '<pos>\\t<en>\\t<de>'. Lines containing a
    sub-index (e.g. '101.1\\t…') or any other shape are dropped silently
    (non-fatal pipeline convention)."""
    marks: List[Mark] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if "." in line.split("\t", 1)[0]:
            # sub-bullet — not expected in marks; drop
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        marks.append(Mark(
            pos=int(m.group(1)),
            text_en=m.group(2).strip(),
            text_de=m.group(3).strip(),
            raw_text=line,
        ))
    return MarkBlock(region=region, marks=marks)


def review_flags_mark(mark: Mark, two_columns: bool) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons) for a parsed mark.

    Gating: an empty read is its own reason and does not also report
    'missing translation'."""
    reasons: List[str] = []
    if not (mark.raw_text or "").strip():
        reasons.append("empty read")
    else:
        if two_columns and (not mark.text_en.strip() or not mark.text_de.strip()):
            reasons.append("missing translation")
    return bool(reasons), reasons


@dataclass
class MarksBlockRegion:
    outer_box: Tuple[int, int, int, int]
    lang_columns: List[Tuple[int, int]]


def mask_region(image: Image.Image, region: MarksBlockRegion) -> Image.Image:
    """Return a copy of `image` with `region.outer_box` filled white. The
    original image is preserved so downstream manual re-reads still work."""
    out = image.copy()
    x0, y0, x1, y1 = region.outer_box
    if x1 > x0 and y1 > y0:
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill="white")
    return out
