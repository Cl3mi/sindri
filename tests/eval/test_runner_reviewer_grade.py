"""score --reviewer-grade (C2 design §Outputs): the agent-safe surface is one
stdout line and the --grade-out digest; the full report, which carries the
client's per-row values and notes, may only be written inside a protected root.

Every error path prints a FIXED sentence: stdout and stderr are read by agents,
and an exception message can quote a part number, a path or a note. And since
an agent runs this command, neither output path may be aimed at anything it
grades or anything else worth keeping: gold, dumps, the pull tar itself."""
import gzip
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
    """A protected root for the report, a gold dir holding one gold file, and
    a fixed salt so the Anonymizer never mints a real ~/.claude/sindri-doc-salt
    in a test."""
    prot = tmp_path / "prot"
    prot.mkdir()
    roots = tmp_path / "protected-paths"
    roots.write_text(f"{prot}\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(roots))
    monkeypatch.setenv("SINDRI_DOC_SALT", SALT)
    _gold(tmp_path, "PART-9")
    return prot


def _gold(tmp_path, doc_id):
    g = GoldDoc(doc_id=doc_id, pdf="x", excel="y", page_rect=(0.0, 0.0, 842.0, 595.0))
    (tmp_path / "gold").mkdir(exist_ok=True)
    path = tmp_path / "gold" / f"{doc_id}.gold.json"
    path.write_text(g.model_dump_json())
    return path


def _manifest(tmp_path, roles, pool=None):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"runs": [], "roles": roles,
                             "pool": pool or {"great": 1, "problem": 1}}))
    return m


def _tar(tmp_path, h="aaaaaaaa", group="great", path=None):
    p = [proposal_row("a")]
    return write_tar(path or tmp_path / "r.tar", [drawing(
        h, p, p, [event(1, "set_note", text=SECRET_NOTE)], group=group,
        file_name=SECRET_FILE)])


def _out(env):
    return env / "pilot-1.grade.json"


def _gout(tmp_path):
    return tmp_path / "eval" / "pilot-1-grade.json"


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


def _run(tmp_path, env, *extra, tar=None, man=None, out=None, gout=None):
    tar = tar or _tar(tmp_path)
    man = man or _manifest(tmp_path, {"aaaaaaaa": "great"})
    return runner.main(_args(tmp_path, tar, man, out or _out(env),
                             gout or _gout(tmp_path), *extra))


# --- the happy path -----------------------------------------------------------

def test_reviewer_grade_writes_digest_and_report(tmp_path, env, capsys):
    out, gout = _out(env), _gout(tmp_path)
    assert _run(tmp_path, env) == 0
    digest = json.loads(gout.read_text())
    assert digest["drawings"]["approved"] == 1 and digest["reconciles"] is True
    report = json.loads(out.read_text())
    assert report["digest"] == digest
    # The kind markers come FIRST: they are how a later run recognises a file
    # it may overwrite without parsing the whole of a large report.
    assert next(iter(report)) == "kind" and report["kind"] == "campaign-grade-report"
    assert next(iter(digest)) == "kind" and digest["kind"] == "campaign-grade-digest"
    cap = _no_leak(capsys)
    assert cap.out.strip().splitlines() == [
        "reviewer-grade: approved=1 pending=0 ungradable=0 "
        "skipped=0 not_in_records=0 reconciles=True"]
    # The digest is the agent-safe file: the note is a count, never text.
    assert SECRET_NOTE not in gout.read_text()
    assert SECRET_FILE not in gout.read_text()
    assert not list(out.parent.glob("*.partial")) and not list(gout.parent.glob("*.partial"))


def test_a_previous_report_and_digest_may_be_regenerated(tmp_path, env, capsys):
    """Re-grading the same pull is how a fixed grader is re-run."""
    assert _run(tmp_path, env) == 0
    assert _run(tmp_path, env) == 0


def test_gold_is_joined_through_the_salted_hash(tmp_path, env, capsys):
    """Records are keyed by the 8-hex hash; gold by the real part number. The
    join goes through the same salt, and only for hashes in the records."""
    h = doc_hash("PART-1", SALT)
    _gold(tmp_path, "PART-1")
    _gold(tmp_path, "PART-2")
    assert _run(tmp_path, env, tar=_tar(tmp_path, h=h),
                man=_manifest(tmp_path, {h: "great"})) == 0
    g = json.loads(_gout(tmp_path).read_text())["gold_cross_check"]
    assert g["n_drawings_computed"] == 1 and g["not_computed"] == {}
    cap = _no_leak(capsys)
    assert "PART-1" not in cap.out + cap.err


