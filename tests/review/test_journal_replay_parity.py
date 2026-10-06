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
from app.review.replay import mismatches, replay

ROOT = Path(__file__).resolve().parents[2]
SEED = 12345
N_SESSIONS = 300


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
            validate_event(e)   # every event the journal ever emits must pass the server's gate
        net = net_events(c["events"])
        bad = mismatches(replay(c["proposal"], net), c["rows"], c["reviewed_ids"])
        assert bad == [], (c["case"], bad, c["log"])
