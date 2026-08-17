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

from app.eval.anon import Anonymizer
from app.eval.balloons import probe_pdf, shape_report
from app.eval.balloon_cv import cv_report
from app.eval.dump import load_dump, save_dump
from app.eval.excel_gold import dump_headers, sheet_vocabulary
from app.eval.ingest import build_gold_doc
from app.eval.models import (GoldDoc, MatchParams, PredictionDump,
                             ReviewCostWeights, RunConfig, RunReport)
from app.eval.report import aggregate, compare_runs, summarize
from app.eval.score import score_doc
from app.eval.splits import load_splits, make_splits, save_splits, splits_hash


_PDF_GLOB = "*.pdf"


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


def _anon(args) -> Anonymizer:
    """Every human-readable line goes through this. Default ON: client part
    numbers must not reach an AI context (--show-ids is for a human terminal)."""
    return Anonymizer(enabled=not getattr(args, "show_ids", False))


def _spread(values):
    import statistics
    vals = sorted(values)
    if not vals:
        return {"min": 0, "median": 0, "max": 0, "total": 0}
    return {"min": vals[0], "median": statistics.median(vals),
            "max": vals[-1], "total": sum(vals)}


def _probe_summary(records) -> dict:
    """Corpus-level view of the encoding question, so a 100-doc probe costs one
    object instead of 100 lines."""
    annot_types = {}
    for rec in records:
        for name, n in (rec.get("annot_types") or {}).items():
            annot_types[name] = annot_types.get(name, 0) + n
    return {
        "n_docs": len(records),
        "multi_page_docs": sum(1 for r in records if r.get("n_pages", 1) > 1),
        "pages_per_doc": _spread(r.get("n_pages", 1) for r in records),
        "with_balloons": sum(1 for r in records if r["n_balloons"]),
        "with_annotations": sum(1 for r in records if r.get("n_annots")),
        "with_images": sum(1 for r in records if r["has_images"]),
        "without_vector_text": sum(1 for r in records if not r["n_words"]),
        "without_vector_content": sum(1 for r in records if not r["n_drawings"]),
        "with_duplicate_numbers": sum(1 for r in records
                                      if r.get("duplicate_numbers")),
        "with_numeric_annotations": sum(1 for r in records
                                        if r.get("n_annot_numbers")),
        "annot_types": annot_types,
        "balloons_per_doc": _spread(r["n_balloons"] for r in records),
        "annots_per_doc": _spread(r.get("n_annots", 0) for r in records),
        "numeric_annots_per_doc": _spread(r.get("n_annot_numbers", 0)
                                          for r in records),
        "vector_items_per_doc": _spread(r["n_drawings"] for r in records),
        "circles_per_doc": _spread(r["n_circles"] for r in records),
        "words_per_doc": _spread(r["n_words"] for r in records),
    }


def _shapes_summary(records) -> dict:
    """Calibration view: why balloon recovery does or does not fire."""
    kinds, widths = {}, {}
    for rec in records:
        for k, n in rec["item_kinds"].items():
            kinds[k] = kinds.get(k, 0) + n
        for k, n in rec["shape_widths"].items():
            widths[k] = widths.get(k, 0) + n
    return {
        "n_docs": len(records),
        "docs_with_digit_words": sum(1 for r in records if r["digit_words"]),
        "digit_words_per_doc": _spread(r["digit_words"] for r in records),
        "digit_words_in_shape_per_doc": _spread(r["digit_words_in_shape"]
                                                for r in records),
        "near_square_shapes_per_doc": _spread(r["near_square_shapes"]
                                              for r in records),
        "digit_height_median": _spread(r["digit_height_median"]
                                       for r in records),
        "item_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "shape_widths": dict(sorted(widths.items(), key=lambda kv: -kv[1])),
    }


def _cmd_probe(args):
    anon = _anon(args)
    records = []
    if args.cv_report:
        reps = [cv_report(f, dpi=150)
                for f in sorted(Path(args.dir).glob("*.p" + "df"))]
        agg, sizes = {}, {}
        keys = ("coloured_px", "dark_px", "blue_px_m15", "blue_px_m40",
                "blue_px_m80", "n_contours", "n_candidates", "n_read")
        for k in keys:
            agg[k] = _spread(r.get(k, 0) for r in reps)
        for r in reps:
            for b, n in (r.get("contour_sizes_pt") or {}).items():
                sizes[b] = sizes.get(b, 0) + n
        print(json.dumps({"n_docs": len(reps),
                          "docs_with_blue": sum(1 for r in reps
                                                if r.get("blue_px_m40", 0) > 0),
                          "docs_with_candidates": sum(1 for r in reps
                                                      if r.get("n_candidates")),
                          "docs_with_readings": sum(1 for r in reps
                                                    if r.get("n_read")),
                          **agg, "contour_sizes_pt": sizes},
                         indent=1, ensure_ascii=False))
        return 0
    if args.shapes:
        reps = [shape_report(f) for f in sorted(Path(args.dir).glob(_PDF_GLOB))]
        print(json.dumps(_shapes_summary(reps), indent=1, ensure_ascii=False))
        return 0
    for pdf in sorted(Path(args.dir).glob(_PDF_GLOB)):
        rec = probe_pdf(pdf)
        rec.pop("pdf", None)
        records.append(rec)
        if not args.summary:
            print(json.dumps({"doc": anon(pdf.stem), **rec}, ensure_ascii=False))
    if args.summary:
        print(json.dumps(_probe_summary(records), indent=1, ensure_ascii=False))
    return 0


