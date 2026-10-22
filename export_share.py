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

Or single drawings by real part number, without naming the run (the shipped-policy
controls are searched, dev first):

    python export_share.py --ids IPB_21-0010 IPB_22-1463 --out ~/client-share

Every export re-applies the plain-rectangle filter to the stored rows (boxed note
numbers / datum letters / basic dimensions are dropped, rounded boxes and GD&T
frames kept), renumbers, and re-places every balloon in blank space on the page --
so dumps taken before those existed come out as a fresh run would. --keep-boxed
turns the filter off (balloons are still re-placed).

--run is a run directory name under <root>/runs, or a path. Hashes are the ids
`runner summary` prints; they resolve here against the dumps, so no real part
number is needed (--ids takes real ids instead). The output folder must be
outside the protected root and any git tree. Terminal output is hashes only.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

from app.eval.anon import Anonymizer
from app.eval.dump import load_dump
from app.excel import write_workbook
from app.pipeline import boxes as bx
from app.pipeline.ballooned_pdf import render_ballooned_pdf
from app.pipeline.place import InkMap, number_characteristics, place_balloons
from app.pipeline.render import render_page

_PRED = ".pred.json"
# Searched, in order, when --ids/--hashes come without --run.
DEFAULT_RUNS = ("r4-control", "r4-controltest")


# A render at the dump's scale reproduces it to float precision (render_page
# only computes dpi / 72), so anything above rounding noise is a real clamp.
_SCALE_TOL = 1e-6


class ScaleMismatch(RuntimeError):
    """The drawing no longer renders at the scale its dump was predicted at
    (the pixel budget clamps it now), so the stored pixel boxes and a fresh
    render are in different frames."""


def check_out_dir(out, root) -> None:
    """Refuse an output folder inside the protected root or inside any git
    tree (raises ValueError). Both tools write client drawings and values, and
    either place would put them where the guard or a commit can reach them."""
    out = Path(out).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    if out == root or root in out.parents:
        raise ValueError(f"--out {out} is inside the protected root {root}")
    for p in (out, *out.parents):
        if (p / ".git").exists():
            raise ValueError(f"--out {out} is inside the git tree at {p}")


def refresh_result(rows, pdf_path, scale: float, drop_boxed: bool = True,
                   suggestions=(), strict: bool = False, page_out=None):
    """(rows, suggestions, n_dropped): the stored rows AND tray suggestions as
    a fresh run would deliver them -- minus those inside a plain rectangle
    (when `drop_boxed`; n_dropped counts both), confirmed rows renumbered, and
    every balloon re-placed in blank space on the page: the confirmed rows
    first, then the suggestions in their own pass, as extract.py does, so a
    suggestion can never push a real balloon off its callout. Suggestions
    keep pos 0.

    The page is rendered at the dump's own scale, because the stored boxes are
    pixels at that scale; a mismatch (budget clamp changed) leaves everything
    untouched rather than compare boxes in the wrong frame -- or, with
    `strict`, raises ScaleMismatch. `page_out` keeps a copy of that render,
    so a caller showing the page gets exactly the frame the boxes were
    checked against."""
    rows, suggestions = list(rows), list(suggestions)
    with tempfile.TemporaryDirectory() as tmp:
        # The float dpi, not a rounded one: a clamped dump's scale is not an
        # integer dpi, and rounding would render a slightly different page.
        render = render_page(pdf_path, dpi=scale * 72.0, out_dir=tmp)
        if abs(render.scale - scale) > _SCALE_TOL * scale:
            msg = f"render scale {render.scale:.4f} != dump scale {scale:.4f}"
            if strict:
                raise ScaleMismatch(msg)
            print(f"WARN {msg}: rows left as stored", file=sys.stderr)
            return rows, suggestions, 0
        if page_out is not None:
            shutil.copyfile(render.png_path, page_out)
        with Image.open(render.png_path) as im:
            im = im.convert("RGB")
            rects = bx.find_plain_rectangles(im) if drop_boxed else []
            ink = InkMap.from_image(im)

    def unboxed(cs):
        return [c for c in cs
                if not (c.target_region and bx.in_plain_rectangle(c.target_region, rects))]
    kept, kept_s = unboxed(rows), unboxed(suggestions)
    kept = number_characteristics(kept)
    place_balloons(kept, dpi=scale * 72.0, ink=ink)
    place_balloons(kept_s, dpi=scale * 72.0, ink=ink)
    return kept, kept_s, len(rows) + len(suggestions) - len(kept) - len(kept_s)


