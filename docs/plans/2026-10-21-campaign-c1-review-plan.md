# Client review campaign C1 — everything the client needs to review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow context-management:milestone-checkpoints: checkpoint `docs/plans/2026-10-21-campaign-c1-review-plan.state.md` at each marker.

**Goal:** Let the client review ~50 of our ballooned gold drawings in the hosted Sindri app on the operator's Hetzner VPS, with every value edit, delete, add, balloon move, anchor move, reason and note logged server-side, and an Approve per drawing.

**Architecture:** A campaign is a batch with a `campaign` name (DB migration 6). The operator builds a bundle per drawing (input, page image, `result.json` re-derived under today's policy, `campaign.json`) from stored prediction dumps. A CLI inside the container loads it through the worker's existing "result.json already exists, so skip extraction" path. The journal gains `move_target` (anchor box), `set_note` (per-drawing note) and closed-vocabulary reasons on `edit_cell`/`delete_row`; replay and the JS↔Python parity fuzz cover them. In campaign mode the UI's Finish becomes **Approve** (seal, no export), and a `SINDRI_CAMPAIGN_ONLY` deployment flag closes every route a reviewer does not need.

**Tech stack:** FastAPI + SQLite (`app/batch`), the review store (`app/review`), vanilla ES modules (`app/static/js`), node `--test` for UI rules, pytest, docker compose, Caddy.

**Design:** `docs/plans/2026-10-21-client-review-campaign-design.md` (approved 2026-10-21). This plan covers design §1-§4 and the UI half of §3. **Grading and the report (§5-§6) are plan C2**, written while the client reviews: C2 needs no client-facing code, and the real record shape from C1 should exist before it is planned.

---

## Decisions this plan takes (beyond the design)

| # | decision | why |
|---|---|---|
| 1 | Campaign = `batch.campaign TEXT` (NULL = normal batch), migration 6 | a campaign is a batch the client walks with the existing review bar; one column, no new table |
| 2 | Loading reuses the worker: the bundle puts `result.json` in the drawing's dir, the row is queued, and `Worker._run` skips extraction because the file exists | one code path builds every review record (header, proposal, progress, `ready`); no second implementation to drift |
| 3 | `campaign.json` in the drawing dir is merged into `header.json` under `"campaign"` by the worker | the header is where grading reads session facts (group, source run, doc hash) |
| 4 | **Deviation from design §0 "proposal frozen … unmodified dump":** the bundle's rows are `reapply_current_code(dump)` (today's flags/drops/tray), then `export_share.refresh_rows` (plain-rectangle drop, renumber, whitespace balloon placement) | many campaign drawings come from `r3-trainpredict`, predicted under older policy; the client must grade what we ship TODAY, and bubble placement can only be judged if it is today's (`fa1cb6f`). The proposal is still frozen once loaded. `campaign.json` records `source_run`, so C2 can split on it |
| 5 | Selection may use **train + dev**, never test; `share_select`'s manifest gains `roles` | 50 drawings exceed dev's 15 scoped documents; test stays held out (design §0). CLAUDE.md §1's "never train" applies to client share-packs, not to this campaign |
| 6 | Reason vocabularies are exactly the phase-2 plan's (`2026-10-09-hitl-phase2-forcing-plan.md` Task 1), and live in `app/static/js/reasons.js` | one vocabulary for both features. **Phase 2 Task 1 and Task 15 must then skip the vocabulary parts and import `reasons.js`**; recorded in the state file |
| 7 | A reason is applied by **applying the op once the popover resolves or is dismissed** (the phase-2 plan's mechanism) | "no reason" and "a reason" are one code path, and the reason rides on the edit/delete event itself |
| 8 | Anchor = `move_target{id, box}`; `replay.mismatches` now compares `target_region` too | otherwise a JS/Python divergence on the anchor would seal silently |
| 9 | Note = `set_note{text}` (doc level; latest net wins), max 2000 chars; `replay.note_of(events)` | the design's free-text policy note, journaled like every other reviewer act |
| 10 | `SINDRI_CAMPAIGN_ONLY=1` allows only GETs (except export downloads) plus `/open`, `/events`, `/seal`, `/read_region`, `/api/auth`; everything else is 403. The UI hides import, the bulk bar and exports | the public instance cannot be used to upload or export anything, whatever the UI shows |
| 11 | Per-batch guards remain too: export (single + mass), re-extract and delete refuse a campaign drawing | the operator's own non-campaign-only instance must not export or discard a campaign drawing either |

## File map

| file | change |
|---|---|
| `app/batch/db.py` | migration 6: `batch.campaign` |
| `app/batch/repo.py` | `create_campaign_document`, `campaign_document_by_sha`, `campaign_of`; `list_documents` selects `b.campaign` |
| `app/batch/worker.py` | merge `campaign.json` into the header |
| `app/batch/exports.py` | skip a campaign drawing (`skip_reason: campaign`) |
| `app/main.py` | `_doc_view` + guards on export / re-extract / delete; campaign-only middleware; `/api/health` reports `campaign_only` |
| `app/campaign/__init__.py` (new) | package docstring: operator tooling + campaign-only policy |
| `app/campaign/policy.py` (new) | `campaign_only()`, `allowed(method, path)` |
| `app/campaign/load.py` (new) | in-container loader CLI |
| `app/campaign/pull.py` (new) | in-container records packer CLI |
| `campaign_build.py` (new, repo root, operator-only) | bundle builder from dumps |
| `app/eval/share_select.py` | manifest gains `roles` |
| `app/review/journal.py` | `move_target`, `set_note`, `EDIT_REASONS`, `DELETE_REASONS`, `NOTE_MAX` |
| `app/review/replay.py` | `move_target` branch, `set_note` no-op, `note_of`, `target_region` in `mismatches` |
| `app/static/js/state.js` | `opMoveTarget`, `opSetNote`, reasons on `opEditCell`/`opDeleteRow`, `state.note`, `opFromEvent` |
| `app/static/js/reasons.js` (new) | vocabularies + labels + `commitWithReason` |
| `app/static/js/anchor.js` (new) | pure box geometry for the handles |
| `app/static/js/campaign.js` (new) | `approvedCount`, `finishCopy` |
| `app/static/js/viewer.js` | anchor box + handles; delete through the reason prompt |
| `app/static/js/table.js` | edit through the reason prompt |
| `app/static/js/main.js` | campaign-mode Finish → Approve, approved counter, note box, campaign-only chrome |
| `app/static/index.html`, `app/static/styles/components.css` | note box, popover, anchor styles, `data-campaign-hide` |
| `tests/js/fuzz_journal.mjs`, `tests/review/test_journal_replay_parity.py` | new ops in the fuzz |
| `docker-compose.vps.yml` (new), `deploy/Caddyfile.example` (new), `docs/deploy/vps.md` (new) | VPS deployment |

Run commands from the repo root. Python tests: `python -m pytest <path> -q`. Node tests: `node --test <file>`; the whole JS suite also runs inside pytest via `tests/test_js_state.py`. Commit trailer per CLAUDE.md §4. **No task reads client data**: every fixture is synthetic, `campaign_build.py` is only ever run by the operator, and nothing here adds anything to the guard's allowlist.

---

## Milestone A — campaign mode on the server

### Task 1: migration 6 — `batch.campaign`

**Files:**
- Modify: `app/batch/db.py` (append to `MIGRATIONS`)
- Test: `tests/batch/test_db.py`

- [ ] **Step 1: Write the failing test** — append to `tests/batch/test_db.py`:

```python
def test_migration_6_adds_a_nullable_campaign_to_batches(tmp_path):
    """A campaign is a batch the client reviews (campaign design §2). NULL
    is every ordinary batch, including all from before the column existed --
    never an empty string that would read as 'a campaign with no name'."""
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("INSERT INTO batch (id, name, draft, created_at) VALUES ('b', 'n', 0, 't')")
    assert conn.execute("SELECT campaign FROM batch WHERE id = 'b'").fetchone()[0] is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) >= 6
```

(If `tests/batch/test_db.py` does not import `db` yet, add `from app.batch import db` at the top.)

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/batch/test_db.py::test_migration_6_adds_a_nullable_campaign_to_batches -q`
Expected: FAIL, `no such column: campaign`

- [ ] **Step 3: Implement** — append inside `MIGRATIONS` in `app/batch/db.py`, after migration 5:

```python
    # 6: review campaigns (docs/plans/2026-10-21-client-review-campaign-design.md
    # §2). A campaign batch holds drawings loaded from stored predictions for
    # the client to grade; NULL is an ordinary batch -- every batch from
    # before this column, and every import.
    """
    ALTER TABLE batch ADD COLUMN campaign TEXT;
    CREATE INDEX batch_campaign ON batch(campaign);
    """,
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/batch/test_db.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/batch/db.py tests/batch/test_db.py
git commit -m "batch: migration 6 adds batch.campaign

A review campaign is a batch the client grades; NULL keeps every existing
and imported batch ordinary.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: repo — create, find and name campaign documents

**Files:**
- Modify: `app/batch/repo.py` (after `create_document`; `list_documents`' SELECT)
- Test: `tests/batch/test_repo.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/batch/test_repo.py`:

```python
def test_campaign_documents_share_one_non_draft_batch_per_campaign(repo):
    a, b = ids(2)
    da = repo.create_campaign_document(a, "pilot-1", "x.pdf", pages=1, sha256="1" * 64)
    db_ = repo.create_campaign_document(b, "pilot-1", "y.pdf", pages=1, sha256="2" * 64)
    assert da["batch_id"] == db_["batch_id"]
    batch = repo.get_batch(da["batch_id"])
    assert (batch["campaign"], batch["name"], batch["draft"]) == ("pilot-1", "pilot-1", 0)
    assert da["state"] == "staged" and da["included"] == 1
    repo.enqueue(a)   # a campaign batch is not a draft, so its drawings queue one by one
    assert repo.get(a)["state"] == "queued"


def test_campaign_document_by_sha_finds_only_that_campaigns_copy(repo):
    a, b = ids(2)
    repo.create_campaign_document(a, "pilot-1", "x.pdf", pages=1, sha256="1" * 64)
    assert repo.campaign_document_by_sha("pilot-1", "1" * 64) == a
    assert repo.campaign_document_by_sha("pilot-2", "1" * 64) is None
    staged(repo, b)   # an ordinary batch holding the same bytes is not a campaign copy
    assert repo.campaign_document_by_sha("pilot-1", "0" * 64) is None


def test_campaign_of_and_the_work_list_name_the_campaign(repo):
    a, b = ids(2)
    repo.create_campaign_document(a, "pilot-1", "x.pdf", pages=1, sha256="1" * 64)
    staged(repo, b)
    assert repo.campaign_of(a) == "pilot-1" and repo.campaign_of(b) is None
    by_id = {d["id"]: d["campaign"] for d in repo.list_documents()}
    assert by_id == {a: "pilot-1", b: None}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/batch/test_repo.py -q -k campaign`
Expected: FAIL, `AttributeError: 'Repo' object has no attribute 'create_campaign_document'`

- [ ] **Step 3: Implement** — in `app/batch/repo.py`, add after `create_document`:

```python
    def create_campaign_document(self, doc_id, campaign: str, file_name, pages, sha256) -> dict:
        """A drawing of a review campaign (campaign design §2), staged in that
        campaign's ONE batch, created on first use. The batch is not a draft:
        the loader queues each drawing itself with enqueue(), and the worker
        then finds result.json already in place and skips extraction."""
        now = self._clock()
        with self._tx() as c:
            row = c.execute("SELECT id FROM batch WHERE campaign = ?", (campaign,)).fetchone()
            if row is None:
                batch_id = uuid.uuid4().hex
                c.execute("INSERT INTO batch (id, name, draft, created_at, updated_at, campaign)"
                          " VALUES (?, ?, 0, ?, ?, ?)", (batch_id, campaign, now, now, campaign))
            else:
                batch_id = row["id"]
                self._touch(c, batch_id)
            c.execute(
                "INSERT INTO document (id, batch_id, file_name, sha256, pages, state, included,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'staged', 1, ?, ?)",
                (doc_id, batch_id, file_name, sha256, pages, now, now))
            return self._row(c, doc_id)

    def campaign_document_by_sha(self, campaign: str, sha256: str) -> Optional[str]:
        """The id of this campaign's drawing with these bytes, or None: what
        makes loading a bundle twice a no-op (campaign design §2)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT d.id FROM document d JOIN batch b ON b.id = d.batch_id"
                " WHERE b.campaign = ? AND d.sha256 = ? ORDER BY d.rowid LIMIT 1",
                (campaign, sha256)).fetchone()
        return row["id"] if row else None

    def campaign_of(self, doc_id) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT b.campaign FROM document d JOIN batch b ON b.id = d.batch_id"
                " WHERE d.id = ?", (doc_id,)).fetchone()
        if row is None:
            raise UnknownDocument(f"no document {str(doc_id)[:8]}")
        return row["campaign"]
```

In `list_documents`, change the SELECT to:

```python
                f"SELECT d.*, b.name AS batch_name, b.campaign AS campaign FROM document d"
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/batch -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/batch/repo.py tests/batch/test_repo.py
git commit -m "batch: create, find and name campaign documents

One non-draft batch per campaign; lookup by sha makes a bundle load
idempotent, and the work list carries the campaign so the UI can switch
Finish to Approve.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: worker — campaign facts in the header

**Files:**
- Modify: `app/batch/worker.py:186` (the `self.store.create(...)` call in `_run`)
- Test: `tests/batch/test_worker.py`

- [ ] **Step 1: Write the failing test** — append to `tests/batch/test_worker.py`:

```python
def test_a_campaign_drawing_skips_extraction_and_records_its_campaign(h):
    """Campaign design §2: the loader leaves result.json and campaign.json in
    the drawing's dir. The worker must not spend the GPU (the file is
    already there), and the header must say which campaign, group and
    source run the proposal came from -- grading reads them there."""
    d = h.data.doc_dir(A)
    d.mkdir(parents=True)
    (d / "input.pdf").write_bytes(b"%PDF-1.4")
    rows = [Characteristic(pos=1, id="r1", nominal="20").model_dump(mode="json")]
    (d / "result.json").write_text(json.dumps(
        {"rows": rows, "notes": None, "marks": None, "title_block": []}))
    facts = {"campaign": "pilot-1", "group": "great", "source_run": "r5-control",
             "doc_hash": "8e5fb4d8"}
    (d / "campaign.json").write_text(json.dumps(facts))
    h.repo.create_campaign_document(A, "pilot-1", "a.pdf", 1, "1" * 64)
    h.repo.enqueue(A)
    h.worker.run_once()
    assert h.extract_calls == 0
    assert h.repo.get(A)["state"] == "ready"
    header = json.loads((h.store.root / A / "header.json").read_text())
    assert header["campaign"] == facts and header["h"] == 1


def test_an_ordinary_drawing_has_no_campaign_key(h):
    h.queue()
    h.worker.run_once()
    header = json.loads((h.store.root / A / "header.json").read_text())
    assert "campaign" not in header
```

- [ ] **Step 2: Run them and watch the first fail**

Run: `python -m pytest tests/batch/test_worker.py -q -k campaign`
Expected: FAIL, `KeyError: 'campaign'`

- [ ] **Step 3: Implement** — in `Worker._run`, replace

```python
            self.store.create(doc_id, self.header_fn(d / "input.pdf"), {"rows": rows})
```

with

```python
            header = self.header_fn(d / "input.pdf")
            # A campaign drawing (campaign design §2) carries its facts --
            # campaign, group, source run, doc hash -- next to its input;
            # grading reads them from the header. Inside this try: a broken
            # campaign.json fails the drawing like any review-record fault.
            campaign_path = d / "campaign.json"
            if campaign_path.is_file():
                header = {**header,
                          "campaign": json.loads(campaign_path.read_text(encoding="utf-8"))}
            self.store.create(doc_id, header, {"rows": rows})
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/batch/test_worker.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/batch/worker.py tests/batch/test_worker.py
git commit -m "worker: record a campaign drawing's facts in its header

The loader leaves campaign.json beside result.json; the worker skips
extraction as for any retry and folds the facts into header.json, where
grading reads group and source run.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: API — campaign in the doc view, and drawings that are never exported, re-extracted or removed

**Files:**
- Modify: `app/main.py` (`_doc_view`, `reextract_document`, `delete_document`, `export_document`), `app/batch/exports.py` (`_export_one`)
- Test: `tests/test_api_campaign.py` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/test_api_campaign.py`:

```python
"""Campaign drawings through the API (campaign design §2-§3; plan C1
decision 11): the client grades them; nobody exports, re-extracts or
removes them, on any instance."""
import json

from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.models import Characteristic

client = TestClient(app)
DOC = "c" * 32


def load_campaign_doc(sample_pdf, doc_id=DOC, campaign="pilot-1"):
    """What app.campaign.load does, minus the bundle: files in place, row
    created and queued, worker run once."""
    rt = main.runtime()
    d = rt.data.doc_dir(doc_id)
    d.mkdir(parents=True)
    (d / "input.pdf").write_bytes(sample_pdf.read_bytes())
    rows = [Characteristic(pos=1, id="r1", nominal="20", balloon_xy=(10, 10),
                           target_region=(1, 1, 5, 5)).model_dump(mode="json")]
    (d / "result.json").write_text(json.dumps(
        {"rows": rows, "notes": None, "marks": None, "title_block": []}))
    (d / "campaign.json").write_text(json.dumps({"campaign": campaign, "group": "great"}))
    (d / "page.png").write_bytes(b"")
    rt.repo.create_campaign_document(doc_id, campaign, "x.pdf", 1, "1" * 64)
    rt.repo.enqueue(doc_id)
    rt.worker.run_once()
    return doc_id


def approve(doc_id):
    opened = client.post(f"/api/documents/{doc_id}/open").json()
    res = client.post(f"/api/session/{doc_id}/seal", json={
        "writer": opened["writer"], "final_seq": 0, "rows": opened["rows"],
        "reviewed_ids": []})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_doc_view_names_the_campaign(sample_pdf):
    load_campaign_doc(sample_pdf)
    assert client.get(f"/api/documents/{DOC}").json()["campaign"] == "pilot-1"


def test_a_campaign_drawing_can_be_approved_but_not_exported(sample_pdf):
    load_campaign_doc(sample_pdf)
    assert approve(DOC)["revision"] == 1
    res = client.get(f"/api/documents/{DOC}/export/xlsx")
    assert res.status_code == 409 and "campaign" in res.json()["detail"]


def test_a_campaign_drawing_is_never_re_extracted_or_removed(sample_pdf):
    load_campaign_doc(sample_pdf)
    assert client.post(f"/api/documents/{DOC}/reextract").status_code == 409
    assert client.delete(f"/api/documents/{DOC}").status_code == 409
    assert main.runtime().repo.get(DOC)["state"] == "ready"


def test_mass_export_skips_a_campaign_drawing(sample_pdf):
    load_campaign_doc(sample_pdf)
    approve(DOC)
    row = main.runtime().exports._export_one(None, DOC, set())
    assert row["skip_reason"] == "campaign"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_api_campaign.py -q`
Expected: FAIL (`KeyError: 'campaign'` in the first, 200s where 409s are expected in the others). If `approve()` itself fails because the store refuses `final_seq: 0` on an empty journal, copy the seal shape from `tests/test_api_review.py::test_events_round_trip_and_seal`. That would be a fixture problem, not campaign behaviour.

- [ ] **Step 3: Implement** — in `app/main.py`:

Add below `_doc_view`'s definition (and add `"campaign": rt.repo.campaign_of(doc_id),` to the dict `_doc_view` returns):

```python
def _refuse_campaign(rt, doc_id: str, action: str) -> None:
    """Campaign drawings are the client's to grade, never ours to change or
    ship (plan C1 decision 11): refused on every instance, campaign-only or
    not, so the operator's own instance cannot export or discard one either."""
    if rt.repo.campaign_of(doc_id) is not None:
        raise InvalidTransition(f"campaign drawings cannot be {action}")
```

Call it:
- in `reextract_document`, right after `rt = runtime()`: `_refuse_campaign(rt, doc_id, "re-extracted")`
- in `delete_document`, right after `rt = runtime()`: `if rt.repo.has_document(doc_id): _refuse_campaign(rt, doc_id, "removed")`. The guard is needed because DELETE is idempotent: a second delete of a gone row must still answer 200, and `campaign_of` raises on a missing row.
- in `export_document`, right after `doc = rt.repo.get(doc_id)`: `_refuse_campaign(rt, doc_id, "exported")`

In `app/batch/exports.py` `_export_one`, directly after `batch = self.repo.get_batch(doc["batch_id"])`:

```python
        if batch.get("campaign"):
            # Campaign drawings are graded, never shipped (plan C1 decision 11).
            return {**base, "batch": batch["name"], "state": doc["state"],
                    "skip_reason": "campaign"}
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/test_api_campaign.py tests/test_api.py tests/test_api_review.py tests/test_api_worklist.py tests/batch -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/batch/exports.py tests/test_api_campaign.py
git commit -m "api: campaign drawings are approved, never exported or discarded

The doc view names the campaign; export (single and mass), re-extract and
remove refuse a campaign drawing on every instance.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: campaign-only deployment — a closed route allowlist

**Files:**
- Create: `app/campaign/__init__.py`, `app/campaign/policy.py`
- Modify: `app/main.py` (a middleware after `_token_gate`; `health()`)
- Test: `tests/campaign/__init__.py` (empty), `tests/campaign/test_policy.py`, `tests/test_api_campaign.py`

- [ ] **Step 1: Write the failing tests** — create `tests/campaign/__init__.py` (empty) and `tests/campaign/test_policy.py`:

```python
"""SINDRI_CAMPAIGN_ONLY (plan C1 decision 10): the public instance serves the
review and nothing else. An allowlist, not a denylist: a route added later is
closed until someone decides a reviewer needs it."""
import pytest

from app.campaign.policy import allowed

D = "c" * 32


@pytest.mark.parametrize("method,path", [
    ("GET", "/"), ("GET", "/js/main.js"), ("GET", "/api/health"),
    ("GET", "/api/documents"), ("GET", f"/api/documents/{D}"), ("GET", f"/api/image/{D}"),
    ("GET", "/api/batches"),
    ("POST", "/api/auth"), ("POST", f"/api/documents/{D}/open"),
    ("POST", f"/api/session/{D}/events"), ("POST", f"/api/session/{D}/seal"),
    ("POST", "/api/read_region"),
])
def test_what_a_reviewer_needs_is_allowed(method, path):
    assert allowed(method, path)


@pytest.mark.parametrize("method,path", [
    ("GET", f"/api/documents/{D}/export/xlsx"), ("GET", "/api/exports/x/download"),
    ("POST", "/api/batches"), ("POST", "/api/uploads"), ("PUT", "/api/uploads/x"),
    ("POST", "/api/exports"), ("POST", "/api/queue/pause"),
    ("POST", f"/api/documents/{D}/reextract"), ("POST", f"/api/documents/{D}/retry"),
    ("DELETE", f"/api/documents/{D}"), ("DELETE", "/api/batches/x"),
    ("POST", f"/api/session/{D}/events/extra"), ("POST", "/api/some_future_route"),
])
def test_everything_else_is_closed(method, path):
    assert not allowed(method, path)
```

Append to `tests/test_api_campaign.py`:

```python
def test_campaign_only_closes_uploads_and_reports_itself(monkeypatch):
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    assert client.post("/api/batches", json={}).status_code == 403
    assert client.get("/api/health").json()["campaign_only"] is True


def test_campaign_only_still_serves_the_review(sample_pdf, monkeypatch):
    load_campaign_doc(sample_pdf)
    monkeypatch.setenv("SINDRI_CAMPAIGN_ONLY", "1")
    assert approve(DOC)["revision"] == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/campaign/test_policy.py tests/test_api_campaign.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.campaign'`

- [ ] **Step 3: Implement** — create `app/campaign/__init__.py`:

```python
"""Review campaigns (docs/plans/2026-10-21-client-review-campaign-design.md):
the client grades drawings we already ballooned. Product code -- like
app/batch, it never imports app.eval -- except campaign_build.py at the repo
root, which is the operator's and reads dumps."""
```

Create `app/campaign/policy.py`:

```python
"""SINDRI_CAMPAIGN_ONLY (plan C1 decision 10). Read per request, like the
access token (app/auth.py), so tests and a real deployment share one truth:
the environment the process actually runs with."""
import os
import re

_HEX = r"[0-9a-f]{32}"
# Exactly what a reviewer's browser calls. Exports are GETs, so GET is not
# blanket-allowed under /api/: the two download shapes are refused first.
_GET_DENY = (re.compile(rf"\A/api/documents/{_HEX}/export/[a-z]+\Z"),
             re.compile(r"\A/api/exports(/.*)?\Z"))
_POST_ALLOW = (re.compile(r"\A/api/auth\Z"),
               re.compile(rf"\A/api/documents/{_HEX}/open\Z"),
               re.compile(rf"\A/api/session/{_HEX}/(events|seal)\Z"),
               re.compile(r"\A/api/read_region\Z"))


def campaign_only() -> bool:
    return os.environ.get("SINDRI_CAMPAIGN_ONLY", "").lower() in ("1", "true", "yes")


def allowed(method: str, path: str) -> bool:
    if not path.startswith("/api/"):
        return method in ("GET", "HEAD")      # the static UI
    if method in ("GET", "HEAD"):
        return not any(p.match(path) for p in _GET_DENY)
    if method == "POST":
        return any(p.match(path) for p in _POST_ALLOW)
    return False
```

In `app/main.py`, import `from app.campaign import policy as campaign_policy` and add below `_token_gate`:

```python
@app.middleware("http")
async def _campaign_only_gate(request: Request, call_next):
    """The public campaign instance serves the review and nothing else
    (plan C1 decision 10) -- no upload, export or queue control, whatever
    the UI shows."""
    if campaign_policy.campaign_only() and not campaign_policy.allowed(
            request.method, request.url.path):
        return JSONResponse({"detail": "not available on this instance"}, status_code=403)
    return await call_next(request)
```

In `health()`, before `return status`: `status["campaign_only"] = campaign_policy.campaign_only()`

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/campaign tests/test_api_campaign.py tests/test_auth.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/campaign/__init__.py app/campaign/policy.py app/main.py tests/campaign tests/test_api_campaign.py
git commit -m "api: SINDRI_CAMPAIGN_ONLY closes every route a reviewer does not need

An allowlist: reads, open/events/seal, read_region and auth. Uploads,
exports, queue control and any future route answer 403 on the public
instance.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

> **Milestone: campaign mode on the server** — state must be checkpointed here before continuing.

---

## Milestone B — the journal learns anchors, notes and reasons

### Task 6: journal vocabulary — `move_target`, `set_note`, reasons

**Files:**
- Modify: `app/review/journal.py`
- Test: `tests/review/test_journal.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/review/test_journal.py`:

```python
from app.review.journal import DELETE_REASONS, EDIT_REASONS, NOTE_MAX


def test_move_target_needs_an_id_and_an_ordered_four_number_box():
    validate_event({"seq": 1, "type": "move_target", "id": "r1", "box": [1, 2, 30, 40]})
    for box in ([1, 2, 3], [5, 2, 3, 40], [1, 40, 30, 2], [1, 2, 30, True], "x"):
        with pytest.raises(JournalError):
            validate_event({"seq": 1, "type": "move_target", "id": "r1", "box": box})


def test_set_note_takes_a_bounded_string():
    validate_event({"seq": 1, "type": "set_note", "text": ""})
    validate_event({"seq": 1, "type": "set_note", "text": "x" * NOTE_MAX})
    for text in (None, 3, "x" * (NOTE_MAX + 1)):
        with pytest.raises(JournalError):
            validate_event({"seq": 1, "type": "set_note", "text": text})


def test_reasons_are_closed_vocabularies_and_optional():
    edit = {"seq": 1, "type": "edit_cell", "id": "r", "field": "nominal", "old": "1", "new": "2"}
    validate_event(edit)
    validate_event({**edit, "reason": "misread_digit"})
    with pytest.raises(JournalError):
        validate_event({**edit, "reason": "because"})
    delete = {"seq": 1, "type": "delete_row", "id": "r"}
    validate_event({**delete, "reason": "unballooned_real_dimension"})
    with pytest.raises(JournalError):
        validate_event({**delete, "reason": "misread_digit"})
    # identical to the phase-2 plan's lists (plan C1 decision 6)
    assert EDIT_REASONS == {"misread_digit", "wrong_tolerance", "wrong_char_type",
                            "wrong_sign_or_unit", "other"}
    assert DELETE_REASONS == {"duplicate", "not_a_characteristic",
                              "unballooned_real_dimension", "wrong_region", "other"}
```

(The file already imports `pytest`, `validate_event` and `JournalError`; add whichever is missing.)

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/review/test_journal.py -q`
Expected: FAIL, `ImportError: cannot import name 'DELETE_REASONS'`

- [ ] **Step 3: Implement** — in `app/review/journal.py`:

Extend `EVENT_TYPES`:

```python
EVENT_TYPES = frozenset({
    "add_row", "delete_row", "move_row", "edit_cell",
    "accept", "unaccept", "confirm_suggestions", "retract",
    "move_target", "set_note",
})
```

Add after `VALUE_FIELDS`:

```python
# Closed vocabularies for the reason chips (HITL design §1). The wire keys
# are the phase-2 plan's own (2026-10-09-hitl-phase2-forcing-plan.md Task 1),
# shared with the client review campaign; app/static/js/reasons.js mirrors
# them and a node test pins the mirror.
EDIT_REASONS = frozenset({
    "misread_digit", "wrong_tolerance", "wrong_char_type",
    "wrong_sign_or_unit", "other",
})
DELETE_REASONS = frozenset({
    "duplicate", "not_a_characteristic", "unballooned_real_dimension",
    "wrong_region", "other",
})
# A per-drawing note is a sentence or two of policy ("we never balloon
# chamfers"), not a document; the bound keeps one event from bloating a
# journal that is replayed on every seal.
NOTE_MAX = 2000
```

In `validate_event`, inside the `edit_cell` branch after the old/new check:

```python
        reason = e.get("reason")
        if reason is not None and reason not in EDIT_REASONS:
            raise JournalError(f"edit_cell reason must be one of {sorted(EDIT_REASONS)} or absent")
```

Inside the `delete_row` branch after the id check:

```python
        reason = e.get("reason")
        if reason is not None and reason not in DELETE_REASONS:
            raise JournalError(f"delete_row reason must be one of {sorted(DELETE_REASONS)} or absent")
```

And two new branches after the `accept`/`unaccept`/`confirm_suggestions` branch:

```python
    elif type_ == "move_target":
        if not _is_str(e.get("id")):
            raise JournalError("move_target needs a string id")
        box = e.get("box")
        if (not isinstance(box, list) or len(box) != 4
                or not all(_is_number(v) for v in box)
                or not (box[0] < box[2] and box[1] < box[3])):
            raise JournalError("move_target needs box as [x0, y0, x1, y1] with x0<x1, y0<y1")
    elif type_ == "set_note":
        text = e.get("text")
        if not _is_str(text) or len(text) > NOTE_MAX:
            raise JournalError(f"set_note needs text as a string of at most {NOTE_MAX} chars")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/review -q`
Expected: all pass, except the parity tests if node is present: the fuzz does not emit the new types yet, so they still pass

- [ ] **Step 5: Commit**

```bash
git add app/review/journal.py tests/review/test_journal.py
git commit -m "journal: move_target, set_note and closed reasons on edit/delete

The client review campaign logs anchor moves, a per-drawing note and why a
value was edited or deleted; the reason keys are phase 2's, so both
features share one vocabulary.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7: replay — anchors move, notes are read, mismatches see the anchor

**Files:**
- Modify: `app/review/replay.py`
- Test: `tests/review/test_replay.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/review/test_replay.py`:

```python
from app.review.replay import mismatches, note_of, replay


def _row(**kw):
    return {"id": "r1", "char_type": "", "nominal": "1", "upper_tol": "", "lower_tol": "",
            "balloon_xy": [1, 1], "target_region": [0, 0, 10, 10], **kw}


def test_move_target_moves_the_anchor_and_mismatches_compare_it():
    rows = replay([_row()], [{"seq": 1, "type": "move_target", "id": "r1", "box": [2, 2, 12, 12]}])
    assert rows["r1"]["target_region"] == [2, 2, 12, 12]
    assert mismatches(rows, [_row(target_region=[2, 2, 12, 12])], []) == []
    assert mismatches(rows, [_row()], []) == ["r1"]


def test_set_note_changes_no_row_and_the_latest_note_wins():
    events = [{"seq": 1, "type": "set_note", "text": "a"},
              {"seq": 2, "type": "set_note", "text": "b"}]
    assert replay([_row()], events)["r1"]["nominal"] == "1"
    assert note_of(events) == "b"
    assert note_of([]) == ""
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/review/test_replay.py -q`
Expected: FAIL, `ImportError: cannot import name 'note_of'`

- [ ] **Step 3: Implement** — in `app/review/replay.py`:

`__all__ = ["replay", "mismatches", "note_of", "VALUE_FIELDS"]`

In `replay`, add before the `else:` branch:

```python
        elif t == "move_target":
            if e["id"] in rows:
                rows[e["id"]]["target_region"] = list(e["box"])
        elif t == "set_note":
            pass   # a drawing-level fact, not a row's: read with note_of()
```

Add after `_xy`:

```python
def _box(r):
    b = r.get("target_region")
    return None if b is None else tuple(round(float(v), 3) for v in b)


def note_of(events: Iterable[dict]) -> str:
    """The drawing's note after NET events (latest set_note wins; "" if none)."""
    note = ""
    for e in events:
        if e["type"] == "set_note":
            note = e["text"]
    return note
```

In `mismatches`, add `or _box(a) != _box(b)` after `or _xy(a) != _xy(b)`.

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/review -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/review/replay.py tests/review/test_replay.py
git commit -m "replay: anchors move, notes are read, mismatches compare the anchor

Without target_region in mismatches a JS/Python divergence on an anchor
move would seal silently.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: state.js — the same ops in the browser

**Files:**
- Modify: `app/static/js/state.js`
- Test: `tests/js/state.test.mjs`

- [ ] **Step 1: Write the failing tests** — append to `tests/js/state.test.mjs`:

```js
test('opMoveTarget moves the anchor, undoes, and emits move_target', () => {
  load([row('a', 0, 0)]);
  const op = S.opMoveTarget('a', [5, 5, 20, 20]);
  S.apply(op);
  assert.deepEqual(S.state.rows[0].target_region, [5, 5, 20, 20]);
  assert.deepEqual(op.event(), { type: 'move_target', id: 'a', box: [5, 5, 20, 20] });
  S.undo();
  assert.deepEqual(S.state.rows[0].target_region, [0, 0, 10, 10]);
});

test('opSetNote sets the drawing note and undo restores the old one', () => {
  load([row('a', 0, 0)]);
  assert.equal(S.state.note, '');
  const op = S.opSetNote('we never balloon chamfers');
  S.apply(op);
  assert.equal(S.state.note, 'we never balloon chamfers');
  assert.deepEqual(op.event(), { type: 'set_note', text: 'we never balloon chamfers' });
  S.undo();
  assert.equal(S.state.note, '');
});

test('a reason rides on edit_cell and delete_row only when given', () => {
  load([row('a', 0, 0)]);
  assert.equal('reason' in S.opEditCell('a', 'nominal', '2').event(), false);
  const withReason = S.opEditCell('a', 'nominal', '2', 'misread_digit');
  S.apply(withReason);
  assert.equal(withReason.event().reason, 'misread_digit');
  assert.equal('reason' in S.opDeleteRow('a').event(), false);
  assert.equal(S.opDeleteRow('a', 'duplicate').event().reason, 'duplicate');
});

test('opFromEvent restores move_target, set_note and reasons', () => {
  load([row('a', 0, 0)]);
  S.restoreSession({ session_id: 's', image_url: '', rows: [row('a', 0, 0)], notes: null }, [
    { seq: 1, type: 'move_target', id: 'a', box: [1, 1, 9, 9] },
    { seq: 2, type: 'set_note', text: 'n' },
    { seq: 3, type: 'edit_cell', id: 'a', field: 'nominal', old: '1', new: '3', reason: 'other' },
  ]);
  assert.deepEqual(S.state.rows[0].target_region, [1, 1, 9, 9]);
  assert.equal(S.state.note, 'n');
  assert.equal(S.state.rows[0].nominal, '3');
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/js/state.test.mjs`
Expected: FAIL, `S.opMoveTarget is not a function`

- [ ] **Step 3: Implement** — in `app/static/js/state.js`:

1. Wherever `setSession(payload)` resets per-session state, add `state.note = '';` and give the initial `state` object a `note: ''` field. Find it with `grep -n "export function setSession" -A20 app/static/js/state.js`, and the initial state with `grep -n "export const state" -A20`.

2. Replace `opDeleteRow` and `opEditCell` with versions taking an optional reason:

```js
export function opDeleteRow(id, reason) {
  let removed = null;
  return {
    label: 'delete balloon',
    do() {
      removed = state.rows.find((r) => r.id === id) || null;
      state.rows = state.rows.filter((r) => r.id !== id);
      renumber();
      if (state.selectedId === id) state.selectedId = null;
    },
    undo() {
      if (removed) {
        state.rows.push(removed);
        renumber();
      }
    },
    // `reason` is omitted, never sent as undefined/null, so the server's
    // e.get("reason") and this agree on "absent" (journal.py).
    event: () => (reason ? { type: 'delete_row', id, reason } : { type: 'delete_row', id }),
  };
}

export function opEditCell(id, field, newValue, reason) {
  let oldValue = null;
  return {
    label: `edit ${field}`,
    do() {
      const r = state.rows.find((x) => x.id === id);
      if (!r) return;
      oldValue = r[field];
      r[field] = newValue;
    },
    undo() {
      const r = state.rows.find((x) => x.id === id);
      if (r) r[field] = oldValue;
    },
    // evaluated after do(), so oldValue is the captured one
    event: () => {
      const e = { type: 'edit_cell', id, field, old: oldValue ?? '', new: newValue };
      if (reason) e.reason = reason;
      return e;
    },
  };
}
```

3. Add after `opMoveRow`:

```js
// The anchor: which feature the balloon points at (campaign design §3),
// separate from where the bubble sits (opMoveRow).
export function opMoveTarget(id, newBox) {
  let oldBox = null;
  return {
    label: 'move anchor',
    do() {
      const r = state.rows.find((x) => x.id === id);
      if (!r) return;
      oldBox = r.target_region ? [...r.target_region] : null;
      r.target_region = [...newBox];
      renumber();
    },
    undo() {
      const r = state.rows.find((x) => x.id === id);
      if (r) { r.target_region = oldBox; renumber(); }
    },
    event: () => ({ type: 'move_target', id, box: [...newBox] }),
  };
}

// The drawing's free-text note (campaign design §3). Not a row: replay
// reads it with note_of().
export function opSetNote(text) {
  let oldText = '';
  return {
    label: 'edit note',
    do() { oldText = state.note; state.note = text; },
    undo() { state.note = oldText; },
    event: () => ({ type: 'set_note', text }),
  };
}
```

4. In `opFromEvent`, replace the `delete_row` and `edit_cell` cases and add two:

```js
    case 'delete_row': return opDeleteRow(e.id, e.reason);
    case 'move_target': return opMoveTarget(e.id, e.box);
    case 'edit_cell': return opEditCell(e.id, e.field, e.new, e.reason);
    case 'set_note': return opSetNote(e.text);
```

- [ ] **Step 4: Run them and watch them pass**

Run: `node --test tests/js/*.test.mjs`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/static/js/state.js tests/js/state.test.mjs
git commit -m "ui: opMoveTarget, opSetNote and reasons on edit/delete

Mirrors the journal's new vocabulary op for op; restored reviews rebuild
anchors, notes and reasons exactly.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9: the parity fuzz covers the new ops

**Files:**
- Modify: `tests/js/fuzz_journal.mjs`, `tests/review/test_journal_replay_parity.py`

- [ ] **Step 1: Extend the parity test first (it must fail)** — in `tests/review/test_journal_replay_parity.py`:

Add to `_TYPE_KEYS`: `"move_target": {"id", "box"}, "set_note": {"text"},`. Import `note_of` from `app.review.replay`. In `test_journal_replay_parity_across_js_and_python`, inside the `for c in cases:` loop after the `bad` assertion:

```python
        assert note_of(net) == c["note"], (c["case"], "note", c["log"])
    types = {e["type"] for c in cases for e in c["events"]}
    # the fuzz must actually exercise the new vocabulary, not just pass
    assert {"move_target", "set_note"} <= types, types
    assert any("reason" in e for c in cases for e in c["events"]), "no reason was fuzzed"
```

In `test_a_review_rebuilt_from_the_server_matches_and_keeps_replaying`, after the `final` assertion:

```python
        assert note_of(net_events(c["events"])) == c["note"], (c["case"], "note", c["log"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/review/test_journal_replay_parity.py -q`
Expected: FAIL, `KeyError: 'note'`

- [ ] **Step 3: Extend the fuzz** — in `tests/js/fuzz_journal.mjs`'s `randomSteps`, replace the edit and delete branches and add two branches before the final `else`:

```js
    else if (r < 0.45) {
      const id = pick(anyIds); const f = pick(['char_type', 'nominal', 'upper_tol', 'lower_tol']);
      const v = pick(['', '1', '2', 'Diameter', '+0.1']);
      const reason = pick([undefined, undefined, 'misread_digit', 'other']);
      S.apply(S.opEditCell(id, f, v, reason)); label = `edit ${id}.${f}=${v}`;
    } else if (r < 0.55) {
      const id = pick(anyIds); S.apply(S.opMoveRow(id, [Math.round(rnd() * 100), 7])); label = `move ${id}`;
    } else if (r < 0.62) {
      const id = pick(anyIds);
      S.apply(S.opDeleteRow(id, pick([undefined, 'duplicate', 'wrong_region'])));
      label = `del ${id}`;
    } else if (r < 0.67) {
      const id = 'm' + (addN++);
      S.apply(S.opAddRow({ ...row(id), suggested: false, reviewed: false, source: 'manual' }));
      label = `add ${id}`;
    } else if (r < 0.72) {
      const id = pick(anyIds); const x = Math.round(rnd() * 400);
      S.apply(S.opMoveTarget(id, [x, 5, x + 20, 30])); label = `anchor ${id}`;
    } else if (r < 0.76) {
      const t = pick(['', 'policy', 'we never balloon chamfers']);
      S.apply(S.opSetNote(t)); label = `note ${t}`;
    } else if (r < 0.82) {
```

(The `confirm` branch keeps its body under the new `r < 0.82` bound; the final `else` stays the accept/unaccept branch.) Add `note: S.state.note,` to the object pushed into `cases`.

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/review/test_journal_replay_parity.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/js/fuzz_journal.mjs tests/review/test_journal_replay_parity.py
git commit -m "test: the replay parity fuzz drives anchors, notes and reasons

And asserts it did, so a fuzz that silently stops emitting a type cannot
pass vacuously.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

> **Milestone: journal vocabulary** — state must be checkpointed here before continuing.

---

## Milestone C — the client's review screen

### Task 10: `reasons.js` — vocabularies and the commit-with-reason flow

**Files:**
- Create: `app/static/js/reasons.js`
- Test: `tests/js/reasons.test.mjs`

- [ ] **Step 1: Write the failing tests** — create `tests/js/reasons.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EDIT_REASONS, DELETE_REASONS, commitWithReason } from '../../app/static/js/reasons.js';

test('vocabularies mirror journal.py exactly', () => {
  assert.deepEqual([...EDIT_REASONS].sort(),
    ['misread_digit', 'other', 'wrong_char_type', 'wrong_sign_or_unit', 'wrong_tolerance']);
  assert.deepEqual([...DELETE_REASONS].sort(),
    ['duplicate', 'not_a_characteristic', 'other', 'unballooned_real_dimension', 'wrong_region']);
});

test('the op is applied once, with the chosen reason', async () => {
  const applied = [];
  await commitWithReason('edit', (reason) => ({ reason }), async () => 'misread_digit',
                         (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: 'misread_digit' }]);
});

test('dismissing applies the op with no reason; an off-vocabulary answer counts as none', async () => {
  const applied = [];
  await commitWithReason('delete', (reason) => ({ reason }), async () => undefined,
                         (op) => applied.push(op));
  await commitWithReason('delete', (reason) => ({ reason }), async () => 'misread_digit',
                         (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: undefined }, { reason: undefined }]);
});

test('a failing prompt still applies the op: the edit is never lost', async () => {
  const applied = [];
  await commitWithReason('edit', (reason) => ({ reason }),
                         async () => { throw new Error('no DOM'); }, (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: undefined }]);
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/js/reasons.test.mjs`
Expected: FAIL, `Cannot find module`

- [ ] **Step 3: Implement** — create `app/static/js/reasons.js`:

```js
// Reason chips after an edit or delete (HITL design §1; campaign plan C1
// decisions 6-7). The vocabularies mirror app/review/journal.py's
// EDIT_REASONS / DELETE_REASONS; tests/js/reasons.test.mjs pins the mirror.
// HITL phase 2 imports these rather than defining its own.

export const EDIT_REASONS = new Set([
  'misread_digit', 'wrong_tolerance', 'wrong_char_type', 'wrong_sign_or_unit', 'other',
]);
export const DELETE_REASONS = new Set([
  'duplicate', 'not_a_characteristic', 'unballooned_real_dimension', 'wrong_region', 'other',
]);

export const REASON_LABELS = {
  edit: [
    ['misread_digit', 'Misread digit'],
    ['wrong_tolerance', 'Wrong/missing tolerance'],
    ['wrong_char_type', 'Wrong characteristic type'],
    ['wrong_sign_or_unit', 'Wrong sign or unit'],
    ['other', 'Other'],
  ],
  delete: [
    ['duplicate', 'Duplicate of another balloon'],
    ['not_a_characteristic', 'Not a characteristic (text, title block…)'],
    ['unballooned_real_dimension', "Real dimension, but we don't balloon it"],
    ['wrong_region', 'Wrong region / misplaced box'],
    ['other', 'Other'],
  ],
};

// Apply-on-resolution (decision 7): the op is built and applied ONCE, after
// the prompt resolves or is dismissed, so the reason is part of the very
// event the journal sends. `ask(kind)` resolves to a reason key or
// undefined; it may throw (no DOM, popover torn down) and the edit still
// lands, reasonless -- a reviewer's change is never lost to a chip.
export async function commitWithReason(kind, makeOp, ask, apply) {
  const vocab = kind === 'edit' ? EDIT_REASONS : DELETE_REASONS;
  let reason;
  try {
    reason = await ask(kind);
  } catch {
    reason = undefined;
  }
  apply(makeOp(vocab.has(reason) ? reason : undefined));
}

// The popover itself (DOM). Keys 1-5 pick a chip while it is open and are
// swallowed so the global filter-pill shortcuts never see them; any other
// key or an outside click dismisses with no reason.
export function askReason(kind, anchorEl) {
  return new Promise((resolve) => {
    const el = document.createElement('div');
    el.className = 'reason-popover';
    const title = document.createElement('div');
    title.className = 'reason-title';
    title.textContent = kind === 'edit' ? 'Why was it wrong? (optional)' : 'Why delete it? (optional)';
    el.appendChild(title);
    const entries = REASON_LABELS[kind];
    entries.forEach(([key, label], i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = `${i + 1} ${label}`;
      b.addEventListener('click', (e) => { e.stopPropagation(); done(key); });
      el.appendChild(b);
    });
    const r = anchorEl?.getBoundingClientRect?.();
    Object.assign(el.style, { position: 'fixed',
      left: `${Math.max(8, r ? r.left : 40)}px`, top: `${r ? r.bottom + 4 : 40}px` });
    document.body.appendChild(el);

    function onKey(e) {
      e.stopPropagation(); e.preventDefault();
      const n = Number(e.key);
      done(n >= 1 && n <= entries.length ? entries[n - 1][0] : undefined);
    }
    function onClick(e) { if (!el.contains(e.target)) done(undefined); }
    function done(reason) {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onClick, true);
      el.remove();
      resolve(reason);
    }
    // capture phase: the popover sees keys before shortcuts.js does
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onClick, true);
  });
}
```

Append to `app/static/styles/components.css`:

```css
/* Reason chips after an edit/delete (campaign plan C1 Task 10) */
.reason-popover { z-index: 50; display: flex; flex-direction: column; gap: 4px;
  padding: 8px; background: var(--panel, #fff); border: 1px solid var(--line, #ccc);
  border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.18); max-width: 320px; }
.reason-popover .reason-title { font-size: 12px; opacity: .7; margin-bottom: 2px; }
.reason-popover .chip { text-align: left; }
```

- [ ] **Step 4: Run them and watch them pass**

Run: `node --test tests/js/reasons.test.mjs`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/static/js/reasons.js tests/js/reasons.test.mjs app/static/styles/components.css
git commit -m "ui: reasons.js — closed reason vocabularies and apply-on-resolution

The op is applied once the chip popover resolves or is dismissed, so the
reason rides on the edit/delete event itself and a dismissed or broken
popover never loses the edit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 11: edits and deletes go through the reason prompt

**Files:**
- Modify: `app/static/js/table.js:199-203`, `app/static/js/viewer.js:266` and `:297`

- [ ] **Step 1: Wire the table edit** — in `table.js`, import `{ commitWithReason, askReason } from './reasons.js'` and replace the blur handler's `apply(opEditCell(r.id, k, newVal));` with:

```js
        commitWithReason('edit', (reason) => opEditCell(r.id, k, newVal, reason),
                         (kind) => askReason(kind, td), apply);
```

- [ ] **Step 2: Wire both viewer deletes** — in `viewer.js`, import the same two names. Replace `if (id) apply(opDeleteRow(id));` (the ×-click, line ~266) with:

```js
      if (id) commitWithReason('delete', (reason) => opDeleteRow(id, reason),
                               (kind) => askReason(kind, e.target), apply);
```

and the Delete-key line (~297) with:

```js
      if (state.selectedId) {
        const id = state.selectedId;
        commitWithReason('delete', (reason) => opDeleteRow(id, reason),
                         (kind) => askReason(kind, null), apply);
        e.preventDefault();
      }
```

- [ ] **Step 3: Run the JS suite and the parity fuzz**

Run: `node --test tests/js/*.test.mjs` then `python -m pytest tests/review tests/test_js_state.py -q`
Expected: all pass. These call sites are DOM-bound; Task 15's browser check exercises them.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/table.js app/static/js/viewer.js
git commit -m "ui: edits and deletes ask for an optional reason

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 12: `anchor.js` + handles on the selected row's anchor box

**Files:**
- Create: `app/static/js/anchor.js`
- Modify: `app/static/js/viewer.js` (`renderMarkers`, mousedown/mousemove/mouseup), `app/static/styles/components.css`
- Test: `tests/js/anchor.test.mjs`

- [ ] **Step 1: Write the failing tests** — create `tests/js/anchor.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dragBox, MIN_SIDE } from '../../app/static/js/anchor.js';

const B = [10, 10, 50, 40];

test('the body handle moves the whole box', () => {
  assert.deepEqual(dragBox(B, 'body', 5, -3), [15, 7, 55, 37]);
});

test('a corner moves only its two edges', () => {
  assert.deepEqual(dragBox(B, 'nw', -4, -2), [6, 8, 50, 40]);
  assert.deepEqual(dragBox(B, 'se', 10, 10), [10, 10, 60, 50]);
  assert.deepEqual(dragBox(B, 'ne', 3, 4), [10, 14, 53, 40]);
  assert.deepEqual(dragBox(B, 'sw', 3, 4), [13, 10, 50, 44]);
});

test('a box never collapses or inverts', () => {
  const b = dragBox(B, 'se', -100, -100);
  assert.ok(b[2] - b[0] >= MIN_SIDE && b[3] - b[1] >= MIN_SIDE, String(b));
  const c = dragBox(B, 'nw', 100, 100);
  assert.ok(c[0] < c[2] && c[1] < c[3], String(c));
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/js/anchor.test.mjs`
Expected: FAIL, `Cannot find module`

- [ ] **Step 3: Implement the geometry** — create `app/static/js/anchor.js`:

```js
// Pure geometry for editing a row's anchor box (campaign design §3).
// Plan-space pixels, [x0, y0, x1, y1]. The journal refuses an empty or
// inverted box (journal.py move_target), so the drag can never emit one.
export const MIN_SIDE = 4;

export function dragBox([x0, y0, x1, y1], handle, dx, dy) {
  if (handle === 'body') return [x0 + dx, y0 + dy, x1 + dx, y1 + dy];
  if (handle.includes('w')) x0 = Math.min(x0 + dx, x1 - MIN_SIDE);
  if (handle.includes('e')) x1 = Math.max(x1 + dx, x0 + MIN_SIDE);
  if (handle.includes('n')) y0 = Math.min(y0 + dy, y1 - MIN_SIDE);
  if (handle.includes('s')) y1 = Math.max(y1 + dy, y0 + MIN_SIDE);
  return [x0, y0, x1, y1];
}
```

- [ ] **Step 4: Run them and watch them pass**

Run: `node --test tests/js/anchor.test.mjs`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire the handles into the viewer** — in `viewer.js`:

Import `{ dragBox } from './anchor.js'` and `opMoveTarget` from `./state.js` (add it to the existing state import). Add a module variable next to `dragging`: `let anchorDrag = null;  // { id, handle, startPlan, box }`.

Add a function after `syncSuggestBox`:

```js
// The selected balloon's anchor -- the feature it points at -- drawn with a
// body and four corner handles (campaign design §3). Separate from the
// bubble: dragging the marker moves the bubble (move_row), dragging this
// moves the anchor (move_target).
function syncAnchorBox() {
  let box = markerLayer.querySelector('.anchor-box');
  const r = state.rows.find((x) => x.id === state.selectedId);
  if (!r || !r.target_region || r.suggested) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement('div');
    box.className = 'anchor-box';
    box.dataset.handle = 'body';
    for (const h of ['nw', 'ne', 'sw', 'se']) {
      const k = document.createElement('div');
      k.className = `anchor-handle ${h}`;
      k.dataset.handle = h;
      box.appendChild(k);
    }
    markerLayer.prepend(box);
  }
  const reg = anchorDrag && anchorDrag.id === r.id ? anchorDrag.box : r.target_region;
  const a = planToView(reg[0], reg[1]);
  const b = planToView(reg[2], reg[3]);
  Object.assign(box.style, { left: a.x + 'px', top: a.y + 'px',
    width: (b.x - a.x) + 'px', height: (b.y - a.y) + 'px' });
}
```

Call `syncAnchorBox();` as the last line of `renderMarkers()`.

In the `mousedown` handler, insert BEFORE the `// MARKER drag` block:

```js
    // ANCHOR drag: body or corner of the selected row's anchor box
    const handleEl = e.target.closest('.anchor-box, .anchor-handle');
    if (handleEl && e.button === 0 && state.selectedId) {
      const row = state.rows.find((x) => x.id === state.selectedId);
      const v = viewportPoint(e.clientX, e.clientY);
      anchorDrag = { id: row.id, handle: handleEl.dataset.handle,
                     startPlan: viewToPlan(v.x, v.y), box: [...row.target_region],
                     origin: [...row.target_region] };
      e.preventDefault();
      e.stopPropagation();
      return;
    }
```

In `mousemove`, insert before `if (dragging) {`:

```js
    if (anchorDrag) {
      const v = viewportPoint(e.clientX, e.clientY);
      const p = viewToPlan(v.x, v.y);
      anchorDrag.box = dragBox(anchorDrag.origin, anchorDrag.handle,
                               p.x - anchorDrag.startPlan.x, p.y - anchorDrag.startPlan.y);
      syncAnchorBox();
      return;
    }
```

In `mouseup`, insert before `if (dragging) {`:

```js
    if (anchorDrag) {
      const { id, box, origin } = anchorDrag;
      anchorDrag = null;
      if (box.some((v, i) => Math.abs(v - origin[i]) >= 1)) {
        apply(opMoveTarget(id, box.map((v) => Math.round(v * 10) / 10)));
      } else {
        syncAnchorBox();
      }
    }
```

Append to `components.css`:

```css
/* The selected balloon's anchor box (campaign plan C1 Task 12) */
#marker-layer .anchor-box { position: absolute; border: 2px solid var(--accent, #2563eb);
  background: rgba(37, 99, 235, .06); cursor: move; pointer-events: auto; box-sizing: border-box; }
#marker-layer .anchor-handle { position: absolute; width: 10px; height: 10px;
  background: var(--accent, #2563eb); border: 1px solid #fff; border-radius: 2px; }
#marker-layer .anchor-handle.nw { left: -6px; top: -6px; cursor: nwse-resize; }
#marker-layer .anchor-handle.se { right: -6px; bottom: -6px; cursor: nwse-resize; }
#marker-layer .anchor-handle.ne { right: -6px; top: -6px; cursor: nesw-resize; }
#marker-layer .anchor-handle.sw { left: -6px; bottom: -6px; cursor: nesw-resize; }
```

Selecting a row elsewhere (table click) must re-render the box: confirm that the `select` event triggers `renderMarkers()` with `grep -n "on('select'" app/static/js/viewer.js`. If it does not, add `on('select', () => renderMarkers());` in `initViewer`.

- [ ] **Step 6: Run the suite**

Run: `node --test tests/js/*.test.mjs` then `python -m pytest tests/test_js_state.py tests/review -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add app/static/js/anchor.js app/static/js/viewer.js app/static/styles/components.css tests/js/anchor.test.mjs
git commit -m "ui: edit the selected balloon's anchor box with handles

Body drag moves it, corners resize it; one move_target per drag, never a
collapsed or inverted box. The bubble still moves with the marker.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 13: campaign mode in the review screen — Approve, counter, note, campaign-only chrome

**Files:**
- Create: `app/static/js/campaign.js`
- Modify: `app/static/js/main.js` (finisher, `wireFinish`, `finish`, the `session` handler, `updateReviewBarInfo`, `pingHealth`), `app/static/index.html`, `app/static/styles/components.css`
- Test: `tests/js/campaign.test.mjs`

- [ ] **Step 1: Write the failing tests** — create `tests/js/campaign.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { approvedCount, finishCopy, isCampaignDoc } from '../../app/static/js/campaign.js';

const d = (id, batch, extra = {}) => ({ id, batch_id: batch, campaign: null,
  state: 'ready', final_revision: null, ...extra });

test('approved = drawings of the batch with a sealed revision', () => {
  const docs = [d('a', 'B', { final_revision: 1, state: 'finished' }),
                d('b', 'B', { final_revision: 2, state: 'in_review' }),  // re-opened: still approved once
                d('c', 'B'), d('x', 'Z', { final_revision: 1 })];
  assert.deepEqual(approvedCount(docs, 'B'), { approved: 2, total: 3 });
});

test('campaign drawings get Approve copy, ordinary ones keep Finish & Export', () => {
  assert.equal(isCampaignDoc(d('a', 'B', { campaign: 'pilot-1' })), true);
  assert.equal(isCampaignDoc(d('a', 'B')), false);
  assert.equal(finishCopy(true).label, 'Approve drawing');
  assert.match(finishCopy(true).title, /no export/i);
  assert.equal(finishCopy(false).label, 'Finish & Export');
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/js/campaign.test.mjs`
Expected: FAIL, `Cannot find module`

- [ ] **Step 3: Implement the pure helpers** — create `app/static/js/campaign.js`:

```js
// Review-campaign rules for the review screen (campaign design §3). Pure.

export const isCampaignDoc = (doc) => !!(doc && doc.campaign);

// "12 / 50 approved": a drawing counts once it has ANY sealed revision --
// re-opening an approved drawing to fix something does not un-approve it.
export function approvedCount(docs, batchId) {
  const inBatch = docs.filter((x) => x.batch_id === batchId && x.state !== 'purged');
  return { approved: inBatch.filter((x) => x.final_revision != null).length,
           total: inBatch.length };
}

export function finishCopy(campaign) {
  return campaign
    ? { label: 'Approve drawing',
        title: 'Everything you did not change is confirmed as correct. Seals your review — no export.' }
    : { label: 'Finish & Export',
        title: 'Seal the review and export Excel + ballooned PDF' };
}
```

- [ ] **Step 4: Run them and watch them pass**

Run: `node --test tests/js/campaign.test.mjs`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire campaign mode into `main.js` and the page**

`index.html`:
- Wrap the Finish button's text in a span: replace the text node `Finish &amp; Export` inside `#finish-btn` with `<span id="finish-label">Finish &amp; Export</span>`.
- Add `data-campaign-hide` to `#wl-import`, `#wl-import-folder` and `#wl-bulk`.
- Add after the `#review-bar-nav` element's `<span id="rb-position">1 of 1</span>`: `<span id="rb-approved" hidden></span>`.
- Add directly before `#finish-btn`:

```html
        <textarea id="doc-note" rows="1" maxlength="2000" hidden
                  placeholder="Note on this drawing (optional) — e.g. “we never balloon chamfers”"></textarea>
```

`components.css`:

```css
/* Campaign mode (campaign plan C1 Task 13) */
body.campaign-only [data-campaign-hide] { display: none !important; }
#doc-note { width: 280px; resize: vertical; font: inherit; font-size: 12px; }
```

`main.js`:
1. Import `{ approvedCount, finishCopy, isCampaignDoc } from './campaign.js'` and `opSetNote` from `./state.js` (add it to the existing state import).
2. Add a helper next to `currentBatchId()`:

```js
function currentDocIsCampaign() {
  return isCampaignDoc(worklistDocsCache().find((d) => d.id === state.sessionId));
}
```

3. In the `createFinisher({...})` call, replace the `exportAll:` line with:

```js
  // A campaign drawing is approved, never exported (campaign design §3):
  // the seal is the whole act. Ordinary drawings keep both downloads.
  exportAll: (() => {
    const exporter = createExporter((sid, kind, revision) => downloadExport(sid, kind, revision));
    return (snapshot, revision) => (currentDocIsCampaign() ? Promise.resolve() : exporter(snapshot, revision));
  })(),
```

4. Add a function and call it from the `on('session', …)` handler's `if (state.sessionId)` branch, from the `on('change', …)` handler in `wireFinish`, and at the end of `updateReviewBarInfo()`:

```js
function syncCampaignChrome() {
  const campaign = currentDocIsCampaign();
  const copy = finishCopy(campaign);
  const label = document.getElementById('finish-label');
  if (label && label.textContent !== copy.label) label.textContent = copy.label;
  const btn = document.getElementById('finish-btn');
  if (btn && canFinish()) btn.title = copy.title;
  const note = document.getElementById('doc-note');
  if (note) {
    note.hidden = !campaign || !state.sessionId;
    if (document.activeElement !== note) note.value = state.note || '';
  }
  const appr = document.getElementById('rb-approved');
  if (appr) {
    appr.hidden = !campaign;
    if (campaign) {
      const { approved, total } = approvedCount(worklistDocsCache(), currentBatchId());
      appr.textContent = `${approved} / ${total} approved`;
    }
  }
}
```

In `wireFinish`'s `on('change', …)`, replace the `btn.title = canFinish() ? 'Seal the review and export Excel + ballooned PDF' : …` expression's first branch with `finishCopy(currentDocIsCampaign()).title`.

5. Note box wiring — add to `init()` (beside `wireFinish();`):

```js
  const note = document.getElementById('doc-note');
  note?.addEventListener('blur', () => {
    const text = note.value.slice(0, 2000);
    if (state.sessionId && text !== (state.note || '')) apply(opSetNote(text));
  });
```

(`apply` comes from `./state.js`; add it to the import if `main.js` does not already import it.)

6. In `finish()`'s `'done'` branch, make the copy campaign-aware:
- replace `toast({ kind: 'ok', title: 'Finished', … })` with `toast({ kind: 'ok', title: currentDocIsCampaign() ? 'Approved' : 'Finished', msg: \`Review sealed as revision r${res.revision}\` });`
- replace the batch-complete toast with `toast({ kind: 'ok', title: currentDocIsCampaign() ? 'All drawings approved — thank you' : 'Batch complete', msg: currentDocIsCampaign() ? 'You can still reopen any drawing from the list.' : 'Export batch from the work list.' });`

Read `currentDocIsCampaign()` once into a `const campaign` at the top of the `'done'` branch, before `go()` changes `state.sessionId`.

7. In `pingHealth()`, after `const data = await health();` and its null check: `document.body.classList.toggle('campaign-only', !!data.campaign_only);`

- [ ] **Step 6: Run the suites**

Run: `node --test tests/js/*.test.mjs` then `python -m pytest tests/test_js_state.py tests/test_api_campaign.py -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add app/static/js/campaign.js app/static/js/main.js app/static/index.html app/static/styles/components.css tests/js/campaign.test.mjs
git commit -m "ui: campaign mode — Approve instead of Finish & Export, counter, note

A campaign drawing seals without exporting, the review bar counts approved
drawings, a per-drawing note is journaled as set_note, and a campaign-only
instance hides import and bulk controls.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

> **Milestone: client review screen** — state must be checkpointed here before continuing.

---

## Milestone D — getting drawings in and records out, and the VPS

### Task 14: `share_select` manifest carries roles

**Files:**
- Modify: `app/eval/share_select.py` (`manifest`)
- Test: `tests/eval/test_share_select.py`

- [ ] **Step 1: Write the failing test** — append to `tests/eval/test_share_select.py`:

```python
def test_manifest_records_each_picks_role():
    """The review campaign stores great/problem per drawing, so its report
    can reweight the 60/40 pick (campaign design §5)."""
    from app.eval.share_select import manifest
    picks = [{"run": "r5-control", "hash": "aa", "role": "great"},
             {"run": "r3-trainpredict", "hash": "bb", "role": "problem"}]
    m = manifest(picks)
    assert m["roles"] == {"aa": "great", "bb": "problem"}
    assert {r["run"] for r in m["runs"]} == {"r5-control", "r3-trainpredict"}
```

First check the pick dict's key names with `grep -n "def take" -A4 app/eval/share_select.py`, and adjust `"hash"`/`"run"` in the test if they differ.

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/eval/test_share_select.py -q`
Expected: FAIL, `KeyError: 'roles'`

- [ ] **Step 3: Implement** — in `manifest()`, add `"roles": {p["hash"]: p["role"] for p in picks}` to the returned dict (the same key names `take()` uses). `export_share.py` reads only `runs`, so it is unaffected.

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/eval/test_share_select.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/eval/share_select.py tests/eval/test_share_select.py
git commit -m "eval: share_select's manifest records each pick's role

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 15: `campaign_build.py` — bundles from dumps (operator-only)

**Files:**
- Create: `campaign_build.py` (repo root, next to `export_share.py`)
- Test: `tests/test_campaign_build.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_campaign_build.py`:

```python
"""campaign_build (campaign design §2; plan C1 decision 4). Synthetic dump
and the repo's own sample PDF only -- the real tool is the operator's."""
import json

from app.eval.dump import save_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult

import campaign_build


def _dump(tmp_path, sample_pdf):
    rows = [Characteristic(pos=1, id="r1", raw_text="20 +0,1 -0,1", nominal="20",
                           kind="dimension", target_region=(40, 40, 120, 70),
                           balloon_xy=(10, 10), confidence=0.995)]
    dump = PredictionDump(doc_id="DOC-1", config=RunConfig(), scale=300 / 72.0,
                          page_rect=(0, 0, 595, 842),
                          result=ExtractionResult(characteristics=rows))
    return save_dump(dump, tmp_path / "runs" / "r5-control")


def test_an_entry_holds_what_the_loader_needs(tmp_path, sample_pdf):
    dump_path = _dump(tmp_path, sample_pdf)
    out = tmp_path / "bundle"
    entry = campaign_build.build_entry(dump_path, sample_pdf, out, campaign="pilot-1",
                                       group="great", run="r5-control", doc_hash="aa")
    assert {p.name for p in entry.iterdir()} == {
        "input.pdf", "page.png", "result.json", "campaign.json"}
    result = json.loads((entry / "result.json").read_text())
    assert set(result) == {"rows", "notes", "marks", "title_block"}
    assert [r["id"] for r in result["rows"]] == ["r1"]
    assert result["rows"][0]["balloon_xy"] is not None   # re-placed for today
    facts = json.loads((entry / "campaign.json").read_text())
    assert facts == {"campaign": "pilot-1", "group": "great", "source_run": "r5-control",
                     "doc_hash": "aa", "file_name": "DOC-1.pdf"}
```

If `RunConfig()` needs arguments, build the smallest valid one the way `tests/eval` already does (`grep -rn "RunConfig(" tests/eval | head -3`).

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_campaign_build.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'campaign_build'`

- [ ] **Step 3: Implement** — create `campaign_build.py`:

```python
"""Build a review-campaign bundle from stored prediction dumps
(docs/plans/2026-10-21-client-review-campaign-design.md §2).

OPERATOR TOOL: it opens client drawings and dumps, so it is NOT in the agent
allowlist (CLAUDE.md §1) -- an agent selects (share_select, hashes only) and
hands you ONE command:

    python campaign_build.py --manifest ~/campaign-manifest.json --name pilot-1 \
        --out ~/campaign-pilot-1

Each pick becomes <out>/<hash>/ with input.pdf, page.png, result.json and
campaign.json -- exactly what app.campaign.load expects. Rows are what we
would ship TODAY (plan C1 decision 4): today's flags/drops/tray via
reapply_current_code, then the shipped plain-rectangle drop, renumbering and
whitespace balloon placement via export_share.refresh_rows. Terminal output
is hashes only. The output folder must stay outside the protected root and
any git tree.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from app.batch.worker import result_payload
from app.eval.anon import Anonymizer
from app.eval.dump import load_dump
from app.eval.reapply import reapply_current_code
from app.pipeline.render import render_page
from export_share import _PRED, refresh_rows


def build_entry(dump_path: Path, pdf_path: Path, out: Path, *, campaign: str,
                group: str, run: str, doc_hash: str) -> Path:
    dump = reapply_current_code(load_dump(dump_path))
    res = dump.result
    scale = res.render_scale or dump.scale
    rows, _ = refresh_rows(list(res.characteristics), pdf_path, scale)
    res = res.model_copy(update={"characteristics": rows})
    entry = Path(out) / doc_hash
    entry.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(pdf_path, entry / "input.pdf")
    # The page the app shows must be the render the rows' pixel boxes live
    # in: the dump's own scale, never the app's default dpi.
    with tempfile.TemporaryDirectory() as tmp:
        render = render_page(pdf_path, dpi=int(round(scale * 72.0)), out_dir=tmp)
        shutil.copyfile(render.png_path, entry / "page.png")
    (entry / "result.json").write_text(json.dumps(result_payload(res)), encoding="utf-8")
    (entry / "campaign.json").write_text(json.dumps({
        "campaign": campaign, "group": group, "source_run": run,
        "doc_hash": doc_hash, "file_name": f"{dump.doc_id}.pdf"}), encoding="utf-8")
    return entry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("SINDRI_CLIENT_ROOT",
                                                     str(Path.home() / "sindri-client-data")))
    ap.add_argument("--manifest", required=True, help="share_select output, with roles")
    ap.add_argument("--name", required=True, help="campaign name, e.g. pilot-1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8"))
    roles = m.get("roles") or {}
    anon = Anonymizer()
    failed = 0
    for entry in m["runs"]:
        run_dir = root / "runs" / entry["run"]
        by_hash = {anon(p.name[:-len(_PRED)]): p for p in run_dir.glob(f"*{_PRED}")}
        for h in entry["hashes"]:
            dump_path = by_hash.get(h)
            pdf = root / "corpus" / "originals" / (
                dump_path.name[:-len(_PRED)] + ".pdf") if dump_path else None
            if dump_path is None or not pdf.is_file() or h not in roles:
                print(f"SKIP {h}: {'no dump' if dump_path is None else 'no drawing' if not pdf.is_file() else 'no role'}",
                      file=sys.stderr)
                failed += 1
                continue
            build_entry(dump_path, pdf, out, campaign=args.name, group=roles[h],
                        run=entry["run"], doc_hash=h)
            print(f"ok   {h} ({roles[h]})")
    n = sum(1 for p in out.iterdir() if p.is_dir())
    print(f"\n{n} drawings in {out}" + (f"  ({failed} FAILED -- see above)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/test_campaign_build.py -q`
Expected: PASS. If `refresh_rows` drops the row as a plain rectangle on the sample page, move `target_region` to a blank area of `tests/fixtures/sample.pdf`.

- [ ] **Step 5: Commit**

```bash
git add campaign_build.py tests/test_campaign_build.py
git commit -m "campaign: operator tool builds review bundles from dumps

Rows are what we ship today (re-applied policy, plain-rectangle drop,
whitespace placement); the page image is rendered at the dump's own scale
so the pixel boxes line up.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 16: `app.campaign.load` and `app.campaign.pull`

**Files:**
- Create: `app/campaign/load.py`, `app/campaign/pull.py`
- Test: `tests/campaign/test_load_pull.py`

- [ ] **Step 1: Write the failing tests** — create `tests/campaign/test_load_pull.py`:

```python
"""In-container loader and packer (campaign design §2, §4). Synthetic bundle;
the worker runs for real, so a loaded drawing ends `ready` exactly like an
extracted one."""
import json
import tarfile

import pytest

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Worker
from app.campaign import load, pull
from app.models import Characteristic
from app.review.store import ReviewStore


@pytest.fixture
def env(tmp_path):
    data = DataDir(tmp_path / "data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    repo, store = Repo(conn), ReviewStore(tmp_path / "reviews")
    def no_gpu(*a):
        raise AssertionError("a campaign drawing must never be extracted")
    worker = Worker(repo, data, store, no_gpu, lambda pdf: {"h": 1})
    yield data, repo, store, worker
    worker.stop()
    conn.close()


def bundle(tmp_path, sample_pdf, hashes=("aa", "bb")):
    root = tmp_path / "bundle"
    for h in hashes:
        e = root / h
        e.mkdir(parents=True)
        # distinct bytes per entry, or they would be one drawing
        (e / "input.pdf").write_bytes(sample_pdf.read_bytes() + h.encode())
        (e / "page.png").write_bytes(b"png")
        rows = [Characteristic(pos=1, id=f"{h}1", nominal="1").model_dump(mode="json")]
        (e / "result.json").write_text(json.dumps(
            {"rows": rows, "notes": None, "marks": None, "title_block": []}))
        (e / "campaign.json").write_text(json.dumps(
            {"campaign": "pilot-1", "group": "great", "source_run": "r", "doc_hash": h,
             "file_name": f"{h}.pdf"}))
    return root


def test_load_queues_every_entry_and_the_worker_makes_them_ready(tmp_path, sample_pdf, env):
    data, repo, store, worker = env
    out = load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    assert out == {"loaded": 2, "skipped": 0}
    while worker.run_once():
        pass
    docs = repo.list_documents()
    assert {d["state"] for d in docs} == {"ready"} and {d["campaign"] for d in docs} == {"pilot-1"}
    assert sorted(d["file_name"] for d in docs) == ["aa.pdf", "bb.pdf"]


def test_loading_the_same_bundle_twice_is_a_no_op(tmp_path, sample_pdf, env):
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    load.load_bundle(b, data, repo)
    assert load.load_bundle(b, data, repo) == {"loaded": 0, "skipped": 2}


def test_a_malformed_proposal_refuses_the_whole_bundle(tmp_path, sample_pdf, env):
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    (b / "bb" / "result.json").write_text(json.dumps({"rows": [{"pos": "x"}]}))
    with pytest.raises(load.BundleError):
        load.load_bundle(b, data, repo)
    assert repo.list_documents() == []   # validated before anything is written


def test_pull_packs_the_campaigns_review_records_and_an_index(tmp_path, sample_pdf, env):
    data, repo, store, worker = env
    load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    while worker.run_once():
        pass
    tar_path = tmp_path / "out.tar"
    n = pull.pull_campaign("pilot-1", repo, store, tar_path)
    assert n == 2
    with tarfile.open(tar_path) as t:
        names = t.getnames()
        index = json.load(t.extractfile("pilot-1/index.json"))
    assert {e["doc_hash"] for e in index["documents"]} == {"aa", "bb"}
    assert sum(n.endswith("/proposal.json") for n in names) == 2
    assert sum(n.endswith("/header.json") for n in names) == 2
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/campaign/test_load_pull.py -q`
Expected: FAIL, `ImportError: cannot import name 'load'`

- [ ] **Step 3: Implement** — create `app/campaign/load.py`:

```python
"""Load a campaign bundle into a running instance (campaign design §2).

    docker compose exec sindri python -m app.campaign.load /import/pilot-1

Runs beside the server on the same data dir: it only writes files and index
rows and queues them; the server's own worker turns each into a review
record, skipping extraction because result.json is already there (plan C1
decision 2). Every entry is validated BEFORE anything is written, so a bad
bundle leaves the instance untouched. Idempotent by drawing bytes."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import fitz
from pydantic import ValidationError

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.models import Characteristic

_FILES = ("input.pdf", "page.png", "result.json", "campaign.json")


class BundleError(ValueError):
    pass


def _check(entry: Path) -> dict:
    missing = [f for f in _FILES if not (entry / f).is_file()]
    if missing:
        raise BundleError(f"{entry.name}: missing {', '.join(missing)}")
    try:
        result = json.loads((entry / "result.json").read_text(encoding="utf-8"))
        for r in result["rows"]:
            Characteristic.model_validate(r)
        facts = json.loads((entry / "campaign.json").read_text(encoding="utf-8"))
    except (ValueError, KeyError, TypeError, ValidationError) as e:
        # TYPE only: a validation message can quote a client value (CLAUDE.md §1).
        raise BundleError(f"{entry.name}: unreadable proposal ({type(e).__name__})") from None
    for k in ("campaign", "group", "file_name"):
        if not isinstance(facts.get(k), str) or not facts[k]:
            raise BundleError(f"{entry.name}: campaign.json lacks {k}")
    return facts


def load_bundle(root, data: DataDir, repo: Repo) -> dict:
    entries = sorted(p for p in Path(root).iterdir() if p.is_dir())
    checked = [(e, _check(e)) for e in entries]
    if len({f["campaign"] for _, f in checked}) > 1:
        raise BundleError("a bundle holds exactly one campaign")
    loaded = skipped = 0
    for entry, facts in checked:
        pdf_bytes = (entry / "input.pdf").read_bytes()
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        if repo.campaign_document_by_sha(facts["campaign"], sha):
            skipped += 1
            continue
        with fitz.open(entry / "input.pdf") as doc:
            pages = doc.page_count
        doc_id = uuid.uuid4().hex
        d = data.doc_dir(doc_id)
        d.mkdir(parents=True)
        for f in _FILES:
            shutil.copyfile(entry / f, d / f)
        repo.create_campaign_document(doc_id, facts["campaign"], facts["file_name"], pages, sha)
        repo.enqueue(doc_id)
        loaded += 1
    return {"loaded": loaded, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle")
    args = ap.parse_args(argv)
    data = DataDir(os.environ.get("SINDRI_DATA_DIR") or "/data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    try:
        out = load_bundle(args.bundle, data, Repo(conn))
    except BundleError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"loaded {out['loaded']}, already present {out['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `app/campaign/pull.py`:

```python
"""Pack a campaign's review records for the operator (campaign design §4).

    docker compose exec sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar

The tar holds, per drawing, the review dir (header, proposal, events,
sealed/) under <campaign>/<doc_hash>/, plus <campaign>/index.json with the
index facts grading needs (state, final revision). It is CLIENT DATA under
CLAUDE.md §1: it goes into the protected root, and an agent only ever sees
what `runner score --reviewer-grade` (plan C2) summarises from it. Safe to
run mid-campaign; unapproved drawings are simply unsealed."""
import argparse
import io
import json
import os
import tarfile
from pathlib import Path

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.review.store import ReviewStore


def pull_campaign(campaign: str, repo: Repo, store: ReviewStore, out_path) -> int:
    docs = [d for d in repo.list_documents() if d["campaign"] == campaign]
    index = []
    with tarfile.open(out_path, "w") as tar:
        for d in docs:
            review_dir = store.root / d["id"]
            header = json.loads((review_dir / "header.json").read_text(encoding="utf-8"))
            h = header.get("campaign", {}).get("doc_hash") or d["id"][:8]
            tar.add(review_dir, arcname=f"{campaign}/{h}")
            index.append({"doc_hash": h, "group": header.get("campaign", {}).get("group"),
                          "state": d["state"], "final_revision": d["final_revision"],
                          "sealed_seq": d["sealed_seq"]})
        blob = json.dumps({"campaign": campaign, "documents": index}, indent=1).encode()
        info = tarfile.TarInfo(f"{campaign}/index.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    return len(docs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaign")
    ap.add_argument("out")
    args = ap.parse_args(argv)
    data = DataDir(os.environ.get("SINDRI_DATA_DIR") or "/data")
    store = ReviewStore(Path(os.environ.get("SINDRI_REVIEW_DIR") or data.root / "reviews"))
    conn = db.connect(data.db_path)
    try:
        n = pull_campaign(args.campaign, Repo(conn), store, args.out)
    finally:
        conn.close()
    print(f"packed {n} drawings of {args.campaign}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/campaign -q`
Expected: all pass. If `ReviewStore(...)` needs its root created first, follow `tests/batch/test_worker.py`'s `Harness`.

- [ ] **Step 5: Check the product boundary still holds**

Run: `python -m pytest tests/review/test_product_boundary.py -q`
Expected: PASS. `app/campaign` must not import `app.eval`; if the boundary test lists packages explicitly, add `app/campaign` to it.

- [ ] **Step 6: Commit**

```bash
git add app/campaign/load.py app/campaign/pull.py tests/campaign/test_load_pull.py
git commit -m "campaign: in-container loader and records packer

Load validates the whole bundle before writing, is idempotent by bytes, and
queues each drawing for the running worker; pull packs review dirs plus an
index under salted doc hashes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 17: VPS deployment files and runbook

**Files:**
- Create: `docker-compose.vps.yml`, `deploy/Caddyfile.example`, `docs/deploy/vps.md`
- Test: a compose config check (no pytest)

- [ ] **Step 1: Write `docker-compose.vps.yml`**

```yaml
# Hetzner VPS override (campaign design §1). Use with:
#   docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build
# The app listens on localhost ONLY: Docker's published ports bypass ufw, so a
# plain "8000:8000" would serve the app to the internet WITHOUT the proxy's
# basic auth. Caddy (deploy/Caddyfile.example) is the only way in.
services:
  sindri:
    ports: !override
      - "127.0.0.1:8000:8000"
    volumes:
      - ./import:/import:ro
      - ./export:/export
    environment:
      - SINDRI_CAMPAIGN_ONLY=1
      # Campaign drawings are never exported, so retention would not purge
      # them anyway; the campaign must not depend on that reasoning.
      - SINDRI_RETENTION_DAYS=0
      - SINDRI_VERSION=${SINDRI_VERSION:-unknown}
    restart: unless-stopped
```

- [ ] **Step 2: Write `deploy/Caddyfile.example`**

```
# Copy to /etc/caddy/Caddyfile and replace the domain. Caddy gets the TLS
# certificate itself. Create the hash with:  caddy hash-password
review.example.com {
	basic_auth {
		# one line per reviewer account: <user> <bcrypt hash>
		reviewer $2a$14$REPLACE_WITH_CADDY_HASH_PASSWORD_OUTPUT
	}
	request_body {
		max_size 1MB
	}
	reverse_proxy 127.0.0.1:8000
}
```

- [ ] **Step 3: Write `docs/deploy/vps.md`**

```markdown
# Review campaign on the Hetzner VPS

Design: `docs/plans/2026-10-21-client-review-campaign-design.md`. Every step
that touches drawings or review records is the **operator's**: they are
client data (CLAUDE.md §1).

## Before the first upload (manual checklist)

- [ ] The client NDA allows hosting their drawings at Hetzner (EU).
- [ ] Hetzner snapshot backups are OFF for this server, or you accept that
      they hold client data until they expire.
- [ ] ufw allows 22, 80 and 443 only. The app itself is bound to 127.0.0.1.

## Install

1. Docker + compose plugin, and Caddy from its official apt repository.
2. `git clone` this repo to `~/sindri`, then `mkdir -p ~/sindri/data ~/sindri/import ~/sindri/export`.
3. `caddy hash-password` per reviewer account; fill `deploy/Caddyfile.example`
   into `/etc/caddy/Caddyfile`; `sudo systemctl reload caddy`.
4. `cd ~/sindri && SINDRI_VERSION=$(git rev-parse --short HEAD) docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build`
5. Check: `curl -s localhost:8000/api/health` shows `"campaign_only": true`;
   `curl -sI https://<domain>/` answers 401 without credentials.

## Run a campaign

1. Agent: selects drawings (`share_select ... --great 30 --problem 20`,
   train + dev digests, never test) and hands you the build command.
2. You: `python campaign_build.py --manifest ~/campaign-manifest.json --name pilot-1 --out ~/campaign-pilot-1`
3. You: `rsync -a ~/campaign-pilot-1/ vps:~/sindri/import/pilot-1/`
4. You: `ssh vps 'cd ~/sindri && docker compose exec sindri python -m app.campaign.load /import/pilot-1'`
5. Open the domain yourself, review one drawing end to end, then send the
   client the link and their credentials.
6. Progress at any time: the work list, or a pull (below).

## Get the records back

1. `ssh vps 'cd ~/sindri && docker compose exec sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar'`
2. `rsync vps:~/sindri/export/pilot-1.tar <protected root>/reviews/`
3. Grading: plan C2 (`runner score --reviewer-grade`).

## After the campaign

`docker compose down`, then delete `~/sindri/data`, `~/sindri/import` and
`~/sindri/export` on the VPS once the final pull is safely in the protected root.
```

- [ ] **Step 4: Validate the compose merge**

Run: `docker compose -f docker-compose.yml -f docker-compose.vps.yml config`
Expected: `sindri.ports` shows exactly `host_ip: 127.0.0.1`, `published: "8000"`; `SINDRI_CAMPAIGN_ONLY: "1"` is in the environment. If the local compose version rejects `!override` (it needs compose ≥ 2.24), replace the base file's ports with `"${SINDRI_BIND:-0.0.0.0}:8000:8000"` and set `SINDRI_BIND=127.0.0.1` in the override's `.env` instead; then re-run the check.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.vps.yml deploy/Caddyfile.example docs/deploy/vps.md
git commit -m "deploy: Hetzner VPS override, Caddy basic-auth example, runbook

The app binds to localhost only (Docker ports bypass ufw), runs
campaign-only with retention off; the runbook keeps every client-data step
with the operator.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 18: end-to-end check and docs

**Files:**
- Modify: `CLAUDE.md` (a short "REVIEW CAMPAIGN" paragraph in §2)

- [ ] **Step 1: Full suite and guard**

Run: `python -m pytest -q`
Expected: the previous count (1946 passed, 2 skipped) plus every test added here, 0 failed.

Run: `bash ~/.claude/hooks/test-sindri-guard.sh`
Expected: `32 passed, 0 failed`

- [ ] **Step 2: Real-browser smoke on a synthetic campaign** — the UI wiring of Tasks 11-13 is DOM-bound. Use a throwaway Playwright venv in the scratchpad (the repo has no browser driver; see memory note "suggestion tray"). Build a two-entry bundle from `tests/fixtures/sample.pdf` with Task 16's `bundle()` helper shape, load it into a scratch `SINDRI_DATA_DIR`, start `uvicorn app.main:app` with `SINDRI_CAMPAIGN_ONLY=1`, and check:
  - the work list shows no Import button and no bulk bar
  - the button reads "Approve drawing" and the bar shows "0 / 2 approved"
  - editing a value opens the reason popover; key `1` picks "Misread digit"; the event holds `reason: "misread_digit"`
  - dragging the anchor's `se` handle emits one `move_target`
  - typing a note and blurring emits `set_note`
  - Approve seals r1, downloads nothing, moves to the next drawing, and the counter reads "1 / 2 approved"
  - `GET /api/documents/<id>/export/xlsx` answers 403

  Read the journal through `/api/documents/<id>/open`'s `events`. This is synthetic data, so an agent may read it.

- [ ] **Step 3: Document it** — add a paragraph to CLAUDE.md §2 after the B4 paragraph:

```markdown
**REVIEW CAMPAIGN C1** (`docs/plans/2026-10-21-campaign-c1-review-plan.md`).
A campaign is a batch with `batch.campaign` set (migration 6): drawings
loaded from stored dumps by `app.campaign.load` (operator bundle from
`campaign_build.py`: today's policy re-applied, balloons re-placed), reviewed
in the hosted app, Approved (seal, no export), and packed by
`app.campaign.pull` into the protected root. `SINDRI_CAMPAIGN_ONLY=1` is an
allowlist of the routes a reviewer needs; export, re-extract and remove
refuse campaign drawings on every instance. The journal gained `move_target`
(anchor box), `set_note` and closed reasons on edit/delete (phase 2's keys,
`app/static/js/reasons.js`). Grading is plan C2. VPS runbook:
`docs/deploy/vps.md`.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe the review campaign (C1) in CLAUDE.md

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

> **Milestone: campaign deliverable** — state must be checkpointed here. C1 is done; next is plan C2 (grading + report), written while the client reviews.
