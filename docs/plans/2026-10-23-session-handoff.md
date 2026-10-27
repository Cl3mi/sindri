# Session handoff — 2026-10-23: the client review campaign is BUILT (C1 + C2); next is hardening, then the pilot

Written at the end of a long session so a fresh one can continue with no lost
context. It supplements, and does not replace, `2026-10-07-session-handoff.md`
(the precision direction's close-out: quality numbers, what shipped, what
closed, what is parked) and CLAUDE.md (hard rules, measured dead ends,
conventions). **Read in this order:** this file → CLAUDE.md (§1 hard rules first)
→ `2026-10-22-campaign-c2-grading-plan.state.md` and
`2026-10-21-campaign-c1-review-plan.state.md` (every deviation, security
decision and open item of the two builds) → `docs/deploy/vps.md`.

**One line:** two plans were executed with subagent-driven development and are
merged to local `main`, UNPUSHED — **C1** (the client reviews ~50 of our
ballooned gold drawings in a hosted app; every action is journaled server-side)
and **C2** (grade the pulled records: client-primary, gold as a cross-check, a
counts-only digest for agents, an operator-only drill-down). The next work is
(1) harden what the security reviews flagged, (2) run the real pilot,
(3) turn its findings into improvement plans.

---

## 0. Verified state (2026-10-23)

* Branch `main` at `81f164b`, **79 commits ahead of `origin/main`, nothing
  pushed.** Only one worktree exists (`/home/clemi/mci/sindri`); the C1/C2
  worktrees and branches were deleted after their merges.
* Untracked and NOT ours: `.claude/settings.json` (do not commit it); root
  fixtures `test_docs/` and `sample.pdf` (untracked; I never inspected them and
  they may be client material — do not copy them anywhere).
* Verified on merged main: `python -m pytest -q` → **2359 passed, 2 skipped,
  0 failed**; `node --test "tests/js/*.test.mjs"` → **298/298**;
  `bash ~/.claude/hooks/test-sindri-guard.sh` → **32 passed, 0 failed**.
  In a fresh `git worktree`, `tests/test_title_block.py` has 2 failures only
  because the untracked root fixtures are absent there; that is not a bug.
* `/home` is the operator's whole home directory. It hit **0 bytes free** once
  during the session (their data, not ours; freed to ~30 GB, 91% used). A full
  disk truncated a file mid-write and blocked git. **Run `df -h /home` before
  long work; stop if free space is under 50 MB.** `~/.claude.json` also had
  several stale `.claude.json.tmp.*` files that look like failed saves while the
  disk was full; that is the operator's to clean up.
* **Backup risk:** 79 commits of work exist only on this disk. Recommend the
  operator push (their choice; see §3.1).

## 1. What was built (do not rebuild; read the state files for detail)

### C1 — client review campaign (`2026-10-21-campaign-c1-review-plan.md`, merged)
* A campaign is a batch with `batch.campaign` set (migration **6**; there are now
  6 SQL migrations). Drawings are loaded from stored prediction dumps
  (`campaign_build.py`, operator tool, refuses to run under `CLAUDECODE`) by
  `python -m app.campaign.load` inside the container; the client reviews in the
  hosted app and clicks **Approve** (seals, never exports); `python -m
  app.campaign.pull` packs the review records into one plain tar for the operator.
* Journal vocabulary added: `move_target` (anchor box), `set_note`, closed
  `EDIT_REASONS`/`DELETE_REASONS` on `edit_cell`/`delete_row`
  (`app/review/journal.py`, mirrored in `app/static/js/reasons.js` — one
  vocabulary shared with HITL phase 2). All coordinates must be finite.
* `SINDRI_CAMPAIGN_ONLY=1` is a positive route allowlist (GETs too;
  `tests/campaign/test_policy.py` fails until any new `/api` route is
  classified). Export, re-extract and remove refuse campaign drawings on every
  instance.
* UI: reason popover (a digit picks a chip only outside an editor; other keys pass
  through), anchor box with handles, 3-screen-px drag dead zone (so a click is
  never graded as a correction), per-drawing note bound to the drawing it was typed
  on, "N / M approved" counter, table re-render keeps the cell being edited.
