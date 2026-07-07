from PIL import Image, ImageDraw
from app.pipeline.boxes import BoxDetection, detect_boxes, tighten_to_ink


def _blank(w=400, h=300):
    return Image.new("RGB", (w, h), "white")


def test_box_detection_dataclass_fields():
    b = BoxDetection(outer_box=(0, 0, 10, 10), inner_box=(2, 2, 8, 8),
                     cells=1, subtype="theoretical", conf=0.8)
    assert b.outer_box == (0, 0, 10, 10)
    assert b.inner_box == (2, 2, 8, 8)
    assert b.cells == 1


def test_blank_page_yields_no_boxes():
    assert detect_boxes(_blank()) == []


def test_single_box_classified_theoretical_with_inset_inner():
    img = _blank()
    ImageDraw.Draw(img).rectangle([100, 100, 180, 132], outline="black", width=3)
    boxes = detect_boxes(img)
    assert len(boxes) == 1
    b = boxes[0]
    assert b.subtype == "theoretical"
    assert b.cells == 1
    assert b.inner_box[0] > b.outer_box[0] and b.inner_box[1] > b.outer_box[1]
    assert b.inner_box[2] < b.outer_box[2] and b.inner_box[3] < b.outer_box[3]


def test_small_single_cell_box_is_theoretical_not_size_based():
    img = _blank()
    ImageDraw.Draw(img).rectangle([50, 50, 80, 78], outline="black", width=3)
    boxes = detect_boxes(img)
    assert len(boxes) == 1
    assert boxes[0].subtype == "theoretical"
    assert boxes[0].cells == 1


def test_multi_cell_box_classified_gdt():
    img = _blank()
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 260, 132], outline="black", width=3)
    d.line([153, 100, 153, 132], fill="black", width=3)   # divider 1
    d.line([206, 100, 206, 132], fill="black", width=3)   # divider 2
    boxes = detect_boxes(img)
    assert any(b.subtype == "gdt" and b.cells >= 3 for b in boxes)


def test_full_page_border_is_ignored():
    img = _blank()
    ImageDraw.Draw(img).rectangle([1, 1, 398, 298], outline="black", width=3)
    assert detect_boxes(img) == []


def test_tighten_hugs_ink_in_oversized_box():
    img = _blank()
    # a small glyph blob near the top-left of a much larger detection box
    ImageDraw.Draw(img).rectangle([110, 108, 150, 128], fill="black")
    box = (100, 100, 300, 260)                 # generous VLM-style box
    t = tighten_to_ink(img, box, pad=3)
    # tight box surrounds the blob (with pad) and is far smaller than the input
    assert 100 <= t[0] <= 110 and 100 <= t[1] <= 108
    assert 150 <= t[2] <= 160 and 128 <= t[3] <= 138
    assert (t[2] - t[0]) * (t[3] - t[1]) < 0.25 * (300 - 100) * (260 - 100)


def test_tighten_blank_box_returns_original():
    box = (50, 50, 200, 200)
    assert tighten_to_ink(_blank(), box) == box


def test_tighten_degenerate_box_returns_original():
    assert tighten_to_ink(_blank(), (10, 10, 10, 40)) == (10, 10, 10, 40)


def test_tighten_full_ink_box_roughly_unchanged():
    img = _blank()
    ImageDraw.Draw(img).rectangle([60, 60, 190, 190], fill="black")
    t = tighten_to_ink(img, (60, 60, 190, 190), pad=3)
    # already tight: stays within a few px of the input on every side
    assert all(abs(a - b) <= 4 for a, b in zip(t, (60, 60, 190, 190)))
