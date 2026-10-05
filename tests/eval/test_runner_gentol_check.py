"""score --gentol-check writes counts to a file an agent may read and leaves the
scored report byte-identical: a diagnostic, not a scoring mode -- the same
contract as --policy-check."""
import json

from app.eval import runner
from app.eval.dump import save_dump
from tests.eval.test_gentol_check import _doc


def _write(tmp_path):
    dump, gold = _doc()
    run = tmp_path / "run"
    save_dump(dump, run)
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "D.gold.json").write_text(gold.model_dump_json())
    return run, gold_dir


def _score(tmp_path, run, gold_dir, *extra):
    return runner.main(["score", "--run", str(run), "--gold", str(gold_dir),
                        "--name", "t", "--out", str(tmp_path / "r.json"),
                        "--show-ids", *extra])


def test_gentol_check_writes_counts_and_keeps_the_report(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    plain = (tmp_path / "r.json").read_text()
    out = tmp_path / "gentol.json"
    assert _score(tmp_path, run, gold_dir, "--gentol-check",
                  "--gentol-out", str(out)) == 0
    assert (tmp_path / "r.json").read_text() == plain
    r = json.loads(out.read_text())
    assert r["would_fix"] == 1 and r["would_break"] == 5
    assert r["verdict"] == "FAIL"


def test_gentol_check_prices_from_the_original_dumps_under_reapply_too(tmp_path):
    """--reapply-policy replaces the dumps before scoring; the check must
    still build its own control and arm from the ORIGINALS, or it would
    re-apply today's code twice and price a different thing."""
    run, gold_dir = _write(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert _score(tmp_path, run, gold_dir, "--gentol-check",
                  "--gentol-out", str(a)) == 0
    assert _score(tmp_path, run, gold_dir, "--reapply-policy",
                  "--gentol-check", "--gentol-out", str(b)) == 0
    assert json.loads(a.read_text()) == json.loads(b.read_text())
