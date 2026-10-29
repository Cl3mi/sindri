# Session handoff 2026-10-30 (H2/H3 merged; pick this up on any device)

**Read order for a fresh session:** `CLAUDE.md` §1 (NDA hard rules) and §2's "H2 + H3" and
"RUNNER OUTPUT FLAGS ARE HARDENED", then this file, then `docs/plans/2026-10-29-session-handoff.md`
(pilot inspection), `docs/plans/2026-10-28-session-handoff.md` §0a/§3 and `docs/deploy/vps.md`.
**One line:** review UX batch 3, H2 (gold provenance) and H3 (no silent salt minting) are merged on
`main`; the HITL phase 2 plan is refreshed but not started; the operator's pilot inspection and a
gold re-ingest come before anything else.

---

## 0. FIRST: this work is local to one machine until it is pushed

`main` = `5028931` (merge of H2/H3) **and is NOT pushed.** It is **17 commits ahead of
`origin/main`** (`95939a9`, measured 2026-10-30 with `git rev-list --count origin/main..main`),
which includes batch 3 (`a597d25`), the 10-29 handoff and the H2/H3/plan work.
**Nothing here exists on another device until the operator pushes** (`git push origin main`,
operator's explicit go-ahead each time). On the new device: `git clone git@github.com:Cl3mi/sindri.git`
only works AFTER that push. If the old machine is still reachable, the alternative is a bundle:
`git bundle create ~/sindri.bundle main` there, copy the file, `git clone ~/sindri.bundle sindri` here.
Do one of the two before anything below matters.

Untracked and NOT ours, never `git add`: `.agents/`, `.claude/settings.json`, `.codex/`,
`AGENTS.md`, `AGENTS.md.before-workflow-update`. Only `main` exists; no worktrees.

## 1. State

- Suite on merged main: **2963 passed, 2 skipped, 0 failed** (measured 2026-10-30 in the main
  checkout, 155 s). Node 339 (inside pytest). Guard
  (`bash ~/.claude/hooks/test-sindri-guard.sh`) **41 passed, 0 failed**.
  `tests/test_title_block.py` needs an ignored local sample file: its 2 tests fail on a
  device without it, and that is environmental, not a regression.
- **Batch 3** (scan rotation, char_type list, reason card, note box): merged earlier, plan and
  "As built" in `docs/plans/2026-10-28-review-ux-batch3-plan.md`.
- **H2** (`0877860`): `ingest` writes `provenance["originals_space"]` (outside `gold_hash`).
  `gold_cross_check` returns `gold_not_originals`; `score --reviewer-grade` refuses up front with
  `ERROR: --gold: not ingested with --originals (re-ingest it; ...)` and writes nothing.
- **H3** (`3a26c00`): `ensure_salt` no longer mints. A missing salt raises `SaltMissing`; the
  runner prints `ERROR: no doc-id salt ...` and exits 1. `SINDRI_ALLOW_NEW_SALT=1` mints
  deliberately (only on the very first machine). `predict`/`verify` (GPU container) use an
  in-memory `Anonymizer.ephemeral()`. `tests/conftest.py` sets a fixed env salt for every test.
  Known gap: `verify` does not print `predict`'s "throwaway ids" warning.
- **HITL phase 2 plan refreshed** (`fede136`): a dated REFRESH block at the top of
  `docs/plans/2026-10-09-hitl-phase2-forcing-plan.md` (R0-R7). Nothing built. Its gate: **the pilot
  must be graded first**, and the audit rate is then set from that digest's silent-confirmation
  counts. New work it adds: Tasks 9a-9c, the grader learning `blind_submit`/`mark_unsure`.
- Operator step 1 of the 10-29 handoff (inspect the pilot via `./campaign_ops.sh local-up`) has
  not been reported on. No UI problems are known.

## 2. Decisions made (do not re-ask)

- Merge H2/H3 locally: done by the operator's go (2026-10-30). **Push is a separate go-ahead.**
- Order of work: UI fixes from the operator's inspection first (each starts with a failing test,
  in a new worktree under `.worktrees/`), then everything below.
