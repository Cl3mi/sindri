"""Locate the dimension-value region each balloon points at.

Balloons in this drawing template are drawn in blue (circle + number + a filled
arrowhead) while dimension text is black. We segment the blue, isolate each
balloon as one connected component, read the arrowhead's pointing direction from
the component's farthest point, and return a crop band beyond the arrow tip in
that direction — which is where the (black) dimension value sits.
"""
from dataclasses import dataclass
import numpy as np
import cv2
from PIL import Image

# Blue-balloon colour threshold (R low, G low-mid, B high).
_B_MIN, _R_MAX, _G_MAX = 120, 110, 140
_DILATE_KERNEL = np.ones((5, 5), np.uint8)


@dataclass
class ValueRegion:
    box: tuple        # (x0, y0, x1, y1) image-space crop of the dimension value
    vertical: bool    # True when the arrow runs vertically (text is rotated)


def _blue_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return ((b > _B_MIN) & (r < _R_MAX) & (g < _G_MAX)).astype(np.uint8)


def label_balloons(image: Image.Image, dilate_iters: int = 3) -> np.ndarray:
    """Return a connected-component label map of the blue balloons.

    Dilation merges each balloon's circle, number and arrowhead into one
    component without bridging to neighbouring balloons.
    """
    rgb = np.asarray(image.convert("RGB")).astype(int)
    mask = _blue_mask(rgb)
    dilated = cv2.dilate(mask, _DILATE_KERNEL, iterations=dilate_iters)
    _, labels = cv2.connectedComponents(dilated)
    return labels


def value_region(labels: np.ndarray, anchor_xy, along: int = 230,
                 across: int = 48, pad: int = 8):
    """Crop band beyond the balloon's arrow tip, or None if anchor is off a balloon.

    `along` extends in the arrow direction; `across` is the half-width
    perpendicular to it; `pad` is the gap left after the arrow tip.
    """
    ax, ay = int(anchor_xy[0]), int(anchor_xy[1])
    h, w = labels.shape
    if not (0 <= ay < h and 0 <= ax < w):
        return None
    cid = labels[ay, ax]
    if cid == 0:
        return None
    ys, xs = np.nonzero(labels == cid)
    cx, cy = xs.mean(), ys.mean()
    d2 = (xs - cx) ** 2 + (ys - cy) ** 2
    tip = int(np.argmax(d2))
    tipx, tipy = float(xs[tip]), float(ys[tip])
    dx, dy = tipx - cx, tipy - cy

    if abs(dx) >= abs(dy):                      # horizontal arrow -> horizontal text
        if dx > 0:
            box = (tipx + pad, tipy - across, tipx + pad + along, tipy + across)
        else:
            box = (tipx - pad - along, tipy - across, tipx - pad, tipy + across)
        vertical = False
    else:                                       # vertical arrow -> rotated text
        if dy > 0:
            box = (tipx - across, tipy + pad, tipx + across, tipy + pad + along)
        else:
            box = (tipx - across, tipy - pad - along, tipx + across, tipy - pad)
        vertical = True

    return ValueRegion(box=tuple(int(v) for v in box), vertical=vertical)