# Structural traits that mark a drawing as atypical for this corpus. Forcing
# these into the frozen test split is what makes cross-template generalization
# visible (handoff section 6) — and it is derivable, so no human labels anything.
def _atypical_traits(rec) -> list:
    traits = []
    if rec.get("n_pages", 1) > 1:
        traits.append("multi_page")
    if not rec.get("n_words"):
        traits.append("no_text_layer")
    if not rec.get("n_drawings"):
        traits.append("no_vector_content")
    if rec.get("has_images"):
        traits.append("raster_content")
    if rec.get("n_annots"):
        traits.append("annotated")
    return traits


def _cmd_variants(args):
    anon = _anon(args)
    scored, trait_counts = [], {}
    for pdf in sorted(Path(args.dir if hasattr(args, "dir") else args.pdfs)
                      .glob(_PDF_GLOB)):
        rec = probe_pdf(pdf)
        traits = _atypical_traits(rec)
        for t in traits:
            trait_counts[t] = trait_counts.get(t, 0) + 1
        if traits:
            scored.append((len(traits), pdf.stem, traits))
    # most atypical first; doc id breaks ties so the choice is reproducible
    scored.sort(key=lambda t: (-t[0], t[1]))
    limit = args.limit if args.limit is not None else max(1, len(scored))
    chosen = scored[:limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(stem for _, stem, _ in chosen) + "\n",
                   encoding="utf-8")

    n_docs = len(list(Path(args.pdfs).glob(_PDF_GLOB)))
    print(json.dumps({
        "n_docs": n_docs,
        "n_atypical": len(scored),
        "n_variants": len(chosen),
        "trait_counts": dict(sorted(trait_counts.items(), key=lambda kv: -kv[1])),
        "variants": [{"doc": anon(stem), "traits": traits}
                     for _, stem, traits in chosen],
        "written_to": str(out),
    }, indent=1, ensure_ascii=False))
    return 0


def _headers_summary(records) -> dict:
    """Group sheets by their header signature: the answer to 'how many distinct
    layouts are in this corpus' in one object instead of one line per file."""
    schemas, header_rows, unmapped = {}, {}, {}
    ok = [r for r in records if "error" not in r]
    for rec in ok:
        key = tuple(rec.get("headers", []))
        schemas[key] = schemas.get(key, 0) + 1
        row = str(rec.get("header_row"))
        header_rows[row] = header_rows.get(row, 0) + 1
        for field in ("pos", "char_type", "nominal", "upper_tol", "lower_tol"):
            if field not in rec.get("mapped_fields", []):
                unmapped[field] = unmapped.get(field, 0) + 1
    ranked = sorted(schemas.items(), key=lambda kv: -kv[1])
    # why failures fail: which sheet names exist, and where a pos-header sits
    sheet_names, deep_hits = {}, {}
    for rec in records:
        for name in rec.get("sheet_names", []):
            sheet_names[name] = sheet_names.get(name, 0) + 1
        for entry in rec.get("scan", []):
            if entry.get("pos_row") is not None:
                key = f"{entry['sheet']}@row{entry['pos_row']}"
                deep_hits[key] = deep_hits.get(key, 0) + 1
    return {
        "n_docs": len(records),
        "with_error": sum(1 for r in records if "error" in r),
        "sheet_names": dict(sorted(sheet_names.items(), key=lambda kv: -kv[1])),
        "pos_header_found_at": dict(sorted(deep_hits.items(),
                                           key=lambda kv: -kv[1])),
        "with_duplicate_pos": sum(1 for r in ok if r.get("duplicate_pos")),
        "header_rows": header_rows,
        "unmapped_fields": unmapped,
        "rows_per_doc": _spread(r.get("n_rows", 0) for r in ok),
        "schemas": [{"docs": n, "headers": list(k)} for k, n in ranked],
    }


