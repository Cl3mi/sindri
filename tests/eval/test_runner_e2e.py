"""End-to-end on synthetic truth: build corpus -> ingest via CLI -> score a
hand-perturbed prediction set via CLI -> compare a run against itself."""
import json

import pytest

from app.eval.models import (GoldCharacteristic, GoldDoc, PredictionDump,
                             RunConfig, ReviewCostWeights)
from app.eval.dump import save_dump
from app.eval.runner import main, predict_one
from app.eval.synthetic import make_synthetic_doc
from app.models import Characteristic, ExtractionResult

RECORDS = {
    "SYNA": [
        GoldCharacteristic(balloon=1, position_pt=(120.0, 90.0),
                           char_type="Diameter", nominal="20",
                           upper_tol="0,1", lower_tol="-0,1"),
        GoldCharacteristic(balloon=2, position_pt=(340.0, 200.0),
                           char_type="Distance", nominal="5,5"),
    ],
    "SYNB": [
        GoldCharacteristic(balloon=1, position_pt=(200.0, 150.0),
                           char_type="Radius", nominal="2"),
        GoldCharacteristic(balloon=2, position_pt=(600.0, 400.0),
                           char_type="Distance", nominal="8"),
        GoldCharacteristic(balloon=3, position_pt=(800.0, 500.0),
                           char_type="Distance", nominal="12"),
    ],
}
SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _perfect_dump(doc_id, gold: GoldDoc, drop_last=False) -> PredictionDump:
    chars = []
    records = gold.characteristics[:-1] if drop_last else gold.characteristics
    for i, g in enumerate(records, start=1):
        x, y = g.position_pt
        chars.append(Characteristic(
            pos=i, char_type=g.char_type, nominal=g.nominal,
            upper_tol=g.upper_tol, lower_tol=g.lower_tol, raw_text=g.nominal,
            target_region=(SCALE * (x - 15), SCALE * (y - 5),
                           SCALE * (x + 15), SCALE * (y + 5))))
    return PredictionDump(doc_id=doc_id, config=RunConfig(model_id="stub"),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def _setup_corpus(root):
    pdfs, excel = root / "pdfs", root / "excel"
    for doc_id, recs in RECORDS.items():
        make_synthetic_doc(recs, root / "raw", doc_id=doc_id)
        pdfs.mkdir(exist_ok=True), excel.mkdir(exist_ok=True)
        (root / "raw" / f"{doc_id}.pdf").rename(pdfs / f"{doc_id}.pdf")
        (root / "raw" / f"{doc_id}.xlsx").rename(excel / f"{doc_id}.xlsx")
    return pdfs, excel


def test_full_pipeline_ingest_score_compare(tmp_path):
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "base"

    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    gold_files = sorted(gold_dir.glob("*.gold.json"))
    assert [p.name for p in gold_files] == ["SYNA.gold.json", "SYNB.gold.json"]

    for path in gold_files:
        gold = GoldDoc.model_validate_json(path.read_text())
        save_dump(_perfect_dump(gold.doc_id, gold,
                                drop_last=(gold.doc_id == "SYNB")), run_dir)

    report_path = tmp_path / "base.report.json"
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "base", "--out", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["taxonomy"] == {"correct": 4, "missed": 1}
    assert report["mean_review_cost"] == 5.0      # (0 + 10)/2

    cmp_path = tmp_path / "cmp.json"
    assert main(["compare", str(report_path), str(report_path),
                 "--out", str(cmp_path)]) == 0
    cmp = json.loads(cmp_path.read_text())
    assert cmp["mean_delta"] == 0.0 and cmp["significant"] is False


def test_probe_and_headers_inspection_commands(tmp_path, capsys):
    pdfs, excel = _setup_corpus(tmp_path)
    assert main(["probe", str(pdfs)]) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert {l["n_balloons"] for l in lines} == {2, 3}
    assert main(["headers", str(excel)]) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert all(l["header_row"] == 1 for l in lines)


def test_predict_one_builds_dump_from_stub_backend(tmp_path):
    from tests.conftest import StubVLMBackend
    from app.pipeline.detect import Detection
    pdfs, _ = _setup_corpus(tmp_path)
    backend = StubVLMBackend(detections=[
        Detection(box=(100, 100, 200, 140), kind="dimension", conf=0.9)])
    dump = predict_one(pdfs / "SYNA.pdf", "SYNA", dpi=300, backend=backend,
                       config=RunConfig(model_id="stub", dpi=300),
                       work_dir=tmp_path / "work")
    assert dump.doc_id == "SYNA"
    assert dump.scale == 300 / 72.0
    assert round(dump.page_rect[2]) == 1191
    assert len(dump.result.characteristics) >= 1


def test_score_with_no_gold_dump_overlap_exits_1(tmp_path, capsys):
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir = tmp_path / "gold"
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    empty_run_dir = tmp_path / "runs" / "empty"
    empty_run_dir.mkdir(parents=True)
    report_path = tmp_path / "empty.report.json"
    rc = main(["score", "--run", str(empty_run_dir), "--gold", str(gold_dir),
              "--name", "empty", "--out", str(report_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR: no documents scored" in err


def test_score_mixed_configs_raises(tmp_path):
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "mixed"
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    for path in sorted(gold_dir.glob("*.gold.json")):
        gold = GoldDoc.model_validate_json(path.read_text())
        dump = _perfect_dump(gold.doc_id, gold)
        if gold.doc_id == "SYNB":
            dump = dump.model_copy(
                update={"config": RunConfig(model_id="other-model")})
        save_dump(dump, run_dir)
    report_path = tmp_path / "mixed.report.json"
    with pytest.raises(ValueError, match="mixed configs"):
        main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
             "--name", "mixed", "--out", str(report_path)])


def test_compare_incomparable_runs_exits_1(tmp_path, capsys):
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "base"
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    for path in sorted(gold_dir.glob("*.gold.json")):
        gold = GoldDoc.model_validate_json(path.read_text())
        save_dump(_perfect_dump(gold.doc_id, gold), run_dir)

    weights_path = tmp_path / "weights.json"
    weights_path.write_text(ReviewCostWeights(miss=99).model_dump_json())

    report_a = tmp_path / "a.report.json"
    report_b = tmp_path / "b.report.json"
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "a", "--out", str(report_a)]) == 0
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "b", "--out", str(report_b),
                 "--weights", str(weights_path)]) == 0

    cmp_path = tmp_path / "cmp.json"
    rc = main(["compare", str(report_a), str(report_b), "--out", str(cmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "NOT COMPARABLE" in err
