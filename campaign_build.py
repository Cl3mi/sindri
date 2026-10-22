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
whitespace balloon placement (confirmed rows, then the tray) via
export_share.refresh_result. Terminal output is hashes only, and a failure is
reported by exception TYPE only. The output folder must stay outside the
protected root and any git tree (refused otherwise). Re-running into the same
folder keeps every complete entry and retries the rest.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from PIL import Image

from app.batch.worker import result_payload
from app.eval.anon import Anonymizer
from app.eval.dump import load_dump
from app.eval.reapply import reapply_current_code
from app.pipeline import policy_rules
from export_share import _PRED, ScaleMismatch, check_out_dir, refresh_result

__all__ = ["ScaleMismatch", "build_entry", "main"]


def _check_ids(rows) -> None:
    """The review journal addresses rows by id, so an empty or repeated id
    would make every reviewer act on that row ambiguous. The message names no
    id: main() reports failures by type, but the rule holds for any caller."""
    ids = [r.id for r in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("row ids")


def _looks_like_path(v) -> bool:
    """Absolute POSIX, home-relative, UNC or drive-letter (C:\\, C:/)."""
    if not isinstance(v, str):
        return False
    return v.startswith(("/", "~", "\\")) or (len(v) > 2 and v[1] == ":" and v[2] in "/\\")


def _run_config_facts(config) -> dict:
    """RunConfig as JSON, minus any value that looks like a filesystem path.
    Its fields are names, hashes and knobs (no doc id), but model_id comes
    from VLM_MODEL_ID, which MAY be a local checkpoint path, and a future
    `extra` key could be one too. campaign.json travels to the VPS and back
    into every header; a path names a machine layout, not a grading fact."""
    out = config.model_dump(mode="json")
    extra = {k: v for k, v in (out.get("extra") or {}).items() if not _looks_like_path(v)}
    out = {k: v for k, v in out.items() if k != "extra" and not _looks_like_path(v)}
    return {**out, "extra": extra}


def build_entry(dump_path: Path, pdf_path: Path, out: Path, *, campaign: str,
                group: str, run: str, doc_hash: str) -> Path:
    """Build <out>/<doc_hash>/ atomically: everything goes into a hidden temp
    dir that is renamed into place only once complete, so an interrupted or
    failing build never leaves an entry the loader would take as whole."""
    out = Path(out)
    entry = out / doc_hash
    tmp = out / f".{doc_hash}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)      # a previous crash's leftovers
    tmp.mkdir(parents=True)
    try:
        dump = reapply_current_code(load_dump(dump_path))
        res = dump.result
        scale = res.render_scale or dump.scale
        # strict: the review screen draws the rows' pixel boxes on page.png,
        # so a page that no longer renders at the dump's scale must refuse the
        # entry -- a PDF export may keep stored rows, this may not. page.png
        # is the very render the boxes were checked against.
        rows, suggestions, _ = refresh_result(
            res.characteristics, pdf_path, scale, suggestions=res.suggestions,
            strict=True, page_out=tmp / "page.png")
        _check_ids([*rows, *suggestions])
        res = res.model_copy(update={"characteristics": rows,
                                     "suggestions": suggestions})
        shutil.copyfile(pdf_path, tmp / "input.pdf")
        (tmp / "result.json").write_text(json.dumps(result_payload(res)),
                                         encoding="utf-8")
        with Image.open(tmp / "page.png") as im:
            page_px = [im.width, im.height]
        (tmp / "campaign.json").write_text(json.dumps({
            "campaign": campaign, "group": group, "source_run": run,
            "doc_hash": doc_hash, "file_name": f"{dump.doc_id}.pdf",
            # Grading facts (C2), recorded here because nothing later can
            # reconstruct them: the scale and page geometry map a reviewer's
            # pixel boxes back to PDF points, and the run config and the
            # policy the rows were re-derived under (decision 4) say what
            # the client was actually shown -- the active rules move as arms
            # ship.
            "render_scale": scale,
            "page_rect": list(dump.page_rect),
            "page_px": page_px,
            "run_config": _run_config_facts(dump.config),
            "policy": {"flag_rules": list(policy_rules.ACTIVE_FLAG_RULES),
                       "drop_stages": [list(s) for s in policy_rules.ACTIVE_DROP_STAGES]}}),
            encoding="utf-8")
        os.replace(tmp, entry)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return entry


def main(argv=None) -> int:
    # Second line behind the Bash guard: an agent session sets CLAUDECODE,
    # and this tool opens client drawings (CLAUDE.md §1). Before argparse,
    # so not even the arguments are looked at.
    if os.environ.get("CLAUDECODE"):
        print("operator tool: run it in your own terminal", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("SINDRI_CLIENT_ROOT",
                                                     str(Path.home() / "sindri-client-data")))
    ap.add_argument("--manifest", required=True, help="share_select output, with roles")
    ap.add_argument("--name", required=True, help="campaign name, e.g. pilot-1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser()
    out = Path(args.out).expanduser()
    try:
        check_out_dir(out, root)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8"))
    roles = m.get("roles") or {}
    anon = Anonymizer()
    failed = 0
    for entry in m["runs"]:
        run_dir = root / "runs" / entry["run"]
        by_hash = {anon(p.name[:-len(_PRED)]): p for p in run_dir.glob(f"*{_PRED}")}
        for h in entry["hashes"]:
            if (out / h / "campaign.json").is_file():
                print(f"have {h}")      # complete: entries are renamed in whole
                continue
            if (out / h).exists():
                # Not built by this tool (or from before entries were atomic):
                # renaming over it would fail as a bare OSError, and silently
                # replacing it could discard something the operator put there.
                print(f"SKIP {h}: incomplete entry exists -- delete it", file=sys.stderr)
                failed += 1
                continue
            dump_path = by_hash.get(h)
            pdf = root / "corpus" / "originals" / (
                dump_path.name[:-len(_PRED)] + ".pdf") if dump_path else None
            if pdf is not None and not pdf.is_file():
                pdf = None
            if dump_path is None or pdf is None or h not in roles:
                why = ("no dump" if dump_path is None else
                       "no drawing" if pdf is None else "no role")
                print(f"SKIP {h}: {why}", file=sys.stderr)
                failed += 1
                continue
            try:
                build_entry(dump_path, pdf, out, campaign=args.name, group=roles[h],
                            run=entry["run"], doc_hash=h)
            except ScaleMismatch:
                print(f"SKIP {h}: page no longer renders at the dump's scale",
                      file=sys.stderr)
                failed += 1
                continue
            except Exception as e:
                # The TYPE only, never the message: pydantic, fitz and OS
                # errors quote the values, paths and ids they choked on, and
                # this output is pasted back to an agent.
                print(f"SKIP {h}: {type(e).__name__}", file=sys.stderr)
                failed += 1
                continue
            print(f"ok   {h} ({roles[h]})")
    n = sum(1 for p in out.iterdir() if p.is_dir() and not p.name.startswith("."))
    print(f"\n{n} drawings in {out}" + (f"  ({failed} FAILED -- see above)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
