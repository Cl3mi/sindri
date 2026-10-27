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


# ---- class B-file: score --out, split --out, variants --out ---------------

REPORT = ".report.json"


def _plain_score(tmp_path, out, *extra):
    run, gold = _score_inputs(tmp_path, "phantom")
    return runner.main(["score", "--run", str(run), "--gold", str(gold),
                        "--name", "t", "--out", str(out), *extra])


def test_score_report_lands_in_reports_and_replaces_only_a_report(
        tmp_path, roots, capsys):
    out = roots / "reports" / f"n{REPORT}"
    assert _plain_score(tmp_path, out) == 0
    assert _carries_marker(out, "run-report")
    assert out.stat().st_mode & 0o777 == 0o600       # client values
    assert _plain_score(tmp_path, out) == 0          # re-score: marker
    old = roots / "reports" / f"old{REPORT}"         # written before H1
    old.write_text('{"schema_version": 1, "run_name": "old"}')
    assert _plain_score(tmp_path, old) == 1
    assert old.read_text() == '{"schema_version": 1, "run_name": "old"}'
    _no_leak(capsys, out, old)


@pytest.mark.parametrize("where", ["gold", "grandchild", "root", "outside",
                                   "suffix"])
def test_score_report_refused_outside_reports(where, tmp_path, roots, capsys):
    out = {"gold": roots / "gold" / f"x{REPORT}",
           "grandchild": roots / "reports" / "sub" / f"x{REPORT}",
           "root": roots / f"x{REPORT}",
           "outside": tmp_path / f"x{REPORT}",
           "suffix": roots / "reports" / "x.json"}[where]
    assert _plain_score(tmp_path, out) == 1
    assert not out.exists()
    _no_leak(capsys, out)


def test_score_report_refuses_a_link_to_gold(tmp_path, roots, capsys):
    gold = _gold_standin(roots)
    before = gold.read_bytes()
    link = roots / "reports" / f"x{REPORT}"
    link.symlink_to(gold)
    assert _plain_score(tmp_path, link) == 1
    assert gold.read_bytes() == before
    _no_leak(capsys, link, gold)


def test_score_report_checked_before_scoring(tmp_path, roots, capsys):
    """Refused with no digest written either: the check runs first."""
    digest = tmp_path / "scratch" / "p.json"
    assert _plain_score(tmp_path, roots / "gold" / f"x{REPORT}",
                        "--phantom-profile", "--phantom-out", str(digest)) == 1
    assert not digest.exists()


def test_score_report_may_not_go_into_its_own_run_dir(tmp_path, capsys):
    """No roots configured (the conftest default), so only the input rule
    can refuse this one."""
    run, _ = _score_inputs(tmp_path, "phantom")
    out = run / f"x{REPORT}"
    assert _plain_score(tmp_path, out) == 1
    assert not out.exists()


def test_score_report_no_roots_mode_still_works(tmp_path, capsys):
    out = tmp_path / "reports" / f"n{REPORT}"
    assert _plain_score(tmp_path, out) == 0
    assert _carries_marker(out, "run-report")
    assert _plain_score(tmp_path, tmp_path / "n.json") == 1     # suffix holds


def _split(tmp_path, out):
    _, gold = _score_inputs(tmp_path, "phantom")
    return runner.main(["split", "--gold", str(gold), "--out", str(out)])


def test_split_written_once_into_meta_and_then_frozen(tmp_path, roots, capsys):
    """The split is frozen (6d174d5e4f1b9228): re-running split must never
    replace it, however the file is marked."""
    out = roots / "meta" / "splits.json"
    assert _split(tmp_path, out) == 0
    first = out.read_bytes()
    assert out.stat().st_mode & 0o777 == 0o600
    assert _split(tmp_path, out) == 1
    assert out.read_bytes() == first
    for bad in (roots / "gold" / "splits.json", tmp_path / "splits.json",
                roots / "meta" / "splits.txt"):
        assert _split(tmp_path, bad) == 1
        assert not bad.exists()
    _no_leak(capsys, out)


def test_split_no_roots_mode_still_never_overwrites(tmp_path, capsys):
    out = tmp_path / "meta" / "splits.json"
    assert _split(tmp_path, out) == 0
    assert _split(tmp_path, out) == 1


def _variants(tmp_path, out):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir(exist_ok=True)
    return runner.main(["variants", "--pdfs", str(pdfs), "--out", str(out)])


