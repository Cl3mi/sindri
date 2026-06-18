#!/usr/bin/env bash
# Build and run the GPU vision-LLM image with NVIDIA CDI device injection.
#
# Compose's `deploy.resources.devices` GPU reservation is Docker-Swarm syntax
# that podman-compose ignores, so the container sees no GPU. CDI device
# injection (--device nvidia.com/gpu=...) is the reliable path and works under
# both podman and recent docker.
#
# Override via env, e.g.:  ENGINE=docker GPU=nvidia.com/gpu=1 ./run-gpu.sh
set -euo pipefail

ENGINE="${ENGINE:-podman}"                 # podman | docker
GPU="${GPU:-nvidia.com/gpu=all}"           # e.g. nvidia.com/gpu=1 to pin one GPU
MODEL="${VLM_MODEL_ID:-Qwen/Qwen2.5-VL-7B-Instruct}"
PORT="${PORT:-8000}"

echo ">> building sindri-gpu with $ENGINE"
"$ENGINE" build -f Dockerfile.gpu -t sindri-gpu .

echo ">> running (GPU=$GPU, model=$MODEL)"
exec "$ENGINE" run --rm \
  --device "$GPU" \
  -p "${PORT}:8000" \
  -e OCR_BACKEND=vlm \
  -e VLM_MODEL_ID="$MODEL" \
  -v sindri-models:/models \
  sindri-gpu
