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

The rotation search is tested against a stub reader, not the real VLM. Whether the real reader
recovers 45° text is for the operator to see on a drawing; the logged `rotation` will tell
later how often each orientation won.
