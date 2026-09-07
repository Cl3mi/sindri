#!/usr/bin/env bash
# Re-score the standing runs over SINGLE-SHEET drawings only, and write the
# aggregate digests into docs/eval/.
#
#   ./rescore_onepage.sh [run-name ...]        (default: the three references)
#
# Why this exists as a script: `score` reaches the protected root, so an agent
# cannot run it — the guard hook refuses, correctly. Every command here is the
# sanctioned CLI, single and unpiped, exactly as CLAUDE.md §1 requires; the
# operator runs the file.
#
# Why one-sheet only: render_page takes page_index=0 and always has, so gold on
# sheet 2 is a miss no model can recover, charged at w=10 — the heaviest weight
# — and sitting in the denominator of every recall number in the campaign.
#
# EVERY number moves. 173.05 / 170.05 / 176.40 / 172.00 were all scored over the
# whole dev split. report._check_comparable refuses a filtered report against an
# unfiltered one, so these runs are comparable only with each other. Re-derive
# the -4.40 here before quoting it again.
set -uo pipefail

ROOT="${SINDRI_CLIENT_ROOT:-$HOME/sindri-client-data}"
WEIGHTS="${WEIGHTS:-docs/eval/weights.json}"
RUNS=("${@:-r3-awqcontrol r3-nf4control r3-loraread}")
read -r -a RUNS <<< "${RUNS[*]}"

[ -d "$ROOT" ] || { echo "no client root at $ROOT (set SINDRI_CLIENT_ROOT)" >&2; exit 1; }

for run in "${RUNS[@]}"; do
    echo "=== $run (one sheet only) ==="
    python3 -m app.eval.runner score \
        --run "$ROOT/runs/$run" \
        --gold "$ROOT/gold" \
        --pdfs "$ROOT/corpus/originals" \
        --max-pages 1 \
        --splits "$ROOT/meta/splits.json" --split dev \
        --weights "$WEIGHTS" \
        --name "$run-1p" \
        --out "$ROOT/reports/$run-1p.report.json" || exit 1

    python3 -m app.eval.runner summary \
        "$ROOT/reports/$run-1p.report.json" \
        --out "docs/eval/${run#r3-}-1p-summary.json" || exit 1
done

# The matched-control comparison, control FIRST — compare_runs reports b - a, and
# experiment.py reads the control out of run_a.
if [ -f "$ROOT/reports/r3-nf4control-1p.report.json" ] &&
   [ -f "$ROOT/reports/r3-loraread-1p.report.json" ]; then
    echo "=== loraread vs nf4control (one sheet only) ==="
    python3 -m app.eval.runner compare \
        "$ROOT/reports/r3-nf4control-1p.report.json" \
        "$ROOT/reports/r3-loraread-1p.report.json" \
        --out docs/eval/loraread-1p-vs-nf4control-1p.json || exit 1
fi

echo
echo "corpus page counts (the number to record in the writeup):"
python3 -m app.eval.runner probe "$ROOT/corpus/originals" --summary
