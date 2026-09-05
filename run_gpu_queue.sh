#!/usr/bin/env bash
# HOST-SIDE GPU queue. Runs ON the GPU host, under tmux, with no connection to
# the operator's machine — start it, disconnect, come back in two days, scp the
# dumps.
#
#   tmux new -d -s rung3 '~/sindri/run_gpu_queue.sh 0 trainpredict'
#   tmux new -d -s gate  '~/sindri/run_gpu_queue.sh 1 awqgate base72bnf4'
#   tmux new -d -s ctl   '~/sindri/run_gpu_queue.sh 1 awqcontrol nf4control'
#
# ONE card per queue, always. The first argument is a single GPU index and it
# reaches podman as `--device nvidia.com/gpu=$GPU_INDEX`, so a queue can never
# occupy both cards -- this host has 24+ other users. Two queues at once means
# two explicit launches naming different indices, which is a deliberate act.
#
# WHY THIS EXISTS SEPARATELY FROM run_experiment_gpu.sh
# That script runs on the operator's machine and drives this host over ssh, so it
# needs that machine alive throughout — and it has twice not been: an ssh channel
# died at document 16 of 20 under load 204, and the host itself left the network
# for ~14 h. It also scores locally, which is deliberate and must not change:
# the gold values are NOT on this host and must never be copied here.
#
# So the pipeline is split at the gold boundary. Everything needing only the
# drawings runs here, unattended. Everything needing gold — score, summary,
# compare — waits for the operator. This script therefore stops at prediction
# dumps and never scores; tests/test_gpu_queue.py enforces that.
#
# Design notes, each paid for by a failure already had:
#   * every stage is resumable. `predict` skips already-predicted documents
#     (RunConfig match), and a finished stage drops a .complete marker so a
#     re-launch after an interruption skips it entirely.
#   * a failing stage STOPS the queue. Continuing would run the next stage
#     against missing inputs, and two days later that reads as a mysterious
#     empty result rather than as the failure it was.
#   * the card is checked before each stage. A 72B AWQ load into an occupied
#     card falls back to Tesseract, which then garbles or fails every document
#     while looking like it worked.
#   * logs are timestamped ON DISK. `podman run --rm` destroys the container's
#     own logs on exit, and the only timing data that survived the detectonly
#     incident came from having read them before that happened.
set -uo pipefail

GPU_INDEX="${1:-}"
shift 2>/dev/null || true
STAGES=("$@")

RROOT="${RROOT:-$HOME/sindri-eval-data}"
REPO="${REPO:-$HOME/sindri}"
MODEL="${MODEL:-Qwen/Qwen2.5-VL-72B-Instruct-AWQ}"
LOGDIR="${LOGDIR:-$HOME/rung3-logs}"
# Which image each stage runs in. The train-split pass deliberately uses the
# image that produced every existing measurement; the NF4 stages need the one
# with peft+bitsandbytes. Kept separate so a dependency change cannot contaminate
# the 26 h crop pass.
IMAGE_OLD="${IMAGE_OLD:-sindri-gpu}"
IMAGE_NEW="${IMAGE_NEW:-sindri-gpu-nf4}"

if [ -z "$GPU_INDEX" ] || [ ${#STAGES[@]} -eq 0 ]; then
    echo "usage: run_gpu_queue.sh <gpu-index> <stage> [<stage>...]" >&2
    echo "stages: trainpredict awqgate base72bnf4 awqcontrol nf4control lora72bnf4 lora72bawq" >&2
    exit 2
fi

mkdir -p "$LOGDIR"

# stage -> run name : split : image : extra env : extra predict args
# A stage is fully described here so the queue never needs a special case.
stage_run()    { case "$1" in trainpredict) echo "r3-trainpredict" ;;
                              awqgate)      echo "r3-awqgate" ;;
                              base72bnf4)   echo "r3-base72bnf4" ;;
                              awqcontrol)   echo "r3-awqcontrol" ;;
                              nf4control)   echo "r3-nf4control" ;;
                              lora72bnf4)   echo "r3-lora72bnf4" ;;
                              lora72bawq)   echo "r3-lora72bawq" ;; esac; }
stage_split()  { case "$1" in trainpredict) echo "train" ;; *) echo "dev" ;; esac; }
stage_image()  { case "$1" in trainpredict) echo "$IMAGE_OLD" ;;
                              *)            echo "$IMAGE_NEW" ;; esac; }
stage_env()    { case "$1" in
                   base72bnf4|nf4control)
                     echo "-e VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct -e SINDRI_QUANT=nf4" ;;
                   lora72bnf4)
                     echo "-e VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct -e SINDRI_QUANT=nf4 -e SINDRI_ADAPTER=read-lora-v1" ;;
                   lora72bawq)
                     echo "-e VLM_MODEL_ID=$MODEL -e SINDRI_ADAPTER=read-lora-v1" ;;
                   *) echo "-e VLM_MODEL_ID=$MODEL" ;; esac; }
