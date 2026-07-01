#!/usr/bin/env bash
set -euo pipefail

HOST="4mehpc4_3"
DIR="~/sindri"

kitty +kitten ssh -L 9090:127.0.0.1:8000 "$HOST" -t "cd $DIR && git pull && GPU=nvidia.com/gpu=1 ./run-gpu.sh --build"
