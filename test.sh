#!/usr/bin/env bash
#
# test.sh — non-interactive remote build + diagnostic run, usable by Claude Code.
#
# deploy.sh opens an interactive `kitty +kitten ssh` session and runs the web
# server in the foreground: it needs a TTY and never returns, so an automated
# agent can't use it. This script instead:
#   * SSHes non-interactively (no TTY, BatchMode so it fails fast instead of
#     hanging on a prompt),
#   * pulls the latest code, rebuilds the GPU image,
#   * runs the diagnostic CLI on a test drawing,
#   * prints ONLY the JSON report on stdout (all progress goes to stderr) and
#     then EXITS.
#
# Everything is overridable via environment variables:
#   ./test.sh                          # full VLM diagnostic, default PDF
#   MODE=cv ./test.sh                  # fast CV-only run (no model load)
#   PDF=/data/T1025215_C.pdf ./test.sh # a different drawing (mounted at /data)
#   BUILD=0 ./test.sh                  # skip the rebuild, just run
#   HOST=myhost DIR=sindri ./test.sh   # different remote / directory
#
set -euo pipefail

HOST="${HOST:-4mehpc4_3}"
DIR="${DIR:-sindri}"
GPU="${GPU:-nvidia.com/gpu=1}"
MODEL="${VLM_MODEL_ID:-Qwen/Qwen2.5-VL-7B-Instruct}"
PDF="${PDF:-/data/T1025300_B.pdf}"
MODE="${MODE:-vlm}"                       # vlm | cv
BUILD="${BUILD:-1}"                       # 1 = rebuild image, 0 = skip
ENGINE="${ENGINE:-podman}"               # podman | docker
SSH_OPTS="${SSH_OPTS:--T -o BatchMode=yes}"

# Mode decides the diagnostic flag and whether we wire up the GPU + model.
if [ "$MODE" = "vlm" ]; then
  DIAG_FLAGS="--vlm"
  RUN_OPTS="--device $GPU -e OCR_BACKEND=vlm -e VLM_MODEL_ID=$MODEL -v sindri-models:/models"
else
  DIAG_FLAGS=""
  RUN_OPTS=""
fi

echo ">> test.sh: host=$HOST dir=$DIR mode=$MODE build=$BUILD pdf=$PDF" >&2

# Unquoted heredoc: the HOST/DIR/PDF/... values are expanded HERE (baked into
# the remote script); only \$PWD is escaped so it expands on the remote after cd.
# shellcheck disable=SC2086,SC2029
ssh $SSH_OPTS "$HOST" bash -s <<REMOTE
set -euo pipefail
cd "$DIR"
echo ">> git pull --ff-only" >&2
git pull --ff-only >&2
if [ "$BUILD" = "1" ]; then
  echo ">> $ENGINE build sindri-gpu" >&2
  $ENGINE build -f Dockerfile.gpu -t sindri-gpu . >&2
fi
echo ">> $ENGINE run diagnostic ($MODE)" >&2
$ENGINE run --rm $RUN_OPTS \
  -v "\$PWD/test_docs:/data" \
  sindri-gpu \
  python -m app.pipeline.diagnose "$PDF" $DIAG_FLAGS --out /data/diag
REMOTE
