#!/usr/bin/env bash
# HOST-SIDE GPU queue. Runs ON the GPU host, under tmux, with no connection to
# the operator's machine — start it, disconnect, come back in two days, scp the
# dumps.
#
#   tmux new -d -s rung3 '~/sindri/run_gpu_queue.sh 0 trainpredict'
#   tmux new -d -s gate  '~/sindri/run_gpu_queue.sh 1 awqgate base72bnf4'
#   tmux new -d -s ctl   '~/sindri/run_gpu_queue.sh 1 awqcontrol nf4control'
#
# ONE card per queue, with ONE exception. The first argument is a GPU index and
# it reaches podman as `--device nvidia.com/gpu=$i`, so a queue can never occupy
# both cards by accident -- this host has 24+ other users. Two queues at once
# means two explicit launches naming different indices, which is a deliberate
# act.
#
# The exception is the HYBRID arm, which serves two checkpoints at once: the 32B
# localising (~44 GB) and the 72B transcribing (~43 GB) do not fit in one 80 GB
# H100. It takes a COMMA-SEPARATED list -- `run_gpu_queue.sh 0,1 hybrid` -- and
# the script refuses to start it on one card, because an OOM there does not
# crash the run: extract._safe_read swallows read exceptions, so it would come
# back as a full run of empty values.
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
# The hybrid arm's LOCALISATION checkpoint. Under the scope policy the 32B is
# the best detector and the worst reader measured -- missed 70 against the 72B's
# 88, field_acc 0.2614 against 0.4798 -- and the detector's weights are the only
# thing in the campaign that has ever moved `missed`.
DETECT_MODEL="${DETECT_MODEL:-Qwen/Qwen2.5-VL-32B-Instruct-AWQ}"
LOGDIR="${LOGDIR:-$HOME/rung3-logs}"
# Which image each stage runs in. The train-split pass deliberately uses the
# image that produced every existing measurement; the NF4 stages need the one
# with peft+bitsandbytes. Kept separate so a dependency change cannot contaminate
# the 26 h crop pass.
IMAGE_OLD="${IMAGE_OLD:-sindri-gpu}"
IMAGE_NEW="${IMAGE_NEW:-sindri-gpu-nf4}"
IMAGE_VLLM="${IMAGE_VLLM:-sindri-vllm}"

if [ -z "$GPU_INDEX" ] || [ ${#STAGES[@]} -eq 0 ]; then
    echo "usage: run_gpu_queue.sh <gpu-index>[,<gpu-index>] <stage> [<stage>...]" >&2
    echo "stages: trainpredict awqgate base72bnf4 awqcontrol nf4control lora72bnf4 lora72bawq loraread loramerged mergedcontrol vllmcontrol vllmlora hybrid hybridgate awqtest cropctx cropctx48" >&2
    echo "  the hybrid stages serve two checkpoints and need two cards: 0,1" >&2
    exit 2
fi

# One entry per card, and one --device per entry. Built once: every stage in a
# queue runs on the same cards, which is what makes the occupancy check below a
# check on the whole queue rather than on one stage.
IFS=, read -r -a GPUS <<< "$GPU_INDEX"
DEVICE_ARGS=()
for g in "${GPUS[@]}"; do DEVICE_ARGS+=(--device "nvidia.com/gpu=$g"); done

mkdir -p "$LOGDIR"

# stage -> run name : split : image : extra env : extra predict args
# A stage is fully described here so the queue never needs a special case.
stage_run()    { case "$1" in trainpredict) echo "r3-trainpredict" ;;
                              awqgate)      echo "r3-awqgate" ;;
                              base72bnf4)   echo "r3-base72bnf4" ;;
                              awqcontrol)   echo "r3-awqcontrol" ;;
                              nf4control)   echo "r3-nf4control" ;;
                              lora72bnf4)   echo "r3-lora72bnf4" ;;
                              lora72bawq)   echo "r3-lora72bawq" ;;
                              loraread)     echo "r3-loraread" ;;
                              loramerged)   echo "r3-loramerged" ;;
                              mergedcontrol) echo "r3-mergedcontrol" ;;
                              vllmcontrol)  echo "r3-vllmcontrol" ;;
                              vllmlora)     echo "r3-vllmlora" ;;
                              hybrid)       echo "r3-hybrid" ;;
                              hybridgate)   echo "r3-hybridgate" ;;
                              awqtest)      echo "r3-awqtest" ;;
                              cropctx)      echo "r3-cropctx" ;;
                              cropctx48)    echo "r3-cropctx48" ;; esac; }
