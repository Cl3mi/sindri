"""Detection-only extraction. The point is entirely that it issues NO reads: the
whole reason to build it is that reads may be most of the ~26 h needed to obtain
train-split crops, and a path that quietly still reads would measure nothing."""
from pathlib import Path

import fitz

from app.pipeline.detect import Detection
from app.pipeline.extract import extract


class _CountingBackend:
    """Records how many times each stage was asked for work."""

    def __init__(self):
        self.detect_calls = 0
        self.read_calls = 0

    def detect_regions(self, image):
        self.detect_calls += 1
        # Real Detection instances, not a stand-in: detect_characteristics maps
        # these to page space and runs merge/dedupe over them, so a duck-typed
        # stub could fail for reasons that have nothing to do with detect_only.
        return [Detection(box=(10, 10, 120, 40), kind="dimension", conf=0.9)]

    def read_region(self, image):
        self.read_calls += 1
        from app.pipeline.ocr.base import OcrResult
        return OcrResult(text="20", confidence=0.9)


def _one_page_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sheet.pdf"
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text(fitz.Point(100, 100), "20 +0,1 -0,1", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def test_detect_only_issues_no_reads(tmp_path):
    backend = _CountingBackend()
    result = extract(_one_page_pdf(tmp_path), tmp_path / "work", dpi=72,
                     backend=backend, detect_only=True)
    assert backend.detect_calls > 0, "detection must still run"
    assert backend.read_calls == 0, (
        f"detect_only issued {backend.read_calls} reads — it would measure "
        f"nothing")
    assert result.characteristics, "boxes must still be returned"
    assert all(c.target_region is not None for c in result.characteristics)


def test_the_default_path_still_reads(tmp_path):
    """The regression half: detect_only must be opt-in, or every existing run
    silently stops transcribing."""
    backend = _CountingBackend()
    extract(_one_page_pdf(tmp_path), tmp_path / "work", dpi=72, backend=backend)
    assert backend.read_calls > 0