def refresh_rows(rows, pdf_path, scale: float, drop_boxed: bool = True):
    """(rows, n_dropped): refresh_result for confirmed rows only -- what the
    client-share export writes (it never exports suggestions)."""
    kept, _, n = refresh_result(rows, pdf_path, scale, drop_boxed)
    return kept, n


def export_one(dump_path: Path, pdf_path: Path, out_dir: Path,
               keep_boxed: bool = False) -> tuple:
    dump = load_dump(dump_path)
    res = dump.result
    rows = [c for c in res.characteristics if not c.suggested]
    scale = res.render_scale or dump.scale
    rows, _ = refresh_rows(rows, pdf_path, scale, drop_boxed=not keep_boxed)
    stem = dump.doc_id
    xlsx = out_dir / f"{stem}.xlsx"
    pdf = out_dir / f"{stem}_ballooned.pdf"
    write_workbook(rows, xlsx, notes=res.notes, marks=res.marks,
                   title_block=res.title_block)
    # render_ballooned_pdf takes dpi and divides by 72; the render clamps dpi on
    # large sheets, so hand it the scale the dump was produced at.
    render_ballooned_pdf(pdf_path, rows, dpi=scale * 72.0, out_path=pdf)
    return xlsx, pdf, len(rows)


def export_run(run_dir: Path, pdfs: Path, out_dir: Path, ids=(), hashes=(),
               keep_boxed: bool = False) -> int:
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
        _, _, n = export_one(dump_path, pdf_path, out_dir, keep_boxed)
        print(f"ok   {anon(doc_id)}: {n} rows")
    return failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("SINDRI_CLIENT_ROOT",
                                                     str(Path.home() / "sindri-client-data")))
    ap.add_argument("--pdfs", help="clean drawings dir (default <root>/corpus/originals)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", help="json from share_select: {runs:[{run,hashes}]}")
    ap.add_argument("--run", action="append",
                    help="run dir name under <root>/runs, or a path; repeat to search "
                         f"several (default for --ids/--hashes: {', '.join(DEFAULT_RUNS)})")
    ap.add_argument("--keep-boxed", action="store_true",
                    help="do not drop rows inside plain rectangles")
    ap.add_argument("--ids", nargs="+", default=[], help="real doc ids")
    ap.add_argument("--hashes", nargs="+", default=[], help="salted ids")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser()
    pdfs = Path(args.pdfs) if args.pdfs else root / "corpus" / "originals"
    jobs = []     # (run_dir, ids, hashes)
    if args.manifest:
        for entry in json.loads(Path(args.manifest).read_text(encoding="utf-8"))["runs"]:
            jobs.append((root / "runs" / entry["run"], [], entry["hashes"]))
    if args.ids or args.hashes:
        runs = [Path(r) if Path(r).is_dir() else root / "runs" / r
                for r in (args.run or DEFAULT_RUNS)]
        # The first run holding a dump for the doc wins; a doc lives in one split.
        for doc_id in args.ids:
            run = next((r for r in runs if (r / f"{doc_id}{_PRED}").is_file()), runs[0])
            jobs.append((run, [doc_id], []))
        anon = Anonymizer()
        for h in args.hashes:
            run = next((r for r in runs if any(anon(p.name[:-len(_PRED)]) == h
                                               for p in r.glob(f"*{_PRED}"))), runs[0])
            jobs.append((run, [], [h]))
    if not jobs or not any(i or h for _, i, h in jobs):
        print("nothing to export: give --manifest, or --ids/--hashes (optionally "
              "with --run)", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser()
    try:
        check_out_dir(out_dir, root)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = sum(export_run(r, pdfs, out_dir, ids, hashes, args.keep_boxed)
                 for r, ids, hashes in jobs)
    print(f"\n{len(list(out_dir.glob('*.xlsx')))} drawing/Excel pairs in {out_dir}"
          + (f"  ({failed} FAILED -- see above)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
