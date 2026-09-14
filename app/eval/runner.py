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

from app.eval.anon import Anonymizer, salt_is_persistent
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
        # The EFFECTIVE prompts, not the module constants: a variant selected by
        # environment variable has to move this hash, or two arms would be
        # indistinguishable in every report they produce. With no variant set
        # this is byte-identical to the old five-constant join.
        blob = "\n".join(vb.effective_prompts())
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
                config: RunConfig, work_dir,
                detect_only: bool = False) -> PredictionDump:
    from app.pipeline.extract import extract
    result = extract(pdf_path, Path(work_dir) / doc_id, dpi=dpi,
                     backend=backend, detect_only=detect_only)
    doc = fitz.open(pdf_path)
    rect = doc[0].rect
    doc.close()
    # The render clamps its dpi to a pixel budget on large-format sheets, so the
    # scale that interprets these boxes is the render's, NOT dpi/72. Taking the
    # requested dpi here would corrupt every coordinate in the dump silently.
    return PredictionDump(doc_id=doc_id, config=config,
                          scale=result.render_scale,
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
    # The scope policy's two exclusions, counted over the WHOLE corpus rather
    # than the 20 documents that have gold -- `score` can only count them where
    # it can score. A drawing can fail both tests, so the two exclusion counts
    # do not sum to `excluded_docs`; `supported + excluded == n_docs` is the
    # identity that holds, and it is the number the client asks for.
    clamp_known = [r for r in records if "render_clamped" in r]
    unmeasured = len(records) - len(clamp_known)
    supported = sum(1 for r in clamp_known
                    if not r["render_clamped"] and r.get("n_pages", 1) <= 1)
    scope = ({"supported_docs": supported,
              "excluded_docs": len(records) - supported} if not unmeasured
             else {})
    return {
        "n_docs": len(records),
        "multi_page_docs": sum(1 for r in records if r.get("n_pages", 1) > 1),
        "render_clamped_docs": sum(1 for r in clamp_known if r["render_clamped"]),
        # Never a plausible-looking 0: a probe record predating the clamp test
        # would otherwise be counted as "fits at full resolution", and
        # supported_docs is withheld entirely rather than guessed. The defect
        # DocScore.frame_origin_frac exists to prevent, in the other direction.
        "render_clamped_not_measured": unmeasured,
        **scope,
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
    originals = ({p.stem: p for p in Path(args.originals).glob(_PDF_GLOB)}
                 if getattr(args, "originals", None) else {})
    if originals:
        no_original = sorted(set(paired) - set(originals))
        if no_original:
            # Loud, because these documents lose their geometry entirely: keeping
            # stamped-space coordinates would reinstate the fault --originals
            # exists to remove, so the positions are dropped and the rows fall
            # back to value matching.
            print(f"WARNING: no original for (positions dropped, rows matched on "
                  f"value and counted as unlocated): {[anon(s) for s in no_original]}",
                  file=sys.stderr)
    for stem in paired:
        gold = build_gold_doc(pdfs[stem], excels[stem], doc_id=stem,
                              is_variant=stem in variants, use_cv=args.cv,
                              target_pdf=originals.get(stem),
                              require_target=bool(originals))
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
    # The hash is the committable proof that a split is frozen: every report
    # embeds it, and compare refuses runs whose splits differ. The file itself
    # lists part numbers and stays outside the repo.
    print(json.dumps({
        "written_to": str(path),
        "splits_hash": splits_hash(splits),
        "seed": splits["seed"],
        "train": len(splits["train"]),
        "dev": len(splits["dev"]),
        "test": len(splits["test"]),
        "variants": len(splits["variants"]),
        "variants_all_in_test": set(splits["variants"]) <= set(splits["test"]),
    }, indent=1))
    return 0


def _reusable_dump(path: Path, config: RunConfig):
    """An already-written dump, but only if THIS config produced it.

    Resume exists so a 20-document run survives an interruption without paying
    for the documents it already finished. It must not become a way to blend two
    pipelines into one run: `score` refuses to mix configs, and a dump written
    before a code change is not the same measurement. An unreadable dump is
    treated as absent and simply re-predicted."""
    if not path.exists():
        return None
    try:
        dump = load_dump(path)
    except Exception:
        return None
    return dump if dump.config == config else None


def _predict_extra(detect_only: bool = False) -> dict:
    """Everything RunConfig.extra must carry for a dump to be identifiable.

    A function rather than an expression inside _cmd_predict so it can be
    tested without loading a checkpoint. Every key here exists because two runs
    were once indistinguishable without it, and _reusable_dump compares the
    WHOLE RunConfig: a missing key does not merely lose information, it makes a
    re-run skip documents as "already predicted" across the change being
    measured.
    """
    from app.pipeline.detect import active_knobs
    from app.pipeline.extract import active_crop_knobs
    from app.pipeline.ocr.hybrid_backend import active_hybrid_config
    from app.pipeline.ocr.vlm_backend import (active_adapter,
                                              active_adapter_scope,
                                              active_prompts, active_quant)
    from app.pipeline.review import active_review_policy
    # extra carries the detection knobs actually in effect. Without them two
    # experiment arms yield indistinguishable reports, and _reusable_dump —
    # which compares the whole RunConfig — would skip a document as "already
    # predicted" after a knob change. git_sha is always "unknown" in the
    # container (.git is dockerignored), so this is the only thing that tells
    # one arm's dumps from another's.
    return {**active_knobs(), **active_prompts(),
            # The needs-review threshold. Pipeline behaviour, not scoring
            # policy, so it is baked into the dumps and nothing downstream can
            # recover it -- and two runs either side of a change to it are
            # otherwise indistinguishable in every field above.
            **active_review_policy(),
            # model_id cannot express HOW the weights were loaded, and Rung
            # 3's control differs from its arm only in whether an adapter is
            # attached. Without these two the runs would be indistinguishable
            # in every report they produce.
            **({"quant": active_quant()} if active_quant() else {}),
            # Which serving stack produced these dumps. Only recorded when
            # it is not the transformers path, so every historical dump keeps
            # the config it already has and stays reusable.
            **({"serving_backend": _serving_backend()}
               if _serving_backend() else {}),
            **({"adapter": active_adapter(),
                # Which pass the adapter is served over. r3-lora72bnf4 served
                # it over the whole model and the scoped arm serves it over
                # the read pass; they agree in every other field here, so
                # without this the void arm's dumps and the real one's are
                # indistinguishable and _reusable_dump could skip a document
                # as "already predicted" across the change being measured.
                "adapter_scope": active_adapter_scope()}
               if active_adapter() else {}),
            # What crop the reader was handed. r3-hybrid measured this as the
            # dominant term in read accuracy (-0.206 for the boxes against
            # +0.013 for the reader), and it is absent on every dump before
            # 2026-09-14 -- which means "the defaults", not "unknown".
            **active_crop_knobs(),
            # Which weights LOCALISED. model_id names the read model,
            # because that is where the values come from, so on a hybrid run it
            # is the only record of the variable the arm is about.
            **active_hybrid_config(),
            # A boxes-only dump carries no values and must never be scored.
            # Recording it here is what stops a resume, a compare, or a human
            # from mistaking one for a full run.
            **({"detect_only": True} if detect_only else {})}


def _cmd_predict(args):
    import os
    from app.pipeline.ocr import get_backend
    backend = get_backend()
    config = RunConfig(
        model_id=os.environ.get("VLM_MODEL_ID", "default"), dpi=args.dpi,
        git_sha=_git_sha(), prompt_sha256=_prompt_sha256(),
        extra=_predict_extra(getattr(args, "detect_only", False)))
    pdfs = {p.stem: p for p in Path(args.pdfs).glob(_PDF_GLOB)}
    doc_ids, _, _ = _select_docs(pdfs, args.splits, args.split)
    # Must precede _anon(): constructing an Anonymizer mints the salt.
    if not getattr(args, "show_ids", False) and not salt_is_persistent():
        print("WARNING: no persistent doc-id salt (SINDRI_DOC_SALT unset, "
              "~/.claude/sindri-doc-salt absent) — the hashed ids below are "
              "throwaway and cannot be joined to a locally-scored report. "
              "Read per-document facts from `runner summary` instead.",
              file=sys.stderr)
    anon = _anon(args)
    out = Path(args.out)
    predicted = skipped = 0
    failures, effective_dpi = [], {}
    for i, doc_id in enumerate(doc_ids, 1):
        tag = f"[{i}/{len(doc_ids)}] {anon(doc_id)}"
        done = _reusable_dump(out / f"{doc_id}.pred.json", config)
        if done is not None:
            skipped += 1
            effective_dpi[doc_id] = done.scale * 72.0
            print(f"{tag} skipped (already predicted)", file=sys.stderr)
            continue
        try:
            dump = predict_one(pdfs[doc_id], doc_id, args.dpi, backend, config,
                               out / "_work",
                               detect_only=getattr(args, "detect_only", False))
        except Exception as e:
            # One unreadable drawing must cost one document, not the run. The
            # exception CLASS is recorded and its message deliberately dropped:
            # a message can carry the client's file path, and this log is meant
            # to stay safe to read.
            failures.append({"doc": anon(doc_id), "error": type(e).__name__})
            print(f"{tag} FAILED {type(e).__name__}", file=sys.stderr)
            continue
        save_dump(dump, out)
        predicted += 1
        effective_dpi[doc_id] = dump.scale * 72.0
        print(f"{tag} dpi={dump.scale * 72.0:.0f}", file=sys.stderr)
    # Oversized sheets render below the requested dpi (render.MAX_RENDER_PIXELS).
    # Naming them keeps "did the misses cluster on the clamped drawings?" an
    # answerable question rather than a guess.
    clamped = sorted(anon(d) for d, eff in effective_dpi.items()
                     if eff < args.dpi - 0.5)
    print(json.dumps({"n_selected": len(doc_ids), "predicted": predicted,
                      "skipped": skipped, "failed": len(failures),
                      "clamped_dpi_docs": clamped, "failures": failures},
                     indent=1, ensure_ascii=False))
    # Partial failure still leaves a run worth pulling and scoring, so it exits
    # 0; a run that produced nothing at all is a failure.
    return 1 if failures and not (predicted or skipped) else 0


def _page_counts(pdf_dir) -> dict:
    """doc_id -> sheet count for every drawing in `pdf_dir`.

    Opened with fitz rather than probed, because page_count is the one fact
    needed here and probe_pdf also recovers balloons, which costs real time on a
    large-format sheet and would be thrown away."""
    import fitz
    out = {}
    for path in sorted(Path(pdf_dir).glob(_PDF_GLOB)):
        doc = fitz.open(path)
        try:
            out[path.stem] = doc.page_count
        finally:
            doc.close()
    return out


def _serving_backend(env=None):
    """The serving stack in effect, or None for the transformers VLM path.

    Route B serves the same base and the same adapter as route A but through
    vLLM, so without this every other recorded field matches and a vLLM run is
    indistinguishable from a transformers one -- and _reusable_dump, which
    compares the whole RunConfig, could skip documents as "already predicted"
    across a change of serving stack.

    None for "vlm" and for absent, because every dump in the campaign was
    predicted on the transformers path: recording it for them would break dump
    reuse against the whole corpus for no measurement. Absence therefore means
    transformers, never "unknown"."""
    import os          # local, matching _cmd_predict — this module has no
                       # module-scope os and predict is the only other user
    choice = (os.environ if env is None else env).get("OCR_BACKEND", "")
    return choice.lower() if choice.lower() not in ("", "vlm") else None


def _cmd_score(args):
    gold = _load_gold_dir(args.gold)
    dumps = {d.doc_id: d for d in
             (load_dump(p) for p in sorted(Path(args.run).glob("*.pred.json")))}
    weights = (ReviewCostWeights.model_validate_json(
                   Path(args.weights).read_text()) if args.weights
               else ReviewCostWeights())
    # Default "none", so an ordinary score run is byte-identical to before. A
    # reconciled run records the mode in match_params, which makes compare_runs
    # refuse it against an unreconciled baseline instead of crediting the
    # difference as an improvement.
    params = MatchParams(reconcile_frames=getattr(args, "reconcile_frames",
                                                  "none"),
                         assignment=getattr(args, "assignment", "greedy"))
    if params.assignment != "greedy":
        print(f"NOTE: scoring with assignment={params.assignment} — pairs are "
              f"augmented to maximum cardinality. Recorded in match_params; not "
              f"comparable to a greedy-scored run.", file=sys.stderr)
    if params.reconcile_frames != "none":
        print(f"NOTE: scoring with reconcile_frames={params.reconcile_frames} — "
              f"gold positions are mapped into each dump's page space. This is a "
              f"DIAGNOSTIC scoring mode; the result is not comparable to a run "
              f"scored without it.", file=sys.stderr)
    max_pages = getattr(args, "max_pages", None)
    if max_pages is not None and not getattr(args, "pdfs", None):
        # Silently scoring the full corpus while the operator believes it was
        # filtered would misattribute the difference to whatever else changed.
        print("ERROR: --max-pages needs --pdfs — page counts are only in the "
              "drawings themselves, and there is nowhere else to read them",
              file=sys.stderr)
        return 1
    # Split members come from GOLD, not from gold-intersect-dumps: a member with
    # no dump has to survive selection to be COUNTED as missing. Intersecting
    # first is what let two runs be scored mid-flight on 2026-09-14 -- 5 of 15
    # documents and 7 of 19 -- and print headline numbers that read like
    # results. The "gold docs without dumps" warning could not catch it: it is
    # computed over the whole gold set, so on a dev run it always names the ~80
    # documents that are simply in other splits, and a warning that fires
    # identically on every healthy run carries no signal.
    expected, sp_hash, sp_name = _select_docs(set(gold), args.splits, args.split)
    anon = _anon(args)
    missing_preds = sorted(d for d in expected if d not in dumps)
    # PARTIAL means some-but-not-all. A run with no overlapping dumps at all is
    # a different fault -- an empty or mismatched run directory -- and the
    # "no documents scored" error below names it better than "this run has not
    # finished predicting" would.
    if (missing_preds and len(missing_preds) < len(expected)
            and not getattr(args, "allow_partial", False)):
        print(f"ERROR: {len(missing_preds)} of {len(expected)} document(s) in split "
              f"{sp_name!r} have no prediction dump — this run has not finished "
              f"predicting. Scoring it would produce a number over a different "
              f"document set than every control it will be compared against, "
              f"and _check_comparable cannot catch that because the report "
              f"would simply describe the smaller set. Wait for the run, or "
              f"pass --allow-partial to score it deliberately. Missing: "
              f"{[anon(d) for d in missing_preds]}", file=sys.stderr)
        return 1
    doc_ids = [d for d in expected if d in dumps]
    if max_pages is not None:
        pages = _page_counts(args.pdfs)
        unknown = [d for d in doc_ids if d not in pages]
        if unknown:
            print(f"ERROR: no drawing found for {len(unknown)} scored document(s) "
                  f"{[anon(d) for d in unknown]}, so their page count is unknown. "
                  f"Refusing to guess — point --pdfs at the corpus these dumps "
                  f"were predicted from", file=sys.stderr)
            return 1
        dropped = [d for d in doc_ids if pages[d] > max_pages]
        doc_ids = [d for d in doc_ids if pages[d] <= max_pages]
        if dropped:
            print(f"excluded {len(dropped)} drawing(s) over {max_pages} "
                  f"sheet(s): {[anon(d) for d in dropped]}", file=sys.stderr)
    orphan_dumps = sorted((set(gold) & set(dumps)) ^ set(dumps))
    if orphan_dumps:
        print(f"WARNING: dumps without gold (excluded): "
              f"{[anon(d) for d in orphan_dumps]}", file=sys.stderr)
    orphan_gold = sorted(set(gold) - set(dumps))
    if orphan_gold:
        print(f"WARNING: gold docs without dumps (excluded): "
              f"{[anon(d) for d in orphan_gold]}", file=sys.stderr)
    if getattr(args, "exclude_clamped", False):
        # scale is render pixels per PDF point, so effective dpi is scale * 72.
        # A page the renderer had to clamp comes back below the dpi asked for.
        # The tolerance absorbs the rounding in that conversion; it is not a
        # judgement about how much clamping is acceptable.
        want = float(getattr(args, "dpi", 300))
        clamped = [d for d in doc_ids if dumps[d].scale * 72.0 < want - 1.0]
        doc_ids = [d for d in doc_ids if d not in set(clamped)]
        if clamped:
            print(f"excluded {len(clamped)} oversized (render-clamped) "
                  f"drawing(s) as out of scope: {[anon(d) for d in clamped]}",
                  file=sys.stderr)

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
                       splits_hash=sp_hash, split_used=sp_name,
                       max_pages=max_pages,
                       exclude_clamped=getattr(args, "exclude_clamped", False),
                       missing_dumps=len(missing_preds))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report.model_dump_json(indent=1),
                              encoding="utf-8")
    print(f"{args.name}: docs={len(scores)} "
          f"mean_review_cost={report.mean_review_cost:.2f} "
          f"recall={report.micro_recall:.3f} "
          f"escaped_rate={report.escaped_rate:.3f}")
    # A DIAGNOSTIC that deliberately does not touch the written report: it
    # re-parses raw_text with today's parser and prices a hypothetical parser
    # change, so the report stays exactly as comparable as it was.
    if getattr(args, "reparse_check", False):
        from app.eval.reparse import reparse_report
        print(json.dumps(reparse_report(dumps, gold, scores), indent=1))
    return 0


