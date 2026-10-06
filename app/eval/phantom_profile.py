"""`score --phantom-profile`: the values the product DELIVERS, by outcome and by
gold-free signal.

Delivered = every prediction the pipeline leaves unflagged. Each is correct
(paired, fields right), escaped (paired, fields wrong) or a phantom (paired with
no gold row). The client ranks precision over recall at any recall
(2026-10-06), and delivered precision on dev is 0.419 -- 80 of its 167 values
are phantoms that review cost cannot see. A gold-free drop rule raises
delivered precision exactly when the rows it removes are more untrue than the
set they come from, so each bucket here reports what dropping it would remove.

Measurement only, and for TRAIN only: profiling dev or test before a rule is
registered would make them selection data
(docs/plans/2026-10-06-phantom-arm-design.md). Counts and slice labels only --
never a value, so the JSON may live in docs/eval/."""
import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from app.eval.normalize import canon_value
from app.eval.score import _center_pt

OUTCOMES = ("correct", "escaped", "phantom")


@dataclass(frozen=True)
class Row:
    c: object                 # the delivered Characteristic
    fx: float                 # centre as a fraction of page width
    fy: float                 # ... and of page height
    nearest: Optional[float]  # to the nearest other prediction, / page diagonal
    repeated: bool            # another prediction carries the same nominal


def _band(value, edges: Sequence[float], labels: Sequence[str]) -> str:
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def confidence_band(v) -> str:
    return _band(v, (0.9, 0.95, 0.98, 0.99),
                 ("<0.9", "0.9-0.95", "0.95-0.98", "0.98-0.99", ">=0.99"))


def height_band(px) -> str:
    # The crop-height buckets the digest already uses, so the two read together.
    return _band(px, (28, 40, 80, 120, 200),
                 ("<28", "28-40", "40-80", "80-120", "120-200", ">=200"))


def width_band(px) -> str:
    return _band(px, (60, 120, 240), ("<60", "60-120", "120-240", ">=240"))


def aspect_band(ratio) -> str:
    return _band(ratio, (1, 2, 4), ("<1", "1-2", "2-4", ">=4"))


def digits_band(nominal) -> str:
    n = sum(ch.isdigit() for ch in (nominal or ""))
    return str(n) if n < 4 else "4+"


def raw_length_band(raw) -> str:
    return _band(len((raw or "").strip()), (4, 8, 16),
                 ("<4", "4-7", "8-15", ">=16"))


def nearest_band(frac) -> str:
    if frac is None:
        return "alone"
    return _band(frac, (0.01, 0.03, 0.1), ("<0.01", "0.01-0.03", "0.03-0.1",
                                           ">=0.1"))


def page_position(fx: float, fy: float) -> str:
    # The bottom-right corner is where the title block sits on these drawings,
    # and text there is title-block text, not a characteristic. Checked first,
    # so a title block that touches the border is not counted as "edge".
    if fx > 0.6 and fy > 0.75:
        return "title_corner"
    if fx < 0.05 or fx > 0.95 or fy < 0.05 or fy > 0.95:
        return "edge"
    return "interior"


def _h(c) -> float:
    return c.target_region[3] - c.target_region[1]


def _w(c) -> float:
    return c.target_region[2] - c.target_region[0]


FEATURES: Dict[str, Callable[[Row], Optional[str]]] = {
    "kind": lambda r: r.c.kind or "unset",
    "confidence": lambda r: confidence_band(r.c.confidence),
    "box_height": lambda r: height_band(_h(r.c)),
    "box_width": lambda r: width_band(_w(r.c)),
    "aspect": lambda r: aspect_band(_w(r.c) / _h(r.c) if _h(r.c) else math.inf),
    "char_type": lambda r: r.c.char_type or "none",
    "nominal_digits": lambda r: digits_band(r.c.nominal),
    "nominal_decimal": lambda r: ("yes" if any(s in (r.c.nominal or "")
                                               for s in ",.") else "no"),
    "raw_length": lambda r: raw_length_band(r.c.raw_text),
    "nearest_other": lambda r: nearest_band(r.nearest),
    "page_position": lambda r: page_position(r.fx, r.fy),
    "repeated_nominal": lambda r: "yes" if r.repeated else "no",
}


def _rows(dump, score) -> List[tuple]:
    """(outcome, Row) for every delivered prediction of one document."""
    preds = [c for c in dump.result.characteristics
             if c.target_region is not None]
    x0, y0, x1, y1 = dump.page_rect
    diag = math.dist((x0, y0), (x1, y1))
    centres = {c.pos: _center_pt(c, dump) for c in preds}
    nominals: Dict[str, int] = {}
    for c in preds:
        key = canon_value(c.nominal)
        if key:
            nominals[key] = nominals.get(key, 0) + 1

    outcome = {p.pred_pos: {"correct": "correct",
                            "escaped_error": "escaped"}.get(p.taxonomy)
               for p in score.pairs}
    for pos in score.false_positions:
        outcome[pos] = "phantom"

    out = []
    for c in preds:
        o = outcome.get(c.pos)
        if o is None or c.needs_review:
            continue            # flagged, or not delivered at all
        cx, cy = centres[c.pos]
        others = [math.dist((cx, cy), centres[p]) for p in centres if p != c.pos]
        key = canon_value(c.nominal)
        out.append((o, Row(c=c, fx=(cx - x0) / (x1 - x0),
                           fy=(cy - y0) / (y1 - y0),
                           nearest=min(others) / diag if others else None,
                           repeated=bool(key) and nominals[key] > 1)))
    return out


def _cell() -> Dict:
    return {"correct": 0, "escaped": 0, "phantom": 0}


def phantom_profile(dumps: Dict, scores: Sequence, doc_ids: Sequence[str]) -> Dict:
    rows = [r for doc_id, s in zip(doc_ids, scores)
            for r in _rows(dumps[doc_id], s)]
    totals = _cell()
    for o, _ in rows:
        totals[o] += 1
    delivered = sum(totals.values())

    features: Dict[str, Dict[str, Dict]] = {}
    reconciles = True
    for name, fn in FEATURES.items():
        buckets: Dict[str, Dict] = {}
        for o, row in rows:
            label = fn(row)
            if label is None:
                continue        # a lost row -- the identity below catches it
            buckets.setdefault(label, _cell())[o] += 1
        for b in buckets.values():
            b["n"] = sum(b[o] for o in OUTCOMES)
            b["untrue_share"] = round((b["escaped"] + b["phantom"]) / b["n"], 4)
        for o in OUTCOMES:
            if sum(b[o] for b in buckets.values()) != totals[o]:
                reconciles = False
        features[name] = dict(sorted(buckets.items()))

    return {
        "n_docs": len(doc_ids),
        "totals": {**totals, "delivered": delivered,
                   "delivered_precision": (round(totals["correct"] / delivered, 4)
                                           if delivered else None),
                   "untrue_share": (round((delivered - totals["correct"])
                                          / delivered, 4) if delivered else None)},
        "features": features,
        "reconciles": reconciles,
    }