def test_variants_written_once_into_meta(tmp_path, roots, capsys):
    out = roots / "meta" / "variants.txt"
    assert _variants(tmp_path, out) == 0
    first = out.read_bytes()
    assert _variants(tmp_path, out) == 1
    assert out.read_bytes() == first
    for bad in (roots / "runs" / "variants.txt", roots / "meta" / "v.json"):
        assert _variants(tmp_path, bad) == 1
        assert not bad.exists()
    _no_leak(capsys, out)


# ---- class C: ingest, predict, verify ---------------------------------------

from app.eval.outputs import DIR_MARKER  # noqa: E402
from tests.eval.test_runner_e2e import (_fake_predict, _no_model,  # noqa: E402
                                        _setup_corpus, _stub_dump)


def _corpus(tmp_path):
    base = tmp_path / "corpus"
    if not base.exists():
        base.mkdir()
        _setup_corpus(base)
    return base / "pdfs", base / "excel"


def _ingest(tmp_path, out):
    pdfs, excel = _corpus(tmp_path)
    return runner.main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                        "--out", str(out)])


def test_ingest_claims_its_dir_and_may_re_ingest(tmp_path, roots, capsys):
    out = roots / "gold" / "g1"
    assert _ingest(tmp_path, out) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted([DIR_MARKER, "SYNA.gold.json", "SYNB.gold.json",
                            "doc_id_map.json"])
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in out.iterdir())
    assert _ingest(tmp_path, out) == 0                      # ours: re-ingest
    _no_leak(capsys, out)


def test_ingest_into_the_gold_folder_itself(tmp_path, roots):
    """The documented layout: `ingest --out <root>/gold`."""
    assert _ingest(tmp_path, roots / "gold") == 0
    assert (roots / "gold" / "SYNA.gold.json").is_file()


def test_an_empty_ingest_writes_nothing(tmp_path, roots, capsys):
    """Review round 2 (D): with no pdf/excel pair an ingest used to stamp
    the directory and rewrite doc_id_map.json as {} -- silently erasing
    the hash-to-drawing trace of a populated gold dir."""
    out = roots / "gold" / "g1"
    assert _ingest(tmp_path, out) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    empty = tmp_path / "empty"
    (empty / "pdfs").mkdir(parents=True)
    (empty / "excel").mkdir()
    capsys.readouterr()
    for target in (out, roots / "gold" / "fresh"):
        assert runner.main(["ingest", "--pdfs", str(empty / "pdfs"),
                            "--excel", str(empty / "excel"),
                            "--out", str(target)]) == 1
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert not (roots / "gold" / "fresh").exists()
    cap = _no_leak(capsys, out, empty)
    assert "no drawing/sheet pair" in cap.err


def test_the_gold_folder_keeps_marked_sub_ingests(tmp_path, roots, capsys):
    """`ingest --out <root>/gold/<name>` leaves a marked subdirectory in
    <root>/gold; ingesting into <root>/gold itself must still work. An
    UNMARKED subdirectory is still foreign and refuses it."""
    assert _ingest(tmp_path, roots / "gold" / "originals") == 0
    assert _ingest(tmp_path, roots / "gold") == 0
    (roots / "gold" / "stray").mkdir()
    assert _ingest(tmp_path, roots / "gold") == 1
    _no_leak(capsys, roots)


def test_ingest_refusals_leave_everything_untouched(tmp_path, roots, capsys):
    standin = _gold_standin(roots)
    before = standin.read_bytes()
    linked = roots / "gold" / "linked"
    linked.mkdir()
    (linked / "SYNA.gold.json").symlink_to(standin)
    foreign = roots / "gold" / "foreign"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("x")
    (roots / "gold" / "dirlink").symlink_to(roots / "reports")
    for bad in (roots / "runs" / "g1", linked, foreign, roots / "gold" / "dirlink",
                roots / "gold" / "a" / "b", tmp_path / "elsewhere"):
        assert _ingest(tmp_path, bad) == 1
    assert standin.read_bytes() == before
    assert (foreign / "notes.txt").read_text() == "x"
    assert sorted(p.name for p in foreign.iterdir()) == ["notes.txt"]
    assert list((roots / "reports").iterdir()) == []
    _no_leak(capsys, linked, foreign)


def _predict(tmp_path, monkeypatch, out, calls=None):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, _ = _corpus(tmp_path)
    _no_model(monkeypatch)

    def fake(pdf_path, doc_id, dpi, backend, config, work_dir,
             detect_only=False):
        # What extract really leaves behind: a render under _work/<doc>/.
        d = Path(work_dir) / doc_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "page.png").write_bytes(b"png")
        if calls is not None:
            calls.append(doc_id)
        return _stub_dump(doc_id, config)
    _fake_predict(monkeypatch, fake)
    return runner.main(["predict", "--pdfs", str(pdfs), "--out", str(out)])


