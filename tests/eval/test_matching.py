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


def _contention_case():
    """Greedy's classic loss. P1 is nearest G1, but P1 is the ONLY prediction G2
    can reach -- P2 is out of gate for G2. Greedy spends P1 on G1 and strands G2;
    a maximum-cardinality assignment pairs both. This is the shape of a "contended" miss:
    a prediction sat inside G2's gate and the matcher gave it to a neighbour."""
    preds = [Cand(key=1, center_pt=(0, 20), nominal=""),
             Cand(key=2, center_pt=(0, -50), nominal="")]
    golds = [Cand(key=1, center_pt=(0, 0), nominal=""),
             Cand(key=2, center_pt=(0, 120), nominal="")]
    return preds, golds


def test_greedy_strands_a_gold_row_whose_only_candidate_is_taken():
    """Documents the defect, so the fix cannot silently regress."""
    preds, golds = _contention_case()
    pairs = match_candidates(preds, golds, PAGE_DIAG, MatchParams())
    assert len(pairs) == 1                       # G2 stranded
    assert {(p, g) for p, g, _ in pairs} == {(1, 1)}


def test_max_cardinality_recovers_the_stranded_row():
    preds, golds = _contention_case()
    pairs = match_candidates(preds, golds, PAGE_DIAG,
                             MatchParams(assignment="max_cardinality"))
    assert len(pairs) == 2                       # both gold rows matched
    assert {(p, g) for p, g, _ in pairs} == {(2, 1), (1, 2)}
    # every pair still inside the gate -- cardinality is not bought by
    # admitting geometrically absurd pairs
    assert all(d <= MatchParams().max_geo_frac for _, _, d in pairs)


def test_greedy_is_still_the_default():
    """Changing the matcher changes every score with no other fingerprint, so it
    has to be opt-in and recorded in MatchParams -- otherwise a reconciled report
    silently outperforms an old one for reasons nothing in the file explains."""
    assert MatchParams().assignment == "greedy"


def test_max_cardinality_never_loses_a_match_greedy_found():
    """On the cases greedy already handles, max_cardinality must match at least as many
    rows -- it is a repair, not a different metric."""
    for preds, golds in (_contention_case(),
                         ([Cand(key=1, center_pt=(100, 100), nominal="")],
                          [Cand(key=7, center_pt=(105, 100), nominal="")])):
        g = match_candidates(preds, golds, PAGE_DIAG, MatchParams())
        o = match_candidates(preds, golds, PAGE_DIAG,
                             MatchParams(assignment="max_cardinality"))
        assert len(o) >= len(g)


def test_max_cardinality_is_deterministic():
    preds, golds = _contention_case()
    runs = [match_candidates(preds, golds, PAGE_DIAG,
                             MatchParams(assignment="max_cardinality")) for _ in range(5)]
    assert all(r == runs[0] for r in runs)


def test_max_cardinality_can_break_a_correct_pair_to_buy_a_match():
    """Why max_cardinality is a diagnostic and not the default.

    P1 and G1 agree on their nominal, so greedy pairs them via value_bonus and
    that pair is RIGHT. Augmenting to satisfy G2 steals P1 and pushes G1 onto P2,
    whose value matches nothing -- two matches where there was one, and zero
    correct where there was one. On the dev split that trade ran 26 misses
    recovered against 27 correct pairings destroyed, taking field accuracy on
    matched rows from 36.4% to 25.4%. Recall goes up; truth goes down."""
    preds = [Cand(key=1, center_pt=(0, 20), nominal="20"),
             Cand(key=2, center_pt=(0, -50), nominal="77")]
    golds = [Cand(key=1, center_pt=(0, 0), nominal="20"),
             Cand(key=2, center_pt=(0, 120), nominal="99")]

    greedy = match_candidates(preds, golds, PAGE_DIAG, MatchParams())
    maxcard = match_candidates(preds, golds, PAGE_DIAG,
                               MatchParams(assignment="max_cardinality"))

    # greedy: one pair, and it is the value-agreeing one
    assert {(p, g) for p, g, _ in greedy} == {(1, 1)}
    # max_cardinality: two pairs, and the correct one is gone
    assert len(maxcard) == 2
    assert (1, 1) not in {(p, g) for p, g, _ in maxcard}
