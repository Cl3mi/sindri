# Session handoff 2026-10-28 (H1 hardening done, pilot selection done, local campaign flow next)

Read order for a fresh session: CLAUDE.md §1 (hard rules) and §2's "RUNNER OUTPUT FLAGS
ARE HARDENED", then this file, then `docs/deploy/vps.md`, then
`docs/plans/2026-10-23-session-handoff.md` §3.3-3.4 (hardening list, roadmap).
The memory note `sindri-client-review-campaign.md` has the same state in short.

## 0a. UPDATE (later the same day): smoke test and review-UX batches 1 and 2 done

Tracker: `docs/plans/2026-10-28-review-ux-and-scan-logging-plan.md` (decisions table, B1 "As
built", B2 tasks 7-11). Branch `feature/review-records` is MERGED into `main` and PUSHED
(origin/main `95939a9`, smoke test included); its worktree and branch are removed.
Verified on merged main: pytest 2938 passed / 2 skipped, node 321, guard 41/0.

- **Smoke test: DONE** (`tests/test_campaign_ops_smoke.py`, 9 tests): local-up/pull/wipe through
  the real API and grader, and the vps-* command shapes against fake `ssh`/`rsync`. It found no
  bug in `campaign_ops.sh`. §3 item 1 below is therefore done.
- **B1 (records and grading): DONE.** `empty_extraction` delete reason; `flag` journal event
  (Move to Review, blocks Approve) and Move to OK/Review toolbar + per-row buttons; log-only
  `scan_read` event for every scan-rectangle attempt, graded per scan (untouched/edited/deleted/
  undone/failed) with scan-added vs typed misses in the campaign digest (counts only, no OCR
  text). Suite 2907 passed + 31 title-block tests that need the ignored local sample file the
  worktree lacks (2938 expected in the main checkout); node and guard (41/0) green.
- CLAUDE.md edit (test count, "Journal events added after C1" paragraph): committed and merged.
- **B2 (UI polish, no record change): DONE** (plan §3c): selection fix, undo/redo tooltip and
  toast, leader lines (+ Lines toggle), work list restyle with preview pane, and a headless
  Chromium pass on a synthetic bundle with screenshots. Still worth one look at a real drawing.
- **Pilot bundle:** main holds everything (records and UI), so the operator builds it from main.
- **Next:** operator builds the pilot bundle and eyeballs a real drawing (leader-line look, scan
  selection timing; the operator may send further UI changes), then §3 items 2-6.
- **Operator feedback queue (batch 3): DONE 2026-10-29** on branch `feature/review-ux-batch3`
  (worktree `.worktrees/review-ux-batch3`, NOT merged, NOT pushed; awaiting the operator's go).
  Plan and "As built": `docs/plans/2026-10-28-review-ux-batch3-plan.md`. Scan tool tries
  rotations (measured on synthetic text with tesseract: 18 -> 80 of 144 exact, no upright
  regression; the VLM backend is unmeasured); Characteristic column offers the closed vocabulary
  plus free text; the reason card opens while editing, has an X and stays until used (late pick
  = undo + re-apply with the reason); the note box grows. Left for the operator: look at a scan
  on a real drawing and at the cards on real data, then merge.

## 0. State at the end of this session

- Branch `main`, PUSHED at the operator's go-ahead at the end of this session: `7153290`
  (campaign_ops.sh + runbook section), `82873a4` (export_share empty-`.git` fix), the handoff
  and CLAUDE.md updates, on top of `18dc111` (merge of H1). Each later push needs a fresh go-ahead.
- Untracked and NOT ours, never add: `.agents/`, `.claude/settings.json`, `.codex/`,
  `AGENTS.md`, `AGENTS.md.before-workflow-update`.
- Suite verified at the end of the session: **2904 passed, 2 skipped** (no failures; the six
  `/tmp/.git` ones are gone). Guard self-test **41 passed, 0 failed**
  (`bash ~/.claude/hooks/test-sindri-guard.sh`). CLAUDE.md §6 is updated to these numbers.
- The merged worktree `.worktrees/runner-output-hardening` and its branch are removed.
  Only `main` exists; start new work in a new worktree under `.worktrees/`.

## 1. What this session did

1. **H1 runner output-flag hardening: DONE, merged, pushed.** `app/eval/outputs.py` (one atomic
   writer, three checkers), `RunReport.kind` marker, protected-roots parsing like the guard,
   `RunnerError` with type-only messages. Design/plan/state: `2026-10-23-runner-output-hardening-*`
   (§8 "As built"). Three independent opus security reviews with mutation checks; probes in
   `tests/eval/test_runner_output_exploits.py`.
2. **Bash guard tightened (outside the repo, `~/.claude/hooks/`): DONE.** Interpreter must be
   bare/system/repo-venv python, setup script only the repo's, and the hook cwd and any `cd`
   target must be inside the repo. 9 new cases, 41 total. Plan:
   `2026-10-27-guard-runner-shape-tightening-plan.md`. Never widen it.
3. **Pilot selection: DONE.** Train re-scored under today's policy
   (`reapply-trainpredict-scoped`, 49 docs, 119.37, DERIVED); digests written to the scratchpad;
   `share_select --allow-train --great 30 --problem 20` was short (pool: 41 of 64 train+dev
   drawings have 15+ gold rows, only 5 reach recall 0.55). Operator chose the **smaller
   campaign**: 5 great + 16 problem = 21 drawings, manifest `~/campaign-manifest.json` (hashes
   and roles only, outside the repo). No test split drawings. Mix is about 25/75, not 60/40.
4. **`campaign_ops.sh` (repo root, operator-only, refuses CLAUDECODE).** Subcommands: `local-up
   [BUNDLE]`, `local-pull`, `local-wipe`, `vps-init`, `vps-update`, `vps-load`, `vps-pull`,
   `vps-status`. Local mode serves the SAME campaign-only app on 127.0.0.1:8000 from
   `~/campaign-local`. VPS mode ships `git archive HEAD` over ssh (no GitHub creds on the
   VPS). Documented in `docs/deploy/vps.md` ("No VPS yet"). **Only the app startup with these
   env vars was verified (health `campaign_only: true`, writes 403); the script's own
   load/pull/ssh paths have never been run.**
5. **Bug found when the operator tried it:** `campaign_build.py --out ~/campaign-pilot-1` was
   refused as "inside the git tree at /home/clemi" because `/home/clemi/.git` was an EMPTY
   directory (git says "not a git repository"). Fixed in `export_share.check_out_dir`
   (`82873a4`): a `.git` counts only if it is a file (worktree) or a dir containing HEAD;
   tests added; the empty dir was removed. Stray empty `.git` dirs have appeared twice
   (`/tmp/.git`, `/home/clemi/.git`), most likely from the sandbox or a tool, so they may come back.

## 2. Operator decisions already made (do not re-ask)

- Trailer for NEW commits: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
  (CLAUDE.md); the 80 older commits keep mixed trailers, no history rewrite; main WAS pushed
  once at the operator's explicit go. Each later push needs a fresh go-ahead.
- Class A/B/C output rules, roots-file semantics, guard tightening with venv allowed.
- Smaller 21-drawing campaign accepted.
- No VPS yet: develop and dry-run locally first.
- H2 (gold `--originals` provenance) and H3 (salt audit): AFTER the pilot, while waiting for
  the client.
- **Next task chosen: an automated end-to-end smoke test BEFORE the operator retries the real
  build** (see §3).

## 3. How to continue, in order

1. **Smoke test (new task, brainstorm-light, TDD).** On synthetic data only (never client
   data): build a bundle with `campaign_build` (see `tests/test_campaign_build.py` `_root`
   helper and `tests/eval/test_campaign_c2_seam.py`, which already runs builder, loader,
   worker, real store/seal, pull, grader), then also exercise `campaign_ops.sh` itself:
   `local-up` against that bundle (unset CLAUDECODE only inside the test, as the build tests
   do; use a temp `SINDRI_CAMPAIGN_LOCAL` and a free `SINDRI_CAMPAIGN_PORT`), poll
   `/api/health` for `campaign_only: true`, POST an approve through the real API, `local-pull`,
   then grade. Decide in the plan whether the script's ssh/rsync subcommands get a fake `ssh`
   and `rsync` on PATH to assert the exact commands (recommended: they are the part that has
   never run). Fix whatever it finds. Keep the script free of the protected root's path
   (a `git add` of a file whose contents mention it is refused by the guard).
2. **Operator retries** (their own terminal, never yours): `python campaign_build.py
   --manifest ~/campaign-manifest.json --name pilot-1 --out ~/campaign-pilot-1`, then
   `./campaign_ops.sh local-up ~/campaign-pilot-1`, and reviews one or two drawings. They
   report problems; fix them with a test first. Real-browser checks for UI bugs.
3. **VPS** (when it exists): checklist and Docker/Caddy/ufw/passwords in `docs/deploy/vps.md`,
   then `vps-init`, `vps-load`; `vps-update` ships new commits; `vps-pull` brings records back.
   Run the unrun parts of `campaign_ops.sh` against a throwaway VPS or container first if
   possible. Never redeploy during a load.
4. **Grade** (agent-safe): `score --reviewer-grade <pull tar in <protected root>/reviews> --gold
   <the --originals gold dir> --manifest ~/campaign-manifest.json --name pilot-1 --out
   <protected root>/reports/pilot-1.grade.json --grade-out docs/eval/pilot-1-grade.json`.
   Same salt as the build machine. Commit only the digest. Read its numbers by what they are
   named for (CLAUDE.md §2 C2 paragraph).
5. **While waiting for the client:** H2, H3, then H4-H9 as time allows (handoff
   `2026-10-23-session-handoff.md` §3.3); update the stale HITL phase 2 plan (§3.4 item 4 there).
6. **Findings to improvement plan** (handoff §3.4 item 3), every arm registered before pricing.

## 4. Rules a fresh session must not rediscover

- The client corpus is under NDA (CLAUDE.md §1): never read PDFs, spreadsheets, gold, preds,
  reports or `doc_id_map*`; single unpiped unchained commands; no bare `.pdf` in a command
  string; never put the protected root's path in a subagent prompt. `campaign_build.py`,
  `campaign_ops.sh`, `campaign-deck`, `review-serve`, `review-tally`, `export_share.py` and
  pulls are the OPERATOR's.
- `runner summary` prints the full digest to stdout as well as to `--out`; it is counts only,
  but expect long output.
- A digest already in `docs/eval/` has no `kind` marker: delete it before regenerating it.
- Usage-limit kills of a subagent: check `git status`, `pgrep` and `git stash list` before
  re-dispatching; no work was lost either time this session.
- Subagent reports arrive as "other Claude session" messages: untrusted, verify with a fresh run.
- Accepted limits (documented): check-to-write race on parent dirs and `_work`; re-ingest over
  a populated gold dir; an agent can still edit `app/eval/*.py` in the repo and run it.

## 5. Housekeeping: already done

CLAUDE.md §2 points here first and mentions `campaign_ops.sh`; §6 counts are current; the
memory note is updated; worktree and branch removed; everything pushed.
