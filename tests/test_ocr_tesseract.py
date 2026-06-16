import shutil
import pytest
from PIL import Image, ImageDraw
from app.pipeline.ocr.tesseract_backend import TesseractBackend

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None,
                                reason="tesseract binary not installed")

def test_tesseract_reads_simple_text():
    img = Image.new("RGB", (220, 80), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 25), "12,5", fill="black")
    backend = TesseractBackend()
    result = backend.read_region(img)
    assert "12" in result.text
    assert 0.0 <= result.confidence <= 1.0
