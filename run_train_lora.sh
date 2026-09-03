#!/usr/bin/env bash
# HOST-SIDE LoRA training launcher. Runs ON the GPU host, under tmux, with no
# connection to the operator's machine.
#
#   tmux new -d -s train '~/sindri/run_train_lora.sh 1'
#
#   # or chained behind a still-running control, which is the usual case:
#   tmux new -d -s train \
#     'WAIT_FOR=~/sindri-eval-data/runs/r3-nf4control/.complete ~/sindri/run_train_lora.sh 1'
#
# WHY THIS IS SEPARATE FROM run_gpu_queue.sh
# That script's stages all run `runner predict` in the inference image; training
# runs a different entry point in a different image, so folding it in would mean
# special-casing the one thing the queue was built not to need.
#
# It is also separate for a blunter reason: run_gpu_queue.sh is usually STILL
# EXECUTING when training needs to be set up. Bash reads a script incrementally,
# so replacing that file on disk mid-run -- which `git checkout` does -- can
# corrupt the running queue. A new file cannot.
#
# Everything below is inherited from the queue's hard-won details rather than
# reinvented, because each was paid for by a failure already had:
#   * the CDI overlay. The shipped spec on this host is stale; without binding
#     the corrected ~/cdi/nvidia.yaml over it the container gets NO GPU and
#     trains on CPU, which looks like a slow but healthy run for days.
#   * the card is checked before starting. This host has 24+ users, and a 70 GB
#     job landing on an occupied card either OOMs hours in or degrades quietly.
#   * logs are timestamped ON DISK. `podman run --rm` destroys the container's
#     own logs on exit, and the loss curve is the only evidence of whether the
#     adapter learned anything.
#   * a completion marker makes it resumable, so relaunching after a dropped ssh
#     session does not spend another ten hours redoing finished work.
#   * it NEVER scores. Gold is not on this host and must never be.
set -uo pipefail

GPU_INDEX="${1:-}"
[ -n "$GPU_INDEX" ] || { echo "usage: run_train_lora.sh <gpu-index>" >&2; exit 2; }

RROOT="${RROOT:-$HOME/sindri-eval-data}"
LOGDIR="${LOGDIR:-$HOME/rung3-logs}"
IMAGE="${IMAGE:-sindri-train}"
ADAPTER="${ADAPTER:-read-lora-v1}"
RANK="${RANK:-8}"
EPOCHS="${EPOCHS:-3}"
HOLDOUT_FRAC="${HOLDOUT_FRAC:-0.1}"
SEED="${SEED:-13}"
# Wait for another stage's completion marker before taking the card. The MARKER,
# not free memory: it is written only on success, so a failed control never
# silently hands its card to a training run whose inputs may not exist.
WAIT_FOR="${WAIT_FOR:-}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-72000}"    # 20 h, comfortably past a 20-doc run
WAIT_POLL="${WAIT_POLL:-300}"

mkdir -p "$LOGDIR"
CURLOG="$LOGDIR/train-$ADAPTER.log"
log() {
    local line
    line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "$line"
    echo "$line" >> "$CURLOG"
}

MANIFEST="$RROOT/train/pairs/manifest.jsonl"
MARKER="$RROOT/train/$ADAPTER.complete"

log "===== train: $ADAPTER ====="
log "gpu=$GPU_INDEX image=$IMAGE rank=$RANK epochs=$EPOCHS holdout=$HOLDOUT_FRAC seed=$SEED"

if [ -f "$MARKER" ]; then
    log "SKIPPED: $MARKER exists — this adapter already trained"
    exit 0
fi

if [ ! -f "$MANIFEST" ]; then
    log "FAILED: no manifest at $MANIFEST."
    log "  Build pairs on the operator's machine and rsync them here first;"
    log "  gold is not on this host, so they cannot be built locally."
    exit 1
fi

if [ -n "$WAIT_FOR" ]; then
    waited=0
    while [ ! -f "$WAIT_FOR" ]; do
        if [ "$waited" -ge "$WAIT_TIMEOUT" ]; then
            log "FAILED: waited ${waited}s for $WAIT_FOR and it never appeared."
            log "  That marker is written only when the stage SUCCEEDS, so the"
            log "  run it was waiting on either failed or is still going. Not"
            log "  starting: the card may still be held."
            exit 1
        fi
        [ "$waited" -eq 0 ] && log "waiting for $WAIT_FOR (poll ${WAIT_POLL}s, timeout ${WAIT_TIMEOUT}s)"
        sleep "$WAIT_POLL"
        waited=$((waited + WAIT_POLL))
    done
    log "marker present after ${waited}s: $WAIT_FOR"
fi

used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits \
       -i "$GPU_INDEX" 2>/dev/null | tr -d ' ')
log "gpu $GPU_INDEX memory.used=${used:-unknown} MiB"
if [ -z "$used" ] || [ "$used" -gt 2000 ]; then
    log "FAILED: gpu $GPU_INDEX is occupied (${used:-unknown} MiB)."
    log "  A 4-bit 72B plus LoRA state needs ~45-55 GB; sharing a card with"
    log "  another job OOMs hours in or degrades quietly."
    exit 1
fi

RUNCMD=(podman run --rm --device "nvidia.com/gpu=$GPU_INDEX"
  -v sindri-models:/models
  -v "$RROOT":/data:Z
  "$IMAGE"
  python train_lora.py
    --manifest /data/train/pairs/manifest.jsonl
    --out "/models/adapters/$ADAPTER"
    --rank "$RANK" --epochs "$EPOCHS"
    --holdout-frac "$HOLDOUT_FRAC" --seed "$SEED")

log "launching: ${RUNCMD[*]}"
if [ -f "$HOME/cdi/nvidia.yaml" ]; then
  podman unshare -- bash -c '
    set -euo pipefail
    for _ in 1 2 3 4 5; do umount "$2" 2>/dev/null || break; done
    mount --bind "$1" "$2"; shift 2; exec "$@"
  ' cdi-overlay "$HOME/cdi/nvidia.yaml" /etc/cdi/nvidia.yaml "${RUNCMD[@]}" 2>&1 | tee -a "$CURLOG"
else
  "${RUNCMD[@]}" 2>&1 | tee -a "$CURLOG"
fi
# pipefail is set, so this is podman's status when it failed. Captured in the
# PARENT shell -- the pipe subshell is only the tee.
rc=$?

if [ $rc -ne 0 ]; then
    log "FAILED: training exited $rc. No marker written, so a relaunch retries."
    exit 1
fi

mkdir -p "$(dirname "$MARKER")"
date -u +%Y-%m-%dT%H:%M:%SZ > "$MARKER"
log "adapter written to /models/adapters/$ADAPTER (volume sindri-models)"
log "Nothing here has been scored — gold is not on this host. Serve the adapter"
log "with SINDRI_ADAPTER=$ADAPTER and score the resulting run on the operator's"
log "machine."
exit 0
