"""Read a multi-arm experiment's values-blind digests and say which direction to
follow.

Reads ONLY `docs/eval/*-summary.json` — the sanctioned, values-blind output of
`runner summary`. It never touches the protected root, so it needs no guard
exemption and its output is safe to paste anywhere.

The verdict rule exists because a cost-only rule is wrong. Maximum-cardinality
matching was measured on this corpus and lowered mean review cost while
recovering 26 misses by destroying 27 correct pairings; field accuracy on matched
rows fell 36.4% -> 25.4%. Cost alone called that an improvement. So an arm only
wins if it lowers cost AND does not degrade the accuracy of the rows it matched
AND does not raise the silent-error rate.
"""
import json
import sys
from pathlib import Path
from typing import Dict

from app.eval.report import auto_accept_precision

# Tolerances, not zero: scoring is deterministic here (verified — 16 unchanged
# documents gave per-document delta exactly 0.0 across a GPU device change), so
# these bound "materially worse", not measurement noise.
FIELD_ACC_TOLERANCE = 0.02
ESCAPED_RATE_TOLERANCE = 0.02
# Deliberately the same 0.005 report.compare_runs uses for its recall-drop
# warning. The two tools disagreed about detectbox: compare_runs warned "review
# cost improved but recall dropped 0.646 -> 0.631 -- likely a net review-time
# LOSS on missed callouts", while this module tolerated it silently and printed
# WIN. One number, one threshold.
RECALL_TOLERANCE = 0.005
# Guards the unflagged set on two axes: precision (report.auto_accept_precision)
# and rate (correct / n_gold). Cost falls by 4 whenever an ESCAPED error (w=5)
# moves to flagged_error (w=1) -- that raises precision, a real win. Cost falls
# by only 1 less when a CORRECT row moves to flagged_correct instead (w=1 for
# both, but the reviewer now checks a row that needed no check) -- that LOWERS
# precision, and is what precision alone can catch. But flagging at RANDOM,
# correct and escaped moved out in the same proportion, leaves precision flat
# while cost still falls and fewer rows are automated -- rate is what falls
# then, and precision cannot see it. Same 0.02 as the other ratio guards.
AUTO_ACCEPT_TOLERANCE = 0.02


def arm_row(name: str, digest: Dict) -> Dict:
    """Flatten one digest into the numbers a direction decision needs."""
    t = digest["taxonomy"]
    md = digest.get("missed_diagnosis", {})
    matched = digest["n_gold"] - t.get("missed", 0)
    correct = t.get("correct", 0)
    right = correct + t.get("flagged_correct", 0)
    return {
        "arm": name,
        # The run name this digest records. It is the identity a comparison
        # file's run_a/run_b refer to, so it is how an arm finds the control it
        # was actually measured against.
        "run": digest.get("run", ""),
        # Which base model produced this row. Rung 3 compares an AWQ baseline
        # against an NF4 run of the same weights, and a delta that does not say
        # which side is which is not a result. "unrecorded" rather than a
        # plausible default: a digest that never captured model_id must not read
        # as the current one, for the same reason frame_origin_frac is None.
        "model": digest.get("config", {}).get("model_id") or "unrecorded",
        "cost": digest["mean_review_cost"],
        "recall": round(digest["micro_recall"], 4),
        "precision": round(digest.get("micro_precision", 0.0), 4),
        "escaped_rate": round(digest["escaped_rate"], 4),
        "missed": t.get("missed", 0),
        "false_detection": t.get("false_detection", 0),
        "matched": matched,
        # The guard metric: of the rows this arm claims to have found, how many
        # did it actually get right?
        "field_acc": round(right / matched, 4) if matched else 0.0,
        # Precision of the unflagged rows: of what the arm let through
        # untouched, how much was actually correct? None (never 0.0) when
        # nothing is unflagged -- see report.auto_accept_precision, the single
        # source of this formula.
        "auto_accept_precision": auto_accept_precision(t),
        # Rate: correct rows as a fraction of ALL gold, not just the unflagged
        # ones. Flagging at random moves correct and escaped rows out of the
        # unflagged set in the same proportion, so precision barely moves --
        # rate is what falls, and it is the guard that catches that case.
        "auto_accept_rate": round(correct / digest["n_gold"], 4)
                             if digest["n_gold"] else None,
        "misplaced": digest.get("misplaced_matches", 0),
        "contended": md.get("contended", 0),
        "isolated": md.get("isolated", 0),
        "unlocated": md.get("unlocated", 0),
        "knobs": digest.get("config", {}).get("extra", {}),
    }


