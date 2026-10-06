"""The verifier: one yes/no question to the serving VLM about a detection that
would otherwise ship unchecked -- "is this a characteristic an inspector would
balloon?".

Why it exists: after the phantom drops, dev still ships 30 phantom values read
at confidence >= 0.99, which no confidence rule can reach (2026-10-06). Read
confidence answers "is this text read correctly?"; the verifier answers a
different question, "is this a characteristic at all?", from CONTEXT the tight
read crop deliberately leaves out.

Everything here is FROZEN by docs/plans/2026-10-07-verifier-registration.md §2.
The prompt deliberately lives outside vlm_backend's hashed prompts: adding it
must not move prompt_sha256, or every existing run would stop being comparable.
"""
import hashlib
import math
from typing import Optional, Tuple

from PIL import Image, ImageDraw

VERIFY_PROMPT = (
    "The red rectangle marks one region detected on a technical drawing. "
    "Is the content inside the red rectangle a dimension or tolerance "
    "callout (a measured size with or without tolerance) that a quality "
    "inspector would number with an inspection balloon? Answer with "
    "exactly one word: yes or no.")

_CONTEXT_FACTOR = 3        # the box plus its own size on every side
_MIN_CONTEXT_PX = 200      # tiny boxes still get enough page to judge from
_OUTLINE = (255, 0, 0)
_OUTLINE_PX = 3
_NO_ANSWER = 1e-6          # P(yes)+P(no) below this: the model said neither


def verifier_prompt_hash() -> str:
    """Recorded as RunConfig.extra["verifier_prompt"], so two verifier runs
    with different prompts can never be mistaken for one another."""
    return hashlib.sha256(VERIFY_PROMPT.encode("utf-8")).hexdigest()[:16]


def context_crop(page: Image.Image, box) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """(crop, box inside the crop). The crop is a COPY with the box outlined
    in red; the page itself is never drawn on, because the same page image is
    reused for every row of the document."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    cw = max(_CONTEXT_FACTOR * w, _MIN_CONTEXT_PX)
    ch = max(_CONTEXT_FACTOR * h, _MIN_CONTEXT_PX)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    left, top = cx - cw // 2, cy - ch // 2
    right, bottom = left + cw, top + ch
    left, top = max(0, left), max(0, top)
    right, bottom = min(page.width, right), min(page.height, bottom)
    crop = page.crop((left, top, right, bottom)).convert("RGB")
    inner = (x0 - left, y0 - top, x1 - left, y1 - top)
    ImageDraw.Draw(crop).rectangle(inner, outline=_OUTLINE, width=_OUTLINE_PX)
    return crop, inner


def verifier_probability(p_yes: float, p_no: float) -> Optional[float]:
    """P(yes) / (P(yes) + P(no)) from the first decoding step, or None when
    the model did not answer yes or no (or anything is non-finite). None is
    never a drop reason: a verdict that was not given cannot remove a value."""
    if not (math.isfinite(p_yes) and math.isfinite(p_no)):
        return None
    total = p_yes + p_no
    if total < _NO_ANSWER:
        return None
    return p_yes / total
