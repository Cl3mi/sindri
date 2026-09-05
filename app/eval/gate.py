"""The reproduction gate: a control arm that changed nothing must reproduce the
baseline exactly, and a MISSING comparison point must fail loudly.

Lives here rather than inline in run_experiment_gpu.sh because the shell version
was nested inside `if [ -f "$CONTROL_REPORT" ]`: with no baseline report on disk
the control arm ran, printed no gate line, and the whole run proceeded as if it
had been gated (findings §8.1). A gate an absent file can skip is not a gate.

Reads only a `runner compare` output. _cmd_compare has already replaced the
per-document keys with salted hashes, so the failure message can name the
drifted documents without naming a client part number."""
import json
import sys
from pathlib import Path
from typing import Tuple


def check_reproduction(cmp_path) -> Tuple[bool, str]:
    """Return (ok, message) for a compare JSON that must show no drift at all.

    Every non-pass is a failure, including the boring ones: an absent file, an
    unreadable file, and an empty delta map all mean "this run was not
    verified", and reporting any of them as a pass is the exact defect this
    module exists to remove."""
    path = Path(cmp_path)
    if not path.exists():
        return False, (
            f"REPRODUCTION GATE FAILED: no comparison point at {path.name} — "
            f"the control arm has nothing to reproduce against. Score the "
            f"baseline first; do NOT interpret any treatment arm.")
    try:
        cmp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, (f"REPRODUCTION GATE FAILED: {path.name} is unreadable "
                       f"({type(e).__name__})")
    deltas = cmp.get("per_doc_deltas") or {}
    if not deltas:
        return False, (f"REPRODUCTION GATE FAILED: {path.name} has no "
                       f"per_doc_deltas — nothing was compared")
    bad = {d: v for d, v in deltas.items() if v != 0.0}
    if bad:
        return False, (
            f"REPRODUCTION GATE FAILED: {len(bad)} of {len(deltas)} document(s) "
            f"drifted: {bad}. The predict path changed since the committed "
            f"baseline; find out why before interpreting any treatment arm.")
    return True, (f"reproduction gate OK: all {len(deltas)} per-document deltas "
                  f"are exactly 0.0")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: python -m app.eval.gate <compare.json>", file=sys.stderr)
        return 2
    ok, message = check_reproduction(argv[0])
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
