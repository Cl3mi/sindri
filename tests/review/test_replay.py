"""Replay is the grading's ground truth for what the reviewer did (design §3,
the replay identity). If replaying the net journal on the server-held proposal
does not reproduce the rows the browser exported, the log is missing something
and no grade built on it can be trusted -- so seal records the mismatch."""
import pytest

from app.review.journal import JournalError
from app.review.replay import mismatches, note_of, replay


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


def _row(**kw):
    return {"id": "r1", "char_type": "", "nominal": "1", "upper_tol": "", "lower_tol": "",
            "balloon_xy": [1, 1], "target_region": [0, 0, 10, 10], **kw}


def test_move_target_moves_the_anchor_and_mismatches_compare_it():
    rows = replay([_row()], [{"seq": 1, "type": "move_target", "id": "r1", "box": [2, 2, 12, 12]}])
    assert rows["r1"]["target_region"] == [2, 2, 12, 12]
    assert mismatches(rows, [_row(target_region=[2, 2, 12, 12])], []) == []
    assert mismatches(rows, [_row()], []) == ["r1"]


def test_set_note_changes_no_row_and_the_latest_note_wins():
    events = [{"seq": 1, "type": "set_note", "text": "a"},
              {"seq": 2, "type": "set_note", "text": "b"}]
    assert replay([_row()], events)["r1"]["nominal"] == "1"
    assert note_of(events) == "b"
    assert note_of([]) == ""


def test_note_of_refuses_a_raw_journal_like_replay_does():
    """A retract in the list means the caller skipped net_events(); returning
    the note the reviewer undid would grade an operation that never stood."""
    events = [{"seq": 1, "type": "set_note", "text": "a"},
              {"seq": 2, "type": "retract", "target_seq": 1}]
    with pytest.raises(JournalError):
        note_of(events)


def test_flag_puts_a_row_back_in_the_review_queue_with_a_manual_reason():
    """Move-to-Review (review UX plan task 2). The reviewer's own flag is a
    fact the grader reads: the row needs review again, is no longer accepted,
    and says WHY (`manual`, never one of the pipeline's reasons)."""
    proposal = [row("a"), row("b", needs_review=True, review_reasons=["no_tolerance"]),
                row("s", suggested=True)]
    out = replay(proposal, [ev(1, "accept", ids=["a", "b"]),
                            ev(2, "flag", ids=["a", "b", "s", "ghost"])])
    assert out["a"]["needs_review"] is True and out["a"]["reviewed"] is False
    assert out["a"]["review_reasons"] == ["manual"]
    # An existing reason stays; "manual" is appended once, never duplicated.
    assert out["b"]["review_reasons"] == ["no_tolerance", "manual"]
    # A suggestion is not a balloon: nothing to flag (state.js agrees).
    assert out["s"].get("needs_review") is False and out["s"].get("review_reasons") is None
    again = replay(proposal, [ev(1, "flag", ids=["b"]), ev(2, "flag", ids=["b"])])
    assert again["b"]["review_reasons"] == ["no_tolerance", "manual"]


def test_mismatches_compares_whether_a_row_needs_review():
    """A flag changes needs_review and nothing else, so without this a replay
    that dropped it would still 'reproduce' the exported rows."""
    replayed = replay([row("a")], [ev(1, "flag", ids=["a"])])
    assert mismatches(replayed, [row("a", needs_review=True)], []) == []
    assert mismatches(replayed, [row("a", needs_review=False)], []) == ["a"]
