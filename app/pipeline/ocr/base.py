from dataclasses import dataclass
from typing import Protocol
from PIL import Image


@dataclass
class OcrResult:
    text: str
    confidence: float        # 0..1


class OCRBackend(Protocol):
    def read_region(self, image: Image.Image) -> OcrResult:
        ...
