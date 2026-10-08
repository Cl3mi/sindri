# HITL Phase 2 — Forcing UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow `context-management:milestone-checkpoints`: the CONTROLLER checkpoints `docs/plans/2026-10-09-hitl-phase2-forcing-plan.state.md` at each marker. Implementers never edit that file.

**Goal:** Build the review queue, per-row accept, the blind-verify panel (audit
sample of unflagged rows + an equal-rate blind sample of flagged rows), reason
chips, the unsure marker, and the exports they feed — phase 2 of
`docs/plans/2026-10-07-hitl-review-grading-design.md` (design §8 item 2). This
is the forcing mechanism that makes "accepted" distinguishable from "never
looked at"; grading (`grade()`, the client report) is phase 3 and is **not**
built here.

**Architecture:** Audit/blind sampling is computed once, deterministically,
server-side, the moment a drawing's proposal is written
(`app/batch/worker.py`'s `_run`, right after `header_fn`) — never in the
browser, which only ever receives rows already tagged `blind`/`audit`. The
journal gains three event types (`mark_unsure`, `unmark_unsure`,
`blind_submit`) plus an optional closed-vocabulary `reason` on `edit_cell` and
`delete_row`; `app/review/replay.py` grows one branch per new type, mirrored
op-for-op in `app/static/js/state.js` exactly as every existing op already is.
A small pure normaliser (`app/review/values.py`, mirrored by
`app/static/js/values.js`, pinned together by one shared JSON fixture) answers
whether a blind entry matches the system's value — needed now for the panel's
live compare, not deferred to phase 3's `grade()`. The Finish gate moves from
"advisory, client-side only" to enforced inside `ReviewStore.seal()` itself,
so no caller of `seal()` can bypass it. No SQL migration: every new field
(`blind`, `audit`, `unsure` on a row; `sampling` in the header) lives in
`header.json`/`proposal.json`/`events.jsonl`, exactly where the design's A/B/C
split already puts session state — the `document` table's existing
`flagged_total`/`flagged_open`/`unchecked` columns are reused by widening what
`ReviewStore.progress()` counts as "the queue," not replaced.

**Tech Stack:** Python 3 / FastAPI / pydantic, vanilla ES modules, `pytest`,
`node --test`, PyMuPDF (`fitz`) for the ballooned PDF, `openpyxl` for the
workbook.

**Design:** `docs/plans/2026-10-07-hitl-review-grading-design.md` — §1
(reviewer workflow: queue, per-row accept, blind panel, reason chips, unsure,
progress bar, Finish), §2 (operation vocabulary: `mark_unsure`,
`unmark_unsure`, `blind_submit{id, entered, outcome}`, optional `reason` on
`edit_cell`/`delete_row`, `dwell_ms` on accept/blind_submit), §3 (only as much
as phase 2 needs to build `app/review/values.py` early — `grade()` itself is
phase 3, **not built here**), §6 (error handling), §7 (testing). Phase 1 as
built: `docs/plans/2026-10-07-hitl-phase1-capture-plan.state.md`. The batch
line (B1-B4) as built, which phase 1's state file deferred and this plan now
assumes exists: `docs/plans/2026-10-08-batch-b3-ui-plan.state.md` (writer-token
rotation via `/api/documents/{id}/open`, `ReviewStore.progress()`, the
`document.flagged_total`/`flagged_open`/`unchecked` columns from migration 4),
`docs/plans/2026-10-08-batch-b4-export-ops-plan.state.md` (the single export
path in `app/batch/export_render.py`, migration count currently **5** —
confirm with `python -c "from app.batch import db; print(len(db.MIGRATIONS))"`
before Task 1, since this plan adds none).

---

## Decisions made while planning

The design is silent or ambiguous on several points; each is resolved here
and justified where it lands in the file structure below.

1. **No SQL migration.** Audit/blind/unsure state is entirely a `header.json`
   / `proposal.json` / `events.jsonl` concern. `ReviewStore.progress()`
   already computes `flagged_total`/`flagged_open`/`unchecked` fresh from a
   replay on every call (it is not a stored column — migration 4 only added
   the `document` table's *cache* of its result); widening what counts as
   "the queue" inside `progress()` changes what those cached columns mean
   (a drawing whose only outstanding work is 3 audit rows now shows "3
   open," not "0 open, 20 unchecked") without touching the schema. The
   CLAUDE.md paragraph (Task 14) must call this out as a user-visible change,
   not a regression.
2. **New row fields, all defaulting False.** `Characteristic` gains `blind`,
   `audit`, `unsure` (exported/sealed, pydantic-defaulted so every
   proposal/sealed JSON written before this plan round-trips unchanged — a
   reopened pre-phase-2 session has zero blind rows and the Finish gate
   behaves exactly as before, satisfying "existing sessions must keep
   working"). `blind_resolved` is **not** one of them: it is internal replay
   bookkeeping (did a `blind_submit` land for this id), never sent to or
   read from the browser as a persisted field, so it cannot drift out of
   sync with the one place it is computed (`app/review/replay.py`).
3. **Sampling runs once, synchronously, in `Worker._run`**, right after
   `header_fn` and before `store.create` — not in the browser, not as a
   background job. It is a pure function of `header["sampling"]["seed"]`
   (`secrets.token_hex(16)`, freshly minted by `build_header` on every call),
   so a re-extract's new `store.create(...)` call mints a new seed for free
   (CLAUDE.md: "a re-extract mints a new session, hence a new seed") with no
   special-case code.
4. **Audit config is validated once, at process import, not threaded through
   `Runtime`.** `app/main.py` gains a module-level
   `_AUDIT_CONFIG = sampling.audit_config_from_env()`, computed the moment
   the module loads — exactly how `_BACKEND = get_backend()` already works
   — so a malformed `SINDRI_AUDIT_RATE`/`MIN`/`MAX` fails the boot loudly,
   the same contract `SINDRI_MIN_FREE_MB` and `SINDRI_RETENTION_DAYS` already
   have in `Runtime.from_env`, without adding a parameter to `Worker` or
   `Runtime.__init__` (whose signature is otherwise untouched).
5. **The blind-flagged sample uses the identical rate/min/max clamp as the
   audit sample**, drawn from the same `random.Random(seed)` stream
   immediately after the audit draw (fixed order, documented in
   `sampling.py`). The design only says "an equal-rate random sample of
   flagged rows" and is silent on min/max; applying the same clamp keeps the
   blind-flagged sample's SIZE — not just its appearance — indistinguishable
   from the audit sample's, which is what blinding is for. It also bounds
   forcing cost on a drawing that happens to flag 40 rows: without a max, a
   same-RATE draw over a much larger flagged population could blind far more
   rows than the audit design ever intended per drawing.
6. **`resolved` is never a client-asserted field.** A row's queue-membership
   and resolution are both computed by replay from events: `needs_review or
   blind` puts it in the queue; `unsure` (toggled by `mark_unsure`/
   `unmark_unsure`), `reviewed` (toggled by `accept`/`unaccept`, unchanged),
   or `blind_resolved` (toggled true by a `blind_submit` targeting that id)
   resolves it. An undo of a `blind_submit` is the existing generic
   `retract{target}` — net_events drops the retracted event, so replaying
   without it leaves `blind_resolved` at its default `False` with no new
   undo code, the same mechanism `reviewed` already relies on.
7. **The Finish gate moves into `ReviewStore.seal()` itself**, not only
   `app/main.py`'s route, because `seal()` is the one choke point every
   caller goes through (design: "server-side too, not only in the UI" — a
   check duplicated only in the route is bypassable by any other caller of
   `seal()`). A new `ReviewIncomplete(ReviewError)` (status 409) is raised
   before anything is written to `sealed/rN.json`.
8. **`app/review/values.py` (a product-side normaliser) is built now, in
   phase 2, not deferred to phase 3** — the blind panel's live "does this
   match?" compare needs it the moment the UI ships, it cannot wait for
   `grade()`. It is mirrored by a pure `app/static/js/values.js`, and the two
   are pinned together by one shared fixture,
   `tests/review/fixtures/value_normalisation_cases.json`, read by both a
   pytest test and a node test — the same "one contract, two runtimes" shape
   `tests/review/test_journal_replay_parity.py` already uses for the
   journal. Phase 3's plan must treat `values.py` as already built and
   extend it (e.g. pinning it against `app/eval/normalize` too, per design
   §3) rather than recreating it.
9. **The server does not trust a `blind_submit`'s `outcome` blindly, but only
   checks the one factual claim it can:** `outcome == "match"` asserts that
   the entered values equal the row's current system values under
   `values.py`'s normaliser, and `ReviewStore.append()` verifies that before
   accepting the event (`JournalInvalid`, 422, if the claim does not hold).
   `kept_system`/`kept_entered`/`edited` make no factual claim — they are the
   reviewer's own choice after a mismatch was already shown — and are never
   second-guessed. This check needs the row's *current replayed state*,
   which `validate_event` (a pure, stateless, per-event function) cannot see,
   so it lives in `ReviewStore.append()` where a replay is cheap to compute
   on demand.
10. **Reason vocabularies are closed, validated server-side at append time**,
    not free text: `EDIT_REASONS = {misread_digit, wrong_tolerance,
    wrong_char_type, wrong_sign_or_unit, other}`,
    `DELETE_REASONS = {duplicate, not_a_characteristic,
    unballooned_real_dimension, wrong_region, other}` (named for the design's
    five bullets per action). Naming them is this plan's own choice — the
    design describes the chips by their UI label, not a wire key.
11. **The Excel `Unsure` column and the PDF's distinct marker are driven by
    the sealed row's `unsure` field only** — never `needs_review`, `blind`,
    or `audit`, which are review-process bookkeeping that must never leak
    into a client-facing export. `unsure` is the one state the design says
    is "exported marked for escalation."
12. **The PDF balloon style for `unsure` is a new amber dashed stroke, not
    the tray's violet.** The tray's violet/dashed style is a UI-only
    convention for an unconfirmed suggestion and is never exported at all (a
    confirmed suggestion always clears `suggested` before it can reach a
    sealed revision) — reusing violet on an exported, reviewed, escalated
    row would wrongly read as "still just a suggestion."
13. **`document.flagged_total`/`flagged_open` now count blind rows too**
    (decision 1) — a user-visible change to the work list's badges that the
    CLAUDE.md paragraph (Task 14) states explicitly, so it is never mistaken
    for a regression later.

---

## File structure

| File | Responsibility |
|---|---|
| `app/review/journal.py` | + `EVENT_TYPES` (`mark_unsure`, `unmark_unsure`, `blind_submit`), `BLIND_OUTCOMES`, `EDIT_REASONS`, `DELETE_REASONS`; `validate_event` branches for the three new types plus optional `reason` on `edit_cell`/`delete_row` |
| `app/review/replay.py` | + branches for `mark_unsure`/`unmark_unsure`/`blind_submit`; `mismatches` compares `unsure` too |
| `app/review/values.py` (new) | `canon_value`, `values_equal` — mirrors `app.eval.normalize.canon_value` plus `±`/whitespace/sign-of-zero |
| `app/review/sampling.py` (new) | `DEFAULT_AUDIT_RATE/MIN/MAX`, `validate_audit_config`, `audit_config_from_env`, `assign_sampling` |
| `app/review/header.py` | `build_header` gains an `audit_config` parameter; `sampling` is a real record, not always `None` |
| `app/review/store.py` | + `ReviewIncomplete`; `append()` checks a `blind_submit`'s `match` claim; `seal()` refuses while any row is unresolved; `progress()` widens "the queue" to include blind rows |
| `app/models.py` | `Characteristic` gains `blind`, `audit`, `unsure` (all `bool = False`) |
| `app/batch/worker.py` | `_run` calls `assign_sampling` on the rows between `header_fn` and `store.create` |
| `app/main.py` | `_AUDIT_CONFIG` module constant; `_header` passes it to `build_header`; `seal_session` maps `ReviewIncomplete` to 409 |
| `app/excel.py` | `_write_characteristics_sheet` gains an `Unsure` column |
| `app/pipeline/ballooned_pdf.py` | a distinct amber dashed style for a row with `unsure=True` |
| `app/static/js/values.js` (new) | `canonValue`, `valuesEqual` — JS mirror of `app/review/values.py` |
| `app/static/js/state.js` | `progress()` widened to match `ReviewStore.progress()`; `opMarkUnsure`, `opUnmarkUnsure`, `opBlindSubmit`; `opFromEvent` cases; row shape gains `blind`/`audit`/`unsure`/`blind_resolved` |
| `app/static/js/queue.js` (new) | pure helpers: `queueRows` (reading-order, flagged+blind), `reasonChips(actionType)` |
| `app/static/js/table.js` | per-row accept (`Y`/`Enter` on the selected row only), the `verify` cell for a blind row, the reason-chip popover after an edit/delete commit, the unsure marker |
| `app/static/js/shortcuts.js` | `U` (unsure), scoped `1`-`5` inside an open reason popover (vs. the existing global `1`-`4` filter pills) |
| `app/static/js/finish.js` | unchanged — already calls the injected `canFinish`, which now reflects the widened queue |
| `tests/review/fixtures/value_normalisation_cases.json` (new) | shared normaliser fixture |
| `tests/review/test_values.py` (new) | pytest side of the fixture |
| `tests/js/values.test.mjs` (new) | node side of the fixture |
| `tests/review/test_sampling.py` (new) | determinism, clamps, existing-session (`None`) behaviour, suggestions excluded |
| `tests/review/test_store.py` | + blind-submit match check, `ReviewIncomplete` gate, widened `progress()` |
| `tests/review/test_journal_replay_parity.py` | `_TYPE_KEYS` gains the three new types; `fuzz_journal.mjs` generates them |
| `tests/js/fuzz_journal.mjs` | generates `mark_unsure`/`unmark_unsure`/`blind_submit` steps |
| `tests/js/queue.test.mjs` (new), `tests/js/state.test.mjs` | queue order, no bulk-accept of a flagged row, widened progress |
| `tests/test_excel.py`, `tests/test_ballooned_pdf.py` | `Unsure` column; amber dashed style |
| `CLAUDE.md` | HITL phase 2 paragraph |
| `docker-compose.yml` | `SINDRI_AUDIT_RATE`/`SINDRI_AUDIT_MIN`/`SINDRI_AUDIT_MAX` (commented, defaults documented) |

---

## Rules that bind every task (from CLAUDE.md — read §1 and §4 there)

* TDD strictly: failing test → watch it fail → minimal code → pass → commit.
  One task, one commit (a review fix may be a second commit).
* Never read, name, or put in a command string any path under
  `/home/clemi/sindri-client-data`; single, unpiped, unchained shell commands
  (no `|`, `&&`, `;`, `>`); never a bare `.pdf`/`.xlsx`/`.csv` in a command
  string, `-k` filter, or commit message.
* `app/batch/` and `app/review/` must never import `app.eval`
  (`tests/review/test_product_boundary.py` globs both). `app/review/values.py`
  is a **product-side** normaliser and must not import `app.eval.normalize` —
  it is pinned against it only by the shared fixture test on the `app.eval`
  side (that test lives under `tests/eval/`, not `tests/review/`, for exactly
  this reason — see Task 3).
* Never use bare `git stash`; `git add` and `git commit` are separate calls;
  never `git checkout`/`restore`/`reset --hard` without `git status` first.
* Server logs carry ids (first 8 chars), states, counts and exception TYPES
  only — never exception messages, file names, drawing text, or values
  (CLAUDE.md §1). A `blind_submit`'s `entered` field is client data the
  moment it is logged in an exception message; log `type(e).__name__` only,
  exactly as every existing handler in `app/main.py`/`app/batch/worker.py`
  does.
* Invisible/control characters inside a test string are written as `\u`
  escapes, never pasted literally.
* Tests use temp dirs only; `tests/conftest.py` already isolates
  `SINDRI_DATA_DIR`/`SINDRI_REVIEW_DIR` per test.
* Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
* Full suite before every commit: `python -m pytest -q`. **Confirmed baseline
  (2026-10-09, after B4): 1946 passed, 2 skipped.** Re-measure with a bare
  `python -m pytest -q` as Task 1's first step if any time has passed since
  this plan was written, and use that count as the true baseline — do not
  trust the number quoted here if another branch has merged since.
* `node --test "tests/js/*.test.mjs"` must report the same node-test count as
  the number inside `tests/test_js_state.py`'s pytest result (currently 272),
  run as a separate check after any JS task.
* Never edit `docs/plans/2026-10-09-hitl-phase2-forcing-plan.state.md` — the
  controller checkpoints it.

**Data-integrity tasks** (controller uses its strongest available model for
implementation AND review): Tasks 1, 2, 3, 4, 5, 6, 7, 8, 9, 13.

---

## Milestone 1: vocabulary and sampling

### Task 1: journal vocabulary — `mark_unsure`, `unmark_unsure`, `blind_submit`, reasons

**Data-integrity: yes** (a malformed event here is a 422 traceable to one
request today; a gap here is a 500 at seal time against the whole journal).

**Files:**
- Modify: `app/review/journal.py`
- Test: `tests/review/test_journal.py`

- [ ] **Step 1: confirm the true baseline**

Run: `python -m pytest -q`
Expected: `1946 passed, 2 skipped` (or whatever the suite actually reports —
use that number for every later "Expected" in this plan).

- [ ] **Step 2: write the failing tests** — append to
  `tests/review/test_journal.py`:

```python
from app.review.journal import validate_event, JournalError


def test_mark_unsure_needs_a_string_id():
    validate_event({"seq": 1, "type": "mark_unsure", "id": "a"})
    with pytest.raises(JournalError):
        validate_event({"seq": 1, "type": "mark_unsure", "id": 1})


def test_unmark_unsure_needs_a_string_id():
    validate_event({"seq": 1, "type": "unmark_unsure", "id": "a"})
    with pytest.raises(JournalError):
        validate_event({"seq": 1, "type": "unmark_unsure"})


def test_blind_submit_needs_an_entered_object_and_a_known_outcome():
    validate_event({
        "seq": 1, "type": "blind_submit", "id": "a", "outcome": "match",
        "entered": {"char_type": "Distance", "nominal": "20", "upper_tol": "", "lower_tol": ""},
    })
    with pytest.raises(JournalError):
        validate_event({"seq": 1, "type": "blind_submit", "id": "a",
                        "outcome": "match", "entered": "not an object"})
    with pytest.raises(JournalError):
        validate_event({"seq": 1, "type": "blind_submit", "id": "a",
                        "outcome": "maybe", "entered": {}})


def test_edit_cell_accepts_a_known_reason_or_none():
    base = {"seq": 1, "type": "edit_cell", "id": "a", "field": "nominal",
            "old": "1", "new": "2"}
    validate_event(base)
    validate_event({**base, "reason": "misread_digit"})
    with pytest.raises(JournalError):
        validate_event({**base, "reason": "because I said so"})


def test_delete_row_accepts_a_known_reason_or_none():
    base = {"seq": 1, "type": "delete_row", "id": "a"}
    validate_event(base)
    validate_event({**base, "reason": "duplicate"})
    with pytest.raises(JournalError):
        validate_event({**base, "reason": "nope"})
```

- [ ] **Step 3: run to confirm failure**

Run: `python -m pytest tests/review/test_journal.py -q`
Expected: FAIL — `unknown event type 'mark_unsure'` and similar.

- [ ] **Step 4: implement** — in `app/review/journal.py`:

```python
EVENT_TYPES = frozenset({
    "add_row", "delete_row", "move_row", "edit_cell",
    "accept", "unaccept", "confirm_suggestions", "retract",
    # Phase 2 forcing (design §1/§2): a row the reviewer could not resolve
    # with confidence (mark_unsure/unmark_unsure), and the outcome of the
    # blind-verify panel (blind_submit) -- audit rows and the equal-rate
    # blind-flagged sample both go through it, indistinguishably.
    "mark_unsure", "unmark_unsure", "blind_submit",
})

# The one factual claim a blind_submit can make that the server can check
# itself (store.append, not here -- it needs the row's CURRENT replayed
# state, which a stateless per-event validator cannot see). The other three
# are the reviewer's own choice after a mismatch was already shown and are
# never second-guessed.
BLIND_OUTCOMES = frozenset({"match", "kept_system", "kept_entered", "edited"})

# Closed vocabularies for the reason-chip popover (design §1). Named here,
# not in the design, which describes each chip only by its UI label -- the
# wire key is this plan's own choice.
EDIT_REASONS = frozenset({
    "misread_digit", "wrong_tolerance", "wrong_char_type",
    "wrong_sign_or_unit", "other",
})
DELETE_REASONS = frozenset({
    "duplicate", "not_a_characteristic", "unballooned_real_dimension",
    "wrong_region", "other",
})
```

Add to `validate_event`, inside the `elif type_ == "edit_cell":` branch
(after the existing checks):

```python
        reason = e.get("reason")
        if reason is not None and reason not in EDIT_REASONS:
            raise JournalError(f"edit_cell reason must be one of {sorted(EDIT_REASONS)} or absent")
```

Inside the `elif type_ == "delete_row":` branch:

```python
        reason = e.get("reason")
        if reason is not None and reason not in DELETE_REASONS:
            raise JournalError(f"delete_row reason must be one of {sorted(DELETE_REASONS)} or absent")
```

And add new branches, after the `elif type_ in ("accept", "unaccept", "confirm_suggestions"):` branch:

```python
    elif type_ in ("mark_unsure", "unmark_unsure"):
        if not _is_str(e.get("id")):
            raise JournalError(f"{type_} needs a string id")
    elif type_ == "blind_submit":
        if not _is_str(e.get("id")):
            raise JournalError("blind_submit needs a string id")
        entered = e.get("entered")
        if not isinstance(entered, dict) or not all(
                _is_str(entered.get(f, "")) for f in VALUE_FIELDS):
            raise JournalError(
                "blind_submit needs an entered object with string value fields")
        if e.get("outcome") not in BLIND_OUTCOMES:
            raise JournalError(f"blind_submit outcome must be one of {sorted(BLIND_OUTCOMES)}")
```

- [ ] **Step 5: run to confirm pass**

Run: `python -m pytest tests/review/test_journal.py -q`
Expected: PASS

- [ ] **Step 6: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 passed, 2 skipped

```bash
git add app/review/journal.py tests/review/test_journal.py
git commit -m "$(cat <<'EOF'
review: add mark_unsure/unmark_unsure/blind_submit to the journal vocabulary

Phase 2 forcing needs an unsure marker and a recorded outcome for the
blind-verify panel; reasons on edit/delete are closed vocabularies so a
later report can group by them instead of parsing free text.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: replay — the three new event types, and `unsure` in `mismatches`

**Data-integrity: yes** (a replay bug here reports a false "replay_ok" at
seal, which `grade()` in phase 3 will trust).

**Files:**
- Modify: `app/review/replay.py`
- Test: `tests/review/test_replay.py`

- [ ] **Step 1: write the failing tests** — append to
  `tests/review/test_replay.py`:

```python
from app.review.replay import mismatches, replay


def test_mark_unsure_sets_unsure_true():
    proposal = [{"id": "a", "needs_review": True, "suggested": False}]
    events = [{"seq": 1, "type": "mark_unsure", "id": "a"}]
    rows = replay(proposal, events)
    assert rows["a"]["unsure"] is True


def test_unmark_unsure_sets_unsure_false():
    proposal = [{"id": "a", "needs_review": True, "suggested": False}]
    events = [
        {"seq": 1, "type": "mark_unsure", "id": "a"},
        {"seq": 2, "type": "unmark_unsure", "id": "a"},
    ]
    rows = replay(proposal, events)
    assert rows["a"]["unsure"] is False


def test_blind_submit_sets_blind_resolved_but_not_values():
    proposal = [{"id": "a", "nominal": "20", "needs_review": False,
                "blind": True, "audit": True, "suggested": False}]
    events = [{"seq": 1, "type": "blind_submit", "id": "a", "outcome": "match",
              "entered": {"char_type": "", "nominal": "20", "upper_tol": "", "lower_tol": ""}}]
    rows = replay(proposal, events)
    assert rows["a"]["blind_resolved"] is True
    assert rows["a"]["nominal"] == "20"


def test_mismatches_catches_an_unsure_disagreement():
    replayed = {"a": {"id": "a", "reviewed": True, "unsure": False, "suggested": False,
                      "char_type": "", "nominal": "1", "upper_tol": "", "lower_tol": ""}}
    final = [{"id": "a", "unsure": True, "suggested": False,
             "char_type": "", "nominal": "1", "upper_tol": "", "lower_tol": ""}]
    assert mismatches(replayed, final, ["a"]) == ["a"]
```

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/review/test_replay.py -q`
Expected: FAIL — `KeyError: 'unsure'` or `cannot replay event type 'mark_unsure'`

- [ ] **Step 3: implement** — in `app/review/replay.py`, extend `replay`'s row
  initialisation and add branches:

```python
def replay(proposal_rows: Iterable[dict], events: Iterable[dict]) -> Dict[str, dict]:
    rows = {r["id"]: {**r, "reviewed": False,
                      "unsure": bool(r.get("unsure", False)),
                      "blind_resolved": False} for r in proposal_rows}
    for e in events:
        t = e["type"]
        if t == "add_row":
            r = e["row"]
            rows[r["id"]] = {**r, "reviewed": bool(r.get("reviewed", False)),
                             "unsure": bool(r.get("unsure", False)),
                             "blind_resolved": False}
        elif t == "delete_row":
            rows.pop(e["id"], None)
        elif t == "move_row":
            if e["id"] in rows:
                rows[e["id"]]["balloon_xy"] = list(e["xy"])
        elif t == "edit_cell":
            if e["id"] in rows:
                rows[e["id"]][e["field"]] = e["new"]
        elif t in ("accept", "unaccept"):
            for i in e["ids"]:
                if i in rows:
                    rows[i]["reviewed"] = t == "accept"
        elif t == "confirm_suggestions":
            for i in e["ids"]:
                r = rows.get(i)
                if r is not None and r.get("suggested"):
                    r["suggested"] = False
                    r["reviewed"] = True
        elif t == "mark_unsure":
            if e["id"] in rows:
                rows[e["id"]]["unsure"] = True
        elif t == "unmark_unsure":
            if e["id"] in rows:
                rows[e["id"]]["unsure"] = False
        elif t == "blind_submit":
            # The ACTUAL value change, if any (outcome kept_entered/edited),
            # arrives as its own edit_cell event in the same batch -- this
            # event only records that the row went through the panel, which
            # is what the Finish gate and (in phase 3) grade() need.
            if e["id"] in rows:
                rows[e["id"]]["blind_resolved"] = True
        else:
            raise JournalError(f"cannot replay event type {t!r}")
    return rows
```

Update `mismatches`:

```python
def mismatches(replayed: Dict[str, dict], final_rows: Iterable[dict],
               reviewed_ids: Iterable[str]) -> List[str]:
    final = {r["id"]: r for r in final_rows}
    reviewed = set(reviewed_ids)
    bad = set(replayed) ^ set(final)
    for i in set(replayed) & set(final):
        a, b = replayed[i], final[i]
        if (any((a.get(f) or "") != (b.get(f) or "") for f in VALUE_FIELDS)
                or bool(a.get("suggested")) != bool(b.get("suggested"))
                or bool(a.get("unsure")) != bool(b.get("unsure"))
                or _xy(a) != _xy(b)
                or a["reviewed"] != (i in reviewed)):
            bad.add(i)
    return sorted(bad)
```

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/review/test_replay.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 passed, 2 skipped

```bash
git add app/review/replay.py tests/review/test_replay.py
git commit -m "$(cat <<'EOF'
review: replay mark_unsure/unmark_unsure/blind_submit

blind_submit only ever flips blind_resolved -- the value change it may
cause arrives as its own edit_cell in the same batch, so replay never has
to special-case which field a free-text entry touched.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `app/review/values.py` — the shared value normaliser

**Data-integrity: yes** (decides whether a blind `match` claim is accepted;
wrong here means a wrong value ships as "verified correct").

**Files:**
- Create: `app/review/values.py`
- Create: `tests/review/fixtures/value_normalisation_cases.json`
- Test: `tests/review/test_values.py` (new), `tests/eval/test_values_pinned_to_normalize.py` (new, lives under `tests/eval/` so it may import `app.eval.normalize` without violating the product-boundary rule)

- [ ] **Step 1: write the shared fixture** — create
  `tests/review/fixtures/value_normalisation_cases.json`:

```json
[
  {"a": "20", "b": "20", "equal": true},
  {"a": "20,0", "b": "20.00", "equal": true},
  {"a": "+20", "b": "20", "equal": true},
  {"a": "±20", "b": "20", "equal": true},
  {"a": "20  mm", "b": "20 mm", "equal": true},
  {"a": "-0", "b": "0", "equal": true},
  {"a": "0.10", "b": "0.1", "equal": true},
  {"a": "Distance", "b": "distance", "equal": true},
  {"a": "20", "b": "21", "equal": false},
  {"a": "", "b": "0", "equal": false},
  {"a": "", "b": "", "equal": true}
]
```

- [ ] **Step 2: write the failing tests** — create `tests/review/test_values.py`:

```python
"""app/review/values.py: the normaliser the blind-verify panel's live
compare needs now, and the server's check of a blind_submit's `match`
claim (app/review/store.py). Pinned against app/static/js/values.js and
app.eval.normalize.canon_value by the SAME fixture file -- one contract,
three readers, the shape tests/review/test_journal_replay_parity.py already
uses for the journal."""
import json
from pathlib import Path

import pytest

from app.review.values import canon_value, values_equal

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "value_normalisation_cases.json")
    .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE)
def test_fixture_cases(case):
    assert values_equal(case["a"], case["b"]) == case["equal"], case


def test_canon_value_of_none_is_empty_string():
    assert canon_value(None) == ""
```

Create `tests/eval/test_values_pinned_to_normalize.py`:

```python
"""app/review/values.py must agree with app.eval.normalize.canon_value on
every case BOTH sides can express (decimal comma/point, trailing zeros,
whitespace, casefolding) -- so "edited" in a reviewer's grade means what
"wrong" already means in eval (design §3). Lives under tests/eval/, not
tests/review/, because only this side of the pin may import app.eval at
all (tests/review/test_product_boundary.py forbids it from app/review)."""
import json
from pathlib import Path

from app.eval.normalize import canon_value as eval_canon
from app.review.values import canon_value as review_canon

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "review" / "fixtures" /
     "value_normalisation_cases.json").read_text(encoding="utf-8"))


