"""Tell a dead arm from a dead connection.

`run_experiment_gpu.sh` drives the GPU host over ssh. When that ssh dies the
script sees a non-zero exit and prints `ARM FAILED (predict)` — but the
container was started by the remote shell and outlives its parent, so the arm is
usually still running. Measured on 2026-08-27: detectbox's driver died at
document 17 of 20 with `Read from remote host: Connection timed out` on a host at
load average 204; the container was `Up 7 hours` and finished the run.

Misreporting that costs a run rather than a connection:

  * "FAILED" invites a relaunch, which puts a second 72B AWQ load into a card the
    orphan still holds 65 GB of. The load fails, `get_backend` falls back to
    Tesseract, and every document of the new arm is worthless.
  * with more arms queued, the driver's `continue` sends the next arm onto the
    same pinned GPU while the orphan is still on it.

So on predict failure the script asks this module what actually happened. Pure
text-in/verdict-out: the shell captures `podman ps` and pipes it here, which
keeps ssh out of the tests and keeps this file free of the client-data path
entirely.

Exit codes are the shell's three-way branch and must not collapse:
    0  no orphan — the arm really did fail, handle as before
    2  orphan alive — hold this card, do not relaunch, wait then resume
"""
import sys
from typing import Dict

# `podman ps` truncates nothing when the driver asks for
# --format '{{.ID}} {{.Status}} {{.Command}}', so the run name appears verbatim
# in the container's `--out /data/runs/<run>` argument.
_OUT_FLAG = "--out"


def _mentions_run(command: str, run_name: str) -> bool:
    """True when `command` predicts into THIS run's directory.

    Compares whole path segments. A prefix match would let `exp-detect` claim
    `/data/runs/exp-detectbox`, reporting another arm's container as this arm's
    and sending the operator away to wait for a run that never started."""
    tokens = command.split()
    for i, tok in enumerate(tokens):
        if tok != _OUT_FLAG or i + 1 >= len(tokens):
            continue
        if run_name in tokens[i + 1].strip("/").split("/"):
            return True
    return False


def classify(podman_ps: str, run_name: str) -> Dict:
    """Verdict on whether a predict container for `run_name` is still alive.

    `podman_ps` is one container per line, `<id> <status> <command...>`."""
    for line in (podman_ps or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        container_id, rest = parts
        if "predict" not in rest or not _mentions_run(rest, run_name):
            continue
        return {
            "alive": True,
            "container_id": container_id,
            "message": (
                f"CONNECTION LOST, ARM STILL RUNNING: container {container_id} "
                f"is still predicting {run_name} on the GPU host. The ssh "
                f"driver died, the arm did not. Do NOT relaunch and do NOT "
                f"start another arm on this card — a second 72B load into an "
                f"occupied GPU falls back to Tesseract and every document is "
                f"then worthless. Wait for it to finish, then re-run this arm: "
                f"resume matches the whole RunConfig, so already-predicted "
                f"documents are skipped. To abandon it instead, free the card "
                f"with: podman kill {container_id}"),
        }
    # An absent container proves only that waiting will not help. It does NOT
    # say the arm died: `podman run --rm` removes the container on a clean exit
    # too, so "died at document 19" and "finished all 20 after the driver
    # dropped" look identical from here. Measured 2026-08-27 — detectbox's
    # container was already gone when this was asked and the arm was 18 of 20
    # documents predicted and resumable, so a verdict of "it failed" would have
    # invited discarding eight hours of good dumps. State what is known, name
    # what is not, and point at the count that settles it.
    return {
        "alive": False,
        "container_id": "",
        "message": (
            f"no container is predicting {run_name} on the GPU host, so waiting "
            f"will not help. This does NOT mean the arm failed — it may have "
            f"finished after the driver dropped, since --rm removes the "
            f"container either way. Count the dumps in the run directory before "
            f"concluding anything, and re-run this arm rather than restarting "
            f"it: resume matches the whole RunConfig, so every document already "
            f"predicted is skipped."),
    }


def main(argv=None, stdin_text=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: podman ps ... | python -m app.eval.orphan <run-name>",
              file=sys.stderr)
        return 1
    text = sys.stdin.read() if stdin_text is None else stdin_text
    verdict = classify(text, argv[0])
    print(verdict["message"], file=sys.stderr if verdict["alive"] else sys.stdout)
    return 2 if verdict["alive"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