# --- what the report may not overwrite ----------------------------------------

def _refused(tmp_path, env, capsys, **kw):
    preexists = kw.pop("gout_preexists", False)
    gout = kw.get("gout") or _gout(tmp_path)
    assert _run(tmp_path, env, **kw) == 1
    assert preexists or not gout.exists()
    cap = _no_leak(capsys)
    assert "ERROR" in cap.err and str(tmp_path) not in cap.err
    return cap


def test_report_outside_the_protected_root_is_refused(tmp_path, env, capsys):
    leak = tmp_path / "leak.grade.json"
    _refused(tmp_path, env, capsys, out=leak)
    assert not leak.exists()


def test_report_may_not_overwrite_gold(tmp_path, env, capsys):
    """A gold file inside the root ends in .gold.json, not .grade.json."""
    gold = env / "PART-1.gold.json"
    gold.write_text('{"doc_id": "PART-1"}')
    _refused(tmp_path, env, capsys, out=gold)
    assert gold.read_text() == '{"doc_id": "PART-1"}'


def test_report_may_not_overwrite_the_pull_it_grades(tmp_path, env, capsys):
    tar = _tar(tmp_path, path=env / "pull.grade.json")
    before = tar.read_bytes()
    _refused(tmp_path, env, capsys, tar=tar, out=tar)
    assert tar.read_bytes() == before


def test_report_may_not_overwrite_the_manifest(tmp_path, env, capsys):
    man = env / "m.grade.json"
    man.write_text(json.dumps({"runs": [], "roles": {"aaaaaaaa": "great"},
                               "pool": {"great": 1}}))
    before = man.read_text()
    _refused(tmp_path, env, capsys, man=man, out=man)
    assert man.read_text() == before


def test_report_may_not_be_a_symlink(tmp_path, env, capsys):
    """A link named like a report can alias a gold file or a dump."""
    gold = env / "PART-1.gold.json"
    gold.write_text("{}")
    link = env / "x.grade.json"
    link.symlink_to(gold)
    _refused(tmp_path, env, capsys, out=link)
    assert gold.read_text() == "{}"


def test_report_may_not_replace_a_foreign_grade_json(tmp_path, env, capsys):
    foreign = env / "other.grade.json"
    foreign.write_text(json.dumps({"campaign": "x"}))
    _refused(tmp_path, env, capsys, out=foreign)
    assert json.loads(foreign.read_text()) == {"campaign": "x"}


# --- what the digest may not overwrite ----------------------------------------

def test_digest_needs_the_grade_suffix(tmp_path, env, capsys):
    _refused(tmp_path, env, capsys, gout=tmp_path / "eval" / "d.json")
    assert not _out(env).exists()


def test_digest_may_not_go_inside_the_protected_root(tmp_path, env, capsys):
    _refused(tmp_path, env, capsys, gout=env / "pilot-1-grade.json")
    assert not _out(env).exists()


def test_digest_may_not_be_a_symlink(tmp_path, env, capsys):
    target = tmp_path / "keep.json"
    target.write_text("{}")
    link = tmp_path / "x-grade.json"
    link.symlink_to(target)
    _refused(tmp_path, env, capsys, gout=link, gout_preexists=True)
    assert target.read_text() == "{}"


def test_digest_may_not_replace_a_foreign_file(tmp_path, env, capsys):
    foreign = tmp_path / "x-grade.json"
    foreign.write_text(json.dumps({"would_fix": 1}))
    _refused(tmp_path, env, capsys, gout=foreign, gout_preexists=True)
    assert json.loads(foreign.read_text()) == {"would_fix": 1}


# --- refusals before anything is read -----------------------------------------

def test_reviewer_grade_refuses_show_ids(tmp_path, env, capsys):
    """Real ids never join the 8-hex record hashes, and the digest must stay
    salted, so --show-ids has no meaning here but harm."""
    assert _run(tmp_path, env, "--show-ids") == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert "--show-ids" in _no_leak(capsys).err