def test_review_values_agrees_with_eval_normalize_on_shared_cases():
    # '±' (a leading +/-) and whitespace collapsing are review-only
    # (eval's gold/prediction text never carries either); skip those, and
    # agree on everything else.
    for case in FIXTURE:
        if "±" in case["a"] or "±" in case["b"]:
            continue
        if "  " in case["a"] or "  " in case["b"]:
            continue
        assert eval_canon(case["a"]) == review_canon(case["a"])
        assert eval_canon(case["b"]) == review_canon(case["b"])
```

- [ ] **Step 3: run to confirm failure**

Run: `python -m pytest tests/review/test_values.py tests/eval/test_values_pinned_to_normalize.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.review.values'`

- [ ] **Step 4: implement** — create `app/review/values.py`:

```python
"""Pure value normalisation shared by the blind-verify panel's live compare
(app/static/js/values.js) and the server's check of a blind_submit's
`match` claim (app/review/store.py). Mirrors app.eval.normalize.canon_value's
decimal handling (comma vs point, Decimal-based trailing-zero stripping)
plus three things eval's offline scorer never has to handle, because gold
and prediction text never carries them: a leading '+/-' (meaningless on one
typed field -- the same treatment canon_value already gives a leading
'+'), arbitrary internal whitespace from a careless paste, and the sign of
zero ("-0" must read as "0": a reviewer typing it is not asserting a
negative zero tolerance).

Pinned against app.eval.normalize by tests/eval/test_values_pinned_to_normalize.py
and tests/review/fixtures/value_normalisation_cases.json, read by a pytest
test and a node test (tests/js/values.test.mjs) -- one contract, three
readers, the shape tests/review/test_journal_replay_parity.py already uses
for the journal. Deliberately NOT imported from app.eval (the product
boundary test, tests/review/test_product_boundary.py, forbids app/review
from importing app.eval) -- the pin runs the other direction instead."""
from decimal import Decimal, InvalidOperation
from typing import Optional


def _try_decimal(s: str) -> Optional[Decimal]:
    t = " ".join(s.split()).replace(",", ".").lstrip("+").lstrip("±")
    if not t:
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def canon_value(v) -> str:
    """Canonical string form of one typed field (nominal, upper_tol,
    lower_tol or char_type). Numeric via Decimal; everything else
    whitespace-collapsed and casefolded."""
    if v is None:
        return ""
    s = str(v)
    d = _try_decimal(s)
    if d is not None:
        if d == 0:
            d = Decimal(0)          # sign of zero: "-0" reads as "0"
        d = d.normalize()
        if d == d.to_integral_value():
            d = d.quantize(Decimal(1))
        return str(d)
    return " ".join(s.split()).casefold()


def values_equal(a, b) -> bool:
    return canon_value(a) == canon_value(b)
```

