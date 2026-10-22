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
