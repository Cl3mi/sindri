# State — campaign C1 review plan

- Plan: `docs/plans/2026-10-21-campaign-c1-review-plan.md`
- Branch / worktree: `feature/review-campaign-c1` at `.worktrees/review-campaign-c1`
- Current milestone: **D — campaign deliverable: DONE** (task 18 of 18), plus the
  final cross-cutting review's fixes
- Completed tasks: 1-18
- At close: 2089 passed, 2 skipped, the same 2 pre-existing `title_block`
  failures; node 298/298; guard 32/0 (implementer's run; re-verified at merge)
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

- Task 10 `8e8c6cb` + `b5c80db`: reasons.js. Review hardening:
  - `classifyKey`: a digit picks a reason only outside an editor, other keys
    pass through, Escape is swallowed;
  - only one popover can be open;
  - it settles on hashchange, pagehide and the tab going hidden;
  - `settleReasonPrompt()`.
- Task 11 `87ba562` + `bb71d60` + `6308dd3`: edits and deletes go through the
  prompt. `await settleReasonPrompt()` runs first in routeOnce and finish(), at
  openForReview (below the same-drawing early return), in the file-close
  handler, and in pagehide (second beacon). settleReasonPrompt is awaitable,
  using in-flight tracking and allSettled.
- Task 12 `c51e0d2` + `6b0436a`: anchor.js (`dragBox`, `settleBox`, `beyondDeadZone`,
  `DRAG_DEAD_ZONE_PX`=3) and the viewer's anchor box with handles. **Both the
  anchor drag and the balloon drag now need 3 screen px of movement before
  anything is journaled**, so tremor clicks don't inflate the
  moved-balloon/anchor metrics. Lost-mouseup and read-only guards added.
- Task 13 `7cc05b7` + `40dae14` + `6f44b42`: campaign.js (`approvedCount`,
  `finishCopy`, `noteToCommit`, `NOTE_MAX`), Approve with no export (decided
  from the snapshot's drawing), the counter, the note box, and campaign-only
  chrome:
  - **Server fix:** `GET /api/documents` rows now include `campaign`. The
    plan missed this; without it, Approve mode never switched on.
  - **Note handling:** the note is bound to the drawing it was focused on, and
    commitNote runs on blur, routeOnce, finish, file-close and pagehide. A
    focused box blurs, rather than re-binding, when the drawing changes.
  - **Hidden on campaign-only:** import, bulk bar, selection column,
    pause/resume and the empty-state upload prompt; handleFile refuses drops.

- Task 14 `4439bad`: share_select's manifest gains `roles`. The pick key is `doc`,
  not `hash`.
- Task 15 `98fed0d` + `d1e5101` + `54e1703`: campaign_build.py.
  - `ScaleMismatch` refuses before writing anything; the page renders at a
    float dpi, so the scale matches exactly (1e-6).
  - Each entry is written as `.<hash>.tmp` and then `os.replace`d. Errors print
    by type only. A re-run skips a complete entry (`have`) and names an
    incomplete one.
  - The row id guard is in place.
  - In export_share: `check_out_dir` refuses --out inside the protected root or
    a git tree; it is used by both tools. `refresh_result(..., suggestions,
    strict, page_out)` also places tray suggestions. `refresh_rows` is kept as
    a wrapper.
  - `share_select --allow-train`.
- Task 16 `3c4e67e` + `925f95a`: app.campaign.load / pull.
  - Loading skips dot-dirs and requires the dir name to equal `doc_hash`
    (8 lowercase hex).
  - Page counts are checked up front, and errors print by type only.
  - The `.importing` marker covers the copy, so the startup sweep cleans up a
    killed load.
  - A row stuck in `staged` is re-queued on re-run.
  - Pull writes `<out>.partial`, then renames it. Per-drawing failures are
    skipped. It excludes `writer` and `*.tmp`, needs `proposal.json`, counts
    sealed revisions, and skips any drawing with a symlink.
- Task 17 `ca0ec4a` + `2d50a87`: docker-compose.vps.yml, Caddyfile.example and
  the docs/deploy/vps.md runbook.
  - `COMPOSE_FILE` in `.env` means a plain `docker compose up` can never
    re-expose the port.
  - `import`/`export` are in .dockerignore and .gitignore.
  - Install: `chmod 700`; wipe with `sudo rm -rf` plus `builder prune`.
  - Caddy: security headers and a 10MB body limit; Docker Engine >= 28.
- Task 18 `58dad8a` + `8d05e72`. **Real-browser check (Playwright, synthetic
  campaign): every item passed**, including the review-derived list below.
  - It found that a table re-render destroyed the cell being edited (Tab then a
    digit lost the digit; this predates the campaign work). `renderRows` now
    keeps the focused cell, its unsaved text, `_orig` and the caret.
  - CLAUDE.md §2 paragraph added.
- Final cross-cutting review fixes:
  - `c91dcdd`: failed/cancelled campaign drawings are re-queued by re-running
    the load (they cannot be removed by design).
  - `a19e892`: campaign.json gains `render_scale`, `page_rect`, `page_px`,
    `run_config` (path-looking values stripped) and `policy`. Plan decision
    row 12.
  - `2155c8f`: both operator tools refuse to run when `CLAUDECODE` is set.
  - `ebccff6`: runbook deletes the local bundle after load. The digests are
    reapply-policy train plus r5-control-scoped dev. `--allow-train` refuses
    test/all/unlabelled digests.
  - `eebdbd2`: chrome starts hidden (`health-pending`, retried every 5 s).
    `#ex-stop` is hidden.
  - `7a7f839`: pull writes `nohash-<id8>` for a missing hash and a `skipped`
    list in index.json. `pull_campaign` takes `data`.
  - `4bf8c7d`: seam test, build → load → worker → pull.
  - `6269aae`: JS↔Python vocabulary and NOTE_MAX pinned by a test.
  - `d1caf97`: /docs, /redoc and /openapi.json are denied in campaign-only mode.

## What's next
Merge (operator's choice), then **plan C2 (grading + report)**, written while the
client reviews. C2 must account for:
1. Grade from `sealed/r{final_revision}.json` (rows plus net_events), never
   from `events.jsonl`, which can hold unsealed or torn tail events.
2. `move_target` / `move_row` carry only the new value. The old one comes from
   replaying the proposal plus the earlier net events (needed for IoU and
   displacement).
3. Pixels to mm: `header.campaign.render_scale` (page px per PDF pt) and
   `page_rect`.
4. Ignore `header.pipeline` and `app_version` for campaign drawings; they
   describe the VPS. Use `header.campaign.run_config`, `policy` and
   `source_run`.
5. Review time from `t_ms` deltas within a session (`t_wall` is the client's
   clock, and `t_ms` resets on each page load).
6. "Missed" means manual `add_row` (`source: manual`, values as tesseract read
   them, possibly edited afterwards).
7. Approve requires the flagged rows to be resolved.
8. Reconcile index.json against the manifest roles. Report `skipped` drawings
   as ungradable, not as missing.
9. Rows come from mixed `source_run`s under today's reapplied policy. Split by
   source run, and treat train drawings as seen by selection.

## Backlog (accepted, not fixed)
- After Enter-to-commit with the reason popover open, the first click on
  another cell only dismisses the popover. It costs one extra click and loses
  no data.
- Wheel-zoom during a balloon drag, then release without moving, places the
  balloon slightly off. This predates the campaign work.

## Task 18 browser check must also exercise (from reviews)
- Edit a cell, press Tab and type a digit: the digit lands in the cell and no
  reason is recorded.
- Edit, then click a balloon's ×: one popover only.
- A popover near the bottom of the viewport flips up.
- Edit, then press Back: the edit is applied.
- A click on a balloon or anchor without movement journals nothing.
- Note typed on A, then Back to B with the box focused: A keeps its note, and
  B's existing note is not erased.
- Known, accepted: wheel-zooming during a balloon drag and releasing without
  moving places the balloon slightly off (pre-existing).

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
