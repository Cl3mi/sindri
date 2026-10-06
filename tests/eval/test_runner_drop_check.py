"""score --drop-check writes counts to a file and leaves the scored report
byte-identical; --drop-configs restricts it to the configuration selected on
train, which is all dev and test may price."""
import json

from app.eval import runner
from tests.eval.test_runner_phantom_profile import _score, _write


def test_drop_check_writes_counts_and_keeps_the_report(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    plain = (tmp_path / "r.json").read_text()
    out = tmp_path / "drops.json"
    assert _score(tmp_path, run, gold_dir, "--drop-check",
                  "--drop-out", str(out)) == 0
    assert (tmp_path / "r.json").read_text() == plain
    r = json.loads(out.read_text())
    assert len(r["configs"]) == 11 and r["selected"] == "conf_below_099"


def test_drop_configs_restricts_the_priced_set(tmp_path):
    run, gold_dir = _write(tmp_path)
    out = tmp_path / "drops.json"
    assert _score(tmp_path, run, gold_dir, "--drop-check", "--drop-configs",
                  "conf_below_099", "--drop-out", str(out)) == 0
    assert list(json.loads(out.read_text())["configs"]) == ["conf_below_099"]


def test_an_unknown_configuration_fails_before_scoring(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir, "--drop-check", "--drop-configs",
                  "conf_below_050") == 1
