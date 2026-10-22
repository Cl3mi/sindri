"""Pick drawings to show the client, from `runner summary --per-doc` digests.

Reads counts and salted ids only -- the same data an agent may see -- so it is
safe to run in an agent session. Writes a manifest of HASHES that
`export_share.py --manifest` (operator terminal) turns into the files.

    python -m app.eval.share_select --digest r4-control=dev.json \
        --digest r4-controltest=test.json --great 3 --problem 2 --out manifest.json

`--digest RUN=FILE`: RUN is the predict run directory under <root>/runs, FILE is
a `summary --per-doc` output for that run. Dev/test digests only: a train
drawing was seen in adapter training and would flatter the result.
`--allow-train` lifts that for the review campaign only (plan C1 decision 5),
and then accepts train and dev digests ONLY: the campaign keeps test held out.

Criteria (defaults; every one is a flag):
  eligible  n_gold >= --min-gold (a demo needs a drawing with substance)
  great     recall >= --great-recall and escaped_error/n_gold <= --max-escaped;
            best recall first
  problem   same escaped ceiling (the client ranks precision over recall: never
            show a wrong value delivered unflagged) but recall in
            [--problem-recall-min, --great-recall) -- visibly imperfect, not
            broken. Two flavours so the pair is informative: the most
            over-detecting drawing (false_detection / n_pred), then the one with
            the most misses (missed / n_gold).
`recall` here is (correct + flagged_correct) / n_gold, as in per_doc_rows.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List


def _ratio(num, den):
    return num / den if den else 0.0


def _esc(r):
    return _ratio(r["counts"].get("escaped_error", 0), r["n_gold"])


def select(rows: List[Dict], great: int = 3, problem: int = 2, min_gold: int = 15,
           great_recall: float = 0.55, problem_recall_min: float = 0.3,
           max_escaped: float = 0.1) -> List[Dict]:
    """rows: per_doc rows, each tagged with a "run". Returns picks with a
    "role" ("great"|"problem") and a "why"; may return fewer than asked."""
    ok = [r for r in rows if r["n_gold"] >= min_gold and _esc(r) <= max_escaped
          and r["recall"] is not None]
    picks, used = [], set()

    def take(r, role, why):
        picks.append({**r, "role": role, "why": why})
        used.add((r["run"], r["doc"]))

    pool = sorted((r for r in ok if r["recall"] >= great_recall),
                  key=lambda r: (-r["recall"], _esc(r), r["doc"]))
    for r in pool[:great]:
        take(r, "great", f"recall {r['recall']:.2f}, "
                         f"{r['counts'].get('escaped_error', 0)} escaped")

    mid = [r for r in ok if problem_recall_min <= r["recall"] < great_recall
           and (r["run"], r["doc"]) not in used]
    flavours = [
        ("over-detection", lambda r: _ratio(r["counts"].get("false_detection", 0),
                                            r["n_pred"])),
        ("misses", lambda r: _ratio(r["counts"].get("missed", 0), r["n_gold"])),
    ]
    for i in range(problem):
        name, key = flavours[i % len(flavours)]
        left = [r for r in mid if (r["run"], r["doc"]) not in used]
        if not left:
            break
        best = max(left, key=lambda r: (key(r), r["doc"]))
        take(best, "problem", f"{name} {key(best):.2f}, recall {best['recall']:.2f}")
    return picks


def manifest(picks: List[Dict]) -> Dict:
    """Group picks by run: {"runs": [{"run": ..., "hashes": [...]}],
    "roles": {hash: "great"|"problem"}}. `export_share.py` reads only "runs";
    "roles" lets the review campaign record each drawing's group so its report
    can reweight the great/problem mix."""
    by_run: Dict[str, List[str]] = {}
    for p in picks:
        by_run.setdefault(p["run"], []).append(p["doc"])
    return {"runs": [{"run": r, "hashes": h} for r, h in by_run.items()],
            "roles": {p["doc"]: p["role"] for p in picks}}


def load_rows(specs: List[str], allow_train: bool = False) -> List[Dict]:
    rows = []
    for spec in specs:
        run, _, path = spec.partition("=")
        if not path:
            raise SystemExit(f"--digest wants RUN=FILE, got {spec!r}")
        digest = json.loads(Path(path).read_text(encoding="utf-8"))
        if "per_doc" not in digest:
            raise SystemExit(f"{path}: no per_doc -- re-run summary with --per-doc")
        # A share-pack shows the client our quality, which a train drawing
        # flatters. The review campaign (docs/plans/2026-10-21-campaign-c1-
        # review-plan.md, decision 5) needs ~50 drawings, more than dev's 15
        # scoped ones, and asks the client to grade rather than admire -- so it
        # may opt in to train. Test stays held out either way.
        if digest.get("split") == "train" and not allow_train:
            raise SystemExit(f"{path}: train split -- the adapter saw these drawings")
        # Under --allow-train (the campaign) test must stay unseen by the
        # client's grades: it is where every later arm is confirmed. "all"
        # holds test drawings, and a digest that names no split cannot prove
        # it holds none, so only train and dev pass.
        if allow_train and digest.get("split") not in ("train", "dev"):
            raise SystemExit(f"{path}: split {digest.get('split')!r} -- the review "
                             f"campaign draws from train and dev only; test stays held out")
        rows += [{**r, "run": run} for r in digest["per_doc"]]
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--digest", action="append", required=True, metavar="RUN=FILE")
    ap.add_argument("--great", type=int, default=3)
    ap.add_argument("--problem", type=int, default=2)
    ap.add_argument("--min-gold", type=int, default=15)
    ap.add_argument("--great-recall", type=float, default=0.55)
    ap.add_argument("--problem-recall-min", type=float, default=0.3)
    ap.add_argument("--max-escaped", type=float, default=0.1)
    ap.add_argument("--out", required=True, help="manifest json (hashes only)")
    ap.add_argument("--allow-train", action="store_true",
                    help="accept train digests -- review campaign only (plan C1 "
                         "decision 5); never for a client share-pack")
    args = ap.parse_args(argv)

    picks = select(load_rows(args.digest, args.allow_train), args.great, args.problem, args.min_gold,
                   args.great_recall, args.problem_recall_min, args.max_escaped)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(manifest(picks), indent=1), encoding="utf-8")
    for p in picks:
        print(f"{p['role']:8} {p['run']}/{p['doc']}  n_gold={p['n_gold']:3d}  {p['why']}")
    short = (args.great - sum(p["role"] == "great" for p in picks),
             args.problem - sum(p["role"] == "problem" for p in picks))
    if any(short):
        print(f"WARNING: short by {short[0]} great, {short[1]} problem -- "
              f"criteria too strict for this pool; loosen a flag", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