def _cmd_headers(args):
    anon = _anon(args)
    records, vocab_freq = [], {}
    for xlsx in sorted(Path(args.dir).glob("*.xlsx")):
        info = dump_headers(xlsx)
        info.pop("file", None)
        records.append(info)
        if args.summary and args.captions:
            for text in sheet_vocabulary(xlsx):
                vocab_freq[text] = vocab_freq.get(text, 0) + 1
        if not args.summary:
            print(json.dumps({"doc": anon(xlsx.stem), **info}, ensure_ascii=False))
    if args.summary:
        digest = _headers_summary(records)
        # captions shared by many workbooks; a per-part value cannot repeat here
        if args.captions:
            shared = {t: n for t, n in vocab_freq.items() if n >= args.min_docs}
            digest["shared_captions"] = dict(
                sorted(shared.items(), key=lambda kv: -kv[1])[:60])
        print(json.dumps(digest, indent=1, ensure_ascii=False))
    return 0


def _cmd_ingest(args):
    pdfs = {p.stem: p for p in Path(args.pdfs).glob(_PDF_GLOB)}
    excels = {p.stem: p for p in Path(args.excel).glob("*.xlsx")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    variants = set(Path(args.variants).read_text().split()) if args.variants else set()
    anon = _anon(args)
    unpaired = sorted(set(pdfs) ^ set(excels))
    if unpaired:
        print(f"WARNING: unpaired stems (skipped): {[anon(s) for s in unpaired]}",
              file=sys.stderr)
    low_join, provenance = [], []
    paired = sorted(set(pdfs) & set(excels))
    for stem in paired:
        gold = build_gold_doc(pdfs[stem], excels[stem], doc_id=stem,
                              is_variant=stem in variants, use_cv=args.cv)
        (out / f"{stem}.gold.json").write_text(gold.model_dump_json(indent=1),
                                               encoding="utf-8")
        provenance.append(gold.provenance)
        if gold.provenance["join_rate"] < 0.95:
            low_join.append((anon(stem), gold.provenance["join_rate"]))
    # Local-only trace file so a human can map a hash back to a drawing. Never
    # committed (gitignored + blocked by .git/hooks/pre-commit).
    (out / "doc_id_map.json").write_text(
        json.dumps(anon.mapping(paired), indent=1), encoding="utf-8")
    if args.summary:
        print(json.dumps(_ingest_summary(provenance), indent=1,
                         ensure_ascii=False))
        return 0
    if low_join:
        print(f"ATTENTION: join_rate < 0.95 (inspect manually): {low_join}",
              file=sys.stderr)
    print(f"ingested {len(paired)} docs -> {out}")
    return 0


def _merge_counts(dicts) -> dict:
    out = {}
    for d in dicts:
        for k, n in (d or {}).items():
            out[k] = out.get(k, 0) + n
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:25])


