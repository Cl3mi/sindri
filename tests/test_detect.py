from app.pipeline.detect import Detection, tile_grid, dedupe, merge_adjacent, parse_detections, detect_characteristics


def test_detection_dataclass_fields():
    d = Detection(box=(0, 0, 10, 10), kind="dimension", conf=0.9)
    assert d.box == (0, 0, 10, 10)
    assert d.kind == "dimension"
    assert d.conf == 0.9


def test_tile_grid_single_tile_when_image_smaller_than_tile():
    boxes = tile_grid(800, 600, tile=1280, overlap=0.15)
    assert boxes == [(0, 0, 800, 600)]


def test_tile_grid_covers_width_with_overlap():
    boxes = tile_grid(2000, 1000, tile=1280, overlap=0.15)
    assert len(boxes) == 2
    assert boxes[0] == (0, 0, 1280, 1000)
    assert boxes[1] == (720, 0, 2000, 1000)
    assert boxes[1][0] < boxes[0][2]


def test_dedupe_collapses_overlapping_same_kind():
    a = Detection(box=(0, 0, 100, 100), kind="dimension", conf=0.9)
    b = Detection(box=(5, 5, 105, 105), kind="dimension", conf=0.7)
    kept = dedupe([a, b], iou_thresh=0.5)
    assert len(kept) == 1
    assert kept[0].conf == 0.9


def test_dedupe_keeps_different_kinds_that_overlap():
    a = Detection(box=(0, 0, 100, 100), kind="dimension", conf=0.9)
    b = Detection(box=(0, 0, 100, 100), kind="gdt", conf=0.8)
    kept = dedupe([a, b], iou_thresh=0.5)
    assert len(kept) == 2


def test_dedupe_keeps_distant_same_kind():
    a = Detection(box=(0, 0, 50, 50), kind="dimension", conf=0.9)
    b = Detection(box=(500, 500, 550, 550), kind="dimension", conf=0.8)
    assert len(dedupe([a, b])) == 2


def test_merge_adjacent_combines_vertically_stacked_same_kind():
    nominal = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.8)
    tol = Detection(box=(12, 35, 52, 55), kind="dimension", conf=0.6)
    merged = merge_adjacent([nominal, tol], x_tol=20, y_gap=20)
    assert len(merged) == 1
    assert merged[0].box == (10, 10, 52, 55)


def test_merge_adjacent_leaves_far_apart_boxes():
    a = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.8)
    b = Detection(box=(10, 200, 50, 220), kind="dimension", conf=0.6)
    assert len(merge_adjacent([a, b], x_tol=20, y_gap=20)) == 2


def test_merge_adjacent_does_not_merge_different_kinds():
    a = Detection(box=(10, 10, 50, 30), kind="dimension", conf=0.8)
    b = Detection(box=(12, 35, 52, 55), kind="note", conf=0.6)
    assert len(merge_adjacent([a, b], x_tol=20, y_gap=20)) == 2


def test_parse_detections_valid_json():
    raw = '[{"box":[1,2,3,4],"kind":"dimension","conf":0.9}]'
    dets = parse_detections(raw)
    assert len(dets) == 1
    assert dets[0].box == (1, 2, 3, 4)
    assert dets[0].kind == "dimension"


def test_parse_detections_strips_code_fence_and_prose():
    raw = 'Here you go:\n```json\n[{"box":[0,0,5,5],"kind":"note"}]\n```'
    dets = parse_detections(raw)
    assert len(dets) == 1
    assert dets[0].kind == "note"
    assert dets[0].conf == 1.0


def test_parse_detections_garbage_returns_empty():
    assert parse_detections("not json at all") == []
    assert parse_detections("") == []


def test_parse_detections_skips_invalid_items():
    raw = ('[{"box":[0,0,5,5],"kind":"dimension"},'
           '{"box":[10,10,8,8],"kind":"dimension"},'
           '{"kind":"note"},'
           '{"box":[1,1,2,2],"kind":"weird"}]')
    dets = parse_detections(raw)
    assert len(dets) == 2
    assert dets[0].box == (0, 0, 5, 5)
    assert dets[1].kind == "dimension"


from PIL import Image
from tests.conftest import StubVLMBackend


def test_detect_characteristics_single_tile_passes_box_through():
    img = Image.new("RGB", (400, 300), "white")
    backend = StubVLMBackend(detections=[Detection((10, 20, 60, 40), "dimension", 0.9)])
    dets = detect_characteristics(img, backend)
    assert len(dets) == 1
    assert dets[0].box == (10, 20, 60, 40)


def test_detect_characteristics_offsets_per_tile():
    img = Image.new("RGB", (2000, 1000), "white")
    backend = StubVLMBackend(detections=[Detection((0, 0, 30, 30), "note", 0.8)])
    dets = detect_characteristics(img, backend)
    xs = sorted(d.box[0] for d in dets)
    assert xs == [0, 720]


from app.pipeline.boxes import BoxDetection
from app.pipeline.detect import merge_boxes


def test_detection_dataclass_has_box_fields_defaulting_none():
    d = Detection(box=(0, 0, 10, 10), kind="dimension", conf=0.9)
    assert d.inner_box is None
    assert d.cells == 1
    assert d.subtype is None


def test_merge_boxes_cv_wins_on_overlap():
    vlm = [Detection(box=(100, 100, 160, 130), kind="dimension", conf=0.9)]
    boxes = [BoxDetection(outer_box=(98, 98, 162, 132), inner_box=(102, 102, 158, 128),
                          cells=1, subtype="theoretical", conf=0.8)]
    merged = merge_boxes(vlm, boxes)
    assert len(merged) == 1
    assert merged[0].subtype == "theoretical"
    assert merged[0].kind == "theoretical"
    assert merged[0].inner_box == (102, 102, 158, 128)


def test_merge_boxes_drops_non_overlapping_cv_box():
    # intersection hybrid: a CV box with no VLM support is dropped
    vlm = [Detection(box=(0, 0, 20, 20), kind="dimension", conf=0.9)]
    boxes = [BoxDetection(outer_box=(300, 300, 360, 330), inner_box=(304, 304, 356, 326),
                          cells=3, subtype="gdt", conf=0.8)]
    merged = merge_boxes(vlm, boxes)
    assert len(merged) == 1
    assert merged[0].kind == "dimension"        # only the VLM detection survives
    assert all(m.subtype != "gdt" for m in merged)


def test_box_to_detection_maps_note_ref_to_note_kind():
    from app.pipeline.detect import _box_to_detection
    d = _box_to_detection(BoxDetection(outer_box=(0, 0, 30, 28), inner_box=(4, 4, 26, 24),
                                       cells=1, subtype="note_ref", conf=0.8))
    assert d.kind == "note"
    assert d.subtype == "note_ref"


def test_detect_characteristics_keeps_cv_box_overlapping_vlm():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(img).rectangle([100, 100, 180, 132], outline="black", width=3)
    # VLM detects a callout at the same spot; the CV box refines it (clean crop)
    backend = StubVLMBackend(detections=[Detection((100, 100, 180, 132), "dimension", 0.9)])
    dets = detect_characteristics(img, backend)
    assert any(d.subtype == "theoretical" and d.inner_box is not None for d in dets)
