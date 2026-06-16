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
    balloon_xy: Optional[Tuple[float, float]] = None        # image-space
    target_region: Optional[Tuple[float, float, float, float]] = None  # x0,y0,x1,y1
