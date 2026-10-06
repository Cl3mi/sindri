# HITL Phase 1 — Capture + Finish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. At each **Milestone** marker, checkpoint `docs/plans/2026-10-07-hitl-phase1-capture-plan.state.md` (context-management:milestone-checkpoints) before continuing.

**Goal:** Every reviewed drawing leaves a durable, server-held record — what the system proposed, the inspector's net operations, and a sealed final — and the inspector finishes a drawing with one gated "Finish & Export" action.

**Architecture:** A filesystem `ReviewStore` (`app/review/`) owns one directory per session under `SINDRI_REVIEW_DIR`: `header.json` and `proposal.json` written by the server at extract time, `events.jsonl` appended from the UI, `sealed/rN.json` at Finish. The UI emits one event per `state.js` operation through the existing `apply/undo/redo` choke point; a `journal.js` module numbers, queues and flushes them. Seal compacts the journal to net operations and checks that replaying them on the proposal reproduces the client's final rows.

**Tech Stack:** FastAPI + pydantic (server), vanilla ES modules (UI), pytest + `node --test` (run inside pytest by `tests/test_js_state.py`).

**Spec:** `docs/plans/2026-10-07-hitl-review-grading-design.md` §1-§2, §6 (Phase 1 of §8).

---

## Roadmap — one plan per phase, each written when the previous lands

| phase | plan | status |
|---|---|---|
| 1 Capture + Finish | this file | ready |
| 2 Forcing UX (queue, per-row accept, blind panel + audit sampling, reasons, unsure) | `docs/plans/<date>-hitl-phase2-forcing-plan.md` | written after phase 1 |
| 3 Grading + client report (`grade()`, `values.py`, report HTML) | `…-phase3-grading-plan.md` | after phase 2 |
| 4 Dev validation (`score --reviewer-grade`, registration doc, operator review) | `…-phase4-validation-plan.md` | after phase 3; gates any client ship |
| 5 Return path (bundle export, `ingest --review-bundle`, reviewer gold) | `…-phase5-bundle-plan.md` | after phase 4 |

Later phases depend on code this phase creates, so writing their exact code now would go stale.

## What Phase 1 deliberately leaves out

* Audit sampling, blind rows, reasons, unsure — Phase 2. `header.sampling` is `null` here, meaning "no audit design", never "rate 0" (CLAUDE.md §4: a default must not lie).
* Finish gating covers flagged rows only; Phase 2 extends `progress()` with blind rows.
* Writer tokens are issued once per extraction. The UI has no way to open an existing session in a second tab today, so there is no re-claim endpoint; the server still rejects a wrong token, which is what Phase 2+ will rely on.

## File structure

| file | responsibility |
|---|---|
| `app/review/__init__.py` | package marker (product code; must NOT import `app.eval`) |
| `app/review/journal.py` | pure: `EVENT_TYPES`, `validate_event`, `contiguous_seq`, `net_events` |
| `app/review/replay.py` | pure: `replay(proposal_rows, events)`, `mismatches(replayed, final_rows, reviewed_ids)` |
| `app/review/header.py` | `build_header(pdf_path, consent)` — pipeline config + drawing hash |
| `app/review/store.py` | `ReviewStore` filesystem layout, `create` / `append` / `seal`, typed errors |
| `app/main.py` | wire store from env; proposal at extract; `/events`, `/seal`; health flag |
| `app/static/js/state.js` | `event()` descriptor on each op; `op` bus emission; `progress()`, `canFinish()` |
| `app/static/js/journal.js` | numbering, retract mapping, ack tracking, single-flight flush |
| `app/static/js/api.js` | `postEvents`, `sealSession`, `beaconEvents` |
| `app/static/js/main.js` | journal wiring (debounce, retry, pagehide); Finish flow; logging pill |
| `app/static/js/table.js` | progress bar text from `progress()` |
| `app/static/index.html` | Finish button replaces the two export buttons; logging pill |
| `docker-compose.yml` | `SINDRI_REVIEW_DIR=/data/reviews` |
| tests | `tests/review/test_journal.py`, `test_replay.py`, `test_header.py`, `test_store.py`; `tests/test_api_review.py`; `tests/js/journal.test.mjs`, `tests/js/progress.test.mjs` |

Run the whole suite with `python -m pytest -q`. Baseline before starting: **1293 passed, 2 skipped**.

---

## Milestone group 1 — the store (pure Python, no web)

### Task 1: journal primitives

**Files:**
- Create: `app/review/__init__.py`, `app/review/journal.py`
- Test: `tests/review/__init__.py`, `tests/review/test_journal.py`

- [ ] **Step 1: Write the failing tests**

`tests/review/__init__.py`: empty file.

`tests/review/test_journal.py`:

```python
"""The review journal's pure rules (docs/plans/2026-10-07-hitl-review-grading-design.md §2).

The journal is what grading replays, so its invariants are the grading's:
an event is numbered, typed and self-contained; an undo is a retraction of
the event it undid; and only the NET operations survive a seal."""
import pytest

from app.review.journal import (
    EVENT_TYPES, JournalError, contiguous_seq, net_events, validate_event)


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, **kw}


def test_vocabulary_is_exactly_phase_one_operations():
    assert EVENT_TYPES == {
        "add_row", "delete_row", "move_row", "edit_cell",
        "accept", "unaccept", "confirm_suggestions", "retract"}


@pytest.mark.parametrize("bad", [
    {"type": "accept", "ids": []},                    # no seq
    {"seq": 0, "type": "accept", "ids": []},          # seq starts at 1
    {"seq": "3", "type": "accept", "ids": []},        # seq must be an int
    {"seq": True, "type": "accept", "ids": []},       # bool is not a seq
    {"seq": 1, "type": "teleport"},                   # unknown type
    "not a dict",
])
def test_validate_event_rejects_malformed(bad):
    with pytest.raises(JournalError):
        validate_event(bad)


def test_validate_event_accepts_a_wellformed_event():
    validate_event(ev(1, "edit_cell", id="a", field="nominal", old="1", new="2"))


def test_contiguous_seq_stops_at_the_first_gap():
    assert contiguous_seq({1, 2, 3, 5}) == 3
    assert contiguous_seq(set()) == 0
    # A seal compacts retracted pairs away, leaving holes BELOW the sealed
    # point; counting must resume from there or the next seal sees a gap.
    assert contiguous_seq({7, 8}, start=6) == 8
    assert contiguous_seq({8}, start=6) == 6


def test_net_events_removes_each_retraction_and_its_target():
    events = [ev(1, "accept", ids=["a"]), ev(2, "edit_cell", id="a", field="nominal", old="1", new="2"),
              ev(3, "retract", target=2), ev(4, "delete_row", id="b")]
    assert [e["seq"] for e in net_events(events)] == [1, 4]


def test_net_events_orders_by_seq_whatever_the_arrival_order():
    events = [ev(2, "delete_row", id="b"), ev(1, "accept", ids=["a"])]
    assert [e["seq"] for e in net_events(events)] == [1, 2]


def test_redo_after_undo_survives_as_a_fresh_event():
    events = [ev(1, "delete_row", id="b"), ev(2, "retract", target=1),
              ev(3, "delete_row", id="b")]
    assert [e["seq"] for e in net_events(events)] == [3]


@pytest.mark.parametrize("events", [
    [ev(1, "retract", target=9)],                                       # unknown target
    [ev(1, "accept", ids=["a"]), ev(2, "retract", target=1),
     ev(3, "retract", target=1)],                                       # retracted twice
])
def test_net_events_refuses_an_inconsistent_journal(events):
    with pytest.raises(JournalError):
        net_events(events)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/review/test_journal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.review'`.

