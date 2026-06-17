from app.pipeline.detect import Detection, tile_grid


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
