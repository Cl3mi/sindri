# Low-confidence suggestions in the reviewer UI — design (approved 2026-10-07)

## Why

The phantom drops (drop stage 2: `conf_below_099 + material_kind +
tight_cluster`) raised delivered precision (dev 0.419 → 0.526, test 0.536 →
0.958, measured), but they delete the rows they drop: 29 correct values per
split simply vanish. The operator wants them back in front of the reviewer, in
the existing UI, highlighted, without giving up the precision gain.

## Decisions (operator, from the mockups)

* Dropped values appear **in the existing characteristics table** and on the
  drawing, edited with the existing inline UI. There is no separate panel.
* They are **highlighted in their own colour, violet** (`--suggest`). Amber
  already means "needs review, but exported", so violet marks a different state:
  *not exported until confirmed*.
* Table: violet row tint, `◌ –` in place of a number. Drawing: dashed violet box
  and a dashed violet balloon showing `?`.
* A fourth filter pill, **"Low confidence"**, next to All / Review / OK
  (shortcut `4`).
* **Confirm** = the existing review action (the dot, or `Y` on visible rows):
  the row becomes a normal numbered balloon, and numbers re-flow in reading
  order. **Dismiss** = the existing delete. Both are undoable.
* **Unconfirmed suggestions are never exported** (Excel or ballooned PDF).
  Delivered precision therefore stays exactly as measured until a person
  confirms a value.

## 1. Pipeline (`app/pipeline/`)

* `policy_rules.split_drop_stages(chars, stages) -> (kept, dropped_by_stage)`
  returns the same `kept` as `apply_drop_stages`, which becomes a thin wrapper,
  so the shipped behaviour cannot drift.
* `ExtractionResult.suggestions: List[Characteristic] = []`. These are rows
  dropped by stage 2 **and any later stage** (a kept verifier would be stage 3).
  Rows dropped by stage 1 (`contained_duplicate`) stay deleted, because a
  duplicate of a balloon is not a suggestion.
* Suggestions keep their read values, get `pos = 0`, and are placed (balloon
  position) in a separate pass AFTER the real balloons, so they never move a
  real balloon. Their `review_reasons` gain the plain-language reason
  "low confidence — not ballooned; confirm to add".
* `extract()` order is unchanged: flags, then drop stages, then numbering of
  the kept rows only.

## 2. Measurement stays exact (`app/eval/`)

* Scoring reads `result.characteristics` only, so suggestions can never count as
  delivered, matched or false. A test pins that a dump scores identically with
  and without its `suggestions`.
* `reapply_current_code` starts from `characteristics + suggestions` (the full
  post-read set), so re-applying today's policy to a dump predicted WITH the
  tray reproduces exactly what the pipeline did, and `drop_check` controls stay
  correct on future dumps. Old dumps have no suggestions; nothing changes for
  them.

## 3. API (`app/main.py`)

* `/api/extract` returns `rows` = characteristics (`suggested: false`) plus
  suggestions (`suggested: true`), each with its id, values, box and balloon
  position.
* `/api/export` and `/api/export/pdf` **drop any row with `suggested: true`**
  server-side, even if the client sent it (defence in depth). A test pins this
  for both endpoints.

## 4. UI (`app/static/`)

* `tokens.css`: `--suggest: #a371f7`, `--suggest-bg: rgba(163,113,247,0.12)`.
* `state.js`:
  * rows carry `suggested`;
  * `renumber()` numbers non-suggested rows only;
  * a new filter `'suggested'` with its count;
  * `opConfirmSuggestions(ids)` sets `suggested=false, reviewed=true` and
    renumbers, with an exact undo.
* `table.js`: class `suggested` (violet tint), pos cell `◌ –`, and the same
  editable cells as every row. Clicking the review dot on a suggested row
  confirms it.
* `viewer.js`: marker class `suggested` (dashed violet box and balloon, label
  `?`).
* `shortcuts.js`: `4` selects the Low-confidence pill. **`Y` confirms
  suggestions only while the Low-confidence filter is active.** On All / Review
  / OK it accepts as today and leaves suggestions alone, so one keystroke on
  the default view can never export unchecked values.
* `main.js`: export sends non-suggested rows only.

## 5. Verification

* Python, TDD: pipeline split, `ExtractionResult.suggestions`, scoring
  invariance, reapply round-trip, API payload, and export filtering on both
  endpoints.
* UI: there is no JS test harness in the repo. JS logic stays in small
  functions in `state.js`, and the UI is checked in a real browser on a
  synthetic drawing. The check covers violet rows and balloons, `◌ –`, the pill
  and its count, inline edit, confirm by dot and by `Y`-in-filter, `Y` on All
  leaving suggestions untouched, undo, and export excluding unconfirmed rows.
  Screenshots go in the result note.
* A native predict run is NOT needed: suggestions change nothing the scorer
  reads. Re-scoring `r5-control` / `r5-controltest` through the new code must
  reproduce their digests exactly.

## Out of scope

The verifier's keep/close decision is its own registered arm
(`2026-10-07-verifier-registration.md`). If it ships, its drops land in the
tray automatically, because the tray takes every stage from 2 on.
