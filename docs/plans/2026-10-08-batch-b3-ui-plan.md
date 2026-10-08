# Batch B3 — UI (router, work list, staging view, review bar) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow `context-management:milestone-checkpoints`: the CONTROLLER checkpoints `docs/plans/2026-10-08-batch-b3-ui-plan.state.md` at each marker. Implementers never edit that file.

**Goal:** Replace the interim single-drawing screen with the real product shell: a hash router (`#/` work list, `#/staging/<batch>` staging, `#/doc/<id>` review), a work list of every drawing with filters and bulk queue controls, a staging view for a draft import, a review bar that moves through a batch's ready/in-review drawings and chains Finish into the next one, a read-only banner for the losing side of a two-tab takeover, and a lagging-journal banner with a retry countdown. One drawing is still a batch of one (decision 17); the staging view and the review bar are how it is now reached.

**Architecture:** The server gains the read path B1/B2 never built — `GET /api/documents` (filtered, diffable by `since`), a queue summary with a rolling-median ETA, and the queue controls the design lists but no earlier phase wired up (move to front, pause/resume, re-extract). Flagged/open/unchecked counts become real, persisted document columns (migration 4; they were in the design's §1 data model from day one but no column ever existed), computed by a new non-mutating `ReviewStore.progress()` and refreshed wherever a review record changes. On the client, four new pure-logic modules (`router.js`, `worklist.js`, `reviewbar.js`, `banner.js`) carry everything node can test without a DOM; `stagingview.js` and the DOM-wiring half of `worklist.js`/`reviewbar.js`/`banner.js` are checked by a node *import* test (the module loads and exports what later code calls) plus the existing Python API tests underneath them, the same split the codebase already uses for `table.js`/`viewer.js`. `main.js` stops owning a single screen and becomes the router's three-screen host; every B1/B2 screen (confirm, extracting, the review table) is reused, not rewritten.

**Tech Stack:** Python 3 / FastAPI / sqlite3 / pytest; vanilla ES modules + `node --test`.

**Design:** `docs/plans/2026-10-07-batch-async-review-design.md` — §0 (binding decisions; B3 touches 8, 9, 11, 12, 13, 18, 25, 28), §2 (queue controls), §3 (work list, review navigation, resume), §4 (Finish, decision 13), §5 (errors, testing, phasing — "B3: router, work list, staging view, review bar, resume, two tabs, lagging banner"). B1 as built: `docs/plans/2026-10-07-batch-b1-core-plan.state.md`. B2 as built: `docs/plans/2026-10-07-batch-b2-import-plan.state.md` plus its running log's CARRY-TO-B3 notes (reproduced in "Carried from B2" below) — this plan builds on the code B1/B2 actually produced, not on their plan text.

**Rules that bind every task** (from CLAUDE.md — read §1 and §4 there):
* TDD strictly: failing test → watch it fail → minimal code → pass → commit. One task, one commit (a review fix may be a second commit).
* Never read client data; never put the protected path (`/home/clemi/sindri-client-data`) in a command or a subagent prompt; single, unpiped, unchained shell commands (no `|`, `&&`, `;`, `>`).
* **The Bash guard denies any command string containing a bare `.pdf`.** Never type a file name with that extension in a command, a `-k` filter or a commit message. Test PDFs are built in code (`tests/batch/pdfs.py`) or reached through the `sample_pdf` fixture, never named on the command line.
* Never use bare `git stash` (the stash stack is shared across worktrees and other sessions may be using it). `git add` and `git commit` are separate calls.
* Never `git checkout`/`restore`/`reset --hard` without checking `git status` first.
* Server logs carry ids (first 8 chars), states, counts and exception TYPES only — never exception messages, file names, or values. Error replies are fixed sentences.
* `app/batch/` and `app/review/` must never import `app.eval` (`tests/review/test_product_boundary.py` globs every module in both).
* A new field's default must not lie about historical data (CLAUDE.md §4): a document created before migration 4 gets `NULL` flagged/open/unchecked columns, never a plausible `0` — a lazy backfill computes them the first time such a row is listed.
* Tests use temp dirs only; `tests/conftest.py` already isolates `SINDRI_DATA_DIR` / `SINDRI_REVIEW_DIR` per test.
* Invisible/control characters inside a test string are written as `\u` escapes (e.g. `" "`), never pasted literally.
* Commit trailer: `Co-Authored-By: <the implementing model's accurate name> <noreply@anthropic.com>`.
* Full suite before every commit: `python -m pytest -q`. **Baseline measured for this plan (2026-10-08): 1802 passed, 2 skipped** (node tests run inside pytest as one test, `tests/test_js_state.py`, currently 159 node tests under it — `node --test "tests/js/*.test.mjs"` run standalone shows the same count).
* Never edit `docs/plans/2026-10-08-batch-b3-ui-plan.state.md` — the controller checkpoints it.

**Carried from B2** (from the B2 running log, items addressed to B3):
* Show `ignored_total` / `ignored_by_reason` (ZIP import, capped list) in the staging view.
* A many-entry ZIP makes `/complete` one long request: show a "processing…" state, not a frozen button.
* An empty archive should say so in the staging view, not just show zero rows.
* A zip-bomb (`StorageFault`/`DiskFull` from `/complete`) answers 507 on every retry; offer the user a way to delete the draft rather than retry forever.
* The interim UI's Cancel discards a whole draft; B3's staging view must remove a single row from a multi-row draft (the per-document `DELETE /api/documents/{id}` already does this — B3 only needs the UI control).
* B3 must add a `hashchange` listener (none exists yet); polling must stop while the tab is hidden (none does yet); a ready drawing switching the screen automatically is existing behaviour, keep it; stale-tab detection should match the server's message text (already does, via `/opened elsewhere/`); a partly-acknowledged flush with nothing further pending should be retried on the next edit, which is already how `scheduleFlush`/`flushBackoff` work.

**Decisions made while planning** (the design is silent or ambiguous; each is justified where it lands):
1. **Flagged/open/unchecked become real document columns in migration 4**, computed by `ReviewStore.progress()` (mirrors `state.js`'s `progress()` exactly: flagged = non-suggested rows with `needs_review`; open = flagged not `reviewed`; unchecked = non-suggested, non-flagged rows) and persisted so a work-list poll over hundreds of drawings never replays every journal on every tick. `NULL` means "not computed yet" (a pre-B3 row, or one with no review record at all); `GET /api/documents` backfills it lazily, once, for a row in `ready`/`in_review`/`finished`.
2. **The queue header is not the design's literal "Extracting 3 / 37" example**, because the design never defines what the two numbers count and the obvious reading (drawings completed so far / total in some scope) needs state this app does not keep (a "since when" boundary, a batch scope for a cross-batch work list). Implemented instead as `{queued, extracting, paused, eta_seconds}` from the server, rendered as "Extracting · N queued · ~T left" (or "Paused · N queued", or "Queue idle"). `eta_seconds` is `None` until the worker has finished at least one drawing this process lifetime — CLAUDE.md §4 forbids a number with no basis, so the UI shows "estimating…" instead of a number built from zero samples.
3. **The hash scheme changes from `#doc=<id>` (B1) to `#/doc/<id>` (design §3)**, with `#/` the work list and `#/staging/<batch>` the staging view. This is the router's whole job; Task 13 is the one place that migrates it, since it owns the one `setHash`/`hashDoc` pair that write and read the fragment.
4. **"Export selected" (work list bulk action) is a client-side loop over the existing per-document export routes** (`GET /api/documents/{id}/export/{xlsx|pdf}`, shipped in B1), skipping any selected drawing that is not `finished` with a toast naming how many were skipped — not a new server endpoint. Design §5 phases real mass export (one ZIP, one manifest) into B4; this is the "trivially a per-drawing loop" the task brief allows now.
5. **Re-extract is a new transition, not a repurposed Retry.** Retry (B1) only leaves `failed`/`cancelled`; re-extract leaves `ready` and is refused the moment `ReviewStore.has_events()` is true, so it can never discard a single edit a human made. The route deletes `result.json` before queuing (the worker only re-extracts when that file is missing) and resets the new progress columns to `NULL`.
6. **Move to front is `priority = current max + 1`, refused off `queued`.** A bulk "move to front" is the client calling it once per id in the order shown (not a new bulk endpoint): each call reads a fresh max, so the visual order survives even though every call is a separate transaction — the design does not require millisecond-exact simultaneity, only that the front of the queue matches what the user saw.
7. **Pause/resume gets two tiny routes** (`POST /api/queue/pause` / `/resume`) over the `Repo.set_paused` already written in B1 but never exposed.
8. **The review bar's navigation order is "the batch's ready/in-review drawings in list order"** (design §3), computed from the SAME `GET /api/documents` payload the work list already polls (filtered client-side to the current batch and those two states) rather than a second endpoint — one source of truth for ordering. `list_documents` sorts by `d.rowid`, the true staging/insertion order (it survives a drawing being queued — only `rowid` is stable and monotonic; `updated_at` is not, since any later event on an UNRELATED row, or this one, changes it). An earlier draft of this plan sorted by `updated_at` instead; that was a bug, not an acceptable imprecision — it would have reordered the whole list every time any row changed, which is the opposite of what Prev/Skip/Jump need. `since`-cursor filtering still compares `updated_at` (`list_documents`'s `WHERE` clause), since that is genuinely when a row last changed and `rowid` says nothing about it.
9. **The two-tab banner and the lagging-journal banner are two different triggers of one `banner.js` state machine**, because both end in "this tab cannot be trusted to save right now" but need different copy and different recovery (reload vs. retry).
10. **A hash change written by `history.replaceState` does not fire `hashchange`** (that event only fires for a navigation the BROWSER performs — a real link click, back/forward, or an assignment to `location.hash`), so B1's `setHash()` helper, reused verbatim, would make Prev/Skip/Jump/Finish's chain-to-next/the post-import redirect silent no-ops: the address bar would change and nothing on screen would. `router.js` therefore exports a `navigate(url, { setUrl, dispatch })` seam — pure and node-testable — and `main.js`'s `go(url)` wraps it with the real `history.replaceState` and `routeFromHash`. The re-entrancy guard lives INSIDE `routeFromHash` itself, not inside `go()`: `routeFromHash` also runs from the native `hashchange` listener (a real link click, back/forward), which never goes through `go()` at all, so a guard only in `go()` would still let a real link click race a `go()`-driven dispatch still in flight. The handful of EXISTING `setHash()` calls that already update the screen themselves through a direct function call (showing the confirm panel, the extracting overlay, or a freshly opened review) are left as plain `setHash()` — they are not "navigation" in the router sense and re-dispatching for them would refetch a document that is already on screen for no reason. Only the five router-driven entry points (Prev, Skip, Jump, Finish's chain-to-next, and the post-import/post-start redirects to staging/work-list) go through `go()`.
11. **A deletion is invisible to the since-cursor poll**: `GET /api/documents?since=` only ever returns rows that CHANGED, and a removed row simply never appears in a diff again — it has no `updated_at` to be newer than anything. The work list therefore does a full (`since`-less) reload every 20th poll (~1 minute at the 3 s cadence, `worklist.js`'s pure `shouldFullRefresh`) and immediately after any 404 the review screen hits on a drawing the work list had cached, rather than only ever growing a list that can shrink.

---

## File structure

| File | Responsibility |
|---|---|
| `app/batch/db.py` | + migration 4: `document.flagged_total` / `flagged_open` / `unchecked` (nullable) |
| `app/review/store.py` | + `progress(sid)` (non-mutating flagged/open/unchecked counts), `has_events(sid)` |
| `app/batch/repo.py` | + `update_progress`, `move_to_front`, `reextract`, `list_documents`, `queue_summary` |
| `app/batch/worker.py` | + rolling-median duration tracking, `eta_seconds`, computes and persists progress right after `store.create` |
| `app/main.py` | `GET /api/documents`, `POST /api/documents/{id}/move_to_front`, `POST /api/documents/{id}/reextract`, `POST /api/queue/pause`, `POST /api/queue/resume`; progress recompute wired into `/events` and `/seal` |
| `app/static/js/router.js` (new) | hash parsing (`parseRoute`), the URL builders, and `navigate` -- the seam that makes a programmatic hash change actually dispatch (`history.replaceState` alone never fires `hashchange`) |
| `app/static/js/worklist.js` (new) | pure: `filterDocuments`, `formatQueueHeader`, `formatEta`; DOM: render + poll + bulk actions |
| `app/static/js/reviewbar.js` (new) | pure: `navOrder`, `prevId`, `nextId`, `nextAfterFinish`, `jumpCandidates`, `skip` (calls `navigate` itself); DOM: the bar itself |
| `app/static/js/banner.js` (new) | pure: `bannerFor({ stale, lagging, retryAt })`; DOM: the banner element |
| `app/static/js/stagingview.js` (new) | staging screen: render rows + badges + Include + Start/Discard/remove-one-row |
| `app/static/js/api.js` | + `listDocuments`, `moveToFront`, `reextractDocument`, `pauseQueue`, `resumeQueue` |
| `app/static/js/badges.js` | + `workListBadges` (exported rN, out-of-scope, failed + error type) |
| `app/static/js/main.js` | router-driven; interim single-screen flow folded into the three routed screens |
| `app/static/index.html` | + work-list screen, staging screen, review bar, two banners |
| `app/static/styles/components.css` | + minimal rules for the above (not unit-tested) |
| `tests/batch/test_db.py` | + migration 4 |
| `tests/review/test_store.py` | + `progress`, `has_events` |
| `tests/batch/test_repo.py` | + `update_progress`, `move_to_front`, `reextract`, `list_documents`, `queue_summary` |
| `tests/batch/test_worker.py` | + ETA / duration tracking, progress-at-ready |
| `tests/test_api_worklist.py` (new) | `GET /api/documents`, move-to-front, re-extract, pause/resume routes |
| `tests/js/router.test.mjs`, `worklist.test.mjs`, `reviewbar.test.mjs`, `banner.test.mjs` (new) | pure-logic + import-smoke node tests |
| `CLAUDE.md` | B3 paragraph |

---

## Milestone 1: server-list-queue-api

### Task 1: Migration 4 — flagged/open/unchecked columns

**Data-integrity: yes** (schema migration of a deployed index).

**Files:**
- Modify: `app/batch/db.py`
- Test: `tests/batch/test_db.py`

- [ ] **Step 1: Write the failing test** — append to `tests/batch/test_db.py`:

```python
def test_migration_4_adds_nullable_progress_columns(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(document)")}
    for name in ("flagged_total", "flagged_open", "unchecked"):
        assert name in cols
        assert cols[name][3] == 0          # not NOT NULL: nullable
        assert cols[name][4] is None       # no default -- NULL, not a lying 0


def test_migration_4_leaves_existing_rows_null(tmp_path, monkeypatch):
    """A row from before this migration has no computed counts yet
    (CLAUDE.md §4: a new field's default must not lie about historical
    data) -- GET /api/documents backfills it lazily, this migration does
    not guess."""
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:3])
    db.migrate(conn)
    t = "2026-10-08T00:00:00.000000+00:00"
    conn.execute("INSERT INTO batch (id, name, draft, created_at, updated_at)"
                 " VALUES ('b', 'x', 0, ?, ?)", (t, t))
    conn.execute("INSERT INTO document (id, batch_id, file_name, state, created_at, updated_at)"
                 " VALUES ('d', 'b', 'x.pdf', 'ready', ?, ?)", (t, t))
    monkeypatch.setattr(db, "MIGRATIONS", full)
    assert db.migrate(conn) == len(full)
    row = conn.execute("SELECT flagged_total, flagged_open, unchecked"
                       " FROM document WHERE id = 'd'").fetchone()
    assert tuple(row) == (None, None, None)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: FAIL — `sqlite3.OperationalError: no such column: flagged_total` (or similar).

- [ ] **Step 3: Implement migration 4** — in `app/batch/db.py`, append to `MIGRATIONS`:

```python
    # 4: work-list progress counts (batch B3, design §1, §3)
    """
    -- Flagged/open/unchecked were in the design's document model from day
    -- one but no earlier phase ever added the columns. NULL means "not
    -- computed" -- a row from before this migration, or one with no review
    -- record at all (staged/queued/extracting/failed/cancelled) -- never a
    -- plausible 0 that would claim a drawing has zero flags before anyone
    -- has looked. GET /api/documents computes and persists them the first
    -- time such a row is listed (app/main.py); this migration does no I/O
    -- against the review store and cannot, since it only holds a DB
    -- connection.
    ALTER TABLE document ADD COLUMN flagged_total INTEGER;
    ALTER TABLE document ADD COLUMN flagged_open INTEGER;
    ALTER TABLE document ADD COLUMN unchecked INTEGER;
    """,
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1804 passed, 2 skipped

```bash
git add app/batch/db.py tests/batch/test_db.py
git commit -m "$(cat <<'EOF'
batch: add the work list's progress columns (migration 4)

Flagged/open/unchecked counts were in the design's document model from
day one but no phase ever added the columns. They are nullable so a
pre-B3 row reads as "not computed", never a lying zero.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `ReviewStore.progress()` and `has_events()`

**Data-integrity: yes** (feeds a persisted count the work list and re-extract both trust; must mirror `state.js`'s `progress()` exactly or the two disagree about the same drawing).

**Files:**
- Modify: `app/review/store.py`
- Test: `tests/review/test_store.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/review/test_store.py`:

```python
def test_progress_counts_flagged_open_and_unchecked(tmp_path):
    """Mirrors state.js's progress(): flagged = non-suggested rows with
    needs_review; open = flagged not yet reviewed; unchecked = non-suggested,
    non-flagged rows. A fresh session has every flagged row open and no
    accepts yet."""
    store = ReviewStore(tmp_path)
    writer = store.create("s1", {}, {"rows": [
        {"id": "a", "needs_review": True, "suggested": False},
        {"id": "b", "needs_review": True, "suggested": False},
        {"id": "c", "needs_review": False, "suggested": False},
        {"id": "d", "needs_review": False, "suggested": True},
    ]})
    assert store.progress("s1") == {"flagged_total": 2, "flagged_open": 2, "unchecked": 1}
    store.append("s1", writer, [{"seq": 1, "type": "accept", "ids": ["a"]}])
    assert store.progress("s1") == {"flagged_total": 2, "flagged_open": 1, "unchecked": 1}


def test_progress_counts_a_confirmed_suggestion_by_its_own_needs_review_flag(tmp_path):
    """confirm_suggestions turns a suggestion into a normal balloon
    (suggestion-tray design) -- but mirrors state.js's progress() EXACTLY
    (no new semantics): whether it lands in `flagged` or `unchecked`
    depends on the row's OWN needs_review, not on having just been
    confirmed. Confirming is not itself a reason to flag a row -- and every
    suggestion in practice already has needs_review False (it was dropped
    for LOW CONFIDENCE, a drop-stage reason, not a review-flag reason; see
    app/pipeline/policy_rules.py's stage-2 drops)."""
    store = ReviewStore(tmp_path)
    writer = store.create("s1", {}, {"rows": [
        {"id": "a", "needs_review": False, "suggested": True},
    ]})
    assert store.progress("s1") == {"flagged_total": 0, "flagged_open": 0, "unchecked": 0}
    store.append("s1", writer, [{"seq": 1, "type": "confirm_suggestions", "ids": ["a"]}])
    # Still not flagged: needs_review on this row was False before AND after
    # confirming. It simply moves from "not a balloon yet" (a suggestion) to
    # an unchecked balloon -- exactly what state.js's progress() would count.
    assert store.progress("s1") == {"flagged_total": 0, "flagged_open": 0, "unchecked": 1}


def test_progress_does_not_rotate_the_writer_or_touch_the_journal(tmp_path):
    """Called every poll cycle (GET /api/documents): it must be safe to call
    from a tab that does not hold the writer, and must not trim an
    unacknowledged event the way open() does."""
    store = ReviewStore(tmp_path)
    writer = store.create("s1", {}, {"rows": [{"id": "a", "needs_review": True, "suggested": False}]})
    store.progress("s1")
    # The original writer still works -- progress() did not rotate it.
    store.append("s1", writer, [{"seq": 1, "type": "accept", "ids": ["a"]}])


def test_has_events_is_false_for_an_untouched_session(tmp_path):
    store = ReviewStore(tmp_path)
    store.create("s1", {}, {"rows": []})
    assert store.has_events("s1") is False


def test_has_events_is_true_once_the_reviewer_has_acted(tmp_path):
    store = ReviewStore(tmp_path)
    writer = store.create("s1", {}, {"rows": [{"id": "a", "needs_review": True, "suggested": False}]})
    store.append("s1", writer, [{"seq": 1, "type": "accept", "ids": ["a"]}])
    assert store.has_events("s1") is True
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/review/test_store.py -q`
Expected: FAIL — `AttributeError: 'ReviewStore' object has no attribute 'progress'`

- [ ] **Step 3: Implement** — in `app/review/store.py`, add after `open()` (before `revision()`):

```python
    def progress(self, sid: str) -> dict:
        """Flagged/open/unchecked counts for the work list (batch design
        §3), computed exactly the way state.js's progress() computes them
        client-side -- if the two ever disagree about the same drawing, one
        of them has drifted from the other's definition. Read-only: unlike
        open(), it never rotates the writer, trims the journal, or touches
        disk at all, so it is safe to call from a poll that holds no writer
        and holds no lock on the reviewer's own tab."""
        with self._lock:
            d = self._dir(sid)
            events = self._events(d)
            through = self._sealed_through(d)
            top = contiguous_seq({e["seq"] for e in events}, start=through)
            kept = [e for e in events if e["seq"] <= top]
            try:
                net = net_events(kept)
                proposal = json.loads(
                    (d / "proposal.json").read_text(encoding="utf-8"))["rows"]
                rows = replay(proposal, net)
            except JournalError as exc:
                raise JournalInvalid(str(exc)) from exc
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise JournalInvalid(f"corrupt review record: {exc}") from exc
        balloons = [r for r in rows.values() if not r.get("suggested")]
        flagged = [r for r in balloons if r.get("needs_review")]
        flagged_open = sum(1 for r in flagged if not r["reviewed"])
        return {"flagged_total": len(flagged), "flagged_open": flagged_open,
                "unchecked": len(balloons) - len(flagged)}

    def has_events(self, sid: str) -> bool:
        """True once the reviewer has acted on this session at all -- the
        gate for re-extract (batch design §2 queue controls): a `ready`
        drawing with no events may be re-extracted (its proposal is
        discarded), one with even a single event may not."""
        with self._lock:
            d = self._dir(sid)
            return bool(self._events(d))
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/review/test_store.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1809 passed, 2 skipped

```bash
git add app/review/store.py tests/review/test_store.py
git commit -m "$(cat <<'EOF'
review: add a read-only progress() and has_events() to ReviewStore

The work list needs flagged/open/unchecked counts and re-extract needs
to know whether a human has touched a drawing yet; both read the
journal without rotating the writer or trimming anything, so a poll
that holds no writer can call them safely.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Repo — `update_progress`, `move_to_front`, `reextract`

**Data-integrity: yes** (new queue transitions; `reextract` discards a proposal and must never touch a drawing a human has reviewed).

**Files:**
- Modify: `app/batch/repo.py`
- Test: `tests/batch/test_repo.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_repo.py`:

```python
def test_update_progress_persists_the_three_counts(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.update_progress(a, {"flagged_total": 3, "flagged_open": 2, "unchecked": 5})
    row = repo.get(a)
    assert (row["flagged_total"], row["flagged_open"], row["unchecked"]) == (3, 2, 5)


def test_move_to_front_outranks_every_queued_priority(repo):
    a, b, c = ids(3)
    for d in (a, b, c):
        queued(repo, d)
    repo._conn.execute("UPDATE document SET priority = 5 WHERE id = ?", (b,))
    repo.move_to_front(a)
    assert repo.claim_next()["id"] == a


def test_move_to_front_refuses_a_drawing_that_is_not_queued(repo):
    (a,) = ids(1)
    staged(repo, a)
    with pytest.raises(InvalidTransition):
        repo.move_to_front(a)


def test_reextract_sends_a_ready_drawing_back_to_queued_and_clears_progress(repo):
    (a,) = ids(1)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    repo.update_progress(a, {"flagged_total": 1, "flagged_open": 1, "unchecked": 0})
    repo.reextract(a)
    row = repo.get(a)
    assert row["state"] == "queued"
    assert row["attempts"] == 0
    assert (row["flagged_total"], row["flagged_open"], row["unchecked"]) == (None, None, None)


def test_reextract_refuses_anything_but_ready(repo):
    (a,) = ids(1)
    staged(repo, a)
    with pytest.raises(InvalidTransition):
        repo.reextract(a)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_repo.py -q`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'update_progress'`

- [ ] **Step 3: Implement** — in `app/batch/repo.py`, add after `mark_exported` (before `delete`):

```python
    def update_progress(self, doc_id, counts: dict) -> None:
        """Flagged/open/unchecked counts for the work list, computed by
        ReviewStore.progress() and persisted so GET /api/documents never
        replays a journal for every drawing on every 3 s poll (batch design
        §3). Silent on an unknown id on purpose: the caller already looked
        the row up to get its counts, so a race with a delete here is a
        lost update to a row that is gone anyway, not a bug to surface."""
        with self._tx() as c:
            c.execute("UPDATE document SET flagged_total = ?, flagged_open = ?,"
                      " unchecked = ? WHERE id = ?",
                      (counts["flagged_total"], counts["flagged_open"],
                       counts["unchecked"], doc_id))

    def move_to_front(self, doc_id) -> None:
        """priority = current max + 1 (batch design §2 queue controls):
        claims next regardless of how long anything else has waited.
        Refused off any state but `queued` -- "move to the front" has no
        meaning for a drawing already running, finished, or not yet
        started."""
        with self._tx() as c:
            row = c.execute("SELECT state FROM document WHERE id = ?", (doc_id,)).fetchone()
            if row is None:
                raise UnknownDocument(f"no document {str(doc_id)[:8]}")
            if row["state"] != "queued":
                raise InvalidTransition("only a queued drawing can move to the front")
            top = c.execute("SELECT COALESCE(MAX(priority), 0) FROM document").fetchone()[0]
            c.execute("UPDATE document SET priority = ?, updated_at = ? WHERE id = ?",
                      (top + 1, self._clock(), doc_id))

    def reextract(self, doc_id) -> None:
        """Back to `queued`, discarding the old proposal (batch design §2
        queue controls): refused off any state but `ready` -- `in_review`
        or `finished` means a human has already acted, which is exactly
        what ReviewStore.has_events() (checked by the caller, app.main)
        exists to protect. Progress counts go back to NULL: the old ones
        described a proposal that no longer exists."""
        with self._tx() as c:
            self._move(c, doc_id, ("ready",), "queued", attempts=0, error_type=None,
                       queued_at=self._clock(), flagged_total=None,
                       flagged_open=None, unchecked=None)
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_repo.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1814 passed, 2 skipped

```bash
git add app/batch/repo.py tests/batch/test_repo.py
git commit -m "$(cat <<'EOF'
batch: repo support for progress counts, move-to-front, re-extract

Three queue-state primitives the work list needs and no earlier phase
exposed: persisting ReviewStore's progress counts, bumping priority
above the current max, and sending a ready-but-unreviewed drawing back
to the queue with its old proposal discarded.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Worker — ETA tracking and progress at ready

**Data-integrity: yes** (touches the worker's success path; a mistake here either loses the progress compute or mis-settles a drawing).

**Files:**
- Modify: `app/batch/worker.py`
- Test: `tests/batch/test_worker.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_worker.py`:

```python
def test_eta_seconds_is_none_with_no_history(tmp_path):
    h = Harness(tmp_path)
    assert h.worker.eta_seconds(5) is None


def test_eta_seconds_is_the_rolling_median_times_queued(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    times = iter([0.0, 1.0,   # drawing A: 1.0 s
                  10.0, 13.0,  # drawing B: 3.0 s
                  20.0, 22.0])  # drawing C: 2.0 s
    monkeypatch.setattr("app.batch.worker.time.monotonic", lambda: next(times))
    for doc_id in (A, B, "c" * 32):
        h.queue(doc_id)
        h.worker.run_once()
    # median of [1.0, 3.0, 2.0] = 2.0
    assert h.worker.eta_seconds(4) == pytest.approx(8.0)


def test_eta_history_only_counts_successful_extractions(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    h.on_extract = lambda progress: (_ for _ in ()).throw(RuntimeError("boom"))
    times = iter([0.0, 5.0])
    monkeypatch.setattr("app.batch.worker.time.monotonic", lambda: next(times))
    h.queue(A)
    h.worker.run_once()
    assert h.repo.get(A)["state"] == "failed"
    assert h.worker.eta_seconds(1) is None


def test_progress_is_computed_and_persisted_once_a_drawing_is_ready(tmp_path):
    h = Harness(tmp_path)
    h.queue(A)
    h.worker.run_once()
    row = h.repo.get(A)
    assert row["state"] == "ready"
    # The stub extraction (Harness._extract) returns one Characteristic with
    # needs_review defaulting False -- so it is unchecked, not flagged.
    assert (row["flagged_total"], row["flagged_open"], row["unchecked"]) == (0, 0, 1)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_worker.py -q`
Expected: FAIL — `AttributeError: 'Worker' object has no attribute 'eta_seconds'`

- [ ] **Step 3: Implement** — in `app/batch/worker.py`:

Add `import collections` and `import time` to the top-of-file imports (alongside the existing `fcntl, json, logging, shutil, threading`).

In `Worker.__init__`, after `self._current = None`:

```python
        # Rolling median of the last 20 SUCCESSFUL extraction wall times,
        # this process's own history only (a restart loses it -- the UI
        # shows "estimating..." rather than a number with no basis,
        # CLAUDE.md §4). A median, not a mean: one very slow multi-page
        # drawing must not drag every later ETA with it.
        self._durations = collections.deque(maxlen=20)
```

Replace the `_run` method's opening and the path from `store.create` through `_settle(doc_id, self.repo.mark_ready)`:

```python
    def _run(self, doc_id: str) -> None:
        started = time.monotonic()
        d = self.data.doc_dir(doc_id)
        result_path = d / "result.json"
```

(the rest of the method body up to and including the `self.store.create(...)` try/except block is unchanged), then replace the final line of `_run`:

```python
        self._durations.append(time.monotonic() - started)
        try:
            self.repo.update_progress(doc_id, self.store.progress(doc_id))
        except Exception as e:
            # A progress-compute fault must not fail a drawing that
            # extracted and journaled fine -- the counts simply stay NULL
            # until the next GET /api/documents backfills them (app.main).
            # TYPE only: a replay mismatch could in principle carry a field
            # name but never a client value (CLAUDE.md §1).
            log.warning("progress compute failed for %s: %s", doc_id[:8], type(e).__name__)
        self._settle(doc_id, self.repo.mark_ready)
```

Add after `progress_of`:

```python
    def eta_seconds(self, queued: int):
        """Rolling-median estimate of time left for `queued` drawings still
        ahead. None with no history yet -- CLAUDE.md §4: no field may imply
        a precision it does not have, and zero samples have none."""
        if not self._durations:
            return None
        d = sorted(self._durations)
        n = len(d)
        median = d[n // 2] if n % 2 else (d[n // 2 - 1] + d[n // 2]) / 2
        return median * max(queued, 0)
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_worker.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1818 passed, 2 skipped

```bash
git add app/batch/worker.py tests/batch/test_worker.py
git commit -m "$(cat <<'EOF'
batch: worker tracks a rolling-median ETA and persists progress at ready

The queue header needs a time estimate and the work list needs
flagged/open/unchecked counts without replaying a journal per row per
poll; both are computed once, right when a drawing settles to ready.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Repo — `list_documents`, `queue_summary`

**Data-integrity: no** (read-only).

**Files:**
- Modify: `app/batch/repo.py`
- Test: `tests/batch/test_repo.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_repo.py`:

```python
def test_list_documents_filters_by_batch_state_and_search(repo):
    a, b = ids(2)
    d1 = staged(repo, a, "Flansch.pdf")
    d2 = staged(repo, b, "Welle.pdf")
    assert [r["id"] for r in repo.list_documents(state="staged")] == [a, b]
    assert [r["id"] for r in repo.list_documents(search="flan")] == [a]   # case-insensitive
    assert [r["id"] for r in repo.list_documents(batch_id=d2["batch_id"])] == [b]


def test_list_documents_needs_me_is_ready_or_in_review(repo):
    a, b, c = ids(3)
    queued(repo, a)
    repo.claim_next()
    repo.mark_ready(a)
    queued(repo, b)
    repo.claim_next()
    repo.mark_ready(b)
    repo.events_appended(b, 1)
    staged(repo, c)
    assert {r["id"] for r in repo.list_documents(needs_me=True)} == {a, b}


def test_list_documents_since_only_returns_rows_touched_after_the_cursor(repo):
    a, b = ids(2)
    staged(repo, a)
    cursor = repo.get(a)["updated_at"]
    staged(repo, b)
    assert [r["id"] for r in repo.list_documents(since=cursor)] == [b]


def test_list_documents_joins_the_batch_name(repo):
    (a,) = ids(1)
    d = staged(repo, a, "x.pdf")
    name = repo._conn.execute("SELECT name FROM batch WHERE id = ?", (d["batch_id"],)).fetchone()[0]
    (row,) = repo.list_documents()
    assert row["batch_name"] == name


def test_queue_summary_counts_queued_and_extracting_and_paused(repo):
    a, b = ids(2)
    queued(repo, a)
    queued(repo, b)
    repo.claim_next()
    assert repo.queue_summary() == {"queued": 1, "extracting": 1, "paused": False}
    repo.set_paused(True)
    assert repo.queue_summary()["paused"] is True
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/batch/test_repo.py -q`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'list_documents'`

- [ ] **Step 3: Implement** — in `app/batch/repo.py`, add to the "reads" section, after `staging`:

```python
    def list_documents(self, *, batch_id=None, state=None, search=None,
                       needs_me=False, since=None) -> list:
        """Work-list rows (batch design §3), joined with the batch name and
        ordered by `updated_at` (ties by rowid) so the client can diff by
        `since` rather than by position. `search` is a case-insensitive
        substring match on `file_name` -- SQLite's default LIKE is
        ASCII-only case-insensitive, which is a known, honest gap for a
        single-operator local tool (a non-ASCII query simply finds fewer
        rows, it does not silently find the wrong ones). Sorted by `rowid`
        -- the true staging/insertion order, and STABLE: it never changes
        once assigned, unlike `updated_at`, which every edit on a row (and
        only that row) moves. The review bar's Prev/Skip/Jump (Task 11) and
        Finish's chain-to-next (Task 13) all depend on this list's order
        meaning "the order drawings were staged in", not "the order they
        were last touched in" -- the latter would reorder the whole list out
        from under the reviewer on every single edit."""
        clauses, params = [], []
        if batch_id is not None:
            clauses.append("d.batch_id = ?"); params.append(batch_id)
        if state is not None:
            clauses.append("d.state = ?"); params.append(state)
        if needs_me:
            clauses.append("d.state IN ('ready', 'in_review')")
        if search:
            esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("d.file_name LIKE ? ESCAPE '\\'")
            params.append(f"%{esc}%")
        if since is not None:
            clauses.append("d.updated_at > ?"); params.append(since)
        where = " AND ".join(clauses) if clauses else "1=1"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT d.*, b.name AS batch_name FROM document d"
                f" JOIN batch b ON b.id = d.batch_id WHERE {where}"
                f" ORDER BY d.rowid", tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def queue_summary(self) -> dict:
        """Counts for the work list's header line; `eta_seconds` is added by
        the caller (app.main), which holds the worker, not the repo."""
        with self._lock:
            queued = self._conn.execute(
                "SELECT COUNT(*) FROM document WHERE state = 'queued'").fetchone()[0]
            extracting = self._conn.execute(
                "SELECT COUNT(*) FROM document WHERE state = 'extracting'").fetchone()[0]
            p = self._conn.execute("SELECT value FROM setting WHERE key = 'paused'").fetchone()
        return {"queued": queued, "extracting": extracting,
                "paused": p is not None and p["value"] == "1"}
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/batch/test_repo.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1823 passed, 2 skipped

```bash
git add app/batch/repo.py tests/batch/test_repo.py
git commit -m "$(cat <<'EOF'
batch: repo read path for the work list (filters, cursor, queue summary)

GET /api/documents needs a filtered, diffable listing and a queue
summary; neither existed anywhere in B1/B2.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `GET /api/documents` and the queue-control routes

**Data-integrity: yes** (wires progress recompute into the `/events` and `/seal` paths that B1 built and this plan must not regress).

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api_worklist.py` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/test_api_worklist.py`:

```python
from fastapi.testclient import TestClient
import app.main as main
from app.main import app
from tests.test_api import save_pdf, start_pdf

client = TestClient(app)


def _ready(sample_pdf, filename="a.pdf"):
    meta = save_pdf(client, sample_pdf, filename)
    sid = meta["session_id"]
    start_pdf(client, sid)
    assert main.runtime().worker.run_once() == sid
    return sid


def test_list_documents_returns_every_drawing_with_progress_and_cursor(sample_pdf):
    sid = _ready(sample_pdf, "a.pdf")
    r = client.get("/api/documents")
    assert r.status_code == 200, r.text
    body = r.json()
    (row,) = [d for d in body["documents"] if d["id"] == sid]
    assert row["state"] == "ready"
    assert row["flagged_total"] is not None   # backfilled on this read
    assert body["cursor"]
    assert body["queue"]["paused"] is False


def test_list_documents_since_cursor_narrows_to_later_changes(sample_pdf):
    _ready(sample_pdf, "a.pdf")
    cursor = client.get("/api/documents").json()["cursor"]
    sid2 = _ready(sample_pdf, "b.pdf")
    r = client.get("/api/documents", params={"since": cursor})
    ids_ = {d["id"] for d in r.json()["documents"]}
    assert sid2 in ids_


def test_move_to_front_refuses_an_extracting_drawing(sample_pdf):
    meta = save_pdf(client, sample_pdf, "a.pdf")
    sid = meta["session_id"]
    start_pdf(client, sid)
    # start_pdf only leaves the drawing `queued` -- move_to_front ALLOWS
    # that state (it is the only one it makes sense for). claim_next() moves
    # it to `extracting` without running the stub worker loop, which is the
    # state this test is actually named for.
    main.runtime().repo.claim_next()
    r = client.post(f"/api/documents/{sid}/move_to_front")
    assert r.status_code == 409, r.text


def test_move_to_front_bumps_a_queued_drawing_ahead(sample_pdf):
    m1 = save_pdf(client, sample_pdf, "a.pdf")
    m2 = save_pdf(client, sample_pdf, "b.pdf")
    start_pdf(client, m1["session_id"])
    start_pdf(client, m2["session_id"])
    r = client.post(f"/api/documents/{m2['session_id']}/move_to_front")
    assert r.status_code == 200, r.text
    assert main.runtime().repo.claim_next()["id"] == m2["session_id"]


def test_reextract_refused_once_there_is_a_review_event(sample_pdf):
    sid = _ready(sample_pdf, "a.pdf")
    doc = client.post(f"/api/documents/{sid}/open").json()
    client.post(f"/api/session/{sid}/events",
               json={"writer": doc["writer"],
                     "events": [{"seq": 1, "type": "accept", "ids": []}]})
    r = client.post(f"/api/documents/{sid}/reextract")
    assert r.status_code == 409, r.text


def test_reextract_requeues_an_untouched_ready_drawing(sample_pdf):
    sid = _ready(sample_pdf, "a.pdf")
    r = client.post(f"/api/documents/{sid}/reextract")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "queued"
    assert not (main.runtime().data.doc_dir(sid) / "result.json").is_file()


def test_pause_then_resume_the_queue():
    assert client.post("/api/queue/pause").json() == {"paused": True}
    assert client.get("/api/documents").json()["queue"]["paused"] is True
    assert client.post("/api/queue/resume").json() == {"paused": False}
    assert client.get("/api/documents").json()["queue"]["paused"] is False
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_api_worklist.py -q`
Expected: FAIL — `404 Not Found` for `GET /api/documents` (no such route yet).

- [ ] **Step 3: Implement** — in `app/main.py`:

Change the import line that currently reads:

```python
from app.batch.staging import clean_batch_name
```

to:

```python
from app.batch.staging import clean_batch_name, out_of_scope
```

Add, after `get_document` and before `cancel_document`:

```python
@app.get("/api/documents")
def list_documents(batch_id: Optional[str] = None, state: Optional[str] = None,
                   search: Optional[str] = None, needs_me: bool = False,
                   since: Optional[str] = None):
    """The work list (batch design §3): every drawing, or a filtered slice,
    diffable by `since` for the 3 s poll. Flagged/open/unchecked are
    backfilled lazily here for a row whose review record predates migration
    4 (CLAUDE.md §4: NULL means "not measured", and this is the one place
    that ever measures it after the fact)."""
    rt = runtime()
    rows = rt.repo.list_documents(batch_id=batch_id, state=state, search=search,
                                  needs_me=needs_me, since=since)
    out = []
    cursor = since
    for r in rows:
        if r["flagged_total"] is None and r["state"] in ("ready", "in_review", "finished"):
            try:
                counts = rt.store.progress(r["id"])
                rt.repo.update_progress(r["id"], counts)
                r = {**r, **counts}
            except ReviewError as e:
                log.warning("progress backfill failed for %s: %s", r["id"][:8], type(e).__name__)
        out.append({
            "id": r["id"], "file_name": r["file_name"], "rel_path": r["rel_path"],
            "batch_id": r["batch_id"], "batch_name": r["batch_name"], "state": r["state"],
            "error_type": r["error_type"], "priority": r["priority"],
            "flagged_total": r["flagged_total"], "flagged_open": r["flagged_open"],
            "unchecked": r["unchecked"],
            "out_of_scope": out_of_scope(r["pages"], r["oversized"]),
            "allowed_out_of_scope": bool(r["allowed_out_of_scope"]),
            "final_revision": r["final_revision"], "exported_revision": r["exported_revision"],
            "updated_at": r["updated_at"],
        })
        if r["updated_at"] and (cursor is None or r["updated_at"] > cursor):
            cursor = r["updated_at"]
    if cursor is None:
        # Nothing matched and no `since` was given: a cursor must still go
        # out, or the client's next poll could never narrow -- the server's
        # own clock is a safe start point since no row exists before it.
        cursor = rt.repo.now().isoformat(timespec="microseconds")
    summary = rt.repo.queue_summary()
    summary["eta_seconds"] = rt.worker.eta_seconds(summary["queued"])
    return {"documents": out, "cursor": cursor, "queue": summary}


@app.post("/api/documents/{doc_id}/move_to_front")
def move_document_to_front(doc_id: str):
    """Queue control (batch design §2): priority = current max + 1."""
    _doc_dir(doc_id)
    runtime().repo.move_to_front(doc_id)
    return _doc_view(doc_id)


@app.post("/api/documents/{doc_id}/reextract")
def reextract_document(doc_id: str):
    """Re-run extraction for a `ready` drawing nobody has reviewed yet
    (batch design §2 queue controls): refused the moment a single review
    event exists -- Retry (failed/cancelled only) is the control for a
    drawing no one's work is at stake in; this one discards a proposal."""
    work = _doc_dir(doc_id)
    rt = runtime()
    doc = rt.repo.get(doc_id)
    if doc["state"] != "ready":
        raise InvalidTransition(f"document is {doc['state']}, not ready")
    if rt.store.has_events(doc_id):
        raise InvalidTransition(
            "this drawing has review events; re-extraction would discard them")
    (work / "result.json").unlink(missing_ok=True)
    rt.repo.reextract(doc_id)
    try:
        rt.worker.wake()
    except Exception as e:
        log.warning("document %s: worker not woken: %s", doc_id[:8], type(e).__name__)
    return _doc_view(doc_id)


@app.post("/api/queue/pause")
def pause_queue():
    runtime().repo.set_paused(True)
    return {"paused": True}


@app.post("/api/queue/resume")
def resume_queue():
    rt = runtime()
    rt.repo.set_paused(False)
    try:
        rt.worker.wake()
    except Exception as e:
        log.warning("queue resume: worker not woken: %s", type(e).__name__)
    return {"paused": False}
```

Now wire progress recompute into the two places a review changes. In `post_events`, replace:

```python
    contiguous = rt.store.append(session_id, req.writer, req.events)
    if contiguous > 0:
        # Only what the store actually ACKNOWLEDGED moves the drawing --
        # events above a gap are unacked and the next open() trims them, so
        # crediting the request's own max seq would move the state for
        # events that never actually landed.
        rt.repo.events_appended(session_id, contiguous)
    return {"contiguous": contiguous}
```

with:

```python
    contiguous = rt.store.append(session_id, req.writer, req.events)
    if contiguous > 0:
        # Only what the store actually ACKNOWLEDGED moves the drawing --
        # events above a gap are unacked and the next open() trims them, so
        # crediting the request's own max seq would move the state for
        # events that never actually landed.
        rt.repo.events_appended(session_id, contiguous)
        try:
            rt.repo.update_progress(session_id, rt.store.progress(session_id))
        except ReviewError as e:
            # The work list's counts simply stay stale until the next
            # change or the lazy backfill in list_documents() catches up --
            # never worth failing an otherwise-successful append over.
            log.warning("progress recompute failed for %s: %s", session_id[:8], type(e).__name__)
    return {"contiguous": contiguous}
```

And in `seal_session`, replace the final two lines:

```python
    rt.repo.mark_finished(session_id, out["revision"], req.final_seq)
    return out
```

with:

```python
    rt.repo.mark_finished(session_id, out["revision"], req.final_seq)
    try:
        rt.repo.update_progress(session_id, rt.store.progress(session_id))
    except ReviewError as e:
        log.warning("progress recompute failed for %s: %s", session_id[:8], type(e).__name__)
    return out
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_api_worklist.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped

```bash
git add app/main.py tests/test_api_worklist.py
git commit -m "$(cat <<'EOF'
api: add GET /api/documents and the queue-control routes

The work list's read path (filtered, diffable listing + queue summary
with an ETA) and the three queue controls the design lists but no
earlier phase exposed: move to front, re-extract, pause/resume.
Progress counts are now kept current from both places a review changes
(an events append and a seal), not only backfilled lazily.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

> **Milestone: server-list-queue-api** - state must be checkpointed here before continuing.

---

## Milestone 2: router-and-worklist

### Task 7: `router.js` — hash parsing

**Data-integrity: no.**

**Files:**
- Create: `app/static/js/router.js`
- Test: `tests/js/router.test.mjs` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/js/router.test.mjs`:

```javascript
// The hash router (batch design §3): #/ work list, #/staging/<batch> staging,
// #/doc/<id> review. Pure parsing only; the dispatcher that drives the three
// screens is wired in main.js (DOM-only, checked by import, Task 10/12/13).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { parseRoute, docUrl, stagingUrl, navigate } = await import('../../app/static/js/router.js');

test('an empty or bare hash is the work list', () => {
  assert.deepEqual(parseRoute(''), { name: 'worklist' });
  assert.deepEqual(parseRoute('#'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#/'), { name: 'worklist' });
});

test('#/doc/<id> is the review screen', () => {
  const id = 'a'.repeat(32);
  assert.deepEqual(parseRoute(`#/doc/${id}`), { name: 'doc', id });
});

test('#/staging/<batch> is the staging screen', () => {
  const id = 'b'.repeat(32);
  assert.deepEqual(parseRoute(`#/staging/${id}`), { name: 'staging', batchId: id });
});

test('a malformed id falls back to the work list rather than crashing', () => {
  assert.deepEqual(parseRoute('#/doc/not-an-id'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#/doc/'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#/bogus'), { name: 'worklist' });
});

test('docUrl and stagingUrl build the fragment parseRoute reads back', () => {
  // Round-trip: the hash this module WRITES must be one it can also READ,
  // or navigate(docUrl(id)) would navigate to a route parseRoute refuses.
  const id = 'c'.repeat(32);
  assert.deepEqual(parseRoute(docUrl(id)), { name: 'doc', id });
  assert.deepEqual(parseRoute(stagingUrl(id)), { name: 'staging', batchId: id });
});

// `navigate` is the fix for a real bug: `history.replaceState` (what B1's
// setHash() called directly) never fires `hashchange` -- that event only
// fires for a navigation the BROWSER performs. Without this seam, every
// button that only changed the URL (Prev, Skip, Jump, Finish's chain-to-
// next, the post-import redirect) was a silent no-op: the address bar
// changed and the screen did not (decision 10). `setUrl`/`dispatch` are
// injected so this stays testable without `window.history` or a real
// router -- `main.js`'s `go()` (Task 13) is the only caller that passes the
// real ones.
test('navigate writes the url THEN dispatches, in that order', () => {
  const calls = [];
  navigate('#/doc/x', {
    setUrl: (u) => calls.push(['setUrl', u]),
    dispatch: () => calls.push(['dispatch']),
  });
  assert.deepEqual(calls, [['setUrl', '#/doc/x'], ['dispatch']]);
});

test('navigate tolerates missing setUrl/dispatch (never throws)', () => {
  assert.doesNotThrow(() => navigate('#/doc/x'));
  assert.doesNotThrow(() => navigate('#/doc/x', {}));
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/router.test.mjs`
Expected: FAIL — cannot find module `app/static/js/router.js`.

- [ ] **Step 3: Implement** — create `app/static/js/router.js`:

```javascript
// The hash router (batch design §3): #/ work list, #/staging/<batch>
// staging, #/doc/<id> review. Replaces B1's `#doc=<id>` (decision: this
// plan's header, "the hash scheme changes"). Pure parsing + the two builders
// that write it, so a write always round-trips through a read; the dispatcher
// that reacts to `hashchange` and swaps screens is DOM-only and lives in
// main.js (Task 13).
//
// `navigate` exists because `history.replaceState` -- what B1's setHash()
// called directly, and what parseRoute's own callers would reach for again
// here -- never fires `hashchange`. That event is the BROWSER's own
// notification of a navigation it performed (a link click, back/forward, a
// `location.hash =` assignment); a script writing history silently is
// invisible to it. So a button whose only effect was "change the URL" would
// change the address bar and nothing else (decision 10) -- `navigate` is the
// fix: it writes the url AND calls the dispatcher itself. `setUrl`/
// `dispatch` are injected (defaulting to no-ops) so this is testable without
// `window.history` or a real router; main.js's `go()` (Task 13) supplies the
// real `history.replaceState` and `routeFromHash`.

const DOC_ID_RE = /^[0-9a-f]{32}$/;

export function parseRoute(hash) {
  const h = (hash || '').replace(/^#\/?/, '');
  if (!h) return { name: 'worklist' };
  const parts = h.split('/').filter(Boolean);
  if (parts[0] === 'doc' && DOC_ID_RE.test(parts[1] || '')) {
    return { name: 'doc', id: parts[1] };
  }
  if (parts[0] === 'staging' && DOC_ID_RE.test(parts[1] || '')) {
    return { name: 'staging', batchId: parts[1] };
  }
  // An unrecognised route (a stale link, a typo, a future route this build
  // does not know) is never a blank screen -- it falls back to the one
  // place every session can always recover from.
  return { name: 'worklist' };
}

export const docUrl = (id) => `#/doc/${id}`;
export const stagingUrl = (batchId) => `#/staging/${batchId}`;
export const worklistUrl = () => '#/';

export function navigate(url, { setUrl = () => {}, dispatch = () => {} } = {}) {
  setUrl(url);
  dispatch();
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/router.test.mjs`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count under `test_js_state.py` now 166)

```bash
git add app/static/js/router.js tests/js/router.test.mjs
git commit -m "$(cat <<'EOF'
ui: add the hash router's pure route parsing and navigate seam

#/ work list, #/staging/<batch>, #/doc/<id> -- the three screens B3
adds. A malformed or unknown route falls back to the work list instead
of a blank screen. navigate(url, {setUrl, dispatch}) exists because
history.replaceState alone never fires hashchange -- a button that
only changed the URL would otherwise be a silent no-op.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `worklist.js` — pure filter/format helpers

**Data-integrity: no.**

**Files:**
- Create: `app/static/js/worklist.js`
- Test: `tests/js/worklist.test.mjs` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/js/worklist.test.mjs`:

```javascript
// Work-list pure logic (batch design §3): client-side filtering of an
// already-fetched page (the server also filters; this is what a typed
// search or a clicked filter pill applies instantly, before the next poll
// round-trips), and the queue header's text. DOM rendering and polling are
// wired in main.js (Task 10, import-checked only).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { filterDocuments, formatEta, formatQueueHeader } =
  await import('../../app/static/js/worklist.js');

const DOCS = [
  { id: 'a', file_name: 'Flansch.pdf', batch_id: 'b1', state: 'ready' },
  { id: 'b', file_name: 'Welle.pdf', batch_id: 'b1', state: 'in_review' },
  { id: 'c', file_name: 'Gehäuse.pdf', batch_id: 'b2', state: 'failed' },
];

test('filterDocuments narrows by batch, state and a case-insensitive name search', () => {
  assert.deepEqual(filterDocuments(DOCS, { batchId: 'b1' }).map((d) => d.id), ['a', 'b']);
  assert.deepEqual(filterDocuments(DOCS, { state: 'failed' }).map((d) => d.id), ['c']);
  assert.deepEqual(filterDocuments(DOCS, { search: 'flan' }).map((d) => d.id), ['a']);
  assert.deepEqual(filterDocuments(DOCS, { search: 'GEHÄUSE' }).map((d) => d.id), ['c']);
});

test('filterDocuments needsMe keeps only ready and in_review', () => {
  assert.deepEqual(filterDocuments(DOCS, { needsMe: true }).map((d) => d.id), ['a', 'b']);
});

test('filterDocuments with no filters returns everything, unchanged order', () => {
  assert.deepEqual(filterDocuments(DOCS, {}), DOCS);
});

test('formatEta renders hours and minutes, or just minutes under an hour', () => {
  assert.equal(formatEta(7800), '~2h 10m left');
  assert.equal(formatEta(125), '~2m left');
  assert.equal(formatEta(0), '~0m left');
});

test('formatEta is null-safe: no history yet says so, not a bogus number', () => {
  assert.equal(formatEta(null), 'estimating…');
  assert.equal(formatEta(undefined), 'estimating…');
});

test('formatQueueHeader covers idle, paused, and extracting with an estimate', () => {
  assert.equal(formatQueueHeader({ queued: 0, extracting: 0, paused: false, eta_seconds: null }),
    'Queue idle');
  assert.equal(formatQueueHeader({ queued: 4, extracting: 1, paused: true, eta_seconds: null }),
    'Paused · 4 queued');
  assert.equal(formatQueueHeader({ queued: 4, extracting: 1, paused: false, eta_seconds: 600 }),
    'Extracting · 4 queued · ~10m left');
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/worklist.test.mjs`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Implement** — create `app/static/js/worklist.js`:

```javascript
// Work list: client-side filtering of an already-fetched page, and the
// queue header's text (batch design §3). The server applies the same
// filters on GET /api/documents (app/batch/repo.py list_documents) -- this
// is what a typed search or a clicked pill applies instantly, between polls.

export function filterDocuments(docs, { batchId, state, search, needsMe } = {}) {
  let out = docs;
  if (batchId) out = out.filter((d) => d.batch_id === batchId);
  if (state) out = out.filter((d) => d.state === state);
  if (needsMe) out = out.filter((d) => d.state === 'ready' || d.state === 'in_review');
  if (search) {
    const q = search.toLowerCase();
    out = out.filter((d) => d.file_name.toLowerCase().includes(q));
  }
  return out;
}

// "~2h 10m left" / "~2m left" / "estimating…" (no history yet: worker.py's
// eta_seconds is null with zero samples -- never a number built from none).
export function formatEta(seconds) {
  if (seconds == null) return 'estimating…';
  const total = Math.max(0, Math.round(seconds / 60));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `~${h}h ${m}m left` : `~${m}m left`;
}

export function formatQueueHeader(q) {
  if (q.paused) return `Paused · ${q.queued} queued`;
  if (q.queued === 0 && q.extracting === 0) return 'Queue idle';
  return `Extracting · ${q.queued} queued · ${formatEta(q.eta_seconds)}`;
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/worklist.test.mjs`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count now 172)

```bash
git add app/static/js/worklist.js tests/js/worklist.test.mjs
git commit -m "$(cat <<'EOF'
ui: add the work list's pure filter and queue-header formatting

Client-side filtering of an already-fetched page and the "Extracting ·
N queued · ~T left" header text; both are pure so node can hold them
to the server's own filters and worker.py's eta_seconds contract.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `api.js` additions and `badges.js` work-list badges

**Data-integrity: no.**

**Files:**
- Modify: `app/static/js/api.js`, `app/static/js/badges.js`
- Test: `tests/js/api.test.mjs`, `tests/js/badges.test.mjs`

- [ ] **Step 1: Write the failing tests** — append to `tests/js/badges.test.mjs`:

```javascript
const { workListBadges } = await import('../../app/static/js/badges.js');

test('workListBadges shows an exported revision', () => {
  const b = workListBadges({ final_revision: 2, exported_revision: 2, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'finished', error_type: null });
  assert.deepEqual(b, [{ kind: 'exported', text: 'exported r2' }]);
});

test('workListBadges flags a newer revision than what was exported', () => {
  const b = workListBadges({ final_revision: 3, exported_revision: 2, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'finished', error_type: null });
  assert.deepEqual(b, [{ kind: 'exported_stale', text: 'exported r2 (newer revision r3 not exported)' }]);
});

test('workListBadges names a failed drawing\'s error type', () => {
  const b = workListBadges({ final_revision: null, exported_revision: null, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'failed', error_type: 'RuntimeError' });
  assert.deepEqual(b, [{ kind: 'failed', text: 'failed (RuntimeError)' }]);
});

test('workListBadges marks an out-of-scope drawing included by permission', () => {
  const b = workListBadges({ final_revision: null, exported_revision: null, out_of_scope: true,
                             allowed_out_of_scope: true, state: 'ready', error_type: null });
  assert.deepEqual(b, [{ kind: 'out_of_scope', text: 'out of scope (included)' }]);
});

test('workListBadges is empty for an ordinary in-progress drawing', () => {
  assert.deepEqual(workListBadges({ final_revision: null, exported_revision: null, out_of_scope: false,
                                    allowed_out_of_scope: false, state: 'queued', error_type: null }), []);
});
```

Append to `tests/js/api.test.mjs`:

```javascript
const { listDocuments, moveToFront, reextractDocument, pauseQueue, resumeQueue } =
  await import('../../app/static/js/api.js');

test('listDocuments builds the query string from only the filters given', async () => {
  const calls = [];
  globalThis.fetch = async (url) => { calls.push(url); return fakeRes(200, { documents: [], cursor: 'x', queue: {} }); };
  await listDocuments({ batchId: 'b1', needsMe: true });
  assert.equal(calls[0], '/api/documents?batch_id=b1&needs_me=true');
  await listDocuments({});
  assert.equal(calls[1], '/api/documents');
});

test('moveToFront, reextractDocument, pauseQueue, resumeQueue post to the right routes', async () => {
  const calls = [];
  globalThis.fetch = async (url, opts) => { calls.push([url, opts?.method]); return fakeRes(200, {}); };
  await moveToFront('a'.repeat(32));
  await reextractDocument('a'.repeat(32));
  await pauseQueue();
  await resumeQueue();
  assert.deepEqual(calls, [
    [`/api/documents/${'a'.repeat(32)}/move_to_front`, 'POST'],
    [`/api/documents/${'a'.repeat(32)}/reextract`, 'POST'],
    ['/api/queue/pause', 'POST'],
    ['/api/queue/resume', 'POST'],
  ]);
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/api.test.mjs tests/js/badges.test.mjs`
Expected: FAIL — `listDocuments is not a function` / `workListBadges is not a function`.

- [ ] **Step 3: Implement** — in `app/static/js/badges.js`, append:

```javascript
// Work-list badges (batch design §3): exported rN / a newer unexported
// revision / failed + error type / out-of-scope-but-included. Separate from
// badgesFor() above: that one describes a STAGED row's warnings, this one a
// row already past staging, and the two never apply to the same row.
export function workListBadges(doc) {
  const out = [];
  if (doc.final_revision) {
    if (doc.exported_revision === doc.final_revision) {
      out.push({ kind: 'exported', text: `exported r${doc.exported_revision}` });
    } else if (doc.exported_revision) {
      out.push({ kind: 'exported_stale',
                 text: `exported r${doc.exported_revision} (newer revision r${doc.final_revision} not exported)` });
    }
  }
  if (doc.state === 'failed') {
    out.push({ kind: 'failed', text: `failed${doc.error_type ? ` (${doc.error_type})` : ''}` });
  }
  if (doc.out_of_scope && doc.allowed_out_of_scope) {
    out.push({ kind: 'out_of_scope', text: 'out of scope (included)' });
  }
  return out;
}
```

In `app/static/js/api.js`, append (after `downloadExport`):

```javascript
// ----- Work list (batch design §3) -----------------------------------
export async function listDocuments(filters = {}) {
  const params = new URLSearchParams();
  if (filters.batchId) params.set('batch_id', filters.batchId);
  if (filters.state) params.set('state', filters.state);
  if (filters.search) params.set('search', filters.search);
  if (filters.needsMe) params.set('needs_me', 'true');
  if (filters.since) params.set('since', filters.since);
  const qs = params.toString();
  return jsonOrThrow(await fetch(qs ? `/api/documents?${qs}` : '/api/documents'),
                     'Could not load the work list');
}

export const moveToFront = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/move_to_front'), { method: 'POST' }),
             'Could not move the drawing to the front');
export const reextractDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/reextract'), { method: 'POST' }),
             'Could not re-extract the drawing');
export const pauseQueue = async () =>
  jsonOrThrow(await fetch('/api/queue/pause', { method: 'POST' }), 'Could not pause the queue');
export const resumeQueue = async () =>
  jsonOrThrow(await fetch('/api/queue/resume', { method: 'POST' }), 'Could not resume the queue');
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/api.test.mjs tests/js/badges.test.mjs`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count now 179)

```bash
git add app/static/js/api.js app/static/js/badges.js tests/js/api.test.mjs tests/js/badges.test.mjs
git commit -m "$(cat <<'EOF'
ui: add the work-list API client functions and badges

listDocuments/moveToFront/reextractDocument/pauseQueue/resumeQueue
over the routes Task 6 added, and the badge texts (exported rN, a
newer unexported revision, failed + error type, out-of-scope-but-
included) the work list shows per row.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Work-list screen (DOM)

**Data-integrity: no** (client-only; every mutation it triggers goes through already-guarded server routes).

**Files:**
- Modify: `app/static/index.html`, `app/static/js/main.js`, `app/static/styles/components.css`
- Test: `tests/js/worklist.test.mjs` (import-smoke addition)

- [ ] **Step 1: Write the failing test** — append to `tests/js/worklist.test.mjs`:

```javascript
test('initWorkList is exported and callable with no DOM work-list elements present (no-op, no throw)', async () => {
  // A node import/smoke check, not a DOM test (the codebase has none): this
  // fails loudly if the export is renamed or dropped, and passes once the
  // module loads and the function exists and tolerates being called before
  // index.html's elements exist (e.g. from a future screen that imports it
  // eagerly). Guards against exactly the "renamed export, nobody noticed"
  // class of bug this plan's brief calls out.
  const { initWorkList } = await import('../../app/static/js/worklist.js');
  assert.equal(typeof initWorkList, 'function');
});

// A deletion is invisible to the since-cursor poll: GET /api/documents?since=
// only ever returns rows that CHANGED, and a removed row has no updated_at
// to be newer than anything, so it would never be noticed missing. Every
// 20th poll (~1 minute at the 3 s cadence) reconciles the whole list instead
// (decision 11); pure, so the cadence itself -- not the timer that drives
// it -- is what node checks.
test('shouldFullRefresh fires on every 20th poll, never the others', async () => {
  const { shouldFullRefresh } = await import('../../app/static/js/worklist.js');
  const fires = [];
  for (let i = 1; i <= 41; i++) if (shouldFullRefresh(i)) fires.push(i);
  assert.deepEqual(fires, [20, 40]);
});

test('shouldFullRefresh never fires on poll 0 (nothing has polled yet)', async () => {
  const { shouldFullRefresh } = await import('../../app/static/js/worklist.js');
  assert.equal(shouldFullRefresh(0), false);
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/worklist.test.mjs`
Expected: FAIL — `initWorkList` is `undefined`.

- [ ] **Step 3: Implement.**

In `app/static/index.html`, add a new top-level screen section right after `<div id="app">`'s opening header/body (i.e. as a sibling of `<header id="shell-header">`, wrapping the existing `<main id="shell-body">` so the review screen becomes one of three toggleable screens):

```html
  <!-- ===== WORK LIST (batch design §3) ========================= -->
  <section id="screen-worklist" hidden>
    <div class="wl-toolbar">
      <button class="btn primary" id="wl-import"><svg class="icon" width="14" height="14"><use href="#i-plus"/></svg> Import</button>
      <div class="search" id="wl-search-wrap">
        <svg width="13" height="13"><use href="#i-search"/></svg>
        <input type="search" id="wl-search" placeholder="Search by file name…" autocomplete="off" />
      </div>
      <input type="file" id="wl-import-input" multiple accept=".pdf,.zip" class="sr" />
      <select id="wl-batch-filter"><option value="">All batches</option></select>
      <select id="wl-state-filter">
        <option value="">All states</option>
        <option value="staged">Staged</option>
        <option value="queued">Queued</option>
        <option value="extracting">Extracting</option>
        <option value="ready">Ready</option>
        <option value="in_review">In review</option>
        <option value="finished">Finished</option>
        <option value="failed">Failed</option>
        <option value="cancelled">Cancelled</option>
      </select>
      <button class="btn ghost" id="wl-needs-me">Needs me</button>
      <div class="spacer"></div>
      <span id="wl-queue-header" class="wl-queue-header">Queue idle</span>
      <button class="btn ghost" id="wl-pause-resume">Pause</button>
    </div>
    <table class="wl-table">
      <thead>
        <tr>
          <th class="check"><input type="checkbox" id="wl-select-all" /></th>
          <th>File</th><th>Batch</th><th>State</th><th>Flagged</th><th>Unchecked</th>
          <th>Last change</th><th>Badges</th>
        </tr>
      </thead>
      <tbody id="wl-body"></tbody>
    </table>
    <div class="wl-bulk" id="wl-bulk" hidden>
      <span id="wl-bulk-count">0 selected</span>
      <button class="btn ghost" id="wl-bulk-front">Move to front</button>
      <button class="btn ghost" id="wl-bulk-cancel">Cancel</button>
      <button class="btn ghost" id="wl-bulk-retry">Retry</button>
      <button class="btn ghost" id="wl-bulk-remove">Remove</button>
      <button class="btn ghost" id="wl-bulk-export">Export selected</button>
    </div>
  </section>

  <!-- ===== BODY (split layout) -- the review screen ============= -->
  <main id="shell-body" hidden>
```

(the closing `</main>` already present stays where it is; only the opening tag gains `hidden` and the new section is inserted before it).

Create `app/static/js/worklist.js`'s DOM half by appending to the file (after the pure functions from Task 8):

```javascript
// A deletion is invisible to the since-cursor poll (GET /api/documents?since=
// only ever returns rows that CHANGED; a removed row has no updated_at to be
// newer than anything, so it would simply never be noticed missing). Every
// 20th poll (~1 minute at the 3 s cadence) does a full, since-less reload
// instead (decision 11). Pure, so the CADENCE is node-testable without a
// timer; the counter that drives it lives in the DOM half below.
export function shouldFullRefresh(pollCount) {
  return pollCount > 0 && pollCount % 20 === 0;
}

// ----- DOM (checked by import only; see tests/js/worklist.test.mjs) ---
let _docs = [];
let _cursor = null;
let _pollTimer = null;
let _selected = new Set();

function row(d) {
  const tr = document.createElement('tr');
  tr.dataset.id = d.id;
  const checked = _selected.has(d.id) ? 'checked' : '';
  const badges = [...workListBadgesFor(d)].map((b) => b.text).join(' · ');
  tr.innerHTML = `
    <td class="check"><input type="checkbox" class="wl-row-check" ${checked} /></td>
    <td><a href="${docUrlFor(d)}">${escapeHtml(d.file_name)}</a>${d.rel_path ? ` <span class="muted">${escapeHtml(d.rel_path)}</span>` : ''}</td>
    <td>${escapeHtml(d.batch_name || '')}</td>
    <td><span class="state-chip state-${d.state}">${d.state.replaceAll('_', ' ')}</span></td>
    <td>${d.flagged_open ?? '—'}/${d.flagged_total ?? '—'}</td>
    <td>${d.unchecked ?? '—'}</td>
    <td>${d.updated_at || ''}</td>
    <td class="muted">${badges}</td>`;
  return tr;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;' }[c]));
}

function render() {
  const body = document.getElementById('wl-body');
  if (!body) return;
  const filtered = filterDocuments(_docs, currentFilters());
  body.innerHTML = '';
  for (const d of filtered) body.appendChild(row(d));
  const header = document.getElementById('wl-queue-header');
  if (header) header.textContent = formatQueueHeader(_lastQueue || { queued: 0, extracting: 0, paused: false, eta_seconds: null });
  updateBulkBar();
}

let _lastQueue = null;

function currentFilters() {
  return {
    batchId: document.getElementById('wl-batch-filter')?.value || undefined,
    state: document.getElementById('wl-state-filter')?.value || undefined,
    search: document.getElementById('wl-search')?.value || undefined,
    needsMe: document.getElementById('wl-needs-me')?.classList.contains('active') || false,
  };
}

function updateBulkBar() {
  const bar = document.getElementById('wl-bulk');
  if (!bar) return;
  bar.hidden = _selected.size === 0;
  const count = document.getElementById('wl-bulk-count');
  if (count) count.textContent = `${_selected.size} selected`;
}

export function initWorkList({ listDocuments, moveToFront, cancelDocument, retryDocument,
                               deleteDocument, downloadExport, pauseQueue, resumeQueue,
                               toast, badgesFor: workListBadgesFn, onOpenDoc } = {}) {
  // Every dependency is injected and every DOM lookup is guarded with `?.`:
  // this function is called from main.js at init() time, but the node
  // import-smoke test above calls it with none of index.html's elements
  // present (and no deps), and it must not throw either way.
  workListBadgesFor = workListBadgesFn || (() => []);
  docUrlFor = (d) => `#/doc/${d.id}`;

  async function refresh(full = false) {
    if (!listDocuments) return;
    try {
      const body = await listDocuments(full ? {} : { since: _cursor || undefined });
      if (full) _docs = body.documents;
      else {
        const byId = new Map(_docs.map((d) => [d.id, d]));
        for (const d of body.documents) byId.set(d.id, d);
        _docs = [...byId.values()];
      }
      _cursor = body.cursor;
      _lastQueue = body.queue;
      render();
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not refresh the work list', msg: String(err?.message || err) });
    }
  }

  document.getElementById('wl-search')?.addEventListener('input', () => render());
  document.getElementById('wl-batch-filter')?.addEventListener('change', () => render());
  document.getElementById('wl-state-filter')?.addEventListener('change', () => render());
  document.getElementById('wl-needs-me')?.addEventListener('click', (e) => {
    e.currentTarget.classList.toggle('active');
    render();
  });
  document.getElementById('wl-select-all')?.addEventListener('change', (e) => {
    _selected = e.target.checked ? new Set(filterDocuments(_docs, currentFilters()).map((d) => d.id)) : new Set();
    render();
  });
  document.getElementById('wl-body')?.addEventListener('change', (e) => {
    if (!e.target.classList.contains('wl-row-check')) return;
    const id = e.target.closest('tr')?.dataset.id;
    if (!id) return;
    if (e.target.checked) _selected.add(id); else _selected.delete(id);
    updateBulkBar();
  });
  document.getElementById('wl-pause-resume')?.addEventListener('click', async (e) => {
    try {
      if (_lastQueue?.paused) await resumeQueue?.(); else await pauseQueue?.();
      await refresh(true);
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not change the queue', msg: String(err?.message || err) });
    }
  });
  document.getElementById('wl-bulk-front')?.addEventListener('click', () => runBulk(moveToFront, toast));
  document.getElementById('wl-bulk-cancel')?.addEventListener('click', () => runBulk(cancelDocument, toast));
  document.getElementById('wl-bulk-retry')?.addEventListener('click', () => runBulk(retryDocument, toast));
  document.getElementById('wl-bulk-remove')?.addEventListener('click', () => runBulk(deleteDocument, toast));
  document.getElementById('wl-bulk-export')?.addEventListener('click', () => runExport(downloadExport, toast));

  async function runBulk(fn, toastFn) {
    if (!fn) return;
    const ids = [..._selected];
    let failed = 0;
    for (const id of ids) {
      try { await fn(id); } catch { failed++; }
    }
    _selected = new Set();
    await refresh(true);
    if (failed) toastFn?.({ kind: 'warn', title: 'Some drawings were not changed', msg: `${failed} of ${ids.length} failed.` });
  }

  async function runExport(downloadFn, toastFn) {
    if (!downloadFn) return;
    const ids = [..._selected].filter((id) => _docs.find((d) => d.id === id)?.state === 'finished');
    const skipped = _selected.size - ids.length;
    for (const id of ids) {
      try { await downloadFn(id, 'xlsx', _docs.find((d) => d.id === id).final_revision); } catch { /* per-file failure; continue */ }
      try { await downloadFn(id, 'pdf', _docs.find((d) => d.id === id).final_revision); } catch { /* per-file failure; continue */ }
    }
    if (skipped) toastFn?.({ kind: 'warn', title: 'Some drawings were skipped',
                             msg: `${skipped} selected drawing(s) are not finished yet.` });
  }

  let _pollCount = 0;
  function startPolling() {
    clearTimeout(_pollTimer);
    const tick = async () => {
      if (!document.hidden) {
        _pollCount++;
        await refresh(shouldFullRefresh(_pollCount));
      }
      _pollTimer = setTimeout(tick, 3000);
    };
    tick();
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });

  refresh(true);
  startPolling();
  // getDocs: the review bar (Task 13) navigates over the SAME fetched list
  // rather than polling a second time, so Prev/Skip/Jump/Finish's "next
  // drawing" can never see a different snapshot than what the work list
  // itself is showing.
  return { refresh, getDocs: () => _docs };
}

let workListBadgesFor = () => [];
let docUrlFor = (d) => `#/doc/${d.id}`;
```

In `app/static/js/main.js`, add to the imports:

```javascript
import { initWorkList } from './worklist.js';
import { workListBadges } from './badges.js';
import { listDocuments, moveToFront, reextractDocument, pauseQueue, resumeQueue, createBatch } from './api.js';
import { importFiles } from './upload.js';
```

and call it from `init()` (added as the last line of `init()`, before `resumeFromHash()`'s existing call, since Task 13 replaces `resumeFromHash` with the router — this task only wires the work list's own init so it renders once a route shows it):

```javascript
  workList = initWorkList({
    listDocuments, moveToFront, cancelDocument, retryDocument, deleteDocument,
    downloadExport, pauseQueue, resumeQueue, toast, badgesFor: workListBadges,
  });
  wireImport();
```

Declare `let workList = null;` alongside the other module-level `let` declarations near the top of the file (Task 13 reads it back via `workList.getDocs()`).

Wire the `+ Import` button and its hidden multi-file input (ZIP import of several PDFs is the common case B3 adds; a folder picker via `webkitdirectory` is a cheap follow-on but not required to cover the design's staging flow, since a ZIP already carries folder structure through B2's unpacker):

```javascript
function wireImport() {
  document.getElementById('wl-import')?.addEventListener('click', () => {
    document.getElementById('wl-import-input')?.click();
  });
  document.getElementById('wl-import-input')?.addEventListener('change', async (e) => {
    const files = [...e.target.files];
    e.target.value = '';   // a picker holding the same files fires no 'change' on a re-pick otherwise
    if (!files.length) return;
    setBusy('Importing…');
    try {
      const name = files.length === 1 ? files[0].name
        : new Date().toLocaleString();   // createBatch's own UTC fallback is used if this is omitted entirely
      const { id: batchId } = await createBatch(name);
      const results = await importFiles(files, { batchId });
      const failed = results.filter((r) => !r.ok).length;
      if (failed) {
        // A disk-full (507) or any other fatal failure lands here too --
        // the staging view's own Discard button (Task 12) is the recovery
        // for a draft that can never complete, so this points at it rather
        // than retrying forever itself.
        toast({ kind: 'warn', title: 'Some files did not import',
                msg: `${failed} of ${files.length} failed. Open the import to retry or discard it.` });
      }
      setHash(stagingUrl(batchId));
    } catch (err) {
      toast({ kind: 'error', title: 'Could not start the import', msg: String(err?.message || err) });
    } finally {
      setIdle();
    }
  });
}
```

(`stagingUrl` comes from `router.js`, imported in Task 13 — if Task 10 is implemented before Task 13 in a strict reading of this plan's order, add `import { stagingUrl } from './router.js';` here already; Task 13 then reuses the same import line rather than duplicating it.)

Add minimal styling to `app/static/styles/components.css` (append):

```css
.wl-toolbar { display: flex; gap: 8px; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.wl-queue-header { color: var(--fg-muted); font-size: var(--fs-xs); }
.wl-table { width: 100%; border-collapse: collapse; }
.wl-table th, .wl-table td { padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; font-size: var(--fs-sm); }
.wl-bulk { display: flex; gap: 8px; align-items: center; padding: 8px 14px; border-top: 1px solid var(--border); }
.state-chip { padding: 1px 8px; border-radius: 10px; background: var(--bg-hover); font-size: var(--fs-xs); }
.muted { color: var(--fg-muted); font-size: var(--fs-xs); }
```

(`.muted` is new — no earlier screen needed a dimmed inline label; the staging view (Task 12), the review bar and both banners (Task 13/14/15) all use it.)

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/worklist.test.mjs`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count now 182)

```bash
git add app/static/index.html app/static/js/main.js app/static/js/worklist.js app/static/styles/components.css tests/js/worklist.test.mjs
git commit -m "$(cat <<'EOF'
ui: add the work-list screen

A table of every drawing with filters, a queue header, pause/resume,
and bulk move-to-front/cancel/retry/remove/export-selected -- all over
routes B1/B2/Task 6 already guard server-side, so the bulk actions are
plain per-id loops, not new server surface.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

> **Milestone: router-and-worklist** - state must be checkpointed here before continuing.

---

## Milestone 3: staging-review-bar-banners

### Task 11: `reviewbar.js` — pure navigation

**Data-integrity: no.**

**Files:**
- Create: `app/static/js/reviewbar.js`
- Test: `tests/js/reviewbar.test.mjs` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/js/reviewbar.test.mjs`:

```javascript
// Review-bar navigation (batch design §3): Prev/Skip move through a batch's
// ready/in-review drawings in list order; after Finish, the next ready
// drawing in the SAME batch first, then any batch; Jump offers every
// ready/in-review drawing regardless of batch. Pure; the bar's DOM (label,
// buttons, Ctrl+K palette) is wired in main.js (Task 13).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { navOrder, prevId, nextId, nextAfterFinish, jumpCandidates, skip } =
  await import('../../app/static/js/reviewbar.js');
const { navigate } = await import('../../app/static/js/router.js');

const DOCS = [
  { id: 'a', batch_id: 'b1', state: 'ready' },
  { id: 'b', batch_id: 'b1', state: 'in_review' },
  { id: 'c', batch_id: 'b1', state: 'finished' },
  { id: 'd', batch_id: 'b2', state: 'ready' },
  { id: 'e', batch_id: 'b2', state: 'extracting' },
];

test('navOrder keeps only ready/in_review drawings of one batch, in list order', () => {
  assert.deepEqual(navOrder(DOCS, 'b1').map((d) => d.id), ['a', 'b']);
});

test('prevId returns the id before the current one in the batch, or null at the start', () => {
  assert.equal(prevId(DOCS, 'b1', 'b'), 'a');
  assert.equal(prevId(DOCS, 'b1', 'a'), null);
});

test('nextAfterFinish prefers the next ready/in_review drawing in the same batch', () => {
  assert.deepEqual(nextAfterFinish(DOCS, 'b1', 'a'), { status: 'next', id: 'b' });
});

test('nextAfterFinish falls back to any other batch once this one is exhausted', () => {
  const docs = [{ id: 'a', batch_id: 'b1', state: 'ready' }, ...DOCS.slice(3)];
  assert.deepEqual(nextAfterFinish(docs, 'b1', 'a'), { status: 'next', id: 'd' });
});

test('nextAfterFinish says "extracting" when nothing is ready but something is on its way', () => {
  const docs = [{ id: 'a', batch_id: 'b1', state: 'ready' }, { id: 'e', batch_id: 'b2', state: 'extracting' }];
  assert.deepEqual(nextAfterFinish(docs, 'b1', 'a'), { status: 'extracting' });
});

test('nextAfterFinish says "done" when nothing is ready and nothing is extracting or queued', () => {
  assert.deepEqual(nextAfterFinish([{ id: 'a', batch_id: 'b1', state: 'ready' }], 'b1', 'a'),
    { status: 'done' });
});

test('jumpCandidates is every ready/in_review drawing across every batch', () => {
  assert.deepEqual(jumpCandidates(DOCS).map((d) => d.id), ['a', 'b', 'd']);
});

// skip() is the fix for decision 10: a bare hash write (what "Skip for now"
// did before this plan's revision) never fires hashchange, so pressing it
// changed the URL and nothing on screen. skip() takes `navigate` itself
// (router.js's seam) rather than returning an id for the caller to
// setHash() with -- that is exactly the shape of mistake that caused the
// bug, and a caller cannot make it again if there is no id-only return path
// to misuse. The real `navigate` is used here with a stubbed `dispatch` in
// place of routeFromHash, so this test is the "a skip actually loads the
// next drawing (stub routeFromHash)" case directly.
test('skip navigates to the next drawing in the same batch', () => {
  const dispatched = [];
  const id = skip(DOCS, 'b1', 'a', {
    navigate: (url) => navigate(url, { dispatch: () => dispatched.push(url) }),
    docUrl: (i) => `#/doc/${i}`,
  });
  assert.equal(id, 'b');
  assert.deepEqual(dispatched, ['#/doc/b']);
});

test('skip falls back to any other batch once this one is exhausted', () => {
  const docs = [{ id: 'a', batch_id: 'b1', state: 'ready' }, ...DOCS.slice(3)];
  const dispatched = [];
  const id = skip(docs, 'b1', 'a', {
    navigate: (url) => navigate(url, { dispatch: () => dispatched.push(url) }),
    docUrl: (i) => `#/doc/${i}`,
  });
  assert.equal(id, 'd');
  assert.deepEqual(dispatched, ['#/doc/d']);
});

test('skip does nothing -- and never calls navigate -- when nothing is left to skip to', () => {
  const calls = [];
  const id = skip([{ id: 'a', batch_id: 'b1', state: 'ready' }], 'b1', 'a',
    { navigate: (url) => calls.push(url), docUrl: (i) => `#/doc/${i}` });
  assert.equal(id, null);
  assert.deepEqual(calls, []);
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/reviewbar.test.mjs`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Implement** — create `app/static/js/reviewbar.js`:

```javascript
// Review-bar navigation (batch design §3). Pure: `docs` is the same array
// the work list already polls (app/static/js/worklist.js), filtered here to
// one batch's reviewable drawings. "List order" is the array's own order
// (server-sorted by rowid, the true staging order -- app/batch/repo.py's
// list_documents; see this plan's decision 8).

const REVIEWABLE = (d) => d.state === 'ready' || d.state === 'in_review';

export function navOrder(docs, batchId) {
  return docs.filter((d) => d.batch_id === batchId && REVIEWABLE(d));
}

export function prevId(docs, batchId, currentId) {
  const order = navOrder(docs, batchId);
  const i = order.findIndex((d) => d.id === currentId);
  return i > 0 ? order[i - 1].id : null;
}

// Mirrors prevId for "Skip for now", which moves forward without changing
// anything (design §3): same list, next entry, or null at the end.
export function nextId(docs, batchId, currentId) {
  const order = navOrder(docs, batchId);
  const i = order.findIndex((d) => d.id === currentId);
  return i >= 0 && i < order.length - 1 ? order[i + 1].id : null;
}

// After Finish (design §3): the next ready/in-review drawing in the SAME
// batch (the one just finished is no longer in that list -- its state has
// moved past `ready`/`in_review` by the time this runs), else the first
// ready/in-review drawing in ANY batch, else "extracting" if something is
// still on its way (queued or extracting, anywhere), else "done".
export function nextAfterFinish(docs, batchId, finishedId) {
  const sameBatch = navOrder(docs, batchId).filter((d) => d.id !== finishedId);
  if (sameBatch.length) return { status: 'next', id: sameBatch[0].id };
  const anyBatch = jumpCandidates(docs).filter((d) => d.id !== finishedId);
  if (anyBatch.length) return { status: 'next', id: anyBatch[0].id };
  const onTheWay = docs.some((d) => d.state === 'queued' || d.state === 'extracting');
  return onTheWay ? { status: 'extracting' } : { status: 'done' };
}

export function jumpCandidates(docs) {
  return docs.filter(REVIEWABLE);
}

// "Skip for now" (design §3): the next reviewable drawing after the current
// one in the SAME batch, falling back to any other batch's -- the same
// fallback nextAfterFinish uses, since running out of drawings to skip to
// in one batch is not meaningfully different from finishing it. Takes
// `navigate` (router.js's seam) and `docUrl` as dependencies and calls
// `navigate` itself, rather than returning an id for the caller to write
// into the hash with a plain setHash() -- that shape of call is exactly
// the bug decision 10 fixes (history.replaceState alone never dispatches),
// so there is no id-only return path left for a caller to misuse the same
// way again. Returns the id it navigated to, or null if there was nowhere
// to go (and navigate is then never called at all).
export function skip(docs, batchId, currentId, { navigate: nav, docUrl }) {
  const id = nextId(docs, batchId, currentId)
    || jumpCandidates(docs).find((d) => d.id !== currentId)?.id
    || null;
  if (id) nav(docUrl(id));
  return id;
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/reviewbar.test.mjs`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count now 192)

```bash
git add app/static/js/reviewbar.js tests/js/reviewbar.test.mjs
git commit -m "$(cat <<'EOF'
ui: add the review bar's pure navigation (prev, skip, next after finish, jump)

Operates on the same document list the work list polls, filtered to
one batch's ready/in-review drawings; after Finish it falls back from
the same batch to any batch to "extracting" to "done".

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Staging view screen

**Data-integrity: no** (every mutation goes through B2's already-guarded `/include`, `/start`, `DELETE /api/documents/{id}`, `DELETE /api/batches/{id}` routes).

**Files:**
- Create: `app/static/js/stagingview.js`
- Modify: `app/static/index.html`
- Test: `tests/js/stagingview.test.mjs` (new, import-smoke only)

- [ ] **Step 1: Write the failing test** — create `tests/js/stagingview.test.mjs`:

```javascript
// Staging screen: mostly DOM (a draft's rows, Include toggles, Start,
// Discard, remove-one-row), so -- like worklist.js's DOM half -- this is an
// import/smoke check, not a behaviour test: it catches a renamed or dropped
// export, which is the failure mode that matters once main.js wires this in
// (Task 13).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../../app/static/js/stagingview.js');

test('initStagingView is exported and callable with no DOM staging elements present', () => {
  assert.equal(typeof mod.initStagingView, 'function');
  assert.doesNotThrow(() => mod.initStagingView({}));
});

test('ignoredSummary formats the capped-list-plus-totals shape uploads.py sends', () => {
  const { ignoredSummary } = mod;
  assert.equal(ignoredSummary({ ignored: [], ignored_total: 0, ignored_by_reason: {} }), '');
  assert.equal(
    ignoredSummary({ ignored: ['a.txt'], ignored_total: 1, ignored_by_reason: { non_pdf: 1 } }),
    '1 file ignored (non pdf)');
  assert.equal(
    ignoredSummary({ ignored: [], ignored_total: 1200, ignored_by_reason: { non_pdf: 1000, unreadable_entry: 200 } }),
    '1200 files ignored (non pdf, unreadable entry)');
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/stagingview.test.mjs`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Implement** — create `app/static/js/stagingview.js`:

```javascript
// Staging screen (batch design §2): a draft's rows with badges and an
// Include checkbox, Start batch, Discard, and removing a single row. Reuses
// badgesFor (app/static/js/badges.js) so the staging view and the old
// single-drawing confirm screen say the same words for the same row.
import { badgesFor } from './badges.js';

// "N file(s) ignored (<reasons>)" from the shape uploads.py returns
// (app/batch/staging.py upload_view: `ignored` the capped list, `ignored_total`
// the true count, `ignored_by_reason` a dict of reason -> count) -- carried
// from B2 (CLAUDE.md B3 note): shown here, never in a log (CLAUDE.md §1).
export function ignoredSummary(upload) {
  const total = upload?.ignored_total || 0;
  if (!total) return '';
  const reasons = Object.keys(upload.ignored_by_reason || {})
    .map((r) => r.replaceAll('_', ' '));
  return `${total} file${total === 1 ? '' : 's'} ignored${reasons.length ? ` (${reasons.join(', ')})` : ''}`;
}

function rowHtml(doc) {
  const badges = badgesFor(doc).map((b) => b.text).join(' · ');
  const disabled = doc.unreadable ? 'disabled' : '';
  return `
    <tr data-id="${doc.id}">
      <td><input type="checkbox" class="sv-include" ${doc.included ? 'checked' : ''} ${disabled} /></td>
      <td>${doc.file_name}${doc.rel_path ? ` <span class="muted">${doc.rel_path}</span>` : ''}</td>
      <td>${doc.pages ?? '—'}</td>
      <td>${(doc.size / 1024).toFixed(0)} KB</td>
      <td class="muted">${badges}</td>
      <td><button class="btn ghost sv-remove" data-id="${doc.id}">Remove</button></td>
    </tr>`;
}

export function initStagingView({ getBatch, setIncluded, startBatch, discardBatch,
                                  deleteDocument, toast, onStarted } = {}) {
  let batchId = null;
  let view = null;

  async function load(id) {
    batchId = id;
    if (!getBatch) return;
    try {
      view = await getBatch(id);
      render();
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not load the import', msg: String(err?.message || err) });
    }
  }

  function render() {
    const body = document.getElementById('sv-body');
    if (!body || !view) return;
    body.innerHTML = view.documents.map(rowHtml).join('');
    const uploadsEl = document.getElementById('sv-uploads');
    if (uploadsEl) {
      uploadsEl.textContent = view.uploads.map(ignoredSummary).filter(Boolean).join(' · ');
    }
    const counts = document.getElementById('sv-counts');
    if (counts) {
      counts.textContent = `${view.counts.included} included · ${view.counts.excluded} excluded`
        + (view.counts.uploading ? ` · ${view.counts.uploading} still uploading` : '');
    }
    const empty = document.getElementById('sv-empty');
    if (empty) {
      // Carried from B2: an empty archive (every entry ignored, or a ZIP
      // with no PDFs at all) must say so, not just show a bare zero-row
      // table that looks like a loading glitch.
      empty.hidden = view.documents.length > 0;
    }
  }

  document.getElementById('sv-body')?.addEventListener('change', async (e) => {
    if (!e.target.classList.contains('sv-include')) return;
    const id = e.target.closest('tr')?.dataset.id;
    if (!id) return;
    try {
      await setIncluded?.(id, e.target.checked);
      await load(batchId);
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not change the selection', msg: String(err?.message || err) });
      await load(batchId);
    }
  });
  document.getElementById('sv-body')?.addEventListener('click', async (e) => {
    if (!e.target.classList.contains('sv-remove')) return;
    const id = e.target.dataset.id;
    try {
      await deleteDocument?.(id);
      await load(batchId);
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not remove the drawing', msg: String(err?.message || err) });
    }
  });
  document.getElementById('sv-start')?.addEventListener('click', async () => {
    try {
      const expectedIds = view.documents.map((d) => d.id);
      const res = await startBatch?.(batchId, expectedIds);
      onStarted?.(res);
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not start the import', msg: String(err?.message || err) });
      await load(batchId);
    }
  });
  document.getElementById('sv-discard')?.addEventListener('click', async () => {
    if (!confirm('Discard this import? Its staged drawings and uploaded files are removed.')) return;
    try { await discardBatch?.(batchId); } catch { /* best-effort, like main.js's existing discardBatch calls */ }
    onStarted?.({ discarded: true });
  });

  return { load };
}
```

Add to `app/static/index.html`, as another sibling screen next to `#screen-worklist`:

```html
  <!-- ===== STAGING (batch design §2) ============================ -->
  <section id="screen-staging" hidden>
    <div class="wl-toolbar">
      <a href="#/" class="btn ghost">← Work list</a>
      <span id="sv-counts" class="wl-queue-header"></span>
      <div class="spacer"></div>
      <span id="sv-uploads" class="muted"></span>
      <button class="btn ghost" id="sv-discard">Discard</button>
      <button class="btn primary" id="sv-start">Start batch</button>
    </div>
    <p id="sv-empty" class="muted" hidden>This import holds no readable PDF — every file was ignored or none was a PDF.</p>
    <table class="wl-table">
      <thead><tr><th>Include</th><th>File</th><th>Pages</th><th>Size</th><th>Notes</th><th></th></tr></thead>
      <tbody id="sv-body"></tbody>
    </table>
  </section>
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/stagingview.test.mjs`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count now 194)

```bash
git add app/static/js/stagingview.js app/static/index.html tests/js/stagingview.test.mjs
git commit -m "$(cat <<'EOF'
ui: add the staging screen

A draft's rows with Include toggles, per-row removal, Start and
Discard -- the multi-row staging view design §2 asks for, reusing
badgesFor so a staged row reads the same here as it did on the interim
confirm screen.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Router wiring — three screens, resume, review bar DOM

**Data-integrity: yes** (rewires the open/flush/resume flow B1 built and this plan must not regress; the most fragile part of the app per the B1 state file's notes).

**Files:**
- Modify: `app/static/js/main.js`, `app/static/index.html`
- Test: manual verification via the commands below (the behaviour itself is covered by the pure modules' existing node tests plus the Python API tests already passing; this task is where they are wired together, and `tests/js/fuzz_journal.mjs` / `tests/review/test_journal_replay_parity.py` — unchanged by this task — remain the contract that catches a real regression in resume itself)

- [ ] **Step 1: Add the review bar markup** — in `app/static/index.html`, inside `<header id="shell-header">`, replace the `file-chip` div with a review bar that also carries it:

```html
    <div class="review-bar" id="review-bar-nav" hidden>
      <a href="#/" class="btn ghost">← Work list</a>
      <span id="rb-batch">Batch "…"</span>
      <span id="rb-position">1 of 1</span>
      <button class="btn ghost" id="rb-prev">‹ Prev</button>
      <button class="btn ghost" id="rb-skip">Skip for now ›</button>
      <button class="btn ghost" id="rb-jump">Jump to… (Ctrl+K)</button>
      <div class="file-chip" id="file-chip" hidden>
        <svg width="13" height="13"><use href="#i-folder"/></svg>
        <span class="name" id="file-name">—</span>
        <button id="file-close" title="Close drawing"><svg width="11" height="11"><use href="#i-x"/></svg></button>
      </div>
    </div>
```

Add the two banners right after `<div id="app">`'s opening tag:

```html
  <div class="banner banner-warn" id="banner-stale" hidden>
    This drawing was opened in another tab or browser — reload to continue here.
    <span id="banner-stale-count"></span>
  </div>
  <div class="banner banner-warn" id="banner-lagging" hidden>
    Changes not yet saved — retrying in <span id="banner-lagging-countdown">—</span>s.
    <button class="btn ghost" id="banner-lagging-retry">Retry now</button>
  </div>
```

- [ ] **Step 2: Wire the router in `main.js`.**

Add imports:

```javascript
import { parseRoute, docUrl, stagingUrl, worklistUrl, navigate } from './router.js';
import { prevId, nextAfterFinish, jumpCandidates, skip } from './reviewbar.js';
import { initStagingView } from './stagingview.js';
import { bannerFor } from './banner.js';   // Task 14/15
```

Replace the two existing hash functions:

```javascript
function setHash(id) { history.replaceState(null, '', id ? `#doc=${id}` : '#'); }
function hashDoc() { return /^#doc=([0-9a-f]{32})$/.exec(location.hash)?.[1] || null; }
```

with:

```javascript
function setHash(url) { history.replaceState(null, '', url); }
function currentRoute() { return parseRoute(location.hash); }

// history.replaceState (setHash above) never fires `hashchange` -- that
// event is the BROWSER's own notice of a navigation it performed, and a
// script writing history silently is invisible to it (decision 10). Every
// router-driven entry point (Prev, Skip, Jump, Finish's chain-to-next, the
// post-import and post-start redirects) calls go(), not setHash(), so the
// screen actually updates instead of only the address bar. The handful of
// EXISTING setHash() calls elsewhere in this file (showConfirm, the
// extracting overlay, a freshly opened review) already update their own
// screen through a direct function call and stay as plain setHash() --
// re-dispatching for those would refetch a document already on screen.
function go(url) {
  navigate(url, {
    setUrl: (u) => history.replaceState(null, '', u),
    dispatch: () => { routeFromHash(); },
  });
}
```

Every call site of `setHash(null)` (passing nothing, meaning "back to no drawing") becomes `setHash(worklistUrl())`; every call site of `setHash(id)` (passing a document id string) becomes `setHash(docUrl(id))`. Every call site of `hashDoc()` is replaced by reading `currentRoute()` and checking `.name === 'doc'` for its `.id`. (`resumeFromHash`'s body, for instance, becomes:)

```javascript
// Re-entrancy guard lives HERE, not inside go() -- routeFromHash also runs
// from the native `hashchange` listener (a real link click, back/forward),
// which go()'s own dispatch does not see. Guarding only go() would still
// let a real link click race a go()-driven dispatch still in flight; this
// way every path that can start a route dispatch shares the same guard.
let navigating = false;
async function routeFromHash() {
  if (navigating) return;
  navigating = true;
  try {
    const route = currentRoute();
    showScreen(route.name);
    if (route.name === 'doc') {
      resuming = true;
      try {
        const doc = await getDocument(route.id);
        await routeDocument(doc);
      } catch (err) {
        if (err?.status === 404) {
          setHash(worklistUrl());
          toast({ kind: 'warn', title: 'Drawing not found', msg: 'The drawing in this link was removed.' });
          showScreen('worklist');
          // A deletion is invisible to the since-cursor poll (decision 11):
          // the work list's cached copy of this (now-gone) drawing would
          // otherwise sit there until its next scheduled full reload, up to
          // ~1 minute away. A 404 on a drawing it had cached is the clearest
          // possible signal that its cache is stale right now.
          workList?.refresh(true);
        } else {
          toast({ kind: 'error', title: 'Could not reach the drawing',
                  msg: `${String(err?.message || err)} — reload the page to try again.` });
        }
      } finally {
        resuming = false;
      }
    } else if (route.name === 'staging') {
      stagingView.load(route.batchId);
    }
  } finally {
    navigating = false;
  }
}

function showScreen(name) {
  document.getElementById('screen-worklist').hidden = name !== 'worklist';
  document.getElementById('screen-staging').hidden = name !== 'staging';
  document.getElementById('shell-body').hidden = name !== 'doc';
  if (name !== 'doc') setReviewBarVisible(false);
}

// The review bar (batch label, Prev/Skip/Jump) only makes sense once a
// drawing is actually open FOR REVIEW. #/doc/<id> for a drawing that is
// still staged, queued, extracting, failed or cancelled shows the confirm
// panel or the extracting overlay instead (both already live inside
// shell-body, unchanged from B1) -- there is no batch position to report
// for those, so the bar stays hidden until routeDocument says otherwise.
function setReviewBarVisible(visible) {
  const el = document.getElementById('review-bar-nav');
  if (el) el.hidden = !visible;
}
```

Update the existing `routeDocument` function to manage the review bar's visibility per sub-state:

```javascript
async function routeDocument(doc) {
  waitingOn = { id: doc.id, batchId: doc.batch_id, fileName: doc.file_name, pages: doc.pages };
  if (doc.state === 'staged') {
    setReviewBarVisible(false);
    await showStaged(doc);
  } else if (doc.state === 'queued' || doc.state === 'extracting') {
    setReviewBarVisible(false);
    showExtracting(doc.file_name);
    pollUntilReady();
  } else if (['ready', 'in_review', 'finished'].includes(doc.state)) {
    await loadForReview(doc.id);
    setReviewBarVisible(true);
  } else {
    setReviewBarVisible(false);
    toast({ kind: 'error', title: `Drawing ${doc.state}`,
            msg: doc.error_type ? `Extraction failed (${doc.error_type}).` : 'Upload it again to retry.' });
    waitingOn = null;
    hideExtracting();
    backToEmptyOrCurrent();
  }
}
```

Rewrite `backToEmptyOrCurrent` to use the router's URL builders instead of the old `#doc=`/`#` scheme, and to land on the work list (the design's home screen, decision 9) instead of the old single-screen "upload a drawing" empty state:

```javascript
function backToEmptyOrCurrent() {
  setHash(state.sessionId ? docUrl(state.sessionId) : worklistUrl());
  if (state.sessionId) {
    document.getElementById('plan-confirm').hidden = true;
    document.getElementById('plan-extracting').hidden = true;
  } else {
    // backToEmpty()'s "plan-empty" drop zone is B1's single-screen home;
    // the router's home screen is the work list now (decision 9), so this
    // branch shows that instead. backToEmpty() and plan-empty's markup are
    // left in index.html unused rather than deleted -- removing dead B1
    // markup is a cleanup, not part of this fix.
    showScreen('worklist');
  }
}
```

Add the `hashchange` listener and the staging-view instance to `init()`:

```javascript
  stagingView = initStagingView({
    getBatch, setIncluded, startBatch, discardBatch, deleteDocument, toast,
    onStarted: () => go(worklistUrl()),
  });
  window.addEventListener('hashchange', routeFromHash);
  routeFromHash();   // replaces the old resumeFromHash() call at the end of init()
```

(remove the old `resumeFromHash();` call at the end of `init()` and the `resumeFromHash`/`hashDoc` function definitions; `routeFromHash` and `currentRoute` replace them). Declare `let stagingView = null;` alongside the other module-level `let` declarations near the top of the file.

**Also fix Task 10's post-import redirect**, which was written before `go()` existed: in `wireImport`'s `change` handler, replace

```javascript
      setHash(stagingUrl(batchId));
```

with

```javascript
      go(stagingUrl(batchId));
```

(this is the "post-import redirect" named in decision 10 -- before this task wires `routeFromHash`, that line only ever changed the address bar, since nothing in `wireImport` itself shows the staging screen).

- [ ] **Step 3: Wire the review bar's buttons.** Add, called from `init()`:

```javascript
function wireReviewBar() {
  document.getElementById('rb-prev')?.addEventListener('click', () => {
    const id = prevId(worklistDocsCache(), currentBatchId(), state.sessionId);
    if (id) go(docUrl(id));
  });
  document.getElementById('rb-skip')?.addEventListener('click', () => {
    // skip() calls `go` itself (reviewbar.js) -- it takes `navigate` as a
    // dependency rather than returning an id for the caller to setHash()
    // with, which is exactly the shape of mistake decision 10 is about.
    skip(worklistDocsCache(), currentBatchId(), state.sessionId, { navigate: go, docUrl });
  });
  document.getElementById('rb-jump')?.addEventListener('click', openJumpPalette);
}

function currentBatchId() {
  return worklistDocsCache().find((d) => d.id === state.sessionId)?.batch_id || null;
}

// A minimal type-to-filter palette: reuses the browser's own prompt() rather
// than a new modal component, because this is the one piece of B3 the design
// marks "Ctrl+K" without specifying a visual design, and a bespoke combobox
// is not worth building before the operator has used the simple version.
function openJumpPalette() {
  const candidates = jumpCandidates(worklistDocsCache());
  const label = candidates.map((d, i) => `${i + 1}. ${d.file_name}`).join('\n');
  const pick = prompt(`Jump to (type a number):\n${label}`);
  const idx = parseInt(pick, 10) - 1;
  if (candidates[idx]) go(docUrl(candidates[idx].id));
}
```

Call `wireReviewBar()` from `init()`, and add a `Ctrl+K` shortcut in `wireShortcuts` (or directly via a `keydown` listener added in `init()`):

```javascript
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); openJumpPalette(); }
  });
```

`worklistDocsCache()` reads the array `worklist.js`'s `initWorkList` already keeps, via the `getDocs()` accessor and the module-level `workList` variable Task 10 added:

```javascript
function worklistDocsCache() { return workList ? workList.getDocs() : []; }
```

- [ ] **Step 4: Wire Finish to use `nextAfterFinish`.** In the existing `finish()` function, replace the `if (res.status === 'done')` branch's body to also navigate:

```javascript
    } else if (res.status === 'done') {
      toast({ kind: 'ok', title: 'Finished', msg: `Review sealed as revision r${res.revision}` });
      // finisher.run() returns only { status, revision } (finish.js) -- the
      // snapshot taken at the top of finish() is what names which drawing
      // this run was for.
      const next = nextAfterFinish(worklistDocsCache(), currentBatchId(), snapshot.sessionId);
      if (next.status === 'next') go(docUrl(next.id));
      else if (next.status === 'extracting') toast({ kind: 'warn', title: 'Next drawing ready in ≈ N min', msg: 'Still extracting — the work list will show it.' });
      else go(worklistUrl());
```

(the "≈ N min" detail is intentionally not a computed number here: `nextAfterFinish`'s `'extracting'` branch only knows SOMETHING is on its way, not which drawing or its own ETA — the work list's queue header, already visible once the user lands there or glances at it, is where that number lives; this toast's copy says so rather than inventing a second estimate.)

- [ ] **Step 5: Run the full suite.**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (no new automated tests in this task; `go()`'s dispatch mechanism is already covered by `router.js`'s `navigate` tests and `reviewbar.js`'s `skip` tests from Tasks 7 and 11, and the end-to-end wiring itself is exercised by Task 16's manual browser drive)

- [ ] **Step 6: Commit.**

```bash
git add app/static/js/main.js app/static/index.html app/static/js/worklist.js
git commit -m "$(cat <<'EOF'
ui: wire the hash router, review bar and staging view into main.js

The hash scheme moves from #doc=<id> to the router's #/, #/staging/<b>,
#/doc/<id>. go() fixes a real bug: history.replaceState alone never
fires hashchange, so Prev/Skip/Jump/Finish's chain-to-next and the
post-import redirect would otherwise change the address bar and
nothing else -- go() calls routeFromHash itself, re-entrancy guarded.
The review bar is hidden for a #/doc/<id> that is not yet ready/in
review/finished (the confirm or extracting screen shows instead), and
the work list does a full reload whenever a cached drawing 404s.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Two-tab read-only banner

**Data-integrity: no** (the server already refuses the stale tab's writes via `StaleWriter`; this is the visible treatment of that refusal).

**Files:**
- Create: `app/static/js/banner.js`
- Modify: `app/static/js/main.js`, `app/static/styles/components.css`
- Test: `tests/js/banner.test.mjs` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/js/banner.test.mjs`:

```javascript
// Two states this one state machine covers (batch design §3, §5): the losing
// side of a two-tab takeover (stale, unrecoverable -- reload) and a journal
// that keeps failing to flush (lagging, recoverable -- retry countdown).
// Pure; the DOM (showing/hiding the two banner elements) is wired in
// main.js (Task 14/15 together own banner.js's DOM half).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { bannerFor } = await import('../../app/static/js/banner.js');

test('no banner when nothing is wrong', () => {
  assert.equal(bannerFor({ stale: false, lagging: false }), null);
});

test('stale wins over lagging -- once another tab has the writer, retrying is pointless', () => {
  assert.deepEqual(bannerFor({ stale: true, lagging: true, unsavedCount: 2 }),
    { kind: 'stale', unsavedCount: 2 });
});

test('lagging reports a retry countdown in whole seconds, never negative', () => {
  assert.deepEqual(bannerFor({ stale: false, lagging: true, retryAt: 5000, now: 2000 }),
    { kind: 'lagging', secondsLeft: 3 });
  assert.deepEqual(bannerFor({ stale: false, lagging: true, retryAt: 1000, now: 2000 }),
    { kind: 'lagging', secondsLeft: 0 });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `node --test tests/js/banner.test.mjs`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Implement** — create `app/static/js/banner.js`:

```javascript
// One state machine for both banners B3 adds (batch design §3, §5): the
// losing side of a two-tab takeover (`stale` -- unrecoverable, reload is the
// only way out) and a journal that keeps failing to flush (`lagging` --
// recoverable, shown with a retry countdown). Stale wins when both are true:
// once another tab holds the writer, no retry of this tab's flush can ever
// succeed, so there is nothing a countdown would be counting down to.
export function bannerFor({ stale, lagging, unsavedCount = 0, retryAt = 0, now = Date.now() }) {
  if (stale) return { kind: 'stale', unsavedCount };
  if (lagging) return { kind: 'lagging', secondsLeft: Math.max(0, Math.ceil((retryAt - now) / 1000)) };
  return null;
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/js/banner.test.mjs`
Expected: PASS

- [ ] **Step 5: Wire the DOM in `main.js`.** Replace the `scheduleFlush` switch's `'stale'` case:

```javascript
      case 'stale':
        // Said once per drawing, not again on every edit's flush.
        if (!staleShown) {
          staleShown = true;
          toast({ kind: 'warn', title: 'Opened in another tab', msg: 'Reload to continue here.' });
        }
        break;
```

with:

```javascript
      case 'stale':
        showStaleBanner(journal.pending.length);
        break;
```

and add, near the other small DOM helpers:

```javascript
function showStaleBanner(unsavedCount) {
  const el = document.getElementById('banner-stale');
  if (!el) return;
  el.hidden = false;
  const countEl = document.getElementById('banner-stale-count');
  if (countEl) countEl.textContent = unsavedCount ? ` (${unsavedCount} change${unsavedCount === 1 ? '' : 's'} not kept)` : '';
  // Read-only now: further edits would only be refused the same way, so
  // disable the controls that would otherwise look live. Table/viewer
  // already check state.sessionId before anything is mutated; this adds a
  // visible cue the design asks for on top of that existing guard.
  document.getElementById('finish-btn').disabled = true;
}

function hideStaleBanner() {
  const el = document.getElementById('banner-stale');
  if (el) el.hidden = true;
}
```

Call `hideStaleBanner()` at the top of `openForReview` (right after the function's opening, before any await) and in `clearSession`'s caller (`wireHeader`'s `file-close` handler, right after `clearSession()`), so a freshly opened or closed drawing never keeps yesterday's stale banner on screen; `staleShown` (now unused by the switch) can be removed, since `showStaleBanner`'s own `el.hidden` check already makes repeat calls a no-op visually.

Add to `app/static/styles/components.css` (shared by both banners; Task 15 reuses `.banner`/`.banner-warn`):

```css
.banner { position: sticky; top: 0; z-index: 5; padding: 8px 14px; font-size: var(--fs-sm); display: flex; gap: 10px; align-items: center; }
.banner-warn { background: var(--warn); color: var(--bg); }
```

- [ ] **Step 6: Full suite + commit**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (node count now 197)

```bash
git add app/static/js/banner.js app/static/js/main.js app/static/styles/components.css tests/js/banner.test.mjs
git commit -m "$(cat <<'EOF'
ui: replace the stale-tab toast with a persistent read-only banner

The server already refuses the losing tab's writes (StaleWriter); this
makes the refusal visible and sticky instead of a toast that scrolls
away, and disables Finish since any further edit would only be
refused the same way.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Lagging-journal banner with retry countdown

**Data-integrity: no** (the journal's own retry/backoff logic, `journal.js`, is unchanged; this only makes its `'retry'` state visible instead of silent).

**Files:**
- Modify: `app/static/js/main.js`
- Test: covered by `tests/js/banner.test.mjs` (Task 14); no new pure logic, this task is DOM wiring only

- [ ] **Step 1: Wire the `'retry'` case.** Replace:

```javascript
      case 'retry':
        // Keep everything pending and retry, backing off to 10 s; Finish
        // refuses until the server holds every event, so nothing is lost.
        flushBackoff = Math.min(flushBackoff * 2, 10000);
        scheduleFlush(flushBackoff);
        break;
```

with:

```javascript
      case 'retry':
        // Keep everything pending and retry, backing off to 10 s; Finish
        // refuses until the server holds every event, so nothing is lost.
        flushBackoff = Math.min(flushBackoff * 2, 10000);
        showLaggingBanner(Date.now() + flushBackoff);
        scheduleFlush(flushBackoff);
        break;
```

and add, in the same places `hideStaleBanner()` is called (`openForReview`'s opening, and the `default:` branch of the `scheduleFlush` switch, where `ok` being true means the flush finally succeeded):

```javascript
let laggingTimer = null;

function showLaggingBanner(retryAt) {
  const el = document.getElementById('banner-lagging');
  if (!el) return;
  el.hidden = false;
  clearInterval(laggingTimer);
  const tick = () => {
    const el2 = document.getElementById('banner-lagging-countdown');
    if (el2) el2.textContent = String(Math.max(0, Math.ceil((retryAt - Date.now()) / 1000)));
  };
  tick();
  laggingTimer = setInterval(tick, 250);
}

function hideLaggingBanner() {
  clearInterval(laggingTimer);
  const el = document.getElementById('banner-lagging');
  if (el) el.hidden = true;
}
```

Update the `default:` branch:

```javascript
      default:   // idle: the next edit schedules the next flush
        if (ok) { flushBackoff = 500; hideLaggingBanner(); }
        break;
```

Wire the manual retry button, in `wireJournal()` or a new small wiring function called from `init()`:

```javascript
  document.getElementById('banner-lagging-retry')?.addEventListener('click', () => scheduleFlush(0));
```

Call `hideLaggingBanner()` alongside `hideStaleBanner()` at the top of `openForReview` and in the `file-close` handler, so neither banner survives a drawing switch.

- [ ] **Step 2: Full suite + commit.**

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped (no new test count change; `banner.js`'s pure logic was already tested in Task 14, and this task is DOM wiring the design's §5 testing list leaves to the manual browser drive, Task 16)

```bash
git add app/static/js/main.js
git commit -m "$(cat <<'EOF'
ui: show a lagging-journal banner with a live retry countdown

The 'retry' branch of the flush loop (journal.js's backoff) was
silent before except for whatever toast happened to be on screen;
this gives it a persistent banner with a countdown and a manual
retry button, matching the design's §5 "log is lagging" banner.

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

> **Milestone: staging-review-bar-banners** - state must be checkpointed here before continuing.

---

## Milestone 4: docs-and-e2e

### Task 16: CLAUDE.md + throwaway browser drive

**Data-integrity: no.**

**Files:**
- Modify: `CLAUDE.md`
- Scratchpad only (not committed): a Playwright drive script

- [ ] **Step 1: Add a paragraph to CLAUDE.md's §2**, after the "DROPPED VALUES ARE NOT LOST ANY MORE" / "REVIEW CAPTURE" paragraphs and before "`read-lora-v1` IS NOW HARMFUL...":

```markdown
**BATCH B3 SHIPPED: THE ROUTER, WORK LIST, STAGING VIEW AND REVIEW BAR**
(2026-10-08, `docs/plans/2026-10-08-batch-b3-ui-plan.md`). The hash scheme
moved from B1's `#doc=<id>` to the design's `#/` (work list), `#/staging/<batch>`
(staging), `#/doc/<id>` (review) — a bookmarked `#doc=` link from before this
ships is NOT rewritten and will fall back to the work list (`router.js`'s
`parseRoute` treats an unrecognised route that way on purpose). The work list
polls `GET /api/documents?since=<cursor>` every 3 s while the tab is visible,
with filters (batch, state, name search, "needs me" = ready + in_review) and
bulk move-to-front/cancel/retry/remove/export-selected (the last three are
plain per-id loops over existing guarded routes, not new server surface).
Flagged/open/unchecked are now real, persisted `document` columns
(migration 4) computed by `ReviewStore.progress()` — mirrors `state.js`'s
`progress()` exactly — and kept current from both places a review changes
(`/events`, `/seal`), with a lazy backfill in `GET /api/documents` for any
row from before this migration (NULL, never a lying zero). Re-extract is a
NEW transition (`ready` → `queued`, discarding the old proposal), refused
the moment `ReviewStore.has_events()` is true — it is not Retry, which only
ever leaves `failed`/`cancelled`. The review bar's Prev/Skip/Jump and
Finish's "next drawing" all read the SAME list the work list polls, filtered
by `reviewbar.js`'s pure `navOrder`/`nextAfterFinish` — ordering is
`list_documents`'s `ORDER BY rowid`, the true staging order, which survives
a drawing being queued (only the since-cursor filter compares `updated_at`;
sorting the LIST by it was considered and rejected as a bug, not an
imprecision — decision 8). **A hash written by `history.replaceState` does
not fire `hashchange`**, so Prev/Skip/Jump/Finish's chain-to-next and the
post-import redirect all go through `router.js`'s `navigate(url, {setUrl,
dispatch})` seam via `main.js`'s `go()`, which calls `routeFromHash` itself
instead of waiting for an event that never comes (decision 10); every other
`setHash()` call, which already updates its own screen through a direct
function call, is unchanged. The work list does a full reload every 20th
poll and after any 404 the review screen hits on a drawing it had cached,
since a deletion is invisible to the since-cursor diff (decision 11). The
two-tab banner and the lagging-journal banner are one `banner.js` state
machine; stale wins over lagging when both are true, since no retry can
succeed once another tab holds the writer. **Mass export, retention and the
access token are still B4** — "export selected" in the work list is a
client-side loop, not the ZIP+manifest mass export the design describes.
```

- [ ] **Step 2: Run the full suite one more time** to confirm nothing regressed from the doc-only change.

Run: `python -m pytest -q`
Expected: 1830 passed, 2 skipped

- [ ] **Step 3: Commit.**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: record batch B3 (router, work list, staging view, review bar)

Co-Authored-By: <model name> <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Throwaway real-browser drive** (scratchpad only, never committed — same pattern as B1 Task 11 Step 4's drive, `docs/plans/2026-10-07-batch-b1-core-plan.state.md`). Write a Node script to the scratchpad directory (never pass a path containing `.pdf` on a command line — write the script to a file and run it by path) that, against a `uvicorn` instance with `SINDRI_DATA_DIR`/`SINDRI_REVIEW_DIR` pointed at a scratch temp dir and a stubbed OCR backend (monkeypatched the way `tests/conftest.py`'s `StubVLMBackend` is, via a tiny standalone FastAPI app import or an environment hook — whichever is less code; the goal is a few seconds per drawing, not a real VLM) and `playwright-core` + a cached Chromium (as B1's drive used), drives:

  1. Import a ZIP of two small sample PDFs via the staging view → both rows show Include checked → Start batch.
  2. Work list shows both `queued`/`extracting`; wait for both `ready`.
  3. Open the first from the work list, edit a cell, accept a flagged row, reload the page mid-review.
  4. Confirm "Review restored" and the edit/accept are both present (resume parity).
  5. Finish → confirm it jumps to the second drawing in the same batch (review bar / `nextAfterFinish`).
  6. Click "Skip for now" then "‹ Prev" → back on the first (now `finished`) drawing via Jump (Ctrl+K palette).
  7. Open the second drawing in a SECOND browser context with the same link; in the first tab make an edit and confirm the stale banner appears with an unsaved-change count.

  Run it, fix anything it surfaces (as a new task inserted before this one, with its own tests — never patch around a real bug found here without a regression test), and report the pass/fail list in the task's commit-adjacent notes (no commit for the script itself; it is throwaway, per the rule carried from B1).

---

## After B3

B4 (Export & ops) builds on this UI directly: "Export selected" in the work
list becomes a real mass-export call (`POST /api/exports`) once that route
exists, replacing the per-id loop Task 10 ships; the staging view's "Discard"
on a zip-bomb draft (CARRY FROM B2) gets a dedicated action once B4's
retention sweep exists to reason about partially-written files; the access
token prompt gates every screen this plan adds, not just the API calls; and
retention's daily sweep purges `finished`+exported drawings that the work
list currently shows forever. HITL phase 2 (forcing UX, audit sampling) lands
on the navigation model this plan builds — the review bar's batch-scoped
Prev/Skip/Jump is the surface it extends, not something it replaces.
