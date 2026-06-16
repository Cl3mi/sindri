import shutil
import pytest
from app.pipeline.extract import extract

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed")

def test_extract_returns_all_balloons(sample_pdf, tmp_path):
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300)
    positions = sorted(r.pos for r in rows)
    for n in range(1, 23):
        assert n in positions

@needs_tesseract
def test_extract_recovers_known_values(sample_pdf, tmp_path):
    rows = {r.pos: r for r in extract(sample_pdf, work_dir=tmp_path, dpi=300)}
    assert rows[4].char_type == "Diameter"
    assert rows[5].char_type == "Diameter"
    assert rows[1].nominal != ""
