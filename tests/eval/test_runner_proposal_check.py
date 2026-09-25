"""score --proposal-check needs --pdfs -- it renders originals to run OCR
against -- and must fail fast, before scoring, like --review-deck does."""
from app.eval import runner
from app.eval.dump import save_dump
from tests.eval.test_policy_check import _setup


def _write(tmp_path):
    dumps, golds = _setup()
    run = tmp_path / "run"
    save_dump(dumps["D"], run)
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "D.gold.json").write_text(golds["D"].model_dump_json())
    return run, gold_dir


def test_proposal_check_without_pdfs_fails_fast(tmp_path):
    run, gold_dir = _write(tmp_path)
    ret = runner.main(["score", "--run", str(run), "--gold", str(gold_dir),
                       "--name", "t", "--out", str(tmp_path / "r.json"),
                       "--proposal-check"])
    assert ret == 1
    assert not (tmp_path / "r.json").exists()
