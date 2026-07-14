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
from typing import List, Tuple

from app.eval.models import MatchParams
from app.eval.normalize import values_equal


@dataclass(frozen=True)
class Cand:
    key: int              # pred.pos or gold.balloon
    center_pt: tuple      # PDF points
    nominal: str = ""


def match_candidates(preds: List[Cand], golds: List[Cand],
                     page_diag_pt: float, params: MatchParams,
                     ) -> List[Tuple[int, int, float]]:
    """Return [(pred_key, gold_key, distance_frac)], one-to-one, sorted by
    pred_key. distance_frac = center distance / page diagonal."""
    scored = []
    for p in preds:
        for g in golds:
            d = math.dist(p.center_pt, g.center_pt) / page_diag_pt
            if d > params.max_geo_frac:
                continue
            cost = d
            if p.nominal and g.nominal and values_equal(p.nominal, g.nominal):
                cost -= params.value_bonus
            scored.append((cost, p.key, g.key, d))
    scored.sort()
    used_p, used_g, out = set(), set(), []
    for _cost, pk, gk, d in scored:
        if pk in used_p or gk in used_g:
            continue
        used_p.add(pk)
        used_g.add(gk)
        out.append((pk, gk, d))
    return sorted(out)
