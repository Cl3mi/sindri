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
    # An untouched, unsealed session (e.g. a retried extraction before any
    # review happened) may be replaced outright.
    w2 = store.create(SID, {"h": 2}, {"rows": [row("a")]})
    assert w2 != writer
    assert not (store.root / SID / "events.jsonl").exists()


def test_create_refuses_to_wipe_a_session_with_events(store, writer):
    # Once the reviewer has acted, a re-extraction must not silently erase
    # their work -- only a brand new session id may replace it.
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    with pytest.raises(JournalInvalid):
        store.create(SID, {"h": 2}, {"rows": [row("a")]})


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


def test_concurrent_appends_of_the_same_batch_land_exactly_once(store):
    # FastAPI serves sync endpoints on a thread pool, and the UI's pagehide
    # beacon can race a normal flush for the same session; without a lock,
    # two threads can both read the same "have" set and both write the same
    # seq, duplicating a line (and, for a retract, corrupting net_events).
    # The race is probabilistic -- it passed about half the time on a single
    # try during development -- so it is looped over many fresh sessions:
    # a missing lock then fails near-certainly instead of flaking.
    events = [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"]),
              ev(3, "delete_row", id="b")]
    for i in range(25):
        sid = format(i, "032x")
        w = store.create(sid, {"h": i}, {"rows": [row("a"), row("b")]})
        barrier = threading.Barrier(2)
        results = []

        def go():
            barrier.wait()
            results.append(store.append(sid, w, events))

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results == [3, 3]
        lines = [l for l in
                 (store.root / sid / "events.jsonl").read_text(encoding="utf-8").split("\n")
                 if l.strip()]
        assert sorted(json.loads(l)["seq"] for l in lines) == [1, 2, 3]
        assert store.seal(sid, w, 3, [row("a")], ["a"])["revision"] == 1


def test_append_survives_a_line_separator_character_in_a_value(store, writer):
    # str.splitlines() also breaks on U+2028/U+2029/U+0085 and others, which
    # an edit_cell's free-text value can legitimately contain. A single
    # valid JSON line holding one must not be sliced into fragments that
    # then fail to parse as a corrupt middle or last line.
    store.append(SID, writer, [
        ev(1, "edit_cell", id="a", field="nominal", old="x", new="1\u20280"),
        ev(2, "accept", ids=["a"]),
    ])
    assert store.append(SID, writer, [ev(3, "accept", ids=["b"])]) == 3
    out = store.seal(SID, writer, 3, [row("a", nominal="1\u20280"), row("b")], ["a", "b"])
    assert out == {"revision": 1, "replay_ok": True}


