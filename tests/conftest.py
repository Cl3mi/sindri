from pathlib import Path
import shutil
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture(scope="session", autouse=True)
def ensure_sample_pdf():
    FIXTURES.mkdir(exist_ok=True)
    target = FIXTURES / "sample.pdf"
    if not target.exists():
        root_pdf = Path(__file__).parents[1] / "sample.pdf"
        shutil.copy(root_pdf, target)

@pytest.fixture
def sample_pdf():
    return FIXTURES / "sample.pdf"


from app.pipeline.detect import Detection
from app.pipeline.ocr.base import OcrResult


class StubVLMBackend:
    """Test double for the VLM backend: returns canned detections (tile-local)
    and a canned transcription. Has detect_regions, so extract() treats it as a
    detection-capable backend."""

    def __init__(self, detections=None, text="1,2 +0,1 -0,1", confidence=0.9,
                 gdt_text="⊕ Ø0.1 A"):
        self._detections = detections or []
        self._text = text
        self._confidence = confidence
        self._gdt_text = gdt_text

    def detect_regions(self, image):
        return [Detection(box=d.box, kind=d.kind, conf=d.conf)
                for d in self._detections]

    def read_region(self, image):
        return OcrResult(text=self._text, confidence=self._confidence)

    def read_region_gdt(self, image):
        return OcrResult(text=self._gdt_text, confidence=self._confidence)
