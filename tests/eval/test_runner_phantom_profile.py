"""score --phantom-profile writes counts to a file an agent may read and leaves
the scored report byte-identical -- the same contract as --policy-check."""
import json

from app.eval import runner
from app.eval.dump import save_dump
from tests.eval.test_phantom_profile import _setup


def _write(tmp_path):
    dumps, golds = _setup()
    run = tmp_path / "run"
    save_dump(dumps["D"], run)
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "D.gold.json").write_text(golds["D"].model_dump_json())
    return run, gold_dir


def _score(tmp_path, run, gold_dir, *extra):
    return runner.main(["score", "--run", str(run), "--gold", str(gold_dir),
                        "--name", "t", "--out", str(tmp_path / "r.json"),
                        *extra])


def test_phantom_profile_writes_counts_and_keeps_the_report(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    plain = (tmp_path / "r.json").read_text()
    out = tmp_path / "profile.json"
    assert _score(tmp_path, run, gold_dir, "--phantom-profile",
                  "--phantom-out", str(out)) == 0
    assert (tmp_path / "r.json").read_text() == plain
    r = json.loads(out.read_text())
    assert r["totals"]["phantom"] == 1 and r["reconciles"] is True


def test_phantom_profile_is_accepted_with_reapply_policy(tmp_path):
    """On train the dumps predate the policy, so the profile must describe
    what TODAY'S code would deliver: it profiles the dumps after
    --reapply-policy has rewritten them, the same ones the report scores."""
    run, gold_dir = _write(tmp_path)
    out = tmp_path / "profile.json"
    assert _score(tmp_path, run, gold_dir, "--reapply-policy",
                  "--phantom-profile", "--phantom-out", str(out)) == 0
    assert json.loads(out.read_text())["reconciles"] is True