- [ ] **Step 5: run to confirm pass**

Run: `python -m pytest tests/review/test_values.py tests/eval/test_values_pinned_to_normalize.py -q`
Expected: PASS

- [ ] **Step 6: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 + 13 passed, 2 skipped

```bash
git add app/review/values.py tests/review/test_values.py tests/eval/test_values_pinned_to_normalize.py tests/review/fixtures/value_normalisation_cases.json
git commit -m "$(cat <<'EOF'
review: add the product-side value normaliser (app/review/values.py)

The blind-verify panel's live compare needs this now; grade() (phase 3)
will reuse it. Pinned against app.eval.normalize by a shared fixture so
"edited" means what "wrong" already means in eval.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

> **Milestone: journal-vocabulary-and-normaliser** - state must be checkpointed here before continuing.

---

### Task 4: `app/review/sampling.py` — audit/blind-flagged sampling

**Data-integrity: yes** (decides which rows are blind forever — not
re-derivable after the fact once a reviewer has seen the table).

**Files:**
- Create: `app/review/sampling.py`
- Test: `tests/review/test_sampling.py` (new)

- [ ] **Step 1: write the failing tests** — create `tests/review/test_sampling.py`:

```python
"""Audit + blind-flagged sampling (design §1/§2, operator decisions
2026-10-09): a deterministic, server-side draw from one seed, computed
once at extract time. Suggestions are never sampled; the clamp is
rate -> round -> clamp(min, max or unbounded) -> never more than the
population."""
import pytest

from app.review.sampling import (
    DEFAULT_AUDIT_MAX, DEFAULT_AUDIT_MIN, DEFAULT_AUDIT_RATE,
    assign_sampling, audit_config_from_env, validate_audit_config,
)


def _rows(n_unflagged, n_flagged, n_suggested=0):
    rows = []
    for i in range(n_unflagged):
        rows.append({"id": f"u{i}", "needs_review": False, "suggested": False})
    for i in range(n_flagged):
        rows.append({"id": f"f{i}", "needs_review": True, "suggested": False})
    for i in range(n_suggested):
        rows.append({"id": f"s{i}", "needs_review": False, "suggested": True})
    return rows


def test_assign_sampling_is_deterministic_given_a_seed():
    rows = _rows(20, 10)
    config = {"rate": 0.5, "min": 1, "max": 0}
    a = assign_sampling(rows, config, seed="fixed-seed")
    b = assign_sampling(rows, config, seed="fixed-seed")
    assert [(r["id"], r["blind"], r["audit"]) for r in a] == \
           [(r["id"], r["blind"], r["audit"]) for r in b]


def test_a_different_seed_can_sample_differently():
    rows = _rows(20, 10)
    config = {"rate": 0.5, "min": 1, "max": 0}
    a = assign_sampling(rows, config, seed="seed-one")
    b = assign_sampling(rows, config, seed="seed-two")
    assert [(r["id"], r["blind"]) for r in a] != [(r["id"], r["blind"]) for r in b]


def test_rate_zero_samples_nothing_even_with_a_minimum_of_zero():
    rows = _rows(10, 10)
    out = assign_sampling(rows, {"rate": 0.0, "min": 0, "max": 0}, seed="s")
    assert not any(r["blind"] for r in out)


def test_rate_one_with_no_cap_samples_everything():
    rows = _rows(5, 5)
    out = assign_sampling(rows, {"rate": 1.0, "min": 1, "max": 0}, seed="s")
    unflagged = [r for r in out if not r["needs_review"]]
    flagged = [r for r in out if r["needs_review"]]
    assert all(r["audit"] for r in unflagged)
    assert all(r["blind"] for r in flagged)


def test_max_caps_the_sample_even_when_rate_would_ask_for_more():
    rows = _rows(100, 0)
    out = assign_sampling(rows, {"rate": 1.0, "min": 1, "max": 5}, seed="s")
    assert sum(1 for r in out if r["audit"]) == 5


def test_min_floors_the_sample_even_when_rate_would_ask_for_fewer():
    rows = _rows(20, 0)
    out = assign_sampling(rows, {"rate": 0.01, "min": 3, "max": 0}, seed="s")
    assert sum(1 for r in out if r["audit"]) == 3


def test_sample_never_exceeds_the_population():
    rows = _rows(2, 0)
    out = assign_sampling(rows, {"rate": 1.0, "min": 10, "max": 0}, seed="s")
    assert sum(1 for r in out if r["audit"]) == 2


def test_suggestions_are_never_sampled():
    rows = _rows(2, 2, n_suggested=10)
    out = assign_sampling(rows, {"rate": 1.0, "min": 1, "max": 0}, seed="s")
    suggested = [r for r in out if r["suggested"]]
    assert not any(r.get("blind") or r.get("audit") for r in suggested)


def test_audit_and_blind_flagged_are_independent_populations():
    """A blind-flagged row (flagged + sampled) is `blind` but never
    `audit` -- `audit` means specifically unflagged-and-sampled."""
    rows = _rows(0, 10)
    out = assign_sampling(rows, {"rate": 1.0, "min": 1, "max": 0}, seed="s")
    assert all(r["blind"] and not r["audit"] for r in out)


@pytest.mark.parametrize("rate,min_n,max_n", [
    (-0.1, 1, 5), (1.1, 1, 5), (0.5, -1, 5), (0.5, 1, -1), (0.5, 10, 5),
])
def test_validate_audit_config_rejects_bad_values(rate, min_n, max_n):
    with pytest.raises(ValueError):
        validate_audit_config(rate, min_n, max_n)


def test_validate_audit_config_accepts_max_zero_as_uncapped():
    validate_audit_config(0.5, 1, 0)


def test_audit_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("SINDRI_AUDIT_RATE", raising=False)
    monkeypatch.delenv("SINDRI_AUDIT_MIN", raising=False)
    monkeypatch.delenv("SINDRI_AUDIT_MAX", raising=False)
    cfg = audit_config_from_env()
    assert cfg == {"rate": DEFAULT_AUDIT_RATE, "min": DEFAULT_AUDIT_MIN,
                   "max": DEFAULT_AUDIT_MAX}


def test_audit_config_from_env_rejects_a_bad_value_loudly(monkeypatch):
    monkeypatch.setenv("SINDRI_AUDIT_RATE", "2.0")
    with pytest.raises(ValueError):
        audit_config_from_env()
```

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/review/test_sampling.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.review.sampling'`

- [ ] **Step 3: implement** — create `app/review/sampling.py`:

```python
"""Audit + blind-flagged sampling (design §1/§2; operator decisions
2026-10-09). Runs once, synchronously, server-side, the moment a drawing's
proposal rows exist (app/batch/worker.py's _run) -- never in the browser,
which only ever sees rows already tagged `blind`/`audit`.

Two independent draws from ONE seeded random.Random, in a FIXED order
(audit first, then blind-flagged), so the whole sampling decision for a
drawing is reproducible from its header's seed alone: a re-extract's fresh
store.create() call mints a new seed (app/review/header.py), so it
naturally gets a fresh, independent sample with no special-case code."""
import os
import random
from typing import List

DEFAULT_AUDIT_RATE = 0.25
DEFAULT_AUDIT_MIN = 1
DEFAULT_AUDIT_MAX = 5


def validate_audit_config(rate: float, min_n: int, max_n: int) -> None:
    """Fails the boot loudly (app/main.py's module-level _AUDIT_CONFIG),
    the same contract SINDRI_MIN_FREE_MB already has in Runtime.from_env.
    max_n == 0 means uncapped (CLAUDE.md's "0 turns the guard off"
    convention, reused here on purpose)."""
    if not (0.0 <= rate <= 1.0):
        raise ValueError("the audit rate must be between 0 and 1")
    if min_n < 0:
        raise ValueError("the audit minimum cannot be negative")
    if max_n < 0:
        raise ValueError("the audit maximum cannot be negative")
    if max_n != 0 and max_n < min_n:
        raise ValueError("the audit maximum cannot be below the minimum")


def audit_config_from_env() -> dict:
    rate = float(os.environ.get("SINDRI_AUDIT_RATE") or DEFAULT_AUDIT_RATE)
    min_n = int(os.environ.get("SINDRI_AUDIT_MIN") or DEFAULT_AUDIT_MIN)
    max_n = int(os.environ.get("SINDRI_AUDIT_MAX") or DEFAULT_AUDIT_MAX)
    validate_audit_config(rate, min_n, max_n)
    return {"rate": rate, "min": min_n, "max": max_n}


def _sample_size(population: int, config: dict) -> int:
    n = round(config["rate"] * population)
    n = max(n, config["min"] if population > 0 else 0)
    if config["max"]:
        n = min(n, config["max"])
    return min(n, population)


def assign_sampling(rows: List[dict], config: dict, seed: str) -> List[dict]:
    """Returns a NEW list (rows are not mutated in place) with `blind` and
    `audit` set on every row. A suggestion (`suggested`) is never sampled
    -- the tray is unchanged and its outcomes are graded separately
    (design §1). Row order is the order `rows` already arrives in (pos
    order, from extraction) -- never re-sorted, so the draw is reproducible
    without this module imposing its own ordering."""
    rng = random.Random(seed)
    balloons = [r for r in rows if not r.get("suggested")]
    unflagged = [r["id"] for r in balloons if not r.get("needs_review")]
    flagged = [r["id"] for r in balloons if r.get("needs_review")]

    audited = set(rng.sample(unflagged, _sample_size(len(unflagged), config)))
    blind_flagged = set(rng.sample(flagged, _sample_size(len(flagged), config)))

    out = []
    for r in rows:
        if r.get("suggested"):
            out.append({**r, "blind": False, "audit": False})
            continue
        is_audit = r["id"] in audited
        is_blind = is_audit or r["id"] in blind_flagged
        out.append({**r, "blind": is_blind, "audit": is_audit})
    return out
```

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/review/test_sampling.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 + 13 + 14 passed, 2 skipped

```bash
git add app/review/sampling.py tests/review/test_sampling.py
git commit -m "$(cat <<'EOF'
review: add deterministic audit + blind-flagged sampling

One seeded draw, audit then blind-flagged in a fixed order, so a
drawing's whole sampling decision reproduces from its header's seed alone
and a re-extract's fresh seed gets an independent sample for free.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `header.py` — a real `sampling` record

**Data-integrity: yes** (the seed this plan's determinism depends on).

**Files:**
- Modify: `app/review/header.py`
- Test: `tests/review/test_header.py`

- [ ] **Step 1: write the failing tests** — append to `tests/review/test_header.py`:

```python
def test_build_header_records_the_audit_config_and_a_seed(tmp_path, sample_pdf):
    config = {"rate": 0.25, "min": 1, "max": 5}
    h = build_header(sample_pdf, consent=False, audit_config=config)
    assert h["sampling"]["rate"] == 0.25
    assert h["sampling"]["min"] == 1
    assert h["sampling"]["max"] == 5
    assert isinstance(h["sampling"]["seed"], str) and h["sampling"]["seed"]


