"""Eval harness CLI.

    python -m app.eval.runner probe   eval_data/pdfs            # day-one: balloon encoding
    python -m app.eval.runner headers eval_data/excel           # day-one: Excel schema
    python -m app.eval.runner ingest  --pdfs ... --excel ... --out eval_data/gold
    python -m app.eval.runner split   --gold eval_data/gold --variants v.txt \
                                      --out docs/eval/splits.json
    python -m app.eval.runner predict --pdfs ... --out eval_data/runs/<name> \
                                      [--splits docs/eval/splits.json --split dev]
    python -m app.eval.runner score   --run eval_data/runs/<name> --gold ... \
                                      --name <name> --out <report.json> \
                                      [--splits ... --split dev] [--weights w.json]
    python -m app.eval.runner compare <report_a.json> <report_b.json> [--out c.json]

probe/headers/ingest/split/score/compare are CPU-only. predict imports the
model stack lazily and captures the RunConfig fingerprint at run time.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import fitz

from app.eval.balloons import probe_pdf
from app.eval.dump import load_dump, save_dump
from app.eval.excel_gold import dump_headers
from app.eval.ingest import build_gold_doc
from app.eval.models import (GoldDoc, MatchParams, PredictionDump,
                             ReviewCostWeights, RunConfig, RunReport)
from app.eval.report import aggregate, compare_runs
from app.eval.score import score_doc
from app.eval.splits import load_splits, make_splits, save_splits, splits_hash


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent, text=True).strip()
    except Exception:
        return "unknown"


def _prompt_sha256() -> str:
    try:
        from app.pipeline.ocr import vlm_backend as vb
        blob = "\n".join([vb._PROMPT, vb._DETECT_PROMPT, vb._GDT_PROMPT,
                          vb._NOTES_PROMPT, vb._TITLE_PROMPT])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unavailable"


def _select_docs(doc_ids, splits_path, split_name):
    if not splits_path:
        return sorted(doc_ids), "", "all"
    splits = load_splits(splits_path)
    keep = set(splits[split_name])
    return (sorted(d for d in doc_ids if d in keep),
            splits_hash(splits), split_name)


def predict_one(pdf_path, doc_id: str, dpi: int, backend,
                config: RunConfig, work_dir) -> PredictionDump:
    from app.pipeline.extract import extract
    result = extract(pdf_path, Path(work_dir) / doc_id, dpi=dpi,
                     backend=backend)
    doc = fitz.open(pdf_path)
    rect = doc[0].rect
    doc.close()
    return PredictionDump(doc_id=doc_id, config=config, scale=dpi / 72.0,
                          page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                          result=result)


def _cmd_probe(args):
    for pdf in sorted(Path(args.dir).glob("*.pdf")):
        print(json.dumps(probe_pdf(pdf), ensure_ascii=False))
    return 0


def _cmd_headers(args):
    for xlsx in sorted(Path(args.dir).glob("*.xlsx")):
        print(json.dumps(dump_headers(xlsx), ensure_ascii=False))
    return 0


def _cmd_ingest(args):
    pdfs = {p.stem: p for p in Path(args.pdfs).glob("*.pdf")}
    excels = {p.stem: p for p in Path(args.excel).glob("*.xlsx")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    variants = set(Path(args.variants).read_text().split()) if args.variants else set()
    unpaired = sorted(set(pdfs) ^ set(excels))
    if unpaired:
        print(f"WARNING: unpaired stems (skipped): {unpaired}", file=sys.stderr)
    low_join = []
    for stem in sorted(set(pdfs) & set(excels)):
        gold = build_gold_doc(pdfs[stem], excels[stem], doc_id=stem,
                              is_variant=stem in variants)
        (out / f"{stem}.gold.json").write_text(gold.model_dump_json(indent=1),
                                               encoding="utf-8")
        if gold.provenance["join_rate"] < 0.95:
            low_join.append((stem, gold.provenance["join_rate"]))
    if low_join:
        print(f"ATTENTION: join_rate < 0.95 (inspect manually): {low_join}",
              file=sys.stderr)
    print(f"ingested {len(set(pdfs) & set(excels))} docs -> {out}")
    return 0


def _load_gold_dir(gold_dir):
    return {g.doc_id: g for g in
            (GoldDoc.model_validate_json(p.read_text(encoding="utf-8"))
             for p in sorted(Path(gold_dir).glob("*.gold.json")))}


def _cmd_split(args):
    gold = _load_gold_dir(args.gold)
    variants = [d for d, g in gold.items() if g.is_variant]
    splits = make_splits(sorted(gold), variants, seed=args.seed)
    path = save_splits(splits, args.out)
    print(f"splits -> {path} (train={len(splits['train'])} "
          f"dev={len(splits['dev'])} test={len(splits['test'])})")
    return 0


def _cmd_predict(args):
    import os
    from app.pipeline.ocr import get_backend
    backend = get_backend()
    config = RunConfig(
        model_id=os.environ.get("VLM_MODEL_ID", "default"), dpi=args.dpi,
        git_sha=_git_sha(), prompt_sha256=_prompt_sha256())
    pdfs = {p.stem: p for p in Path(args.pdfs).glob("*.pdf")}
    doc_ids, _, _ = _select_docs(pdfs, args.splits, args.split)
    for i, doc_id in enumerate(doc_ids, 1):
        print(f"[{i}/{len(doc_ids)}] {doc_id}", file=sys.stderr)
        dump = predict_one(pdfs[doc_id], doc_id, args.dpi, backend, config,
                           Path(args.out) / "_work")
        save_dump(dump, args.out)
    return 0


def _cmd_score(args):
    gold = _load_gold_dir(args.gold)
    dumps = {d.doc_id: d for d in
             (load_dump(p) for p in sorted(Path(args.run).glob("*.pred.json")))}
    weights = (ReviewCostWeights.model_validate_json(
                   Path(args.weights).read_text()) if args.weights
               else ReviewCostWeights())
    params = MatchParams()
    doc_ids, sp_hash, sp_name = _select_docs(
        set(gold) & set(dumps), args.splits, args.split)
    missing = sorted((set(gold) & set(dumps)) ^ set(dumps))
    if missing:
        print(f"WARNING: dumps without gold (excluded): {missing}",
              file=sys.stderr)
    scores = [score_doc(dumps[d], gold[d], weights, params) for d in doc_ids]
    config = scores and dumps[doc_ids[0]].config or RunConfig()
    report = aggregate(args.name, config, weights, params, scores,
                       splits_hash=sp_hash, split_used=sp_name)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report.model_dump_json(indent=1),
                              encoding="utf-8")
    print(f"{args.name}: docs={len(scores)} "
          f"mean_review_cost={report.mean_review_cost:.2f} "
          f"recall={report.micro_recall:.3f} "
          f"escaped_rate={report.escaped_rate:.3f}")
    return 0


def _cmd_compare(args):
    a = RunReport.model_validate_json(Path(args.report_a).read_text())
    b = RunReport.model_validate_json(Path(args.report_b).read_text())
    cmp = compare_runs(a, b)
    out = json.dumps(cmp, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    print(out)
    for w in cmp["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.eval.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe"); p.add_argument("dir"); p.set_defaults(fn=_cmd_probe)
    p = sub.add_parser("headers"); p.add_argument("dir"); p.set_defaults(fn=_cmd_headers)

    p = sub.add_parser("ingest")
    p.add_argument("--pdfs", required=True); p.add_argument("--excel", required=True)
    p.add_argument("--out", required=True); p.add_argument("--variants", default=None)
    p.set_defaults(fn=_cmd_ingest)

    p = sub.add_parser("split")
    p.add_argument("--gold", required=True); p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=13)
    p.set_defaults(fn=_cmd_split)

    p = sub.add_parser("predict")
    p.add_argument("--pdfs", required=True); p.add_argument("--out", required=True)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.set_defaults(fn=_cmd_predict)

    p = sub.add_parser("score")
    p.add_argument("--run", required=True); p.add_argument("--gold", required=True)
    p.add_argument("--name", required=True); p.add_argument("--out", required=True)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.add_argument("--weights", default=None)
    p.set_defaults(fn=_cmd_score)

    p = sub.add_parser("compare")
    p.add_argument("report_a"); p.add_argument("report_b")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=_cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
