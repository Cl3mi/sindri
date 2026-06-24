from typing import List, Optional, Tuple
from pydantic import BaseModel


class Characteristic(BaseModel):
    pos: int
    char_type: str = ""          # Distance|Diameter|Radius|Flatness|Material|Note
    nominal: str = ""
    upper_tol: str = ""
    lower_tol: str = ""
    raw_text: str = ""
    confidence: float = 0.0
    id: str = ""                 # stable per-row id for the review UI
    kind: str = ""               # detector kind: dimension|gdt|surface|note|material
    subtype: str = ""            # box sub-type: gdt|theoretical|reference|note_ref
    source: str = "auto"         # "auto" (detected) or "manual" (user-added)
    needs_review: bool = False
    review_reasons: List[str] = []   # e.g. ["empty read", "missing nominal"]
    balloon_xy: Optional[Tuple[float, float]] = None        # image-space
    target_region: Optional[Tuple[float, float, float, float]] = None  # x0,y0,x1,y1
    note_ref_pos: Optional[int] = None    # set when subtype == "note_ref"


class Note(BaseModel):
    pos: int                          # 101, 102, … for top-level; 1, 2, … for sub-bullets
    parent_pos: Optional[int] = None  # set for sub-bullets (1, 2, 3 → parent 101)
    sub_index: Optional[int] = None   # 1, 2, 3 within a parent; None for top-level
    text_en: str = ""
    text_de: str = ""
    raw_text: str = ""
    box: Optional[Tuple[float, float, float, float]] = None
    confidence: float = 0.0
    needs_review: bool = False
    review_reasons: List[str] = []


class NoteBlock(BaseModel):
    region: Tuple[float, float, float, float]
    notes: List[Note] = []


class ExtractionResult(BaseModel):
    characteristics: List[Characteristic]
    notes: Optional[NoteBlock] = None
