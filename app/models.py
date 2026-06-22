from typing import Optional, Tuple
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
    balloon_xy: Optional[Tuple[float, float]] = None        # image-space
    target_region: Optional[Tuple[float, float, float, float]] = None  # x0,y0,x1,y1