def verdict(row: Dict, control: Dict, comparison: Dict = None) -> Dict:
    """Did this arm actually improve things, and which bucket did it move?

    `comparison` is the arm's `runner compare` output against control. Without
    it, robustness is unmeasured — and an unmeasured condition is not a passing
    one, so no arm wins on the default weighting alone."""
    d_cost = round(row["cost"] - control["cost"], 2)
    d_acc = round(row["field_acc"] - control["field_acc"], 4)
    d_esc = round(row["escaped_rate"] - control["escaped_rate"], 4)
    d_rec = round(row["recall"] - control["recall"], 4)
    reasons = []
    # None handling: an unmeasured or undefined precision is never treated as
    # a pass. Control None means the baseline itself never left anything
    # unflagged, so no comparison is possible; arm None (control measured)
    # means the arm automated nothing at all -- both are named explicitly
    # rather than silently skipped, and the delta stays None either way so it
    # is never printed as a number that was not computed.
    row_aap, ctl_aap = row["auto_accept_precision"], control["auto_accept_precision"]
    if ctl_aap is None:
        d_aap = None
        reasons.append("auto-accept precision unmeasured on control")
    elif row_aap is None:
        d_aap = None
        reasons.append("auto-accepts nothing — every matched row is flagged")
    else:
        d_aap = round(row_aap - ctl_aap, 4)
    row_rate, ctl_rate = row["auto_accept_rate"], control["auto_accept_rate"]
    d_rate = (round(row_rate - ctl_rate, 4)
              if row_rate is not None and ctl_rate is not None else None)
    if d_cost >= 0:
        reasons.append(f"review cost did not improve ({d_cost:+.2f})")
    if d_acc < -FIELD_ACC_TOLERANCE:
        reasons.append(f"field accuracy on matched rows fell {d_acc:+.4f} — "
                       f"recall bought by breaking correct pairs")
    if d_esc > ESCAPED_RATE_TOLERANCE:
        reasons.append(f"escaped-error rate rose {d_esc:+.4f} — more silent "
                       f"wrong values reaching the customer")
    # Recall, because field accuracy is a RATIO over matched rows and rises when
    # its denominator shrinks. detectbox turned 7 wrong rows into misses at
    # w=10 instead of into correct ones: field_acc +0.0284 with `correct`
    # unchanged at 72, which reads as a quality gain and is not one.
    if d_rec < -RECALL_TOLERANCE:
        reasons.append(f"recall fell {d_rec:+.4f} — field accuracy can rise "
                       f"merely by losing matched rows to the miss bucket, "
                       f"which costs w=10 each")
    if d_aap is not None and d_aap < -AUTO_ACCEPT_TOLERANCE:
        reasons.append(f"auto-accept precision fell {d_aap:+.4f} — cost "
                       f"bought by flagging more, which leaves the unflagged "
                       f"rows less trustworthy")
    # Precision alone misses random flagging: correct and escaped rows leave
    # the unflagged set in the same proportion, so precision holds steady
    # while fewer rows are automated at all. The rate is what falls then.
    if d_rate is not None and d_rate < -AUTO_ACCEPT_TOLERANCE:
        reasons.append(f"auto-accept rate fell {d_rate:+.4f} — fewer rows "
                       f"automated; precision alone cannot see flags spread "
                       f"evenly over right and wrong rows")
    # Robustness last, so the taxonomy reasons above are always reported too.
    if comparison is None:
        reasons.append("robustness unmeasured — no vs-control comparison found, "
                       "so there is no evidence this beats control under any "
                       "weighting but the default")
    else:
        ws = comparison.get("weight_sensitivity") or {}
        fraction = ws.get("b_better_fraction")
        n = ws.get("n_weight_vectors", 0)
        if not ws.get("robust") or fraction != 1.0:
            better = int(round((fraction or 0.0) * n))
            ci = comparison.get("ci95") or [0.0, 0.0]
            reason = (f"not robust — better under only {better} of {n} "
                      f"weightings, ci95 {ci}, significant "
                      f"{comparison.get('significant')}")
            # Only say "spans zero" when it does. nomerge's ci95 is
            # [1.15, 9.70] — significantly WORSE, not a no-op — and appending
            # that clause unconditionally stated something untrue about it. An
            # inaccurate diagnostic is what sent this campaign after the wrong
            # lever to begin with.
            if len(ci) == 2 and ci[0] <= 0.0 <= ci[1]:
                reason += (". A mean delta on an interval spanning zero is a "
                           "no-op, not a small gain")
            reasons.append(reason)
    return {
        "arm": row["arm"],
        "win": not reasons,
        "why": "; ".join(reasons) if reasons else "cost down, accuracy held",
        "cost_delta": d_cost,
        "recall_delta": d_rec,
        "field_acc_delta": d_acc,
        "escaped_delta": d_esc,
        "auto_accept_delta": d_aap,
        "auto_accept_rate_delta": d_rate,
        "contended_delta": row["contended"] - control["contended"],
        "isolated_delta": row["isolated"] - control["isolated"],
        "missed_delta": row["missed"] - control["missed"],
        "false_delta": row["false_detection"] - control["false_detection"],
    }


