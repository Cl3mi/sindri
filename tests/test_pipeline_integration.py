from app.pipeline.detect import Detection
from app.pipeline.extract import extract
from tests.conftest import StubVLMBackend


def test_extract_detects_numbers_places_and_reads(sample_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr("app.pipeline.boxes.detect_boxes", lambda image: [])
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1",
    )
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert len(rows) >= 1
    for r in rows:
        assert r.source == "auto"
        assert r.id != ""
        assert r.target_region is not None
        assert r.balloon_xy is not None
        assert r.char_type == "Distance"
        assert r.nominal == "1,2"
    positions = sorted(r.pos for r in rows)
    assert positions == list(range(1, len(rows) + 1))


def test_extract_requires_detection_capable_backend(sample_pdf, tmp_path):
    class ReadOnlyBackend:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)

    import pytest
    with pytest.raises(RuntimeError, match="VLM backend"):
        extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=ReadOnlyBackend())
