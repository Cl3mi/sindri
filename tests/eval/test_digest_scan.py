"""H7: every committed campaign grade digest re-passes the leak rules.

The grader builds the digest from counts and closed words, but a later edit
to it (a new key fed from a row, a hand-edited file) could let a client value
or note through, and nothing re-checked the file that actually gets
committed. `scan_grade_digest` is that second lens, and the last test runs it
over whatever is in docs/eval/ today."""
import json
from pathlib import Path

import pytest

from app.eval.campaign_grade import DIGEST_KIND, grade_campaign
from app.eval.campaign_records import read_records
from app.eval.digest_scan import scan_grade_digest
from tests.eval.campaign_fixtures import drawing, event, proposal_row, write_tar
from tests.eval.test_campaign_grade import NOTE


def _real_digest(tmp_path):
    p = [proposal_row("a"), proposal_row("b")]
    tar = write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, [event(1, "set_note", text=NOTE)], group="great"),
        drawing("bbbbbbbb", p, [proposal_row("a")],
                [event(1, "delete_row", id="b", reason="duplicate")], group="problem")])
    _, digest = grade_campaign(read_records(tar), gold_by_hash=None)
    return json.loads(json.dumps(digest))


def test_a_digest_the_real_grader_wrote_is_clean(tmp_path):
    assert scan_grade_digest(_real_digest(tmp_path)) == []


def test_a_missing_or_wrong_kind_marker_is_flagged(tmp_path):
    d = _real_digest(tmp_path)
    d["kind"] = "something-else"
    assert scan_grade_digest(d)


@pytest.mark.parametrize("leak", ["Ø12.5 +0.1 -0.1", "a free text note",
                                  NOTE + " with spaces", "x" * 200])
def test_free_text_anywhere_is_flagged_without_quoting_it(tmp_path, leak):
    d = _real_digest(tmp_path)
    d["name"] = leak
    problems = scan_grade_digest(d)
    assert problems and all(leak not in p for p in problems)


def test_a_free_text_key_is_flagged_without_quoting_it(tmp_path):
    d = _real_digest(tmp_path)
    d["groups"]["see the client note"] = 1
    problems = scan_grade_digest(d)
    assert problems and all("client note" not in p for p in problems)


def test_the_value_field_tokens_are_flagged(tmp_path):
    """The pre-commit hook's own trap: a quoted field name as a key."""
    d = _real_digest(tmp_path)
    d["groups"]["upper_tol"] = 1
    assert scan_grade_digest(d)


def test_every_committed_grade_digest_is_clean():
    root = Path(__file__).resolve().parents[2] / "docs" / "eval"
    for path in sorted(root.glob("*-grade.json")):
        d = json.loads(path.read_text())
        assert d.get("kind") == DIGEST_KIND, path.name
        assert scan_grade_digest(d) == [], path.name
