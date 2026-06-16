import math


def _dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _nearest_segment(anchor, segments, max_dist):
    ax, ay = anchor
    best = None
    best_d = max_dist
    for seg in segments:
        x0, y0, x1, y1 = seg
        d = min(_dist(ax, ay, x0, y0), _dist(ax, ay, x1, y1))
        if d < best_d:
            best_d = d
            best = seg
    return best


def trace_target(anchor, segments, region: float = 120, max_dist: float = 25):
    """Return an (x0,y0,x1,y1) image-space box around the leader's far endpoint."""
    ax, ay = anchor
    seg = _nearest_segment(anchor, segments, max_dist)
    if seg is None:
        cx, cy = ax, ay
    else:
        x0, y0, x1, y1 = seg
        # near end = endpoint closest to anchor; target = the other end
        if _dist(ax, ay, x0, y0) <= _dist(ax, ay, x1, y1):
            cx, cy = x1, y1
        else:
            cx, cy = x0, y0
    half = region / 2
    return (cx - half, cy - half, cx + half, cy + half)