# The ONLY stage that touches the frozen test split, and deliberately so: it is
# spendable once per question, and a stage that wandered onto it by accident
# would burn it while producing a report that merely looks incomparable.
stage_split()  { case "$1" in trainpredict) echo "train" ;;
                              awqtest)      echo "test" ;;
                              *)            echo "dev" ;; esac; }
stage_image()  { case "$1" in trainpredict) echo "$IMAGE_OLD" ;;
                              # vLLM cannot share the pinned image: that one is
                              # held at transformers 4.49.0 / autoawq 0.2.8
                              # because 4.50+ breaks AWQ dispatch for Qwen2.5-VL
                              # at inference, and it must stay exactly as every
                              # committed measurement found it.
                              vllmcontrol|vllmlora) echo "$IMAGE_VLLM" ;;
                              *)            echo "$IMAGE_NEW" ;; esac; }
# The serving backend, emitted ONCE. stage_env may set its own, and passing both
# left the command carrying -e OCR_BACKEND=vlm -e OCR_BACKEND=vllm: podman takes
# the last one, so it worked, but the run then depended on an ordering
# convention rather than on what the stage asked for. Resolved the other way,
# the vLLM arms would have run the transformers path against an AWQ checkpoint —
# a complete, plausible, WRONG run under route B's name.
stage_backend() { case "$1" in
                    vllmcontrol|vllmlora|hybrid|hybridgate) echo "" ;;
                    *) echo "-e OCR_BACKEND=vlm" ;; esac; }

stage_env()    { case "$1" in
                   base72bnf4|nf4control)
                     echo "-e VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct -e SINDRI_QUANT=nf4" ;;
                   lora72bnf4|loraread)
                     echo "-e VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct -e SINDRI_QUANT=nf4 -e SINDRI_ADAPTER=read-lora-v1" ;;
                   lora72bawq)
                     echo "-e VLM_MODEL_ID=$MODEL -e SINDRI_ADAPTER=read-lora-v1" ;;
                   loramerged)
                     echo "-e VLM_MODEL_ID=/models/merged/read-lora-v1-awq" ;;
                   # Route B serves Qwen's OFFICIAL AWQ checkpoint untouched —
                   # nothing is quantised here, which is the route's advantage.
                   # No SINDRI_QUANT: AWQ is the checkpoint's own property.
                   vllmcontrol)
                     echo "-e OCR_BACKEND=vllm -e VLM_MODEL_ID=$MODEL" ;;
                   vllmlora)
                     echo "-e OCR_BACKEND=vllm -e VLM_MODEL_ID=$MODEL -e SINDRI_ADAPTER=read-lora-v1" ;;
                   mergedcontrol)
                     echo "-e VLM_MODEL_ID=/models/merged/zero-scale-awq" ;;
                   # 24 px is 2 mm at 300 dpi -- the smallest dose that can
                   # reach a tolerance line stacked under a nominal. The default
                   # 6 px is 0.5 mm and recovers nothing that was clipped.
                   cropctx)
                     echo "-e VLM_MODEL_ID=$MODEL -e SINDRI_CROP_PAD=24" ;;
                   cropctx48)
                     echo "-e VLM_MODEL_ID=$MODEL -e SINDRI_CROP_PAD=48" ;;
                   # VLM_MODEL_ID stays the READ model on both hybrid stages, so
                   # RunConfig.model_id keeps naming the weights the VALUES came
                   # from and the arm stays comparable to r3-awqcontrol, which
                   # was served with exactly this checkpoint in exactly this
                   # image.
                   hybrid)
                     echo "-e OCR_BACKEND=hybrid -e VLM_MODEL_ID=$MODEL -e VLM_DETECT_MODEL_ID=$DETECT_MODEL" ;;
                   hybridgate)
                     echo "-e OCR_BACKEND=hybrid -e VLM_MODEL_ID=$MODEL -e VLM_DETECT_MODEL_ID=$MODEL" ;;
                   *) echo "-e VLM_MODEL_ID=$MODEL" ;; esac; }