- [ ] **Step 3: Implement**

`app/review/__init__.py`:

```python
"""Review records: what the system proposed, what the inspector did, and the
sealed final (docs/plans/2026-10-07-hitl-review-grading-design.md).

Product code -- it ships to the client. It must never import app.eval, which
holds gold-handling code that does not."""
```

`app/review/journal.py`:

```python
"""Pure rules for the review journal (design §2).

An event is one reviewer operation, numbered by the UI (`seq` from 1) and
self-contained enough to replay without the browser. An undo is not a deletion
but a `retract` of the event it undid, so the server can stay append-only and
a crashed tab loses nothing; only at seal are retracted pairs compacted away,
which is the "net operations only" retention the operator chose."""
from typing import Iterable, List, Set

EVENT_TYPES = frozenset({
    "add_row", "delete_row", "move_row", "edit_cell",
    "accept", "unaccept", "confirm_suggestions", "retract",
})


class JournalError(ValueError):
    """A journal that cannot be trusted for grading."""


def validate_event(e) -> None:
    if not isinstance(e, dict):
        raise JournalError("event is not an object")
    seq = e.get("seq")
    # bool is an int subclass; True would silently become seq 1.
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise JournalError(f"bad seq {seq!r}")
    if e.get("type") not in EVENT_TYPES:
        raise JournalError(f"unknown event type {e.get('type')!r}")


def contiguous_seq(seqs: Set[int], start: int = 0) -> int:
    """Highest n such that every seq in (start, n] is present. `start` is the
    last sealed seq: compaction leaves holes below it that are not gaps."""
    n = start
    while n + 1 in seqs:
        n += 1
    return n


def net_events(events: Iterable[dict]) -> List[dict]:
    """The operations that survive their undos, in seq order. Undo is LIFO, so
    removing a retracted event and its retraction leaves a sequence whose
    replay equals the state the reviewer actually ended in."""
    live = {}
    for e in sorted(events, key=lambda x: x["seq"]):
        if e["type"] == "retract":
            target = e.get("target")
            if target not in live:
                raise JournalError(f"retract of unknown or already retracted seq {target!r}")
            del live[target]
        else:
            live[e["seq"]] = e
    return list(live.values())
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/review/test_journal.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/review/__init__.py app/review/journal.py tests/review/__init__.py tests/review/test_journal.py
git commit -m "review: journal primitives — typed events, retractions, net ops"
```

(Body: why — undo as retraction keeps the store append-only; trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` on every commit in this plan.)

### Task 2: replay and the seal-time cross-check

**Files:**
- Create: `app/review/replay.py`
- Test: `tests/review/test_replay.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Replay is the grading's ground truth for what the reviewer did (design §3,
the replay identity). If replaying the net journal on the server-held proposal
does not reproduce the rows the browser exported, the log is missing something
and no grade built on it can be trusted -- so seal records the mismatch."""
import pytest

from app.review.journal import JournalError
from app.review.replay import mismatches, replay


def row(id_, **kw):
    base = {"id": id_, "char_type": "Distance", "nominal": "10", "upper_tol": "",
            "lower_tol": "", "balloon_xy": [5.0, 5.0], "suggested": False,
            "needs_review": False}
    return {**base, **kw}


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, **kw}


def test_replay_applies_every_phase_one_operation():
    proposal = [row("a"), row("b"), row("s", suggested=True)]
    events = [
        ev(1, "edit_cell", id="a", field="nominal", old="10", new="12"),
        ev(2, "move_row", id="a", xy=[7, 8]),
        ev(3, "delete_row", id="b"),
        ev(4, "add_row", row=row("m", source="manual", nominal="3")),
        ev(5, "confirm_suggestions", ids=["s"]),
        ev(6, "accept", ids=["a", "m"]),
        ev(7, "unaccept", ids=["m"]),
    ]
    out = replay(proposal, events)
    assert set(out) == {"a", "s", "m"}
    assert out["a"]["nominal"] == "12" and out["a"]["balloon_xy"] == [7, 8]
    assert out["a"]["reviewed"] is True and out["m"]["reviewed"] is False
    assert out["s"]["suggested"] is False and out["s"]["reviewed"] is True


def test_confirm_ignores_rows_that_are_not_suggestions():
    # Mirrors opConfirmSuggestions in state.js: only a suggestion is confirmed.
    out = replay([row("a")], [ev(1, "confirm_suggestions", ids=["a"])])
    assert out["a"]["reviewed"] is False


def test_replay_refuses_a_retraction_it_was_not_given_net():
    with pytest.raises(JournalError):
        replay([row("a")], [ev(1, "retract", target=1)])


def test_mismatches_is_empty_when_the_final_matches_the_replay():
    replayed = replay([row("a")], [ev(1, "accept", ids=["a"])])
    assert mismatches(replayed, [row("a", balloon_xy=(5.0, 5.0))], ["a"]) == []


def test_mismatches_names_every_kind_of_disagreement():
    replayed = replay([row("a"), row("b"), row("c"), row("d")], [])
    final = [row("a", nominal="99"),          # value differs
             row("b", balloon_xy=[1, 1]),     # position differs
             row("c"),                        # reviewed state differs (below)
             row("x")]                        # row the journal never added; d missing
    assert mismatches(replayed, final, ["c"]) == ["a", "b", "c", "d", "x"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/review/test_replay.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.review.replay'`.

- [ ] **Step 3: Implement** `app/review/replay.py`

```python
"""Replay net review operations on the proposal (design §2, §3).

The semantics mirror app/static/js/state.js operation by operation; a change
to an op there must change its branch here, or seal starts reporting replay
mismatches on healthy sessions."""
from typing import Dict, Iterable, List

from app.review.journal import JournalError

VALUE_FIELDS = ("char_type", "nominal", "upper_tol", "lower_tol")


def replay(proposal_rows: Iterable[dict], events: Iterable[dict]) -> Dict[str, dict]:
    rows = {r["id"]: {**r, "reviewed": False} for r in proposal_rows}
    for e in events:
        t = e["type"]
        if t == "add_row":
            r = e["row"]
            rows[r["id"]] = {**r, "reviewed": bool(r.get("reviewed", False))}
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
        else:
            # A retract here means the caller skipped net_events(): replaying
            # it would grade an operation the reviewer undid.
            raise JournalError(f"cannot replay event type {t!r}")
    return rows


def _xy(r):
    xy = r.get("balloon_xy")
    return None if xy is None else tuple(round(float(v), 3) for v in xy)


def mismatches(replayed: Dict[str, dict], final_rows: Iterable[dict],
               reviewed_ids: Iterable[str]) -> List[str]:
    """Ids whose replayed state disagrees with the exported final. `pos` is
    not compared: renumbering is derived, not an operation."""
    final = {r["id"]: r for r in final_rows}
    reviewed = set(reviewed_ids)
    bad = set(replayed) ^ set(final)
    for i in set(replayed) & set(final):
        a, b = replayed[i], final[i]
        if (any((a.get(f) or "") != (b.get(f) or "") for f in VALUE_FIELDS)
                or bool(a.get("suggested")) != bool(b.get("suggested"))
                or _xy(a) != _xy(b)
                or a["reviewed"] != (i in reviewed)):
            bad.add(i)
    return sorted(bad)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/review/test_replay.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/review/replay.py tests/review/test_replay.py
git commit -m "review: replay net operations and name every final-row mismatch"
```

### Task 3: session header

**Files:**
- Create: `app/review/header.py`
- Test: `tests/review/test_header.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The header says under which configuration a session was proposed, so a
grade is never pooled across policies without anyone knowing (the r3/r4/r5
lesson: compare only like with like)."""
import hashlib