def _cmd_compare(args):
    a = RunReport.model_validate_json(Path(args.report_a).read_text())
    b = RunReport.model_validate_json(Path(args.report_b).read_text())
    # Built BEFORE the comparison: compare_runs raises on incomparability, and
    # that message used to interpolate the raw doc_id straight to the terminal.
    anon = _anon(args)
    try:
        cmp = compare_runs(a, b, anonymizer=anon)
    except ValueError as e:
        print(f"NOT COMPARABLE: {e}", file=sys.stderr)
        return 1
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
    p.add_argument("--originals", default=None,
                   help="clean drawings the PIPELINE reads. --pdfs must be the "
                        "STAMPED set (only it carries balloons); pass this and "
                        "recovered positions are mapped into the originals' page "
                        "space. Without it gold geometry stays in the stamped "
                        "sheet's space, which cost the Rung-0 baseline 141 "
                        "matches. Changes gold_hash, so re-score any report.")
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
    p.add_argument("--detect-only", action="store_true",
                   help="stop after detection and write boxes with no "
                        "transcription. For building training crops: reads are "
                        "the bulk of per-document time, so this is much cheaper "
                        "-- but the dumps carry NO values and must never be "
                        "scored. Recorded in RunConfig.extra.")
    p.set_defaults(fn=_cmd_predict)

    p = sub.add_parser("score", parents=[common])
    p.add_argument("--run", required=True); p.add_argument("--gold", required=True)
    p.add_argument("--name", required=True); p.add_argument("--out", required=True)
    p.add_argument("--splits", default=None); p.add_argument("--split", default="dev")
    p.add_argument("--weights", default=None)
    p.add_argument("--pdfs", default=None,
                   help="source drawings, needed only to read page counts for "
                        "--max-pages")
    p.add_argument("--dpi", type=int, default=300,
                   help="the dpi the run REQUESTED; a dump rendered below it "
                        "was clamped. Only read by --exclude-clamped.")
    p.add_argument("--allow-partial", action="store_true",
                   help="score a run that has not finished predicting. The "
                        "count is recorded in the report as missing_dumps, "
                        "because a partial number must never be quotable "
                        "later without its caveat.")
    p.add_argument("--exclude-clamped", action="store_true",
                   help="drop render-clamped (oversized) sheets as out of "
                        "scope. render.py clamps any page over the pixel "
                        "budget, and raising that budget was measured and lost, "
                        "so these sheets need tiling that does not exist.")
    p.add_argument("--max-pages", type=int, default=None,
                   help="score only drawings with at most this many sheets. "
                        "render_page reads sheet 1 only, so gold on later "
                        "sheets is an unrecoverable miss. Off by default: every "
                        "committed measurement scored the whole split.")
    p.add_argument("--reconcile-frames", choices=("none", "scale", "center"),
                   default="none",
                   help="DIAGNOSTIC: map gold balloon positions into each dump's "
                        "page space before matching. Gold geometry comes from the "
                        "stamped drawings and predictions from the clean "
                        "originals; where those pages differ in extent the two "
                        "are not comparable. Recorded in match_params, so a "
                        "reconciled report refuses to compare against a plain one.")
    p.add_argument("--assignment", choices=("greedy", "max_cardinality"), default="greedy",
                   help="DIAGNOSTIC. greedy (default) is what every report has "
                        "used; it strands a gold row whenever its only in-gate "
                        "prediction is nearest a different row. max_cardinality "
                        "augments those back, but measured on dev it recovered 26 "
                        "misses by destroying 27 correct pairings and cut field "
                        "accuracy on matched rows from 36.4%% to 25.4%% -- it "
                        "maximises pairings, not true ones. Recorded in "
                        "match_params.")
    p.add_argument("--reparse-check", action="store_true",
                   help="DIAGNOSTIC: re-parse each matched pair's raw_text with "
                        "today's parser and print how many rows a parser change "
                        "would fix or break. Counts only, no values. Does not "
                        "alter the written report. On an unmodified parser "
                        "identical must equal n_pairs -- anything else means the "
                        "hint reconstruction is wrong, not the parser.")
    p.set_defaults(fn=_cmd_score)

    p = sub.add_parser("compare", parents=[common])
    p.add_argument("report_a"); p.add_argument("report_b")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=_cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
