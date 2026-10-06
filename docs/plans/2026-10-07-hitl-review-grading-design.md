# Human-in-the-loop: review logging, grading and cognitive forcing — design

Status: DESIGN, approved section by section with the operator on 2026-10-07.
Starts from `2026-10-07-session-handoff.md` §5. Controls for every gold-based
comparison: `r5-control` (dev) and `r5-controltest` (test).

## 0. Goal and deployment

The client runs Sindri **in-house**: an inspector uploads a drawing, the
pipeline extracts and balloons it, the inspector reviews until it is ready, then
exports. Where it is hosted is not decided, so nothing here may assume a
particular host.

This design adds three things, built in order and specified together:

* **A. Capture** — what the system proposed, what the inspector did, and the
  sealed final, stored durably.
* **B. Grading** — a per-row grade of the system's proposal against the
  inspector's actions, aggregated into a client quality report and a
  counts-only digest for us, validated against gold on dev.
* **C. Cognitive forcing** — a review workflow in which a flagged value cannot
  be waved through and a random audit sample of unflagged values is re-entered
  blind, so production delivered precision is measurable without bias.

Who consumes it: **us** (pipeline quality as the client's inspectors grade it)
and **the client** (a quality report for their own QA). Whether the grades are
ever fed back into the system automatically is open and out of scope.

Decisions taken (operator, 2026-10-07):

| question | decision |
|---|---|
| reviewer identity | none — session-level only (avoids employee performance monitoring; AT/DE works-council and GDPR) |
| data return | full logs, with the client's consent, via an admin export bundle; no phone-home |
| "accepted" vs "never looked at" | random audit sample of unflagged rows, re-entered blind |
| audit rate | configurable, default 0.25 per drawing, min 1, max 5; recorded per session |
| blinding | audit rows indistinguishable from flagged rows; a same-rate random share of flagged rows is blind too |
| forcing on flagged rows | per-row accept only, no bulk accept |
| tray (stage-2 suggestions) | unchanged; its outcomes are graded |
| taxonomy additions | `unsure` action; optional reason on edit and on delete |
| log store | server-side, appended as the review proceeds |
| undo | net operations only are retained |
| completion | explicit Finish seals a revision; Finish blocked while any flagged or blind row is unresolved |
| unsure rows at Finish | exported, marked for escalation |
| client view | exportable quality report (no live dashboard) |
| client logs on our side | ingested as a separate, evidence-weighted reviewer gold |
| validation | operator reviews the 15 scoped dev drawings, live extraction on the GPU host, audit rate 1.0 |
| our grading entry point | a mode of the allowlisted `score`; the guard is not widened |
| architecture | event-sourced session record (approach A); rejected: final-state annotation (no journal, no timing, client-supplied proposal), eval-native scoring (not shippable, cannot express unsure/reasons/audit design) |

Out of scope for this version: notes, marks and title-block fields (only
characteristics are graded); passive view telemetry; per-reviewer statistics;
automatic recalibration from grades.

## 1. Reviewer workflow (C)

**Review queue.** After extraction the outstanding work is one queue — flagged
rows plus blind rows, in reading order (`pos`). Selecting a row zooms the
drawing to its crop automatically.

* **Shown rows** (flagged, not blind): proposal visible. `Y` / `Enter` accepts
  **that row only** and advances to the next outstanding row; `E` edit, `Del`
  delete, `U` unsure. Bulk accept (`Y` over the visible list, the Accept button)
  keeps working for non-flagged rows and for suggestions inside their own
  filter, and **never accepts a flagged row**.
* **Blind rows**: the audit sample of unflagged rows plus an equal-rate random
  sample of flagged rows, both drawn server-side at extract time from a
  per-session seed. In the table the value cell shows `verify` and the reason
  reads "verify" for both kinds, so the inspector cannot tell a confident row
  from a flagged one. The verify panel shows the zoomed crop and EMPTY fields
  (nominal, upper, lower, type). The inspector types what the drawing says;
  `Enter` submits.
  * Entry equals the system's value under value normalisation (§3) → accepted.
  * Otherwise both are shown side by side; `1` keeps the system's, `2` keeps
    the entry, `E` edits. The log records which.
  * `U` marks unsure.
* **Reason chips** appear in a small popover after an edit or delete is
  committed. They are optional; their keys `1`-`5` are scoped to the popover
  (globally `1`-`4` are filter pills, `shortcuts.js`); any other key or click
  dismisses with no reason.
  * Edit: misread digit · wrong/missing tolerance · wrong characteristic type ·
    wrong sign or unit · other.
  * Delete: duplicate of another balloon · not a characteristic (text, title
    block…) · real dimension, but we don't balloon it · wrong region /
    misplaced box · other.
* **Unsure** has its own marker, counts as resolved, and is exported marked
  for escalation (an `unsure` column in Excel, a distinct balloon style in the
  ballooned PDF).
* **Progress bar** — today `table.js` counts an unflagged row as reviewed
  (`r.reviewed || !r.needs_review`), which hides exactly the rows nobody looked
  at. It becomes "N/M resolved" over rows actually resolved, with unflagged,
  non-blind rows shown separately as "unchecked (system-accepted)".
* **Finish & Export** is the primary action, replacing the two export buttons
  as such. It is disabled while any flagged or blind row is unresolved; the
  tooltip says how many remain and offers a jump to the first. Finish seals
  revision `r1` and produces both exports. Re-opening a sealed session and
  finishing again seals `r2`.
* **Admin settings**: audit rate, logging consent. Both are recorded in each
  session's header.

Blinding is UX-level, not adversarial: the browser receives the proposed values
of blind rows and hides them. An inspector reading developer tools could see
them; acceptable for in-house inspectors.

## 2. Capture and storage (A)

**Durable store.** `SINDRI_REVIEW_DIR`, a configured directory outside `/tmp`
(today sessions live in `/tmp/sindri_sessions` and vanish). One directory per
session:

| file | written by | content |
|---|---|---|
| `header.json` | server, at extract | `schema_version`; app version; pipeline config (active flag rules, drop stages, crop knobs, `LOW_CONF`); audit rate and seed; consent flag; drawing sha256; page count; timestamps |
| `proposal.json` | server, at extract | exactly the rows sent to the UI (characteristics + suggestions), each with server-assigned `blind` and `audit` (= unflagged and sampled). The client never supplies it. |
| `events.jsonl` | server, appended per batch from the UI | numbered operations, below |
| `sealed/rN.json` | server, at Finish | final rows, per-row resolution state, compacted net-op list. The raw journal is then truncated: only net operations are retained. |

**Operation vocabulary** — one event per `state.js` operation: `add_row`
(including `source: "manual"` rows from `/api/read_region`),
`delete_row{reason?}`, `move_row`, `edit_cell{field, old, new, reason?}`,
`accept{ids}`, `unaccept{ids}`, `confirm_suggestions{ids}`, `mark_unsure`,
`unmark_unsure`, `blind_submit{id, entered, outcome: match|kept_system|kept_entered|edited}`.
Every event carries `seq`, wall-clock time and time since session start;
`accept` and `blind_submit` also carry `dwell_ms` (time since the row was
selected), which prices the forcing without general view telemetry. Undo
appends `retract{seq}`; redo appends a fresh event; compaction at seal removes
retracted pairs.

**Transport.** The UI queues events, flushes with a ~500 ms debounce to
`POST /api/session/{id}/events` and on page close via `sendBeacon`. The server
is idempotent by `seq` and answers with the highest contiguous `seq` it holds;
the client resends anything above it. Finish asks the server to seal at the
client's final `seq`, and the server refuses on a gap — an incomplete log is
never graded. One writer per session: a second tab gets a new writer token and
the server rejects the stale one with "opened elsewhere".

**Unchanged.** `_exportable` (unconfirmed suggestions never exported). Finish
runs the existing Excel / PDF writers on the sealed rows, plus the `unsure`
column and balloon style.

## 3. Grading (B)

**Location.** `app/review/grading.py`, a pure function
`grade(proposal, sealed, events, header)` in the product package (the client
report needs it). It does **not** import `app/eval`. Field equality comes from a
small product-side normaliser, `app/review/values.py` (decimal comma vs point,
`±` expansion, whitespace, sign of zero); an eval-side test pins it against
`app/eval/normalize` on a shared fixture set, so "edited" here means what
"wrong" means in eval. Rows join on their stable `id`; no geometric matching.

**Per-row grade.** Every proposal row gets exactly one:

| row class | outcome → grade |
|---|---|
| flagged, shown | unchanged → `unneeded_flag` · edited → `justified_flag` · deleted → `phantom_flagged` · `unsure` |
| flagged, blind | as above, tagged `blind` |
| unflagged, audit (blind) | match, or `kept_system` after a mismatch → `audit_correct` (the latter also logged `entry_disagreed`) · `kept_entered` / edited → `audit_wrong` · deleted → `audit_phantom` · `unsure` |
| unflagged, not audited | unchanged → `unchecked` (never counted as correct) · edited → `caught_spontaneously` · deleted → `phantom_spontaneous` |
| suggestion | confirmed unchanged → `drop_wrong` · confirmed after edit → `drop_partial` · deleted → `drop_right` · left → `drop_unexamined` |
| added by hand | `missed_value` |

Edited grades carry the **field signature** (`fields:nominal+upper_tol`, the
`wrong_fields` analogue) and the reason chip; deletes carry the delete reason.
A move is recorded as `placement`, orthogonal to the value grade.

Mapping to the eval taxonomy: `audit_correct` ↔ correct; `audit_wrong` /
`caught_spontaneously` ↔ escaped_error; `justified_flag` ↔ flagged_error;
`unneeded_flag` ↔ flagged_correct; phantoms ↔ false_detection; `missed_value`
↔ missed (lower bound).

**Metrics**, per drawing and pooled over a report period:

* **Delivered precision, production estimate** =
  audit_correct / (audit_correct + audit_wrong + audit_phantom), unsure
  excluded. Drawings have different inclusion probabilities (min 1 / max 5),
  so each is weighted by n_unflagged / n_audited (ratio estimator); CI by
  bootstrap over drawings, since rows within a drawing are correlated. This is
  the number comparable to gold's 0.526 (dev) / 0.958 (test).
* **Flag precision** = justified / (justified + unneeded), shown and blind
  separately.
* **Anchoring effect** = error rate found on blind flagged rows minus that on
  shown flagged rows, bootstrap CI; labelled underpowered until the sample is
  large enough. It says whether per-row accept is enough forcing.
* **Missed values per drawing** (manual adds) — a lower bound.
* **Phantoms by delete reason** — "we don't balloon it" answers the parked
  ballooning-policy question (handoff §4.2).
* **Tray outcomes** — whether drop stage 2 is right in production.
* **Field signatures** of edits.
* **Forcing cost** — median `dwell_ms` for blind, shown-flagged and audit rows;
  review time per drawing.
* **Estimated review cost** under the current weights, labelled ESTIMATED.

**Reconciliation identities** (CLAUDE.md §4), asserted in `grade()` and tests:
grades partition the proposal rows; audited count = the header's sample;
blind-flagged count = its sample; tray grades partition the suggestions;
replaying the net ops on the proposal reproduces the sealed rows exactly. A
session failing one is reported "ungradable: <identity>", never dropped
silently.

## 4. Reports, return path, validation

**Client quality report.** Admin action "Download quality report" for a date
range: one self-contained, printable HTML from `grade()` over sealed sessions —
the §3 metrics with CIs, weekly trend, reason breakdowns, forcing cost, and a
sampling-design footer (rates, seeds, n). It may show values; it never leaves
the client's site by itself.

**Return bundle (consent).** Admin action "Export review logs": a versioned zip
with `manifest.json` (schema version, installation id, period, sha256 per file)
and, per sealed session, `header`, `proposal`, `sealed/rN` and the drawing.
Sessions sealed while consent was off are excluded and counted in the manifest.

**Our side.** A bundle is client data. `ingest --review-bundle` (allowlisted
`ingest`) stores it under the protected root in `reviews/<bundle-id>/` and
writes `gold_reviewer/` with a per-row evidence level: `verified` (edited,
accepted-after-flag, blind-entered, added by hand), `deleted`, `unverified`
(unflagged, never audited — excluded from numerator and denominator, never
counted correct). Reviewer gold is separate from Excel gold and never enters
the frozen split `6d174d5e4f1b9228`.

**Dev validation (gate before any client ships).**

1. GPU host: UI bound to localhost, reached via `ssh -L`; `SINDRI_REVIEW_DIR`
   inside the host's eval data dir; audit rate **1.0** (every unflagged row
   blind — at 0.25 dev would yield ~20 audited rows).