stage_why()    { case "$1" in
    trainpredict) echo "60 train documents -> the boxes Rung 3's training crops come from. Never scored: train is the training split." ;;
    awqgate)      echo "adding peft+bitsandbytes to the inference image could perturb AWQ dispatch. This re-runs the AWQ path on dev so app/eval/gate.py can prove all 20 per-document deltas are 0.0. If it does not, the frozen 174.30 baseline is invalid and so is every Rung-2 conclusion." ;;
    base72bnf4)   echo "zero-shot NF4 control. The adapter will be served on this base, so this is what separates 'quantisation changed' from 'LoRA helped'." ;;
    awqcontrol)   echo "RE-RUN of the AWQ zero-shot on current code, because review.LOW_CONF moved 0.6 -> 0.8. That is a PIPELINE change, so every earlier dump carries flags computed at the old threshold and comparing an arm against them would credit the adapter with ~3.00 of threshold move. PREDICTION this run must hit: mean_review_cost 170.05, and recall/n_pred/missed/false_detection/field_acc IDENTICAL to baseline-dev. Only escaped_error->flagged_error may move, by exactly 15." ;;
    nf4control)   echo "the same re-run for the NF4 base, and the control for lora72b-nf4. PREDICTION: mean_review_cost 176.40, everything but the escaped/flagged split identical to r3-base72bnf4, which moves by exactly 17." ;;
    lora72bnf4)   echo "THE FINE-TUNE, isolated. Same NF4 base as r3-nf4control, adapter the only difference, both on current code. Judge vs r3-nf4control -- and on field_acc RISING plus the targeted bucket moving, not on review cost alone, which has been wrong three times on this corpus." ;;
    loraread)     echo "THE FINE-TUNE, actually isolated. lora72bnf4 served the read adapter over the WHOLE model, so detect_regions ran through it and false_detection went 607 -> 931: the arm measured two stages at once and lost on the one it never meant to touch. Detection is now scoped back to base weights. Same base, same adapter, same image, same split as lora72bnf4 -- the scoping is the only variable. PREDICTION, registered before the run: n_pred returns to EXACTLY 926 and false_detection to 607, bit-identical to r3-nf4control, because decoding is greedy and both are pure functions of detection. If n_pred is not 926 the scoping is incomplete and this arm is VOID. Judge vs r3-nf4control (176.40), never vs exp-control, and on field_acc plus the read buckets, not on review cost alone." ;;
    loramerged)   echo "THE DEPLOYMENT ROUTE THAT WORKS. read-lora-v1 merged into bf16 and re-quantised to AWQ by merge_lora.py, so the fine-tune is baked into an ordinary checkpoint: no PEFT at serving time, no WQLinear_GEMM problem, and none of the NF4 route's +6.35 review cost or its inference penalty. Judge vs r3-mergedcontrol, NOT vs r3-awqcontrol -- the merged checkpoint is a different one from any AWQ number ever measured. Reference: r3-loraread showed the scoped adapter is worth -4.40 on the NF4 base; this asks whether that survives the round trip onto the base production serves." ;;
    mergedcontrol) echo "THE CONTROL FOR loramerged, and it is not optional. Same merge and the same re-quantisation with the adapter contribution scaled to zero (merge_lora.py --zero-scale), so it is numerically the base but travels byte-identical code. PREDICTION: it reproduces r3-awqcontrol at 170.05. If it does NOT, the merge/quantisation round trip moved the model on its own and no delta from loramerged is attributable to the fine-tune. Run it FIRST." ;;
    vllmcontrol)  echo "ROUTE B's CONTROL, and it is not optional. Qwen's official AWQ checkpoint served through vLLM with NO adapter. vLLM's kernels are not transformers' kernels, so r3-awqcontrol (170.05) is NOT a valid baseline for a vLLM run: this prices the change of serving stack by itself, and only then can r3-vllmlora price the adapter. Run it FIRST." ;;
    vllmlora)     echo "ROUTE B: read-lora-v1 served at RUNTIME on the official AWQ checkpoint via vLLM's per-request LoRA. Nothing is quantised on this route -- no merge, no calibration, none of the failure class that cost days on route A. Detection issues requests with NO adapter and reads issue them with one, which is VLMBackend._base_weights() expressed the way vLLM expects. Judge vs r3-vllmcontrol, never vs r3-awqcontrol, and on field_acc plus escaped_rate, not review cost alone." ;;
    awqtest)      echo "THE HONEST NUMBER. Production -- the same AWQ checkpoint, image and settings r3-awqcontrol serves -- on the FROZEN TEST SPLIT, which nothing has ever predicted on. Every figure quoted to the client (133.93, recall 0.7170, 28.3% missed) comes from dev, and dev is the split ten-plus arms were selected against; splits.py also forces the structurally atypical `variants` into test on purpose, so cross-template generalization is visible here and nowhere else. This is NOT an arm and has no control: _check_comparable will refuse it against any dev report, correctly, because it is a different document set. It is a standalone number, and the question it answers is whether anything already shown to the client is optimistic. Score it under the same scope policy: SPLIT=test ./rescore_onepage.sh r3-awqtest." ;;
    cropctx)      echo "THE CROP, which r3-hybrid identified as the dominant term in read accuracy: holding the reader and changing the boxes moved field_acc -0.206, against +0.013 for holding the boxes and changing the reader. Crop preparation is the only untested family on that lever -- every box lever tried so far was about WHICH boxes exist. SINDRI_CROP_PAD 6 -> 24 px, i.e. 0.5 mm -> 2 mm of context at 300 dpi, which is the smallest dose that can reach a tolerance line stacked under a nominal. TARGET BUCKET, registered before the run: dropped_tolerances (48 rows) and missing:lower_tol (45) / missing:upper_tol (28) must FALL. If they do not, the arm is a loss whatever the cost does -- the rule that killed readcenter, whose target bucket was provably untouched at 64 -> 64. CEILING: 30 of 223 matched rows are wrong ONLY in their tolerances, worth about -6.80. IDENTITY GATE: detection is upstream of the crop, so n_pred must be EXACTLY 569. Judge vs r3-awqcontrol (133.93 scoped). Full prediction: docs/plans/2026-09-14-crop-context-arm-prediction.md." ;;
    cropctx48)    echo "THE SECOND CROP DOSE. cropctx (pad 24) WON: 131.87 vs 133.93, better under 6 of 6 weightings, field_acc 0.4798 -> 0.5291 -- the largest read-quality move in the campaign, and larger than read-lora-v1'\''s +0.039 which needs a trained adapter and a blocked deployment route. Detection came back BIT-IDENTICAL (n_pred 569, missed 88, false 346), so the whole delta is read stage. The registered damage counter did NOT fire -- misplaced_matches went 44 -> 42 -- so the mechanism has not met its limit, and only 4 of 30 recoverable tolerance-only rows were taken. 48 px is 4 mm at 300 dpi. DECISION RULE, registered before the run: field_acc up AND misplaced_matches <= 46 -> take 48 and test 96; field_acc up but misplaced > 46 -> the limit is met, 24 ships; field_acc down -> 24 ships. SHIP 24 EITHER WAY -- it is banked. Identity gate: n_pred must be 569 again. Full result and prediction: docs/plans/2026-09-15-crop-context-arm-result.md." ;;
    hybrid)       echo "THE HYBRID: the 32B localises, the 72B transcribes. The 32B is the best detector and the worst reader measured -- missed 70 vs 88, recall 0.7749 vs 0.7170, field_acc 0.2614 vs 0.4798 -- and Rung 1 established that the detector's WEIGHTS are the only thing that has ever moved missed; its knobs and prompts never did. Judge vs r3-awqcontrol (133.93 scoped), never vs r3-32bawq, which differs in two variables at once. VOID GATES, registered in docs/plans/2026-09-09-hybrid-arm-prediction.md BEFORE this ran: n_pred must be EXACTLY 890 (detection is a pure function of the detect model) and field_acc must be >= 0.40 (below it the reads did not reach the 72B). PREDICTED 164.60, a LOSS of about +30: 18 recovered misses are worth -180 and the 303 extra false detections cost +606 at w=2. The arm is not run for its cost verdict, which is arithmetic; it is run for the ceiling on missed, for whether the 32B's recall survives good reading, and for what the 72B reads on the spurious boxes -- which is the only thing that could size a filter." ;;
    hybridgate)   echo "THE HYBRID'\''S CONTROL, and it is only skippable by the rule the prediction registers. Serves the 72B on BOTH passes through the hybrid machinery, so it prices the two-card device pinning and the delegation layer by themselves. PREDICTION: it reproduces r3-awqcontrol with every per-document delta exactly 0.0, which app/eval/gate.py checks -- the same role awqgate played for the dependency change. Run it only if the hybrid'\''s field_acc lands outside [0.40, 0.52]; inside that band the device change cannot be the explanation, because decoding is greedy and CLAUDE.md section 5 records 16 documents at exactly 0.0 across a GPU device change." ;;
    lora72bawq)   echo "THE DEPLOYMENT QUESTION: an adapter attached to what production actually serves. Judge vs r3-awqcontrol (170.05). Trained on NF4 and served on AWQ, so this arm also measures that quantisation mismatch, whose size is unknown." ;;
  esac; }

