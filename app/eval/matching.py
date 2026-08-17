"""Deterministic greedy bipartite matching of predictions to gold records.

cost(pred, gold) = center_distance / page_diagonal
                   - value_bonus   (when the nominals agree via normalize)
Pairs farther than max_geo_frac of the diagonal are forbidden outright — a
value match cannot rescue a geometrically absurd pair. Greedy consumes pairs
in ascending (cost, pred_key, gold_key) order, so output is a pure function of
the inputs (comparability requirement: same inputs -> same matching, always).
"""
import math
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Tuple

from app.eval.models import MatchParams
from app.eval.normalize import canon_value, values_equal


def _value_similarity(a: str, b: str) -> float:
    """1.0 for equal values, a graded score for near-misses ('28' vs '20'), ~0
    for unrelated ones. Near-misses must still pair: a misread is one wrong row,
    not a missed callout plus a phantom."""
    ca, cb = canon_value(a), canon_value(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    try:
        fa, fb = float(ca), float(cb)
    except ValueError:
        return SequenceMatcher(None, ca, cb).ratio()
    # Numeric closeness, not string overlap: '20' vs '28' share one character
    # out of four (0.5 by SequenceMatcher) but are plainly the same dimension
    # misread, whereas '20' vs '137,5' are not.
    denom = max(abs(fa), abs(fb))
    if denom == 0:
        return 1.0
    return max(0.0, 1.0 - abs(fa - fb) / denom)


@dataclass(frozen=True)
class Cand:
    key: int              # pred.pos or gold.balloon
    center_pt: tuple      # PDF points; None when the balloon was not located
    nominal: str = ""


def match_candidates(preds: List[Cand], golds: List[Cand],
                     page_diag_pt: float, params: MatchParams,
                     ) -> List[Tuple[int, int, float]]:
    """Return [(pred_key, gold_key, distance_frac)], one-to-one, sorted by
    pred_key. distance_frac = center distance / page diagonal."""
    if len({c.key for c in preds}) != len(preds):
        raise ValueError("duplicate pred keys — matching would silently drop one")
    if len({c.key for c in golds}) != len(golds):
        raise ValueError("duplicate gold keys — matching would silently drop one")
    scored = []
    if params.mode == "value":
        for p in preds:
            for g in golds:
                sim = _value_similarity(p.nominal, g.nominal)
                if sim < params.value_sim_min:
                    continue
                scored.append((1.0 - sim, p.key, g.key, 0.0))
    elif params.mode == "geometry":
        for p in preds:
            for g in golds:
                if g.center_pt is None or p.center_pt is None:
                    # Hybrid: this characteristic is real gold but its balloon
                    # could not be located, so geometry is unavailable for this
                    # pair only. Fall back to value similarity rather than
                    # letting it read as a guaranteed miss.
                    sim = _value_similarity(p.nominal, g.nominal)
                    if sim < params.value_sim_min:
                        continue
                    scored.append((1.0 - sim, p.key, g.key, 0.0))
                    continue
                d = math.dist(p.center_pt, g.center_pt) / page_diag_pt
                if d > params.max_geo_frac:
                    continue
                cost = d
                if p.nominal and g.nominal and values_equal(p.nominal, g.nominal):
                    cost -= params.value_bonus
                scored.append((cost, p.key, g.key, d))
    else:
        raise ValueError(f"unknown match mode {params.mode!r}")
    scored.sort()
    used_p, used_g, out = set(), set(), []
    for _cost, pk, gk, d in scored:
        if pk in used_p or gk in used_g:
            continue
        used_p.add(pk)
        used_g.add(gk)
        out.append((pk, gk, d))
    return sorted(out)
