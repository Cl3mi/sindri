"""Fuzzes state.js + journal.js through random reviewer action sequences
(node) and checks, in Python, that every emitted event validates, nets
cleanly, and replays (app/review/replay.py) to exactly the final rows the UI
ended up with -- the cross-language contract the whole journal depends on,
permanent so a future change to either side cannot regress it unnoticed.

Skipped, not failed, where node is not installed (same pattern as
tests/test_js_state.py)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.review.journal import net_events, validate_event
from app.review.replay import mismatches, note_of, replay

ROOT = Path(__file__).resolve().parents[2]
SEED = 12345
N_SESSIONS = 300

# Every event the journal emits carries these, whatever its type.
_BASE_KEYS = {"seq", "type", "t_wall", "t_ms"}
# ...plus these, by type. mismatches() only catches a field replay.py reads
# AND compares; a key journal.js stops sending (or misnames) for a field
# replay.py never looks at would pass that check silently, so this is a
# direct shape assertion instead.
_TYPE_KEYS = {
    "edit_cell": {"id", "field", "old", "new"},
    "move_row": {"id", "xy"},
    "delete_row": {"id"},
    "add_row": {"row"},
    "accept": {"ids"},
    "unaccept": {"ids"},
    "confirm_suggestions": {"ids"},
    "retract": {"target"},
    "move_target": {"id", "box"},
    "set_note": {"text"},
}


def _check_event_shape(e):
    missing_base = _BASE_KEYS - e.keys()
    assert not missing_base, (e, missing_base)
    required = _TYPE_KEYS.get(e["type"])
    assert required is not None, f"unknown event type {e['type']!r}: {e}"
    missing = required - e.keys()
    assert not missing, (e, missing)
    if e["type"] == "add_row":
        assert "id" in e["row"], e


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_journal_replay_parity_across_js_and_python():
    r = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "fuzz_journal.mjs"), str(SEED), str(N_SESSIONS)],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    cases = json.loads(r.stdout)
    assert len(cases) == N_SESSIONS

    for c in cases:
        for e in c["events"]:
            _check_event_shape(e)
            validate_event(e)   # every event the journal ever emits must pass the server's gate
        net = net_events(c["events"])
        bad = mismatches(replay(c["proposal"], net), c["rows"], c["reviewed_ids"])
        assert bad == [], (c["case"], bad, c["log"])
        assert note_of(net) == c["note"], (c["case"], "note", c["log"])
    types = {e["type"] for c in cases for e in c["events"]}
    # the fuzz must actually exercise the new vocabulary, not just pass
    assert {"move_target", "set_note"} <= types, types
    assert any("reason" in e for c in cases for e in c["events"]), "no reason was fuzzed"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_review_rebuilt_from_the_server_matches_and_keeps_replaying():
    """Batch design §3: reopening a drawing rebuilds the review through
    state.js from the proposal + net events. The rebuilt state must equal what
    the server replays, and edits after the reload -- undos of restored
    operations included -- must still replay to the final rows.

    Numbering resumes from `last_seq`, never from the highest NET event: the
    store keeps the first write of a seq, so a reused seq would be dropped
    while looking acknowledged. A post-seal reopen (the session ended on
    undos, so `last_seq` sits above every net event) is the case that tells
    the two apart, and the fuzz must actually produce it."""
    r = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "fuzz_journal.mjs"), str(SEED), str(N_SESSIONS), "resume"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    cases = json.loads(r.stdout)
    assert len(cases) == N_SESSIONS
    gapped = 0
    reached_back = 0
    for c in cases:
        for e in c["events"]:
            _check_event_shape(e)
            validate_event(e)
        held = net_events(c["events_before_reload"])
        # `held and`: an empty net set would make all() vacuously true and
        # count a case that tells nothing about holes in the numbering
        if held and all(e["seq"] < c["last_seq"] for e in held):
            gapped += 1
        after = c["events"][len(c["events_before_reload"]):]
        seqs = [e["seq"] for e in after]
        # strictly past last_seq and contiguous: nothing the server already
        # holds is ever re-sent under a seq it would silently drop
        assert seqs == list(range(c["last_seq"] + 1, c["last_seq"] + 1 + len(after))), (c["case"], seqs)
        held_seqs = {e["seq"] for e in held}
        reached_back += any(e["type"] == "retract" and e["target"] in held_seqs for e in after)
        # 1. what the browser rebuilt equals the server's replay of what it held
        rebuilt = mismatches(replay(c["proposal"], held),
                             c["restored_rows"], c["restored_reviewed"])
        assert rebuilt == [], (c["case"], "rebuild", rebuilt, c["log"])
        assert note_of(held) == c["restored_note"], (c["case"], "restored note", c["log"])
        # 2. edits after the reload (undos of restored ops included) still replay
        final = mismatches(replay(c["proposal"], net_events(c["events"])),
                           c["rows"], c["reviewed_ids"])
        assert final == [], (c["case"], "after reload", final, c["log"])
        assert note_of(net_events(c["events"])) == c["note"], (c["case"], "note", c["log"])
    # the fuzz must exercise the cases this test exists for, not just pass
    assert gapped > 0, "no case with last_seq above every net event (post-seal reopen)"
    assert reached_back > 0, "no case undid a restored operation after the reload"
    types = {e["type"] for c in cases for e in c["events"]}
    assert {"move_target", "set_note"} <= types, types
    assert any("reason" in e for c in cases for e in c["events"]), "no reason was fuzzed"
