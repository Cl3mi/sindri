# Review UX round and scan logging (plan, 2026-10-28)

Source: the operator's feedback after the smoke test. Decisions below were made
interactively and are not to be re-asked. Two batches: **B1 changes what a
review record contains, so it must land before the pilot bundle is built**
(a later grader must read the pilot's records); **B2 is UI polish** and touches
no record.

## 0. Decisions

| Topic | Decision |
|---|---|
| Scan logging | Hybrid: passive (derived from what the reviewer does next) plus the existing reason chips. No new grade prompt. |
| Scan attempts logged | EVERY attempt: empty reads and scans whose row is undone count as misses. |
| Scan storage | New log-only journal event `scan_read`, kept through undo and compaction. Same transport as every event. |
| Move to Review | Persistent manual flag (new `flag` event). A manually flagged row blocks Approve until resolved, like an auto-flagged one. Move to OK = the existing `accept`. |
| Move controls | Buttons for ticked rows in the table toolbar (next to Accept all) AND an action on the selected row. No drag, no shortcuts yet. |
| Delete reason | New `empty_extraction` chip, always chosen manually (no preselect). |
| Selection after add | The new row is selected briefly; a click on empty canvas or Esc deselects; anchor handles appear only once the balloon is clicked. |
| Leader lines | Match the export (thin blue line from the balloon rim to just outside the box, `ballooned_pdf._leader_segment`'s geometry). Shown for the selected/hovered balloon; a toolbar toggle draws all. |
| Undo/redo | Tooltip names the next action ("Undo: delete balloon 5"); clicking shows a toast ("Undid: delete balloon 5", ~2 s, with a Redo link). |
| Worklist | Takes the workbench's panels/tokens, table density and row states, and a preview pane (drawing thumbnail and counts beside the list). Filters keep working as today. |
| Verification | Node tests, replay parity fuzz, and a headless-browser pass on a synthetic bundle through `campaign_ops.sh local-up`, with screenshots. |

## 1. Batch B1: records (before the pilot) -- DONE 2026-10-28, branch `feature/review-records`

Every task is TDD, one commit each. Mirrors that must move together:
`app/review/journal.py`, `app/review/replay.py`, `app/static/js/state.js`,
`app/static/js/journal.js`, `app/static/js/reasons.js`,
`app/eval/campaign_grade.py`, `app/review/grading.py`.

1. **`empty_extraction` delete reason.** Add to `DELETE_REASONS` (journal.py),
   `reasons.js` (set and label), the grader's closed words, and the tests that
   pin the mirror (`tests/review/test_journal.py`, `tests/js/reasons.test.mjs`,
   `tests/test_campaign_static.py`, the C2 seam). Additive: old records stay valid.
2. **`flag` event.** `{type:"flag", ids:[...]}` validated like `accept`; replay
   sets `needs_review = true`, appends the review reason `manual`, and
   `reviewed = false`. `state.js` gets `opFlagRows(ids)` (undo = retract, as for
   every op); `progress()` and `ReviewStore.progress()` count it (they must stay
   identical, and `tests/review/test_journal_replay_parity.py` fuzzes the JS and
   Python replay, so the fuzz is extended with `flag`). Grading: a row carries
   `manually_flagged`; the digest counts flagged-by-reviewer rows and their
   outcome (accepted unchanged / edited / deleted) as counts only.
3. **UI for moving rows.** "Move to OK" and "Move to Review" in the table
   toolbar (acts on ticked rows, like Accept all) and on the selected row. Move
   to OK on a flagged row = `accept`; Move to Review on any non-suggestion row
   = `flag` (for an accepted flagged row, `flag` also un-accepts). Disabled in
   read-only mode.
4. **`scan_read` log-only event.** `{type:"scan_read", row_id, box, text, conf,
   ms, empty}` written when `/api/read_region` returns, BEFORE the `add_row` it
   leads to, with the row id the add will use. `validate_event` checks it;
   `replay` ignores it; it is never retracted, so `net_events` keeps it and an
   undone scan survives as an event whose `row_id` has no surviving `add_row`.
   Document the exception beside the journal's "net operations only" note.
   Empty and failed reads log too (with `empty: true`, no row id needed).
   It holds OCR text, so it is client data like everything in the review dir.
5. **Scan grading.** `app/review/grading.py` derives per scan: `undone` (no
   surviving add), `deleted` (+ reason), `edited` (which fields, from the net
   `edit_cell`s on that id), `box_moved` (IoU of drawn vs final box),
   `untouched`, `empty`, plus OCR latency. The campaign grader's report gets a
   `scans` section and the digest counts only, keys namespaced like every digest
   (`fields:nominal`), never the OCR text. Manual adds in `missed` split into
   `scan_added` and `typed`. Every count reconciles to attempts (scan outcomes
   partition `scan_read` events), checked like the other digest identities.
6. **Seam test.** Extend `tests/eval/test_campaign_c2_seam.py`: one kept scan,
   one edited, one undone, one empty, plus a manual flag, driven through the
   real store, seal, pull and grader.

## 2. Batch B2: UI polish (no record change) -- DONE 2026-10-28, branch `feature/review-records`

7. **Selection after add** (`state.js` `opAddRow`, `viewer.js`): clear on empty
   canvas click and Esc; hide anchor handles until the balloon is clicked.
   Verified in the browser first (find what keeps it selected before changing it).
8. **Undo/redo labels and toast** (`main.js` `wireUndoRedo`, `state.js` ops'
   labels, small `toast.js`): the next-op label includes the balloon number.
9. **Leader lines** (`viewer.js`, new pure `leader.js` with a node test that
   pins parity with `_leader_segment`): SVG layer under the markers, redrawn on
   drag, selected/hover plus a toolbar toggle (remembered per viewer in
   `localStorage`, wrapped in try/catch).
10. **Worklist restyle** (`worklist.js`, `index.html`, styles): panels, tokens,
    density, row states and the preview pane. Use the frontend-design skill; the
    preview thumbnail reads `/api/image/{id}` (already campaign-safe, check
    `test_policy.py`).
11. **Browser pass**: synthetic bundle through `campaign_ops.sh local-up`;
    screenshots of the worklist, selection, lines, toast, and move buttons;
    fix findings with a test first.

## 3. Out of scope / rules

- No drag-between-filters, no move shortcuts, no history dropdown, no explicit
  scan grading prompt. The scan log makes a later explicit grade cheap to add.
- Never touch client data; synthetic bundles only. The new events are client data
  in real use, so nothing in a commit message, test fixture or digest may carry
  OCR text.
- Re-verify after each batch: full suite, node tests, guard self-test
  (41 cases). Update CLAUDE.md §6 counts and the handoff when a batch lands.
- Branching: merge `feature/campaign-smoke` first (operator's go), then one
  branch per batch off `main` in `.worktrees/`. Commit trailer per CLAUDE.md.

## 3b. As built (B1)

Tasks 1-6 landed as planned, one commit each, plus: `replay.mismatches` now
compares `needs_review` (a flag changes nothing else, so a replay that lost it
would still have 'reproduced' the export); `net_events` refuses a retract of a
log-only event; the digest gained `manual_flagged*`, `missed_scan|typed|via_unknown`
and a per-group `scans` section, all reconciled in `grade_campaign`. The seam test
runs a kept, an edited, an undone and a failed scan plus a flag through the real
store and seal. Suite 2938 (2907 + the 31 title-block tests, which need the ignored
local sample in the worktree), node green, guard 41/0. Mutation checks: the parity
fuzz fails if replay forgets to un-accept on `flag`, and if reload does not skip
log-only events. **B2 (UI polish) is next; the pilot bundle can be built from B1.**

## 3c. As built (B2)

Tasks 7-10 as planned: `state.js` `clearSelection()`/`anchorId`/`showAnchorFor()` (handles
only after a balloon click; a scan-added selection lets go after 2.5 s; Esc or an
empty-canvas click clears); ops carry `describe()` with their balloon number, `undoLabel()`
/`redoLabel()` feed the button tooltips and a 2 s "Undid: ..." toast with a Redo link
(`ui.toast` gained an `action`); `leader.js` ports `_leader_segment` (node test pinned to the
Python's numbers) and `viewer.js` draws a thin blue SVG layer, selected/hovered plus a "Lines"
toggle in localStorage; the work list is a two-pane layout on the workbench's `.toolbar`,
`table.inspection` and left-bar row states (`rowTone`), with a preview pane
(`previewFacts`, `/api/image/<id>`, already campaign-safe) and a short timestamp.

Task 11 was run with a scratch Playwright (installed outside the repo) driving Chromium
against the campaign-only app started with `campaign_ops.sh`'s env switches on a synthetic
3-drawing bundle (not the script itself: it refuses an agent session). Verified: 3 rows and a
loaded thumbnail; balloon click gives one handle set and one leader; Esc and empty click clear;
Lines draws all 8; delete gives "Undo: delete balloon 3", undo shows the toast and
"Redo: delete balloon 3"; a ticked row moves to Review and the tooltip reads "move 1 row
to review". Findings fixed: unstyled filter selects, raw ISO timestamps. Not changed: the
selected-row fallback for the Move buttons only follows a balloon click (a table click does
not select a row). The loader dedupes by drawing bytes, so a synthetic bundle needs distinct
PDFs per entry. The operator should still eyeball one real drawing before the pilot.
Suite 2907 passed, 2 skipped (+31 title-block tests in the main checkout), node 321.

## 4. Open questions

None blocking. To confirm when writing task 5: whether the digest should also
report scan latency percentiles (default: median and max only).