def test_build_header_mints_a_fresh_seed_every_call(tmp_path, sample_pdf):
    config = {"rate": 0.25, "min": 1, "max": 5}
    a = build_header(sample_pdf, consent=False, audit_config=config)
    b = build_header(sample_pdf, consent=False, audit_config=config)
    assert a["sampling"]["seed"] != b["sampling"]["seed"]
```

(If this test file has no `sample_pdf` fixture yet, check
`tests/conftest.py` first — phase 1's `tests/review/test_header.py` already
exercises `build_header` against a real PDF fixture; reuse whatever name it
uses there instead of inventing `sample_pdf`.)

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/review/test_header.py -q`
Expected: FAIL — `TypeError: build_header() missing 1 required positional argument: 'audit_config'`

- [ ] **Step 3: implement** — in `app/review/header.py`:

```python
import secrets
...

def build_header(pdf_path: Union[str, Path], consent: bool, audit_config: dict) -> dict:
    pdf_path = Path(pdf_path)
    with fitz.open(pdf_path) as doc:
        pages = doc.page_count
    return {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "app_version": os.environ.get("SINDRI_VERSION", "unknown"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "drawing_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "pages": pages,
        "consent": bool(consent),
        # A fresh seed every call (not just every drawing): a re-extract
        # calls build_header again and must get an independent sample
        # (CLAUDE.md: "a re-extract mints a new session, hence a new
        # seed"), with no special-case code anywhere else.
        "sampling": {**audit_config, "seed": secrets.token_hex(16)},
        "pipeline": {
            "flag_rules": list(pr.ACTIVE_FLAG_RULES),
            "drop_stages": [list(s) for s in pr.ACTIVE_DROP_STAGES],
            "crop_knobs": active_crop_knobs(),
            "low_conf": pipeline_review.LOW_CONF,
        },
    }
```

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/review/test_header.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 + 13 + 14 + 2 passed, 2 skipped (plus any existing
`test_header.py` tests that called `build_header` with the old two-argument
signature must be updated in this same commit — grep for
`build_header(` across `tests/` first and fix every call site)

```bash
git add app/review/header.py tests/review/test_header.py
git commit -m "$(cat <<'EOF'
review: header.sampling is a real record, not always None

Phase 2's audit/blind-flagged draw needs a seed and the configured
rate/min/max recorded per session; the field was reserved for this since
phase 1 (header.py's own "Set by the audit sampler from Phase 2 on"
comment).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

> **Milestone: sampling-complete** - state must be checkpointed here before continuing.

---

## Milestone 2: server wiring — worker, store, main, exports

### Task 6: `Characteristic` gains `blind`/`audit`/`unsure`

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: write the failing test** — append to `tests/test_models.py`:

```python
def test_characteristic_defaults_blind_audit_unsure_false():
    c = Characteristic(pos=1)
    assert c.blind is False
    assert c.audit is False
    assert c.unsure is False


def test_characteristic_round_trips_blind_audit_unsure():
    c = Characteristic(pos=1, blind=True, audit=True, unsure=True)
    again = Characteristic.model_validate(c.model_dump(mode="json"))
    assert (again.blind, again.audit, again.unsure) == (True, True, True)
```

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/test_models.py -q`
Expected: FAIL — `TypeError: Characteristic() got an unexpected keyword argument 'blind'`

- [ ] **Step 3: implement** — in `app/models.py`, inside `class Characteristic`:

```python
    # Phase 2 forcing (design §1/§2). All default False, so every
    # proposal/sealed JSON written before this plan round-trips unchanged.
    blind: bool = False     # shown as "verify": an audit row or a blind-flagged row
    audit: bool = False     # specifically unflagged-and-sampled (a SUBSET of blind)
    unsure: bool = False    # exported marked for escalation
```

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 + 13 + 14 + 2 + 2 passed, 2 skipped

```bash
git add app/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
models: add blind/audit/unsure to Characteristic

Phase 2 forcing fields, all defaulting False so every proposal and sealed
revision written before this plan still round-trips unchanged.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: wire sampling into the worker

**Data-integrity: yes** (the only point sampling ever runs).

**Files:**
- Modify: `app/batch/worker.py`
- Test: `tests/batch/test_worker.py`

- [ ] **Step 1: write the failing test** — append to `tests/batch/test_worker.py`
  (check the existing fixtures in this file first — reuse whatever rig
  already builds a `Worker` with fake `extract_fn`/`header_fn`):

```python
def test_sampled_rows_carry_blind_and_audit_before_the_review_record_is_created(rig):
    """A fake header_fn supplies a real sampling config; _run must call
    assign_sampling on the extracted rows before store.create, so the
    PROPOSAL the UI ever sees already carries blind/audit."""
    repo, data, store, worker = rig   # adapt to this file's actual fixture names
    doc_id = "a" * 32
    repo.create_document(doc_id, "w.pdf", pages=1, sha256=None)
    repo.enqueue(doc_id)
    repo.claim_next()

    def extract_fn(pdf_path, work_dir, progress):
        from app.pipeline.extract import ExtractionResult
        return ExtractionResult(characteristics=[
            Characteristic(pos=1, id="r1", needs_review=False),
        ], suggestions=[], notes=None, marks=None, title_block=[])

    def header_fn(pdf_path):
        return build_header(pdf_path, consent=False,
                            audit_config={"rate": 1.0, "min": 1, "max": 0})

    worker.extract_fn = extract_fn
    worker.header_fn = header_fn
    worker._run(doc_id)

    proposal = json.loads((store.root / doc_id / "proposal.json").read_text())
    assert proposal["rows"][0]["audit"] is True
    assert proposal["rows"][0]["blind"] is True
```

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/batch/test_worker.py -q`
Expected: FAIL — `assert False is True` (rows written with `audit`/`blind`
at their pydantic defaults, never assigned)

- [ ] **Step 3: implement** — in `app/batch/worker.py`, add the import:

```python
from app.review.sampling import assign_sampling
```

and change the `self.store.create(...)` call inside `_run`:

```python
        try:
            header = self.header_fn(d / "input.pdf")
            sampling = header.get("sampling")
            if sampling is not None:
                # Deterministic from the header's own seed (app/review/
                # sampling.py): the proposal the UI ever sees already
                # carries blind/audit, never computed client-side.
                rows = assign_sampling(rows, sampling, seed=sampling["seed"])
            self.store.create(doc_id, header, {"rows": rows})
        except Exception as e:
```

(Replace the existing `self.store.create(doc_id, self.header_fn(d / "input.pdf"), {"rows": rows})`
line with the block above, inside the same `try`/`except Exception as e:`
that already wraps it — check the surrounding lines before editing so the
existing `except`'s `self._settle(..., f"review_store:{type(e).__name__}")`
body is preserved unchanged.)

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/batch/test_worker.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 + 13 + 14 + 2 + 2 + 1 passed, 2 skipped

```bash
git add app/batch/worker.py tests/batch/test_worker.py
git commit -m "$(cat <<'EOF'
batch: sample blind/audit rows before the review record is created

assign_sampling runs between header_fn and store.create, so the proposal
the UI ever receives already carries blind/audit -- the browser never
computes or sees the sampling decision itself.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `ReviewStore` — blind-submit match check, Finish gate, widened `progress()`

**Data-integrity: yes** (the Finish gate and the one server-side check on a
blind claim).

**Files:**
- Modify: `app/review/store.py`
- Test: `tests/review/test_store.py`

- [ ] **Step 1: write the failing tests** — append to `tests/review/test_store.py`
  (reuse this file's existing `store`/`tmp_path` fixtures and whatever helper
  it already has for seeding a session — check the file before writing, since
  phase 1's tests already build sessions by hand with `store.create`):

```python
from app.review.store import ReviewIncomplete


def test_append_refuses_a_false_match_claim(store):
    writer = store.create("s1", {}, {"rows": [
        {"id": "a", "nominal": "20", "needs_review": False, "blind": True,
         "audit": True, "suggested": False},
    ]})
    with pytest.raises(JournalInvalid):
        store.append("s1", writer, [{
            "seq": 1, "type": "blind_submit", "id": "a", "outcome": "match",
            "t_wall": "x", "t_ms": 0,
            "entered": {"char_type": "", "nominal": "99", "upper_tol": "", "lower_tol": ""},
        }])


def test_append_accepts_a_true_match_claim(store):
    writer = store.create("s2", {}, {"rows": [
        {"id": "a", "nominal": "20", "needs_review": False, "blind": True,
         "audit": True, "suggested": False},
    ]})
    through = store.append("s2", writer, [{
        "seq": 1, "type": "blind_submit", "id": "a", "outcome": "match",
        "t_wall": "x", "t_ms": 0,
        "entered": {"char_type": "", "nominal": "20,0", "upper_tol": "", "lower_tol": ""},
    }])
    assert through == 1


def test_seal_refuses_while_a_flagged_row_is_unresolved(store):
    writer = store.create("s3", {}, {"rows": [
        {"id": "a", "needs_review": True, "suggested": False},
    ]})
    with pytest.raises(ReviewIncomplete):
        store.seal("s3", writer, 0, [
            {"id": "a", "needs_review": True, "suggested": False},
        ], [])


def test_seal_refuses_while_a_blind_row_is_unresolved(store):
    writer = store.create("s4", {}, {"rows": [
        {"id": "a", "needs_review": False, "blind": True, "audit": True, "suggested": False},
    ]})
    with pytest.raises(ReviewIncomplete):
        store.seal("s4", writer, 0, [
            {"id": "a", "needs_review": False, "blind": True, "audit": True, "suggested": False},
        ], [])


def test_seal_succeeds_once_an_unsure_row_is_marked(store):
    writer = store.create("s5", {}, {"rows": [
        {"id": "a", "needs_review": True, "suggested": False},
    ]})
    seq = store.append("s5", writer, [
        {"seq": 1, "type": "mark_unsure", "id": "a", "t_wall": "x", "t_ms": 0},
    ])
    out = store.seal("s5", writer, seq, [
        {"id": "a", "needs_review": True, "unsure": True, "suggested": False},
    ], [])
    assert out["revision"] == 1


def test_progress_counts_a_blind_row_as_outstanding(store):
    writer = store.create("s6", {}, {"rows": [
        {"id": "a", "needs_review": False, "blind": True, "audit": True, "suggested": False},
        {"id": "b", "needs_review": False, "suggested": False},
    ]})
    p = store.progress("s6")
    assert p["flagged_total"] == 1       # the blind row joins the queue
    assert p["flagged_open"] == 1
    assert p["unchecked"] == 1           # only the untouched, non-blind row


def test_progress_resolves_a_blind_row_after_blind_submit(store):
    writer = store.create("s7", {}, {"rows": [
        {"id": "a", "nominal": "1", "needs_review": False, "blind": True,
         "audit": True, "suggested": False},
    ]})
    store.append("s7", writer, [{
        "seq": 1, "type": "blind_submit", "id": "a", "outcome": "match",
        "t_wall": "x", "t_ms": 0,
        "entered": {"char_type": "", "nominal": "1", "upper_tol": "", "lower_tol": ""},
    }])
    p = store.progress("s7")
    assert p["flagged_open"] == 0
```

(Add a `store` fixture at the top of `tests/review/test_store.py` if this
file does not already have one importing `ReviewStore` over a `tmp_path`;
check first — phase 1's version of this file almost certainly has one
already, named `store`.)

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/review/test_store.py -q`
Expected: FAIL — several: `ImportError: cannot import name 'ReviewIncomplete'`,
then (after adding the import locally to unblock collection) assertion
failures on the gate and on `progress()`'s counts.

- [ ] **Step 3: implement** — in `app/review/store.py`, add the exception and
  the import:

```python
from app.review.values import values_equal

...

class ReviewIncomplete(ReviewError):
    """Finish is blocked while any flagged or blind row is unresolved
    (design §1) -- enforced here, not only in the UI's canFinish(), so no
    OTHER caller of seal() can bypass it."""
    status = 409
```

Add a module-level helper (near the top, after `VALUE_FIELDS`-style
constants are imported):

```python
def _unresolved_ids(replayed: dict) -> List[str]:
    """Rows the Finish gate blocks on: a flagged row never accepted, or a
    blind row (audit or blind-flagged) never submitted through the
    verify panel -- unless marked unsure, which design §1 treats as
    resolved too. A suggestion is never in the queue (the tray is
    unchanged)."""
    out = []
    for rid, r in replayed.items():
        if r.get("suggested"):
            continue
        if not (r.get("needs_review") or r.get("blind")):
            continue
        if r.get("unsure") or r.get("reviewed") or r.get("blind_resolved"):
            continue
        out.append(rid)
    return sorted(out)
```

Add the blind-submit check, called from `append()`:

```python
    def _check_blind_submits(self, d: Path, batch_through: int, batch_events: List[dict]) -> None:
        """A `match` outcome is the one factual claim in a blind_submit the
        server can check itself (app/review/values.py) -- it needs the
        row's CURRENT replayed state, which validate_event (stateless,
        per-event) cannot see. kept_system/kept_entered/edited make no
        factual claim and are never second-guessed."""
        try:
            net = net_events(self._events(d))
            proposal = json.loads(
                (d / "proposal.json").read_text(encoding="utf-8"))["rows"]
            current = replay(proposal, net)
        except JournalError as exc:
            raise JournalInvalid(str(exc)) from exc
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise JournalInvalid(f"corrupt review record: {exc}") from exc
        for e in batch_events:
            if e.get("type") != "blind_submit" or e.get("outcome") != "match":
                continue
            row = current.get(e.get("id"))
            entered = e.get("entered") or {}
            if row is None:
                raise JournalInvalid("blind_submit refers to an unknown row")
            if not all(values_equal(row.get(f, ""), entered.get(f, ""))
                      for f in VALUE_FIELDS):
                raise JournalInvalid(
                    "blind_submit claims a match the values do not support")
```

Need `VALUE_FIELDS` imported in `store.py`; add to the existing `from
app.review.journal import (...)` line:

```python
from app.review.journal import (
    JournalError, VALUE_FIELDS, contiguous_seq, net_events, validate_event)
```

Call it from `append()`, right after the existing `validate_event` loop:

```python
            try:
                for e in events:
                    validate_event(e)
            except JournalError as exc:
                raise JournalInvalid(str(exc)) from exc
            if any(e.get("type") == "blind_submit" for e in events):
                self._check_blind_submits(d, self._sealed_through(d), events)
```

Add the Finish gate to `seal()`, right after `net = net_events(events)` and
before computing `proposal`/`bad`:

```python
            try:
                net = net_events(events)
                proposal = json.loads(
                    (d / "proposal.json").read_text(encoding="utf-8"))["rows"]
                replayed = replay(proposal, net)
                outstanding = _unresolved_ids(replayed)
                if outstanding:
                    raise ReviewIncomplete(
                        f"{len(outstanding)} row(s) still need review before Finish")
                bad = mismatches(replayed, rows, reviewed_ids)
            except JournalError as exc:
                raise JournalInvalid(str(exc)) from exc
```

(This replaces the existing `bad = mismatches(replay(proposal, net), rows,
reviewed_ids)` line — `replayed` is now computed once and reused for both
the gate and `mismatches`.)

Update `progress()`'s queue definition:

```python
        balloons = [r for r in rows.values() if not r.get("suggested")]
        # The queue widens to blind rows (design §1): an audit row or a
        # blind-flagged row must be resolved through the verify panel
        # (or marked unsure) exactly like a shown flagged row, so it must
        # count as outstanding here too -- this is the ONE place
        # flagged_total/flagged_open widen; the document columns they feed
        # (migration 4) are unchanged.
        queue = [r for r in balloons if r.get("needs_review") or r.get("blind")]
        resolved = sum(1 for r in queue
                      if r.get("unsure") or r.get("reviewed") or r.get("blind_resolved"))
        return {"flagged_total": len(queue), "flagged_open": len(queue) - resolved,
                "unchecked": len(balloons) - len(queue)}
```

(Replaces the existing `flagged = ...` / `flagged_open = ...` / `return
{...}` block at the end of `progress()`.)

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/review/test_store.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + 6 + 4 + 13 + 14 + 2 + 2 + 1 + 9 passed, 2 skipped

```bash
git add app/review/store.py tests/review/test_store.py
git commit -m "$(cat <<'EOF'
review: enforce the Finish gate and the blind-submit match check in ReviewStore

seal() now refuses while any flagged or blind row is unresolved, server
side, not only in the UI's canFinish(); append() refuses a blind_submit
that falsely claims a match. progress() widens its queue to blind rows.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `app/main.py` — boot-time audit config, `ReviewIncomplete` → 409

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: write the failing test** — append to `tests/test_api.py`:

```python
def test_seal_with_an_unresolved_flagged_row_is_409(client, monkeypatch):
    # Adapt doc_id/session setup to this file's existing helper for
    # creating a ready, reviewable drawing (every other seal test in this
    # file already has one) -- then post a seal whose rows still carry an
    # unresolved flagged row.
    ...
```

(This test's exact setup depends on `tests/test_api.py`'s existing helper
for standing up a `ready` drawing with a session — read the file's existing
`test_seal_*` tests first and copy their fixture/setup shape exactly; the
assertion is `resp.status_code == 409` and the body names "review" or
"Finish".)

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/test_api.py -k seal -q`
Expected: FAIL — an unhandled `ReviewIncomplete` is a 500, not 409 (FastAPI
has no handler for it yet)

- [ ] **Step 3: implement** — in `app/main.py`, near the top, after the other
  module-level constants (`_BACKEND = get_backend()`):

```python
from app.review import sampling

# Validated once, at import -- a malformed SINDRI_AUDIT_RATE/MIN/MAX fails
# the boot loudly, the same contract SINDRI_MIN_FREE_MB already has in
# Runtime.from_env (app/batch/runtime.py), without adding a parameter to
# Worker or Runtime.
_AUDIT_CONFIG = sampling.audit_config_from_env()
```

Update `_header`:

```python
def _header(pdf_path):
    return build_header(pdf_path, _review_consent(), _AUDIT_CONFIG)
```

Find the existing exception handler registration (search for
`app.exception_handler` or the central `ReviewError` handler this file
already wires for `UnknownSession`/`StaleWriter`/`JournalGap`/
`JournalInvalid` — phase 1 added one generic handler keyed off
`ReviewError.status`, not per-exception-class ones; confirm which pattern
exists before editing). If it is a single generic handler keyed off
`isinstance(exc, ReviewError)` reading `exc.status`, **no change is needed**
— `ReviewIncomplete` is already a `ReviewError` subclass and will be mapped
to 409 automatically. Only add an explicit handler if the existing code
instead lists exception classes one by one.

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/test_api.py -k seal -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + ... + 1 passed, 2 skipped

```bash
git add app/main.py tests/test_api.py
git commit -m "$(cat <<'EOF'
api: validate the audit config at boot; wire ReviewIncomplete

SINDRI_AUDIT_RATE/MIN/MAX are parsed and validated once at import, the
same loud-boot contract SINDRI_MIN_FREE_MB already has.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Excel — the `Unsure` column

**Files:**
- Modify: `app/excel.py`
- Test: `tests/test_excel.py`

- [ ] **Step 1: write the failing test** — append to `tests/test_excel.py`:

```python
def test_unsure_column_reflects_the_row_s_unsure_field(tmp_path):
    rows = [
        Characteristic(pos=1, nominal="1", unsure=False),
        Characteristic(pos=2, nominal="2", unsure=True),
    ]
    out = tmp_path / "w.xlsx"
    write_workbook(rows, out)
    wb = load_workbook(out)
    ws = wb["Inspection"]
    header_row = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    assert "Unsure" in header_row
    col = header_row.index("Unsure") + 1
    assert ws.cell(3, col).value in ("", None, False)
    assert ws.cell(4, col).value is True or ws.cell(4, col).value == "Yes"
```

(Adjust the exact expected cell values once Step 3 is implemented, to match
whatever representation is chosen below — a plain boolean is simplest and
openpyxl writes it natively.)

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/test_excel.py -q`
Expected: FAIL — `"Unsure" in header_row` is False

- [ ] **Step 3: implement** — in `app/excel.py`:

```python
HEADERS = [
    ("Pos.", "Pos."),
    ("Merkmal", "Characteristic"),
    ("Nennmaß", "Nominal value"),
    ("O-TOL", "Upper-tol"),
    ("U-TOL", "Lower-tol"),
    ("Unsicher", "Unsure"),
]
```

```python
    ordered = sorted(rows, key=lambda c: c.pos)
    for i, c in enumerate(ordered, start=3):
        # Driven by `unsure` ONLY (design decision: never needs_review/
        # blind/audit, which are review-process bookkeeping that must
        # never leak into a client-facing export).
        values = [c.pos, c.char_type, c.nominal, c.upper_tol, c.lower_tol,
                 bool(c.unsure)]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(i, col, v)
            cell.alignment = _center
            cell.border = _border

    widths = [8, 18, 16, 12, 12, 10]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + col)].width = w
```

(Header row changes from `("Pos.", "Pos.")` through `("U-TOL", "Lower-tol")`
plus the new `("Unsicher", "Unsure")`; the `values`/`widths` lists both grow
by one element. Keep the English row's cell for this column reading
`"Unsure"` by using the EN half of the tuple, matching exactly how every
other header cell already does — check line 21-27's loop, which writes
`de`/`en` into two separate rows, so `ws.cell(2, 6).value == "Unsure"`.)

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/test_excel.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + ... + 1 passed, 2 skipped

```bash
git add app/excel.py tests/test_excel.py
git commit -m "$(cat <<'EOF'
excel: add the Unsure column

Driven by the sealed row's unsure field only -- the one state design
2026-10-07 says is "exported marked for escalation."

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: ballooned PDF — a distinct unsure style

**Files:**
- Modify: `app/pipeline/ballooned_pdf.py`
- Test: `tests/test_ballooned_pdf.py`

- [ ] **Step 1: write the failing test** — append to `tests/test_ballooned_pdf.py`
  (check this file's existing fixtures for a minimal `src_pdf`/rows setup and
  reuse it):

```python
def test_an_unsure_row_is_drawn_with_the_amber_dashed_style(tmp_path, monkeypatch, sample_pdf):
    calls = {"dashes": []}
    import fitz
    orig_draw_line = fitz.Page.draw_line

    def spy_draw_line(self, p1, p2, color=None, width=None, dashes=None):
        calls["dashes"].append(dashes)
        return orig_draw_line(self, p1, p2, color=color, width=width, dashes=dashes)
    monkeypatch.setattr(fitz.Page, "draw_line", spy_draw_line)

    rows = [Characteristic(pos=1, id="a", balloon_xy=(10, 10),
                           target_region=(5, 5, 20, 20), unsure=True)]
    out = render_ballooned_pdf(sample_pdf, rows, out_path=tmp_path / "out.pdf")
    assert any(d for d in calls["dashes"] if d)   # at least one dashed line drawn
```

(Adapt the fixture name `sample_pdf` to whatever this test file's existing
tests actually use for a minimal source PDF — check first.)

- [ ] **Step 2: run to confirm failure**

Run: `python -m pytest tests/test_ballooned_pdf.py -q`
Expected: FAIL — no call ever passes a non-empty `dashes`

- [ ] **Step 3: implement** — in `app/pipeline/ballooned_pdf.py`:

```python
_BLUE = (0.0, 0.3, 0.8)
_AMBER = (0.8, 0.55, 0.0)
_RADIUS = 9.0      # balloon radius in PDF points
_UNSURE_DASHES = "[3 2] 0"   # PyMuPDF dash spec: 3pt on, 2pt off


def render_ballooned_pdf(src_pdf, rows, dpi: int = 300, out_path=None, page_index: int = 0):
    out_path = Path(out_path)
    scale = dpi / 72.0
    doc = fitz.open(src_pdf)
    page = doc[page_index]
    rect = page.rect

    def to_pt(x, y):
        px = min(max(x / scale, rect.x0), rect.x1)
        py = min(max(y / scale, rect.y0), rect.y1)
        return fitz.Point(px, py)

    for c in rows:
        if not c.balloon_xy or not c.target_region:
            continue
        bx, by = c.balloon_xy
        tx = (c.target_region[0] + c.target_region[2]) / 2.0
        ty = (c.target_region[1] + c.target_region[3]) / 2.0
        b_pt, t_pt = to_pt(bx, by), to_pt(tx, ty)

        # unsure (design: "exported marked for escalation") gets its own
        # amber dashed style -- distinct from the tray's violet, which is
        # a UI-only convention for an unconfirmed suggestion and is never
        # exported at all.
        color = _AMBER if c.unsure else _BLUE
        dashes = _UNSURE_DASHES if c.unsure else None
        page.draw_line(b_pt, t_pt, color=color, width=1.0, dashes=dashes)
        page.draw_circle(b_pt, _RADIUS, color=color, width=1.5, dashes=dashes)
        page.insert_text(fitz.Point(b_pt.x - 5, b_pt.y + 4), str(c.pos),
                         fontsize=10, color=color)

    doc.save(out_path)
    doc.close()
    return out_path
```

- [ ] **Step 4: run to confirm pass**

Run: `python -m pytest tests/test_ballooned_pdf.py -q`
Expected: PASS

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: baseline + ... + 1 passed, 2 skipped

```bash
git add app/pipeline/ballooned_pdf.py tests/test_ballooned_pdf.py
git commit -m "$(cat <<'EOF'
pdf: draw an unsure row with a distinct amber dashed balloon

Not the tray's violet -- a suggestion is never exported at all, so
reusing its style on an exported, reviewed, escalated row would read
as "still just a suggestion."

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

> **Milestone: server-complete** - state must be checkpointed here before continuing.

---

## Milestone 3: JS logic parity

### Task 12: `app/static/js/values.js` + node/pytest fixture parity

**Files:**
- Create: `app/static/js/values.js`
- Create: `tests/js/values.test.mjs`

- [ ] **Step 1: write the failing test** — create `tests/js/values.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { canonValue, valuesEqual } from '../../app/static/js/values.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(
  join(__dirname, '..', 'review', 'fixtures', 'value_normalisation_cases.json'), 'utf8'));

test('values.js agrees with the shared fixture', () => {
  for (const c of fixture) {
    assert.equal(valuesEqual(c.a, c.b), c.equal, JSON.stringify(c));
  }
});

test('canonValue of null/undefined is empty string', () => {
  assert.equal(canonValue(null), '');
  assert.equal(canonValue(undefined), '');
});
```

- [ ] **Step 2: run to confirm failure**

Run: `node --test tests/js/values.test.mjs`
Expected: FAIL — `Cannot find module '../../app/static/js/values.js'`

- [ ] **Step 3: implement** — create `app/static/js/values.js`:

```js
// Pure value normalisation for the blind-verify panel's live compare
// (docs/plans/2026-10-07-hitl-review-grading-design.md §1/§3). Mirrors
// app/review/values.py's canon_value field for field; pinned against it
// by tests/review/fixtures/value_normalisation_cases.json (this file's own
// test and tests/review/test_values.py) -- one contract, three readers,
// the shape tests/review/test_journal_replay_parity.py already uses for
// the journal.

function tryDecimal(s) {
  const collapsed = s.split(/\s+/).join(' ').trim();
  const t = collapsed.replace(/,/g, '.').replace(/^\+/, '').replace(/^±/, '');
  if (t === '') return null;
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  return t;
}

// parseFloat, not a bignum library: every value in this domain is a short,
// human-typed number (a nominal or a tolerance), so float precision is
// enough once the SAME normalisation (zero sign, trailing zeros via
// String(Number(...))) applies to both sides being compared.
export function canonValue(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  const t = tryDecimal(s);
  if (t !== null) {
    let n = Number(t);
    if (n === 0) n = 0;   // sign of zero: "-0" reads as "0"
    return String(n);
  }
  return s.split(/\s+/).join(' ').trim().toLowerCase();
}

export function valuesEqual(a, b) {
  return canonValue(a) === canonValue(b);
}
```

- [ ] **Step 4: run to confirm pass**

Run: `node --test tests/js/values.test.mjs`
Expected: PASS (2 tests)

- [ ] **Step 5: full node + pytest run + commit**

Run: `node --test "tests/js/*.test.mjs"`
Expected: 272 + 2 = 274 (reconfirm the exact prior count from
`tests/test_js_state.py`'s last known-good run before trusting this
arithmetic)

Run: `python -m pytest -q`
Expected: unchanged from Task 11 (this task adds no new `.py` test)

