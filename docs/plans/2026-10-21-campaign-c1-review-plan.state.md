# State — campaign C1 review plan

- Plan: `docs/plans/2026-10-21-campaign-c1-review-plan.md`
- Branch / worktree: `feature/review-campaign-c1` at `.worktrees/review-campaign-c1`
- Current milestone: **B — journal vocabulary: DONE** (task 9 of 18)
- Completed tasks: 1, 2, 3, 4, 5, 6, 7, 8, 9
- Baseline before Task 1: 1968 passed, 2 skipped, **2 pre-existing failures** in
  `tests/test_title_block.py` (they need a `sample` drawing at the repo root that
  this machine does not have; they fail on main too, and are unrelated).

## What's done
- Task 1 `97eeb4a`: migration 6, nullable `batch.campaign` + index.
- Task 2 `9849fc5`: `Repo.create_campaign_document` / `campaign_document_by_sha` /
  `campaign_of`; `list_documents` selects `campaign`.
- Task 3 `186f7c0`: the worker merges `campaign.json` into `header.json["campaign"]`.
- Task 4 `9fd3929`: `_doc_view.campaign`; export (single and mass), re-extract
  and delete refuse campaign drawings (409; mass export `skip_reason: campaign`).
- Task 5 `9f923a0` + `e4fd248`: `SINDRI_CAMPAIGN_ONLY` allowlist (app/campaign/policy.py),
  middleware, `health.campaign_only`.

- Task 6 `7505317` + `1f6e492`: journal `move_target`, `set_note`, closed reasons,
  `NOTE_MAX`; **all coordinates must be finite** (review: an `Infinity` written to
  `events.jsonl` would make `/open` answer 500 forever).
- Task 7 `320dd27` + `db3746b`: replay `move_target`, `note_of` (raises on a raw
  journal that still has a retract), `mismatches` compares `target_region`; an
  API test pins an int anchor through seal (`replay_ok: True`).
- Task 8 `6aed2df`: state.js `opMoveTarget`, `opSetNote`, reasons, `state.note`
  (also reset in `clearSession`).
- Task 9 `4c40233` + `3c14483`: the parity fuzz drives anchors, notes and reasons,
  and checks that the note survives a reload.

## What's next
Milestone C: Task 10 (`reasons.js`), at plan line 1067.

## Requirements carried into the UI tasks (from reviews)
- Only pass reasons from the mirrored vocabulary. A reason outside it is
  refused server-side, which fails the flush and stalls the journal.
- `opMoveTarget` must get a normalised, finite box (x0<x1, y0<y1). `anchor.js`
  guarantees this, and the viewer must round as planned.
- The note box must clamp to 2000 characters (`maxlength` plus `slice` on blur).
  A longer note fails the whole event batch at append.
- `state.note` (the reviewer's note) is not `state.notes` (the drawing's notes
  block). Don't mix them up.
- Accepted minor: a huge integer literal in a box gives a 500 instead of a 422,
  and nothing is written.

## Deviations and decisions made during execution
- Task 2: `app/batch/staging.py` `batch_view` also returns `"campaign"`. This was
  required, because the plan's own test reads `get_batch(...)["campaign"]`. As a
  result, `/api/batches*` responses carry the key too.
- Task 5: **GET is a positive allowlist too** (review finding; the plan's version
  allowed every GET except exports). Allowed: GET/HEAD on health, documents,
  `documents/{hex}`, `image/{hex}`, batches and `batches/{hex}`; POST on auth,
  open, events, seal and read_region. `tests/campaign/test_policy.py::test_every_registered_route_is_classified`
  fails whenever a new /api route is added until someone classifies it.
  `app/campaign` is registered in `tests/review/test_product_boundary.py`.
- Accepted minors, recorded for later:
  - two loaders racing on one bundle could double-insert, which is fine with a
    single CLI loader;
  - `batch.campaign` has no UNIQUE index;
  - a later ordinary import of the same bytes shows as a duplicate of the campaign copy;
  - the campaign mass-export skip row leaves `folder` blank.
- **Task 17 must add to `docs/deploy/vps.md`:** a campaign-only instance must hold
  ONE client's campaign data and nothing else. The allowed reads (documents,
  batches, image, open) are not scoped to campaign batches. `read_region` (CPU
  OCR) has no rate limit; basic auth limits it to reviewers.