2. The operator reviews the 15 scoped dev drawings as an inspector would.
3. The operator pulls with `sync_client_data.sh`.
4. `runner score --reviewer-grade <campaign>` treats each session's
   `proposal.json` as a pred dump, matches it against gold with the normal
   matcher, and writes a counts-only digest:
   * the **reproduction gate** — does the live proposal equal `r5-control`
     (`n_pred`, per-kind counts)? Reported, not assumed: the cross-tab below is
     valid either way because each proposal is scored against gold itself;
   * the **cross-tab reviewer grade × gold taxonomy**;
   * delivered precision as reviewer-measured vs gold-measured; flag
     precision both ways; the anchoring effect.
5. **Agreement thresholds are registered in a doc before the operator starts
   reviewing.** Off-diagonal cells are findings: a gold-wrong value the
   reviewer accepted is automation bias in a careful operator; a gold-correct
   value the reviewer edited is a gold gap or a reviewer error.

## 5. Privacy and guard

* `SINDRI_REVIEW_DIR` contents and bundles are client data under the corpus
  rules (CLAUDE.md §1, `docs/eval/DATA-HANDLING.md`). Agents read only
  counts-only digests.
* The guard is **not widened**: grading runs through `score`, ingest through
  `ingest`.
* Committed digests are counts-only under `docs/eval/`, aggregate keys
  namespaced (`field:upper_tol`, not a bare tolerance key — the pre-commit
  digest-key trap, CLAUDE.md §5).
