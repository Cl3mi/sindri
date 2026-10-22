"""`runner campaign-deck` (C2 design §Operator drill-down): the OPERATOR's
command, absent from the agent guard's allowlist on purpose.

Its startup refusals are still printed where anyone may read them, so each is
ONE fixed sentence -- never a path, a part number or exception text -- and a
refused start binds no port at all."""
import json
import re
from pathlib import Path

import pytest

from app.eval import anon, campaign_deck, runner
from tests.eval.test_campaign_deck import SAMPLE, STEM, _report, _write

SECRET = "SECRET-PART-4711"


@pytest.fixture
def env(tmp_path, monkeypatch):
    prot = tmp_path / "prot" / SECRET
    pdfs = prot / "originals"
    pdfs.mkdir(parents=True)
    (pdfs / (STEM + SAMPLE.suffix)).write_bytes(SAMPLE.read_bytes())
    roots = tmp_path / "protected-paths"
    roots.write_text(f"{tmp_path / 'prot'}\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(roots))
    monkeypatch.setenv("SINDRI_DOC_SALT", "t")
    bound = []

    def no_bind(*a, **kw):
        bound.append(1)
        raise AssertionError("a refused start must not bind")
    monkeypatch.setattr(campaign_deck, "make_server", no_bind)
    return prot, pdfs, bound


def _refused(capsys, argv, bound):
    assert runner.main(argv) == 1
    out, err = capsys.readouterr()
    lines = [l for l in (out + err).splitlines() if l.strip()]
    assert len(lines) == 1, lines
    # No path of any kind: the protected dir is itself named like a part.
    assert SECRET not in lines[0] and "/" not in lines[0]
    assert not bound
    return lines[0]


def test_a_report_outside_a_protected_root_is_refused(env, tmp_path, capsys):
    prot, pdfs, bound = env
    report = _write(tmp_path, _report())
    _refused(capsys, ["campaign-deck", "--report", str(report), "--pdfs", str(pdfs)],
             bound)


def test_a_symlinked_report_is_refused(env, capsys):
    prot, pdfs, bound = env
    real = _write(prot, _report())
    link = prot / "link.grade.json"
    link.symlink_to(real)
    _refused(capsys, ["campaign-deck", "--report", str(link), "--pdfs", str(pdfs)], bound)


@pytest.mark.parametrize("body", [
    json.dumps({"kind": "campaign-grade-digest", "drawings": []}),
    json.dumps({"drawings": [], "kind": "campaign-grade-report"}),
    '{"kind": "campaign-grade-report", "drawings": [',     # marker ok, JSON torn
    json.dumps({"kind": "campaign-grade-report", "drawings": {"x": 1}})])
def test_a_file_that_is_not_a_readable_grading_report_is_refused(env, capsys, body):
    prot, pdfs, bound = env
    report = prot / "c.grade.json"
    report.write_text(body)
    _refused(capsys, ["campaign-deck", "--report", str(report), "--pdfs", str(pdfs)],
             bound)


def test_a_missing_report_is_refused(env, capsys):
    prot, pdfs, bound = env
    _refused(capsys, ["campaign-deck", "--report", str(prot / "none.grade.json"),
                      "--pdfs", str(pdfs)], bound)


def test_pdfs_that_is_not_a_directory_is_refused(env, capsys):
    prot, pdfs, bound = env
    report = _write(prot, _report())
    _refused(capsys, ["campaign-deck", "--report", str(report),
                      "--pdfs", str(pdfs / (STEM + SAMPLE.suffix))], bound)


def test_no_persistent_salt_is_refused_and_none_is_minted(env, tmp_path, monkeypatch,
                                                          capsys):
    """Without the operator's salt no report hash joins to a drawing, and
    minting one would leave a throwaway salt file behind."""
    prot, pdfs, bound = env
    monkeypatch.delenv("SINDRI_DOC_SALT")
    nosalt = tmp_path / "nosalt" / "sindri-doc-salt"
    monkeypatch.setattr(anon, "DEFAULT_SALT_FILE", nosalt)
    report = _write(prot, _report())
    _refused(capsys, ["campaign-deck", "--report", str(report), "--pdfs", str(pdfs)],
             bound)
    assert not nosalt.exists()


def test_a_good_start_prints_only_the_localhost_url(env, monkeypatch, capsys):
    """The token travels only in the URL line on the operator's terminal;
    nothing about paths or values is printed."""
    prot, pdfs, _ = env

    class Srv:
        server_address = ("127.0.0.1", 5555)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass
    monkeypatch.setattr(campaign_deck, "make_server", lambda app, port=0: Srv())
    report = _write(prot, _report())
    assert runner.main(["campaign-deck", "--report", str(report),
                        "--pdfs", str(pdfs)]) == 0
    out, err = capsys.readouterr()
    assert re.search(r"http://127\.0\.0\.1:5555/\?t=[\w-]{20,}", out)
    for leak in (SECRET, STEM, str(prot), "<img"):
        assert leak not in out + err


def test_the_subcommand_says_it_is_the_operators():
    help_text = runner_help()
    assert "agent" in help_text and "own terminal" in help_text


def runner_help():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        runner.main(["campaign-deck", "--help"])
    return buf.getvalue()


def test_the_guard_allowlist_does_not_admit_campaign_deck():
    """The guard is outside the repo; when it is present, campaign-deck must
    not be one of the runner verbs it lets an agent run."""
    guard = Path.home() / ".claude" / "hooks" / "sindri-guard.py"
    if not guard.is_file():
        pytest.skip("no agent guard on this machine")
    assert "campaign-deck" not in guard.read_text(encoding="utf-8")
