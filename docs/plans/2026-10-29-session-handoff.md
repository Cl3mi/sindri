# Session handoff 2026-10-29 (review UX batch 3 merged; operator inspects the pilot)

Read order for a fresh session: CLAUDE.md §1 (hard rules, NDA corpus) and §2's "RUNNER OUTPUT FLAGS
ARE HARDENED", then this file, then `docs/plans/2026-10-28-session-handoff.md` (§0a, §3) and
`docs/deploy/vps.md`. Memory note: `sindri-client-review-campaign.md`.

## 1. State

- `main` = `a597d25`, **local only, NOT pushed**. Each push needs the operator's explicit go-ahead.
  Untracked and NOT ours, never add: `.agents/`, `.claude/settings.json`, `.codex/`, `AGENTS.md`,
  `AGENTS.md.before-workflow-update`. Only `main` exists; no worktrees.
- Verified on merged main: pytest **2954 passed, 2 skipped**; node **339**; guard
  (`bash ~/.claude/hooks/test-sindri-guard.sh`) **41 passed, 0 failed**.
- **Batch 3 (operator feedback queue) is merged.** Plan and "As built":
  `docs/plans/2026-10-28-review-ux-batch3-plan.md`. In short: the scan tool searches orientations
  (`app/pipeline/rotation_read.py`); the Characteristic column offers the closed char_type list
  plus free text (`chartypes.js`, `typemenu.js`); the reason card opens while editing, has an X and
  stays until used, a late pick swapping the still-top op via `state.replaceTop` (`reasons.js`);
  the drawing-note box grows (`autosize.js`).

## 2. Decisions made this session (do not re-ask)

- Next step is **merge, then the operator inspects** (done: merged, not pushed).
- While the operator inspects and the client has not answered, the agent works on **H2, H3 and the
  refresh of the stale HITL phase 2 plan** (in this order; UI fixes from the operator's inspection
  always come first).
- **No GPU rotation check for the campaign.** `campaign_ops.sh` and the VPS run with
  `OCR_BACKEND=tesseract` and no GPU, so the scan tool's reader on the client's side IS the
  tesseract backend that batch 3 measured. A VLM measurement of rotated text is only relevant if the
  product later serves scans from a GPU host; it is parked, not planned.
- Commit trailer for new commits: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
  (CLAUDE.md wins over the harness reminder's different trailer).

## 3. Next steps, in order

1. **Operator (own terminal, never the agent's):** rebuild the bundle from main
   (`python campaign_build.py --manifest ~/campaign-manifest.json --name pilot-1 --out
   ~/campaign-pilot-1`), `./campaign_ops.sh local-up ~/campaign-pilot-1`, and look at:
   a scan on a real drawing (a sideways or 45 degree value, and an ordinary upright one),
   the reason card while editing and after Enter, the Characteristic list, the note box.
   They report problems; **each fix starts with a failing test** and goes in a new worktree under
   `.worktrees/`. Real-browser check for UI (see §5).
2. **H2: gold `--originals` provenance.** Make it impossible to grade against gold ingested from
   the stamped drawings (the frame mismatch the grader already refuses by fixed word is the symptom;
   H2 is about recording where a gold dir came from). Spec: handoff `2026-10-23-session-handoff.md`
   §3.3. Brainstorm lightly, TDD.
3. **H3: doc-id salt audit.** A different `~/.claude/sindri-doc-salt` silently stops every hashed id
   in `docs/eval/` joining; find where that can happen unnoticed and make it loud. Same handoff §3.3.
4. **Refresh the HITL phase 2 plan** (forcing + audit; handoff `2026-10-23-session-handoff.md`
   §3.4 item 4): it predates batch B1-B4, the campaign, review records and this batch. Bring it up
   to date so the direction after the pilot is executable; do not start building it.
5. Later, unchanged from the 10-28 handoff: VPS (`docs/deploy/vps.md`, `vps-init|load|update|pull`),
   grade the pull (`score --reviewer-grade ...`, commit only the digest), then findings to an
   improvement plan with every arm registered before pricing.

## 4. Measured, and what is not

- Scan rotation, real tesseract on SYNTHETIC text only (12 strings x 6 printed angles x 2 sizes):
  old upright-only path 18/144 exact reads, search 80/144, rotated orientations 10-14/24 each from
  0, **no upright regression** (upright 18/18). The first version DID regress one upright read
  (noise beat a correct low-confidence read on "parsed nominal + confidence"); `_plausibility`
  fixed it. **Lesson: that score cannot tell noise from a value; measure a search/ensemble change on
  a grid with real OCR, a stub-only test would have passed.** 30 degree text is not searched.
- Not measured: a scan on a REAL drawing (the operator's step 1), and anything with the VLM reader.

## 5. Things a fresh session should not rediscover

- The NDA rules in CLAUDE.md §1 are unchanged. Never run `campaign_build.py`, `campaign_ops.sh`,
  `review-serve`, `campaign-deck`; never read client data; test rotation and UI only on synthetic
  data; single unpiped unchained commands; no bare `.pdf` in a command string.
- **Browser pass recipe** (the scripts lived in an ephemeral scratchpad and are gone): build a
  3-drawing synthetic bundle from `tests/fixtures/sample.pdf` (distinct bytes per entry; the loader
  dedupes by hash) with `campaign_build.build_entry`, load it with `python -m app.campaign.load`
  into a scratch `SINDRI_DATA_DIR`, serve `app.main:app` with `SINDRI_CAMPAIGN_ONLY=1
  SINDRI_RETENTION_DAYS=0 SINDRI_MIN_FREE_MB=0 OCR_BACKEND=tesseract`, drive Chromium (the
  cached `~/.cache/ms-playwright/chromium-*` with a Playwright installed OUTSIDE the repo). The
  fixture page is LANDSCAPE (842 x 595 pt; the page image is 3508 x 2481), and the work list's
  first row is not necessarily DOC-1: pick the row by text. Capture the journal by listening to
  POSTs to `/events`. The headless run found nothing the unit tests missed except my own wrong
  geometry, but it is how the late-pick journal shape was confirmed end to end.
- The fixture's real tesseract confidence for some reads is exactly 0.0; do not treat 0.0 as "no
  read".
- `tests/test_title_block.py` needs an ignored local sample file: 2 tests fail in any worktree
  that lacks it, and all pass in the main checkout.
- Alt+digit may be reserved by the browser for tab switching; clicking a reason chip always works.
- The harness's "Failed to load resource 404" in the browser console is the favicon, not a bug.
