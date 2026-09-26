"""auto_accept: the precision of the rows the product leaves UNFLAGGED.

It exists because review cost alone is gameable in the flagging direction:
flag=1 and escaped=5 mean that flagging every matched row cost 223 against
406 on dev (2026-09-25) while telling the reviewer nothing. A policy change
that lowers cost by flagging more must also make the unflagged set MORE
trustworthy, and this is the number that says whether it did."""
from app.eval.models import DocScore, MatchParams, ReviewCostWeights, RunConfig
from app.eval.report import aggregate, summarize


def _report(counts, n_gold):
    ds = DocScore(doc_id="D", gold_hash="h", n_gold=n_gold, n_pred=n_gold,
                  counts=counts)
    return aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [ds])


def test_auto_accept_is_correct_over_unflagged_matched_rows():
    r = _report({"correct": 73, "escaped_error": 64, "flagged_correct": 45,
                 "flagged_error": 41, "missed": 88}, n_gold=311)
    aa = summarize(r, lambda d: "x")["auto_accept"]
    assert aa["n_auto"] == 137
    assert aa["precision"] == round(73 / 137, 4)
    assert aa["rate"] == round(73 / 311, 4)


def test_flag_everything_has_no_auto_accept_precision_not_a_perfect_one():
    """Zero unflagged rows is 'nothing automated', never precision 1.0."""
    r = _report({"flagged_correct": 10, "flagged_error": 5}, n_gold=15)
    aa = summarize(r, lambda d: "x")["auto_accept"]
    assert aa["n_auto"] == 0
    assert aa["precision"] is None
    assert aa["rate"] == 0.0


def test_a_derived_report_says_so_in_its_digest():
    """A --reapply-policy report is DERIVED from older dumps; its digest is
    committed to docs/eval next to measured ones, so the marker must survive
    summarize or a derived number reads as a measured run."""
    r = _report({"correct": 1}, n_gold=1)
    assert summarize(r, lambda d: "x")["reapplied_policy"] is None
    r.reapplied_policy = {"flag_rules": ["nondim_kind"], "drop_rules": [],
                          "reparsed": True}
    assert summarize(r, lambda d: "x")["reapplied_policy"]["reparsed"] is True
