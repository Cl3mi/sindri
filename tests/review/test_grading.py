"""Per-session grading (C2 design §Grading). The client's review is the truth:
a grade says what the client did to each proposed row, read from the SEALED
revision (rows + net_events), never from events.jsonl."""
from app.review.grading import GRADES, grade_session


def row(id, **kw):
    base = {"id": id, "pos": 1, "char_type": "Distance", "nominal": "10",
            "upper_tol": "+0.1", "lower_tol": "-0.1", "needs_review": False,
            "suggested": False, "balloon_xy": [100.0, 100.0],
            "target_region": [10.0, 10.0, 50.0, 30.0], "source": "auto"}
    return {**base, **kw}


def sealed(rows, events, mismatch=(), reviewed=()):
    # reviewed_ids must list what the events marked reviewed (accept,
    # confirm_suggestions), or Task 3's replay check reads the seal as
    # inconsistent -- the fixture must be a record the real store could write.
    return {"revision": 1, "rows": rows, "net_events": events,
            "reviewed_ids": list(reviewed), "replay_mismatch_ids": list(mismatch)}


def test_every_proposal_row_gets_exactly_one_grade():
    prop = [row("a"), row("b"), row("c"), row("s1", suggested=True, pos=0),
            row("s2", suggested=True, pos=0), row("s3", suggested=True, pos=0),
            row("s4", suggested=True, pos=0)]
    ev = [
        {"seq": 1, "type": "edit_cell", "id": "b", "field": "nominal",
         "old": "10", "new": "12", "reason": "misread_digit"},
        {"seq": 2, "type": "delete_row", "id": "c", "reason": "duplicate"},
        {"seq": 3, "type": "confirm_suggestions", "ids": ["s1", "s2"]},
        {"seq": 4, "type": "edit_cell", "id": "s2", "field": "upper_tol",
         "old": "+0.1", "new": "+0.2"},
        {"seq": 5, "type": "delete_row", "id": "s3"},
    ]
    final = [row("a"), row("b", nominal="12"),
             row("s1", suggested=False), row("s2", suggested=False, upper_tol="+0.2"),
             row("s4", suggested=True, pos=0)]
    g = grade_session(prop, sealed(final, ev, reviewed=["s1", "s2"]))
    by = {r["id"]: r for r in g["rows"]}
    assert g["ungradable"] is None
    assert {k: by[k]["grade"] for k in by} == {
        "a": "confirmed", "b": "edited", "c": "phantom",
        "s1": "suggestion_confirmed", "s2": "suggestion_edited",
        "s3": "suggestion_deleted", "s4": "suggestion_left"}
    assert by["b"]["fields"] == ["nominal"] and by["b"]["reason"] == "misread_digit"
    assert by["c"]["reason"] == "duplicate"
    assert by["s2"]["fields"] == ["upper_tol"]
    assert set(GRADES) >= {r["grade"] for r in g["rows"]}


def test_an_edit_undone_by_a_second_edit_is_confirmed():
    """Net state decides, not activity: 10 -> 12 -> 10 is still 10."""
    ev = [{"seq": 1, "type": "edit_cell", "id": "a", "field": "nominal", "old": "10", "new": "12"},
          {"seq": 2, "type": "edit_cell", "id": "a", "field": "nominal", "old": "12", "new": "10"}]
    g = grade_session([row("a")], sealed([row("a")], ev))
    assert g["rows"][0]["grade"] == "confirmed"


def test_rows_added_by_the_client_are_missed():
    added = row("m1", source="manual", nominal="5")
    ev = [{"seq": 1, "type": "add_row", "row": added}]
    g = grade_session([row("a")], sealed([row("a"), added], ev))
    assert [m["id"] for m in g["missed"]] == ["m1"]


def test_flagged_is_carried_for_flag_precision():
    g = grade_session([row("a", needs_review=True)],
                      sealed([row("a", needs_review=True)], []))
    assert g["rows"][0]["flagged"] is True