```bash
git add app/static/js/values.js tests/js/values.test.mjs
git commit -m "$(cat <<'EOF'
ui: add values.js, the JS mirror of app/review/values.py

The blind-verify panel's live "does this match?" compare needs this in
the browser now; pinned against the Python side by one shared fixture.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: `state.js` — widened `progress()`, new ops, `opFromEvent`

**Data-integrity: yes** (must stay byte-for-byte aligned with
`ReviewStore.progress()`/`seal()` or `canFinish()` lies to the reviewer).

**Files:**
- Modify: `app/static/js/state.js`
- Test: `tests/js/state.test.mjs`

- [ ] **Step 1: write the failing tests** — append to `tests/js/state.test.mjs`
  (reuse this file's existing `setSession`/`apply` helpers and row-shape
  conventions — read the top of the file first):

```js
test('progress() counts a blind row as outstanding', () => {
  setSession({ rows: [
    row('a', { needs_review: false, blind: true, audit: true }),
    row('b', { needs_review: false }),
  ], events: [] });
  const p = progress();
  assert.equal(p.total, 1);
  assert.equal(p.unchecked, 1);
});

test('opMarkUnsure / opUnmarkUnsure toggle unsure and resolve the queue', () => {
  setSession({ rows: [row('a', { needs_review: true })], events: [] });
  apply(opMarkUnsure('a'));
  assert.equal(state.rows[0].unsure, true);
  assert.equal(progress().outstanding, 0);
  apply(opUnmarkUnsure('a'));
  assert.equal(state.rows[0].unsure, false);
  assert.equal(progress().outstanding, 1);
});

test('opBlindSubmit resolves a blind row without changing its values', () => {
  setSession({ rows: [row('a', { needs_review: false, blind: true, audit: true, nominal: '20' })], events: [] });
  apply(opBlindSubmit('a', { char_type: '', nominal: '20', upper_tol: '', lower_tol: '' }, 'match'));
  assert.equal(state.rows[0].nominal, '20');
  assert.equal(progress().outstanding, 0);
});

test('opFromEvent reconstructs the three new op types', () => {
  const a = opFromEvent({ type: 'mark_unsure', id: 'a' });
  const b = opFromEvent({ type: 'unmark_unsure', id: 'a' });
  const c = opFromEvent({
    type: 'blind_submit', id: 'a', outcome: 'match',
    entered: { char_type: '', nominal: '1', upper_tol: '', lower_tol: '' },
  });
  assert.ok(a && b && c);
});
```

(`row(id, extra)` is almost certainly already a helper in this test file —
check before adding a second one; if absent, add a minimal local one
matching `tests/js/fuzz_journal.mjs`'s `row()` shape.)

- [ ] **Step 2: run to confirm failure**

Run: `node --test tests/js/state.test.mjs`
Expected: FAIL — `opMarkUnsure is not a function` and similar

- [ ] **Step 3: implement** — in `app/static/js/state.js`:

```js
export function opMarkUnsure(id) {
  return { type: 'mark_unsure', id };
}
export function opUnmarkUnsure(id) {
  return { type: 'unmark_unsure', id };
}
export function opBlindSubmit(id, entered, outcome) {
  return { type: 'blind_submit', id, entered, outcome };
}
```

Extend `apply(op)`'s dispatch (find the existing `switch`/`if` chain over
`op.type` that already handles `add_row`/`delete_row`/`move_row`/
`edit_cell`/`accept`/`unaccept`/`confirm_suggestions`, and add branches in
the same style — e.g. if it is an `if/else if` chain):

```js
  } else if (op.type === 'mark_unsure') {
    const r = state.rows.find((x) => x.id === op.id);
    if (r) r.unsure = true;
  } else if (op.type === 'unmark_unsure') {
    const r = state.rows.find((x) => x.id === op.id);
    if (r) r.unsure = false;
  } else if (op.type === 'blind_submit') {
    const r = state.rows.find((x) => x.id === op.id);
    if (r) r.blind_resolved = true;
  }
```

Extend `opFromEvent` (the existing function reconstructing an `op` from a
journal event on reload) with matching cases:

```js
  if (e.type === 'mark_unsure') return opMarkUnsure(e.id);
  if (e.type === 'unmark_unsure') return opUnmarkUnsure(e.id);
  if (e.type === 'blind_submit') return opBlindSubmit(e.id, e.entered, e.outcome);
```

Update `progress()` to match `ReviewStore.progress()` exactly:

```js
// The review queue (Phase 2: flagged balloons AND blind rows -- an audit
// row or a blind-flagged sample row must be resolved through the
// verify panel, or marked unsure, exactly like a shown flagged row).
// Unflagged, non-blind balloons are counted apart as `unchecked`: the
// system accepted them and nobody was asked to look. Suggestions are
// neither.
export function progress() {
  const balloons = state.rows.filter((r) => !r.suggested);
  const queue = balloons.filter((r) => r.needs_review || r.blind);
  const resolved = queue.filter((r) => r.unsure || r.reviewed || r.blind_resolved).length;
  return { resolved, total: queue.length, outstanding: queue.length - resolved,
           unchecked: balloons.length - queue.length };
}
```

Also check `restoreSession`/`setSession`'s row-defaulting (wherever a raw
server row is turned into `state.rows` entries) and add the same defaults
`replay.py` uses: `unsure: !!r.unsure`, `blind_resolved: false` (a reload
always starts a row's `blind_resolved` at `false` and lets the restored
events — including any retained `blind_submit` — set it back to `true`
during replay, exactly mirroring `app/review/replay.py`'s own
`"blind_resolved": False` row initialisation).

- [ ] **Step 4: run to confirm pass**

Run: `node --test tests/js/state.test.mjs`
Expected: PASS

- [ ] **Step 5: full node + pytest + fuzz-parity run**

Run: `node --test "tests/js/*.test.mjs"`
Expected: previous count + 4

Run: `python -m pytest -q`
Expected: unchanged (no new `.py` tests yet; Task 14 extends the fuzz)

```bash
git add app/static/js/state.js tests/js/state.test.mjs
git commit -m "$(cat <<'EOF'
ui: widen progress() to blind rows; add the three new ops

state.js now mirrors ReviewStore.progress() exactly: an audit row or a
blind-flagged row joins the queue and must be resolved (or marked
unsure) before canFinish() is true.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: extend the replay-parity fuzz with the new ops

**Data-integrity: yes** (the cross-language contract the whole journal
depends on; CLAUDE.md: "if it fails, state.js's ops and app/review/replay.py
have drifted apart").

**Files:**
- Modify: `tests/js/fuzz_journal.mjs`
- Modify: `tests/review/test_journal_replay_parity.py`

- [ ] **Step 1: extend `_TYPE_KEYS`** in
  `tests/review/test_journal_replay_parity.py`:

```python
_TYPE_KEYS = {
    "edit_cell": {"id", "field", "old", "new"},
    "move_row": {"id", "xy"},
    "delete_row": {"id"},
    "add_row": {"row"},
    "accept": {"ids"},
    "unaccept": {"ids"},
    "confirm_suggestions": {"ids"},
    "retract": {"target"},
    "mark_unsure": {"id"},
    "unmark_unsure": {"id"},
    "blind_submit": {"id", "entered", "outcome"},
}
```

- [ ] **Step 2: run to confirm the fuzz still passes on existing ops**
  (sanity check before touching the generator)

Run: `python -m pytest tests/review/test_journal_replay_parity.py -q`
Expected: PASS (the Python-side dict change alone does not yet make the
generator emit the new types, so this just confirms nothing broke)

- [ ] **Step 3: extend the JS generator** — in `tests/js/fuzz_journal.mjs`,
  find `randomSteps`'s `if/else if` chain over `rnd()` ranges (the block
  shown partially in this plan's research: `r < 0.18` undo, `r < 0.30` redo,
  `r < 0.45` edit_cell, ...) and add branches for the new ops, each calling
  `S.apply(S.opX(...))` and pushing a matching `label`, following the exact
  pattern the existing branches use (read the rest of the function before
  editing — it also updates `log` and `addN` bookkeeping per branch, which
  the new branches must do too, consistently with whichever existing branch
  they most resemble: `mark_unsure`/`unmark_unsure` resemble the `accept`
  branch's "pick an existing id" shape; `blind_submit` additionally needs an
  `entered` object and one of the four outcome strings, generated with
  `pick(['match', 'kept_system', 'kept_entered', 'edited'])`):

```js
    else if (r < 0.55) {
      const id = pick(anyIds);
      S.apply(S.opMarkUnsure(id)); label = `mark_unsure ${id}`;
    }
    else if (r < 0.60) {
      const id = pick(anyIds);
      S.apply(S.opUnmarkUnsure(id)); label = `unmark_unsure ${id}`;
    }
    else if (r < 0.65) {
      const id = pick(anyIds);
      const outcome = pick(['match', 'kept_system', 'kept_entered', 'edited']);
      const entered = { char_type: 'Distance', nominal: String(Math.floor(rnd() * 9)),
                        upper_tol: '', lower_tol: '' };
      S.apply(S.opBlindSubmit(id, entered, outcome));
      label = `blind_submit ${id} ${outcome}`;
    }
```

