"""Refusing to score a run that has not finished predicting.

2026-09-14: `r3-cropctx` and `r3-awqtest` were pulled and scored while both were
still running. `score` scored 5 of 15 documents and 7 of 19 and printed headline
numbers -- 118.80 against a 133.93 control -- that read exactly like results.
They were an artefact of which documents happened to have finished.

The warning that should have caught it structurally could not. `_cmd_score`
selected split members from `set(gold) & set(dumps)`, so a split member with no
dump vanished BEFORE selection, and the "gold docs without dumps" warning was
computed over the whole gold set -- which on a dev run always lists the ~80
documents that are simply in other splits. A warning that fires identically on
every healthy run carries no signal.

The split is the contract: it names the documents a run is supposed to cover.
Missing one of those is a partial run; gold outside the split is not.
"""
import json

import pytest

from app.eval.models import GoldDoc
from app.eval.runner import main

from tests.eval.test_score_scope_policy import _corpus


def _splits(tmp_path, dev):
    """A splits file naming `dev` as the dev split."""
    path = tmp_path / "splits.json"
    path.write_text(json.dumps({"schema_version": 1, "seed": 13, "variants": [],
                                "train": [], "dev": dev, "test": []}))
    return path


def _score(tmp_path, run_dir, gold_dir, splits, extra=()):
    return main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "partial", "--out", str(tmp_path / "r.report.json"),
                 "--splits", str(splits), "--split", "dev", *extra])


def test_a_split_member_without_a_dump_refuses_to_score(tmp_path):
    """The defect this exists for. SYNB is in the dev split and has no dump --
    the shape of a run still in flight -- and scoring SYNA alone produces a
    number over a different document set than every control it will be compared
    against."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    (run_dir / "SYNB.pred.json").unlink()

    assert _score(tmp_path, run_dir, gold_dir, _splits(tmp_path, ["SYNA", "SYNB"])) == 1


def test_the_refusal_says_how_many_and_names_the_flag(tmp_path):
    """It has to be actionable at 2am: how many are missing, and the one way to
    override it deliberately."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    (run_dir / "SYNB.pred.json").unlink()

    with _capture() as err:
        _score(tmp_path, run_dir, gold_dir, _splits(tmp_path, ["SYNA", "SYNB"]))
    msg = err.getvalue()
    assert "1 of 2" in msg, msg
    assert "--allow-partial" in msg, msg


def test_gold_outside_the_split_is_not_a_partial_run(tmp_path):
    """The case that made the old warning useless: a dev run always has gold for
    documents in other splits. That is normal and must stay silent."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    assert _score(tmp_path, run_dir, gold_dir, _splits(tmp_path, ["SYNA"])) == 0


def test_allow_partial_scores_anyway_and_records_it(tmp_path):
    """A deliberate peek at a running job stays possible -- the detect-only
    timing diagnostic needed exactly that -- but the report must carry the fact,
    because a partial number is the one thing that must never be quotable later
    without its caveat."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    (run_dir / "SYNB.pred.json").unlink()

    assert _score(tmp_path, run_dir, gold_dir, _splits(tmp_path, ["SYNA", "SYNB"]),
                  extra=["--allow-partial"]) == 0
    report = json.loads((tmp_path / "r.report.json").read_text())
    assert report["missing_dumps"] == 1


def test_a_complete_run_records_zero_not_none(tmp_path):
    """Zero means measured-and-complete. None means a report predating the
    check, which is every report in the campaign -- the distinction
    DocScore.frame_origin_frac exists to preserve."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    assert _score(tmp_path, run_dir, gold_dir, _splits(tmp_path, ["SYNA", "SYNB"])) == 0
    report = json.loads((tmp_path / "r.report.json").read_text())
    assert report["missing_dumps"] == 0


def _capture():
    import contextlib
    import io
    buf = io.StringIO()
    return _Ctx(contextlib.redirect_stderr(buf), buf)


class _Ctx:
    def __init__(self, cm, buf):
        self.cm, self.buf = cm, buf

    def __enter__(self):
        self.cm.__enter__()
        return self.buf

    def __exit__(self, *a):
        return self.cm.__exit__(*a)