- No GPU rotation check for the campaign (`campaign_ops.sh` and the VPS run tesseract, no GPU).
- Commit trailer: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` (CLAUDE.md
  §4 wins over a harness reminder that names a different model).
- Design choices I made in H2/H3, mark as mine if questioned: refuse per gold dir up front in the
  CLI AND per drawing in the library; stale gold (no record) is refused, not grandfathered;
  salt refusal is a runner-level fixed sentence, `predict`/`verify` keep throwaway ids.

## 3. Next steps, in order

1. **Operator, own terminal:** push `main` (or make the bundle) so the work exists elsewhere.
2. **Operator: re-ingest gold with `--originals`.** Every gold dir ingested before H2 has no
   `originals_space` record and the grader will refuse it. Command shape (single, unpiped, bare;
   take the real paths from `docs/plans/2026-10-23-session-handoff.md` / `setup_client_data.py`):
   `python3 -m app.eval.runner ingest --pdfs <stamped> --excel <excel> --originals <originals> --out <gold dir>`.
   Gold hashes change only if `--originals` itself changes geometry; re-score any report that
   used the old gold. Run it BEFORE the pilot is graded.
3. **Operator: inspect the pilot** (`python campaign_build.py --manifest ~/campaign-manifest.json
   --name pilot-1 --out ~/campaign-pilot-1`, then `./campaign_ops.sh local-up ~/campaign-pilot-1`):
   a scan on a real drawing (sideways, 45 degree, upright), the reason card while editing and after
   Enter, the Characteristic list, the note box. Report problems. **Agents never run these.**
4. VPS pilot per `docs/deploy/vps.md`, pull, then an agent runs `score --reviewer-grade <pull tar>
   --gold <originals gold dir> --manifest ... --name ... --out <root>/reports/<name>.grade.json
   --grade-out docs/eval/<name>-grade.json` and commits only the digest.
5. Findings to an improvement plan (each arm registered before pricing; CLAUDE.md §4).
6. **HITL phase 2**: re-check the plan's R1-R9 against `main` of that day, set the audit rate from
   the digest, then build (design `2026-10-07-hitl-review-grading-design.md`).
7. Parked, unchanged: client's real weights; high-confidence phantoms (ask the client what they
   leave unballooned); retraining the read adapter after the `render_target` fix; table rows in
   detection order until the first edit; hardening H4-H9 (`2026-10-23-session-handoff.md` §3.3).

## 4. PICKING THIS UP ON A NEW DEVICE (checklist)

The repo is self-contained for code. These live OUTSIDE it, and several fail silently. Full
rationale: `docs/plans/2026-09-17-session-handoff.md` §1.

1. Clone (after the push or from the bundle) and **`./install-hooks.sh` before your first commit**.
2. `~/.claude/sindri-protected-paths`: one absolute client-data root per line.
3. `~/.claude/hooks/sindri-guard.py` and `test-sindri-guard.sh`, copied from the old machine;
   verify **41 passed, 0 failed**. Do this BEFORE the corpus arrives.
4. **`~/.claude/sindri-doc-salt`, copied by hand, never committed.** NEW since H3: without it,
   every runner command that hashes ids now stops with `ERROR: no doc-id salt` instead of
   silently minting a different one. That error on a fresh device means "copy the salt", not
   "set SINDRI_ALLOW_NEW_SALT". Set that only if this really is the first machine ever.
5. `tesseract` binary (system package). Without it six tests fail as `TesseractNotFoundError`.
6. Python env as in `requirements*.txt`, plus `node` for the JS tests (they skip without it).
7. Local dev of the app: set `SINDRI_DATA_DIR` to a scratch dir (the `/data` default is not
   writable); campaign-only runs add `SINDRI_CAMPAIGN_ONLY=1 SINDRI_RETENTION_DAYS=0
   SINDRI_MIN_FREE_MB=0 OCR_BACKEND=tesseract`.
8. The client corpus (`~/sindri-client-data`) is never in git: `setup_client_data.py` builds the
   layout, `sync_client_data.sh` (operator-only) moves data. `~/campaign-manifest.json`,
   `~/campaign-pilot-1` and `~/client-share-manifest.json` are operator files outside the repo:
   copy them if you want to continue the pilot on the new device.
9. GPU host: ssh alias `4mehpc4_3`; code arrives by push to `from-operator` + detached checkout
   (CLAUDE.md §5). Not needed for the campaign.
10. Agent memory is per machine (`~/.claude/projects/.../memory/`) and is NOT in the repo; this
    handoff and CLAUDE.md are the durable carriers. Nothing in memory is required to continue.
11. Verify on arrival, in this order: `python -m pytest -q`,
    `bash ~/.claude/hooks/test-sindri-guard.sh`, then
    `python3 -m app.eval.runner summary <a report>` printing hashed ids (proves the salt).

## 5. Things a fresh session should not rediscover

- NDA rules (CLAUDE.md §1): never run `campaign_build.py`, `campaign_ops.sh`, `review-serve`,
  `campaign-deck`; never read client data; single unpiped unchained commands; **no `.pdf` or
  `.gold.json` text anywhere in a Bash command string, including heredocs and Python `-c`**
  (this session lost several calls to it: use Edit/Write for files whose content names them).
- The guard also refuses `git add <file whose CONTENTS mention the protected root>` chained with
  other commands (CLAUDE.md itself and plan docs do): `git add` such a file in its own call.
- A background `pytest` over the whole suite takes ~150 s; run it with `run_in_background`.
- Browser pass recipe, `tests/test_title_block.py` caveat, Alt+digit caveat, favicon 404:
  unchanged, see `docs/plans/2026-10-29-session-handoff.md` §5.
- `store.create` replaces a session only while it has no events and no seal; relevant to the
  phase 2 sampling seed (plan R2).
