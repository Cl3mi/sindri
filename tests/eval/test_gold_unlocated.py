"""How many SCORED gold rows had no balloon position at all.

The digest has counted `missed_unlocated` since the taxonomy existed -- unlocated
gold rows that were MISSED -- and never the denominator. So the one question that
decides what to do about them cannot be answered: are unlocated rows a handicap
the value-matching path already absorbs, or dead weight charged at w=10?

It stopped being academic on 2026-09-15. The corpus-wide ingest says 201 of 2489
scored (dimension-bucket) gold rows have no position -- 8.1% -- while dev
measured 3.5% and the test split 10.6%. So dev is the outlier, not test, and ~8%
of the primary metric rests on rows the pipeline was never given a position to
find. `matching.py:68-76` still matches those by value similarity ALONE, so some
of them DO pair; without the denominator nobody can say how many.

CLAUDE.md §4: every aggregate must reconcile against a count that already
exists. This one reconciles two ways -- `n_gold_unlocated <= n_gold`, and
`missed_unlocated <= n_gold_unlocated`.
"""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _pt_box(x, y):
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _gold(*positions):
    """One gold doc; each argument is a position, or None for an unlocated row.

    Nominals are distinct so a value match is unambiguous -- the unlocated path
    matches on value alone, and identical nominals would make the pairing an
    accident rather than the mechanism under test."""
    return GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[
                       GoldCharacteristic(balloon=i + 1, position_pt=p,
                                          char_type="Distance",
                                          nominal=str(10 + i))
                       for i, p in enumerate(positions)])


def _dump(*preds):
    """A dump whose predictions sit at the given (x, y) points, in gold order."""
    return PredictionDump(
        doc_id="D", config=RunConfig(model_id="stub", dpi=300), scale=SCALE,
        page_rect=RECT,
        result=ExtractionResult(characteristics=[
            Characteristic(pos=i + 1, char_type="Distance", nominal=str(10 + i),
                           raw_text=str(10 + i), target_region=_pt_box(x, y))
            for i, (x, y) in enumerate(preds)]))


def _score(gold, dump):
    return score_doc(dump, gold, ReviewCostWeights(), MatchParams())


def test_the_denominator_is_recorded():
    """Two gold rows, one with no position. The count has to be visible whether
    or not that row ends up missed."""
    s = _score(_gold((100.0, 100.0), None), _dump((100.0, 100.0)))
    assert s.n_gold == 2
    assert s.n_gold_unlocated == 1


def test_it_reconciles_against_missed_unlocated():
    """`missed_unlocated` can never exceed it: an unlocated row that was missed
    is one of the unlocated rows."""
    s = _score(_gold((100.0, 100.0), None, None), _dump((100.0, 100.0)))
    assert s.missed_unlocated <= s.n_gold_unlocated
    assert s.n_gold_unlocated <= s.n_gold


def test_an_unlocated_row_that_matched_by_value_is_in_the_denominator():
    """The whole point. matching.py matches an unlocated gold row by value
    similarity ALONE, so it can pair without a position -- and is then absent
    from missed_unlocated while still BEING an unlocated row. Only the
    denominator shows the mechanism worked."""
    s = _score(_gold(None), _dump((300.0, 300.0)))
    assert s.n_gold_unlocated == 1
    assert s.missed_unlocated == 0, "value matching should have paired it"


def test_a_fully_located_document_records_zero():
    """0 means measured-and-none, which is what a document with complete gold
    positions should say -- not the absence of the field."""
    s = _score(_gold((100.0, 100.0)), _dump((100.0, 100.0)))
    assert s.n_gold_unlocated == 0


def test_the_digest_shows_the_denominator_and_what_it_carried():
    """`runner summary` is the only sanctioned view of a run, so a count that
    exists on DocScore and not in the digest cannot settle anything. `carried`
    is the difference -- unlocated rows that paired by value anyway -- and it is
    the number the policy question turns on."""
    from app.eval.report import aggregate, summarize
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(),
                       [_score(_gold((100.0, 100.0), None, None),
                               _dump((100.0, 100.0), (300.0, 300.0)))])
    digest = summarize(report, lambda d: "hashed")
    cov = digest["gold_coverage"]
    assert cov["unlocated_gold"] == 2
    assert cov["not_measured"] == 0
    # It reconciles against a count that already exists, and it lives OUTSIDE
    # missed_diagnosis so that partition still sums to `missed`.
    assert (digest["missed_diagnosis"]["unlocated"] + cov["carried_by_value"]
            == cov["unlocated_gold"])
    assert (sum(digest["missed_diagnosis"].values())
            == digest["taxonomy"]["missed"])
