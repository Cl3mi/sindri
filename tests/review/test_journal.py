"""The review journal's pure rules (docs/plans/2026-10-07-hitl-review-grading-design.md §2).

The journal is what grading replays, so its invariants are the grading's:
an event is numbered, typed and self-contained; an undo is a retraction of
the event it undid; and only the NET operations survive a seal."""
import pytest

from app.review.journal import (
    EVENT_TYPES, JournalError, contiguous_seq, net_events, validate_event)


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, **kw}


def test_vocabulary_is_exactly_phase_one_operations():
    assert EVENT_TYPES == {
        "add_row", "delete_row", "move_row", "edit_cell",
        "accept", "unaccept", "confirm_suggestions", "retract"}


@pytest.mark.parametrize("bad", [
    {"type": "accept", "ids": []},                    # no seq
    {"seq": 0, "type": "accept", "ids": []},          # seq starts at 1
    {"seq": "3", "type": "accept", "ids": []},        # seq must be an int
    {"seq": True, "type": "accept", "ids": []},       # bool is not a seq
    {"seq": 1, "type": "teleport"},                   # unknown type
    "not a dict",
    {"seq": 2, "type": "retract"},                    # no target
    {"seq": 2, "type": "retract", "target": "1"},     # target must be an int
    {"seq": 2, "type": "retract", "target": True},    # bool is not a target
    {"seq": 2, "type": "retract", "target": 2},       # target equal to own seq
    {"seq": 2, "type": "retract", "target": 3},       # target above own seq
])
def test_validate_event_rejects_malformed(bad):
    with pytest.raises(JournalError):
        validate_event(bad)


def test_validate_event_accepts_a_wellformed_event():
    validate_event(ev(1, "edit_cell", id="a", field="nominal", old="1", new="2"))


def test_validate_event_accepts_a_wellformed_retract():
    validate_event(ev(2, "retract", target=1))


@pytest.mark.parametrize("bad", [
    {"seq": 1, "type": "edit_cell", "id": "r1", "new": "2"},                  # missing field
    {"seq": 1, "type": "edit_cell", "field": "nominal", "old": "1", "new": "2"},  # missing id
    {"seq": 1, "type": "edit_cell", "id": 5, "field": "nominal", "old": "1", "new": "2"},  # id not str
    # a cell edit must never rewrite review state through a forbidden field
    {"seq": 1, "type": "edit_cell", "id": "r1", "field": "reviewed", "old": "", "new": "yes"},
    {"seq": 1, "type": "edit_cell", "id": "r1", "field": "suggested", "old": "", "new": "yes"},
    {"seq": 1, "type": "edit_cell", "id": "r1", "field": "id", "old": "r1", "new": "r2"},
    {"seq": 1, "type": "edit_cell", "id": "r1", "field": "nominal", "old": 1, "new": "2"},  # old not str
    {"seq": 1, "type": "edit_cell", "id": "r1", "field": "nominal", "old": "1", "new": None},  # new not str
    {"seq": 1, "type": "accept", "ids": 5},                        # ids not a list
    {"seq": 1, "type": "unaccept", "ids": ["a", 5]},                # element not str
    {"seq": 1, "type": "confirm_suggestions", "ids": "a"},          # ids not a list
    {"seq": 1, "type": "add_row"},                                  # no row
    {"seq": 1, "type": "add_row", "row": "not a dict"},             # row not a dict
    {"seq": 1, "type": "add_row", "row": {}},                       # row missing id
    {"seq": 1, "type": "add_row", "row": {"id": 5}},                # row id not str
    {"seq": 1, "type": "move_row", "xy": [1, 2]},                   # missing id
    {"seq": 1, "type": "move_row", "id": "r1", "xy": None},         # xy missing/None
    {"seq": 1, "type": "move_row", "id": "r1", "xy": [1, 2, 3]},    # xy wrong length
    {"seq": 1, "type": "move_row", "id": "r1", "xy": [1, "2"]},     # xy element not a number
    {"seq": 1, "type": "move_row", "id": "r1", "xy": [1, True]},    # bool is not a number
    {"seq": 1, "type": "delete_row"},                               # no id
    {"seq": 1, "type": "delete_row", "id": 5},                      # id not str
])
def test_validate_event_rejects_malformed_fields_per_type(bad):
    with pytest.raises(JournalError):
        validate_event(bad)


def test_validate_event_accepts_wellformed_events_of_every_type():
    validate_event(ev(1, "edit_cell", id="r1", field="nominal", old="1", new="2"))
    validate_event(ev(1, "move_row", id="r1", xy=[1, 2.5]))
    validate_event(ev(1, "delete_row", id="r1"))
    validate_event(ev(1, "add_row", row={"id": "r1"}))
    validate_event(ev(1, "accept", ids=["a", "b"]))
    validate_event(ev(1, "unaccept", ids=[]))
    validate_event(ev(1, "confirm_suggestions", ids=["a"]))


def test_contiguous_seq_stops_at_the_first_gap():
    assert contiguous_seq({1, 2, 3, 5}) == 3
    assert contiguous_seq(set()) == 0
    # A seal compacts retracted pairs away, leaving holes BELOW the sealed
    # point; counting must resume from there or the next seal sees a gap.
    assert contiguous_seq({7, 8}, start=6) == 8
    assert contiguous_seq({8}, start=6) == 6


def test_net_events_removes_each_retraction_and_its_target():
    events = [ev(1, "accept", ids=["a"]), ev(2, "edit_cell", id="a", field="nominal", old="1", new="2"),
              ev(3, "retract", target=2), ev(4, "delete_row", id="b")]
    assert [e["seq"] for e in net_events(events)] == [1, 4]


def test_net_events_orders_by_seq_whatever_the_arrival_order():
    events = [ev(2, "delete_row", id="b"), ev(1, "accept", ids=["a"])]
    assert [e["seq"] for e in net_events(events)] == [1, 2]


def test_redo_after_undo_survives_as_a_fresh_event():
    events = [ev(1, "delete_row", id="b"), ev(2, "retract", target=1),
              ev(3, "delete_row", id="b")]
    assert [e["seq"] for e in net_events(events)] == [3]


@pytest.mark.parametrize("events", [
    [ev(1, "retract", target=9)],                                       # unknown target
    [ev(1, "accept", ids=["a"]), ev(2, "retract", target=1),
     ev(3, "retract", target=1)],                                       # retracted twice
])
def test_net_events_refuses_an_inconsistent_journal(events):
    with pytest.raises(JournalError):
        net_events(events)
