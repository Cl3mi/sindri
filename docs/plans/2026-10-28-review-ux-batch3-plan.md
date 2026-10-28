# Review UX batch 3 — operator feedback queue (2026-10-28)

Branch `feature/review-ux-batch3` (worktree `.worktrees/review-ux-batch3`). Source: the
operator's own inspection of the pilot bundle (handoff `2026-10-28-session-handoff.md` §0a).
Follows `2026-10-28-review-ux-and-scan-logging-plan.md`. TDD, one commit per item. Synthetic
data only; no client data, no `campaign_build.py` / `campaign_ops.sh` / `review-serve`.

## Decisions (agreed with the operator before any code)

| # | Decision |
|---|----------|
| 1 | Scan tool reads orientations in stages (0; then ±90; then 180, ±45, ±135) and stops at the first stage that reads well (nominal parsed, conf ≥ `LOW_CONF`), so an upright scan still costs ONE read. A rotated read replaces the upright one only if it beats it by `ROTATION_MARGIN` (0.15). The winning angle rides beside the row as `scan_rotation` and is logged in `scan_read.rotation` (optional, log-only, replay ignores it). |
| 2 | Characteristic column: input + `<datalist>` of the closed char_type vocabulary; free text still allowed; same commit path as every other cell, so no journal change. |
| 3 | Reason card (operator: "appears after the edit", "no explicit close", "stay until picked or X"): opens when the editor gets focus (or a delete starts), has a visible X, is not dismissed by typing/Tab/clicks elsewhere. Click or Alt+1..n picks (plain digits stay digits inside an editor). Commit applies the op at once with the reason picked so far; the card stays for a late pick, which re-journals the same edit with the reason. Fallback if net_events/replay cannot carry that: keep apply-on-resolution, card opens at edit start. |
| 4 | Note input becomes an auto-growing textarea (max height, manual resize). |

## Tasks

1. **Scan rotation** — `app/pipeline/rotation_read.py`, `/api/read_region`, journal `rotation`,
   `scanlog.js`, `viewer.js`. Tests: `tests/test_rotation_read.py` (stub reader that only reads
   upright glyphs, synthetic PIL text at 0/±45/±90/180), `test_api.py`, `test_journal.py`,
   `tests/js/scanlog.test.mjs`.
2. **Characteristic combobox.**
3. **Reason card rework.**
4. **Growing note textarea.**
5. Verify: pytest, `node --test tests/js/*.test.mjs`, headless Chromium pass on a synthetic
   bundle, guard self-test (41/0). Update CLAUDE.md counts and the handoff. No push.

## Not measured

Written before building; superseded by "As built" below, which has the tesseract measurement.
What is still unmeasured: the production VLM reader on rotated text, and a scan on a real
drawing. The logged `rotation` will say later how often each orientation wins.

## As built (2026-10-29)

All four items shipped on `feature/review-ux-batch3`, one commit each (item 1 has a follow-up).
Pytest 2952 passed / 2 skipped / 2 failed (the two `test_title_block.py` tests that need the
ignored local sample file; 2954 expected in the main checkout), node 339, guard 41/0.

1. **Scan rotation.** `app/pipeline/rotation_read.py`; `/api/read_region` returns `scan_rotation`
   beside the row (not a Characteristic field; `viewer.js` strips it before the journaled add);
   `scan_read.rotation` is optional, in [-180, 180]. **Measured, real tesseract on synthetic text
   only** (12 strings x 6 printed angles x 2 sizes = 144 reads; harness in the session scratchpad,
   not committed): old upright-only path 18/144 exact, search 80/144; rotated orientations went
   from 0/24 to 10-14/24 each. **The first version regressed one upright read** (`'120 +0.3 0'`
   read correctly, replaced by a -45 degree read `'2 X 0 90'` at higher confidence, because a
   parsed nominal plus confidence cannot tell noise from a value). `_plausibility` (digit density
   x token length) fixed it: 0 regressions, upright 18/18. 30-degree text is not searched and
   mostly fails. **Not measured: the production VLM backend** on rotated text; tesseract is what
   was available. The extra reads only run when the upright read is poor, so an upright scan
   costs one call. The logged `rotation` will say how often each orientation wins on real scans.
2. **Characteristic suggestions.** `chartypes.js` (vocabulary + `suggest` + `moveHighlight`),
   `typemenu.js` (DOM). The cell stays a contentEditable td. The vocabulary is pinned to what
   `parser.py` can emit by `test_campaign_static.py`. No journal change.
3. **Reason card.** `reasons.js` is rewritten around `createReasonSession` (pure) and
   `openReason` (DOM). The op is applied at commit and never held for the card; a late pick
   swaps the still-top op via `state.replaceTop` (undo + apply), so the journal nets to the one
   explained edit (pinned by `test_a_late_reason_pick_nets_to_the_single_explained_edit`); a
   pick after anything else was done is refused. The card opens on the first real change in the
   cell (at once for a delete), has an X, and closes on a pick, X, a different op, or navigation.
   **Consciously changed:** typing, Tab and clicks no longer dismiss it; plain digits pick only
   outside an editor, Alt+digit picks anywhere (Alt+digit may be taken by the browser for tab
   switching on some platforms: clicking works everywhere); `settleReasonPrompt` now only closes
   the card. Esc inside an editor still reverts the edit.
4. **Note box.** `autosize.js`: grows with its text up to 160 px, then scrolls; refit after
   programmatic value changes and unhide.

**Headless Chromium pass** (synthetic 3-drawing bundle, campaign-only app with the same env as
`campaign_ops.sh`, which an agent may not run): 15 menu options on focus, "ci" narrows to 3, a
pick commits; the card appears while typing, a chip click keeps cell focus, Enter then applies
with the reason (no retract); an unexplained commit leaves the card up through keys and clicks,
X closes it; a late pick journals edit, retract, edit-with-reason; a delete applies at once and
a late pick journals delete, retract, delete-with-reason; one Undo restores the row; the note
box grew 42 -> 75 -> 160 px (scrolling) and shrank back; a scan on a busy region logged
`scan_read` with `rotation`. Not verified in a browser: a scan on a real drawing, and the
look of the cards on real data: the operator should eyeball both.