(Insert this immediately after the existing `r < 0.45` edit_cell branch and
before whatever branch currently starts at `0.45` — renumber the existing
later thresholds so every original branch's RELATIVE width is preserved and
the new branches simply claim additional probability mass; do not change
which absolute threshold any PRE-EXISTING branch keyed off of other than
shifting it later in the chain, since the fixed seed's reproducibility
depends on the generator being internally consistent, not on matching this
plan's exact numbers.)

- [ ] **Step 4: run to confirm the extended fuzz passes**

Run: `python -m pytest tests/review/test_journal_replay_parity.py -q`
Expected: PASS — both tests, including
`test_a_review_rebuilt_from_the_server_matches_and_keeps_replaying`, which
exercises undo/redo and reopen against the new ops too since it reuses the
same generator

- [ ] **Step 5: full suite + commit**

Run: `python -m pytest -q`
Expected: unchanged test count (no new tests added, existing ones now cover
more ground)

Run: `node --test "tests/js/*.test.mjs"`
Expected: unchanged count (fuzz_journal.mjs is explicitly not a `*.test.mjs`
file, per its own docstring, so it is not globbed)

```bash
git add tests/js/fuzz_journal.mjs tests/review/test_journal_replay_parity.py
git commit -m "$(cat <<'EOF'
review: extend the replay-parity fuzz to mark_unsure/unmark_unsure/blind_submit

The JS<->Python replay contract must cover every op journal.py accepts,
or a future drift between state.js and replay.py on these three types
would pass unnoticed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

> **Milestone: js-parity-complete** - state must be checkpointed here before continuing.

---

## Milestone 4: reviewer UI, docs, drive

### Task 15: `queue.js` — pure queue-ordering and reason-chip helpers

**Files:**
- Create: `app/static/js/queue.js`
- Test: `tests/js/queue.test.mjs` (new)

- [ ] **Step 1: write the failing tests** — create `tests/js/queue.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { queueRows, EDIT_REASONS, DELETE_REASONS } from '../../app/static/js/queue.js';

function row(id, extra = {}) {
  return { id, pos: 0, suggested: false, needs_review: false, blind: false,
          reviewed: false, unsure: false, blind_resolved: false, ...extra };
}

test('queueRows returns flagged and blind rows, in pos order, excluding suggestions and unchecked rows', () => {
  const rows = [
    row('c', { pos: 3, suggested: true, needs_review: true }),
    row('a', { pos: 1, needs_review: true }),
    row('b', { pos: 2, blind: true }),
    row('d', { pos: 4 }),   // plain unflagged, non-blind -- unchecked, not queued
  ];
  assert.deepEqual(queueRows(rows).map((r) => r.id), ['a', 'b']);
});

test('queueRows excludes an already-resolved row', () => {
  const rows = [
    row('a', { pos: 1, needs_review: true, reviewed: true }),
    row('b', { pos: 2, needs_review: true }),
  ];
  assert.deepEqual(queueRows(rows).map((r) => r.id), ['b']);
});

test('reason vocabularies match the server\'s closed lists', () => {
  assert.deepEqual([...EDIT_REASONS].sort(), [
    'misread_digit', 'other', 'wrong_char_type', 'wrong_sign_or_unit', 'wrong_tolerance',
  ]);
  assert.deepEqual([...DELETE_REASONS].sort(), [
    'duplicate', 'not_a_characteristic', 'other', 'unballooned_real_dimension', 'wrong_region',
  ]);
});
```

- [ ] **Step 2: run to confirm failure**

Run: `node --test tests/js/queue.test.mjs`
Expected: FAIL — `Cannot find module '../../app/static/js/queue.js'`

- [ ] **Step 3: implement** — create `app/static/js/queue.js`:

```js
// Pure helpers for the review queue (docs/plans/2026-10-07-hitl-review-
// grading-design.md §1): flagged rows plus blind rows, in reading order
// (pos), the SAME set state.js's progress() counts as outstanding --
// queueRows must never diverge from progress()'s own `queue` filter.

export function queueRows(rows) {
  return rows
    .filter((r) => !r.suggested && (r.needs_review || r.blind))
    .filter((r) => !(r.unsure || r.reviewed || r.blind_resolved))
    .sort((a, b) => a.pos - b.pos);
}

// Mirrors app/review/journal.py's EDIT_REASONS/DELETE_REASONS exactly --
// the UI never sends a reason the server's closed vocabulary would reject.
export const EDIT_REASONS = new Set([
  'misread_digit', 'wrong_tolerance', 'wrong_char_type',
  'wrong_sign_or_unit', 'other',
]);
export const DELETE_REASONS = new Set([
  'duplicate', 'not_a_characteristic', 'unballooned_real_dimension',
  'wrong_region', 'other',
]);

export const EDIT_REASON_LABELS = {
  misread_digit: 'Misread digit',
  wrong_tolerance: 'Wrong/missing tolerance',
  wrong_char_type: 'Wrong characteristic type',
  wrong_sign_or_unit: 'Wrong sign or unit',
  other: 'Other',
};
export const DELETE_REASON_LABELS = {
  duplicate: 'Duplicate of another balloon',
  not_a_characteristic: 'Not a characteristic (text, title block…)',
  unballooned_real_dimension: "Real dimension, but we don't balloon it",
  wrong_region: 'Wrong region / misplaced box',
  other: 'Other',
};
```

- [ ] **Step 4: run to confirm pass**

Run: `node --test tests/js/queue.test.mjs`
Expected: PASS

- [ ] **Step 5: full node run + commit**

Run: `node --test "tests/js/*.test.mjs"`
Expected: previous count + 3

```bash
git add app/static/js/queue.js tests/js/queue.test.mjs
git commit -m "$(cat <<'EOF'
ui: add queue.js — pure queue ordering and the closed reason vocabularies

queueRows mirrors progress()'s own filter exactly (same outstanding set);
the reason constants mirror journal.py's server-side vocabularies so the
UI never offers a chip the server would reject.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: `shortcuts.js` — per-row accept, `U`, and reason-popover key scoping

**Files:**
- Modify: `app/static/js/shortcuts.js`
- Modify: `app/static/js/table.js` (per-row accept target, verify cell, reason
  popover, unsure marker)
- Test: `tests/js/table.test.mjs` (new — check whether `table.js` already has
  a test file under a different name before creating one; if `tests/js/` has
  no `table.test.mjs` today, this is the first)

This task is UI wiring, not a pure-logic module — write it as DOM-level node
tests using whatever light DOM stub the existing `tests/js/*.test.mjs` suite
already uses for `table.js`/`main.js` (check `tests/js/finish.test.mjs` and
`tests/js/reopen.test.mjs` first, since `finish.js`/`reviewbar.js` are also
UI-adjacent and already have tests — follow their harness exactly rather than
introducing a second one).

- [ ] **Step 1: write the failing tests** — add to the chosen test file
  (behavioural specs the implementer must make pass; the exact DOM-harness
  boilerplate must match the sibling test file's existing setup):

```js
test('Y/Enter on the selected row in the queue accepts that row only, not the whole visible list', () => {
  // Set up two flagged, unreviewed rows; select one; fire the keyboard
  // shortcut; assert only the selected row's `reviewed` flipped true and
  // the OTHER flagged row is untouched.
});

test('bulk accept never accepts a flagged row, blind or shown', () => {
  // Set up one flagged-unflagged-mixed table; trigger the Accept button
  // with no explicit selection; assert acceptTargets().review excludes
  // every row with needs_review true (already true pre-phase-2 for SHOWN
  // flagged rows -- this test's new assertion is that a blind-flagged row
  // is excluded the same way, since it also carries needs_review: true).
});

test('a blind row renders a "verify" cell instead of its real value', () => {
  // Render a row with blind: true, nominal: "20"; assert the rendered
  // cell's textContent is the literal string "verify", not "20".
});

test('U marks the selected row unsure and un-marks it on a second press', () => {
});

test('the reason popover appears after a committed edit, and keys 1-5 only act while it is open', () => {
  // Commit an edit_cell on a row; assert a popover element appears;
  // press "1" and assert the NEXT apply() call is an edit_cell retract +
  // a new edit_cell carrying reason: "misread_digit" OR (simpler,
  // preferred) that the journal's last-sent event for that edit now
  // carries `reason: "misread_digit"` -- whichever the implementation
  // actually does, pick ONE mechanism and test that, not both: an edit's
  // reason should attach to the edit_cell event ITSELF (added as a
  // `reason` field when the popover resolves, sent in the SAME flush
  // batch if the popover resolves before the debounce fires, or as a
  // follow-up amendment otherwise -- state this choice explicitly in the
  // implementation and keep it consistent with journal.py's validate_event,
  // which reads `reason` directly off the edit_cell/delete_row event, not
  // off a separate event type).
  // Press any OTHER key, or click elsewhere, and assert the popover closes
  // with NO reason sent and no global shortcut (e.g. filter pill "1")
  // fired while it was open.
});
```

- [ ] **Step 2: run to confirm failure**

Run: `node --test <the chosen test file>`
Expected: FAIL on every new test (the behaviour does not exist yet)

- [ ] **Step 3: implement.**

In `app/static/js/table.js`'s `renderRows()`, change the editable-cell loop
so a blind row's cell renders `verify` and is not directly editable inline
(it opens the verify panel instead — Task 17 builds the panel; this task
only changes the TABLE cell's rendering and click target):

```js
    for (const k of COLS) {
      const td = document.createElement('td');
      if (r.blind) {
        // Blinding is UX-level (design §1): the value is in `r[k]` but
        // never rendered. Clicking opens the verify panel (Task 17) --
        // inline edit is disabled for this cell.
        td.textContent = 'verify';
        td.className = 'verify-cell';
        td.contentEditable = 'false';
        td.addEventListener('click', () => emit('open-verify-panel', r.id));
        tr.appendChild(td);
        continue;
      }
      td.contentEditable = 'true';
      // ... existing body unchanged ...
```

Add the unsure marker next to the existing review-dot (in the `pos` cell
block):

```js
    if (r.unsure) {
      const u = document.createElement('span');
      u.className = 'unsure-marker';
      u.title = 'Marked unsure';
      u.textContent = '?';
      tdP.appendChild(u);
    }
```

Change `acceptTargets`/`acceptRows` so a row carrying `needs_review` is
**never** in `t.review` regardless of selection — re-read the existing
function: `ids.filter((id) => !byId(id)?.suggested)` currently includes a
flagged row when it IS explicitly selected (ticked), which the ORIGINAL
per-row accept already allowed via the checkbox + bulk Accept button. Phase
2 changes this: "bulk accept... never accepts a flagged row" applies even to
an explicit selection now, and per-row accept is instead the dedicated
`Y`/`Enter`-on-the-selected-row shortcut. Update `acceptTargets`:

```js
export function acceptTargets(selectedIds) {
  const byId = (id) => state.rows.find((r) => r.id === id);
  const explicit = selectedIds.length > 0;
  const ids = explicit
    ? selectedIds
    : state.rows.filter(isVisibleRow).filter((r) => !r.reviewed).map((r) => r.id);
  const allowSugg = explicit || state.filter === 'suggested';
  const suggested = ids.filter((id) => byId(id)?.suggested);
  return {
    confirm: allowSugg ? suggested : [],
    // Phase 2: a flagged row (needs_review) is NEVER a bulk/explicit
    // accept target, blind or shown -- per-row accept (acceptSelectedRow,
    // below) is the only way to resolve one.
    review: ids.filter((id) => {
      const r = byId(id);
      return r && !r.suggested && !r.needs_review;
    }),
  };
}

// The dedicated per-row accept (Y/Enter on the selected row in the queue):
// accepts ONLY that row, and only a SHOWN flagged row (not blind -- a
// blind row resolves through the verify panel's Enter, never this path).
export function acceptSelectedRow(id) {
  const r = state.rows.find((x) => x.id === id);
  if (!r || r.suggested || !r.needs_review || r.blind) return false;
  apply(opBulkReview([id], true));
  return true;
}
```

In `app/static/js/shortcuts.js`, replace the existing bare `Y` branch and
add `U`:

```js
    // Per-row accept on the row selected in the queue (Y/Enter) -- NOT
    // the same as bulk Accept (plain Y used to also do per-row accept
    // when nothing was ticked; phase 2 splits them: bulk never touches a
    // flagged row at all now, see acceptTargets).
    if ((e.key.toLowerCase() === 'y' || e.key === 'Enter') && !e.ctrlKey && !e.metaKey) {
      if (state.selectedId && acceptSelectedRow(state.selectedId)) {
        e.preventDefault();
        return;
      }
    }
    if (e.key.toLowerCase() === 'y' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      acceptRows([]);
    }
    if (e.key.toLowerCase() === 'u' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (state.selectedId) toggleUnsure(state.selectedId);
    }
```

Add `toggleUnsure` to `table.js` (or `shortcuts.js`, whichever already
imports `apply`/`opMarkUnsure` most naturally — `table.js` already imports
from `state.js`, so put it there and export it):

```js
export function toggleUnsure(id) {
  const r = state.rows.find((x) => x.id === id);
  if (!r) return;
  apply(r.unsure ? opUnmarkUnsure(id) : opMarkUnsure(id));
}
```

For the reason popover: add a small, self-contained popover controller in
`table.js` (fired after a committed `edit_cell` in the existing `td`
`blur` handler, and after a committed `delete_row` wherever that is
triggered today — locate it, likely a toolbar/keyboard `Del` handler).
Attach the reason to the event by extending `opEditCell`/`opDeleteRow`'s
call sites to accept an optional `reason`, threaded through to
`state.js`'s `opEditCell(id, field, newValue, reason)` /
`opDeleteRow(id, reason)`, which must merge it into the emitted op object
(`{ type: 'edit_cell', id, field, old, new, reason }`, omitting the key
entirely when `reason` is undefined so `validate_event`'s `reason is not
None` check behaves identically to the Python side's `e.get("reason")`
returning `None`). The popover itself:

```js
// app/static/js/table.js, near the bottom
import { EDIT_REASON_LABELS, DELETE_REASON_LABELS } from './queue.js';

let _activeReasonPopover = null;

function openReasonPopover(anchorEl, labels, onPick) {
  closeReasonPopover();
  const el = document.createElement('div');
  el.className = 'reason-popover';
  let i = 1;
  for (const [key, label] of Object.entries(labels)) {
    const btn = document.createElement('button');
    btn.textContent = `${i} · ${label}`;
    btn.dataset.key = key;
    btn.addEventListener('click', () => { onPick(key); closeReasonPopover(); });
    el.appendChild(btn);
    i++;
  }
  anchorEl.appendChild(el);
  _activeReasonPopover = { el, labels: Object.keys(labels), onPick };
  document.addEventListener('keydown', _reasonPopoverKeydown, { capture: true });
  document.addEventListener('click', _reasonPopoverOutsideClick, { capture: true });
}

function closeReasonPopover() {
  if (!_activeReasonPopover) return;
  _activeReasonPopover.el.remove();
  _activeReasonPopover = null;
  document.removeEventListener('keydown', _reasonPopoverKeydown, { capture: true });
  document.removeEventListener('click', _reasonPopoverOutsideClick, { capture: true });
}

function _reasonPopoverKeydown(e) {
  if (!_activeReasonPopover) return;
  // Scoped to 1-5 while open -- this STOPS the global shortcut handler
  // (shortcuts.js's filter-pill 1-4) from also firing, by capturing and
  // stopping propagation before it reaches that listener.
  const idx = Number(e.key) - 1;
  if (idx >= 0 && idx < _activeReasonPopover.labels.length) {
    e.stopPropagation();
    e.preventDefault();
    _activeReasonPopover.onPick(_activeReasonPopover.labels[idx]);
    closeReasonPopover();
  } else {
    closeReasonPopover();   // any OTHER key dismisses with no reason
  }
}

function _reasonPopoverOutsideClick(e) {
  if (_activeReasonPopover && !_activeReasonPopover.el.contains(e.target)) {
    closeReasonPopover();
  }
}
```

Wire it into the existing `td` blur handler (edit commit) and the delete
path:

```js
      td.addEventListener('blur', () => {
        const newVal = td.textContent.trim();
        if (newVal === td.dataset._orig) return;
        apply(opEditCell(r.id, k, newVal));
        openReasonPopover(td, EDIT_REASON_LABELS,
          (reason) => amendLastReason(r.id, k, reason));
      });
```

(`amendLastReason` is a small helper that re-emits the SAME logical edit
with `reason` attached, as this plan's Task 16 test comment above
discusses — because the original `opEditCell` call already applied and
queued the event without a reason, the simplest correct mechanism is: do
NOT apply the edit immediately on blur; instead hold the pending `{id,
field, newVal}` and apply `opEditCell(id, field, newVal, reason)` (or
`opEditCell(id, field, newVal, undefined)` if the popover is dismissed)
only once the popover resolves or dismisses. Implement it this way —
apply-on-popover-resolution, not apply-then-amend — since `journal.js`
already numbers and queues events on `apply()`, and a dismiss must still
commit the edit immediately with no reason, not wait indefinitely.)

- [ ] **Step 4: run to confirm pass**

Run: `node --test <the chosen test file>`
Expected: PASS

- [ ] **Step 5: full node + pytest run + commit**

Run: `node --test "tests/js/*.test.mjs"`
Expected: previous count + however many tests Step 1 added

Run: `python -m pytest -q`
Expected: unchanged (this task is JS-only)

```bash
git add app/static/js/table.js app/static/js/shortcuts.js app/static/js/state.js
git commit -m "$(cat <<'EOF'
ui: per-row accept, the verify cell, U (unsure), and the reason popover

Bulk/explicit accept now never touches a flagged row, blind or shown;
Y/Enter on the selected queue row is the only way to resolve one. The
reason popover commits an edit/delete only once it resolves or is
dismissed, so "no reason" and "a chosen reason" are the same code path,
not an edit amended after the fact.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: the blind-verify panel

**Files:**
- Modify: `app/static/js/table.js` (or a new `app/static/js/verifypanel.js` if
  `table.js` is already large — check its current line count first; if it is
  pushing 400+ lines, prefer a new file importing from `table.js`/`state.js`,
  matching this codebase's "prefer smaller, focused files" convention)
- Modify: `app/static/js/main.js` (listens for the `open-verify-panel` event
  emitted by Task 16's blind-cell click, shows/hides the panel DOM)
- Test: the same test file Task 16 used, extended

- [ ] **Step 1: write the failing tests**

```js
test('the verify panel starts with all four fields empty regardless of the system value', () => {
});

test('Enter with an entry equal to the system value (under valuesEqual) commits outcome "match" and resolves the row', () => {
});

test('Enter with a mismatching entry shows the system/entered values side by side and waits for 1/2/E', () => {
});

test('"1" after a mismatch keeps the system value, outcome "kept_system"', () => {
});

test('"2" after a mismatch keeps the entered value, outcome "kept_entered", and commits an edit_cell for every changed field', () => {
});

test('U from within the panel marks the row unsure and closes the panel without a blind_submit', () => {
});
```

- [ ] **Step 2: run to confirm failure**

Run: `node --test <test file>`
Expected: FAIL — the panel does not exist yet

- [ ] **Step 3: implement.** Core comparison logic (pure, testable without
  DOM) — add to `app/static/js/queue.js` or a sibling pure module:

```js
import { valuesEqual } from './values.js';

const VALUE_FIELDS = ['char_type', 'nominal', 'upper_tol', 'lower_tol'];

// Pure decision for the blind-verify panel's Enter (design §1): compares
// every VALUE_FIELDS entry under valuesEqual, the SAME normaliser the
// server re-checks a "match" claim against (app/review/values.py) -- so a
// client-side "match" can never be refused by the server's own check.
export function compareBlindEntry(systemRow, entered) {
  const allMatch = VALUE_FIELDS.every((f) => valuesEqual(systemRow[f] ?? '', entered[f] ?? ''));
  return allMatch ? { outcome: 'match' } : {
    outcome: null,   // undecided -- the reviewer still must pick 1/2/E
    mismatchFields: VALUE_FIELDS.filter((f) => !valuesEqual(systemRow[f] ?? '', entered[f] ?? '')),
  };
}
```

DOM wiring in `main.js`: on `open-verify-panel`, render a modal/panel with
four empty inputs for `VALUE_FIELDS`, bound to the row's crop (reuse
whatever existing zoom-to-crop function the row-click handler already
calls — `flashAndCenter`, seen in `table.js`). On Enter:

```js
function submitVerify(row, entered) {
  const cmp = compareBlindEntry(row, entered);
  if (cmp.outcome === 'match') {
    apply(opBlindSubmit(row.id, entered, 'match'));
    closeVerifyPanel();
    return;
  }
  showMismatch(row, entered, cmp.mismatchFields, (choice) => {
    if (choice === 'keep_system') {
      apply(opBlindSubmit(row.id, entered, 'kept_system'));
    } else if (choice === 'keep_entered') {
      for (const f of VALUE_FIELDS) {
        if (entered[f] !== row[f]) apply(opEditCell(row.id, f, entered[f]));
      }
      apply(opBlindSubmit(row.id, entered, 'kept_entered'));
    } else if (choice === 'edit') {
      // Opens the normal inline-edit path on the now-revealed row instead
      // of closing -- the reviewer corrects a field by hand, then Enter
      // again re-runs submitVerify with the edited entry.
      revealForEdit(row);
      return;
    }
    closeVerifyPanel();
  });
}
```

Wire `U` from within the panel to `toggleUnsure(row.id)` followed by
`closeVerifyPanel()`, with NO `blind_submit` emitted (an unsure row is
resolved by `unsure` alone, per `_unresolved_ids`/`progress()`'s OR
condition — it never needs a `blind_resolved` as well).

- [ ] **Step 4: run to confirm pass**

Run: `node --test <test file>`
Expected: PASS

- [ ] **Step 5: full node + pytest + commit**

Run: `node --test "tests/js/*.test.mjs"`
Expected: previous count + however many Step 1 added

Run: `python -m pytest -q`
Expected: unchanged

```bash
git add app/static/js/main.js app/static/js/table.js app/static/js/queue.js
git commit -m "$(cat <<'EOF'
ui: the blind-verify panel

compareBlindEntry uses the same values.js normaliser the server checks a
"match" claim against, so a client-side match can never be refused
server-side. U from the panel resolves a row via unsure alone, with no
blind_submit -- unsure and blind_resolved are independent resolution
paths, never both required.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

> **Milestone: ui-complete** - state must be checkpointed here before continuing.

---

### Task 18: Finish tooltip, progress bar copy, compose env, CLAUDE.md

**Files:**
- Modify: `app/static/js/finish.js` callers in `main.js` (tooltip text only —
  `finish.js` itself needs no change, per the plan's Architecture note: it
  already calls the injected `canFinish`)
- Modify: `app/static/js/table.js`'s `renderCounts()` (progress bar copy)
- Modify: `docker-compose.yml`
- Modify: `CLAUDE.md`
- Test: `tests/js/finish.test.mjs` (extend, if the tooltip logic is pure
  enough to extract — otherwise a thin DOM test alongside Task 16/17's file)

- [ ] **Step 1: write the failing test** — a pure helper for the tooltip
  text, in whichever file already holds `nextReadyCopy`-style pure UI-text
  helpers (`reviewbar.js` has the closest precedent):

```js
test('finishTooltip names the outstanding count and offers a jump', () => {
  const p = { resolved: 2, total: 5, outstanding: 3, unchecked: 10 };
  assert.equal(finishTooltip(p), '3 row(s) still need review before you can finish.');
});

test('finishTooltip is empty once nothing is outstanding', () => {
  assert.equal(finishTooltip({ resolved: 5, total: 5, outstanding: 0, unchecked: 10 }), '');
});
```

- [ ] **Step 2: run to confirm failure**

Run: `node --test <test file>`
Expected: FAIL — `finishTooltip is not a function`

- [ ] **Step 3: implement** — add to `app/static/js/queue.js`:

```js
export function finishTooltip(progress) {
  if (progress.outstanding === 0) return '';
  return `${progress.outstanding} row(s) still need review before you can finish.`;
}
```

Wire it into `main.js`'s Finish button (wherever `canFinish()`/`progress()`
is already read to enable/disable the button — set `title =
finishTooltip(progress())` there, and the "jump to first" affordance per
phase 1's state notes is "the switch to the Review filter," unchanged).

Update `renderCounts()` in `table.js` to read the widened `progress()` and
show "N/M resolved" plus "K unchecked (system-accepted)" per the design's
copy, replacing whatever the current pre-phase-2 label reads (read the
function's current body first; it is small, driven by `counts()`/`progress()`
already).

Add to `docker-compose.yml`, next to the existing commented
`SINDRI_RETENTION_DAYS`/`SINDRI_ACCESS_TOKEN`:

```yaml
      # HITL phase 2 (docs/plans/2026-10-09-hitl-phase2-forcing-plan.md):
      # audit sampling of unflagged rows, and an equal-rate blind sample of
      # flagged rows, per drawing. Defaults: rate 0.25, min 1, max 5 (0 =
      # uncapped). A malformed value fails the boot loudly.
      # - SINDRI_AUDIT_RATE=0.25
      # - SINDRI_AUDIT_MIN=1
      # - SINDRI_AUDIT_MAX=5
```

Append to `CLAUDE.md`, directly after the existing "BATCH B4 SHIPPED"
paragraph (keep the existing paragraphs above it byte-for-byte unchanged;
add this as a new paragraph):

```markdown
**HITL PHASE 2 SHIPPED: THE REVIEW QUEUE, PER-ROW ACCEPT, AND THE
BLIND-VERIFY PANEL** (2026-10-09+,
`docs/plans/2026-10-09-hitl-phase2-forcing-plan.md`). A flagged row can no
longer be waved through by bulk Accept — `acceptTargets` excludes every
`needs_review` row regardless of selection, and the ONLY way to resolve one
is per-row `Y`/`Enter` on the row selected in the queue, or marking it
`unsure`. At extraction, `app/review/sampling.py` draws an audit sample of
unflagged rows plus an equal-rate, equally-clamped blind sample of flagged
rows (`SINDRI_AUDIT_RATE`/`MIN`/`MAX`, default 0.25/1/5, validated at boot
the same way `SINDRI_MIN_FREE_MB` is), from a seed minted fresh by
`build_header` every call — so a re-extract's new session gets an
independent sample for free. Both populations render identically in the
table (`verify`, no real value shown) and go through the same blind-verify
panel; `app/review/values.py` (mirrored by `app/static/js/values.js`, pinned
by one shared fixture) decides whether an entry matches, and the server
re-checks any client-claimed `match` itself rather than trusting it
(`ReviewStore.append`). **The Finish gate is now enforced inside
`ReviewStore.seal()`, not only the UI** — a flagged or blind row left
unresolved refuses the seal with `ReviewIncomplete` (409), regardless of
caller. `ReviewStore.progress()` widens its queue to include blind rows,
which also widens what the work list's existing `flagged_total`/
`flagged_open` columns (migration 4, no new migration here) count — a
drawing whose only outstanding work is a few audit rows now shows them as
"open," which is a visible change, not a regression. `unsure` is a new,
independent resolution path (own marker, own Excel column, own amber
dashed PDF balloon style, distinct from the tray's violet, which is never
exported at all) — exported marked for escalation. Grading (`grade()`, the
client report, dev validation against gold) is phase 3 and is not built
here; `app/review/values.py` is built early because the panel's live
compare needed it, and phase 3 should extend it, not recreate it.
```

- [ ] **Step 4: run to confirm pass**

Run: `node --test <test file>`
Expected: PASS

- [ ] **Step 5: full suite + guard + commit**

Run: `python -m pytest -q`
Expected: unchanged pass count from Task 17, plus whatever pytest-run node
count `tests/test_js_state.py` reports now matches the final `node --test
"tests/js/*.test.mjs"` count exactly

Run: `node --test "tests/js/*.test.mjs"`
Expected: matches the count above

Run: `bash ~/.claude/hooks/test-sindri-guard.sh`
Expected: `32 passed, 0 failed` (this plan never widens the guard — confirm
it, do not assume it)

```bash
git add app/static/js/queue.js app/static/js/main.js app/static/js/table.js docker-compose.yml CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: HITL phase 2 shipped — forcing UX complete

Finish tooltip, progress-bar copy, compose env vars, and the CLAUDE.md
paragraph for the review queue, per-row accept, and the blind-verify
panel.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: real-browser drive

**Files:**
- Create (scratchpad only, never committed): a throwaway Playwright driver
  following the exact pattern phase 1 and the B3/B4 drives used — a scratch
  `SINDRI_DATA_DIR`/`SINDRI_REVIEW_DIR`, synthetic single-page PDFs built in
  code (never a bare `.pdf` on any command line), a stub `extract_fn`
  producing a handful of characteristics with a mix of flagged/unflagged rows
  large enough that `SINDRI_AUDIT_RATE=1.0`, `SINDRI_AUDIT_MAX=0` guarantees
  at least one audit row and one blind-flagged row deterministically.

- [ ] **Step 1: write the drive script** (in the scratchpad directory, not
  the repo) exercising, in order:
  1. Import and start a synthetic drawing with 2 unflagged + 2 flagged rows,
     `SINDRI_AUDIT_RATE=1.0` so sampling is deterministic (everything
     blind).
  2. Open it; confirm the table shows `verify` for all four rows (both
     populations, indistinguishable).
  3. Submit the verify panel on one row with the exact system value —
     confirm it resolves with no visible "accepted"/"wrong" distinction in
     the table (blinding).
  4. Submit a DIFFERENT value on a second row — confirm the side-by-side
     mismatch UI appears, choose "keep entered," confirm the cell updates.
  5. Mark a third row unsure — confirm the marker appears and the progress
     bar's outstanding count drops.
  6. Attempt Finish while the fourth row is still unresolved — confirm it is
     blocked with the tooltip naming "1 row(s)."
  7. Resolve the fourth row; Finish succeeds; download the Excel and confirm
     the `Unsure` column has exactly one `True` row; download the PDF and
     confirm (by byte-searching for the dash-pattern operator, or simply
     trusting the unit test from Task 11 and only confirming the PDF opens
     without error here).
  8. Reload mid-session (before Finish) on a fresh drawing and confirm a
     restored blind row still renders `verify`, not its real value — the
     blinding survives a reload exactly like every other row property.

- [ ] **Step 2: run it**

Run the scratch driver script by path (never a bare `.pdf` in the command).
Expected: all eight checks pass; note any bug found and fix it with a
regression test in the relevant Task's file above (do not add a Task 20 —
amend the task whose code the bug lives in, one more commit on that task's
test file, per the "data-integrity tasks" review convention already used in
B3/B4).

- [ ] **Step 3: final full-suite verification**

Run: `python -m pytest -q`
Expected: final total, 2 skipped

Run: `node --test "tests/js/*.test.mjs"`
Expected: matches `tests/test_js_state.py`'s count exactly

Run: `bash ~/.claude/hooks/test-sindri-guard.sh`
Expected: `32 passed, 0 failed`

Run: `python3 -m app.eval.experiment`
Expected: unchanged from before this plan (phase 2 touches no pipeline/eval
code at all — this is a sanity check that the arm decision table still
loads, not a claim this plan changes any arm)

- [ ] **Step 4: update the state file** (controller only; implementers never
  edit it) with the final suite counts, the bug(s) the drive found and
  which task's commit fixed them, and "What's next: phase 3 (grading +
  client report)."

> **Milestone: phase-2-complete** - state must be checkpointed here before continuing.

---

## Self-review against the design

**Spec coverage** (design §1/§2, operator decisions 2026-10-09):

* Review queue, reading order, zoom-on-select → Task 15 (`queueRows`) +
  Task 16/17 wiring (zoom reuses the existing `flashAndCenter`).
* Per-row accept only for shown flagged rows, bulk never touches a flagged
  row → Task 16 (`acceptTargets`, `acceptSelectedRow`).
* Blind panel: empty fields, Enter submits, match/mismatch/1/2/E/U → Task 17.
* Reason chips, scoped 1-5, dismiss on any other key/click → Task 16.
* Unsure marker, counts as resolved, exported → Task 2 (replay), Task 6
  (model), Task 10 (Excel), Task 11 (PDF), Task 13/15/16 (UI).
* Progress bar "resolved/unchecked" copy → Task 18.
* Finish gate blocked server-side, tooltip with jump → Task 8 (server),
  Task 18 (tooltip; "jump" reuses phase 1's Review-filter switch).
* Audit rate/min/max config, recorded in header, seed-deterministic,
  existing sessions unaffected, suggestions excluded → Tasks 4, 5, 7, 9.
* Blind-flagged sample at the same rate → Task 4 (`assign_sampling`,
  decision 5).
* `app/review/values.py` built now, pinned to `app.eval.normalize` →
  Task 3.
* Server does not trust a client `match` blindly → Task 8
  (`_check_blind_submits`).
* `dwell_ms` on accept/blind_submit → no schema change needed (event dicts
  already pass through unknown keys unvalidated; explicitly out of a
  separate task because there is nothing to build).
* Replay-parity fuzz extended → Task 14.
* CLAUDE.md paragraph, compose env → Task 18.
* `grade()`, `values.py`'s phase-3 extensions, the client report, dev
  validation — **explicitly out of scope**, confirmed absent from every
  task above.

**Placeholder scan:** every code block above is complete, runnable code or an
exact diff instruction naming the precise existing line(s) to change; no
"TODO"/"handle appropriately"/"similar to Task N" without the actual code.
Tasks 9, 16, and 19 name specific existing-file investigation steps
("check this file's existing helper/fixture first") rather than guessing
file contents this research pass could not fully confirm line-by-line
(`tests/test_api.py`'s seal-test fixture shape, `table.js`'s exact current
line count, `tests/js/`'s DOM-stub convention) — each still states the exact
behavioural assertion required, not a vague instruction.

**Type consistency:** `blind`/`audit`/`unsure` are `bool` everywhere they
appear (Python `Characteristic`, JS row objects, JSON fixtures). `entered` is
always a `dict`/object with the four `VALUE_FIELDS` keys, never a bare
string, consistently from `journal.py`'s `validate_event` through
`store.py`'s `_check_blind_submits` to `state.js`'s `opBlindSubmit` and the
verify panel. `outcome` is always one of the four `BLIND_OUTCOMES` string
literals, defined once in `journal.py` and mirrored as plain string literals
in JS (no separate JS enum module — matched by the fuzz's generator in
Task 14 and the panel's own calls in Task 17). `reason` is `Optional[str]`
server-side (`None` or a closed-vocabulary string) and `undefined` or a
string client-side, consistently omitted-when-absent on both sides so
`validate_event`'s `e.get("reason")` and JS's `e.reason` agree on "absent."