from app.pipeline import policy_rules as pr
from app.pipeline import review as pipeline_review
from app.review.header import REVIEW_SCHEMA_VERSION, build_header


def test_header_records_drawing_and_pipeline_config(sample_pdf, monkeypatch):
    monkeypatch.setenv("SINDRI_VERSION", "abc123")
    h = build_header(sample_pdf, consent=True)
    assert h["review_schema_version"] == REVIEW_SCHEMA_VERSION == 1
    assert h["app_version"] == "abc123"
    assert h["drawing_sha256"] == hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    assert h["pages"] >= 1
    assert h["consent"] is True
    assert h["pipeline"]["flag_rules"] == list(pr.ACTIVE_FLAG_RULES)
    assert h["pipeline"]["drop_stages"] == [list(s) for s in pr.ACTIVE_DROP_STAGES]
    assert h["pipeline"]["low_conf"] == pipeline_review.LOW_CONF
    assert "crop_knobs" in h["pipeline"]
    assert h["created_at"].endswith("+00:00")


def test_sampling_is_none_not_a_rate_of_zero(sample_pdf):
    # Phase 1 has no audit design. None says so; 0.0 would claim a design
    # that sampled nothing, and a grade would read "0 audited" as a result.
    assert build_header(sample_pdf, consent=False)["sampling"] is None


def test_app_version_defaults_to_unknown(sample_pdf, monkeypatch):
    monkeypatch.delenv("SINDRI_VERSION", raising=False)
    assert build_header(sample_pdf, consent=False)["app_version"] == "unknown"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/review/test_header.py -q` → FAIL, module missing.

- [ ] **Step 3: Implement** `app/review/header.py`

```python
"""header.json: the configuration a session was proposed under (design §2)."""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

from app.pipeline import policy_rules as pr
from app.pipeline import review as pipeline_review
from app.pipeline.extract import active_crop_knobs

# The REVIEW record's schema -- unrelated to app.eval's SCHEMA_VERSION.
REVIEW_SCHEMA_VERSION = 1


