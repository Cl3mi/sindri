from app.pipeline.detect import Detection, tile_grid, dedupe


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
