from app.pipeline.anchors import extract_anchors

def test_extract_balloon_anchors(sample_pdf):
    anchors = extract_anchors(sample_pdf, scale=200 / 72)
    nums = sorted(a.number for a in anchors)
    assert nums == list(range(1, 23))
    a1 = next(a for a in anchors if a.number == 1)
    assert a1.x > 0 and a1.y > 0