* Report and bundle actions are admin-only. The app has no auth today, so
  "admin" is a configured admin token for now — a stated limitation.

## 6. Error handling

* Event flush failure: retried with backoff; a persistent banner if the
  server's contiguous `seq` lags; Finish refused until it catches up.
* Seal refused on a `seq` gap or a stale writer token, with the reason shown.
* `SINDRI_REVIEW_DIR` unset or unwritable: the app starts, shows a persistent
  "review logging disabled" banner, and Finish still exports — but the session
  is recorded nowhere, so it is never graded rather than graded wrong.
* `grade()` identity failure: "ungradable: <identity>" in report and digest.
* Any model-derived float stored in the log goes through the existing NaN
  guard (`models.Confidence`), so a dump cannot become write-only.

## 7. Testing

* **pytest**: store layout; event idempotency and gap refusal; writer token;
  seal compaction and replay identity; `grade()` on synthetic sessions covering
  every row class; ratio estimator and drawing bootstrap on seeded data;
  normaliser pinned against `app/eval/normalize`; `score --reviewer-grade` on a
  synthetic campaign; bundle manifest and evidence levels.
* **node** (`tests/js/`): queue order; no bulk accept of flagged rows;
  blind-panel outcomes; reason-popover key scoping; progress counts; Finish
  gating.
* **Real browser**: throwaway Playwright venv in the scratchpad with a stub
  backend, as in the tray work (handoff §6).
* TDD throughout, one task one commit (CLAUDE.md §4).

## 8. Phases (each releasable on its own)

1. **Capture + Finish** — store, events, seal, gating, progress-bar fix.
2. **Forcing UX** — queue, per-row accept, blind panel, reasons, unsure.
3. **Grading + client report** — `grade()`, `values.py`, report HTML.
4. **Dev validation** — `score --reviewer-grade`, registration doc, operator
   review. **Must pass before anything ships to a client.**
5. **Return path** — bundle export, `ingest --review-bundle`, reviewer gold.