def _load(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    docs = Path(argv[0]) if argv else Path(__file__).parents[2] / "docs" / "eval"
    control_path = docs / "exp-control-summary.json"
    if not control_path.exists():
        control_path = docs / "baseline-summary.json"
    if not control_path.exists():
        print(f"no control digest in {docs} (expected exp-control-summary.json "
              f"or baseline-summary.json)", file=sys.stderr)
        return 1
    control = arm_row("control", _load(control_path))

    # Every digest in the directory, by the run name it records. A comparison
    # names its control in run_a, so the arm->control pairing is recorded data
    # and never inferred: guessing it from config.extra is what produced
    # lora72bnf4 at +13.35 against an AWQ control when the matched answer
    # against r3-nf4control is +10.00.
    digests = {}
    for path in sorted(docs.glob("*-summary.json")):
        d = _load(path)
        if d.get("run"):
            digests[d["run"]] = (path, d)

    # run_b -> (control run name, comparison). A vs-file is the only place the
    # matched control is written down.
    matched = {}
    for path in sorted(docs.glob("*-vs-*.json")):
        cmp_ = _load(path)
        if cmp_.get("run_a") and cmp_.get("run_b"):
            matched[cmp_["run_b"]] = (cmp_["run_a"], cmp_)

    arms = [control]
    seen = {control_path}
    for path in sorted(docs.glob("exp-*-summary.json")):
        name = path.name[len("exp-"):-len("-summary.json")]
        if name == "control":
            continue
        seen.add(path)
        arms.append(arm_row(name, _load(path)))
    # An arm need not carry the exp- prefix to be an arm. Anything a comparison
    # measured against a control is one, which is how r3-loraread -- written as
    # loraread-summary.json -- reaches this table at all.
    # A run something else is judged AGAINST is a control, whatever else was
    # once measured about it. r3-nf4control is the control for both LoRA arms
    # and was itself re-scored against r3-base72bnf4 when the review threshold
    # moved; without this it appears as a winning arm and reads as a treatment
    # win that does not exist.
    controls = {ctl for ctl, _ in matched.values()}
    for run_b in sorted(matched):
        if run_b in controls:
            continue
        if run_b in digests and digests[run_b][0] not in seen:
            path, d = digests[run_b]
            seen.add(path)
            arms.append(arm_row(path.name[:-len("-summary.json")], d))

    w = 12
    cols = ("arm", "model", "cost", "recall", "field_acc", "missed", "contended",
            "isolated", "false_detection", "misplaced")
    # Model ids are long ("Qwen/Qwen2.5-VL-72B-Instruct-AWQ"); this table is read
    # by a human deciding whether two rows are comparable at all, so the id has
    # to be legible rather than truncated.
    widths = {c: (34 if c == "model" else w) for c in cols}
    print(f"control digest: {control_path.name}")
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in arms:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))

    if len(arms) == 1:
        print("\nno treatment arms found — nothing to decide yet")
        return 0

    print("\nverdicts (an arm wins only if cost falls AND matched-row accuracy "
          "holds AND silent errors do not rise AND recall holds AND the gain is "
          "robust across every weighting):")
    wins = []
    for r in arms[1:]:
        # The arm's paired comparison, written next to its digest. Absent it,
        # robustness is unmeasured and the arm cannot win on the default
        # weighting alone.
        cmp_path = docs / f"exp-{r['arm']}-vs-control.json"
        comparison = _load(cmp_path) if cmp_path.exists() else None
        arm_control, note = control, ""
        if r["run"] in matched:
            ctl_run, cmp_ = matched[r["run"]]
            comparison = comparison or cmp_
            if ctl_run in digests:
                arm_control = arm_row("control", digests[ctl_run][1])
                note = f"  [vs {ctl_run}]"
            else:
                # Refusing to fall back silently: the global control is a
                # DIFFERENT base, and crediting that difference to the arm is
                # the precise error this selection exists to remove.
                note = (f"  [no matched control: {ctl_run} names no digest here "
                        f"— NOT judged]")
                print(f"  ??  {r['arm']:<14}{note}")
                continue
        v = verdict(r, arm_control, comparison=comparison)
        flag = "WIN " if v["win"] else "no  "
        print(f"  {flag}{v['arm']:<14} cost {v['cost_delta']:+8.2f}  "
              f"recall {v['recall_delta']:+.4f}  field_acc {v['field_acc_delta']:+.4f}  "
              f"contended {v['contended_delta']:+d}  isolated {v['isolated_delta']:+d}"
              f"{note}")
        print(f"      {v['why']}")
        print(f"      knobs: {r['knobs'] or '<defaults>'}")
        if v["win"]:
            wins.append(v)

    print("\ndirection:")
    if not wins:
        print("  none of these arms is an improvement. Do NOT pick the "
              "cheapest-looking one — read the 'why' lines: an arm that lowers "
              "cost while dropping field accuracy is inflating recall, not "
              "fixing anything.")
    else:
        best = min(wins, key=lambda v: v["cost_delta"])
        moved = ("contended (merge_adjacent)" if best["contended_delta"]
                 <= best["isolated_delta"] else "isolated (tile coverage)")
        print(f"  best arm: {best['arm']} ({best['cost_delta']:+.2f} review cost)")
        print(f"  bucket moved most: {moved}")
        print(f"  next: confirm it on the full corpus, then tune that knob only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
