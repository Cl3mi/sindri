from app.eval.matching import Cand, match_candidates
from app.eval.models import MatchParams

PAGE_DIAG = 1000.0
P = MatchParams()   # max_geo_frac=0.10, value_bonus=0.35


def test_matches_nearest_when_values_unavailable():
    preds = [Cand(key=1, center_pt=(100, 100), nominal=""),
             Cand(key=2, center_pt=(500, 500), nominal="")]
    golds = [Cand(key=7, center_pt=(105, 100), nominal=""),
             Cand(key=8, center_pt=(510, 505), nominal="")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, P)
    assert {(p, g) for p, g, _ in pairs} == {(1, 7), (2, 8)}


def test_geometry_gate_blocks_distant_pairs():
    preds = [Cand(key=1, center_pt=(100, 100), nominal="20")]
    golds = [Cand(key=7, center_pt=(400, 400), nominal="20")]   # 42% of diag
    assert match_candidates(preds, golds, PAGE_DIAG, P) == []


def test_value_agreement_breaks_geometric_ambiguity():
    # two golds equidistant-ish from two preds; nominals disambiguate
    preds = [Cand(key=1, center_pt=(100, 100), nominal="20"),
             Cand(key=2, center_pt=(110, 100), nominal="5,5")]
    golds = [Cand(key=7, center_pt=(105, 108), nominal="5.5"),
             Cand(key=8, center_pt=(105, 92), nominal="20")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, P)
    assert {(p, g) for p, g, _ in pairs} == {(1, 8), (2, 7)}


def test_one_to_one_and_deterministic():
    preds = [Cand(key=1, center_pt=(100, 100), nominal=""),
             Cand(key=2, center_pt=(100, 100), nominal="")]   # identical preds
    golds = [Cand(key=7, center_pt=(100, 100), nominal="")]
    for _ in range(5):
        pairs = match_candidates(preds, golds, PAGE_DIAG, P)
        assert pairs == [(1, 7, 0.0)]          # lower key wins the tie, always
