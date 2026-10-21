# State — campaign C1 review plan

- Plan: `docs/plans/2026-10-21-campaign-c1-review-plan.md`
- Branch / worktree: `feature/review-campaign-c1` at `.worktrees/review-campaign-c1`
- Current milestone: **A — campaign mode on the server: DONE** (task 5 of 18)
- Completed tasks: 1, 2, 3, 4, 5
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

## What's next
Milestone B: Task 6 (journal vocabulary), at plan line 591.

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