def test_a_trailing_special_whitespace_char_does_not_look_torn(store, writer):
    # Same bug from the other end: a torn-tail check built on splitlines()
    # can see a value ending in one of these characters as a fragment and
    # silently delete an already-acknowledged event.
    store.append(SID, writer, [ev(1, "edit_cell", id="a", field="nominal", old="x", new="x\x85y")])
    assert store.append(SID, writer, [ev(2, "accept", ids=["a"])]) == 2
    lines = [l for l in
             (store.root / SID / "events.jsonl").read_text(encoding="utf-8").split("\n")
             if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["new"] == "x\x85y"


def test_repair_preserves_a_complete_line_missing_its_trailing_newline(store, writer):
    # A crash can also leave a COMPLETE line simply missing its "\n" (the
    # write returned before the separator landed). The old torn-tail check
    # only looked at whether the last line parsed, so it let this one through
    # unrepaired; the next append then ran straight onto it with no
    # separator, merging two JSON objects into one unparsable line -- which
    # then looked torn in turn and was erased, losing BOTH events.
    d = store.root / SID
    (d / "events.jsonl").write_text(json.dumps(ev(1, "accept", ids=["a"])), encoding="utf-8")
    assert store.append(SID, writer, [ev(2, "unaccept", ids=["a"])]) == 2
    assert store.seal(SID, writer, 2, [row("a"), row("b")], [])["revision"] == 1


def test_check_writer_handles_a_missing_writer_file(store, writer):
    (store.root / SID / "writer").unlink()
    with pytest.raises(StaleWriter):
        store.append(SID, writer, [])


def test_check_writer_refuses_a_non_string_token(store, writer):
    with pytest.raises(StaleWriter):
        store.append(SID, None, [])


def test_seal_refuses_a_non_int_final_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    with pytest.raises(JournalInvalid):
        store.seal(SID, writer, "1", [row("a"), row("b")], ["a"])


def test_seal_refuses_a_final_seq_below_what_is_already_sealed(store, writer):
    # With no events pending, the older "events beyond final_seq" guard is
    # vacuous (there is nothing to be beyond it), so a regressive final_seq
    # could otherwise sail through and mint a bogus second revision.
    store.seal(SID, writer, 0, [row("a"), row("b")], [])
    with pytest.raises(JournalInvalid):
        store.seal(SID, writer, -1, [row("a"), row("b")], [])


def test_fsync_dir_does_not_raise_on_a_real_directory(tmp_path):
    # Smoke test for the helper itself -- the durability it buys (a rename
    # surviving power loss) is not observable from within one process, so
    # this only confirms it is wired up and safe to call.
    from app.review.store import _fsync_dir
    _fsync_dir(tmp_path)


def test_a_structurally_complete_but_invalid_event_is_corruption(store, writer):
    # json.loads("{}") succeeds -- a crash mid-append can only tear an
    # in-flight write into an INCOMPLETE fragment, never leave behind a
    # complete, syntactically valid object that is simply the wrong shape.
    # So this is external corruption, not something to forgive even though
    # it parses, and not something whose position (first/middle/last)
    # should matter.
    d = store.root / SID
    (d / "events.jsonl").write_text(
        json.dumps(ev(1, "accept", ids=["a"])) + "\n"
        + "{}\n"
        + json.dumps(ev(3, "delete_row", id="b")) + "\n",
        encoding="utf-8")
    with pytest.raises(JournalInvalid):
        store.append(SID, writer, [])


def test_events_reads_a_raw_line_separator_char_written_with_ensure_ascii_false(store, writer):
    # Isolates the READ side of C1 alone: even a line some other writer (or
    # an older version of this code) wrote with ensure_ascii=False, with the
    # separator character raw rather than escaped, must still be read back
    # as one whole event -- _events splits only on "\n".
    d = store.root / SID
    line = json.dumps(
        ev(1, "edit_cell", id="a", field="nominal", old="x", new="1 0"), ensure_ascii=False)
    (d / "events.jsonl").write_text(line + "\n", encoding="utf-8")
    events = store._events(d)
    assert len(events) == 1
    assert events[0]["new"] == "1 0"


def test_seal_refuses_a_corrupted_proposal_cleanly(store, writer):
    # A pre-existing bad record on disk (corruption, or a proposal.json
    # written by a different format/version) must come back as a clean 422,
    # never an unhandled KeyError -- seal() is reachable from the API and an
    # unhandled exception there is a 500 for the rest of the session.
    d = store.root / SID
    (d / "proposal.json").write_text(json.dumps({"rows": [{"not_id": "a"}]}), encoding="utf-8")
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    with pytest.raises(JournalInvalid):
        store.seal(SID, writer, 1, [row("a")], ["a"])


def test_create_is_not_blocked_by_an_empty_sealed_directory(store, writer):
    # A crash right after mkdir(sealed) but before any revision file lands
    # must not permanently lock the session as "sealed" -- _revisions counts
    # files on disk, not directory existence.
    (store.root / SID / "sealed").mkdir()
    w2 = store.create(SID, {"h": 2}, {"rows": [row("a")]})
    assert w2 != writer


# ----- reopen (batch design §3) -----------------------------------------

def test_open_rotates_the_writer_so_the_older_tab_is_refused(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    opened = store.open(SID)
    assert opened["writer"] != writer
    with pytest.raises(StaleWriter):
        store.append(SID, writer, [ev(2, "accept", ids=["b"])])
    assert store.append(SID, opened["writer"], [ev(2, "accept", ids=["b"])]) == 2


def test_open_returns_the_proposal_the_net_events_and_the_last_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "delete_row", id="b"),
                               ev(3, "retract", target=2)])
    opened = store.open(SID)
    assert [r["id"] for r in opened["proposal"]] == ["a", "b"]
    assert [e["seq"] for e in opened["events"]] == [1]
    assert opened["last_seq"] == 3