def _ingest_summary(provenance) -> dict:
    """Where the join actually stands: balloons recovered from the drawings
    versus rows in the sheets, and which side each shortfall is on. pdf_only
    means a recovered number the sheet does not list (over-detection);
    excel_only means a listed characteristic no balloon was found for."""
    return {
        "n_docs": len(provenance),
        "docs_fully_joined": sum(1 for p in provenance
                                 if p["join_rate"] >= 0.999),
        "join_rate": _spread(round(p["join_rate"], 4) for p in provenance),
        "balloons_total": sum(p["n_balloons"] for p in provenance),
        "excel_rows_total": sum(p["n_excel_rows"] for p in provenance),
        "pdf_only_total": sum(len(p["pdf_only"]) for p in provenance),
        "excel_only_total": sum(len(p["excel_only"]) for p in provenance),
        "without_position_total": sum(p.get("without_position", 0)
                                      for p in provenance),
        "on_later_pages_total": sum(p.get("on_later_pages", 0)
                                    for p in provenance),
        "recovered_by_cv_total": sum(p.get("recovered_by_cv", 0)
                                     for p in provenance),
        "unlocated_kinds": _merge_counts(p.get("unlocated_kinds", {})
                                         for p in provenance),
        "gold_kinds": _merge_counts(p.get("kinds", {}) for p in provenance),
        "unlocated_char_types": _merge_counts(
            p.get("unlocated_char_types", {}) for p in provenance),
        "balloons_per_doc": _spread(p["n_balloons"] for p in provenance),
        "excel_rows_per_doc": _spread(p["n_excel_rows"] for p in provenance),
        "pdf_only_per_doc": _spread(len(p["pdf_only"]) for p in provenance),
        "excel_only_per_doc": _spread(len(p["excel_only"]) for p in provenance),
        "docs_with_duplicate_balloons": sum(
            1 for p in provenance if p.get("duplicate_balloons")),
    }


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
    pdfs = {p.stem: p for p in Path(args.pdfs).glob(_PDF_GLOB)}
    doc_ids, _, _ = _select_docs(pdfs, args.splits, args.split)
    anon = _anon(args)
    for i, doc_id in enumerate(doc_ids, 1):
        print(f"[{i}/{len(doc_ids)}] {anon(doc_id)}", file=sys.stderr)
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
    anon = _anon(args)
    missing = sorted((set(gold) & set(dumps)) ^ set(dumps))
    if missing:
        print(f"WARNING: dumps without gold (excluded): "
              f"{[anon(d) for d in missing]}", file=sys.stderr)
    orphan_gold = sorted(set(gold) - set(dumps))
    if orphan_gold:
        print(f"WARNING: gold docs without dumps (excluded): "
              f"{[anon(d) for d in orphan_gold]}", file=sys.stderr)
    scores = [score_doc(dumps[d], gold[d], weights, params) for d in doc_ids]
    if len(scores) == 0:
        print("ERROR: no documents scored (no gold/dump overlap in selected "
              "split)", file=sys.stderr)
        return 1
    configs = {(dumps[d].config.model_id, dumps[d].config.dpi,
               dumps[d].config.git_sha, dumps[d].config.prompt_sha256)
              for d in doc_ids}
    if len(configs) > 1:
        raise ValueError(f"mixed configs in run dir: {sorted(configs)} — "
                         f"re-predict the full split with one config")
    config = dumps[doc_ids[0]].config
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
    try:
        cmp = compare_runs(a, b)
    except ValueError as e:
        print(f"NOT COMPARABLE: {e}", file=sys.stderr)
        return 1
    anon = _anon(args)
    cmp["per_doc_deltas"] = {anon(k): v for k, v in cmp["per_doc_deltas"].items()}
    out = json.dumps(cmp, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    print(out)
    for w in cmp["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    return 0


def _cmd_summary(args):
    """The ONLY sanctioned way to look at a run: aggregate metrics, hashed ids,
    no client values. Safe to show an AI agent, commit, or paste in a ticket."""
    report = RunReport.model_validate_json(
        Path(args.report).read_text(encoding="utf-8"))
    digest = summarize(report, _anon(args))
    out = json.dumps(digest, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
    print(out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.eval.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--show-ids", action="store_true",
                        help="print real part numbers instead of salted "
                             "hashes; for a human terminal only, never for an "
                             "AI agent")

    p = sub.add_parser("probe", parents=[common])
    p.add_argument("dir")
    p.add_argument("--summary", action="store_true",
                   help="one aggregate object instead of one line per document")
    p.add_argument("--cv-report", action="store_true",
                   help="calibration for rendered-page detection: ink colour, "
                        "contour sizes, and where the pipeline drops out")
    p.add_argument("--shapes", action="store_true",
                   help="calibration diagnostic: shape sizes, primitives, and "
                        "whether digit words land inside a candidate outline")
    p.set_defaults(fn=_cmd_probe)
    p = sub.add_parser("headers", parents=[common])
    p.add_argument("dir")
    p.add_argument("--summary", action="store_true",
                   help="group sheets by header signature instead of one line each")
    p.add_argument("--captions", action="store_true",
                   help="also report captions shared across workbooks (slow: "
                        "re-reads every file)")
    p.add_argument("--min-docs", type=int, default=5,
                   help="a caption must appear in this many workbooks to be shown")
    p.set_defaults(fn=_cmd_headers)

    p = sub.add_parser("summary", parents=[common])
    p.add_argument("report"); p.add_argument("--out", default=None)
    p.set_defaults(fn=_cmd_summary)

    p = sub.add_parser("ingest", parents=[common])
    p.add_argument("--summary", action="store_true",
                   help="print join aggregates instead of per-document warnings")
    p.add_argument("--cv", action="store_true",
                   help="read balloons off the rendered page for rows the text "
                        "layer cannot locate (slower: renders + OCRs each page)")
    p.add_argument("--pdfs", required=True); p.add_argument("--excel", required=True)
    p.add_argument("--out", required=True); p.add_argument("--variants", default=None)
    p.set_defaults(fn=_cmd_ingest)

    p = sub.add_parser("variants", parents=[common])
    p.add_argument("--pdfs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=None,
                   help="keep only the N most atypical drawings")
    p.set_defaults(fn=_cmd_variants)

    p = sub.add_parser("split", parents=[common])
    p.add_argument("--gold", required=True); p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=13)
    p.set_defaults(fn=_cmd_split)

    p = sub.add_parser("predict", parents=[common])
    p.add_argument("--pdfs", required=True); p.add_argument("--out", required=True)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.set_defaults(fn=_cmd_predict)

    p = sub.add_parser("score", parents=[common])
    p.add_argument("--run", required=True); p.add_argument("--gold", required=True)
    p.add_argument("--name", required=True); p.add_argument("--out", required=True)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.add_argument("--weights", default=None)
    p.set_defaults(fn=_cmd_score)

    p = sub.add_parser("compare", parents=[common])
    p.add_argument("report_a"); p.add_argument("report_b")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=_cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
