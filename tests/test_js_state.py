"""Runs the reviewer UI's node tests (tests/js/*.test.mjs) inside the Python
suite, so the suggestion rules -- numbering, filter, counts, confirm/undo --
cannot regress unnoticed. Skipped, not failed, where node is not installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ui_state_rules():
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert files, "no JS tests found"
    r = subprocess.run(["node", "--test", *files], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
