"""Replay is the grading's ground truth for what the reviewer did (design §3,
the replay identity). If replaying the net journal on the server-held proposal
does not reproduce the rows the browser exported, the log is missing something
and no grade built on it can be trusted -- so seal records the mismatch."""
import pytest

from app.review.journal import JournalError
from app.review.replay import mismatches, replay


def row(id_, **kw):
    base = {"id": id_, "char_type": "Distance", "nominal": "10", "upper_tol": "",
            "lower_tol": "", "balloon_xy": [5.0, 5.0], "suggested": False,
            "needs_review": False}
    return {**base, **kw}


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, **kw}


def test_replay_applies_every_phase_one_operation():
    proposal = [row("a"), row("b"), row("s", suggested=True)]
    events = [
        ev(1, "edit_cell", id="a", field="nominal", old="10", new="12"),
        ev(2, "move_row", id="a", xy=[7, 8]),
        ev(3, "delete_row", id="b"),
        ev(4, "add_row", row=row("m", source="manual", nominal="3")),
        ev(5, "confirm_suggestions", ids=["s"]),
        ev(6, "accept", ids=["a", "m"]),
        ev(7, "unaccept", ids=["m"]),
    ]
    out = replay(proposal, events)
    assert set(out) == {"a", "s", "m"}
    assert out["a"]["nominal"] == "12" and out["a"]["balloon_xy"] == [7, 8]
    assert out["a"]["reviewed"] is True and out["m"]["reviewed"] is False
    assert out["s"]["suggested"] is False and out["s"]["reviewed"] is True


def test_confirm_ignores_rows_that_are_not_suggestions():
    # Mirrors opConfirmSuggestions in state.js: only a suggestion is confirmed.
    out = replay([row("a")], [ev(1, "confirm_suggestions", ids=["a"])])
    assert out["a"]["reviewed"] is False


def test_replay_refuses_a_retraction_it_was_not_given_net():
    with pytest.raises(JournalError):
        replay([row("a")], [ev(1, "retract", target=1)])


def test_mismatches_is_empty_when_the_final_matches_the_replay():
    replayed = replay([row("a")], [ev(1, "accept", ids=["a"])])
    assert mismatches(replayed, [row("a", balloon_xy=(5.0, 5.0))], ["a"]) == []


def test_mismatches_names_every_kind_of_disagreement():
    replayed = replay([row("a"), row("b"), row("c"), row("d")], [])
    final = [row("a", nominal="99"),          # value differs
             row("b", balloon_xy=[1, 1]),     # position differs
             row("c"),                        # reviewed state differs (below)
             row("x")]                        # row the journal never added; d missing
    assert mismatches(replayed, final, ["c"]) == ["a", "b", "c", "d", "x"]