stage_why()    { case "$1" in
    trainpredict) echo "60 train documents -> the boxes Rung 3's training crops come from. Never scored: train is the training split." ;;
    awqgate)      echo "adding peft+bitsandbytes to the inference image could perturb AWQ dispatch. This re-runs the AWQ path on dev so app/eval/gate.py can prove all 20 per-document deltas are 0.0. If it does not, the frozen 174.30 baseline is invalid and so is every Rung-2 conclusion." ;;
    base72bnf4)   echo "zero-shot NF4 control. The adapter will be served on this base, so this is what separates 'quantisation changed' from 'LoRA helped'." ;;
    awqcontrol)   echo "RE-RUN of the AWQ zero-shot on current code, because review.LOW_CONF moved 0.6 -> 0.8. That is a PIPELINE change, so every earlier dump carries flags computed at the old threshold and comparing an arm against them would credit the adapter with ~3.00 of threshold move. PREDICTION this run must hit: mean_review_cost 170.05, and recall/n_pred/missed/false_detection/field_acc IDENTICAL to baseline-dev. Only escaped_error->flagged_error may move, by exactly 15." ;;
    nf4control)   echo "the same re-run for the NF4 base, and the control for lora72b-nf4. PREDICTION: mean_review_cost 176.40, everything but the escaped/flagged split identical to r3-base72bnf4, which moves by exactly 17." ;;
    lora72bnf4)   echo "THE FINE-TUNE, isolated. Same NF4 base as r3-nf4control, adapter the only difference, both on current code. Judge vs r3-nf4control -- and on field_acc RISING plus the targeted bucket moving, not on review cost alone, which has been wrong three times on this corpus." ;;
    lora72bawq)   echo "THE DEPLOYMENT QUESTION: an adapter attached to what production actually serves. Judge vs r3-awqcontrol (170.05). Trained on NF4 and served on AWQ, so this arm also measures that quantisation mismatch, whose size is unknown." ;;
  esac; }

for s in "${STAGES[@]}"; do
    [ -n "$(stage_run "$s")" ] || { echo "unknown stage: $s" >&2; exit 2; }
done

# Writes to stdout AND the current stage log. Deliberately NOT `{ ... } | tee`
# around the loop body: that runs the body in a SUBSHELL, so `FAILED+=()`,
# `break` and `continue` inside it cannot affect the outer loop -- the queue
# would run every later stage after a failure and ignore completion markers.
# Caught by tests/test_gpu_queue_behaviour.py, which executes this script for
# real against stubbed podman/nvidia-smi.
CURLOG="$LOGDIR/queue-gpu$GPU_INDEX.log"
log() {
    local line
    line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "$line"
    echo "$line" >> "$CURLOG"
}

FAILED=()
for stage in "${STAGES[@]}"; do
    run="$(stage_run "$stage")"
    split="$(stage_split "$stage")"
    image="$(stage_image "$stage")"
    outdir="$RROOT/runs/$run"
    marker="$outdir/.complete"
    CURLOG="$LOGDIR/$run.log"

    log "===== stage: $stage ====="
    log "why: $(stage_why "$stage")"
    log "run=$run split=$split image=$image gpu=$GPU_INDEX"

    if [ -f "$marker" ]; then
        log "SKIPPED: $marker exists — this stage already finished"
        continue
    fi

    # A 72B load into an occupied card falls back to Tesseract and silently
    # ruins every document, so refuse rather than produce garbage.
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits \
           -i "$GPU_INDEX" 2>/dev/null | tr -d ' ')
    log "gpu $GPU_INDEX memory.used=${used:-unknown} MiB"
    if [ -z "$used" ] || [ "$used" -gt 2000 ]; then
        log "STAGE FAILED ($stage): gpu $GPU_INDEX is occupied (${used:-unknown} MiB)."
        log "  A 72B load into an occupied card falls back to Tesseract."
        FAILED+=("$stage"); break
    fi

    mkdir -p "$outdir"
    RUNCMD=(podman run --rm --device "nvidia.com/gpu=$GPU_INDEX"
      -e OCR_BACKEND=vlm $(stage_env "$stage")
      -v sindri-models:/models
      -v "$RROOT":/data:Z
      "$image"
      python -m app.eval.runner predict
        --pdfs /data/corpus/originals --out "/data/runs/$run"
        --splits /data/meta/splits.json --split "$split")

    log "launching: ${RUNCMD[*]}"
    # The shipped CDI spec on this host is stale; without binding the corrected
    # ~/cdi/nvidia.yaml over it the container gets no GPU at all and silently
    # runs on CPU. The container's own output is teed to the stage log because
    # `podman run --rm` destroys it on exit, and the per-document timings in it
    # are the only record of how long the run actually took.
    if [ -f "$HOME/cdi/nvidia.yaml" ]; then
      podman unshare -- bash -c '
        set -euo pipefail
        for _ in 1 2 3 4 5; do umount "$2" 2>/dev/null || break; done
        mount --bind "$1" "$2"; shift 2; exec "$@"
      ' cdi-overlay "$HOME/cdi/nvidia.yaml" /etc/cdi/nvidia.yaml "${RUNCMD[@]}" 2>&1 | tee -a "$CURLOG"
    else
      "${RUNCMD[@]}" 2>&1 | tee -a "$CURLOG"
    fi
    # pipefail is set, so this is podman's status when it failed. Captured in
    # the PARENT shell -- the pipe subshell is only the tee.
    rc=$?

    if [ $rc -ne 0 ]; then
        log "STAGE FAILED ($stage): predict exited $rc"
        log "  Dumps already written are kept: re-launching this queue resumes,"
        log "  because predict skips documents whose RunConfig matches."
        FAILED+=("$stage"); break
    fi

    n=$(find "$outdir" -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l)
    log "stage $stage done: $n dumps in $outdir"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$marker"
done

CURLOG="$LOGDIR/queue-gpu$GPU_INDEX.log"
{
    log "===== queue finished on gpu $GPU_INDEX ====="
    if [ ${#FAILED[@]} -gt 0 ]; then
        log "FAILED stages: ${FAILED[*]}"
        log "The queue stopped rather than running later stages against missing"
        log "inputs. Re-launch the same command once the cause is fixed:"
        log "finished stages are skipped and partial predicts resume."
    else
        log "all stages complete. Nothing here has been scored -- gold is not on"
        log "this host. Pull the dumps to the operator's machine and score there."
    fi
}

[ ${#FAILED[@]} -gt 0 ] && exit 1
exit 0
