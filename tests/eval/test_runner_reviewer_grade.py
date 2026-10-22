"""score --reviewer-grade (C2 design §Outputs): the agent-safe surface is one
stdout line and the --grade-out digest; the full report, which carries the
client's per-row values and notes, may only be written inside a protected root.

Every error path prints a FIXED sentence: stdout and stderr are read by agents,
and an exception message can quote a part number, a path or a note."""
import json

import pytest

from app.eval import runner
from app.eval.anon import doc_hash
from app.eval.models import GoldDoc
from tests.eval.campaign_fixtures import drawing, event, proposal_row, write_tar

SALT = "test-salt"
# Planted client text: none of it may ever reach stdout or stderr.
SECRET_FILE = "SECRET-PART-4711_rev-B.pdf"
SECRET_NOTE = "customer said bore 12H7 is wrong"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A protected root for the report, an empty gold dir, and a fixed salt so
    the Anonymizer never mints a real ~/.claude/sindri-doc-salt in a test."""
    prot = tmp_path / "prot"
    prot.mkdir()
    roots = tmp_path / "protected-paths"
    roots.write_text(f"{prot}\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(roots))
    monkeypatch.setenv("SINDRI_DOC_SALT", SALT)
    (tmp_path / "gold").mkdir()
    return prot


def _manifest(tmp_path, roles, pool=None):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"runs": [], "roles": roles,
                             "pool": pool or {"great": 1, "problem": 1}}))
    return m


def _tar(tmp_path, h="aaaaaaaa", group="great"):
    p = [proposal_row("a")]
    return write_tar(tmp_path / "r.tar", [drawing(
        h, p, p, [event(1, "set_note", text=SECRET_NOTE)], group=group,
        file_name=SECRET_FILE)])


def _args(tmp_path, tar, manifest, out, grade_out, *extra):
    argv = ["score", "--reviewer-grade", str(tar), "--gold", str(tmp_path / "gold"),
            "--name", "pilot-1", "--out", str(out), "--grade-out", str(grade_out)]
    if manifest is not None:
        argv += ["--manifest", str(manifest)]
    return argv + list(extra)


def _no_leak(capsys):
    cap = capsys.readouterr()
    for stream in (cap.out, cap.err):
        assert SECRET_FILE not in stream and SECRET_NOTE not in stream
        assert "Traceback" not in stream
    return cap


def test_reviewer_grade_writes_digest_and_report(tmp_path, env, capsys):
    tar = _tar(tmp_path)
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    out, gout = env / "pilot-1.grade.json", tmp_path / "eval" / "digest.json"
    assert runner.main(_args(tmp_path, tar, man, out, gout)) == 0
    digest = json.loads(gout.read_text())
    assert digest["drawings"]["approved"] == 1 and digest["reconciles"] is True
    assert json.loads(out.read_text())["digest"] == digest
    cap = _no_leak(capsys)
    lines = cap.out.strip().splitlines()
    assert lines == ["reviewer-grade: approved=1 pending=0 ungradable=0 "
                     "skipped=0 not_in_records=0 reconciles=True"]
    # The digest is the agent-safe file: the note is a count, never text.
    assert SECRET_NOTE not in gout.read_text()
    assert SECRET_FILE not in gout.read_text()


def test_gold_is_joined_through_the_salted_hash(tmp_path, env, capsys):
    """Records are keyed by the 8-hex hash; gold by the real part number. The
    join goes through the same salt, and only for hashes in the records."""
    h = doc_hash("PART-1", SALT)
    gold = GoldDoc(doc_id="PART-1", pdf="x", excel="y",
                   page_rect=(0.0, 0.0, 842.0, 595.0))
    (tmp_path / "gold" / "PART-1.gold.json").write_text(gold.model_dump_json())
    other = GoldDoc(doc_id="PART-2", pdf="x", excel="y",
                    page_rect=(0.0, 0.0, 842.0, 595.0))
    (tmp_path / "gold" / "PART-2.gold.json").write_text(other.model_dump_json())
    tar = _tar(tmp_path, h=h)
    man = _manifest(tmp_path, {h: "great"})
    gout = tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tar, man, env / "r.json", gout)) == 0
    g = json.loads(gout.read_text())["gold_cross_check"]
    assert g["n_drawings_computed"] == 1 and g["not_computed"] == {}
    cap = _no_leak(capsys)
    assert "PART-1" not in cap.out + cap.err


def test_reviewer_grade_refuses_a_report_outside_the_protected_root(tmp_path, env, capsys):
    tar = _tar(tmp_path)
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    leak, gout = tmp_path / "leak.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tar, man, leak, gout)) == 1
    assert not leak.exists() and not gout.exists()
    cap = _no_leak(capsys)
    assert "ERROR" in cap.err and str(leak) not in cap.err


def test_reviewer_grade_refuses_show_ids(tmp_path, env, capsys):
    """Real ids never join the 8-hex record hashes, and the digest must stay
    salted, so --show-ids has no meaning here but harm."""
    tar = _tar(tmp_path)
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    out, gout = env / "r.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tar, man, out, gout, "--show-ids")) == 1
    assert not out.exists() and not gout.exists()
    assert "--show-ids" in _no_leak(capsys).err


def test_reviewer_grade_needs_a_manifest(tmp_path, env, capsys):
    tar = _tar(tmp_path)
    out, gout = env / "r.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tar, None, out, gout)) == 1
    assert not out.exists() and not gout.exists()
    assert "--manifest" in _no_leak(capsys).err


def test_a_file_that_is_not_a_pull_is_one_fixed_error(tmp_path, env, capsys):
    bogus = tmp_path / SECRET_FILE
    bogus.write_text("not a tar at all")
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    out, gout = env / "r.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, bogus, man, out, gout)) == 1
    assert not out.exists() and not gout.exists()
    cap = _no_leak(capsys)
    assert cap.err.count("\n") == 1 and "ERROR" in cap.err


def test_a_missing_pull_is_one_fixed_error(tmp_path, env, capsys):
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    out, gout = env / "r.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tmp_path / "nope.tar", man, out, gout)) == 1
    assert not out.exists() and not gout.exists()
    assert str(tmp_path) not in _no_leak(capsys).err


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"runs": [], "roles": ["aaaaaaaa"], "pool": {}}),
    json.dumps({"runs": [], "roles": {"aaaaaaaa": "great"}, "pool": {"great": "x"}}),
])
def test_a_bad_manifest_is_one_fixed_error(tmp_path, env, capsys, content):
    tar = _tar(tmp_path)
    man = tmp_path / "m.json"
    man.write_text(content)
    out, gout = env / "r.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tar, man, out, gout)) == 1
    assert not out.exists() and not gout.exists()
    cap = _no_leak(capsys)
    assert cap.err.count("\n") == 1 and "manifest" in cap.err


def test_a_reconcile_problem_exits_2_with_a_count_only(tmp_path, env, capsys):
    """The run is not clean, so a script chaining on the exit code must stop;
    the problem keys carry salted hashes, but stdout gets the count alone."""
    tar = _tar(tmp_path, group="great")
    man = _manifest(tmp_path, {"aaaaaaaa": "problem"})   # disagrees on group
    out, gout = env / "r.json", tmp_path / "d.json"
    assert runner.main(_args(tmp_path, tar, man, out, gout)) == 2
    digest = json.loads(gout.read_text())
    assert digest["reconciles"] is False and out.is_file()
    lines = _no_leak(capsys).out.strip().splitlines()
    assert lines[0].startswith("reviewer-grade: approved=1")
    assert lines[0].endswith("reconciles=False")
    assert lines[1] == f"reviewer-grade: PROBLEMS {len(digest['reconcile_problems'])}"


def test_no_digest_without_its_report(tmp_path, env, capsys):
    """A digest whose report failed to write cannot be verified, so the report
    is written first and a failure there writes neither."""
    tar = _tar(tmp_path)
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    blocker = env / "file"
    blocker.write_text("x")
    out, gout = blocker / "r.json", tmp_path / "d.json"   # parent is a file
    assert runner.main(_args(tmp_path, tar, man, out, gout)) == 1
    assert not gout.exists()
    assert "ERROR" in _no_leak(capsys).err


def test_score_still_requires_run_without_reviewer_grade(tmp_path, env, capsys):
    rc = runner.main(["score", "--gold", str(tmp_path / "gold"), "--name", "x",
                      "--out", str(tmp_path / "o.json")])
    assert rc == 1
    assert "--run" in capsys.readouterr().err
