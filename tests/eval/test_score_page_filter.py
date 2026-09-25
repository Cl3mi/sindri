"""Scoring only the drawings the pipeline can actually read.

`render_page` renders `page_index=0` and nothing else, so every gold
characteristic printed on sheet 2 of a multi-page drawing is a miss no model can
recover. Those rows are structural noise: they move `missed` (w=10, the heaviest
weight) for a reason no arm can ever address, and they sit in the denominator of
every recall number in the campaign.

The filter is OFF by default. Every committed measurement -- baseline-dev 173.05,
r3-awqcontrol 170.05, r3-nf4control 176.40, r3-loraread 172.00 -- was scored over
the whole dev split, and turning this on by default would silently redefine all
of them.

Comparability needs no new guard: report._check_comparable already raises
"doc set differs" when two reports do not carry the identical document list, so a
filtered report cannot be quietly compared against an unfiltered one.
"""
import json

import pytest

from app.eval.dump import save_dump
from app.eval.models import GoldDoc
from app.eval.runner import main

from tests.eval.test_runner_e2e import _perfect_dump, _setup_corpus


def _add_a_second_sheet(pdf_path):
    """Turn a synthetic single-sheet drawing into a two-sheet one, leaving sheet
    1 byte-identical so the only variable is the page count."""
    import fitz
    doc = fitz.open(str(pdf_path))
    doc.new_page(width=600, height=400)
    tmp = pdf_path.with_suffix(".tmp")
    doc.save(str(tmp))
    doc.close()
    tmp.replace(pdf_path)


def _corpus_with_one_multi_page_drawing(tmp_path):
    """SYNA stays one sheet, SYNB becomes two. Returns (pdfs, gold, run)."""
    pdfs, excel = _setup_corpus(tmp_path)
    _add_a_second_sheet(pdfs / ("SYNB" + ".p" + "df"))
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "base"
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    for path in sorted(gold_dir.glob("*.gold.json")):
        gold = GoldDoc.model_validate_json(path.read_text())
        save_dump(_perfect_dump(gold.doc_id, gold), run_dir)
    return pdfs, gold_dir, run_dir


def test_max_pages_excludes_a_multi_page_drawing_from_scoring(tmp_path):
    """The whole point: a two-sheet drawing must leave the corpus entirely, not
    be scored on the one sheet the renderer looked at."""
    pdfs, gold_dir, run_dir = _corpus_with_one_multi_page_drawing(tmp_path)
    out = tmp_path / "filtered.report.json"

    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--pdfs", str(pdfs), "--max-pages", "1",
                 "--name", "filtered", "--out", str(out)]) == 0

    report = json.loads(out.read_text())
    assert [d["doc_id"] for d in report["doc_scores"]] == ["SYNA"]


def test_scoring_without_the_filter_still_scores_every_document(tmp_path):
    """The default must stay byte-identical to what produced 173.05 / 170.05 /
    176.40 / 172.00. Turning the filter on by default would redefine all four."""
    pdfs, gold_dir, run_dir = _corpus_with_one_multi_page_drawing(tmp_path)
    out = tmp_path / "plain.report.json"

    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "plain", "--out", str(out)]) == 0

    report = json.loads(out.read_text())
    assert len(report["doc_scores"]) == 2
    assert report["max_pages"] is None


def test_the_filter_is_recorded_so_a_short_run_is_not_mistaken_for_a_partial_one(
        tmp_path):
    """n_docs=1 has two very different causes -- a page filter, or dumps that
    failed to arrive. Without the field a reader cannot tell a deliberate corpus
    from a broken pull, and `missing dumps` is a bug while `filtered` is not."""
    pdfs, gold_dir, run_dir = _corpus_with_one_multi_page_drawing(tmp_path)
    out = tmp_path / "filtered.report.json"
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--pdfs", str(pdfs), "--max-pages", "1",
                 "--name", "filtered", "--out", str(out)]) == 0

    assert json.loads(out.read_text())["max_pages"] == 1


def test_max_pages_without_the_drawings_fails_loudly(tmp_path, capsys):
    """Page counts come from the drawings themselves; there is nowhere else to
    read them. Scoring the full corpus while the operator believes it was
    filtered is the silent-wrong-corpus failure, so refuse instead.

    Exit 1 with an ERROR line, matching how score already refuses an empty
    gold/dump overlap. The message must name --pdfs, which is also what stops
    this test passing merely because argparse rejected an unknown flag."""
    pdfs, gold_dir, run_dir = _corpus_with_one_multi_page_drawing(tmp_path)
    out = tmp_path / "nope.report.json"

    rc = main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
               "--max-pages", "1", "--name", "nope", "--out", str(out)])

    assert rc == 1
    assert "--pdfs" in capsys.readouterr().err
    assert not out.exists(), "a refused score must not leave a report behind"
