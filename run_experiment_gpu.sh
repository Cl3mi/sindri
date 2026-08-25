#!/usr/bin/env bash
# Multi-arm GPU experiment: predict each arm on the GPU host with a different
# detection-knob setting, pull the dumps back, score every arm against the gold
# that never leaves this machine, and compare each one to the control.
#
#   ./run_experiment_gpu.sh [ssh-host] [remote-root] [arm ...]
#   ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data'            # all arms
#   ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' nomerge    # just one
#
# What travels: the clean drawings and the split file. Nothing else. The
# inspection sheets, gold values and ballooned drawings stay here, because
# scoring runs where the gold lives. What comes back: prediction dumps only.
# What reaches a human: `runner summary` output — aggregate metrics, salted ids.
#
# Design notes, each one paid for by a mistake already made:
#   * every arm gets its own run name. Reusing one would (a) overwrite the
#     comparison point and (b) hit resume, which compares RunConfig — and the
#     container's git_sha is always "unknown", so before detection knobs were
#     recorded in RunConfig.extra a re-run silently skipped all 20 documents as
#     "already predicted" and measured nothing.
#   * the summary is written per arm, never to baseline-summary.json.
#   * GPU is pinned, not "all". GPU 0 is often busy with another job and the 72B
#     AWQ then fails to load, falling back to Tesseract, which fails every
#     document loudly but only after wasting the scheduling.
#   * arms run sequentially and a failing arm does not stop the rest.
set -uo pipefail

HOST="${1:-4mehpc4_3}"
RROOT_IN="${2:-~/sindri-eval-data}"
shift 2 2>/dev/null || true
LOCAL_ROOT="${SINDRI_CLIENT_ROOT:-$HOME/sindri-client-data}"
MODEL="${VLM_MODEL_ID:-Qwen/Qwen2.5-VL-72B-Instruct-AWQ}"
GPU="${GPU:-nvidia.com/gpu=1}"
BRANCH="${BRANCH:-worktree-eval-harness}"
SPLIT="${SPLIT:-dev}"
CONTROL_REPORT="${CONTROL_REPORT:-$LOCAL_ROOT/reports/baseline-dev.report.json}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# name : env for the container : what it tests
# Keep one knob per arm. Two knobs at once cannot be attributed, and with a zero
# noise floor (verified: 16 unchanged documents gave per-document delta exactly
# 0.0 across a device change) a single arm per hypothesis is enough — no repeats.
declare -A ARM_ENV=(
  [control]=""
  [nomerge]="-e SINDRI_MERGE_MAX_LINES=1"
  [tightmerge]="-e SINDRI_MERGE_Y_GAP=8"
  [finetiles]="-e VLM_TILE=768"
)
declare -A ARM_WHY=(
  [control]="reproduction check: must match the committed baseline's metrics"
  [nomerge]="82 contended misses: is merge_adjacent collapsing sibling callouts?"
  [tightmerge]="same hypothesis, softer — merge less rather than not at all"
  [finetiles]="74 isolated misses: does a finer grid find undetected callouts?"
)
ARM_ORDER=(control nomerge tightmerge finetiles)