@pytest.mark.parametrize("flags", [
    ["--run", "x"], ["--review-deck", "x"], ["--policy-check"],
    ["--reapply-policy"], ["--gentol-check"], ["--phantom-profile"],
    ["--drop-check"], ["--reparse-check"], ["--splits", "s.json"],
    ["--split", "test"], ["--policy-out", "p.json"], ["--gentol-out", "g.json"],
    ["--phantom-out", "x.json"], ["--drop-out", "d.json"], ["--pdfs", "dir"],
    ["--max-pages", "1"], ["--exclude-clamped"], ["--weights", "w.json"],
    ["--allow-partial"], ["--flag-rules", "a"], ["--drop-rules", "a"],
    ["--drop-configs", "a"], ["--drop-family", "verifier"],
    ["--assignment", "max_cardinality"], ["--reconcile-frames", "scale"],
    ["--dpi", "150"]])
def test_score_flags_of_other_modes_are_refused(tmp_path, env, capsys, flags):
    assert _run(tmp_path, env, *flags) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert "--reviewer-grade" in _no_leak(capsys).err


def test_reviewer_grade_needs_a_manifest(tmp_path, env, capsys):
    tar = _tar(tmp_path)
    assert runner.main(_args(tmp_path, tar, None, _out(env), _gout(tmp_path))) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert "--manifest" in _no_leak(capsys).err


def test_a_missing_salt_is_refused_not_minted(tmp_path, env, capsys, monkeypatch):
    """A fresh salt would hash nothing to the records' hashes -- every gold
    join silently fails -- and would leave a file behind."""
    import app.eval.anon as anon
    monkeypatch.delenv("SINDRI_DOC_SALT")
    salt_file = tmp_path / "no-salt"
    monkeypatch.setattr(anon, "DEFAULT_SALT_FILE", salt_file)
    assert _run(tmp_path, env) == 1
    assert not salt_file.exists()
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert "salt" in _no_leak(capsys).err


@pytest.mark.parametrize("which", ["missing", "empty", "file"])
def test_a_gold_dir_without_gold_is_refused(tmp_path, env, capsys, which):
    tar = _tar(tmp_path)
    man = _manifest(tmp_path, {"aaaaaaaa": "great"})
    gold = tmp_path / ("g-" + which)
    if which == "empty":
        gold.mkdir()
    elif which == "file":
        gold.write_text("x")
    argv = _args(tmp_path, tar, man, _out(env), _gout(tmp_path))
    argv[argv.index("--gold") + 1] = str(gold)
    assert runner.main(argv) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    cap = _no_leak(capsys)
    assert "--gold" in cap.err and str(tmp_path) not in cap.err


# --- unreadable inputs ---------------------------------------------------------

def test_a_file_that_is_not_a_pull_is_one_fixed_error(tmp_path, env, capsys):
    bogus = tmp_path / SECRET_FILE
    bogus.write_text("not a tar at all")
    assert _run(tmp_path, env, tar=bogus) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    cap = _no_leak(capsys)
    assert cap.err.count("\n") == 1 and "ERROR" in cap.err


def test_a_compressed_pull_is_refused(tmp_path, env, capsys):
    """pull.py writes plain tar; a compressed archive is something else, and
    decompressing it unasked is how a small file becomes a huge one."""
    raw = _tar(tmp_path).read_bytes()
    gz = tmp_path / "r.tar.gz"
    gz.write_bytes(gzip.compress(raw))
    assert _run(tmp_path, env, tar=gz) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert _no_leak(capsys).err.count("\n") == 1


def test_a_missing_pull_is_one_fixed_error(tmp_path, env, capsys):
    assert _run(tmp_path, env, tar=tmp_path / "nope.tar") == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert str(tmp_path) not in _no_leak(capsys).err


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"runs": [], "roles": ["aaaaaaaa"], "pool": {}}),
    json.dumps({"runs": [], "roles": {"aaaaaaaa": "great"}, "pool": {"great": "x"}}),
])
def test_a_bad_manifest_is_one_fixed_error(tmp_path, env, capsys, content):
    man = tmp_path / "m.json"
    man.write_text(content)
    assert _run(tmp_path, env, man=man) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    cap = _no_leak(capsys)
    assert cap.err.count("\n") == 1 and "manifest" in cap.err


def test_an_unexpected_failure_is_one_fixed_error(tmp_path, env, capsys, monkeypatch):
    """Grading code can raise with a client value in the message; the runner
    must never print it."""
    import app.eval.campaign_grade as cg

    def boom(*a, **k):
        raise RuntimeError(SECRET_NOTE)
    monkeypatch.setattr(cg, "grade_campaign", boom)
    assert _run(tmp_path, env) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert _no_leak(capsys).err == "ERROR: grading failed\n"