* Deploy: `docker-compose.vps.yml` (port bound to 127.0.0.1; `COMPOSE_FILE` in
  `.env` so a plain `docker compose up` cannot expose it), `deploy/Caddyfile.example`
  (TLS, basic auth, security headers), `docs/deploy/vps.md` (the operator's
  runbook). `import/` and `export/` are in `.dockerignore`/`.gitignore`.

### C2 — grading + findings report (`2026-10-22-campaign-c2-grading-{design,plan}.md`, merged)
* `app/review/grading.py::grade_session` (product code, reusable for HITL phase 3):
  per-row grades `confirmed|edited|phantom|suggestion_{confirmed,edited,deleted,left}`,
  per-FIELD edit reasons, `reviewed` flag, placement px (proposal → sealed final),
  `ungradable` = `replay_mismatch|replay_disagrees|replay_error`.
* `app/eval/campaign_records.py` (reads the pull tar in place, plain tar only),
  `app/eval/campaign_grade.py` (metrics, `gold_cross_check`, `grade_campaign`,
  `ranked_findings`, `LEVERS`, `row_buckets`), `app/eval/campaign_deck.py` +
  `campaign_deck_page.html` (the operator's drill-down).
* **`score --reviewer-grade <pull tar> --gold <the --originals gold dir> --manifest
  <share_select manifest> --name <n> --out <protected root>/reports/<n>.grade.json
  --grade-out docs/eval/<n>-grade.json`** — the agent-run surface (the guard
  allowlists `runner score`). Rules, all enforced and exploit-tested: the full
  report (client values) may only be written inside a protected root, name
  `*.grade.json`, never over an existing file unless it carries `"kind":
  "campaign-grade-report"`, never a symlink, never over the tar/manifest; the digest
  `*-grade.json` must be OUTSIDE the protected root and carries `"kind":
  "campaign-grade-digest"`; atomic `.partial` writes; fixed-sentence errors only;
  the runner-wide `_QuietParser` no longer echoes argument values; a missing salt or
  empty gold dir is refused.
* **Exit codes:** 0 clean, 3 does not reconcile (read `reconcile_problems` before
  quoting any number), 1 refused/failed (if only the digest failed, the report was
  written), 2 bad arguments. **stdout** is one line:
  `reviewer-grade: approved=N pending=N ungradable=N skipped=N not_in_records=N
  gold=<joined>/<approved> [gold_not_computed=...] reconciles=<bool>`. `gold=0/N`
  (wrong salt, or gold not the `--originals` ingest) is a reconcile problem
  (`gold_none_joined`) → exit 3.
* `runner campaign-deck --report <report> --pdfs <originals dir>`: OPERATOR-ONLY
  (not in the guard allowlist, a test pins that; agents never run it or accept its
  link). Same security as `review-serve` plus a per-response script nonce, crops
  from the ORIGINAL drawings after a 1 pt page-frame check, every client value set
  with `textContent`. Verified in a real browser on synthetic data.
* **Digest leak safety** (the digest is committed to git and read by agents): delete
  reasons mapped onto the closed vocabulary; `group` and `source_run` are
  ALLOWLISTS (great/problem + the manifest's pool/roles/runs; our `rN-…` run
  naming); campaign/error words pass a strict token check; keys are namespaced
  (`fields:upper_tol+lower_tol`) so the pre-commit hook's quoted-key patterns can
  never match.
* **How to read the numbers (documented in CLAUDE.md §2 and vps.md):** recalls are
  UPPER bounds (client additions are incomplete); review time is a LOWER bound (the
  sealed journal is compacted); `precision` includes silent confirmations
  (approval only requires flagged rows to be resolved) — quote it with
  `confirmed_explicit/silent` and the `*_explicit_only` lower bounds; pooled
  metrics are reweighted to `manifest.pool` and are `None` with a reason
  (`pooled_unmeasured`) whenever a group is unknown or missing.

## 2. Process that worked (keep doing it)

* Pipeline per plan: brainstorm (interactive questions) → design doc → plan with
  milestones → `git worktree` → subagent-driven development: a fresh implementer per
  task (sonnet for mechanical, opus for integration/security), then a SEPARATE
  reviewer (opus for security/data-integrity code) doing spec compliance first and
  quality second, fixes sent back to the implementer, a re-review, a controller
  checkpoint in `<plan>.state.md` at each milestone, a final cross-cutting review,
  then merge. Reviews found real bugs in nearly every UI/journal/security task
  (stolen keystrokes, notes saved on the wrong drawing, a delete reason leaking into
  the committed digest, `--out` able to overwrite client gold). **Do not skip them.**
* Tell every reviewer/implementer to run pytest with **absolute worktree paths** — one
  reviewer ran the main checkout and reported a wrong count.
* The controller (you) verifies independently with fresh commands before claiming
  anything; read diffs for small changes yourself.
* A real-browser check is required for DOM-bound UI (the repo has no driver: use a
  throwaway Playwright venv under the session scratchpad with synthetic data).
* If an API usage limit kills a subagent mid-task: check `git status`, stray
  processes (`pgrep -af`), and `git stash list` BEFORE re-dispatching.
* Avoid `git stash` (the stack is shared across worktrees).
* **Guard behaviour (CLAUDE.md §1):** single, unchained, unpiped commands; a bare
  `.pdf` in a command string is denied (write such text with the file tools); a
  subagent prompt may not contain the protected root's path — say "the protected
  root". Never read the protected root, `*.pred.json`, `*.gold.json`,
  `*.report.json`, pull tars or campaign reports. Operator-only: `campaign_build.py`,
  `campaign-deck`, `review-serve`, `review-tally`, `export_share.py`, the load/pull
  CLIs, `sync_client_data.sh` pulls.
* Commit trailer: **unresolved.** CLAUDE.md §4 says `Co-Authored-By: Claude Opus 5
  (1M context) <noreply@anthropic.com>`; the session's attribution reminder
  produced `Claude Opus 5.5` on the C1 commits and `Claude Sonnet 5.5` on the C2
  commits. Ask the operator which convention to use; nothing is pushed, so history
  can still be rewritten to one convention before the first push.

## 3. Next steps, in order

### 3.1 Immediately (operator decisions; ask first)
1. **Push strategy.** 79 unpushed commits (batch B1–B4, HITL phase 1, C1, C2 and
   earlier work) live on one disk that already filled once. Existing context:
   PR #2/#3 and branch `worktree-eval-harness` are in CLAUDE.md/memory; decide
   whether to push `main`, open PRs, or keep local. Resolve the trailer question
   BEFORE pushing.
2. Start the hardening below (the operator already said yes: "plan it next").

### 3.2 HARDENING — H1 — **DONE 2026-10-27** (branch `feature/runner-output-hardening`; design/plan/state in `docs/plans/2026-10-23-runner-output-hardening-*`; the text below is the original brief, kept for context. What was built, and the one open guard item, is in CLAUDE.md §2 "RUNNER OUTPUT FLAGS ARE HARDENED")
(IMPORTANT, pre-existing, found by the C2 security reviews)
**The hole:** the guard allowlists `python -m app.eval.runner
<probe|headers|ingest|split|predict|score|compare|summary|variants>`, but several of
those subcommands write to ANY path the caller gives with no suffix, symlink or
overwrite check. An agent could aim one at client gold, dumps or reports (a reviewer
proved this for C2's own flags before they were fixed):
* write-capable flags in `app/eval/runner.py` (line numbers from 2026-10-23):
  `summary --out` (1218), `ingest --out` (1230), `variants --out` (1242), `split
  --out` (1248), `predict --out` (1253), `score --out` (1275) and its
  `--policy-out` (1338) / `--gentol-out` (1349) / `--phantom-out` (1355) /
  `--drop-out` (1369), `compare --out` (1430). Not allowlisted (operator-only):
  `verify`, `review-tally`, `review-serve`, `campaign-deck`.
* `ingest`, `split` and `predict` legitimately write INSIDE the protected root (gold,
  split file, run dumps); `score --out` writes `*.report.json` there.
**Plan it like C2's `check_report_path`/`check_digest_path`** (`app/eval/review.py`):
1. Brainstorm briefly, then write design + plan (`docs/plans/2026-10-2x-runner-output-hardening-*`).
   Step 1 of the plan: enumerate EVERY output flag with its intended destination and
   classify (A: counts-only digest meant for `docs/eval/`; B: client-value file that
   must stay inside the protected root; C: directory output like `predict --out`).
2. One shared checker per class: required suffix, refuse symlinks (check before
   `resolve()`), refuse an existing file unless it carries a kind marker we write
   (reports currently have no marker — add one, keeping old reports readable;
   `RunReport` has `schema_version`), never overwrite gold/dumps/corpus (class B may
   only write under `<protected root>/reports/`; class A must be outside the
   protected root), atomic `.partial` + `os.replace`, fixed error sentences (no
   path/value/exception text), `OSError` wrapped.
3. Update CLAUDE.md §1/§2 examples and the `share-pack` skill commands
   (`summary --per-doc --out <scratchpad>/dev.json` must still work — scratchpad is
   outside the repo and the protected root, so class A needs a rule that permits it;
   decide whether class A is "anywhere not inside the protected root and not an
   existing non-marker file").
4. Tests prove each exploit is closed (an agent-style probe file per flag, like the
   C2 review's 37 probes). Re-verify the guard (32/0). Do NOT widen the guard.
5. Use opus for the implementer AND the security reviewer; re-review after fixes.

### 3.3 Further hardening candidates (smaller; do after H1 or fold into it)
* **H2 gold provenance:** `campaign_grade._same_frame` cannot tell gold ingested
  WITHOUT `--originals` from a same-size stamped sheet (positions all wrong, check
  passes). Record "ingested with --originals" in `GoldDoc.provenance` (ingest change;
  needs re-ingest) and have `score --reviewer-grade` refuse gold lacking it.
* **H3 salt:** `Anonymizer`/`ensure_salt` silently mints a salt on some paths (the
  reviewer-grade path refuses; `predict` only warns). Audit every runner path that
  hashes ids and refuse or warn consistently.
* **H4 campaign instance scope:** the campaign-only allowlist's reads (documents,
  batches, image, open) are not scoped to campaign batches, so an instance must hold
  ONE client's campaign and nothing else (documented). Consider scoping reads to
  campaign batches; `/api/read_region` (CPU OCR) has no rate limit (basic auth is
  the only guard) — consider a per-session limit.
* **H5 loader/pull robustness** (accepted minors, see C1 state): two loaders racing
  on one bundle can double-insert (no UNIQUE index on `batch.campaign`/sha);
  restarting the container DURING a load can delete a half-copied drawing (remedy:
  re-run the load; it re-queues failed drawings); parent-directory `fsync` after
  `os.replace` is not done; a later ordinary import of the same bytes shows as a
  duplicate of the campaign copy.
* **H6 review-time fidelity:** the sealed journal drops undone work and the seal is
  not a journal event, so review time is a lower bound. Consider journaling an
  `approve` marker and keeping raw event timing in the sealed revision.
* **H7 digest scanner:** add a test/pre-commit check that every committed
  `docs/eval/*-grade.json` re-passes the leak rules (kind marker, no free-text
  strings outside the closed sets).
* **H8 UI backlog:** after Enter-to-commit with the reason popover open, the first
  click on another cell only dismisses the popover (one extra click, no data lost);
  wheel-zoom during a balloon drag then release without moving places the balloon
  slightly off (pre-existing).
* **H9 C2 minors left as documented:** the `--split dev` flag explicitly passed with
  `--reviewer-grade` is not detectable (compare-to-default); weighting a per-row
  ratio by drawing counts in `reweight` is an approximation (documented).

### 3.4 Roadmap
1. **H1 hardening** (above) — small, security-relevant, already approved.
2. **Pilot campaign** (the point of C1/C2; mostly the operator's hands):
   prerequisites — confirm the client NDA permits hosting at Hetzner (EU), turn off
   Hetzner snapshot backups for the data volume, long random reviewer passwords
   (basic auth is the only guard), Docker Engine ≥ 28, Compose ≥ 2.24. Then follow
   `docs/deploy/vps.md`: an AGENT selects (`share_select --allow-train --great 30
   --problem 20`, train digest from a `score --reapply-policy` re-score of
   `r3-trainpredict`, dev digest from `r5-control-scoped`; never test; the manifest
   now records `roles` and the eligible `pool`); the OPERATOR builds
   (`campaign_build.py`), rsyncs, loads, reviews one drawing end to end, then sends
   the client the link; the operator pulls the records at any time; an AGENT runs
   `score --reviewer-grade` (same salt as the build machine; `--gold` = the
   `--originals` gold); the OPERATOR optionally runs `campaign-deck`; wipe the VPS
   data afterwards (`sudo rm -rf` + `docker builder prune -af`).
3. **Findings → improvement plan.** The digest's ranked findings map each correction
   bucket to a lever (`LEVERS` in `campaign_grade.py`, mirrors CLAUDE.md §2–3): `phantom:unballooned_real_dimension`
   → a ballooning-policy classifier learned on train gold (parked item 2 below; the
   verifier is CLOSED); `fields:char_type` → read stage (Ø Diameter↔Distance);
   tolerance edits → `render_target` fix + adapter retrain (then price under the
   active rules with `escaped_error` as the damage counter); `fields:nominal` /
   multi-field → read input / box framing; `missed` → detector weights; placement →
   whitespace placement (`fa1cb6f`, new, unmeasured) and box framing; tray outcomes →
   drop-stage-2 thresholds. Each arm: register the prediction BEFORE pricing, select on
   train, validate on dev, confirm on test (CLAUDE.md §4). Client-judged numbers from
   the pilot become a third control alongside `r5-control` / `r5-controltest`.
4. **HITL phases 2–5** (`2026-10-07-hitl-review-grading-design.md`; phase 1 done).
   Phase 2 plan exists: `2026-10-09-hitl-phase2-forcing-plan.md`, NOT executed, written
   BEFORE C1, so **update it first**: the migration count is now **6** (it says 5, adds
   none); `EDIT_REASONS`/`DELETE_REASONS` already exist in `journal.py` and
   `reasons.js` — its Tasks 1 and 15 must IMPORT them, not redefine them;
   `EVENT_TYPES` already holds `move_target`/`set_note`; the settle-before-leaving
   rules (`settleReasonPrompt` at `routeOnce`, `finish`, `openForReview`, file-close,
   pagehide) now exist and the blind panel must follow them; table re-render now keeps
   the focused cell. Phase 3 (grading + client report) can reuse `grade_session`
   and the digest design; phase 4 is dev validation on the GPU host (must pass before
   any client ships); phase 5 is bundle + reviewer gold.
5. **Still parked (operator decision 2026-10-07):** the client's real weights;
   the high-confidence phantoms (ask the client what they leave unballooned — the
   pilot's `unballooned_real_dimension` bucket and notes answer this); retraining the
   read adapter after the `render_target` fix; table rows in detection order until the
   first edit.
6. **GPU host / operations** (unchanged; see the 2026-10-07 handoff §6 and memory):
   code reaches the host only by push to `from-operator` + `git checkout --detach`;
   check no queue is running and which H100 is free; pulls are the operator's.

## 4. Facts a fresh session must not rediscover

* Key names carry end to end: `campaign.json` {campaign, group, source_run, doc_hash
  (8 lowercase hex = `Anonymizer` hash), file_name, render_scale (page px per PDF
  pt), page_rect, page_px, run_config (path-looking values stripped), policy} → the
  worker merges it into `header.campaign` → `pull` index {doc_hash, dir, group, state,
  final_revision, sealed_seq, sealed_revision} + `skipped` → `read_records` keyed by
  doc_hash (or `dir` for hashless/colliding records) → report/digest → deck.
* Grade from `sealed/r{final_revision}.json` (rows + net_events), never
  `events.jsonl`; `move_target`/`move_row` events carry only the NEW value (the old one
  comes from the proposal); pass `grade_session` the sealed session's OWN
  `proposal.json` rows; `t_ms` resets on each open of a drawing.
* The pre-commit hook blocks staged json containing quoted `"upper_tol"`,
  `"lower_tol"`, `"characteristics"`, `"position_pt"`, `"field_errors"`,
  `"raw_text"` tokens — keep aggregate keys namespaced; never use
  `SINDRI_ALLOW_DATA_COMMIT`.
* `tesseract` is a device prerequisite; local dev must set `SINDRI_DATA_DIR`;
  `tests/conftest.py` isolates data/review dirs.
* Memory: `/home/clemi/.claude/projects/-home-clemi-mci-sindri/memory/` (index
  `MEMORY.md`); the campaign entry is `sindri-client-review-campaign.md`.