# The whole queue is validated before anything starts, so a typo or a wrong card
# count costs nothing rather than being discovered after the first stage's ~10
# minute model load.
stage_cards()  { case "$1" in hybrid|hybridgate) echo 2 ;; *) echo 1 ;; esac; }

for s in "${STAGES[@]}"; do
    [ -n "$(stage_run "$s")" ] || { echo "unknown stage: $s" >&2; exit 2; }
    if [ "$(stage_cards "$s")" -gt "${#GPUS[@]}" ]; then
        echo "stage $s serves two checkpoints and needs two cards, e.g." >&2
        echo "  run_gpu_queue.sh 0,1 $s" >&2
        echo "  (32B ~44 GB + 72B ~43 GB does not fit in one 80 GB H100, and an" >&2
        echo "   OOM there does not crash the run -- _safe_read swallows read" >&2
        echo "   exceptions, so it returns a full run of empty values.)" >&2
        exit 2
    fi
done

# Writes to stdout AND the current stage log. Deliberately NOT `{ ... } | tee`
# around the loop body: that runs the body in a SUBSHELL, so `FAILED+=()`,
# `break` and `continue` inside it cannot affect the outer loop -- the queue
# would run every later stage after a failure and ignore completion markers.
# Caught by tests/test_gpu_queue_behaviour.py, which executes this script for
# real against stubbed podman/nvidia-smi.
CURLOG="$LOGDIR/queue-gpu${GPU_INDEX//,/-}.log"
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
    log "run=$run split=$split image=$image gpu(s)=$GPU_INDEX"

    if [ -f "$marker" ]; then
        log "SKIPPED: $marker exists — this stage already finished"
        continue
    fi

    # A 72B load into an occupied card falls back to Tesseract and silently
    # ruins every document, so refuse rather than produce garbage. EVERY card
    # the queue holds is checked: the hybrid loads its second checkpoint ~10
    # minutes after the first, and a queue that checked only card 0 would
    # discover card 1 was taken after paying for that load.
    occupied=""
    for g in "${GPUS[@]}"; do
        used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits \
               -i "$g" 2>/dev/null | tr -d ' ')
        log "gpu $g memory.used=${used:-unknown} MiB"
        if [ -z "$used" ] || [ "$used" -gt 2000 ]; then
            occupied="$g (${used:-unknown} MiB)"
            break
        fi
    done
    if [ -n "$occupied" ]; then
        log "STAGE FAILED ($stage): gpu $occupied is occupied."
        log "  A 72B load into an occupied card falls back to Tesseract."
        FAILED+=("$stage"); break
    fi

    mkdir -p "$outdir"
    RUNCMD=(podman run --rm "${DEVICE_ARGS[@]}"
      $(stage_backend "$stage") $(stage_env "$stage")
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

CURLOG="$LOGDIR/queue-gpu${GPU_INDEX//,/-}.log"
{
    log "===== queue finished on gpu(s) $GPU_INDEX ====="
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
