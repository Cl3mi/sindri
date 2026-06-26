"""Title-block path: locate the bottom-right Schriftfeld, detect its grid
cells with OpenCV, read each non-empty cell as a {label, value} pair, mask the
region before the main detector runs. Mirrors notes_block.py. Never raises from
the public entry points; any failure yields an empty title block."""
import json
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import cv2
from PIL import Image, ImageDraw

from app.models import TitleField


_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def parse_title_cell(raw: str) -> Tuple[str, str]:
    """Return (label, value) from the backend's per-cell read. Accepts a JSON
    object {"label":..,"value":..} (optionally wrapped in code fences), then a
    'label: value' fallback, else ("", raw)."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    cleaned = _FENCE_RE.sub("", raw).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return str(obj.get("label", "")).strip(), str(obj.get("value", "")).strip()
    except (ValueError, TypeError):
        pass
    if ":" in cleaned:
        label, _, value = cleaned.partition(":")
        return label.strip(), value.strip()
    return "", cleaned


def split_label(label: str) -> Tuple[str, str]:
    """Split a bilingual caption 'English / German' into (en, de)."""
    if "/" in label:
        en, _, de = label.partition("/")
        return en.strip(), de.strip()
    return label.strip(), ""


def review_flags_field(value: str, label: str,
                       expect_caption: bool = True) -> Tuple[bool, List[str]]:
    """Return (needs_review, reasons). An empty value is always a reason. A
    present value with no caption is flagged only for grid cells
    (expect_caption=True); loose text legitimately has no caption."""
    reasons: List[str] = []
    if not value.strip():
        reasons.append("empty value")
    elif expect_caption and not label.strip():
        reasons.append("missing caption")
    return bool(reasons), reasons
