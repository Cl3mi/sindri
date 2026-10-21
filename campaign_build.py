"""Build a review-campaign bundle from stored prediction dumps
(docs/plans/2026-10-21-client-review-campaign-design.md §2).

OPERATOR TOOL: it opens client drawings and dumps, so it is NOT in the agent
allowlist (CLAUDE.md §1) -- an agent selects (share_select, hashes only) and
hands you ONE command:

    python campaign_build.py --manifest ~/campaign-manifest.json --name pilot-1 \
        --out ~/campaign-pilot-1

Each pick becomes <out>/<hash>/ with input.pdf, page.png, result.json and
campaign.json -- exactly what app.campaign.load expects. Rows are what we
would ship TODAY (plan C1 decision 4): today's flags/drops/tray via
reapply_current_code, then the shipped plain-rectangle drop, renumbering and
whitespace balloon placement via export_share.refresh_rows. Terminal output
is hashes only. The output folder must stay outside the protected root and
any git tree.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from app.batch.worker import result_payload
from app.eval.anon import Anonymizer
from app.eval.dump import load_dump
from app.eval.reapply import reapply_current_code
from app.pipeline.render import render_page
from export_share import _PRED, refresh_rows


class ScaleMismatch(RuntimeError):
    """The drawing no longer renders at the scale its dump was predicted at.

    refresh_rows only WARNS in that case and leaves the rows as stored, which
    is right for a PDF export (it draws at the dump's own dpi) but wrong here:
    the review screen draws the rows' pixel boxes straight onto page.png, so a
    page at another scale would show every box in the wrong place. The entry
    is refused instead."""


def build_entry(dump_path: Path, pdf_path: Path, out: Path, *, campaign: str,
                group: str, run: str, doc_hash: str) -> Path:
    dump = reapply_current_code(load_dump(dump_path))
    res = dump.result
    scale = res.render_scale or dump.scale
    entry = Path(out) / doc_hash
    with tempfile.TemporaryDirectory() as tmp:
        # The page the app shows must be the render the rows' pixel boxes live
        # in: the dump's own scale, never the app's default dpi. render_page
        # may clamp the dpi under its pixel budget, so check the scale it
        # actually used BEFORE anything is written -- no half-built entry.
        render = render_page(pdf_path, dpi=int(round(scale * 72.0)), out_dir=tmp)
        if abs(render.scale - scale) > 1e-3 * scale:
            raise ScaleMismatch(f"{doc_hash}: render scale {render.scale:.4f} "
                                f"!= dump scale {scale:.4f}")
        # Suggestions are not re-placed: refresh_rows renumbers and places
        # confirmed balloons only, and a tray row is pos 0 by construction.
        rows, _ = refresh_rows(list(res.characteristics), pdf_path, scale)
        res = res.model_copy(update={"characteristics": rows})
        entry.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(render.png_path, entry / "page.png")
    shutil.copyfile(pdf_path, entry / "input.pdf")
    (entry / "result.json").write_text(json.dumps(result_payload(res)), encoding="utf-8")
    (entry / "campaign.json").write_text(json.dumps({
        "campaign": campaign, "group": group, "source_run": run,
        "doc_hash": doc_hash, "file_name": f"{dump.doc_id}.pdf"}), encoding="utf-8")
    return entry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("SINDRI_CLIENT_ROOT",
                                                     str(Path.home() / "sindri-client-data")))
    ap.add_argument("--manifest", required=True, help="share_select output, with roles")
    ap.add_argument("--name", required=True, help="campaign name, e.g. pilot-1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8"))
    roles = m.get("roles") or {}
    anon = Anonymizer()
    failed = 0
    for entry in m["runs"]:
        run_dir = root / "runs" / entry["run"]
        by_hash = {anon(p.name[:-len(_PRED)]): p for p in run_dir.glob(f"*{_PRED}")}
        for h in entry["hashes"]:
            dump_path = by_hash.get(h)
            pdf = root / "corpus" / "originals" / (
                dump_path.name[:-len(_PRED)] + ".pdf") if dump_path else None
            if dump_path is None or not pdf.is_file() or h not in roles:
                print(f"SKIP {h}: {'no dump' if dump_path is None else 'no drawing' if not pdf.is_file() else 'no role'}",
                      file=sys.stderr)
                failed += 1
                continue
            try:
                build_entry(dump_path, pdf, out, campaign=args.name, group=roles[h],
                            run=entry["run"], doc_hash=h)
            except ScaleMismatch:
                # The message names the hash only; never the doc id.
                print(f"SKIP {h}: page no longer renders at the dump's scale",
                      file=sys.stderr)
                failed += 1
                continue
            print(f"ok   {h} ({roles[h]})")
    n = sum(1 for p in out.iterdir() if p.is_dir())
    print(f"\n{n} drawings in {out}" + (f"  ({failed} FAILED -- see above)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
