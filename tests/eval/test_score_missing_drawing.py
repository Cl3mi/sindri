"""A gold document with no drawing is not an unfinished run.

2026-09-15: `SPLIT=test ./rescore_onepage.sh r3-awqtest` was refused with
"1 of 20 document(s) in split 'test' have no prediction dump -- this run has not
finished predicting ... Wait for the run". The run HAD finished: `.complete` was
written, predict reported 19 predicted / 0 failed, and the host held all 99
drawings. The test split simply names 20 gold documents and the corpus contains
a drawing for only 19 of them.

So the partial-run guard was right that a dump was missing and wrong about why,
and told the operator to wait for something that was never going to happen. A
guard that misdiagnoses costs exactly what the warning it replaced cost.

The two causes need different answers:

* no DRAWING -> the document can never be predicted. That is structural, like
  multi-sheet and oversized: exclude it, print it, and score the rest. Charging
  its gold rows as missed at w=10 would measure a data-delivery gap rather than
  the model.
* a drawing but no DUMP -> the run really is unfinished. Refuse.

`predict` already selects on the drawings it can find, which is why the run
stopped at 19 without reporting a failure.
"""
import json

from app.eval.runner import main

from tests.eval.test_score_scope_policy import _corpus


def _splits(tmp_path, dev):
    path = tmp_path / "splits.json"
    path.write_text(json.dumps({"schema_version": 1, "seed": 13, "variants": [],
                                "train": [], "dev": dev, "test": []}))
    return path


def _score(tmp_path, run_dir, gold_dir, pdfs, splits, extra=()):
    args = ["score", "--run", str(run_dir), "--gold", str(gold_dir),
            "--name", "nodrawing", "--out", str(tmp_path / "r.report.json"),
            "--splits", str(splits), "--split", "dev", *extra]
    if pdfs is not None:
        args += ["--pdfs", str(pdfs)]
    return main(args)


def test_a_gold_doc_with_no_drawing_is_excluded_not_refused(tmp_path, capsys):
    """The case that blocked the test split. SYNB has gold and no drawing, so
    no dump for it can ever exist -- scoring the other document is the correct
    answer, not a refusal that says "wait"."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    (run_dir / "SYNB.pred.json").unlink()
    next(pdfs.glob("SYNB.*")).unlink()

    rc = _score(tmp_path, run_dir, gold_dir, pdfs,
                _splits(tmp_path, ["SYNA", "SYNB"]))

    assert rc == 0
    err = capsys.readouterr().err
    assert "no drawing" in err, err
    report = json.loads((tmp_path / "r.report.json").read_text())
    assert report["missing_dumps"] == 0, (
        "a document that cannot be predicted is an exclusion, not an "
        "incomplete run")
    assert len(report["doc_scores"]) == 1


def test_a_drawing_without_a_dump_still_refuses(tmp_path):
    """The other half must keep working: the drawing is there, so the run
    genuinely has not finished and scoring it would compare a smaller document
    set against every control."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    (run_dir / "SYNB.pred.json").unlink()

    assert _score(tmp_path, run_dir, gold_dir, pdfs,
                  _splits(tmp_path, ["SYNA", "SYNB"])) == 1


def test_without_the_drawings_the_refusal_admits_it_cannot_tell(tmp_path, capsys):
    """--pdfs is optional for score. Without it the two causes are
    indistinguishable, and the message must say so rather than assert the
    timing one -- which is the mistake this whole file exists for."""
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    (run_dir / "SYNB.pred.json").unlink()
    next(pdfs.glob("SYNB.*")).unlink()

    assert _score(tmp_path, run_dir, gold_dir, None,
                  _splits(tmp_path, ["SYNA", "SYNB"])) == 1
    err = capsys.readouterr().err
    assert "--pdfs" in err, err
