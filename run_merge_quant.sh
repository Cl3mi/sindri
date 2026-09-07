#!/usr/bin/env bash
# Build one merged-and-requantised AWQ checkpoint on the GPU host.
#
#   run_merge_quant.sh <gpu-index> <control|arm>
#
#     control  read-lora-v1 folded in with its contribution scaled to ZERO.
#              Numerically the base, through byte-identical code. Served by the
#              PINNED inference image it must reproduce r3-awqcontrol (170.05);
#              until it does, no delta from the arm is attributable.
#     arm      read-lora-v1 folded in for real.
#
# Runs in Dockerfile.quant (autoawq 0.2.9), NOT the serving image: 0.2.8 has no
# qwen2_5_vl wrapper and cannot quantise this base at all. The serving image
# stays pinned at transformers 4.49.0 / autoawq 0.2.8 and is not touched here.
#
# Resumable. The completion marker is written only after save_quantized returns,
# so an interrupted quantisation re-runs instead of leaving a half-written
# checkpoint that looks finished — the same discipline as run_gpu_queue.sh.
#
# Touches no client data: weights and autoawq's own generic calibration corpus.
set -uo pipefail

GPU_INDEX="${1:-}"
WHICH="${2:-}"
IMAGE="${IMAGE:-sindri-quant}"
LOGDIR="${LOGDIR:-$HOME/rung3-logs}"
ADAPTER="${ADAPTER:-/models/adapters/read-lora-v1}"

case "$WHICH" in
    control) NAME="zero-scale"; EXTRA="--zero-scale" ;;
    arm)     NAME="read-lora-v1"; EXTRA="" ;;
    *) echo "usage: run_merge_quant.sh <gpu-index> <control|arm>" >&2; exit 2 ;;
esac
[ -n "$GPU_INDEX" ] || { echo "usage: run_merge_quant.sh <gpu-index> <control|arm>" >&2; exit 2; }

mkdir -p "$LOGDIR"
LOG="$LOGDIR/merge-$NAME.log"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

# A 72B bf16 load into an occupied card OOMs partway through the merge, which
# leaves a partial checkpoint and costs the whole run.
USED=$(nvidia-smi --id="$GPU_INDEX" --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null)
if [ -z "$USED" ] || [ "$USED" -gt 1024 ]; then
    log "REFUSING: gpu $GPU_INDEX reports memory.used=${USED:-unknown} MiB"
    exit 1
fi

MARKER="/models/merged/$NAME-awq/.complete"
if podman run --rm --entrypoint "" -v sindri-models:/models "$IMAGE" \
        test -f "$MARKER" 2>/dev/null; then
    log "already built: $NAME-awq — nothing to do"
    exit 0
fi

log "building $NAME on gpu $GPU_INDEX (merge -> quantise); image=$IMAGE"
podman run --rm --device "nvidia.com/gpu=$GPU_INDEX" \
    -v sindri-models:/models \
    -e HF_HOME=/models \
    "$IMAGE" \
    python merge_lora.py --adapter "$ADAPTER" \
        --out "/models/merged/$NAME" $EXTRA 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}

if [ "$rc" -ne 0 ]; then
    log "FAILED ($rc): $NAME — no marker written, so a re-run retries it"
    exit "$rc"
fi

# Marker last, and only on success: a half-written checkpoint that looks
# finished would be served as if it were the real thing.
podman run --rm --entrypoint "" -v sindri-models:/models "$IMAGE" \
    sh -c "date -u +%FT%TZ > $MARKER"
log "done: /models/merged/$NAME-awq"
