"""Telling a dead arm from a dead connection.

detectbox's driver died with `Read from remote host: Connection timed out` at
document 17 of 20, on a host at load average 204. The container was untouched
and finished the run; the script printed `ARM FAILED (predict)`. Two ways that
misreport hurts, and both are what these tests pin:

  * the operator's natural response to "FAILED" is to relaunch, which puts a
    second 72B AWQ load into a card the orphan still holds 65 GB of. It fails,
    falls back to Tesseract, and every document of the new arm is worthless.
  * with more arms queued, `continue` sends the next one onto the same pinned
    GPU while the orphan is still on it.

The classifier is pure text-in/verdict-out so it can be tested without a GPU
host: `podman ps` output is captured by the shell and handed over on stdin."""
from app.eval.orphan import classify, main

# One line as `podman ps --format '{{.ID}} {{.Status}} {{.Command}}'` emits it.
_ALIVE = ("2852f811de84b51ceadda01dfa54f7a6a1fec581c94fb7884d80384cb8896171 "
          "Up 7 hours python -m app.eval.runner predict --pdfs "
          "/data/corpus/originals --out /data/runs/exp-detectbox --splits "
          "/data/meta/splits.json --split dev")


def test_a_live_container_for_this_run_is_reported_as_still_running():
    v = classify(_ALIVE, "exp-detectbox")
    assert v["alive"] is True
    assert v["container_id"].startswith("2852f811")
    # the operative instruction, however it is capitalised
    assert "not relaunch" in v["message"].lower()


def test_no_container_does_not_claim_the_arm_failed():
    """An absent container proves only that waiting will not help. It does NOT
    distinguish "the arm died" from "the arm finished after the driver died" --
    `podman run --rm` removes the container either way, so both look identical
    here.

    Measured 2026-08-27, and it matters: detectbox's container was gone by the
    time this was asked, and the arm was 18 of 20 documents predicted and
    resumable. A verdict of "genuinely failed" invites discarding eight hours of
    good dumps."""
    v = classify("", "exp-detectbox")
    assert v["alive"] is False
    assert "no container" in v["message"]
    assert "genuinely failed" not in v["message"]
    # it must name both possibilities and point at the check that settles it
    assert "finished" in v["message"]
    assert "resume" in v["message"].lower()


def test_a_container_for_a_DIFFERENT_arm_does_not_count_as_ours():
    """Matching on the run name, not on "a predict is running": another arm's
    container on the other card must not make this arm look recoverable."""
    v = classify(_ALIVE, "exp-readcenter")
    assert v["alive"] is False


def test_the_run_name_must_match_a_whole_path_segment():
    """`exp-detect` must not match `/data/runs/exp-detectbox`. A prefix match
    would report a different arm's container as this arm's and send the operator
    off to wait for a run that was never started."""
    v = classify(_ALIVE, "exp-detect")
    assert v["alive"] is False


def test_the_message_names_the_container_so_a_card_can_actually_be_freed():
    """findings §8.1: to free the GPU you must `podman kill` THAT id. A verdict
    that says "something is running" without the id leaves the operator
    guessing, which is how a card stays allocated."""
    v = classify(_ALIVE, "exp-detectbox")
    assert v["container_id"] in v["message"]
    assert "podman kill" in v["message"]


def test_exit_code_2_signals_orphan_alive_distinctly_from_a_real_failure():
    """The shell has to branch three ways, so the codes cannot collapse: 0 = no
    orphan (a real arm failure, proceed as before), 2 = orphan alive (hold, do
    not relaunch, do not start another arm on this card)."""
    assert main(["exp-detectbox"], stdin_text=_ALIVE) == 2
    assert main(["exp-detectbox"], stdin_text="") == 0
