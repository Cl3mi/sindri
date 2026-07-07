"""Pipeline diagnostics you can run on a remote machine and paste back.

Two modes:

* CV-only (default) — renders the drawing and reports what the deterministic
  locators find (marks legend, title block). Needs no VLM model, so it is safe
  to run anywhere and is enough to check the marks-block fix.
* Full (``--vlm``) — additionally runs the whole extraction pipeline through the
  configured backend and summarises the characteristics (kind histogram,
  duplicate detections, review flags) plus the marks/notes/title counts.

Both modes print a single JSON blob to stdout and, unless ``--no-image`` is
given, save an annotated PNG next to the rendered page for a visual check:
marks = red, title = blue, notes = green, characteristic boxes = orange, and —
in ``--vlm`` mode — each balloon marker + its leader line to the callout (also
orange) so placement distance is obvious.

    python -m app.pipeline.diagnose test_docs/T1025300_B.pdf
    python -m app.pipeline.diagnose drawing.pdf --vlm --out /tmp/diag
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from app.pipeline.render import render_page
from app.pipeline import marks_block as mb
from app.pipeline import title_block as tb
from app.pipeline.geom import _iou

# IoU above which two characteristics are treated as the same physical callout
# detected twice — the fingerprint of the cross-kind duplication bug.
_DUP_IOU = 0.6


def _box_report(outer, page_w, page_h):
    x0, y0, x1, y1 = outer
    return {
        "outer_box": [int(v) for v in outer],
        "width_frac": round((x1 - x0) / page_w, 3),
        "height_frac": round((y1 - y0) / page_h, 3),
    }


def build_cv_report(image: Image.Image) -> dict:
    """CV-only diagnosis (no VLM). Reports whether the marks legend and title
    block were located and how much of the page each spans."""
    w, h = image.size
    report = {
        "page": {"width": w, "height": h},
        "marks": {"located": False},
        "title_block": {"located": False},
    }
    reg = mb.locate_marks_block(image)
    if reg is not None:
        report["marks"] = {
            "located": True,
            "lang_columns": len(reg.lang_columns),
            **_box_report(reg.outer_box, w, h),
        }
    tbr = tb.locate_title_block(image)
    if tbr is not None:
        report["title_block"] = {
            "located": True,
            "cells": len(tbr.cells),
            **_box_report(tbr.outer_box, w, h),
        }
    return report


def _potential_duplicates(chars) -> list:
    """Pairs of characteristics whose target regions overlap past `_DUP_IOU` —
    should be empty once cross-kind duplicates are resolved."""
    boxed = [c for c in chars if c.target_region is not None]
    dups = []
    for i in range(len(boxed)):
        for j in range(i + 1, len(boxed)):
            iou = _iou(boxed[i].target_region, boxed[j].target_region)
            if iou >= _DUP_IOU:
                dups.append({
                    "pos": [boxed[i].pos, boxed[j].pos],
                    "kinds": [boxed[i].kind, boxed[j].kind],
                    "iou": round(iou, 3),
                })
    return dups


def _hist(values) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _read_sample(c) -> dict:
    tr = c.target_region or (0, 0, 0, 0)
    return {
        "pos": c.pos, "kind": c.kind, "char_type": c.char_type,
        "conf": round(c.confidence, 3),
        "nominal": c.nominal, "upper_tol": c.upper_tol, "lower_tol": c.lower_tol,
        "box_wh": [int(tr[2] - tr[0]), int(tr[3] - tr[1])],
        "raw": (c.raw_text or "").replace("\n", " ")[:40],
    }


def _box_size_stats(chars, page_w, page_h) -> dict:
    """Width/height of characteristic boxes as a fraction of the page — a proxy
    for whether detection boxes are sanely tight (post tighten_to_ink) or still
    engulfing whitespace/leader lines."""
    import statistics as _st
    fr = [((c.target_region[2] - c.target_region[0]) / page_w,
           (c.target_region[3] - c.target_region[1]) / page_h)
          for c in chars if c.target_region is not None]
    if not fr:
        return {}
    return {
        "median_w_frac": round(_st.median(w for w, _ in fr), 4),
        "median_h_frac": round(_st.median(h for _, h in fr), 4),
        "max_w_frac": round(max(w for w, _ in fr), 4),
        "max_h_frac": round(max(h for _, h in fr), 4),
        "oversized": sum(1 for w, h in fr if w > 0.15 or h > 0.10),
    }


def summarize_result(result, page=None) -> dict:
    """Summarise an ExtractionResult for a quick correctness read: counts,
    kind/char-type histograms, likely duplicates, review-flag totals, and — to
    judge read quality and placement — per-read samples, low-confidence reads
    and box-size stats. `page` is an optional (width, height) for the box-size
    fractions; omitted, those stats are skipped."""
    chars = result.characteristics
    out = {
        "characteristics": len(chars),
        "by_kind": _hist(c.kind for c in chars if c.kind),
        "by_char_type": _hist(c.char_type for c in chars if c.char_type),
        "needs_review": sum(1 for c in chars if c.needs_review),
        "potential_duplicates": _potential_duplicates(chars),
        "low_confidence": [_read_sample(c) for c in chars if c.confidence < 0.6][:25],
        "reads": [_read_sample(c) for c in sorted(chars, key=lambda c: c.pos)][:40],
        "marks": len(result.marks.marks) if result.marks is not None else None,
        "notes": len(result.notes.notes) if result.notes is not None else None,
        "title_fields": len(result.title_block),
    }
    if page is not None:
        out["box_size"] = _box_size_stats(chars, page[0], page[1])
    return out


def _raw_summary(raw: str) -> dict:
    return {
        "chars": len(raw),
        "lines": len(raw.splitlines()),
        "has_tab": "\t" in raw,
        "preview": raw[:1000],
    }


def capture_raw_reads(image: Image.Image, backend) -> dict:
    """Instrument the read->parse boundary: return the *raw* VLM transcription of
    the marks and notes blocks (before parsing), plus a `has_tab` flag. When a
    block is located but yields 0 parsed rows, this shows whether the model
    emitted the tab-delimited format the parsers require. Never raises."""
    out: dict = {}
    try:
        reg = mb.locate_marks_block(image)
        if reg is not None:
            out["marks"] = _raw_summary(mb.read_marks_block(image, reg, backend))
    except Exception as e:  # pragma: no cover - defensive
        out["marks_error"] = repr(e)
    try:
        from app.pipeline import notes_block as nb
        nreg = nb.locate_notes_block(image, backend)
        if nreg is not None:
            out["notes"] = _raw_summary(nb.read_notes_block(image, nreg, backend))
    except Exception as e:  # pragma: no cover - defensive
        out["notes_error"] = repr(e)
    return out


def _annotate(image: Image.Image, result=None) -> Image.Image:
    """Draw located regions and balloon markers for a visual sanity check."""
    vis = image.copy()
    d = ImageDraw.Draw(vis)
    reg = mb.locate_marks_block(image)
    if reg is not None:
        d.rectangle(reg.outer_box, outline=(255, 0, 0), width=8)
    tbr = tb.locate_title_block(image)
    if tbr is not None:
        d.rectangle(tbr.outer_box, outline=(0, 0, 255), width=8)
    if result is not None:
        if result.notes is not None:
            d.rectangle(result.notes.region, outline=(0, 170, 0), width=8)
        for c in result.characteristics:
            if c.target_region is not None:
                d.rectangle(c.target_region, outline=(255, 140, 0), width=4)
            # leader line + balloon marker, so placement (distance to the
            # callout) is visible at a glance in the annotated PNG.
            if c.balloon_xy is not None and c.target_region is not None:
                bx, by = c.balloon_xy
                cx = (c.target_region[0] + c.target_region[2]) / 2.0
                cy = (c.target_region[1] + c.target_region[3]) / 2.0
                d.line((bx, by, cx, cy), fill=(255, 140, 0), width=2)
                r = 14
                d.ellipse((bx - r, by - r, bx + r, by + r),
                          outline=(255, 140, 0), width=3)
                d.text((bx - 4, by - 6), str(c.pos), fill=(255, 140, 0))
    return vis


def run(pdf_path: str, dpi: int = 300, out_dir=None, use_vlm: bool = False,
        save_image: bool = True) -> dict:
    """Render `pdf_path`, build the report, optionally run the full pipeline and
    save an annotated image. Returns the JSON-able report dict."""
    out_dir = Path(out_dir) if out_dir else Path(pdf_path).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    render = render_page(pdf_path, dpi=dpi, out_dir=out_dir)
    image = Image.open(render.png_path).convert("RGB")

    report = {"pdf": str(pdf_path), "dpi": dpi, **build_cv_report(image)}
    result = None
    if use_vlm:
        # One backend instance, reused for extraction and raw-read instrumentation
        # so the model is loaded only once.
        from app.pipeline.ocr import get_backend
        from app.pipeline.extract import extract
        backend = get_backend()
        try:
            result = extract(pdf_path, out_dir, dpi=dpi, backend=backend)
            report["extraction"] = summarize_result(result, page=image.size)
        except Exception as e:
            report["extraction_error"] = repr(e)
        report["raw_reads"] = capture_raw_reads(image, backend)

    if save_image:
        annotated = _annotate(image, result)
        img_path = out_dir / (Path(pdf_path).stem + "_diag.png")
        annotated.save(img_path)
        report["annotated_image"] = str(img_path)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sindri pipeline diagnostics")
    ap.add_argument("pdf", help="path to the drawing PDF")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--out", default=None, help="output directory for artifacts")
    ap.add_argument("--vlm", action="store_true",
                    help="also run the full extraction through the VLM backend")
    ap.add_argument("--no-image", action="store_true",
                    help="skip writing the annotated PNG")
    args = ap.parse_args(argv)
    report = run(args.pdf, dpi=args.dpi, out_dir=args.out,
                 use_vlm=args.vlm, save_image=not args.no_image)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
