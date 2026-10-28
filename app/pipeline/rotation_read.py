"""Orientation search for the manual scan tool (`/api/read_region`).

The scan tool used to read the rectangle at 0 degrees only, so a value printed
at 90 or 45 degrees came back empty or garbled (operator feedback, 2026-10-28).
Auto-detection already tries +-90 for tall boxes (`extract._best_read`); this
widens that to the angles a drawing really uses, for the one path where the
reviewer has already told us there IS a value in the rectangle.

Orientations are tried in stages and the search stops at the first stage that
yields a good read, so the common upright case still costs ONE backend call --
the VLM read is the expensive part, and most scans are horizontal.
"""
from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image

from app.pipeline.parser import parse_value
from app.pipeline.review import LOW_CONF

# Counter-clockwise degrees applied to the crop before reading (PIL's sign).
# Stage 1 is today's behaviour; the rest only run when it did not read well.
_STAGES: List[Tuple[int, ...]] = [
    (0,),
    (-90, 90),
    (180, -45, 45, -135, 135),
]

# A rotated read replaces the upright one only when it scores this much
# higher. Without it a mediocre horizontal read gets swapped for whichever
# rotation hallucinated a slightly more confident string.
ROTATION_MARGIN = 0.15


@dataclass
class RotatedRead:
    text: str
    confidence: float
    angle: int          # rotation applied to the crop to read it; 0 = as drawn


def _read(backend, crop: Image.Image, angle: int) -> Tuple[str, float]:
    im = crop if angle == 0 else crop.rotate(angle, expand=True, fillcolor="white")
    try:
        r = backend.read_region(im)
        return r.text, r.confidence
    except Exception:
        # A reader that fell over is an empty read, as in the 0 degree path;
        # the reviewer then types the value rather than seeing a 500.
        return "", 0.0


def _plausibility(text: str) -> float:
    """0..1: how much `text` looks like a dimension rather than OCR noise.
    A real value is dense in digits and arrives as a few substantial tokens
    ('120+0.30', '12 +0.1 -0.2'); noise from a wrong orientation is single
    characters split by spaces ('2 X 0 90', 'X M - + .0 A - M'). Parsing a
    nominal and a high confidence cannot tell these apart -- measured with
    real tesseract on synthetic text, a correct upright read lost to noise
    on exactly those two terms."""
    tokens = text.split()
    chars = sum(len(t) for t in tokens)
    if not chars:
        return 0.0
    digits = sum(ch.isdigit() for t in tokens for ch in t)
    avg = chars / len(tokens)
    return (digits / chars) * min(1.0, avg / 3.0) ** 2


def _score(text: str, conf: float) -> float:
    c = parse_value(text)
    return (1.0 if c.nominal else 0.0) + (0.5 if c.upper_tol else 0.0) + conf + _plausibility(text)


def _good(text: str, conf: float) -> bool:
    return bool(parse_value(text).nominal) and conf >= LOW_CONF


def read_best_rotation(backend, crop: Image.Image) -> RotatedRead:
    upright = _read(backend, crop, 0)
    upright_score = _score(*upright)
    best = RotatedRead(upright[0], upright[1], 0)
    best_score = upright_score
    if _good(*upright):
        return best
    for stage in _STAGES[1:]:
        for angle in stage:
            text, conf = _read(backend, crop, angle)
            s = _score(text, conf)
            if s > best_score and s - upright_score >= ROTATION_MARGIN:
                best, best_score = RotatedRead(text, conf, angle), s
        if _good(best.text, best.confidence):
            break
    return best
