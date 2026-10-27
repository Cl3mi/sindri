# State — runner output hardening (H1)

- Plan: `docs/plans/2026-10-23-runner-output-hardening-plan.md`; design: `...-design.md` (§8 = as built)
- Branch / worktree: `feature/runner-output-hardening` at `.worktrees/runner-output-hardening`
- Commit trailer (operator decision 2026-10-23): `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Push: operator said keep local; nothing is pushed.
- Current milestone: **C — probes, reviews, docs: DONE; awaiting operator decision on merge**

## Verified (controller, fresh runs, 2026-10-27)
- `python -m pytest <worktree>/tests -q`: 2894 passed, 2 skipped, 8 failed = 2 `test_title_block`
  (untracked root fixtures absent in a worktree) + 6 caused by an empty `/tmp/.git`
  (`test_share_select` x2, `test_campaign_build` x4; they fail identically on untouched main;
  `rmdir /tmp/.git` fixes them — it is the operator's directory, not deleted).
- node 298/298; guard 32/0 at the time (tightened afterwards: 41/0, see the guard plan).

## Milestones
- A (outputs.py, RunReport.kind) + review round 1 (roots-file comment parsing blocker, parent
  re-check, NUL, avoid-in-dir, .git/.claude, 0600).
- B (wiring all flags; `_work`, st_nlink, one writer) + Task 7 exploit probes + review round 2
  (deck + C2 checkers, input-parse leak in `main`, empty ingest, env override fail-closed,
  orphan out-flags).
- Round 3 re-review: last-resort catch (`RunnerError`), tally writer, grade digest .git rule,
  ProtectedRootsError surfaced. Mutation checks by the reviewer showed each fix is test-covered.

## Open / operator
- Guard `_RUNNER_RE` accepts a spoofed `python` path and a `cd X &&` prefix (bypasses CLI checks):
  tighten, never widen. NOT touched.
- Accepted: check-to-write race on parent dirs and `_work`; re-ingest over a populated gold dir.
- Merge to main, trailer convention for the 80+ unpushed commits, push strategy: operator.