# --- an OSError while probing a path never prints the path ---------------------
# resolve(), is_symlink(), exists() and the marker read can all raise, and a
# PermissionError's text IS the path -- a client folder name, on a real run.
MARKER = "PLANTED-CLIENT-DIR-4711"


@pytest.mark.parametrize("check", ["check_report_path", "check_digest_path"])
def test_an_oserror_checking_an_output_is_a_fixed_refusal(tmp_path, env, capsys,
                                                         monkeypatch, check):
    import app.eval.review as review

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", MARKER)
    monkeypatch.setattr(review, check, boom)
    assert _run(tmp_path, env) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    cap = _no_leak(capsys)
    assert cap.err == "ERROR: output path refused\n" and MARKER not in cap.out


def test_an_oserror_from_the_real_path_probe_is_a_fixed_refusal(tmp_path, env, capsys,
                                                                monkeypatch):
    """The same through pathlib itself, not a stubbed checker."""
    import pathlib
    real = pathlib.Path.is_symlink

    def boom(self):
        if self.name.endswith("grade.json"):
            raise PermissionError(13, "Permission denied", MARKER)
        return real(self)
    monkeypatch.setattr(pathlib.Path, "is_symlink", boom)
    assert _run(tmp_path, env) == 1
    cap = _no_leak(capsys)
    assert cap.err == "ERROR: output path refused\n" and MARKER not in cap.out


def test_an_oserror_probing_the_inputs_is_a_fixed_refusal(tmp_path, env, capsys,
                                                          monkeypatch):
    import pathlib
    real = pathlib.Path.glob

    def boom(self, pattern, *a, **k):
        if pattern.endswith("gold.json"):
            raise PermissionError(13, "Permission denied", MARKER)
        return real(self, pattern, *a, **k)
    monkeypatch.setattr(pathlib.Path, "glob", boom)
    assert _run(tmp_path, env) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    cap = _no_leak(capsys)
    assert cap.err == "ERROR: input unreadable\n" and MARKER not in cap.out


# --- exit codes and writes -----------------------------------------------------

def test_a_reconcile_problem_exits_3_with_a_count_only(tmp_path, env, capsys):
    """The run is not clean, so a script chaining on the exit code must stop;
    3, because 2 is argparse's usage error. The problem keys carry salted
    hashes, but stdout gets the count alone."""
    man = _manifest(tmp_path, {"aaaaaaaa": "problem"})   # disagrees on group
    assert _run(tmp_path, env, man=man) == 3
    digest = json.loads(_gout(tmp_path).read_text())
    assert digest["reconciles"] is False and _out(env).is_file()
    lines = _no_leak(capsys).out.strip().splitlines()
    assert lines[0].startswith("reviewer-grade: approved=1")
    assert lines[0].endswith("reconciles=False")
    assert lines[1] == f"reviewer-grade: PROBLEMS {len(digest['reconcile_problems'])}"


def test_no_digest_without_its_report(tmp_path, env, capsys, monkeypatch):
    """A digest whose report failed to write cannot be verified, so the report
    is written first and a failure there writes neither -- nor leaves a
    partial file behind."""
    import os
    real = os.replace

    def fail_report(src, dst):
        if str(dst).endswith(".grade.json"):
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", fail_report)
    assert _run(tmp_path, env) == 1
    assert not _out(env).exists() and not _gout(tmp_path).exists()
    assert not list(env.glob("*.partial"))
    assert "ERROR" in _no_leak(capsys).err


def test_score_still_requires_run_without_reviewer_grade(tmp_path, env, capsys):
    rc = runner.main(["score", "--gold", str(tmp_path / "gold"), "--name", "x",
                      "--out", str(tmp_path / "o.json")])
    assert rc == 1
    assert "--run" in capsys.readouterr().err


def test_a_bad_flag_is_a_usage_error_that_echoes_nothing(tmp_path, env, capsys):
    """argparse quotes the offending VALUE ('unrecognized arguments: --outt
    SECRET'); runner-wide, a usage error is one fixed line and exit 2."""
    with pytest.raises(SystemExit) as e:
        runner.main(["score", "--outt", SECRET_FILE])
    assert e.value.code == 2
    cap = _no_leak(capsys)
    assert cap.err == "ERROR: bad arguments (see --help)\n" and cap.out == ""


def test_help_still_works(capsys):
    with pytest.raises(SystemExit) as e:
        runner.main(["score", "--help"])
    assert e.value.code == 0
    assert "--reviewer-grade" in capsys.readouterr().out
