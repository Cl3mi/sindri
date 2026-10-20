"""Write OUR ballooned PDF + Excel for chosen drawings, from a predict run's dumps.

Operator tool: it opens client drawings, so it is NOT in the agent allowlist and
must be run from your own terminal. It renders exactly what the app would export
-- confirmed rows only (tray suggestions are never written), via the same
write_workbook / render_ballooned_pdf the export route uses -- but straight from
the prediction dump, so no review session is needed.

The usual route: an agent runs `python -m app.eval.share_select` (hashes only)
and hands you one command:

    python export_share.py --manifest <manifest.json> --out ~/client-share

Or by hand, one run at a time:

    python export_share.py --run r4-control --hashes 8e5fb4d8 f1639571 --out ~/client-share

--run is a run directory name under <root>/runs, or a path. Hashes are the ids
`runner summary` prints; they resolve here against the dumps, so no real part
number is needed (--ids takes real ids instead). The output folder must be
outside the protected root and any git tree. Terminal output is hashes only.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from app.eval.anon import Anonymizer
from app.eval.dump import load_dump
from app.excel import write_workbook
from app.pipeline.ballooned_pdf import render_ballooned_pdf

_PRED = ".pred.json"


def export_one(dump_path: Path, pdf_path: Path, out_dir: Path) -> tuple:
    dump = load_dump(dump_path)
    res = dump.result
    rows = [c for c in res.characteristics if not c.suggested]
    scale = res.render_scale or dump.scale
    stem = dump.doc_id
    xlsx = out_dir / f"{stem}.xlsx"
    pdf = out_dir / f"{stem}_ballooned.pdf"
    write_workbook(rows, xlsx, notes=res.notes, marks=res.marks,
                   title_block=res.title_block)
    # render_ballooned_pdf takes dpi and divides by 72; the render clamps dpi on
    # large sheets, so hand it the scale the dump was produced at.
    render_ballooned_pdf(pdf_path, rows, dpi=scale * 72.0, out_path=pdf)
    return xlsx, pdf, len(rows)


def export_run(run_dir: Path, pdfs: Path, out_dir: Path, ids=(), hashes=()) -> int:
    """Export ids/hashes from one run dir; returns the number that failed."""
    anon = Anonymizer()
    run_dir = Path(run_dir)
    ids = list(ids)
    failed = 0
    if hashes:
        by_hash = {anon(p.name[:-len(_PRED)]): p.name[:-len(_PRED)]
                   for p in run_dir.glob(f"*{_PRED}")}
        for h in hashes:
            if h in by_hash:
                ids.append(by_hash[h])
            else:
                print(f"SKIP {h}: no dump in {run_dir.name} hashes to this id",
                      file=sys.stderr)
                failed += 1
    for doc_id in ids:
        dump_path = run_dir / f"{doc_id}{_PRED}"
        pdf_path = pdfs / f"{doc_id}.pdf"
        if not dump_path.is_file() or not pdf_path.is_file():
            print(f"SKIP {anon(doc_id)}: missing "
                  f"{'dump' if not dump_path.is_file() else 'drawing'}", file=sys.stderr)
            failed += 1
            continue
        _, _, n = export_one(dump_path, pdf_path, out_dir)
        print(f"ok   {anon(doc_id)}: {n} rows")
    return failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("SINDRI_CLIENT_ROOT",
                                                     str(Path.home() / "sindri-client-data")))
    ap.add_argument("--pdfs", help="clean drawings dir (default <root>/corpus/originals)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", help="json from share_select: {runs:[{run,hashes}]}")
    ap.add_argument("--run", help="run dir name under <root>/runs, or a path")
    ap.add_argument("--ids", nargs="+", default=[], help="real doc ids")
    ap.add_argument("--hashes", nargs="+", default=[], help="salted ids")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser()
    pdfs = Path(args.pdfs) if args.pdfs else root / "corpus" / "originals"
    jobs = []     # (run_dir, ids, hashes)
    if args.manifest:
        for entry in json.loads(Path(args.manifest).read_text(encoding="utf-8"))["runs"]:
            jobs.append((root / "runs" / entry["run"], [], entry["hashes"]))
    if args.run:
        run = Path(args.run)
        jobs.append((run if run.is_dir() else root / "runs" / args.run,
                     args.ids, args.hashes))
    if not jobs or not any(i or h for _, i, h in jobs):
        print("nothing to export: give --manifest, or --run with --ids/--hashes",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = sum(export_run(r, pdfs, out_dir, ids, hashes) for r, ids, hashes in jobs)
    print(f"\n{len(list(out_dir.glob('*.xlsx')))} drawing/Excel pairs in {out_dir}"
          + (f"  ({failed} FAILED -- see above)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
