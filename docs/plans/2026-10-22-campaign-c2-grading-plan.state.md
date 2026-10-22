# State — campaign C2 grading plan

- Plan: `docs/plans/2026-10-22-campaign-c2-grading-plan.md`; design: `...-design.md`
- Branch / worktree: `feature/review-campaign-c2` at `.worktrees/review-campaign-c2`
- Current milestone: **D — seam + docs: DONE** (task 12 of 12; plan complete)
- Completed tasks: 1-12 (full suite 2339 passed, 2 skipped, 2 pre-existing title_block
  failures; node 298; guard 32/0)
- Baseline before Task 1: 2089 passed, 2 skipped, **2 pre-existing failures** in
  `tests/test_title_block.py` (this worktree lacks the untracked root sample drawing;
  they pass in the main checkout). At close of B: tests/eval + tests/review 912 passed;
  guard 32/0.

## What's done
**Milestone A (pure grader, `app/review/grading.py`)**
- Task 1 `a32a861` + `4eb9e26`: `grade_session`; grades `confirmed|edited|phantom|suggestion_*`;
  per-FIELD edit reasons (a later unexplained edit of a field clears its reason); `reviewed`
  flag; `missed` = client-added rows.
- Task 2 `b6f0d65` + `dd91477`: `iou`, `_placement` (px, proposal -> sealed final);
  `ANCHOR_MOVED_PX = 0.15`; None/False for deleted rows and unconfirmed suggestions.
- Task 3 `d09f6fa` + `2bc40eb`: `ungradable` = `replay_mismatch|replay_disagrees|replay_error`;
  malformed seals never raise.

**Milestone B (`app/eval/`)**
- Task 4 `390a4b2` + `f9f35d3`: `campaign_records.read_records` (tar read in place, plain tar
  only, torn bytes tolerated, `DrawingRecord.error` words, `revision_mismatch`); fixtures in
  `tests/eval/campaign_fixtures.py` mirror the REAL pull layout and a seam test runs the real
  `pull_campaign`.
- Task 5 `1dad05f` + `9a8bfab`: metrics, named for what they measure: `recall_*_vs_client_known`
  (upper bounds), `review_minutes_lower_bound_median` (compacted journal drops undone work),
  `confirmed_explicit/silent` split, `anchor_measured/unmeasurable`, `moved_bubble_mm_*`.
- Task 6 `6c07df5` + `dc41d4a`: `gold_cross_check` (verified equal to the scorer's taxonomy on
  300 random docs); refuses (computed False + word) on frame mismatch (all four coords),
  duplicate gold balloons, duplicate ids, unsupported params; never changes a grade.