def build_header(pdf_path: Path, consent: bool) -> dict:
    pdf_path = Path(pdf_path)
    with fitz.open(pdf_path) as doc:
        pages = doc.page_count
    return {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        # Container builds have no .git (CLAUDE.md §5), so the version must be
        # injected; "unknown" is honest where a guessed sha would not be.
        "app_version": os.environ.get("SINDRI_VERSION", "unknown"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "drawing_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "pages": pages,
        "consent": bool(consent),
        # Set by the audit sampler from Phase 2 on. None means NO audit design,
        # never a design with rate 0 (CLAUDE.md §4).
        "sampling": None,
        "pipeline": {
            "flag_rules": list(pr.ACTIVE_FLAG_RULES),
            "drop_stages": [list(s) for s in pr.ACTIVE_DROP_STAGES],
            "crop_knobs": active_crop_knobs(),
            "low_conf": pipeline_review.LOW_CONF,
        },
    }
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/review/test_header.py -q`.

- [ ] **Step 5: Commit**

```bash
git add app/review/header.py tests/review/test_header.py
git commit -m "review: session header records drawing hash and pipeline config"
```

### Task 4: `ReviewStore`

**Files:**
- Create: `app/review/store.py`
- Test: `tests/review/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The durable review record (design §2). Every refusal here exists so that an
incomplete or foreign log is never graded as if it were whole."""
import json

import pytest

from app.review.store import (
    JournalGap, JournalInvalid, ReviewStore, StaleWriter, UnknownSession)

SID = "a" * 32


def row(id_, **kw):
    return {"id": id_, "char_type": "Distance", "nominal": "10", "upper_tol": "",
            "lower_tol": "", "balloon_xy": [5.0, 5.0], "suggested": False,
            "needs_review": True, **kw}


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, **kw}


@pytest.fixture
def store(tmp_path):
    return ReviewStore(tmp_path / "reviews")


@pytest.fixture
def writer(store):
    return store.create(SID, {"h": 1}, {"rows": [row("a"), row("b")]})


def test_create_writes_header_and_proposal_and_returns_a_token(store, writer):
    d = store.root / SID
    assert json.loads((d / "header.json").read_text()) == {"h": 1}
    assert [r["id"] for r in json.loads((d / "proposal.json").read_text())["rows"]] == ["a", "b"]
    assert len(writer) >= 32


def test_append_is_idempotent_and_reports_the_contiguous_seq(store, writer):
    assert store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(3, "delete_row", id="b")]) == 1
    # A resend of seq 1 is ignored; seq 2 closes the gap.
    assert store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "unaccept", ids=["a"])]) == 3
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert sorted(json.loads(l)["seq"] for l in lines) == [1, 2, 3]


def test_append_refuses_a_stale_writer(store, writer):
    with pytest.raises(StaleWriter):
        store.append(SID, "not-the-token", [ev(1, "accept", ids=["a"])])


def test_append_refuses_an_unknown_session(store):
    with pytest.raises(UnknownSession):
        store.append("b" * 32, "t", [])


def test_append_refuses_a_malformed_event_and_writes_nothing(store, writer):
    with pytest.raises(JournalInvalid):
        store.append(SID, writer, [ev(1, "accept", ids=["a"]), {"seq": 2, "type": "teleport"}])
    assert not (store.root / SID / "events.jsonl").exists()


def test_seal_refuses_a_gap(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(3, "delete_row", id="b")])
    with pytest.raises(JournalGap):
        store.seal(SID, writer, 3, [row("a")], ["a"])


def test_seal_refuses_events_beyond_the_final_seq(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"]), ev(2, "delete_row", id="b")])
    with pytest.raises(JournalInvalid):
        store.seal(SID, writer, 1, [row("a"), row("b")], ["a"])


def test_seal_writes_a_revision_compacts_the_journal_and_checks_replay(store, writer):
    store.append(SID, writer, [
        ev(1, "accept", ids=["a"]),
        ev(2, "delete_row", id="b"),
        ev(3, "retract", target=2),        # the reviewer undid the delete
    ])
    out = store.seal(SID, writer, 3, [row("a"), row("b")], ["a"])
    assert out == {"revision": 1, "replay_ok": True}
    sealed = json.loads((store.root / SID / "sealed" / "r1.json").read_text())
    assert sealed["final_seq"] == 3
    assert [e["seq"] for e in sealed["net_events"]] == [1]
    assert sealed["replay_mismatch_ids"] == []
    assert sealed["reviewed_ids"] == ["a"]
    # Net operations only are retained (operator decision): the raw journal
    # is rewritten to its net form.
    lines = (store.root / SID / "events.jsonl").read_text().splitlines()
    assert [json.loads(l)["seq"] for l in lines] == [1]


def test_seal_records_a_replay_mismatch_instead_of_hiding_it(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    out = store.seal(SID, writer, 1, [row("a", nominal="99"), row("b")], ["a"])
    assert out == {"revision": 1, "replay_ok": False}
    sealed = json.loads((store.root / SID / "sealed" / "r1.json").read_text())
    assert sealed["replay_mismatch_ids"] == ["a"]


def test_a_second_seal_continues_after_compaction_holes(store, writer):
    store.append(SID, writer, [ev(1, "delete_row", id="b"), ev(2, "retract", target=1)])
    store.seal(SID, writer, 2, [row("a"), row("b")], [])
    # seqs 1-2 are gone after compaction; seq 3 must still count as contiguous.
    assert store.append(SID, writer, [ev(3, "accept", ids=["a"])]) == 3
    assert store.seal(SID, writer, 3, [row("a"), row("b")], ["a"])["revision"] == 2


def test_create_again_before_any_seal_starts_over(store, writer):
    store.append(SID, writer, [ev(1, "accept", ids=["a"])])
    w2 = store.create(SID, {"h": 2}, {"rows": [row("a")]})
    assert w2 != writer
    assert not (store.root / SID / "events.jsonl").exists()


def test_create_refuses_to_overwrite_a_sealed_session(store, writer):
    store.seal(SID, writer, 0, [row("a"), row("b")], [])
    with pytest.raises(JournalInvalid):
        store.create(SID, {"h": 2}, {"rows": []})
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/review/test_store.py -q` → module missing.

- [ ] **Step 3: Implement** `app/review/store.py`

```python
"""Durable, server-held review records (design §2).

    <root>/<session_id>/header.json      server, at extract
                        proposal.json    server, at extract -- never client-supplied
                        writer           the session's single-writer token
                        events.jsonl     appended from the UI; net form after a seal
                        sealed/rN.json   one per Finish

Everything here is CLIENT DATA under the corpus rules (CLAUDE.md §1)."""
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from app.review.journal import (
    JournalError, contiguous_seq, net_events, validate_event)
from app.review.replay import mismatches, replay


class ReviewError(Exception):
    status = 400


class UnknownSession(ReviewError):
    status = 404


class StaleWriter(ReviewError):
    status = 409


class JournalGap(ReviewError):
    status = 409


class JournalInvalid(ReviewError):
    status = 422


def _write_json(path: Path, obj) -> None:
    # Write-then-rename, so a crash never leaves a half-written record that a
    # later grade would read as complete.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


class ReviewStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ----- layout ------------------------------------------------------
    def _dir(self, sid: str) -> Path:
        d = self.root / sid
        if not (d / "proposal.json").is_file():
            raise UnknownSession(f"no review record for session {sid[:8]}")
        return d

    def _check_writer(self, d: Path, writer: str) -> None:
        if not secrets.compare_digest((d / "writer").read_text(), writer or ""):
            raise StaleWriter("this drawing was opened elsewhere; reload to continue")

    def _events(self, d: Path) -> List[dict]:
        f = d / "events.jsonl"
        if not f.is_file():
            return []
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]

    def _sealed_through(self, d: Path) -> int:
        seals = sorted((d / "sealed").glob("r*.json")) if (d / "sealed").is_dir() else []
        return max((json.loads(p.read_text())["final_seq"] for p in seals), default=0)

    def _revisions(self, d: Path) -> int:
        return len(list((d / "sealed").glob("r*.json"))) if (d / "sealed").is_dir() else 0

    # ----- operations --------------------------------------------------
    def create(self, sid: str, header: dict, proposal: dict) -> str:
        d = self.root / sid
        if (d / "sealed").is_dir():
            raise JournalInvalid("session already sealed; a re-extraction needs a new session")
        if d.exists():
            # Re-extraction of an unsealed session: the old proposal no longer
            # describes what the reviewer sees, so its journal must go with it.
            shutil.rmtree(d)
        d.mkdir(parents=True)
        _write_json(d / "header.json", header)
        _write_json(d / "proposal.json", proposal)
        writer = secrets.token_hex(16)
        (d / "writer").write_text(writer)
        return writer

    def append(self, sid: str, writer: str, events: Iterable[dict]) -> int:
        d = self._dir(sid)
        self._check_writer(d, writer)
        events = list(events)
        try:
            for e in events:
                validate_event(e)
        except JournalError as exc:
            raise JournalInvalid(str(exc)) from exc
        through = self._sealed_through(d)
        have = {e["seq"] for e in self._events(d)}
        new = [e for e in events if e["seq"] > through and e["seq"] not in have]
        if new:
            with (d / "events.jsonl").open("a") as f:
                for e in new:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
                    have.add(e["seq"])
        return contiguous_seq(have, start=through)

    def seal(self, sid: str, writer: str, final_seq: int,
             rows: List[dict], reviewed_ids: List[str]) -> dict:
        d = self._dir(sid)
        self._check_writer(d, writer)
        events = self._events(d)
        seqs = {e["seq"] for e in events}
        through = self._sealed_through(d)
        if contiguous_seq(seqs, start=through) < final_seq:
            raise JournalGap("the review log is missing events; Finish again once it has caught up")
        if any(s > final_seq for s in seqs):
            raise JournalInvalid("the review log holds events after the final one")
        try:
            net = net_events(events)
            proposal = json.loads((d / "proposal.json").read_text())["rows"]
            # Replay from the PROPOSAL through every surviving event, including
            # those kept from earlier seals, so revision N describes the whole
            # session, not only what changed since N-1.
            bad = mismatches(replay(proposal, net), rows, reviewed_ids)
        except JournalError as exc:
            raise JournalInvalid(str(exc)) from exc
        rev = self._revisions(d) + 1
        (d / "sealed").mkdir(exist_ok=True)
        _write_json(d / "sealed" / f"r{rev}.json", {
            "revision": rev,
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "final_seq": final_seq,
            "rows": rows,
            "reviewed_ids": sorted(reviewed_ids),
            "net_events": net,
            "replay_mismatch_ids": bad,
        })
        tmp = d / "events.jsonl.tmp"
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in net))
        os.replace(tmp, d / "events.jsonl")
        return {"revision": rev, "replay_ok": not bad}
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/review -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/review/store.py tests/review/test_store.py
git commit -m "review: durable store — idempotent append, gap-refusing seal, net compaction"
```

> **Milestone: store** — state must be checkpointed here before continuing.

---

## Milestone group 2 — the API

### Task 5: store from env, proposal at extract, health flag

**Files:**
- Modify: `app/main.py` (imports; after `_BACKEND = get_backend()`; `health()`; `extract_endpoint.worker`)
- Test: `tests/test_api_review.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Review capture through the API (design §2, §6)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.detect import Detection
from app.review.store import ReviewStore
from tests.conftest import StubVLMBackend
from tests.test_api import parse_sse, save_pdf

client = TestClient(app)


@pytest.fixture
def stub_backend(monkeypatch):
    backend = StubVLMBackend(
        detections=[Detection((40, 40, 120, 70), "dimension", 0.9)],
        text="1,2 +0,1 -0,1")
    monkeypatch.setattr("app.main._BACKEND", backend)
    return backend


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ReviewStore(tmp_path / "reviews")
    monkeypatch.setattr("app.main._REVIEW_STORE", s)
    return s


def extract(sample_pdf):
    meta = save_pdf(client, sample_pdf)
    _p, result, error = parse_sse(client.post(f"/api/extract/{meta['session_id']}"))
    assert error is None
    return result


def test_extract_writes_the_proposal_server_side(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    d = store.root / result["session_id"]
    proposal = json.loads((d / "proposal.json").read_text())
    assert [r["id"] for r in proposal["rows"]] == [r["id"] for r in result["rows"]]
    assert json.loads((d / "header.json").read_text())["review_schema_version"] == 1
    assert result["writer"] and result["review_logging"] is True


def test_extract_without_a_store_still_works_and_says_logging_is_off(
        sample_pdf, stub_backend, monkeypatch):
    monkeypatch.setattr("app.main._REVIEW_STORE", None)
    result = extract(sample_pdf)
    assert result["writer"] is None and result["review_logging"] is False


def test_health_reports_review_logging(store):
    assert client.get("/api/health").json()["review_logging"] is True


def test_consent_comes_from_the_environment(sample_pdf, stub_backend, store, monkeypatch):
    monkeypatch.setenv("SINDRI_REVIEW_CONSENT", "1")
    result = extract(sample_pdf)
    header = json.loads((store.root / result["session_id"] / "header.json").read_text())
    assert header["consent"] is True
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_api_review.py -q` → FAIL (`app.main` has no `_REVIEW_STORE`).

- [ ] **Step 3: Implement** in `app/main.py`

Add `import os` to the stdlib imports and, after the pipeline imports:

```python
from app.review.header import build_header
from app.review.store import ReviewError, ReviewStore
```

After `_BACKEND = get_backend()`:

```python
def _review_store_from_env():
    """The durable review store, or None when SINDRI_REVIEW_DIR is unset or
    unwritable. None is not an error: the app still reviews and exports, it
    just records nothing -- and a session recorded nowhere is never graded,
    rather than graded wrong (design §6)."""
    root = os.environ.get("SINDRI_REVIEW_DIR")
    if not root:
        return None
    try:
        return ReviewStore(Path(root))
    except OSError:
        return None


_REVIEW_STORE = _review_store_from_env()


def _review_consent() -> bool:
    return os.environ.get("SINDRI_REVIEW_CONSENT", "").lower() in ("1", "true", "yes")
```

In `health()`, before `return status`:

```python
    status["review_logging"] = _REVIEW_STORE is not None
```

In `extract_endpoint.worker`, replace the `events.put(("result", {...}))` block with:

```python
            rows = [r.model_dump(mode="json") for r in
                    [*result.characteristics, *result.suggestions]]
            writer = None
            if _REVIEW_STORE is not None:
                try:
                    writer = _REVIEW_STORE.create(
                        session_id, build_header(pdf_path, _review_consent()),
                        {"rows": rows})
                except (ReviewError, OSError):
                    writer = None   # review proceeds unlogged; the UI says so
            events.put(("result", {
                "session_id": session_id,
                "image_url": f"/api/image/{session_id}",
                # Suggestions ride in the same list, flagged, so the reviewer
                # edits them with the existing table; the client keeps them out
                # of exports and so does _exportable below.
                "rows": rows,
                "notes": result.notes.model_dump() if result.notes is not None else None,
                "marks": result.marks.model_dump() if result.marks is not None else None,
                "title_block": [t.model_dump() for t in result.title_block],
                "writer": writer,
                "review_logging": writer is not None,
            }))
```

(`mode="json"` turns tuples into lists, so the proposal on disk and the rows the UI receives are the same JSON.)

- [ ] **Step 4: Run** `python -m pytest tests/test_api_review.py tests/test_api.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api_review.py
git commit -m "api: write the review proposal server-side at extract; health reports logging"
```

### Task 6: `POST /api/session/{id}/events` and `/seal`

**Files:**
- Modify: `app/main.py` (new request models next to `ReadRegionRequest`; endpoints after `delete_session`)
- Test: `tests/test_api_review.py` (append)

- [ ] **Step 1: Append failing tests**

```python
def test_events_round_trip_and_seal(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    sid, writer, rows = result["session_id"], result["writer"], result["rows"]
    rid = rows[0]["id"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 1, "type": "edit_cell", "id": rid, "field": "nominal",
         "old": rows[0]["nominal"], "new": "7"},
        {"seq": 2, "type": "accept", "ids": [rid]}]})
    assert r.status_code == 200 and r.json() == {"contiguous": 2}
    final = [{**rows[0], "nominal": "7"}, *rows[1:]]
    r = client.post(f"/api/session/{sid}/seal", json={
        "writer": writer, "final_seq": 2, "rows": final, "reviewed_ids": [rid]})
    assert r.status_code == 200
    assert r.json() == {"revision": 1, "replay_ok": True, "review_logging": True}


def test_events_with_a_stale_writer_are_409(sample_pdf, stub_backend, store):
    sid = extract(sample_pdf)["session_id"]
    r = client.post(f"/api/session/{sid}/events", json={"writer": "x", "events": []})
    assert r.status_code == 409
    assert "opened elsewhere" in r.json()["detail"]


def test_seal_with_a_gap_is_409(sample_pdf, stub_backend, store):
    result = extract(sample_pdf)
    sid, writer = result["session_id"], result["writer"]
    client.post(f"/api/session/{sid}/events", json={"writer": writer, "events": [
        {"seq": 2, "type": "accept", "ids": []}]})
    r = client.post(f"/api/session/{sid}/seal", json={
        "writer": writer, "final_seq": 2, "rows": result["rows"], "reviewed_ids": []})
    assert r.status_code == 409


def test_malformed_session_id_is_404(store):
    r = client.post("/api/session/not-a-hex-id/events", json={"writer": "x", "events": []})
    assert r.status_code == 404


def test_seal_without_a_store_reports_logging_off(sample_pdf, stub_backend, monkeypatch):
    monkeypatch.setattr("app.main._REVIEW_STORE", None)
    result = extract(sample_pdf)
    r = client.post(f"/api/session/{result['session_id']}/seal", json={
        "writer": "", "final_seq": 0, "rows": result["rows"], "reviewed_ids": []})
    assert r.status_code == 200
    assert r.json() == {"revision": None, "review_logging": False}
```

- [ ] **Step 2: Run to verify failure** — the new tests fail with 404/405 (routes missing).

- [ ] **Step 3: Implement** in `app/main.py`

Request models, after `ReadRegionRequest`:

```python
class EventsRequest(BaseModel):
    writer: str
    events: List[dict]


class SealRequest(BaseModel):
    writer: str
    final_seq: int
    rows: List[Characteristic]       # every row incl. suggestions; extras like `reviewed` are ignored
    reviewed_ids: List[str] = []
```

Endpoints, after `delete_session`:

```python
@app.post("/api/session/{session_id}/events")
def post_events(session_id: str, req: EventsRequest):
    """Append reviewer operations. Idempotent by seq, so the UI simply resends
    everything the reply says is not yet held contiguously."""
    _session_dir(session_id)         # rejects a malformed id before any path is built
    if _REVIEW_STORE is None:
        return {"contiguous": None, "review_logging": False}
    try:
        return {"contiguous": _REVIEW_STORE.append(session_id, req.writer, req.events)}
    except ReviewError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@app.post("/api/session/{session_id}/seal")
def seal_session(session_id: str, req: SealRequest):
    """Seal a review revision at the client's final seq. Refuses on a gap: an
    incomplete log must never be graded (design §2)."""
    _session_dir(session_id)
    if _REVIEW_STORE is None:
        return {"revision": None, "review_logging": False}
    try:
        out = _REVIEW_STORE.seal(
            session_id, req.writer, req.final_seq,
            [r.model_dump(mode="json") for r in req.rows], req.reviewed_ids)
    except ReviewError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {**out, "review_logging": True}
```

- [ ] **Step 4: Run** `python -m pytest tests/test_api_review.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api_review.py
git commit -m "api: events and seal endpoints for the review journal"
```

> **Milestone: api** — state must be checkpointed here before continuing.

---

## Milestone group 3 — the UI

### Task 7: every operation describes itself; the bus carries do/undo

**Files:**
- Modify: `app/static/js/state.js` (`apply`, `undo`, `redo`, every `op*` factory)
- Test: `tests/js/journal.test.mjs` (first half)

- [ ] **Step 1: Write the failing test** `tests/js/journal.test.mjs`

```js
// The review journal (docs/plans/2026-10-07-hitl-review-grading-design.md §2):
// each state.js operation emits one self-contained event; an undo emits a
// retraction of the event it undid.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../../app/static/js/state.js');

function row(id, extra = {}) {
  return { id, pos: 0, char_type: 'Distance', nominal: '1', upper_tol: '',
           lower_tol: '', needs_review: true, review_reasons: [],
           target_region: [0, 0, 10, 10], balloon_xy: [1, 1], suggested: false, ...extra };
}
function load(rows) { S.setSession({ session_id: 's', image_url: '', rows, notes: null }); }
function capture() {
  const seen = [];
  const off = S.on('op', (e) => seen.push({ kind: e.kind, event: e.op.event() }));
  return { seen, off };
}

test('each operation describes itself after it ran', () => {
  load([row('a'), row('b'), row('s', { suggested: true })]);
  const { seen, off } = capture();
  S.apply(S.opEditCell('a', 'nominal', '2'));
  S.apply(S.opMoveRow('a', [3, 4]));
  S.apply(S.opDeleteRow('b'));
  S.apply(S.opAddRow(row('m', { source: 'manual' })));
  S.apply(S.opConfirmSuggestions(['s']));
  S.apply(S.opBulkReview(['a'], true));
  S.apply(S.opBulkReview(['a'], false));
  off();
  assert.deepEqual(seen.map((x) => x.event.type),
    ['edit_cell', 'move_row', 'delete_row', 'add_row', 'confirm_suggestions', 'accept', 'unaccept']);
  assert.deepEqual(seen[0].event, { type: 'edit_cell', id: 'a', field: 'nominal', old: '1', new: '2' });
  assert.deepEqual(seen[1].event, { type: 'move_row', id: 'a', xy: [3, 4] });
  assert.equal(seen[3].event.row.id, 'm');
});

test('undo and redo travel the bus as undo and do', () => {
  load([row('a')]);
  const { seen, off } = capture();
  S.apply(S.opEditCell('a', 'nominal', '2'));
  S.undo();
  S.redo();
  off();
  assert.deepEqual(seen.map((x) => x.kind), ['do', 'undo', 'do']);
});
```

- [ ] **Step 2: Run** `node --test tests/js/journal.test.mjs` → FAIL (`e.op.event is not a function` / no `op` events).

- [ ] **Step 3: Implement** in `state.js`

`apply` / `undo` / `redo` — emit after the state change so `event()` can read captured values:

```js
export function apply(op) {
  op.do();
  undoStack.push(op);
  redoStack.length = 0;
  emit('op', { kind: 'do', op });
  emit('change');
  emit('history');
}
export function undo() {
  const op = undoStack.pop();
  if (!op) return;
  op.undo();
  redoStack.push(op);
  emit('op', { kind: 'undo', op });
  emit('change');
  emit('history');
}
export function redo() {
  const op = redoStack.pop();
  if (!op) return;
  op.do();
  undoStack.push(op);
  emit('op', { kind: 'do', op });
  emit('change');
  emit('history');
}
```

Add an `event()` member to each op object (the existing `label`/`do`/`undo` stay as they are):

```js
// opAddRow — JSON clone, so a later edit of the row cannot rewrite the logged event
    event: () => ({ type: 'add_row', row: JSON.parse(JSON.stringify(row)) }),
// opDeleteRow
    event: () => ({ type: 'delete_row', id }),
// opMoveRow
    event: () => ({ type: 'move_row', id, xy: [...newXY] }),
// opEditCell — evaluated after do(), so oldValue is the captured one
    event: () => ({ type: 'edit_cell', id, field, old: oldValue ?? '', new: newValue }),
// opConfirmSuggestions
    event: () => ({ type: 'confirm_suggestions', ids: [...ids] }),
// opBulkReview
    event: () => ({ type: target ? 'accept' : 'unaccept', ids: [...ids] }),
```

Above `apply`, add one comment line: `// Every op carries event(): the review journal's self-description of it (journal.js). Python mirrors these semantics in app/review/replay.py.`

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs` → all pass (existing `state.test.mjs` included).

- [ ] **Step 5: Commit**

```bash
git add app/static/js/state.js tests/js/journal.test.mjs
git commit -m "ui: every state operation describes itself for the review journal"
```

### Task 8: `journal.js` — numbering, retractions, acked flush

**Files:**
- Create: `app/static/js/journal.js`
- Test: `tests/js/journal.test.mjs` (append)

- [ ] **Step 1: Append failing tests**

```js
const { createJournal } = await import('../../app/static/js/journal.js');

function fakeOp(ev) { return { event: () => ev }; }
function fakeServer() {
  const held = new Set();
  const calls = [];
  return {
    calls,
    fail: false,
    async send(sid, writer, events) {
      calls.push(events.map((e) => e.seq));
      if (this.fail) throw new Error('offline');
      events.forEach((e) => held.add(e.seq));
      let n = 0; while (held.has(n + 1)) n++;
      return { contiguous: n };
    },
  };
}

test('numbers events from 1 and turns an undo into a retraction', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.start('s', 'w');
  const op = fakeOp({ type: 'accept', ids: ['a'] });
  j.record({ kind: 'do', op });
  j.record({ kind: 'undo', op });
  assert.deepEqual(j.pending.map((e) => [e.seq, e.type, e.target]),
    [[1, 'accept', undefined], [2, 'retract', 1]]);
});

test('a redo is a fresh event, and undoing it retracts the fresh seq', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.start('s', 'w');
  const op = fakeOp({ type: 'delete_row', id: 'b' });
  j.record({ kind: 'do', op });     // 1
  j.record({ kind: 'undo', op });   // 2 retract 1
  j.record({ kind: 'do', op });     // 3
  j.record({ kind: 'undo', op });   // 4 retract 3
  assert.equal(j.pending.at(-1).target, 3);
});

test('records nothing without a writer (logging off)', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }) });
  j.start('s', null);
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: [] }) });
  assert.equal(j.pending.length, 0);
  assert.equal(j.active, false);
});

test('flush drops what the server acknowledged and keeps the rest', async () => {
  const srv = fakeServer();
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['b'] }) });
  assert.equal(await j.flush(), 2);
  assert.equal(j.pending.length, 0);
  assert.equal(j.acked, 2);
  assert.equal(j.lastSeq, 2);
});

test('a failed flush keeps every event for the next attempt', async () => {
  const srv = fakeServer(); srv.fail = true;
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await assert.rejects(j.flush());
  assert.equal(j.pending.length, 1);
  srv.fail = false;
  assert.equal(await j.flush(), 1);
});

test('concurrent flushes share one request', async () => {
  const srv = fakeServer();
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await Promise.all([j.flush(), j.flush()]);
  assert.equal(srv.calls.length, 1);
});
```

- [ ] **Step 2: Run** `node --test tests/js/journal.test.mjs` → FAIL (module missing).

- [ ] **Step 3: Implement** `app/static/js/journal.js`

```js
// The review journal (docs/plans/2026-10-07-hitl-review-grading-design.md §2).
//
// Numbers each operation's event from 1, turns an undo into a `retract` of
// the event it undid, and flushes to the server, which is idempotent by seq
// and answers with the highest seq it holds contiguously. Everything above
// that stays pending and is resent, so a dropped request costs nothing.
// Pure apart from the injected `send`, so node can test it.

export function createJournal({ send, now = () => Date.now(),
                                wallNow = () => new Date().toISOString() }) {
  let sessionId = null;
  let writer = null;
  let seq = 0;
  let acked = 0;
  let t0 = 0;
  let pending = [];
  let inflight = null;
  let seqOf = new WeakMap();   // op -> seq of its most recent 'do' event

  return {
    start(sid, w) {
      sessionId = sid; writer = w || null;
      seq = 0; acked = 0; pending = []; inflight = null; seqOf = new WeakMap();
      t0 = now();
    },
    get active() { return writer !== null; },
    get sessionId() { return sessionId; },
    get writer() { return writer; },
    get lastSeq() { return seq; },
    get acked() { return acked; },
    get pending() { return pending.slice(); },

    record({ kind, op }) {
      if (!writer || typeof op.event !== 'function') return;
      if (kind === 'undo' && !seqOf.has(op)) return;   // done before logging started
      const base = { seq: ++seq, t_wall: wallNow(), t_ms: now() - t0 };
      if (kind === 'do') {
        pending.push({ ...base, ...op.event() });
        seqOf.set(op, base.seq);
      } else {
        pending.push({ ...base, type: 'retract', target: seqOf.get(op) });
      }
    },

    flush() {
      if (!writer) return Promise.resolve(acked);
      if (inflight) return inflight;
      if (!pending.length) return Promise.resolve(acked);
      const batch = pending.slice();
      inflight = send(sessionId, writer, batch)
        .then(({ contiguous }) => {
          acked = Math.max(acked, contiguous);
          pending = pending.filter((e) => e.seq > acked);
          return acked;
        })
        .finally(() => { inflight = null; });
      return inflight;
    },
  };
}
```

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/journal.js tests/js/journal.test.mjs
git commit -m "ui: review journal — seq numbering, retractions, acked single-flight flush"
```

### Task 9: `progress()` and `canFinish()` — stop calling unchecked rows reviewed

**Files:**
- Modify: `app/static/js/state.js` (after `counts()`), `app/static/js/table.js` (`renderCounts`)
- Test: `tests/js/progress.test.mjs`

- [ ] **Step 1: Write the failing test** `tests/js/progress.test.mjs`

```js
// Review progress (design §1). The old bar counted an unflagged row as
// reviewed (r.reviewed || !r.needs_review) -- exactly the rows nobody looked at.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../../app/static/js/state.js');

function row(id, extra = {}) {
  return { id, pos: 0, needs_review: false, suggested: false,
           target_region: [0, 0, 1, 1], ...extra };
}
function load(rows) { S.setSession({ session_id: 's', image_url: '', rows, notes: null }); }

test('progress counts flagged balloons as the queue and unflagged ones as unchecked', () => {
  load([row('f1', { needs_review: true }), row('f2', { needs_review: true }),
        row('u1'), row('u2'), row('s', { suggested: true, needs_review: true })]);
  assert.deepEqual(S.progress(), { resolved: 0, total: 2, outstanding: 2, unchecked: 2 });
  S.apply(S.opBulkReview(['f1'], true));
  assert.deepEqual(S.progress(), { resolved: 1, total: 2, outstanding: 1, unchecked: 2 });
});

test('accepting an unflagged row does not make it checked', () => {
  load([row('u1')]);
  S.apply(S.opBulkReview(['u1'], true));
  assert.equal(S.progress().unchecked, 1);
});

test('Finish is allowed only when no flagged balloon is outstanding', () => {
  load([row('f1', { needs_review: true }), row('s', { suggested: true, needs_review: true })]);
  assert.equal(S.canFinish(), false);
  S.apply(S.opDeleteRow('f1'));
  assert.equal(S.canFinish(), true);   // the unconfirmed suggestion does not block
});

```

- [ ] **Step 2: Run** `node --test tests/js/progress.test.mjs` → FAIL (`S.progress is not a function`).

- [ ] **Step 3: Implement**

`state.js`, after `counts()`:

```js
// The review queue (Phase 1: flagged balloons). Unflagged balloons are counted
// apart as `unchecked`: the system accepted them and nobody was asked to look,
// so an explicit accept on one is not evidence it was checked either.
// Suggestions are not balloons and are neither.
export function progress() {
  const balloons = state.rows.filter((r) => !r.suggested);
  const queue = balloons.filter((r) => r.needs_review);
  const resolved = queue.filter((r) => r.reviewed).length;
  return { resolved, total: queue.length, outstanding: queue.length - resolved,
           unchecked: balloons.length - queue.length };
}
export const canFinish = () => progress().outstanding === 0;
```

`table.js`: add `progress` to the import from `./state.js`, and in `renderCounts` replace the block from `const balloons = …` through the closing `}` of `if (totalN > 0) {…} else {…}` with:

```js
  // Resolved flagged rows only; unflagged rows are shown apart as unchecked,
  // never as reviewed (design §1).
  const p = progress();
  const balloons = state.rows.filter((r) => !r.suggested);
  const totalN = balloons.length;
  const prog = document.getElementById('review-progress');
  if (totalN > 0) {
    prog.hidden = false;
    document.getElementById('review-text').textContent =
      `${p.resolved}/${p.total} resolved · ${p.unchecked} unchecked`;
    document.getElementById('review-bar').style.width =
      (p.total ? (p.resolved / p.total * 100) : 100) + '%';
  } else {
    prog.hidden = true;
  }
```

(`totalN` is still used by the footer below; keep it.)

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/state.js app/static/js/table.js tests/js/progress.test.mjs
git commit -m "ui: progress counts resolved flags and shows unchecked rows apart"
```

### Task 10: Finish & Export, journal wiring, logging pill

**Files:**
- Modify: `app/static/js/api.js` (append), `app/static/js/main.js`, `app/static/index.html`
- Test: browser drive (Step 4) — DOM wiring has no node test; the logic it calls is covered by Tasks 7-9 and the API tests.

- [ ] **Step 1: `api.js` — append**

```js
export async function postEvents(sessionId, writer, events) {
  const res = await fetch(`/api/session/${sessionId}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ writer, events }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Could not save the review log' }));
    throw new Error(err.detail || 'Could not save the review log');
  }
  return res.json();
}

// Last-chance flush when the page goes away; the server dedups by seq, so a
// beacon that races a normal flush costs nothing.
export function beaconEvents(sessionId, writer, events) {
  if (!navigator.sendBeacon || !events.length) return;
  const blob = new Blob([JSON.stringify({ writer, events })], { type: 'application/json' });
  navigator.sendBeacon(`/api/session/${sessionId}/events`, blob);
}

export async function sealSession(sessionId, body) {
  const res = await fetch(`/api/session/${sessionId}/seal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Could not finish' }));
    throw new Error(err.detail || 'Could not finish');
  }
  return res.json();
}
```

- [ ] **Step 2: `index.html`**

Replace the two buttons `#export-xlsx` and `#export-pdf` with:

```html
        <button class="btn primary" id="finish-btn" disabled
                title="Seal the review and export Excel + ballooned PDF">
          <svg class="icon"><use href="#i-download"/></svg>
          Finish &amp; Export
        </button>
```

After the `#ocr-pill` span in the header, add:

```html
    <span class="status-pill warn" id="log-pill" hidden
          title="SINDRI_REVIEW_DIR is not set or not writable: this review is not recorded and will not be graded">
      <span class="dot"></span><span>Review logging off</span>
    </span>
```

- [ ] **Step 3: `main.js`**

Imports — extend the two existing import lines and add the journal:

```js
import { state, on, setSession, clearSession, undo, redo, canUndo, canRedo,
         progress, canFinish } from './state.js';
import { savePdf, runExtraction, deleteSession, exportFile, health,
         postEvents, beaconEvents, sealSession } from './api.js';
import { createJournal } from './journal.js';
```

Module scope, after `let extractAbort = null;`:

```js
// The review journal: every state operation, numbered and flushed to the
// server (docs/plans/2026-10-07-hitl-review-grading-design.md §2).
const journal = createJournal({
  send: (sid, writer, events) => postEvents(sid, writer, events),
});
let flushTimer = null;
let flushBackoff = 500;
```

In `init()`, replace `wireExports();` with `wireFinish();` and add `wireJournal();` after it.

In `wireHeader`'s `on('session', …)` handler, replace the four lines that toggle `export-xlsx` / `export-pdf` `.disabled` with:

```js
      document.getElementById('finish-btn').disabled = false;
```

in the `if (state.sessionId)` branch, and in the `else` branch:

```js
      document.getElementById('finish-btn').disabled = true;
      document.getElementById('log-pill').hidden = true;
```

In the extraction success path, directly after `setSession(data);`:

```js
    journal.start(data.session_id, data.writer);
    document.getElementById('log-pill').hidden = !!data.review_logging;
```

Replace the whole `// ===== Exports` section (`wireExports`) with:

```js
// ===== Journal =======================================================
function wireJournal() {
  on('op', (e) => { journal.record(e); scheduleFlush(500); });
  // pagehide, not beforeunload: it also fires on mobile tab discards.
  window.addEventListener('pagehide', () => {
    if (journal.active) beaconEvents(journal.sessionId, journal.writer, journal.pending);
  });
}

function scheduleFlush(delay) {
  clearTimeout(flushTimer);
  flushTimer = setTimeout(async () => {
    try {
      await journal.flush();
      flushBackoff = 500;
    } catch {
      // Keep everything pending and retry, backing off to 10 s; Finish
      // refuses until the server holds every event, so nothing is lost.
      flushBackoff = Math.min(flushBackoff * 2, 10000);
      scheduleFlush(flushBackoff);
    }
  }, delay);
}

// ===== Finish & Export ==============================================
function wireFinish() {
  const btn = document.getElementById('finish-btn');
  on('change', () => {
    if (!state.sessionId) return;
    const p = progress();
    btn.classList.toggle('blocked', !canFinish());
    btn.title = canFinish()
      ? 'Seal the review and export Excel + ballooned PDF'
      : `${p.outstanding} flagged row${p.outstanding === 1 ? '' : 's'} still to resolve`;
  });
  btn.addEventListener('click', finish);
}

async function finish() {
  if (!canFinish()) {
    const n = progress().outstanding;
    toast({ kind: 'warn', title: 'Not finished yet',
            msg: `${n} flagged row${n === 1 ? '' : 's'} still to resolve — showing them now.` });
    document.querySelector('#filter-pills button[data-filter="review"]')?.click();
    return;
  }
  setBusy('Finishing…');
  try {
    let sealed = null;
    if (journal.active) {
      clearTimeout(flushTimer);
      const acked = await journal.flush();
      if (acked < journal.lastSeq) throw new Error('The review log is still saving — try again in a moment.');
      sealed = await sealSession(state.sessionId, {
        writer: journal.writer,
        final_seq: journal.lastSeq,
        rows: state.rows,
        reviewed_ids: state.rows.filter((r) => r.reviewed).map((r) => r.id),
      });
    }
    const payload = { session_id: state.sessionId, rows: state.rows.filter((r) => !r.suggested),
                      notes: state.notes, marks: state.marks, title_block: state.title_block };
    await exportFile('/api/export', payload, 'inspection.xlsx');
    await exportFile('/api/export/pdf', payload, 'ballooned.pdf');
    setIdle();
    toast({ kind: 'ok', title: 'Finished',
            msg: sealed && sealed.revision ? `Review sealed as revision r${sealed.revision}` : 'Exported (review not recorded)' });
  } catch (err) {
    setIdle();
    toast({ kind: 'error', title: 'Finish failed', msg: String(err.message || err) });
  }
}
```

In `styles/components.css`, add:

```css
/* Finish while flagged rows remain: still clickable (the click explains and
   jumps to them), but visibly not ready. */
.btn.primary.blocked { opacity: .55; }
```

- [ ] **Step 4: Verify in a real browser**

Run the full suite first: `python -m pytest -q` → expected **1293 + new passed, 2 skipped**.

Then drive the UI (handoff §6: throwaway Playwright venv in the scratchpad plus a stub-backend launcher; do NOT add Playwright to the repo). Launcher in the scratchpad, `stub_app.py`:

```python
import os, sys
sys.path.insert(0, os.environ["REPO"])
from app.pipeline.detect import Detection
from tests.conftest import StubVLMBackend
import app.main as m
m._BACKEND = StubVLMBackend(detections=[Detection((40, 40, 120, 70), "dimension", 0.9),
                                        Detection((200, 40, 280, 70), "dimension", 0.5)],
                            text="1,2 +0,1 -0,1")
app = m.app
```

Start with `SINDRI_REVIEW_DIR=<scratchpad>/reviews REPO=<repo> uvicorn stub_app:app --port 8765` (run from the scratchpad). Check, by script, using the repo's own `tests/fixtures/sample.pdf`:
1. Upload + extract → `log-pill` hidden; `<scratchpad>/reviews/<sid>/proposal.json` exists.
2. With a flagged row unresolved, Finish → warning toast, Review filter active, no download.
3. Edit a cell, undo, redo, accept the flagged row → after ~1 s `events.jsonl` holds 4 lines (edit, retract, edit, accept).
4. Finish → two downloads; `sealed/r1.json` has `replay_mismatch_ids: []`; `events.jsonl` now holds 2 lines (net).
5. Restart without `SINDRI_REVIEW_DIR` → `log-pill` visible; Finish still exports; toast says "review not recorded".

- [ ] **Step 5: Commit**

```bash
git add app/static/js/api.js app/static/js/main.js app/static/index.html app/static/styles/components.css
git commit -m "ui: Finish & Export seals the review; journal flushes with retry and beacon"
```

### Task 11: deployment config and docs

**Files:**
- Modify: `docker-compose.yml`, `CLAUDE.md` (§2 one paragraph), `docs/plans/2026-10-07-session-handoff.md` is NOT edited (historical)

- [ ] **Step 1: `docker-compose.yml`** — under `environment:` add:

```yaml
      # Durable review records (docs/plans/2026-10-07-hitl-review-grading-design.md).
      # Client data: never inside the image, never in /tmp.
      - SINDRI_REVIEW_DIR=/data/reviews
      - SINDRI_REVIEW_CONSENT=0
```

The existing `./data:/data` volume already persists it; `docker-compose.gpu.yml` merges environment by key, so it inherits these.

- [ ] **Step 2: `CLAUDE.md` §2** — add after the suggestion-tray paragraph:

```markdown
**REVIEW CAPTURE (HITL phase 1,
`docs/plans/2026-10-07-hitl-review-grading-design.md`).** With
`SINDRI_REVIEW_DIR` set, every extraction writes `header.json` and
`proposal.json` server-side, the UI appends net reviewer operations to
`events.jsonl`, and Finish & Export seals `sealed/rN.json` after checking that
replaying the journal on the proposal reproduces the exported rows. **That
directory is client data under §1.** Unset, the app reviews and exports but
records nothing, and the UI says so. Phases 2-5 (forcing, grading, dev
validation, return bundle) each get their own plan.
```


- [ ] **Step 3: Verify**

```bash
python -m pytest -q                          # expect 1293 + new passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # expect 32 passed, 0 failed (unchanged)
```

- [ ] **Step 4: Commit** (stage files one `git add` per call; CLAUDE.md §5)

```bash
git add docker-compose.yml
git add CLAUDE.md
git commit -m "deploy: persist review records under /data/reviews; note phase 1 in CLAUDE.md"
```

> **Milestone: phase 1 complete** — checkpoint state, then write the Phase 2 plan.
