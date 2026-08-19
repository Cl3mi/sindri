#!/usr/bin/env bash
# End-to-end Rung-0 baseline: push the clean drawings to the GPU host, predict
# there with the production model, pull the predictions back, score against the
# gold that never left this machine, and print an NDA-safe summary.
#
#   ./run_baseline_gpu.sh [ssh-host] [remote-root] [run-name]
#   ./run_baseline_gpu.sh 4mehpc4_3 '~/sindri-eval-data' baseline
#
# What travels: the 99 clean drawings and the split file. Nothing else.
# The inspection sheets, the gold values and the ballooned drawings stay here,
# because scoring runs where the gold lives.
#
# What comes back: prediction dumps only.
#
# What reaches a human: the output of `runner summary` — aggregate metrics with
# salted document ids, no values.
#
# The offline Qwen model in the container reads the drawings. That is the
# product doing its job on your hardware; nothing is sent to a cloud service.
set -euo pipefail

HOST="${1:-4mehpc4_3}"
RROOT="${2:-~/sindri-eval-data}"
RUN="${3:-baseline}"
LOCAL_ROOT="${SINDRI_CLIENT_ROOT:-$HOME/sindri-client-data}"
MODEL="${VLM_MODEL_ID:-Qwen/Qwen2.5-VL-72B-Instruct-AWQ}"
GPU="${GPU:-nvidia.com/gpu=all}"
BRANCH="${BRANCH:-worktree-eval-harness}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Same resolution as sync_client_data.sh, and for the same reason: a tilde in a
# quoted ssh argument is not expanded remotely.
RROOT=$(ssh -o BatchMode=yes "$HOST" "eval echo $RROOT") || {
    echo "cannot resolve remote root on $HOST" >&2; exit 1; }
case "$RROOT" in
    /*) ;;
    *) echo "remote root did not resolve to an absolute path: $RROOT" >&2; exit 1 ;;
esac
echo "remote root resolves to: $RROOT"

echo "== 1/5 push drawings =="
"$HERE/sync_client_data.sh" push "$HOST" "$RROOT"

echo "== 2/5 sync code + build image on $HOST =="
ssh -o BatchMode=yes "$HOST" "
  set -euo pipefail
  cd ~/sindri
  git fetch -q origin '$BRANCH'
  git checkout -q -B '$BRANCH' 'origin/$BRANCH'
  echo \"code at \$(git rev-parse --short HEAD)\"
  podman build -q -f Dockerfile.gpu -t sindri-gpu . >/dev/null
  echo 'image built'
"

echo "== 3/5 predict on dev split (model=$MODEL) =="
# Mirrors run-gpu.sh: rootless CDI overlay when a corrected spec exists,
# otherwise straight through.
#
# The container gets no SINDRI_DOC_SALT and has no ~/.claude/sindri-doc-salt, so
# the doc ids in this step's log are hashed under a salt it mints and then
# destroys. They are readable, but they join to NOTHING — not to the report from
# step 5, not to a previous run. Per-document facts come from `runner summary`,
# which hashes under the local salt (see summary.clamped_docs). Passing the salt
# in would make the logs joinable at the cost of putting it on the GPU host and
# in that host's process list; that is the user's call, not a default.
ssh -o BatchMode=yes "$HOST" "
  set -euo pipefail
  cd ~/sindri
  RUNCMD=(podman run --rm --device '$GPU'
    -e OCR_BACKEND=vlm -e VLM_MODEL_ID='$MODEL'
    -v sindri-models:/models
    -v '$RROOT':/data:Z
    sindri-gpu
    python -m app.eval.runner predict
      --pdfs /data/corpus/originals --out /data/runs/$RUN
      --splits /data/meta/splits.json --split dev)
  if [ -f ~/cdi/nvidia.yaml ]; then
    podman unshare -- bash -c '
      set -euo pipefail
      for _ in 1 2 3 4 5; do umount \"\$2\" 2>/dev/null || break; done
      mount --bind \"\$1\" \"\$2\"; shift 2; exec \"\$@\"
    ' cdi-overlay ~/cdi/nvidia.yaml /etc/cdi/nvidia.yaml \"\${RUNCMD[@]}\"
  else
    \"\${RUNCMD[@]}\"
  fi
"

echo "== 4/5 pull predictions =="
"$HERE/sync_client_data.sh" pull "$HOST" "$RROOT" "$RUN" "$LOCAL_ROOT"

echo "== 5/5 score locally against gold, then summarise =="
python3 -m app.eval.runner score \
    --run "$LOCAL_ROOT/runs/$RUN" --gold "$LOCAL_ROOT/gold" \
    --splits "$LOCAL_ROOT/meta/splits.json" --split dev \
    --weights "$HERE/docs/eval/weights.json" \
    --name "$RUN-dev" --out "$LOCAL_ROOT/reports/$RUN-dev.report.json"

python3 -m app.eval.runner summary \
    "$LOCAL_ROOT/reports/$RUN-dev.report.json" \
    --out "$HERE/docs/eval/baseline-summary.json"

echo
echo "Summary written to docs/eval/baseline-summary.json (safe to share)."
echo "The full report stays at $LOCAL_ROOT/reports/ — it embeds gold values."
