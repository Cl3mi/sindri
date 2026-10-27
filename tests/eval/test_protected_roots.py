"""The protected-roots file is parsed the way the Bash guard parses it.

Security review of H1 Milestone A, finding 1: `_protected_roots()` used to
keep every non-blank line and resolve it against the cwd. The real file
starts with comment lines, so `<cwd>/#` and `<cwd>/# ...` became "roots",
and a path under them passed every inside-a-root check -- the deck, the
grading report and the class-B outputs could all be written to an
agent-readable folder named `#`. These tests reproduce those exploits on a
synthetic roots file; none of them touches the machine's real one."""
import os

import pytest

from app.eval import outputs
from app.eval.outputs import OutputRefused, check_client_file_out, check_digest_out
from app.eval.review import (ProtectedRootsError, ReviewRefused, _protected_roots,
                             check_deck_path, check_report_path, inside_protected)

HEADER = ("#\n"
          "# protected roots, one per line\n"
          "   # indented comment\n")


def _write_roots(tmp_path, monkeypatch, body: str):
    f = tmp_path / "roots.txt"
    f.write_text(body)
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(f))
    return f


@pytest.fixture
def commented_roots(tmp_path, monkeypatch):
    """The real file's shape: comment lines, a trailing comment, a `~` line.
    The cwd is the tmp dir, which is where a comment line used to resolve."""
    home = tmp_path / "home"
    (home / "client" / "reports").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    _write_roots(tmp_path, monkeypatch,
                 HEADER + "~/client\n" + f"{other}   # second root\n\n")
    return home / "client", other


def test_comments_and_tilde_parse_like_the_guard(commented_roots):
    client, other = commented_roots
    assert _protected_roots() == [client.resolve(), other.resolve()]


@pytest.mark.parametrize("comment_dir", ["#", "# protected roots, one per line"])
def test_comment_lines_are_not_roots(commented_roots, tmp_path, comment_dir):
    """Each of these was ALLOWED before the fix."""
    d = tmp_path / comment_dir
    assert not inside_protected(d / "reports" / "a.report.json")
    with pytest.raises(OutputRefused):
        check_client_file_out(d / "reports" / "a.report.json",
                              subdir="reports", suffix=".report.json")
    with pytest.raises(ReviewRefused):
        check_deck_path(f"{comment_dir}/deck.json")
    with pytest.raises(ReviewRefused):
        check_report_path(f"{comment_dir}/x.grade.json")


def test_the_real_root_still_works(commented_roots):
    client, _ = commented_roots
    assert check_client_file_out(client / "reports" / "a.report.json",
                                 subdir="reports", suffix=".report.json")
    assert check_deck_path(client / "reports" / "deck.json")
    with pytest.raises(OutputRefused):
        check_digest_out(client / "reports" / "d.json", "run-summary-digest")


def test_a_relative_line_is_a_config_error_that_fails_closed(tmp_path, monkeypatch):
    """A relative root means different folders from different cwds. Neither
    'no roots' (would skip the root rule) nor 'resolve here' (would protect
    the wrong folder) is safe, so every checker refuses."""
    monkeypatch.chdir(tmp_path)
    _write_roots(tmp_path, monkeypatch, HEADER + "relative/client\n")
    with pytest.raises(ProtectedRootsError) as e:
        _protected_roots()
    assert "relative" not in str(e.value)
    with pytest.raises(ReviewRefused):
        check_deck_path(tmp_path / "relative" / "client" / "deck.json")
    for call in (
            lambda: check_digest_out(tmp_path / "d.json", "run-summary-digest"),
            lambda: check_client_file_out(tmp_path / "a.report.json",
                                          subdir="reports", suffix=".report.json"),
            lambda: outputs.check_client_dir_out(tmp_path / "r", subdir="runs",
                                                 legacy=("*.pred.json",))):
        with pytest.raises(ProtectedRootsError) as e:
            call()
        assert str(tmp_path) not in str(e.value)