def _edit(seq, field, old, new, reason=None):
    e = {"seq": seq, "type": "edit_cell", "id": "a", "field": field,
         "old": old, "new": new}
    if reason:
        e["reason"] = reason
    return e


def test_a_reason_given_on_an_undone_edit_is_not_blamed_on_another_field():
    """nominal 10 -> 12 (misread_digit) -> 10, then upper_tol edited with no
    reason: the tolerance change must not be recorded as a digit misread."""
    ev = [_edit(1, "nominal", "10", "12", "misread_digit"),
          _edit(2, "nominal", "12", "10"),
          _edit(3, "upper_tol", "+0.1", "+0.2")]
    g = grade_session([row("a")], sealed([row("a", upper_tol="+0.2")], ev))
    r = g["rows"][0]
    assert r["grade"] == "edited" and r["fields"] == ["upper_tol"]
    assert r["reason"] is None


def test_a_single_explained_edit_keeps_its_reason():
    ev = [_edit(1, "nominal", "10", "12", "misread_digit")]
    g = grade_session([row("a")], sealed([row("a", nominal="12")], ev))
    assert g["rows"][0]["reason"] == "misread_digit"


def test_with_two_edited_fields_the_last_explained_edit_gives_the_reason():
    ev = [_edit(1, "nominal", "10", "12"),
          _edit(2, "upper_tol", "+0.1", "+0.2", "wrong_tolerance")]
    g = grade_session([row("a")], sealed([row("a", nominal="12", upper_tol="+0.2")], ev))
    assert g["rows"][0]["reason"] == "wrong_tolerance"


def test_suggestion_edit_reason_belongs_to_its_field_too():
    s = row("s", suggested=True, pos=0)
    ev = [{"seq": 1, "type": "edit_cell", "id": "s", "field": "nominal",
           "old": "10", "new": "12", "reason": "misread_digit"},
          {"seq": 2, "type": "edit_cell", "id": "s", "field": "nominal",
           "old": "12", "new": "10"},
          {"seq": 3, "type": "edit_cell", "id": "s", "field": "lower_tol",
           "old": "-0.1", "new": "-0.2"},
          {"seq": 4, "type": "confirm_suggestions", "ids": ["s"]}]
    g = grade_session([s], sealed([row("s", lower_tol="-0.2")], ev, reviewed=["s"]))
    r = g["rows"][0]
    assert r["grade"] == "suggestion_edited" and r["reason"] is None


def test_explicit_review_is_recorded_apart_from_silence():
    ev = [{"seq": 1, "type": "accept", "ids": ["a"]}]
    g = grade_session([row("a"), row("b")],
                      sealed([row("a"), row("b")], ev, reviewed=["a"]))
    by = {r["id"]: r for r in g["rows"]}
    assert by["a"]["reviewed"] is True and by["b"]["reviewed"] is False


from app.review.grading import iou


def test_iou_basics():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert abs(iou([0, 0, 10, 10], [5, 0, 15, 10]) - 1 / 3) < 1e-9
    assert iou(None, [0, 0, 1, 1]) is None


def test_placement_is_measured_against_the_proposal_not_the_events():
    """Events carry only the NEW position (C1 decision 8); the old one is the
    proposal's, so displacement is proposal -> sealed final."""
    ev = [{"seq": 1, "type": "move_row", "id": "a", "xy": [130.0, 140.0]},
          {"seq": 2, "type": "move_target", "id": "a", "box": [10.0, 10.0, 50.0, 30.0]}]
    final = [row("a", balloon_xy=[130.0, 140.0])]
    g = grade_session([row("a")], sealed(final, ev))
    r = g["rows"][0]
    assert r["bubble_px"] == 50.0           # (30, 40) -> 50
    assert r["anchor_iou"] == 1.0 and r["anchor_moved"] is False


def test_a_deleted_row_has_no_placement():
    g = grade_session([row("a")], sealed([], [{"seq": 1, "type": "delete_row", "id": "a"}]))
    assert g["rows"][0]["bubble_px"] is None and g["rows"][0]["anchor_iou"] is None
