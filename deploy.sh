#!/usr/bin/env bash
set -euo pipefail

HOST="4mehpc4_3"
DIR="~/sindri"

kitty +kitten ssh -L 9090:127.0.0.1:8000 "$HOST" -t "cd $DIR && git pull && GPU=nvidia.com/gpu=1 VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct-AWQ ./run-gpu.sh --build"
