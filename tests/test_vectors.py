from app.pipeline.vectors import extract_segments

def test_extract_segments(sample_pdf):
    segs = extract_segments(sample_pdf, scale=200 / 72)
    assert len(segs) > 50          # drawing is line-art heavy
    s = segs[0]
    assert len(s) == 4            # (x0, y0, x1, y1) image-space