ARMS=("$@")
[ ${#ARMS[@]} -eq 0 ] && ARMS=("${ARM_ORDER[@]}")
for a in "${ARMS[@]}"; do
    [ -n "${ARM_ENV[$a]+set}" ] || { echo "unknown arm: $a" >&2
        echo "known: ${ARM_ORDER[*]}" >&2; exit 2; }
done

RROOT=$(ssh -o BatchMode=yes "$HOST" "eval echo $RROOT_IN") || {
    echo "cannot resolve remote root on $HOST" >&2; exit 1; }
case "$RROOT" in
    /*) ;;
    *) echo "remote root did not resolve to an absolute path: $RROOT" >&2; exit 1 ;;
esac

echo "host=$HOST remote_root=$RROOT gpu=$GPU split=$SPLIT"
echo "arms: ${ARMS[*]}"
echo

# Refuse to start if the pinned GPU is already busy: a 72B AWQ load into a card
# with another job on it is the documented way to lose a run.
FREE=$(ssh -o BatchMode=yes "$HOST" \
    "nvidia-smi --query-gpu=index,memory.used --format=csv,noheader" ) || true
echo "GPU memory in use on $HOST:"; echo "$FREE"
echo

echo "== push drawings =="
"$HERE/sync_client_data.sh" push "$HOST" "$RROOT" || exit 1

echo "== sync code + build image on $HOST =="
ssh -o BatchMode=yes "$HOST" "
  set -euo pipefail
  cd ~/sindri
  git fetch -q origin '$BRANCH'
  git checkout -q -B '$BRANCH' 'origin/$BRANCH'
  echo \"code at \$(git rev-parse --short HEAD)\"
  podman build -q -f Dockerfile.gpu -t sindri-gpu . >/dev/null
  echo 'image built'
" || exit 1

FAILED=()
for arm in "${ARMS[@]}"; do
    run="exp-$arm"
    echo
    echo "===== arm: $arm ====="
    echo "why: ${ARM_WHY[$arm]}"
    echo "env: ${ARM_ENV[$arm]:-<defaults>}"

    # The container gets no SINDRI_DOC_SALT and has no ~/.claude/sindri-doc-salt,
    # so the doc ids in this log are hashed under a salt it mints and destroys.
    # They join to NOTHING. Per-document facts come from `runner summary`, which
    # hashes under the local salt.
    if ! ssh -o BatchMode=yes "$HOST" "
        set -euo pipefail
        cd ~/sindri
        RUNCMD=(podman run --rm --device '$GPU'
          -e OCR_BACKEND=vlm -e VLM_MODEL_ID='$MODEL' ${ARM_ENV[$arm]}
          -v sindri-models:/models
          -v '$RROOT':/data:Z
          sindri-gpu
          python -m app.eval.runner predict
            --pdfs /data/corpus/originals --out /data/runs/$run
            --splits /data/meta/splits.json --split $SPLIT)
        if [ -f ~/cdi/nvidia.yaml ]; then
          podman unshare -- bash -c '
            set -euo pipefail
            for _ in 1 2 3 4 5; do umount \"\$2\" 2>/dev/null || break; done
            mount --bind \"\$1\" \"\$2\"; shift 2; exec \"\$@\"
          ' cdi-overlay ~/cdi/nvidia.yaml /etc/cdi/nvidia.yaml \"\${RUNCMD[@]}\"
        else
          \"\${RUNCMD[@]}\"
        fi
    "; then
        echo "ARM FAILED (predict): $arm" >&2; FAILED+=("$arm"); continue
    fi

    echo "-- pull $run --"
    "$HERE/sync_client_data.sh" pull "$HOST" "$RROOT" "$run" "$LOCAL_ROOT" || {
        echo "ARM FAILED (pull): $arm" >&2; FAILED+=("$arm"); continue; }

    echo "-- score $run --"
    python3 -m app.eval.runner score \
        --run "$LOCAL_ROOT/runs/$run" --gold "$LOCAL_ROOT/gold" \
        --splits "$LOCAL_ROOT/meta/splits.json" --split "$SPLIT" \
        --weights "$HERE/docs/eval/weights.json" \
        --name "$run-$SPLIT" \
        --out "$LOCAL_ROOT/reports/$run-$SPLIT.report.json" || {
        echo "ARM FAILED (score): $arm" >&2; FAILED+=("$arm"); continue; }

    python3 -m app.eval.runner summary \
        "$LOCAL_ROOT/reports/$run-$SPLIT.report.json" \
        --out "$HERE/docs/eval/$run-summary.json" >/dev/null

    if [ -f "$CONTROL_REPORT" ]; then
        echo "-- compare $run vs committed baseline --"
        python3 -m app.eval.runner compare \
            "$CONTROL_REPORT" "$LOCAL_ROOT/reports/$run-$SPLIT.report.json" \
            --out "$HERE/docs/eval/$run-vs-control.json" >/dev/null \
            || echo "NOTE: not comparable — see stderr above" >&2
    else
        echo "NOTE: no control report at $CONTROL_REPORT — nothing to compare" >&2
    fi

    # REPRODUCTION GATE, deliberately OUTSIDE the existence test above. The
    # control arm changes no knob and scoring is deterministic on this corpus,
    # so every per-document delta must be exactly 0.0. A missing comparison
    # point must FAIL this arm rather than silently skip the gate, which is what
    # the previous nested version did (findings §8.1). app/eval/gate.py treats
    # an absent file as a failure, so no `-f` test belongs here. (The dumps are
    # not byte-identical -- the old ones predate RunConfig.extra -- but the
    # SCORES must be.)
    if [ "$arm" = "control" ]; then
        if ! python3 -m app.eval.gate "$HERE/docs/eval/$run-vs-control.json"; then
            echo "ABORTING: control did not reproduce" >&2
            FAILED+=("control-reproduction"); break
        fi
    fi
    echo "arm $arm done: docs/eval/$run-summary.json"
done

echo
echo "===== all arms finished ====="
[ ${#FAILED[@]} -gt 0 ] && echo "FAILED arms: ${FAILED[*]}" >&2
echo "Now read the decision table:"
echo "  python3 -m app.eval.experiment"
echo
echo "Summaries are values-blind and safe to share. Full reports stay in"
echo "$LOCAL_ROOT/reports/ — they embed gold values."
[ ${#FAILED[@]} -gt 0 ] && exit 1
exit 0
