# State — campaign C2 grading plan

- Plan: `docs/plans/2026-10-22-campaign-c2-grading-plan.md`; design: `...-design.md`
- Branch / worktree: `feature/review-campaign-c2` at `.worktrees/review-campaign-c2`
- Current milestone: **A — pure grader: DONE** (task 3 of 12)
- Completed tasks: 1, 2, 3
- Baseline before Task 1: 2089 passed, 2 skipped, **2 pre-existing failures** in
  `tests/test_title_block.py` (this worktree lacks the untracked root sample drawing;
  they pass in the main checkout). tests/review at close of A: 136 passed.

## What's done
- Task 1 `a32a861` + `4eb9e26`: `app/review/grading.py::grade_session`. Per-row grades
  `confirmed|edited|phantom|suggestion_*`, per-FIELD edit reasons (a later unexplained
  edit of a field clears its reason), `reviewed` flag, `missed` (client-added rows).
- Task 2 `b6f0d65` + `dd91477`: `iou`, `_placement` (px, proposal -> sealed final).
  `ANCHOR_MOVED_PX = 0.15` (above the UI's 0.1 rounding). **Placement is None/False for
  deleted rows and for suggestions the client never confirmed.** (For the record, an
  anchor [10,10,50,30] -> [20,10,60,30] has IoU 0.6.)
- Task 3 `d09f6fa` + `2bc40eb`: `ungradable` = `replay_mismatch` (stored by the seal) |
  `replay_disagrees` | `replay_error`; malformed seals never raise; rows are still listed
  one per proposal row; `missed` is meaningless when ungradable.

## Record shape Tasks 5-7 consume
`rows[i]`: id, flagged (proposal needs_review), suggested, reviewed, kind, fields, reason,
grade, bubble_px, anchor_iou, anchor_moved. `missed`: client-added final rows.

## Requirements carried into Milestone B (from reviews)
- **Task 7 must pass `grade_session` the sealed session's OWN `proposal.json` rows** (the
  list in the pull tar, incl. suggestions) — anything else flags every drawing
  `replay_disagrees`.
- **Task 7 must wrap `grade_session` per drawing** (`except Exception`) and report the
  drawing by exception TYPE only (`grade_error`), counted as ungradable: a corrupt
  proposal is not handled inside grade_session, and one bad drawing must not crash the run.
- **Task 5 `group_metrics`:** placement counts only rows with `bubble_px is not None`
  (already None for unconfirmed suggestions). Treat `anchor_moved False` with
  `anchor_iou None` as unmeasurable. `recall_lower_bound` stays balloons-only
  (confirmed+edited vs missed); ALSO add `recall_with_tray` counting confirmed/edited
  suggestions as found values (the tray rows are values the pipeline found but did not ship).
  Flag precision counts value grades only.
- Reviewers must run pytest with the WORKTREE path
  (`python -m pytest -q /home/clemi/mci/sindri/.worktrees/review-campaign-c2/tests/...`):
  one reviewer ran the main checkout and reported a wrong count.

## What's next
Milestone B: Task 4 (`campaign_fixtures` + `read_records`), plan line 350.

## Open question for the operator
Commit trailer: CLAUDE.md §4 says `Claude Opus 5 (1M context)`; this session's reminder says
`Claude Sonnet 5.5` (model-accurate, used so far). Nothing is pushed, so it can be amended.
