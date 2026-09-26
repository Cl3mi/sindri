"""score --policy-check writes counts to a file an agent may read, and leaves
the scored report byte-identical: it is a diagnostic, not a scoring mode."""
import json

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


def _score(tmp_path, run, gold_dir, *extra):
    return runner.main(["score", "--run", str(run), "--gold", str(gold_dir),
                        "--name", "t", "--out", str(tmp_path / "r.json"),
                        *extra])


def test_policy_check_writes_counts_and_keeps_the_report(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    plain = (tmp_path / "r.json").read_text()
    out = tmp_path / "policy.json"
    assert _score(tmp_path, run, gold_dir, "--policy-check",
                  "--policy-out", str(out)) == 0
    assert (tmp_path / "r.json").read_text() == plain
    r = json.loads(out.read_text())
    assert set(r["flag_rules"]) and set(r["drop_rules"])
    assert "joint" not in r


def test_joint_set_from_the_command_line(tmp_path):
    run, gold_dir = _write(tmp_path)
    out = tmp_path / "policy.json"
    assert _score(tmp_path, run, gold_dir, "--policy-check",
                  "--policy-out", str(out), "--flag-rules", "nondim_kind",
                  "--drop-rules", "empty_read") == 0
    r = json.loads(out.read_text())
    assert r["joint"]["flag_rules"] == ["nondim_kind"]
    assert r["joint"]["drop_rules"] == ["empty_read"]


def test_unknown_rule_name_is_refused_before_scoring(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir, "--policy-check",
                  "--flag-rules", "no_such_rule") == 1
    assert not (tmp_path / "r.json").exists()


def test_reapply_policy_scores_with_the_active_rules_and_says_so(
        tmp_path, monkeypatch):
    from app.pipeline import policy_rules as pr
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ("empty_read",))
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir, "--reapply-policy") == 0
    r = json.loads((tmp_path / "r.json").read_text())
    assert r["reapplied_policy"] == {"flag_rules": ["nondim_kind"],
                                     "drop_rules": ["empty_read"],
                                     "reparsed": True}
    assert r["taxonomy"].get("false_detection", 0) == 0   # empty read dropped
    assert r["taxonomy"].get("escaped_error", 0) == 0     # gdt row flagged


def test_plain_score_records_no_reapplied_policy(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    r = json.loads((tmp_path / "r.json").read_text())
    assert r.get("reapplied_policy") is None


def test_reapply_with_no_rules_and_unchanged_parser_is_identity(
        tmp_path, monkeypatch):
    """The gate: with nothing active, reapplying must reproduce the plain
    score exactly -- otherwise the reconstruction itself moves numbers."""
    from app.pipeline import policy_rules as pr
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ())
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ())
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    plain = json.loads((tmp_path / "r.json").read_text())
    assert _score(tmp_path, run, gold_dir, "--reapply-policy") == 0
    re = json.loads((tmp_path / "r.json").read_text())
    assert re["taxonomy"] == plain["taxonomy"]
    assert re["mean_review_cost"] == plain["mean_review_cost"]
