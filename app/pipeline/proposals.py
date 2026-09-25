"""OCR line proposals for callouts the VLM detector never returned.

58 of 88 dev misses are ISOLATED -- nothing was detected anywhere near the
gold balloon -- and in eight arms only the detector's WEIGHTS ever moved them
(Rung 3, 75 -> 43, at +324 false detections). This is the other route: a
cheap, independent text detector proposes dimension-shaped lines the VLM did
not box, the VLM is asked only "is this one callout?", and the survivors enter
the normal read loop FLAGGED, so a recovered row costs 1 instead of a miss's
10 and a wrong proposal costs a false detection's 2. Break-even precision is
therefore 2 / (2 + 9) = 18%.

Off unless SINDRI_PROPOSALS=ocr; recorded in detect.active_knobs, and the
verify prompt joins the prompt hash only when on, so a control run's
RunConfig is byte-identical to every dump taken before this module existed."""
import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_MODES = {"ocr"}
PROPOSAL_REASON = "ocr proposal"
_DIGIT = re.compile(r"\d")
_MIN_CONF = 50.0          # tesseract word confidence, 0-100
_MAX_CHARS = 24           # a callout, not a sentence
_MAX_DIGIT_RUN = 6        # longer runs are part / drawing numbers
_MIN_H, _MAX_H = 12, 120  # render px; outside this it is not a callout line
_TESS_CONFIG = "--psm 11"  # sparse text: find text anywhere, no layout


def resolve_proposals(env: Optional[Dict] = None) -> Optional[str]:
    """None when off. A value outside _MODES RAISES: a typo must lose the arm,
    not run a control under the arm's name."""
    v = (os.environ if env is None else env).get("SINDRI_PROPOSALS", "")
    v = v.strip().lower()
    if not v:
        return None
    if v not in _MODES:
        raise ValueError(f"SINDRI_PROPOSALS={v!r} (expected one of {_MODES})")
    return v


def _unrotate_box(box, orig_w: int) -> Tuple[int, int, int, int]:
    """Box in the rotate(90, expand=True) image -> box in the original.
    Forward: (x, y) -> (y, W - x); so rotated (xr, yr) -> (W - yr, xr)."""
    xr0, yr0, xr1, yr1 = box
    return (orig_w - yr1, xr0, orig_w - yr0, xr1)


def _dimension_lines(data) -> List[Tuple[Tuple[int, int, int, int], str]]:
    """Group tesseract words into lines; keep dimension-shaped ones."""
    lines: Dict[Tuple[int, int, int], List[int]] = {}
    for i, text in enumerate(data["text"]):
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if not str(text).strip() or conf < _MIN_CONF:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(i)
    out = []
    for key in sorted(lines):
        idx = lines[key]
        text = " ".join(str(data["text"][i]).strip() for i in idx)
        if not _DIGIT.search(text) or len(text) > _MAX_CHARS:
            continue
        if max(len(m) for m in re.findall(r"\d+", text)) > _MAX_DIGIT_RUN:
            continue
        x0 = min(data["left"][i] for i in idx)
        y0 = min(data["top"][i] for i in idx)
        x1 = max(data["left"][i] + data["width"][i] for i in idx)
        y1 = max(data["top"][i] + data["height"][i] for i in idx)
        # The SHORT side is the text line height whatever the orientation.
        h = min(y1 - y0, x1 - x0)
        if not (_MIN_H <= h <= _MAX_H):
            continue
        out.append(((x0, y0, x1, y1), text))
    return out


def _not_covered(cands, existing, margin: int = 8):
    """Candidates that touch no existing detection box grown by `margin`."""
    def hit(a, b):
        return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                    or a[3] < b[1] - margin or a[1] > b[3] + margin)
    return [c for c in cands if not any(hit(c, e) for e in existing)]


def ocr_proposals(image, existing: Sequence[tuple]) -> List[tuple]:
    """Dimension-shaped OCR lines, horizontal and vertical, not already
    covered by a detection. Boxes in the image's pixel space."""
    import pytesseract
    from app.pipeline.detect import Detection, dedupe
    w, _ = image.size
    boxes = []
    for rotated in (False, True):
        im = image.rotate(90, expand=True) if rotated else image
        data = pytesseract.image_to_data(
            im, lang="deu+eng", config=_TESS_CONFIG,
            output_type=pytesseract.Output.DICT)
        for box, _text in _dimension_lines(data):
            boxes.append(_unrotate_box(box, w) if rotated else box)
    # The two passes can find the same line; dedupe as detections do.
    uniq = dedupe([Detection(box=b, kind="dimension", conf=0.0)
                   for b in boxes])
    return _not_covered([d.box for d in uniq], existing)


def verified_proposals(image, detections, backend,
                       crop_fn: Callable) -> list:
    """Proposals the VLM confirms as ONE callout, as Detections tagged
    source="proposal". Refuses a backend without verify_callout rather than
    skipping: a silent skip would run a control under the arm's name."""
    from app.pipeline.detect import Detection
    if not hasattr(backend, "verify_callout"):
        raise RuntimeError("SINDRI_PROPOSALS needs a backend with "
                           "verify_callout (the transformers VLM path)")
    out = []
    for box in ocr_proposals(image, [d.box for d in detections]):
        if backend.verify_callout(crop_fn(box)):
            out.append(Detection(box=box, kind="dimension", conf=0.0,
                                 source="proposal"))
    return out