- Task 7 `f63f900` + `18d0dc5` + `9a8be63`: `grade_campaign`, `ranked_findings`, `LEVERS`
  (exact single-field levers before prefix levers). **Digest leak safety:** delete reasons
  mapped onto the closed vocabulary; group and source_run are ALLOWLISTS (great/problem + the
  manifest's pool/roles/runs; our `rN-...` run naming); campaign/error words pass `_token`;
  record keys pass `_safe_key`. Reconciliation checks can fail (group_drawings, gold_coverage,
  cross_matrix, cross_gold, skipped_and_recorded, duplicate_doc_hash). Pooled metrics say why
  they are unmeasured (`pooled_unmeasured`). Malformed headers/groups/manifests are reported by
  fixed word, never raised (except `ValueError("manifest: bad roles/pool")`).
- Task 8 `cd045cd`: `share_select.eligible_counts`; the manifest carries `pool`.
- Task 9 `f1bff94` + `634a19e` + `05044be`: `score --reviewer-grade <tar> --manifest
  --grade-out`. **Security (proven by probe tests):** the full report may only be written inside
  a protected root, name `*.grade.json`, never over an existing file unless it carries
  `"kind": "campaign-grade-report"`, never a symlink, never the input tar or manifest; the
  digest `*-grade.json` must be OUTSIDE the protected root and carries
  `"kind": "campaign-grade-digest"`; atomic `.partial` writes; fixed-sentence errors only (no
  path/value/exception text); argparse errors no longer echo argument values (runner-wide
  `_QuietParser`); missing salt / empty gold dir refused; PROBLEMS exits 3; flags that would be
  silently ignored are refused. Operator: `--gold` MUST be the `--originals` gold directory.

**Milestone C (operator drill-down)**
- Task 10 `0064d6c`: `campaign_grade.row_buckets` is the ONE bucket definition used by
  `ranked_findings` and `campaign_deck.examples` (a consistency test pins every bucket's example
  total to the digest's count). `examples` returns `{"buckets": {b: {"total","examples"}},
  "order": [...]}`; reasons are closed-vocabulary words only.
- Task 11 `9de13fe` + `33cbee4`: `runner campaign-deck --report --pdfs [--port]`, OPERATOR-ONLY
  (the guard regex does not match it; a test pins that; it is not to be added). Same security
  as review-serve (127.0.0.1, Host check, per-session token, no-store, no-referrer, no logging)
  plus: startup refusals with fixed sentences (symlink, outside protected root, kind marker,
  `--pdfs` dir, persistent salt); drawings resolved by hashing PDF stems; crops from the ORIGINAL
  drawings after a 1 pt page-frame check (report drawings now carry `page_rect`; report only);
  `/crop?doc=&bucket=&i=` validated against the examples shown; per-response script nonce CSP
  (no `unsafe-inline` for script); every client value set with textContent/setAttribute; PyMuPDF
  calls behind a lock; fixed one-line errors. **Verified in a real browser on synthetic data:**
  hostile `<img onerror>`/`</script>`/U+202E payloads inert, every crop loads, no Referer, no CSP
  errors from the page's own script. An old report (no `page_rect`) shows placeholders: re-run
  `score --reviewer-grade`.

## Record shape later tasks consume
Report (protected-root only): `{"kind": "campaign-grade-report", "campaign", "drawings": [per
drawing: doc_hash(key), group, source_run, status, reason, rows, missed, scale, time_ms,
proposal, final, cross, note], "digest"}`; examples for the deck come from `rows[i]`
(id, grade, fields, reason, bubble_px, anchor_iou, anchor_moved), `proposal`, `final`, `cross`.

## Requirements carried into Milestone C (the operator deck)
- `runner campaign-deck` is OPERATOR-ONLY: it reads client values. It must NOT be added to the
  guard allowlist, and an agent never runs it or accepts its link (CLAUDE.md §1, like
  review-serve). Reuse review_server's pattern exactly (127.0.0.1, per-session token on every
  request, no-store, no-referrer, no request logging).
- The report is the input; refuse a report path outside a protected root and one that is not a
  `campaign-grade-report` (kind marker).
- Crops come from the ORIGINAL drawings (`--pdfs`), converted with each drawing's `scale` and
  the original page origin; refuse on frame mismatch like the cross-check.
- Never write the report/crops anywhere outside the protected root; the deck holds no files.
- Fixed sentences on every error path (nothing agents read may carry a path or value).
- Reviewers must run pytest with WORKTREE paths.

## Open items for the operator
1. **Pre-existing, IMPORTANT (found by the Task 9 security review, out of C2 scope):** plain
   `runner score --out`, and `--policy-out/--gentol-out/--phantom-out/--drop-out`, write to ANY
   agent-chosen path with no suffix/symlink/marker check, and the guard allowlists `runner score`.
   An agent could aim them at client gold or dumps. Same fix pattern as C2's
   `check_report_path` (suffix, no symlink, refuse existing file without a marker). Also
   `review-tally --out` only checks "inside a protected root". Suggest a separate hardening task.
2. Commit trailer: CLAUDE.md §4 says `Claude Opus 5 (1M context)`; this session's reminder says
   `Claude Sonnet 5.5` (model-accurate, used so far on C2; C1 used `Claude Opus 5.5`). Nothing
   is pushed, so all can be amended.
3. `/home` hit 0 bytes free during Task 7 (user's disk, not ours); freed to ~19 GB.

**Milestone D**
- Task 12 `c305b9e`: `tests/eval/test_campaign_c2_seam.py` runs the REAL chain (build_entry →
  load_bundle → Worker.run_once → ReviewStore append/seal as /seal does → mark_finished →
  pull_campaign → read_records → grade_campaign and `runner score --reviewer-grade` →
  campaign_deck.examples). Found no seam drift: header.campaign carries render_scale/page_rect
  after a real load, events keep t_ms, the gold cross-check computes. CLAUDE.md §1 (campaign-deck
  operator-only) + §2 C2 paragraph; vps.md grading + optional deck steps.

## What's next
The final cross-cutting review and the merge choice. Open item 1 above (plain `score` output
hardening) stays open as its own task.
