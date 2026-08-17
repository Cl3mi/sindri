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


V = MatchParams(mode="value")


def test_value_mode_pairs_equal_values_ignoring_geometry():
    """Fallback for documents whose balloon positions cannot be recovered:
    geometry is unavailable, so values carry the match."""
    preds = [Cand(key=1, center_pt=(0, 0), nominal="20"),
             Cand(key=2, center_pt=(0, 0), nominal="5,5")]
    golds = [Cand(key=7, center_pt=(9999, 9999), nominal="5.5"),
             Cand(key=8, center_pt=(9999, 9999), nominal="20")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, V)
    assert {(p, g) for p, g, _ in pairs} == {(1, 8), (2, 7)}


def test_value_mode_still_pairs_a_near_miss_so_it_scores_as_an_error():
    """A misread must remain ONE matched-but-wrong row. If it failed to pair it
    would count as a miss AND a false detection — charging 12 instead of 5 and
    misrepresenting where the error is."""
    preds = [Cand(key=1, center_pt=(0, 0), nominal="28")]
    golds = [Cand(key=7, center_pt=(0, 0), nominal="20")]
    assert match_candidates(preds, golds, PAGE_DIAG, V) == [(1, 7, 0.0)]


def test_value_mode_refuses_unrelated_values():
    preds = [Cand(key=1, center_pt=(0, 0), nominal="20")]
    golds = [Cand(key=7, center_pt=(0, 0), nominal="137,5")]
    assert match_candidates(preds, golds, PAGE_DIAG, V) == []


def test_value_mode_is_one_to_one_on_repeated_values():
    """Two Ø20 callouts must consume two gold rows, not match both to one."""
    preds = [Cand(key=1, center_pt=(0, 0), nominal="20"),
             Cand(key=2, center_pt=(0, 0), nominal="20")]
    golds = [Cand(key=7, center_pt=(0, 0), nominal="20"),
             Cand(key=8, center_pt=(0, 0), nominal="20")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, V)
    assert sorted(p for p, _, _ in pairs) == [1, 2]
    assert sorted(g for _, g, _ in pairs) == [7, 8]


def test_geometry_mode_falls_back_to_value_for_positionless_gold():
    """Hybrid: gold rows whose balloon was located match on geometry; rows
    without a position still match, on value alone. Otherwise a located
    characteristic and an unlocated one would be scored by different rules —
    or the unlocated one would always read as a miss."""
    preds = [Cand(key=1, center_pt=(100, 100), nominal="20"),
             Cand(key=2, center_pt=(700, 700), nominal="5,5")]
    golds = [Cand(key=7, center_pt=(105, 100), nominal="20"),
             Cand(key=8, center_pt=None, nominal="5.5")]
    pairs = match_candidates(preds, golds, PAGE_DIAG, P)
    assert {(p, g) for p, g, _ in pairs} == {(1, 7), (2, 8)}


def test_positionless_gold_still_refuses_an_unrelated_value():
    preds = [Cand(key=1, center_pt=(100, 100), nominal="20")]
    golds = [Cand(key=8, center_pt=None, nominal="137,5")]
    assert match_candidates(preds, golds, PAGE_DIAG, P) == []


def test_duplicate_keys_rejected_loudly():
    import pytest
    dup = [Cand(key=1, center_pt=(0, 0)), Cand(key=1, center_pt=(9, 9))]
    ok = [Cand(key=7, center_pt=(0, 0))]
    with pytest.raises(ValueError, match="duplicate pred keys"):
        match_candidates(dup, ok, PAGE_DIAG, P)
    with pytest.raises(ValueError, match="duplicate gold keys"):
        match_candidates(ok, dup, PAGE_DIAG, P)
