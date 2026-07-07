#!/usr/bin/env bash
# Build and run the GPU vision-LLM image with NVIDIA CDI device injection.
#
# Compose's `deploy.resources.devices` GPU reservation is Docker-Swarm syntax
# that podman-compose ignores, so the container sees no GPU. CDI device
# injection (--device nvidia.com/gpu=...) is the reliable path and works under
# both podman and recent docker.
#
# Override via env, e.g.:  ENGINE=docker GPU=nvidia.com/gpu=1 ./run-gpu.sh
#
# Rootless CDI overlay (no sudo):
#   podman injects the GPU from the system CDI spec /etc/cdi/nvidia.yaml, which
#   pins exact library versions. When the host NVIDIA userspace is updated those
#   files are replaced and the pinned paths vanish, so `podman run` dies with
#   e.g. `crun: cannot stat .../libnvidia-egl-wayland.so.1.1.19`. The real fix is
#   `sudo nvidia-ctk cdi generate`, but without sudo we bind-mount a corrected
#   copy over the system spec inside a `podman unshare` namespace. Generate the
#   corrected copy with ./fix-cdi.sh (writes ~/cdi/nvidia.yaml); this script
#   picks it up automatically when it exists.
set -euo pipefail

ENGINE="${ENGINE:-podman}"                 # podman | docker
GPU="${GPU:-nvidia.com/gpu=all}"           # e.g. nvidia.com/gpu=1 to pin one GPU
MODEL="${VLM_MODEL_ID:-Qwen/Qwen2.5-VL-7B-Instruct}"
PORT="${PORT:-8000}"
CDI_OVERLAY="${CDI_OVERLAY:-$HOME/cdi/nvidia.yaml}"   # corrected CDI spec, if any
CDI_TARGET="${CDI_TARGET:-/etc/cdi/nvidia.yaml}"      # system spec to shadow

echo ">> building sindri-gpu with $ENGINE"
"$ENGINE" build -f Dockerfile.gpu -t sindri-gpu .

echo ">> running (GPU=$GPU, model=$MODEL)"
RUN=( "$ENGINE" run --rm
  --device "$GPU"
  -p "${PORT}:8000"
  -e OCR_BACKEND=vlm
  -e VLM_MODEL_ID="$MODEL"
  -v sindri-models:/models
  sindri-gpu )

# Engage the rootless CDI overlay only for podman + an NVIDIA CDI device when a
# corrected spec is present; otherwise run straight through (docker, other GPUs,
# or a host whose system spec is already fine).
if [[ "$ENGINE" == "podman" && "$GPU" == nvidia.com/gpu=* && -f "$CDI_OVERLAY" ]]; then
  echo ">> rootless CDI overlay: $CDI_OVERLAY -> $CDI_TARGET"
  exec podman unshare -- bash -c '
    set -euo pipefail
    # podman reuses a persistent unshare mount namespace, so a previous overlay
    # can linger (pointing at a since-replaced inode) and make a fresh bind fail
    # with ENOENT. Clear any stacked binds on the target first, then re-bind.
    for _ in 1 2 3 4 5; do umount "$2" 2>/dev/null || break; done
    mount --bind "$1" "$2"
    shift 2
    exec "$@"
  ' cdi-overlay "$CDI_OVERLAY" "$CDI_TARGET" "${RUN[@]}"
else
  exec "${RUN[@]}"
fi