def test_open_after_a_seal_continues_from_the_sealed_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"]),
                               ev(3, "retract", target=2)])
    rows = [{**row("a"), "reviewed": True}, row("b")]
    store.seal(SID, writer, 3, rows, ["a"])
    opened = store.open(SID)
    assert opened["last_seq"] == 3
    assert [e["seq"] for e in opened["events"]] == [1]


def test_open_drops_events_stranded_above_a_gap(store, writer):
    """An event above a hole was never acknowledged (acks are contiguous), so
    its tab still held it -- and that tab is the one this open just replaced.
    Left on disk it would collide with the new tab's seq numbers, and append
    keeps the FIRST write of a seq, silently discarding the new edit."""
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(3, "delete_row", id="b")])
    opened = store.open(SID)
    assert opened["last_seq"] == 1
    assert store.append(SID, opened["writer"], [ev(2, "accept", ids=["b"]),
                                                ev(3, "unaccept", ids=["b"])]) == 3
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert [json.loads(l)["type"] for l in lines] == ["accept", "accept", "unaccept"]


def test_open_an_unknown_session_is_refused(store):
    with pytest.raises(UnknownSession):
        store.open(SID)


def test_a_refused_open_changes_nothing_on_disk(store, writer):
    """An open that cannot rebuild the review (here: a retract of a seq that
    is no longer live) must change nothing beyond the torn-tail repair, which
    runs first and is safe on its own. Rotating the writer first would lock
    the working tab out of a session nobody can reopen; trimming first would
    discard its stranded events for nothing."""
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "retract", target=1),
                               ev(3, "retract", target=1), ev(5, "accept", ids=["b"])])
    before = (store.root / SID / "events.jsonl").read_text()
    with pytest.raises(JournalInvalid):
        store.open(SID)
    assert (store.root / SID / "events.jsonl").read_text() == before
    assert store.append(SID, writer, []) == 3


def test_revision_reads_a_sealed_revision_or_the_latest(store, writer):
    store.seal(SID, writer, 0, [row("a"), row("b")], [])
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    store.seal(SID, writer, 1, [{**row("a"), "reviewed": True}, row("b")], ["a"])
    assert store.revision(SID)["revision"] == 2
    assert store.revision(SID, 1)["reviewed_ids"] == []
    with pytest.raises(UnknownSession):
        store.revision(SID, 3)


def test_revision_refuses_when_nothing_is_sealed(store, writer):
    with pytest.raises(UnknownSession):
        store.revision(SID)
    with pytest.raises(UnknownSession):
        store.revision(SID, 0)
    with pytest.raises(UnknownSession):
        store.revision(SID, True)


def test_open_after_a_seal_trims_a_gap_above_the_sealed_seq(store, writer):
    """The contiguous run restarts at the sealed seq: 4 continues it and is
    kept, 6 sits above the hole at 5 and belongs to the replaced tab."""
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"]),
                               ev(3, "retract", target=2)])
    store.seal(SID, writer, 3, [{**row("a"), "reviewed": True}, row("b")], ["a"])
    store.append(SID, writer, [ev(4, "delete_row", id="b"), ev(6, "accept", ids=["b"])])
    opened = store.open(SID)
    assert opened["last_seq"] == 4
    assert [e["seq"] for e in opened["events"]] == [1, 4]
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert [json.loads(l)["seq"] for l in lines] == [1, 4]
    assert store.append(SID, opened["writer"], [ev(5, "unaccept", ids=["a"])]) == 5


@pytest.mark.parametrize("op", ["open", "seal"])
def test_a_proposal_that_is_not_json_is_a_journal_error(store, writer, op):
    # json.JSONDecodeError is a ValueError, not a KeyError/TypeError -- left
    # uncaught it is a 500 on every open/seal of that session, forever.
    (store.root / SID / "proposal.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(JournalInvalid):
        if op == "open":
            store.open(SID)
        else:
            store.seal(SID, writer, 0, [], [])
