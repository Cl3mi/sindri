"""OCR line proposals: text regions the VLM detector did not return. The pure
parts are tested without OCR quality in the loop -- rotation mapping and the
line filter -- because tesseract on a synthetic image would test tesseract."""
import pytest
from PIL import Image

from app.pipeline import proposals as pp


def test_unrotate_maps_a_box_from_the_90_degree_image_back():
    # Original 200x100. PIL rotate(90, expand=True) is counter-clockwise, so
    # original (x, y) -> rotated (y, W - x). A box at x 10..30, y 40..50 in the
    # original sits at x 40..50, y 170..190 in the rotated image.
    assert pp._unrotate_box((40, 170, 50, 190), orig_w=200) == (10, 40, 30, 50)


def test_unrotate_agrees_with_pil_on_a_real_pixel():
    """Pin the mapping to PIL's actual behaviour, not to my reading of it."""
    im = Image.new("L", (200, 100), 0)
    im.putpixel((20, 45), 255)
    rot = im.rotate(90, expand=True)
    xs, ys = zip(*[(x, y) for x in range(rot.width) for y in range(rot.height)
                   if rot.getpixel((x, y)) == 255])
    box = pp._unrotate_box((min(xs), min(ys), max(xs) + 1, max(ys) + 1),
                           orig_w=200)
    assert box[0] <= 20 < box[2] and box[1] <= 45 < box[3]


def _tess(lines):
    """A minimal image_to_data dict: one word per entry."""
    keys = ("text", "conf", "left", "top", "width", "height",
            "block_num", "par_num", "line_num")
    d = {k: [] for k in keys}
    for (text, conf, box, line) in lines:
        d["text"].append(text); d["conf"].append(conf)
        d["left"].append(box[0]); d["top"].append(box[1])
        d["width"].append(box[2] - box[0]); d["height"].append(box[3] - box[1])
        d["block_num"].append(1); d["par_num"].append(1)
        d["line_num"].append(line)
    return d


def test_lines_group_words_and_keep_only_dimension_shaped_text():
    data = _tess([
        ("Ø20", 90, (10, 10, 50, 30), 1), ("±0,1", 85, (55, 10, 90, 30), 1),
        ("SCALE", 90, (10, 60, 60, 80), 2),                       # no digit
        ("1234567890", 90, (10, 100, 150, 120), 3),               # part no.
        ("7", 10, (10, 140, 20, 160), 4),                          # low conf
    ])
    lines = pp._dimension_lines(data)
    assert lines == [((10, 10, 90, 30), "Ø20 ±0,1")]


def test_proposals_overlapping_an_existing_detection_are_dropped():
    cand = [(10, 10, 90, 30), (300, 300, 340, 320)]
    existing = [(80, 5, 200, 40)]
    assert pp._not_covered(cand, existing, margin=8) == [(300, 300, 340, 320)]


def test_resolve_proposals_rejects_a_typo():
    assert pp.resolve_proposals({}) is None
    assert pp.resolve_proposals({"SINDRI_PROPOSALS": "ocr"}) == "ocr"
    with pytest.raises(ValueError):
        pp.resolve_proposals({"SINDRI_PROPOSALS": "orc"})


def test_detection_source_defaults_to_the_vlm():
    from app.pipeline.detect import Detection
    assert Detection(box=(0, 0, 1, 1), kind="dimension", conf=1.0).source == "vlm"


def test_verified_proposals_refuses_a_backend_without_verification():
    class NoVerify:
        pass
    with pytest.raises(RuntimeError):
        pp.verified_proposals(Image.new("RGB", (10, 10), "white"), [],
                              NoVerify(), crop_fn=lambda b: None)


def test_verified_proposals_keeps_only_confirmed_ones(monkeypatch):
    monkeypatch.setattr(pp, "ocr_proposals",
                        lambda image, existing: [(0, 0, 10, 10), (20, 0, 30, 10)])

    class Backend:
        def verify_callout(self, crop):
            return crop == (20, 0, 30, 10)
    out = pp.verified_proposals(Image.new("RGB", (40, 20), "white"), [],
                                Backend(), crop_fn=lambda b: b)
    assert [d.box for d in out] == [(20, 0, 30, 10)]
    assert out[0].source == "proposal" and out[0].kind == "dimension"
