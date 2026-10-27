# Guard: tighten the sanctioned-command shape -- DONE 2026-10-27 (41 cases; see the end note)

Found by H1's branch-wide security review (2026-10-27). The Bash guard
(`~/.claude/hooks/sindri-guard.py`, outside the repo) clears a command as
"sanctioned" with `_RUNNER_RE` and `_CD_PREFIX_RE` BEFORE it scans anything, and a
sanctioned command may name the protected root. Three parts of the shape accept
paths the agent can write, which would run agent-authored code under the sanctioned
shape and bypass everything the CLI now enforces (H1) and every content scan:

1. **Interpreter path** `[\w./+-]*/python3?` — `/tmp/scratch/python -m app.eval.runner
   summary <root>/...` (an agent-written file named `python`) matches.
2. **`cd <dir> &&` prefix** (`_CD_PREFIX_RE`) accepts ANY directory — `cd /tmp/scratch &&
   python -m app.eval.runner ...` puts an agent-written `app/eval/runner.py` first on
   `sys.path` (`python -m` adds the cwd).
3. **Setup script path** `[\w./+-]*setup_client_data\.py` — any directory's file of that
   name matches, including an agent-written one.

Goal: ONLY narrow what is accepted. Never add an allow rule that did not exist.

## Proposed rules (operator to confirm the two marked decisions)

- Interpreter: bare `python` / `python3` (resolved through PATH), or exactly
  `/usr/bin/python3?` / `/usr/local/bin/python3?`. **Decision A:** also allow the repo's
  `.venv/bin/python` (absolute, under the repo root) if the operator uses a venv here;
  today `python` resolves to `/usr/bin/python`.
- `cd` prefix: after `expanduser` and `realpath`, the target must be the repo root
  (`/home/clemi/mci/sindri`) or a directory under it (its `.worktrees/*` included).
  No `~`/`$` variables survive: `$` is not in `_CHAINING` today, so refuse a target
  containing `$` explicitly. Anything else: not sanctioned, so the normal scans apply
  (and a command naming the protected root is denied as before).
- Setup script: only `setup_client_data.py`, `./setup_client_data.py`, or the absolute
  path under the repo root.
- **Decision B (test count):** the guard self-test goes from 32 to 37 cases, so
  "32 passed, 0 failed" in CLAUDE.md §1/§6 becomes "37 passed, 0 failed", or the new
  cases are added to a second script and 32/0 stays. Recommend 37/0 in one script.

## Tests (write first; they must fail before the change)

In `~/.claude/hooks/test-sindri-guard.sh`, each with `$ROOT` synthetic like the others:
DENY `/tmp/x/python -m app.eval.runner summary $ROOT/reports/r.json`;
DENY `./python -m app.eval.runner probe $ROOT`;
DENY `cd /tmp/scratch && python3 -m app.eval.runner summary $ROOT/reports/r.json`;
DENY `/tmp/x/setup_client_data.py $ROOT --incoming $ROOT/incoming`;
ALLOW `/usr/bin/python3 -m app.eval.runner probe $ROOT/pdfs`;
ALLOW `cd /home/clemi/mci/sindri/.worktrees/x && python3 -m app.eval.runner headers $ROOT/excel`
(the existing "runner w/ cd prefix" case stays); all 32 existing cases unchanged.

## Process

1. Operator approves the plan and answers A and B (editing the guard is the operator's
   control, so the agent edits it only on that explicit go-ahead).
2. Write the new test cases, run the script: the five DENY cases fail (red).
3. Change `_RUNNER_RE` / `_CD_PREFIX_RE` handling in `check_bash` only; run: all pass.
4. Run `python -m pytest -q` (a hook-parity test may exist; grep `tests/` for
   `sindri-guard`) and the H1 exploit suite (unaffected).
5. Update the "32 passed" mentions (CLAUDE.md §1, §6, handoffs) per Decision B.

## Inherent limit (say it, do not pretend to fix it)

The agent can edit `app/eval/*.py` inside the repo and then run the sanctioned shape,
so the CLI checks only constrain a MISTAKEN agent, not a malicious one. The repo-side
defences are code review of the diff, the pre-commit hook, and the operator running
anything that reads client PDFs. This plan closes the cheap bypass outside the repo.

## End note: as built (2026-10-27)

Operator approved with Decision A = yes (repo `.venv/bin/python` allowed) and B = one
script. Built exactly the three rules above, plus one the plan missed: the Bash tool's
cwd PERSISTS between calls, so a separate earlier `cd /tmp/scratch` made the bare
`python -m app.eval.runner ...` shape import from the scratch directory. The hook payload
carries `cwd`; a sanctioned command now also needs that cwd (and any `cd` target, with
`$`/backticks refused and relative targets resolved against it) inside the repo. A payload
with no `cwd` (the self-test's) keeps the old behaviour for that one check only.
Test count is 41, not 37: the plan's list had seven cases, not five, and the cwd rule
added two. Written red first (5 of the 9 failed: fake python, `./python`, cd scratch,
fake setup script, cwd scratch), green after. The 32 original cases are unchanged; the
diff of `sindri-guard.py` only adds AND-conditions and narrows two regexes (every path
the new regex accepts matched the old one). Backups of both files before the change are
in the session scratchpad.
