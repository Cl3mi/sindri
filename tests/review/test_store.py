"""The durable review record (design §2). Every refusal here exists so that an
incomplete or foreign log is never graded as if it were whole."""
import json
import threading

import pytest

from app.review.store import (
    JournalGap, JournalInvalid, ReviewStore, StaleWriter, UnknownSession)

SID = "a" * 32


def row(id_, **kw):
    return {"id": id_, "char_type": "Distance", "nominal": "10", "upper_tol": "",
            "lower_tol": "", "balloon_xy": [5.0, 5.0], "suggested": False,
            "needs_review": True, **kw}


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, **kw}


@pytest.fixture
def store(tmp_path):
    return ReviewStore(tmp_path / "reviews")


@pytest.fixture
def writer(store):
    return store.create(SID, {"h": 1}, {"rows": [row("a"), row("b")]})


def test_create_writes_header_and_proposal_and_returns_a_token(store, writer):
    d = store.root / SID
    assert json.loads((d / "header.json").read_text()) == {"h": 1}
    assert [r["id"] for r in json.loads((d / "proposal.json").read_text())["rows"]] == ["a", "b"]
    assert len(writer) >= 32


def test_append_is_idempotent_and_reports_the_contiguous_seq(store, writer):
    assert store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(3, "delete_row", id="b")]) == 1
    # A resend of seq 1 is ignored; seq 2 closes the gap.
    assert store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"])]) == 3
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert sorted(json.loads(l)["seq"] for l in lines) == [1, 2, 3]


def test_append_refuses_a_stale_writer(store, writer):
    with pytest.raises(StaleWriter):
        store.append(SID, "not-the-token", [ev(1, "accept", ids=["a"])])


def test_append_refuses_an_unknown_session(store):
    with pytest.raises(UnknownSession):
        store.append("b" * 32, "t", [])


def test_append_refuses_a_malformed_event_and_writes_nothing(store, writer):
    with pytest.raises(JournalInvalid):
        store.append(SID, writer, [ev(1, "accept", ids=["a"]), {"seq": 2, "type": "teleport"}])
    assert not (store.root / SID / "events.jsonl").exists()


def test_seal_refuses_a_gap(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(3, "delete_row", id="b")])
    with pytest.raises(JournalGap):
        store.seal(SID, writer, 3, [row("a")], ["a"])


def test_seal_refuses_events_beyond_the_final_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "delete_row", id="b")])
    with pytest.raises(JournalInvalid):
        store.seal(SID, writer, 1, [row("a"), row("b")], ["a"])


def test_seal_writes_a_revision_compacts_the_journal_and_checks_replay(store, writer):
    store.append(SID, writer, [
        ev(1, "accept", ids=["a"]),
        ev(2, "delete_row", id="b"),
        ev(3, "retract", target=2),        # the reviewer undid the delete
    ])
    out = store.seal(SID, writer, 3, [row("a"), row("b")], ["a"])
    assert out == {"revision": 1, "replay_ok": True}
    sealed = json.loads((store.root / SID / "sealed" / "r1.json").read_text())
    assert sealed["final_seq"] == 3
    assert [e["seq"] for e in sealed["net_events"]] == [1]
    assert sealed["replay_mismatch_ids"] == []
    assert sealed["reviewed_ids"] == ["a"]
    # Net operations only are retained (operator decision): the raw journal
    # is rewritten to its net form.
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert [json.loads(l)["seq"] for l in lines] == [1]


def test_seal_records_a_replay_mismatch_instead_of_hiding_it(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    out = store.seal(SID, writer, 1, [row("a", nominal="99"), row("b")], ["a"])
    assert out == {"revision": 1, "replay_ok": False}
    sealed = json.loads((store.root / SID / "sealed" / "r1.json").read_text())
    assert sealed["replay_mismatch_ids"] == ["a"]


def test_a_second_seal_continues_after_compaction_holes(store, writer):
    store.append(SID, writer, [ev(1, "delete_row", id="b"), ev(2, "retract", target=1)])
    store.seal(SID, writer, 2, [row("a"), row("b")], [])
    # seqs 1-2 are gone after compaction; seq 3 must still count as contiguous.
    assert store.append(SID, writer, [ev(3, "accept", ids=["a"])]) == 3
    assert store.seal(SID, writer, 3, [row("a"), row("b")], ["a"])["revision"] == 2


def test_create_again_before_any_seal_starts_over(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    w2 = store.create(SID, {"h": 2}, {"rows": [row("a")]})
    assert w2 != writer
    assert not (store.root / SID / "events.jsonl").exists()


def test_create_refuses_to_overwrite_a_sealed_session(store, writer):
    store.seal(SID, writer, 0, [row("a"), row("b")], [])
    with pytest.raises(JournalInvalid):
        store.create(SID, {"h": 2}, {"rows": []})


def test_append_tolerates_a_torn_trailing_line(store, writer):
    # A crash mid-append can only tear the LAST line, and that batch was
    # never acknowledged to the client, so it resends exactly this event.
    d = store.root / SID
    (d / "events.jsonl").write_text(
        json.dumps(ev(1, "accept", ids=["a"])) + "\n"
        + json.dumps(ev(2, "unaccept", ids=["a"])) + "\n"
        + '{"seq": 3, "type": "delete_row", "id": "b"'  # no closing brace, no newline
    )
    assert store.append(SID, writer, [ev(3, "delete_row", id="b")]) == 3
    assert store.seal(SID, writer, 3, [row("a")], [])["replay_ok"] is True


def test_a_malformed_middle_line_is_corruption_not_a_crash(store, writer):
    # Only the LAST line is forgiven; a broken line buried earlier in the
    # file was never the tail of an in-flight write and means the log
    # cannot be trusted.
    d = store.root / SID
    (d / "events.jsonl").write_text(
        json.dumps(ev(1, "accept", ids=["a"])) + "\n"
        + "not json at all\n"
        + json.dumps(ev(3, "delete_row", id="b")) + "\n"
    )
    with pytest.raises(JournalInvalid):
        store.append(SID, writer, [])


def test_concurrent_appends_of_the_same_batch_land_exactly_once(store, writer):
    # FastAPI serves sync endpoints on a thread pool, and the UI's pagehide
    # beacon can race a normal flush for the same session; without a lock,
    # two threads can both read the same "have" set and both write the same
    # seq, duplicating a line (and, for a retract, corrupting net_events).
    events = [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"]),
              ev(3, "delete_row", id="b")]
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(store.append(SID, writer, events))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [3, 3]
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert sorted(json.loads(l)["seq"] for l in lines) == [1, 2, 3]
    assert store.seal(SID, writer, 3, [row("a")], ["a"])["revision"] == 1
