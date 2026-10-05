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


# --- delivered precision: what the client means by "every value true" -------
#
# auto_accept.precision counts MATCHED unflagged rows only. A false detection
# that ships unflagged is a phantom value delivered silently, and the client
# ranks exactly that (precision over recall, 2026-10-05). It belongs in the
# denominator of anything quoted to them as precision.

def _report_ds(counts, n_gold, false_unflagged):
    ds = DocScore(doc_id="D", gold_hash="h", n_gold=n_gold, n_pred=n_gold,
                  counts=counts, false_unflagged=false_unflagged)
    return aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [ds])


def test_delivered_precision_counts_unflagged_phantoms():
    r = _report_ds({"correct": 70, "escaped_error": 17, "false_detection": 340},
                   n_gold=311, false_unflagged=33)
    aa = summarize(r, lambda d: "x")["auto_accept"]
    assert aa["false_unflagged"] == 33
    assert aa["n_delivered"] == 70 + 17 + 33
    assert aa["delivered_precision"] == round(70 / 120, 4)
    # the matched-only precision is unchanged, so no history moves
    assert aa["precision"] == round(70 / 87, 4)


def test_delivered_precision_is_not_measured_on_an_old_report():
    """A report scored before the field existed must say so, never 0 phantoms
    -- that would inflate delivered precision to the matched-only one."""
    r = _report_ds({"correct": 5, "escaped_error": 1}, n_gold=6,
                   false_unflagged=None)
    aa = summarize(r, lambda d: "x")["auto_accept"]
    assert aa["false_unflagged"] is None
    assert aa["delivered_precision"] is None
    assert aa["false_unflagged_not_measured"] == 1


def test_score_doc_counts_only_unflagged_false_detections():
    from app.eval.score import score_doc
    from tests.eval.test_policy_check import _setup
    from app.eval.models import ReviewCostWeights, MatchParams
    dumps, golds = _setup()
    # _setup: pos 3 is a phantom with needs_review=True (empty read)
    s = score_doc(dumps["D"], golds["D"], ReviewCostWeights(), MatchParams())
    assert 3 in s.false_positions
    assert s.false_unflagged == 0
    dumps["D"].result.characteristics[2].needs_review = False
    s = score_doc(dumps["D"], golds["D"], ReviewCostWeights(), MatchParams())
    assert s.false_unflagged == 1
