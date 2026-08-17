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


def test_probe_and_headers_anonymize_doc_ids_by_default(tmp_path, capsys,
                                                        monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, excel = _setup_corpus(tmp_path)
    assert main(["probe", str(pdfs)]) == 0
    out = capsys.readouterr().out
    assert "SYNA" not in out and "SYNB" not in out
    rows = [json.loads(line) for line in out.strip().splitlines()]
    assert all(len(r["doc"]) == 8 for r in rows)

    assert main(["headers", str(excel)]) == 0
    out = capsys.readouterr().out
    assert "SYNA" not in out and "SYNB" not in out


def test_variants_command_ranks_structurally_atypical_drawings(tmp_path, capsys,
                                                               monkeypatch):
    """The frozen test split must hold the odd drawings so cross-template
    generalization stays visible. Atypicality is derivable from structure —
    no human has to label anything."""
    import fitz
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()

    def _plain(path, pages=1, raster=False):
        doc = fitz.open()
        for _ in range(pages):
            page = doc.new_page(width=600, height=400)
            if raster:
                pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
                pix.clear_with(200)
                page.insert_image(fitz.Rect(10, 10, 200, 200), pixmap=pix)
            else:
                page.insert_text(fitz.Point(50, 50), "12", fontsize=9)
                page.draw_line(fitz.Point(10, 10), fitz.Point(80, 80))
        doc.save(path)
        doc.close()

    _plain(pdfs / "NORMAL_A.pdf")
    _plain(pdfs / "NORMAL_B.pdf")
    _plain(pdfs / "MULTIPAGE.pdf", pages=3)
    _plain(pdfs / "RASTER.pdf", raster=True)

    out = tmp_path / "variants.txt"
    assert main(["variants", "--pdfs", str(pdfs), "--out", str(out),
                 "--limit", "2"]) == 0
    picked = set(out.read_text().split())
    assert picked == {"MULTIPAGE", "RASTER"}

    printed = capsys.readouterr().out
    assert "NORMAL_A" not in printed and "MULTIPAGE" not in printed
    digest = json.loads(printed)
    assert digest["n_docs"] == 4 and digest["n_variants"] == 2


def test_ingest_summary_reports_join_aggregates(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, excel = _setup_corpus(tmp_path)
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(tmp_path / "gold"), "--summary"]) == 0
    digest = json.loads(capsys.readouterr().out)
    assert digest["n_docs"] == 2
    assert digest["docs_fully_joined"] == 2
    assert digest["excel_only_total"] == 0
    assert digest["pdf_only_total"] == 0
    assert digest["join_rate"]["min"] == 1.0


def test_probe_summary_aggregates_instead_of_per_doc_lines(tmp_path, capsys,
                                                           monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, _ = _setup_corpus(tmp_path)
    assert main(["probe", str(pdfs), "--summary"]) == 0
    digest = json.loads(capsys.readouterr().out)   # one object, not JSONL
    assert digest["n_docs"] == 2
    assert digest["with_balloons"] == 2
    assert digest["with_annotations"] == 0
    assert digest["with_images"] == 0
    assert digest["balloons_per_doc"]["max"] == 3
    assert digest["balloons_per_doc"]["min"] == 2
    assert digest["with_numeric_annotations"] == 0
    assert digest["vector_items_per_doc"]["min"] > 0


def test_headers_summary_groups_sheets_by_schema(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    _, excel = _setup_corpus(tmp_path)
    assert main(["headers", str(excel), "--summary"]) == 0
    digest = json.loads(capsys.readouterr().out)
    assert digest["n_docs"] == 2
    assert digest["with_error"] == 0
    assert digest["header_rows"] == {"1": 2}
    assert len(digest["schemas"]) == 1            # both sheets share a layout
    assert digest["schemas"][0]["docs"] == 2
    assert "Merkmal" in digest["schemas"][0]["headers"]


def test_show_ids_opts_back_into_real_part_numbers(tmp_path, capsys,
                                                   monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, _ = _setup_corpus(tmp_path)
    assert main(["probe", str(pdfs), "--show-ids"]) == 0
    assert "SYNA" in capsys.readouterr().out


def test_summary_command_emits_value_free_digest(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, excel = _setup_corpus(tmp_path)
    gold_dir, run_dir = tmp_path / "gold", tmp_path / "runs" / "base"
    assert main(["ingest", "--pdfs", str(pdfs), "--excel", str(excel),
                 "--out", str(gold_dir)]) == 0
    for path in sorted(gold_dir.glob("*.gold.json")):
        gold = GoldDoc.model_validate_json(path.read_text())
        save_dump(_perfect_dump(gold.doc_id, gold,
                                drop_last=(gold.doc_id == "SYNB")), run_dir)
    report_path = tmp_path / "base.report.json"
    assert main(["score", "--run", str(run_dir), "--gold", str(gold_dir),
                 "--name", "base", "--out", str(report_path)]) == 0
    capsys.readouterr()

    assert main(["summary", str(report_path)]) == 0
    out = capsys.readouterr().out
    assert "SYNA" not in out and "SYNB" not in out
    digest = json.loads(out)
    assert digest["n_docs"] == 2
    assert digest["taxonomy"]["missed"] == 1
    assert len(digest["worst_docs"][0]["doc"]) == 8


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
