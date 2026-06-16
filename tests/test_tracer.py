from app.pipeline.tracer import trace_target, _nearest_segment

def test_nearest_segment_picks_touching_leader():
    anchor = (100.0, 100.0)
    segments = [
        (102.0, 100.0, 300.0, 100.0),   # leader starting near anchor
        (500.0, 500.0, 600.0, 600.0),   # unrelated
    ]
    seg = _nearest_segment(anchor, segments, max_dist=20)
    assert seg == (102.0, 100.0, 300.0, 100.0)

def test_trace_target_region_at_far_end():
    anchor = (100.0, 100.0)
    segments = [(102.0, 100.0, 300.0, 100.0)]
    region = trace_target(anchor, segments, region=80, max_dist=20)
    x0, y0, x1, y1 = region
    assert x0 < 300 < x1 and y0 < 100 < y1

def test_trace_target_fallback_when_no_leader():
    anchor = (100.0, 100.0)
    region = trace_target(anchor, [], region=80, max_dist=20)
    x0, y0, x1, y1 = region
    assert x0 < 100 < x1 and y0 < 100 < y1
