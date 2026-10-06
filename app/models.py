import math
from typing import Annotated, List, Optional, Tuple
from pydantic import BaseModel, BeforeValidator


def _finite_or_zero(value):
    """Confidence, with NaN/inf/None collapsed to 0.0 at the model boundary.

    A NaN confidence made a whole nine-hour run unscoreable on 2026-09-15:
    pydantic ACCEPTS NaN at construction, `model_dump_json` serialises it as
    `null`, and reloading that null raises -- so the dump was write-only and
    nothing said so until someone tried to score it. float16 AWQ can produce a
    degenerate logits row whose softmax is NaN, and the test split is where the
    structurally atypical drawings that trigger it live.

    0.0 rather than a refusal, for two reasons. It is what
    `extract._safe_read` already returns for a failed read, so the meaning is
    consistent; and it puts the row BELOW `review.LOW_CONF`, where a reviewer
    sees it. NaN would do the opposite: every comparison against NaN is False,
    so `conf < LOW_CONF` never fires and the row ships as a SILENT error --
    quietly worse than the crash that exposed it.

    None is accepted because the dumps already on disk carry it. Refusing them
    would mean re-predicting nine GPU hours to recover a number already there.
    """
    if value is None:
        return 0.0
    try:
        return 0.0 if not math.isfinite(float(value)) else value
    except (TypeError, ValueError):
        return value        # let pydantic produce its own error message


Confidence = Annotated[float, BeforeValidator(_finite_or_zero)]


def _finite_or_none(value):
    """A model probability that may be absent: NaN/inf become None, never a
    number. Same r3-awqtest lesson as _finite_or_zero (a NaN makes a dump
    unreloadable), but None rather than 0.0 here, because 0.0 would read as a
    confident "no" and a verdict that was never given must not drop a value."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return f if math.isfinite(f) else None


OptionalProbability = Annotated[Optional[float], BeforeValidator(_finite_or_none)]


class Characteristic(BaseModel):
    pos: int
    char_type: str = ""          # Distance|Diameter|Radius|Flatness|Material|Note
    nominal: str = ""
    upper_tol: str = ""
    lower_tol: str = ""
    raw_text: str = ""
    confidence: Confidence = 0.0
    id: str = ""                 # stable per-row id for the review UI
    kind: str = ""               # detector kind: dimension|gdt|surface|note|material
    subtype: str = ""            # box sub-type: gdt|theoretical|reference|note_ref
    source: str = "auto"         # "auto" (detected) or "manual" (user-added)
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty read", "missing nominal"]
    balloon_xy: Optional[Tuple[float, float]] = None        # image-space
    target_region: Optional[Tuple[float, float, float, float]] = None  # x0,y0,x1,y1
    note_ref_pos: Optional[int] = None    # set when subtype == "note_ref"
    # The verifier's P(yes) that this row is a ballooned characteristic
    # (pipeline/verifier.py). None = never asked, or no answer -- including
    # every dump written before the verifier existed.
    verifier_p: OptionalProbability = None
    # True for a row a drop stage >= 2 removed (low confidence etc.): shown to
    # the reviewer, never exported until confirmed. The API and the UI carry
    # it; the scorer never sees such rows (they live in
    # ExtractionResult.suggestions, not characteristics).
    suggested: bool = False


class Note(BaseModel):
    pos: int                          # 101, 102, … for top-level; 1, 2, … for sub-bullets
    parent_pos: Optional[int] = None  # set for sub-bullets (1, 2, 3 → parent 101)
    sub_index: Optional[int] = None   # 1, 2, 3 within a parent; None for top-level
    text_en: str = ""
    text_de: str = ""
    raw_text: str = ""
    box: Optional[Tuple[float, float, float, float]] = None
    confidence: Confidence = 0.0
    needs_review: bool = False
    review_reasons: List[str] = []


class NoteBlock(BaseModel):
    region: Tuple[float, float, float, float]
    notes: List[Note] = []


class Mark(BaseModel):
    pos: int                          # 101, 102, …
    text_en: str = ""
    text_de: str = ""
    raw_text: str = ""
    needs_review: bool = False
    review_reasons: List[str] = []


class MarkBlock(BaseModel):
    region: Tuple[float, float, float, float]
    marks: List[Mark] = []


class TitleField(BaseModel):
    label: str = ""          # caption as printed, e.g. "Sheet / Blatt"
    label_en: str = ""
    label_de: str = ""
    value: str = ""
    box: Optional[Tuple[float, float, float, float]] = None
    confidence: Confidence = 0.0
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty value", "missing caption"]


class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
    title_block: List[TitleField] = []
    marks: Optional[MarkBlock] = None
    # Rows removed by drop stages >= 2, kept for the reviewer's tray. NOT
    # characteristics: scoring, delivered precision and exports read
    # `characteristics` only (docs/plans/2026-10-07-suggestion-tray-design.md).
    suggestions: List[Characteristic] = []
    # Every box above is in RENDER PIXELS, so the result cannot be converted
    # back to PDF points without the resolution it was produced at. The render
    # clamps its dpi to a pixel budget for large-format sheets, so this is not
    # always the requested dpi/72 — read it here, never recompute it.
    render_scale: Optional[float] = None    # render pixels per PDF point
