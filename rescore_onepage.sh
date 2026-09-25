#!/usr/bin/env bash
# Re-score the standing runs under the SCOPE POLICY, and write the aggregate
# digests into docs/eval/.
#
#   ./rescore_onepage.sh [run-name ...]        (default: every measured run)
#
# THE SCOPE POLICY (set 2026-09-09). The product covers SINGLE-SHEET drawings at
# a size the renderer handles at full resolution. Both exclusions are structural
# rather than model faults, so scoring them measures a capability never claimed:
#
#   --max-pages 1      render_page takes page_index=0 and always has, so gold on
#                      sheet 2 is unreachable, charged at w=10 -- the heaviest
#                      weight there is.
#   --exclude-clamped  render.py clamps any page over the pixel budget. The four
#                      clamped drawings in dev cost 283.75 review at 0.371
#                      recall against 141.62 at 0.728 for the other sixteen, and
#                      raising that budget was measured and LOST -- they need
#                      tiling that does not exist.
#
# Both excluded counts are PRINTED, so "we support N of M drawings" stays
# answerable and cannot be confused with "some runs failed".
#
# Why this exists as a script: `score` reaches the protected root, so an agent
# cannot run it -- the guard hook refuses, correctly. Every command here is the
# sanctioned CLI, single and unpiped, exactly as CLAUDE.md §1 requires; the
# operator runs the file.
#
# EVERY NUMBER MOVES. 173.05 / 170.05 / 176.40 / 172.00 / 174.05 were all scored
# over the whole dev split. report._check_comparable refuses a scoped report
# against an unscoped one, so these runs are comparable only with each other.
set -uo pipefail

ROOT="${SINDRI_CLIENT_ROOT:-$HOME/sindri-client-data}"
WEIGHTS="${WEIGHTS:-docs/eval/weights.json}"
# Which split to score. dev by default, so every existing invocation is
# unchanged. `SPLIT=test ./rescore_onepage.sh r3-awqtest` is the only honest
# generalization number in the project: nothing has ever scored on test, and dev
# is the split ten-plus arms were selected against.
SPLIT="${SPLIT:-dev}"
RUNS=("${@:-r3-awqcontrol r3-nf4control r3-loraread r3-vllmcontrol r3-vllmlora r3-7bawq r3-32bawq r3-hybrid r3-cropctx r3-cropctx48 r3-tallpad}")
read -r -a RUNS <<< "${RUNS[*]}"

[ -d "$ROOT" ] || { echo "no client root at $ROOT (set SINDRI_CLIENT_ROOT)" >&2; exit 1; }

for run in "${RUNS[@]}"; do
    [ -d "$ROOT/runs/$run" ] || { echo "=== $run: no dumps pulled, skipping ==="; continue; }
    echo "=== $run (single sheet, supported size) ==="
    python3 -m app.eval.runner score \
        --run "$ROOT/runs/$run" \
        --gold "$ROOT/gold" \
        --pdfs "$ROOT/corpus/originals" \
        --max-pages 1 \
        --dpi 300 --exclude-clamped \
        --splits "$ROOT/meta/splits.json" --split "$SPLIT" \
        --weights "$WEIGHTS" \
        --name "$run-scoped" \
        --out "$ROOT/reports/$run-scoped.report.json" || exit 1

    python3 -m app.eval.runner summary \
        "$ROOT/reports/$run-scoped.report.json" \
        --out "docs/eval/${run#r3-}-scoped-summary.json" || exit 1
done

# The matched-control comparisons, control FIRST -- compare_runs reports b - a,
# and experiment.py reads the control out of run_a.
#
# Skipped off dev, because every control here was scored on dev: a cross-split
# pair is a different document set, _check_comparable refuses it, and printing
# NOT COMPARABLE once per pair would teach the operator to ignore the one
# message that must never become background noise.
compare_pair() {   # compare_pair <control> <arm> <out-name>
    [ "$SPLIT" = dev ] || return 0
    [ -f "$ROOT/reports/$1-scoped.report.json" ] || return 0
    [ -f "$ROOT/reports/$2-scoped.report.json" ] || return 0
    echo "=== ${2#r3-} vs ${1#r3-} (scoped) ==="
    python3 -m app.eval.runner compare \
        "$ROOT/reports/$1-scoped.report.json" \
        "$ROOT/reports/$2-scoped.report.json" \
        --out "docs/eval/$3" || exit 1
}
compare_pair r3-nf4control  r3-loraread   loraread-scoped-vs-nf4control-scoped.json
compare_pair r3-vllmcontrol r3-vllmlora   vllmlora-scoped-vs-vllmcontrol-scoped.json
compare_pair r3-awqcontrol  r3-7bawq      7bawq-scoped-vs-awqcontrol-scoped.json
# The 32B's +38.80 was quoted for a whole session with no comparison file behind
# it: no ci95, no weight-robustness. It is the arm the hybrid is built on, so it
# is the last one that should rest on a cost delta alone.
compare_pair r3-awqcontrol  r3-32bawq     32bawq-scoped-vs-awqcontrol-scoped.json
# The hybrid against the read stack it SHARES -- same 72B AWQ checkpoint, same
# image, no adapter, and the only difference is which weights localised. Against
# r3-32bawq it would move two variables at once and credit the reader's
# contribution to the detector.
compare_pair r3-awqcontrol  r3-hybrid     hybrid-scoped-vs-awqcontrol-scoped.json
# The hybrid MACHINERY, priced against the run it must reproduce exactly. Feed
# the output to `python3 -m app.eval.gate` -- every per-document delta must be
# 0.0, as awqgate's was.
compare_pair r3-awqcontrol  r3-hybridgate hybridgate-scoped-vs-awqcontrol-scoped.json
# The crop arm. Single variable against production -- same checkpoint, same
# image, same prompts, only SINDRI_CROP_PAD moved -- and its first score had no
# comparison file at all, which is the gap the 32B sat in for a session.
compare_pair r3-awqcontrol  r3-cropctx    cropctx-scoped-vs-awqcontrol-scoped.json
# The second dose, against the SAME control as the first. The two are points on
# one curve: comparing 48 against 24 would price the step rather than the
# setting, and the registered decision rule reads both against production.
compare_pair r3-awqcontrol  r3-cropctx48  cropctx48-scoped-vs-awqcontrol-scoped.json
# The height-dependent pad, against the SHIPPED pad rather than production.
# r3-awqcontrol ran at pad 6, so comparing there would price the shipped pad
# change and the height dependence together; r3-cropctx is pad 24 everywhere,
# which is this arm minus its one variable.
compare_pair r3-cropctx    r3-tallpad    tallpad-scoped-vs-cropctx-scoped.json

echo
echo "corpus page counts (the number the writeup is still waiting for):"
python3 -m app.eval.runner probe "$ROOT/corpus/originals" --summary
