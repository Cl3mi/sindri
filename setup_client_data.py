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
        --originals "~/sindri-client-data/incoming/Orginalzeichnungen" \\
        --stamped   "~/sindri-client-data/incoming/Gestempelte Zeichnungen" \\
        --excel     "~/sindri-client-data/incoming/Berichte"
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="protected client-data root")
    ap.add_argument("--originals", required=True, help="clean drawings (pipeline input)")
    ap.add_argument("--stamped", required=True, help="ballooned drawings (gold positions)")
    ap.add_argument("--excel", required=True, help="inspection sheets (gold values)")
    ap.add_argument("--show-names", action="store_true",
                    help="print mismatching stems (for your eyes only)")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    sources = {
        "originals": (Path(args.originals).expanduser(), DRAWING_EXT),
        "stamped": (Path(args.stamped).expanduser(), DRAWING_EXT),
        "excel": (Path(args.excel).expanduser(), SHEET_EXT),
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