def test_predict_claims_its_run_dir_and_resumes(tmp_path, roots, monkeypatch,
                                                capsys):
    out = roots / "runs" / "r1"
    calls = []
    assert _predict(tmp_path, monkeypatch, out, calls) == 0
    assert (out / DIR_MARKER).is_file()
    assert (out / "SYNA.pred.json").stat().st_mode & 0o777 == 0o600
    calls.clear()
    capsys.readouterr()
    assert _predict(tmp_path, monkeypatch, out, calls) == 0    # with _work/
    assert calls == []


def test_predict_resumes_a_legacy_run(tmp_path, roots, monkeypatch, capsys):
    """A run written before H1: dumps, a .complete marker and _work/, no
    .sindri-output. Resume on the GPU host depends on it passing."""
    out = roots / "runs" / "old"
    assert _predict(tmp_path, monkeypatch, out) == 0
    (out / DIR_MARKER).unlink()
    (out / ".complete").write_text("2026-10-01T00:00:00Z\n")
    calls = []
    capsys.readouterr()
    assert _predict(tmp_path, monkeypatch, out, calls) == 0
    assert calls == []
    assert json.loads(capsys.readouterr().out)["skipped"] == 2


def test_predict_refuses_foreign_or_misplaced_dirs(tmp_path, roots, monkeypatch,
                                                  capsys):
    foreign = roots / "runs" / "foreign"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("x")
    for bad in (foreign, roots / "gold" / "r1", roots / "runs" / "a" / "b",
                roots / "reports"):
        assert _predict(tmp_path, monkeypatch, bad) == 1
    assert sorted(p.name for p in foreign.iterdir()) == ["notes.txt"]
    assert not (roots / "gold" / "r1").exists()
    _no_leak(capsys, foreign)


def test_predict_no_roots_mode_like_the_gpu_container(tmp_path, monkeypatch):
    """run_gpu_queue.sh: `mkdir -p /data/runs/<run>` then predict into it,
    in a container with no roots file."""
    out = tmp_path / "data" / "runs" / "r"
    out.mkdir(parents=True)
    assert _predict(tmp_path, monkeypatch, out) == 0
    assert _predict(tmp_path, monkeypatch, out) == 0
    (tmp_path / "data" / "runs" / "x").mkdir()
    (tmp_path / "data" / "runs" / "x" / "notes.txt").write_text("x")
    assert _predict(tmp_path, monkeypatch, tmp_path / "data" / "runs" / "x") == 1


def _verify_setup(tmp_path, monkeypatch, src):
    from tests.eval.test_runner_verify import _setup as vsetup
    s, pdfs, stub = vsetup(tmp_path, monkeypatch)
    return s, pdfs


def test_verify_claims_its_dir_and_never_writes_its_input(tmp_path, roots,
                                                         monkeypatch, capsys):
    src, pdfs = _verify_setup(tmp_path, monkeypatch, None)
    out = roots / "runs" / "v1"
    argv = ["verify", "--run", str(src), "--pdfs", str(pdfs), "--out"]
    assert runner.main(argv + [str(out)]) == 0
    assert (out / DIR_MARKER).is_file() and (out / "D.pred.json").is_file()
    assert runner.main(argv + [str(out)]) == 0                  # resume
    before = sorted((p.name, p.read_bytes()) for p in src.iterdir())
    assert runner.main(argv + [str(src)]) == 1                  # --out == --run
    assert sorted((p.name, p.read_bytes()) for p in src.iterdir()) == before
    assert runner.main(argv + [str(roots / "gold" / "v")]) == 1
    _no_leak(capsys, out, src)


@pytest.mark.parametrize("out_flag,check_flag", [
    ("--policy-out", "--policy-check"), ("--gentol-out", "--gentol-check"),
    ("--phantom-out", "--phantom-profile"), ("--drop-out", "--drop-check")])
def test_a_digest_flag_without_its_check_is_refused(out_flag, check_flag,
                                                    tmp_path, roots, capsys):
    """Review round 2 (F): `--phantom-out x` without `--phantom-profile`
    used to be silently ignored -- it reads as if a digest had been written,
    exactly like --manifest without --reviewer-grade, which already fails."""
    out = tmp_path / "scratch" / "x.json"
    argv = _score_argv(tmp_path, roots, "phantom", out_flag, str(out))
    assert runner.main(argv) == 1
    assert not out.exists()
    assert not (roots / "reports" / "t.report.json").exists()
    cap = _no_leak(capsys, out)
    assert f"{out_flag} needs {check_flag}" in cap.err
