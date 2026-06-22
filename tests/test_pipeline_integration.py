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


def test_extract_reads_gdt_box_with_gdt_prompt_and_sets_subtype(tmp_path, sample_pdf, monkeypatch):
    from app.pipeline.boxes import BoxDetection
    from app.pipeline.detect import merge_boxes
    import app.pipeline.extract as extract_mod

    box = BoxDetection(outer_box=(50, 50, 210, 82), inner_box=(54, 54, 206, 78),
                       cells=3, subtype="gdt", conf=0.8)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: merge_boxes(
                            [Detection(box=(50, 50, 210, 82), kind="gdt", conf=0.9)], [box]))
    backend = StubVLMBackend(detections=[], gdt_text="⊕ Ø0.1 A")
    rows = extract_mod.extract(sample_pdf, tmp_path, backend=backend)
    assert len(rows) == 1
    r = rows[0]
    assert r.subtype == "gdt"
    assert r.char_type == "Position"
    assert r.nominal == "0"
    assert r.upper_tol == "0,1"
    assert r.lower_tol == "0"


def test_extract_retags_boxed_100_series_as_note_ref(tmp_path, sample_pdf, monkeypatch):
    from app.pipeline.boxes import BoxDetection
    from app.pipeline.detect import merge_boxes
    import app.pipeline.extract as extract_mod

    box = BoxDetection(outer_box=(50, 50, 90, 78), inner_box=(54, 54, 86, 74),
                       cells=1, subtype="theoretical", conf=0.8)
    monkeypatch.setattr(extract_mod, "detect_characteristics",
                        lambda image, backend, **kw: merge_boxes(
                            [Detection(box=(50, 50, 90, 78), kind="dimension", conf=0.9)], [box]))
    backend = StubVLMBackend(detections=[], text="101")
    rows = extract_mod.extract(sample_pdf, tmp_path, backend=backend)
    assert len(rows) == 1
    r = rows[0]
    assert r.subtype == "note_ref"
    assert r.kind == "note"
    assert r.char_type == "Note"
    assert r.nominal == "101"
    assert r.upper_tol == "" and r.lower_tol == ""


def test_best_read_flags_rotation_ambiguity_on_vertical_crop():
    from PIL import Image
    from app.pipeline.extract import _best_read

    backend = StubVLMBackend(text="1,2", confidence=0.9)
    tall = Image.new("RGB", (20, 80), "white")
    text, conf, ambiguous = _best_read(backend, tall, vertical=True)
    assert text == "1,2"
    assert ambiguous is True


def test_best_read_not_ambiguous_on_horizontal_crop():
    from PIL import Image
    from app.pipeline.extract import _best_read

    backend = StubVLMBackend(text="1,2", confidence=0.9)
    wide = Image.new("RGB", (80, 20), "white")
    _, _, ambiguous = _best_read(backend, wide, vertical=False)
    assert ambiguous is False


def test_extract_flags_empty_read(sample_pdf, tmp_path, monkeypatch):
    from app.pipeline.detect import Detection
    import app.pipeline.boxes as boxes_mod
    import app.pipeline.extract as extract_mod
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    det = Detection((40, 40, 120, 70), "dimension", 0.9)
    monkeypatch.setattr(extract_mod, "detect_characteristics", lambda image, backend: [det])
    backend = StubVLMBackend(
        detections=[det], text="")
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert len(rows) == 1
    assert rows[0].needs_review is True
    assert rows[0].review_reasons == ["empty read"]


def test_extract_flags_missing_nominal(sample_pdf, tmp_path, monkeypatch):
    from app.pipeline.detect import Detection
    import app.pipeline.boxes as boxes_mod
    import app.pipeline.extract as extract_mod
    monkeypatch.setattr(boxes_mod, "detect_boxes", lambda image: [])
    det = Detection((40, 40, 120, 70), "dimension", 0.9)
    monkeypatch.setattr(extract_mod, "detect_characteristics", lambda image, backend: [det])
    backend = StubVLMBackend(
        detections=[det], text="garbled")
    rows = extract(sample_pdf, work_dir=tmp_path, dpi=300, backend=backend)
    assert len(rows) == 1
    assert rows[0].needs_review is True
    assert "missing nominal" in rows[0].review_reasons
