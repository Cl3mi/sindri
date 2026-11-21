# 0007. Agents see only counts-only digests; the CLI itself refuses unsafe output paths

Status: Accepted
Date: 2026-08-26 (digest namespacing); 2026-10-23 (runner output hardening, H1)

## Context

The ~100 client drawings and inspection sheets are confidential under an NDA
(`docs/eval/DATA-HANDLING.md`). Two rules: no AI model reads them, and they never enter
git. The client data lives in a protected root outside every working tree
(`~/sindri-client-data`), so a git leak is structurally impossible, not just blocked.

Yet the work is *driven* by an AI agent that must still see how the pipeline is doing. The
agent therefore needs a view of runs and reviews that is useful and provably free of client
values. Enforcement is by a Bash guard hook and git pre-commit hook, which check the
**shape** of a command, not its arguments.

That last point is the hole H1 found (`2026-10-23-runner-output-hardening-design.md` §1):
the guard allowlists `python -m app.eval.runner <subcommand>`, so any output flag of an
allowlisted subcommand is an agent-chosen write path. An agent could aim `summary --out` at
a gold file, `predict --out` at an existing run directory, or `score --out` at the frozen
split file.

## Decision

1. **Digests are counts-only.** `runner summary` / `compare` / the grading digest emit
   aggregates, salted document ids and closed vocabulary words only; the committed form
   lives in `docs/eval/`. Client values live in reports under the protected root. Keys are
   namespaced (`field:lower_tol`, `fields:char_type+nominal`) so a COUNT keyed by a field
   name cannot be confused with a VALUE by the pre-commit hook.
2. **The guard is not widened; the CLI refuses by itself** (`app/eval/outputs.py`, one
   writer: atomic `.partial` + `os.replace`). Output flags fall in three classes:
   A (digests: `.json`, outside every protected root, replaced only if it carries the
   digest's own leading `"kind"` marker), B (client-value files: only direct children of
   `reports/` or `meta/`, the frozen split and variants never overwritten), C (directories
   under `runs/` or `gold/`: absent, empty, or ours by marker). Refusals are fixed
   sentences, never paths or exception text.
3. **Interactive review tools stay with the operator** (`review-serve`, `review-tally`,
   `campaign-deck`); they read decks full of client text and are not in the allowlist.

## Alternatives considered

- **Widening the guard** to validate arguments — explicitly forbidden by the design (§5):
  the CLI has to refuse by itself, because the guard sees shapes.
- **Class A may overwrite "never" or "any path"** — the operator chose "only a file
  carrying its own kind marker" (§4 decision 1).
- **Class B/C "anywhere inside the root"** — operator chose fixed subfolders (§4 decision 2).
- **Fail closed when no protected root is configured** — operator chose to skip only the
  root rule so the GPU queue and CI keep working (§4 decision 3).
- Committing the stripped `baseline-report.json`, `splits.json` and `variants.txt` as the
  original Rung-0 plan wanted — amended: part numbers are confidential, so these stay in
  `<root>/meta/` and a `splits_hash` embedded in every report keeps the split frozen
  without committing it.

## Consequences

Easier: the agent can compare arms and read grading results with no path to client text;
a new flag cannot silently become a write primitive (`tests/eval/test_runner_output_exploits.py`
probes every flag and its completeness test fails when a `--*out` flag is added without
a probe).

Accepted costs:

- **Friction.** Single, unpiped, unchained commands only; a bare `.pdf` anywhere in a command
  string is denied. Digests already in `docs/eval/` have no marker, so regenerating one
  means deleting it first.
- The agent's view is less rich than a human's: investigations that need to *see* a
  drawing are operator tasks, so the operator built local review UIs (`review-serve`,
  campaign deck) rather than worksheets.
- **Known limits, stated in the docs:** a check-to-write race on parent directories; an
  agent can still edit `app/eval/*.py` inside the repo and run that — the defences there
  are code review, the pre-commit hook and the operator.

## Lessons

- The hole was found by reading the allowlist as an attacker would, not by an incident —
  and "a guard that has been right every time it fired" was not enough to cover its blind
  spot. The later guard-shape tightening (interpreter, setup script and cwd must be inside
  the repo) came from the same insight.
- A pre-commit hook cannot tell a COUNT keyed by a field name from a VALUE stored under it.
  The fix was to namespace keys, not to reach for `SINDRI_ALLOW_DATA_COMMIT`, which would
  make every future digest commit a bypass.
- H7 added a scanner (`app/eval/digest_scan.py`) and a test that scans every committed
  `docs/eval/*-grade.json`, so the property is checked, not trusted.
- Error messages are part of the surface: a comparability guard once printed a client part
  number (fixed 2026-08-19), and `main` used to echo client file content on an
  input-read failure.
- Related: [0006](0006-pipeline-and-eval-kept-separate.md),
  [0005](0005-campaign-only-positive-allowlist.md).