def test_an_undecodable_roots_file_fails_closed(tmp_path, monkeypatch):
    f = tmp_path / "roots.txt"
    f.write_bytes(b"\xff\xfe\x00/x\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(f))
    with pytest.raises(ProtectedRootsError):
        _protected_roots()
    with pytest.raises(ProtectedRootsError):
        check_digest_out(tmp_path / "d.json", "run-summary-digest")


def test_an_unreadable_roots_file_fails_closed(tmp_path, monkeypatch):
    f = _write_roots(tmp_path, monkeypatch, str(tmp_path / "client") + "\n")
    f.chmod(0)
    try:
        if os.access(f, os.R_OK):
            pytest.skip("running as a user that ignores permissions")
        with pytest.raises(ProtectedRootsError):
            _protected_roots()
    finally:
        f.chmod(0o644)


# ---- fix round 2 (E): an explicit override that names nothing fails closed --

@pytest.mark.parametrize("what", ["missing", "directory"])
def test_an_explicit_override_that_is_not_a_file_fails_closed(
        tmp_path, monkeypatch, what):
    """SINDRI_PROTECTED_PATHS set by hand means someone meant a roots file.
    A typo there used to read as 'no roots' -- skipping the root rule on a
    machine that has client data. Only the DEFAULT path may be absent."""
    target = tmp_path / "roots-typo"
    if what == "directory":
        target.mkdir()
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(target))
    with pytest.raises(ProtectedRootsError) as e:
        _protected_roots()
    assert str(tmp_path) not in str(e.value)
    with pytest.raises(ProtectedRootsError):
        check_client_file_out(tmp_path / "a.report.json", subdir="reports",
                              suffix=".report.json")


def test_the_operator_sees_the_roots_sentence_not_cannot_be_checked(
        tmp_path, monkeypatch, capsys):
    """Review round 3: the checkers' generic catch turned a broken roots
    file into 'the digest path cannot be checked', hiding the actual fix.
    It now reaches main() as itself."""
    from app.eval import runner
    from tests.eval.test_runner_output_hardening import _report
    reports_root = tmp_path / "client"
    (reports_root / "reports").mkdir(parents=True)
    good = tmp_path / "good-roots"
    good.write_text(f"{reports_root}\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(good))
    report = _report(tmp_path, reports_root)
    broken = tmp_path / "roots-dir"
    broken.mkdir()
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(broken))
    capsys.readouterr()
    for mk in (lambda: check_digest_out(tmp_path / "d.json", "k"),
               lambda: check_client_file_out(tmp_path / "a.report.json",
                                             subdir="reports",
                                             suffix=".report.json")):
        with pytest.raises(ProtectedRootsError):
            mk()
    assert runner.main(["summary", str(report),
                        "--out", str(tmp_path / "d.json")]) == 1
    err = capsys.readouterr().err
    assert "protected-roots file cannot be used" in err
    assert "cannot be checked" not in err and str(tmp_path) not in err


def test_an_empty_roots_file_means_no_roots(tmp_path, monkeypatch):
    f = tmp_path / "empty"
    f.write_text("# no roots on this machine\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(f))
    assert _protected_roots() == []


def test_the_default_path_may_be_absent(tmp_path, monkeypatch):
    """The GPU container has no ~/.claude at all."""
    monkeypatch.delenv("SINDRI_PROTECTED_PATHS")
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    assert _protected_roots() == []


# ---- finding 5b: a skipped root rule is visible ---------------------------

def test_skipping_the_root_rule_says_so_once(tmp_path, monkeypatch, capsys):
    """With no roots file the class B/C location rule is skipped (operator
    decision 3). On a machine that SHOULD have roots that is a silent
    misconfiguration, so it is announced -- once, in a fixed sentence."""
    monkeypatch.setattr(outputs, "_NO_ROOTS_ANNOUNCED", False)
    # No roots: conftest points SINDRI_PROTECTED_PATHS at an EMPTY file
    # (an explicitly named missing file fails closed).
    check_client_file_out(tmp_path / "a.report.json", subdir="reports",
                          suffix=".report.json")
    check_client_dir_out = outputs.check_client_dir_out
    check_client_dir_out(tmp_path / "r", subdir="runs", legacy=("*.pred.json",))
    err = capsys.readouterr().err
    assert err == "no protected roots configured: root rule skipped\n"


def test_no_notice_when_roots_are_configured(commented_roots, monkeypatch, capsys):
    monkeypatch.setattr(outputs, "_NO_ROOTS_ANNOUNCED", False)
    client, _ = commented_roots
    check_client_file_out(client / "reports" / "a.report.json",
                          subdir="reports", suffix=".report.json")
    assert capsys.readouterr().err == ""
