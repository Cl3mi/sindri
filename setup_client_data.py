#!/usr/bin/env python3
"""Lay out the protected client-data root so the eval CLI can consume it.

The client delivery has three folders with German names (and a space in one).
The CLI wants three FLAT dirs, globbed non-recursively with lowercase
extensions, whose file STEMS match across all three — the stem is what pairs a
drawing with its sheet (`ingest`) and a prediction with its gold (`score`).

This script builds that view with symlinks, so the delivered files stay
pristine and nothing is duplicated. Run it yourself; it prints only counts and
stem-overlap statistics, never filenames (use --show-names if YOU need to see
mismatches — do not paste those into an AI session).

    ./setup_client_data.py ~/sindri-client-data \\
        --incoming  ~/sindri-client-data/incoming

The three roles are auto-detected by content (sheets by extension; ballooned vs
clean drawings by whether balloons are recoverable), so the delivered folder
names never have to be typed — which matters because the client-data guard
blocks them. Pass --originals/--stamped/--excel explicitly to override.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

DRAWING_EXT = {".pdf"}
SHEET_EXT = {".xlsx", ".xlsm", ".xls", ".ods"}


def collect(src: Path, exts) -> dict:
    if not src.is_dir():
        sys.exit(f"not a directory: {src}")
    found = {}
    for p in sorted(src.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            found.setdefault(p.stem, p)
    return found


def link_into(mapping: dict, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for existing in dest.iterdir():
        if existing.is_symlink():
            existing.unlink()
    for stem, src in mapping.items():
        (dest / f"{stem}{src.suffix.lower()}").symlink_to(src.resolve())


def suffix_histogram(mapping: dict) -> dict:
    return dict(Counter(p.suffix.lower() for p in mapping.values()))


def _mean_balloons(drawings: dict, sample: int) -> float:
    """Average recoverable balloons over a sample — the signal that separates
    the ballooned copies from the clean ones."""
    from app.eval.balloons import recover_balloons
    picks = list(drawings.values())[:sample]
    if not picks:
        return 0.0
    total = 0
    for path in picks:
        try:
            total += len(recover_balloons(path))
        except Exception:
            pass
    return total / len(picks)


def detect_roles(incoming: Path, sample: int = 5, drawing_order=None) -> dict:
    """Identify the three delivered folders by CONTENT, never by name.

    Folder names cannot appear in an agent command (the client-data guard
    blocks them), and spelling varies ("Orginal..."/"Original..."). Sheets are
    found by extension; of the two drawing folders, the one whose pages yield
    balloons is the ballooned set."""
    subdirs = [d for d in sorted(incoming.iterdir()) if d.is_dir()]
    sheet_dirs, draw_dirs = [], []
    for d in subdirs:
        if collect(d, SHEET_EXT):
            sheet_dirs.append(d)
        elif collect(d, DRAWING_EXT):
            draw_dirs.append(d)
    if len(sheet_dirs) != 1 or len(draw_dirs) != 2:
        raise SystemExit(
            f"expected 1 spreadsheet folder and 2 drawing folders under "
            f"{incoming}, found {len(sheet_dirs)} and {len(draw_dirs)} — "
            f"pass --originals/--stamped/--excel explicitly instead")
    if drawing_order:
        if sorted(drawing_order) != ["originals", "stamped"]:
            raise SystemExit("--drawing-order must list exactly "
                             "'originals' and 'stamped'")
        by_role = dict(zip(drawing_order, draw_dirs))
        return {"originals": by_role["originals"],
                "stamped": by_role["stamped"], "excel": sheet_dirs[0]}

    scored = [(_mean_balloons(collect(d, DRAWING_EXT), sample), d)
              for d in draw_dirs]
    if scored[0][0] == scored[1][0]:
        # Guessing here could point `predict` at the BALLOONED drawings, which
        # would silently invalidate every downstream number.
        raise SystemExit(
            f"cannot tell the drawing folders apart: both yield "
            f"{scored[0][0]:.1f} balloons/page. Re-run with "
            f"--drawing-order stamped,originals (or originals,stamped) to "
            f"assign the alphabetically-sorted folders explicitly.")
    ranked = [d for _, d in sorted(scored, key=lambda t: t[0])]
    return {"originals": ranked[0], "stamped": ranked[-1],
            "excel": sheet_dirs[0]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="protected client-data root")
    ap.add_argument("--incoming", default=None,
                    help="delivery folder; the three roles are auto-detected "
                         "by content, so folder names are never needed")
    ap.add_argument("--originals", default=None, help="clean drawings (pipeline input)")
    ap.add_argument("--stamped", default=None, help="ballooned drawings (gold positions)")
    ap.add_argument("--excel", default=None, help="inspection sheets (gold values)")
    ap.add_argument("--drawing-order", default=None,
                    help="comma-separated roles for the alphabetically-sorted "
                         "drawing folders, e.g. 'stamped,originals'; use when "
                         "balloon detection cannot discriminate")
    ap.add_argument("--sample", type=int, default=5,
                    help="drawings sampled per folder during role detection")
    ap.add_argument("--show-names", action="store_true",
                    help="print mismatching stems (for your eyes only)")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if args.incoming:
        order = args.drawing_order.split(",") if args.drawing_order else None
        roles = detect_roles(Path(args.incoming).expanduser(),
                             sample=args.sample, drawing_order=order)
    elif args.originals and args.stamped and args.excel:
        roles = {"originals": Path(args.originals).expanduser(),
                 "stamped": Path(args.stamped).expanduser(),
                 "excel": Path(args.excel).expanduser()}
    else:
        ap.error("give --incoming, or all of --originals/--stamped/--excel")
    sources = {
        "originals": (roles["originals"], DRAWING_EXT),
        "stamped": (roles["stamped"], DRAWING_EXT),
        "excel": (roles["excel"], SHEET_EXT),
    }

    collected = {}
    for name, (src, exts) in sources.items():
        collected[name] = collect(src, exts)
        link_into(collected[name], root / "corpus" / name)

    for sub in ("gold", "runs", "reports", "meta"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    stems = {k: set(v) for k, v in collected.items()}
    common = stems["originals"] & stems["stamped"] & stems["excel"]

    print(f"root: {root}")
    for name in ("originals", "stamped", "excel"):
        print(f"  {name:<10} {len(collected[name]):>4} files  "
              f"suffixes={suffix_histogram(collected[name])}")
    if args.incoming:
        # evidence for the role assignment, so a wrong guess is visible
        for name in ("stamped", "originals"):
            mean = _mean_balloons(collected[name], args.sample)
            print(f"  detect: {name:<10} {mean:.1f} balloons/page "
                  f"(sample of {args.sample})")
    print(f"  {'USABLE':<10} {len(common):>4} docs present in all three")

    problems = 0
    for name in ("originals", "stamped", "excel"):
        only = stems[name] - common
        if only:
            problems += len(only)
            print(f"  ! {len(only)} in '{name}' with no counterpart elsewhere")
            if args.show_names:
                for s in sorted(only):
                    print(f"      {s}")
    if problems and not args.show_names:
        print("  (re-run with --show-names to see which — keep them out of AI chats)")
    if not problems:
        print("  stems align across all three folders")

    non_xlsx = {s: h for s in ("excel",)
                for h in [suffix_histogram(collected[s])] if set(h) - {".xlsx"}}
    if non_xlsx:
        print("  ! sheets are not all .xlsx — `ingest`/`headers` glob *.xlsx only; "
              "tell Claude the suffix histogram so the glob can be widened")

    print(f"\nnext: echo {root} >> ~/.claude/sindri-protected-paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
