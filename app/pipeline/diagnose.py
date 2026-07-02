"""Pipeline diagnostics you can run on a remote machine and paste back.

Two modes:

* CV-only (default) — renders the drawing and reports what the deterministic
  locators find (marks legend, title block). Needs no VLM model, so it is safe
  to run anywhere and is enough to check the marks-block fix.
* Full (``--vlm``) — additionally runs the whole extraction pipeline through the
  configured backend and summarises the characteristics (kind histogram,
  duplicate detections, review flags) plus the marks/notes/title counts.

Both modes print a single JSON blob to stdout and, unless ``--no-image`` is
given, save an annotated PNG (marks = red, title = blue, notes = green,
balloons = orange) next to the rendered page for a visual check.

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


def summarize_result(result) -> dict:
    """Summarise an ExtractionResult for a quick correctness read: counts,
    kind/char-type histograms, likely duplicates, and review-flag totals."""
    chars = result.characteristics
    return {
        "characteristics": len(chars),
        "by_kind": _hist(c.kind for c in chars if c.kind),
        "by_char_type": _hist(c.char_type for c in chars if c.char_type),
        "needs_review": sum(1 for c in chars if c.needs_review),
        "potential_duplicates": _potential_duplicates(chars),
        "marks": len(result.marks.marks) if result.marks is not None else None,
        "notes": len(result.notes.notes) if result.notes is not None else None,
        "title_fields": len(result.title_block),
    }


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
        try:
            from app.pipeline.extract import extract
            result = extract(pdf_path, out_dir, dpi=dpi)
            report["extraction"] = summarize_result(result)
        except Exception as e:
            report["extraction_error"] = repr(e)

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
