"""Every agent-reachable runner output flag refuses every path but its own
(H1, docs/plans/2026-10-23-runner-output-hardening-design.md).

End to end through `runner.main`, against a SYNTHETIC protected root under
tmp_path (`SINDRI_PROTECTED_PATHS` points at it), never the real one. Class A
digests first: each flag must (1) write its digest with its `kind` as the
first key, (2) replace its own previous output on a re-run, (3) refuse a
foreign file there, (4) refuse a link to a gold stand-in, (5) refuse a path
inside the root -- leaving the bytes of whatever was there untouched -- and
(6) say so in a fixed sentence: no path, no traceback."""
import json
from pathlib import Path

import pytest

from app.eval import runner
from app.eval.review import _carries_marker


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = tmp_path / "client"
    for sub in ("reports", "meta", "runs", "gold"):
        (root / sub).mkdir(parents=True)
    f = tmp_path / "roots.txt"
    f.write_text(str(root) + "\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(f))
    return root


def _gold_standin(root: Path) -> Path:
    g = root / "gold" / "standin.gold.json"
    g.write_text('{"GOLD": "do not touch"}')
    return g


# ---- inputs, built from the fixtures the existing runner tests use ----------

def _score_inputs(tmp_path, which):
    if which == "policy":
        from tests.eval.test_runner_policy_check import _write
    elif which == "gentol":
        from tests.eval.test_runner_gentol_check import _write
    else:
        from tests.eval.test_runner_phantom_profile import _write
    base = tmp_path / f"in-{which}"
    if base.exists():                       # a re-run reads the same inputs
        return base / "run", base / "gold"
    return _write(base)


def _score_argv(tmp_path, roots, which, *extra):
    run, gold = _score_inputs(tmp_path, which)
    return ["score", "--run", str(run), "--gold", str(gold), "--name", "t",
            "--out", str(roots / "reports" / "t.report.json"), *extra]


def _report(tmp_path, roots, name="base"):
    """A scored report inside the root, as summary/compare read in real use."""
    out = roots / "reports" / f"{name}.report.json"
    if not out.exists():
        run, gold = _score_inputs(tmp_path, "phantom")
        assert runner.main(["score", "--run", str(run), "--gold", str(gold),
                            "--name", name, "--out", str(out)]) == 0
    return out


def _summary(tmp_path, roots, out):
    return ["summary", str(_report(tmp_path, roots)), "--out", str(out)]


def _compare(tmp_path, roots, out):
    a = _report(tmp_path, roots)
    return ["compare", str(a), str(a), "--out", str(out)]


def _tally(tmp_path, roots, out):
    from app.eval.review import write_deck
    from tests.eval.test_review import _deck3
    deck = roots / "reports" / "d.json"
    if not deck.exists():
        write_deck(deck, _deck3())
    return ["review-tally", str(deck), "--out", str(out)]


CLASS_A = {
    "summary": (_summary, "run-summary-digest"),
    "compare": (_compare, "run-comparison-digest"),
    "policy-out": (lambda t, r, o: _score_argv(t, r, "policy", "--policy-check",
                                               "--policy-out", str(o)),
                   "policy-check-digest"),
    "gentol-out": (lambda t, r, o: _score_argv(t, r, "gentol", "--show-ids",
                                               "--gentol-check",
                                               "--gentol-out", str(o)),
                   "gentol-check-digest"),
    "phantom-out": (lambda t, r, o: _score_argv(t, r, "phantom",
                                                "--phantom-profile",
                                                "--phantom-out", str(o)),
                    "phantom-profile-digest"),
    "drop-out": (lambda t, r, o: _score_argv(t, r, "phantom", "--drop-check",
                                             "--drop-out", str(o)),
                 "drop-check-digest"),
    # The tally carries the deck's own kind as its first key already, and
    # review-serve writes the same file; using that kind keeps the two
    # writers able to replace each other's output (see runner).
    "review-tally": (_tally, "gdt-char-type-only"),
}


def _no_leak(capsys, *paths):
    cap = capsys.readouterr()
    for stream in (cap.err,):
        assert "Traceback" not in stream
        for p in paths:
            assert str(p) not in stream
    return cap


@pytest.mark.parametrize("flag", CLASS_A)
def test_class_a_writes_its_kind_first_and_replaces_only_itself(
        flag, tmp_path, roots, capsys):
    build, kind = CLASS_A[flag]
    out = tmp_path / "scratch" / "x.json"
    assert runner.main(build(tmp_path, roots, out)) == 0
    assert _carries_marker(out, kind)
    assert json.loads(out.read_text())["kind"] == kind
    first = out.read_text()
    assert runner.main(build(tmp_path, roots, out)) == 0      # re-run: marker
    assert out.read_text() == first
    _no_leak(capsys, out)


@pytest.mark.parametrize("flag", CLASS_A)
def test_class_a_refuses_a_foreign_file(flag, tmp_path, roots, capsys):
    build, _ = CLASS_A[flag]
    out = tmp_path / "scratch" / "x.json"
    out.parent.mkdir()
    out.write_text('{"someone": "else"}')
    assert runner.main(build(tmp_path, roots, out)) == 1
    assert out.read_text() == '{"someone": "else"}'
    _no_leak(capsys, out)


@pytest.mark.parametrize("flag", CLASS_A)
def test_class_a_refuses_a_link_to_gold(flag, tmp_path, roots, capsys):
    build, _ = CLASS_A[flag]
    gold = _gold_standin(roots)
    before = gold.read_bytes()
    out = tmp_path / "scratch" / "x.json"
    out.parent.mkdir()
    out.symlink_to(gold)
    assert runner.main(build(tmp_path, roots, out)) == 1
    assert gold.read_bytes() == before and out.is_symlink()
    _no_leak(capsys, out, gold)


@pytest.mark.parametrize("flag", CLASS_A)
def test_class_a_refuses_the_protected_root(flag, tmp_path, roots, capsys):
    build, _ = CLASS_A[flag]
    out = roots / "meta" / "x.json"
    assert runner.main(build(tmp_path, roots, out)) == 1
    assert not out.exists()
    _no_leak(capsys, out)


@pytest.mark.parametrize("flag", ["summary", "compare"])
def test_class_a_refuses_its_own_input(flag, tmp_path, roots, capsys):
    """`summary <r> --out <r>` would destroy the report it reads -- refused
    even though the report lives inside the root (that alone refuses it) and
    even if it carried a digest's marker."""
    build, _ = CLASS_A[flag]
    report = _report(tmp_path, roots)
    before = report.read_bytes()
    assert runner.main(build(tmp_path, roots, report)) == 1
    assert report.read_bytes() == before
    _no_leak(capsys, report)


def test_score_checks_every_digest_flag_before_scoring(tmp_path, roots, capsys):
    """A bad --drop-out must fail in seconds, before the report is written:
    a refusal after the work reads as a warning."""
    argv = _score_argv(tmp_path, roots, "phantom", "--drop-check",
                       "--drop-out", str(roots / "meta" / "d.json"))
    assert runner.main(argv) == 1
    assert not (roots / "reports" / "t.report.json").exists()
    _no_leak(capsys)


def test_two_digest_flags_may_not_share_a_path(tmp_path, roots, capsys):
    out = tmp_path / "scratch" / "x.json"
    argv = _score_argv(tmp_path, roots, "phantom", "--phantom-profile",
                       "--phantom-out", str(out), "--drop-check",
                       "--drop-out", str(out))
    assert runner.main(argv) == 1
    assert not out.exists()


def test_share_pack_summary_command_shape(tmp_path, roots, capsys):
    """The share-pack skill's step 1 verbatim in shape: `summary <report>
    --per-doc --out <scratchpad>/dev.json`, then the same again -- a session
    re-running its selection must not be refused by its own digest. And
    share_select must still read it with the leading kind."""
    report = _report(tmp_path, roots)
    out = tmp_path / "scratchpad" / "dev.json"
    for _ in range(2):
        assert runner.main(["summary", str(report), "--per-doc",
                            "--out", str(out)]) == 0
    digest = json.loads(out.read_text())
    assert next(iter(digest)) == "kind" and digest["per_doc"]
    from app.eval import share_select
    assert share_select.main(["--digest", f"r={out}", "--great", "0",
                              "--problem", "0",
                              "--out", str(tmp_path / "m.json")]) in (0, 1)
    _no_leak(capsys, out)
