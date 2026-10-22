# Client review campaign C2 — grading and findings report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Milestones follow context-management:milestone-checkpoints: checkpoint `docs/plans/2026-10-22-campaign-c2-grading-plan.state.md` at each marker.

**Goal:** Grade a pulled review campaign against our proposal (client-primary) and against gold (cross-check), and produce:
- a counts-only digest with ranked findings for agents;
- an operator-only drill-down with example crops.

**Architecture:** A pure per-session grader in product code (`app/review/grading.py`), reused later by HITL phase 3. The campaign analysis lives in `app/eval/campaign_grade.py`: it reads the pull tar in place, joins gold through the salted hash, converts to mm, reweights the groups and builds the digest. It is reached as a mode of the allowlisted `score` (`--reviewer-grade`). The drill-down `runner campaign-deck` follows the `review-serve` security pattern and is the operator's.

**Tech stack:** Python 3 / pydantic, `tarfile`, the existing `app/eval` matcher and normalisers, PyMuPDF (crops), `http.server` (deck), pytest.

**Design:** `docs/plans/2026-10-22-campaign-c2-grading-design.md` (approved 2026-10-22). **Read the C1 state file's "What's next" first** (`docs/plans/2026-10-21-campaign-c1-review-plan.state.md`). It is the list of traps this plan encodes.

---

## Rules for every task (project CLAUDE.md)

- **Client data:**
  - Agents never read the protected root, `*.pdf` outside `tests/fixtures`, `*.gold.json`, `*.pred.json`, `*.report.json`, pull tars or campaign reports.
  - All tests use synthetic records built in the test.
  - The agent-safe surfaces are: `runner score --reviewer-grade` (stdout is one count line) and the `--grade-out` digest.
- **Commands:**
  - One unchained command per Bash call. `a && b`, pipes, `>` and a bare `.pdf` in a command are denied.
  - Never widen `~/.claude/hooks/sindri-guard.py`. Re-verify it with `bash ~/.claude/hooks/test-sindri-guard.sh` (expected: 32 passed, 0 failed).
- **Code:**
  - TDD, one task per commit. The commit trailer is `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Digest keys must be namespaced (`fields:...`, `field:...`). A quoted `"upper_tol"`/`"lower_tol"`/`"characteristics"` token in a staged json is blocked by the pre-commit hook. Never set `SINDRI_ALLOW_DATA_COMMIT`.
  - `app/review` must not import `app.eval` (`tests/review/test_product_boundary.py`). `app/eval` may import `app/review`.

## File map

| file | responsibility |
|---|---|
| `app/review/grading.py` (new) | `iou`, `grade_session(proposal_rows, sealed)`: per-row grades, missed rows, placement in px, ungradable reason. Pure; product side |
| `app/eval/campaign_records.py` (new) | `read_records(tar_path) -> CampaignRecords`: index, per-dir header/proposal/sealed/events text, read from the tar in place; tolerates a torn `events.jsonl` tail |
| `app/eval/campaign_grade.py` (new) | `px_to_mm`, `review_time_ms`, `group_metrics`, `reweight`, `gold_cross_check`, `ranked_findings`, `LEVERS`, `grade_campaign(...) -> (report, digest)` |
| `app/eval/share_select.py` | `eligible_counts(rows, ...)`; manifest gains `pool` |
| `app/eval/runner.py` | `score --reviewer-grade/--manifest/--grade-out`; `--run` optional only in that mode; `campaign-deck` subcommand |
| `app/eval/campaign_deck.py` (new) | `examples(report, per_bucket)`, `CampaignDeckApp`, handler, `serve` |
| `tests/review/test_grading.py`, `tests/eval/test_campaign_records.py`, `tests/eval/test_campaign_grade.py`, `tests/eval/test_campaign_deck.py`, `tests/eval/test_runner_reviewer_grade.py`, `tests/eval/campaign_fixtures.py` (new) | tests + shared synthetic-records builder |

---

## Milestone A — the pure grader (product side)

### Task 1: `grade_session` — per-row grades

**Files:**
- Create: `app/review/grading.py`
- Test: `tests/review/test_grading.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Per-session grading (C2 design §Grading). The client's review is the truth:
a grade says what the client did to each proposed row, read from the SEALED
revision (rows + net_events), never from events.jsonl."""
from app.review.grading import GRADES, grade_session


def row(id, **kw):
    base = {"id": id, "pos": 1, "char_type": "Distance", "nominal": "10",
            "upper_tol": "+0.1", "lower_tol": "-0.1", "needs_review": False,
            "suggested": False, "balloon_xy": [100.0, 100.0],
            "target_region": [10.0, 10.0, 50.0, 30.0], "source": "auto"}
    return {**base, **kw}


def sealed(rows, events, mismatch=(), reviewed=()):
    # reviewed_ids must list what the events marked reviewed (accept,
    # confirm_suggestions), or Task 3's replay check reads the seal as
    # inconsistent -- the fixture must be a record the real store could write.
    return {"revision": 1, "rows": rows, "net_events": events,
            "reviewed_ids": list(reviewed), "replay_mismatch_ids": list(mismatch)}


def test_every_proposal_row_gets_exactly_one_grade():
    prop = [row("a"), row("b"), row("c"), row("s1", suggested=True, pos=0),
            row("s2", suggested=True, pos=0), row("s3", suggested=True, pos=0),
            row("s4", suggested=True, pos=0)]
    ev = [
        {"seq": 1, "type": "edit_cell", "id": "b", "field": "nominal",
         "old": "10", "new": "12", "reason": "misread_digit"},
        {"seq": 2, "type": "delete_row", "id": "c", "reason": "duplicate"},
        {"seq": 3, "type": "confirm_suggestions", "ids": ["s1", "s2"]},
        {"seq": 4, "type": "edit_cell", "id": "s2", "field": "upper_tol",
         "old": "+0.1", "new": "+0.2"},
        {"seq": 5, "type": "delete_row", "id": "s3"},
    ]
    final = [row("a"), row("b", nominal="12"),
             row("s1", suggested=False), row("s2", suggested=False, upper_tol="+0.2"),
             row("s4", suggested=True, pos=0)]
    g = grade_session(prop, sealed(final, ev, reviewed=["s1", "s2"]))
    by = {r["id"]: r for r in g["rows"]}
    assert g["ungradable"] is None
    assert {k: by[k]["grade"] for k in by} == {
        "a": "confirmed", "b": "edited", "c": "phantom",
        "s1": "suggestion_confirmed", "s2": "suggestion_edited",
        "s3": "suggestion_deleted", "s4": "suggestion_left"}
    assert by["b"]["fields"] == ["nominal"] and by["b"]["reason"] == "misread_digit"
    assert by["c"]["reason"] == "duplicate"
    assert by["s2"]["fields"] == ["upper_tol"]
    assert set(GRADES) >= {r["grade"] for r in g["rows"]}


def test_an_edit_undone_by_a_second_edit_is_confirmed():
    """Net state decides, not activity: 10 -> 12 -> 10 is still 10."""
    ev = [{"seq": 1, "type": "edit_cell", "id": "a", "field": "nominal", "old": "10", "new": "12"},
          {"seq": 2, "type": "edit_cell", "id": "a", "field": "nominal", "old": "12", "new": "10"}]
    g = grade_session([row("a")], sealed([row("a")], ev))
    assert g["rows"][0]["grade"] == "confirmed"


def test_rows_added_by_the_client_are_missed():
    added = row("m1", source="manual", nominal="5")
    ev = [{"seq": 1, "type": "add_row", "row": added}]
    g = grade_session([row("a")], sealed([row("a"), added], ev))
    assert [m["id"] for m in g["missed"]] == ["m1"]


def test_flagged_is_carried_for_flag_precision():
    g = grade_session([row("a", needs_review=True)],
                      sealed([row("a", needs_review=True)], []))
    assert g["rows"][0]["flagged"] is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/review/test_grading.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.review.grading'`

- [ ] **Step 3: Implement** — create `app/review/grading.py`:

```python
"""Grade one sealed review against its proposal (C2 design §Grading).

Product code (no app.eval import), so HITL phase 3 can grade in-house
reviews with the same function. Pure: dicts in, dicts out.

The client's review is the truth here. A grade says what the reviewer did to
each proposed row, judged on the SEALED revision -- its rows and net_events --
because events.jsonl can hold unsealed or torn tail events (C1 state file,
'What's next' 1). Value comparison is exact string equality on the four
VALUE_FIELDS: every change arrives as an explicit edit_cell, so a difference
is the reviewer's act, and an edit undone by a later edit nets to unchanged."""
from typing import Dict, List, Optional

from app.review.journal import VALUE_FIELDS

GRADES = ("confirmed", "edited", "phantom", "suggestion_confirmed",
          "suggestion_edited", "suggestion_deleted", "suggestion_left")


def _changed_fields(a: dict, b: dict) -> List[str]:
    return [f for f in VALUE_FIELDS if (a.get(f) or "") != (b.get(f) or "")]


def _last_reasons(events: List[dict]) -> Dict[str, Dict[str, str]]:
    """The last reason given per row, per kind of act ('edit' / 'delete')."""
    out: Dict[str, Dict[str, str]] = {}
    for e in events:
        if e.get("reason") and e["type"] in ("edit_cell", "delete_row"):
            kind = "edit" if e["type"] == "edit_cell" else "delete"
            out.setdefault(e["id"], {})[kind] = e["reason"]
    return out


def grade_session(proposal_rows: List[dict], sealed: dict) -> dict:
    final = {r["id"]: r for r in sealed.get("rows", [])}
    reasons = _last_reasons(sealed.get("net_events", []))
    graded = []
    for p in proposal_rows:
        f = final.get(p["id"])
        r = reasons.get(p["id"], {})
        rec = {"id": p["id"], "flagged": bool(p.get("needs_review")),
               "suggested": bool(p.get("suggested")), "kind": p.get("kind") or "",
               "fields": [], "reason": None}
        if p.get("suggested"):
            if f is None:
                rec["grade"] = "suggestion_deleted"
                rec["reason"] = r.get("delete")
            elif f.get("suggested"):
                rec["grade"] = "suggestion_left"
            else:
                rec["fields"] = _changed_fields(p, f)
                rec["grade"] = "suggestion_edited" if rec["fields"] else "suggestion_confirmed"
                rec["reason"] = r.get("edit") if rec["fields"] else None
        elif f is None:
            rec["grade"] = "phantom"
            rec["reason"] = r.get("delete")
        else:
            rec["fields"] = _changed_fields(p, f)
            rec["grade"] = "edited" if rec["fields"] else "confirmed"
            rec["reason"] = r.get("edit") if rec["fields"] else None
        graded.append(rec)
    proposed = {p["id"] for p in proposal_rows}
    missed = [r for i, r in final.items() if i not in proposed]
    return {"rows": graded, "missed": missed, "ungradable": None}
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python -m pytest tests/review/test_grading.py tests/review/test_product_boundary.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/review/grading.py tests/review/test_grading.py
git commit -m "review: grade a sealed review against its proposal

Client-primary per-row grades from the sealed revision (rows + net events),
product-side so HITL phase 3 reuses it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: placement — bubble displacement and anchor IoU (px)

**Files:**
- Modify: `app/review/grading.py`
- Test: `tests/review/test_grading.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from app.review.grading import iou


def test_iou_basics():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert abs(iou([0, 0, 10, 10], [5, 0, 15, 10]) - 1 / 3) < 1e-9
    assert iou(None, [0, 0, 1, 1]) is None


def test_placement_is_measured_against_the_proposal_not_the_events():
    """Events carry only the NEW position (C1 decision 8); the old one is the
    proposal's, so displacement is proposal -> sealed final."""
    ev = [{"seq": 1, "type": "move_row", "id": "a", "xy": [130.0, 140.0]},
          {"seq": 2, "type": "move_target", "id": "a", "box": [10.0, 10.0, 50.0, 30.0]}]
    final = [row("a", balloon_xy=[130.0, 140.0])]
    g = grade_session([row("a")], sealed(final, ev))
    r = g["rows"][0]
    assert r["bubble_px"] == 50.0           # (30, 40) -> 50
    assert r["anchor_iou"] == 1.0 and r["anchor_moved"] is False


def test_a_deleted_row_has_no_placement():
    g = grade_session([row("a")], sealed([], [{"seq": 1, "type": "delete_row", "id": "a"}]))
    assert g["rows"][0]["bubble_px"] is None and g["rows"][0]["anchor_iou"] is None
```

- [ ] **Step 2: Run and watch fail** — `python -m pytest tests/review/test_grading.py -q` → ImportError `iou`.

- [ ] **Step 3: Implement** — add to `grading.py`:

```python
import math


def iou(a, b) -> Optional[float]:
    if a is None or b is None:
        return None
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else None


def _placement(p: dict, f: Optional[dict]) -> dict:
    """Pixels in the page image the proposal was drawn in; campaign_grade
    turns them into mm with the drawing's render_scale."""
    if f is None:
        return {"bubble_px": None, "anchor_iou": None, "anchor_moved": False}
    a, b = p.get("balloon_xy"), f.get("balloon_xy")
    bubble = (round(math.dist(a, b), 3) if a is not None and b is not None else None)
    pa, fa = p.get("target_region"), f.get("target_region")
    moved = (pa is not None and fa is not None
             and any(abs(x - y) > 1e-6 for x, y in zip(pa, fa)))
    return {"bubble_px": bubble, "anchor_iou": iou(pa, fa), "anchor_moved": moved}
```

In `grade_session`, after the grade is decided for each row: `rec.update(_placement(p, f))`.

- [ ] **Step 4: Run and watch pass** — same command → PASS.

- [ ] **Step 5: Commit** — `git add app/review/grading.py tests/review/test_grading.py`, then commit "review: measure bubble and anchor placement against the proposal" with the why (events hold only new values) and the trailer.

### Task 3: ungradable sessions

**Files:**
- Modify: `app/review/grading.py`
- Test: `tests/review/test_grading.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
def test_a_seal_that_recorded_a_replay_mismatch_is_ungradable():
    g = grade_session([row("a")], sealed([row("a")], [], mismatch=["a"]))
    assert g["ungradable"] == "replay_mismatch"


def test_a_seal_whose_rows_disagree_with_replay_is_ungradable():
    """The sealed rows say 12, but no event changed the value: a record we
    cannot explain is never graded (design §Reconciliation)."""
    g = grade_session([row("a")], sealed([row("a", nominal="12")], []))
    assert g["ungradable"] == "replay_disagrees"


def test_ungradable_still_lists_rows_so_counts_reconcile():
    g = grade_session([row("a")], sealed([row("a")], [], mismatch=["a"]))
    assert len(g["rows"]) == 1
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement** — in `grade_session`, before returning:

```python
from app.review.replay import mismatches, replay

    # A record whose rows cannot be reproduced from its own journal is
    # reported and never graded (design §Reconciliation). The rows are still
    # listed so the per-drawing counts reconcile; callers exclude them.
    if sealed.get("replay_mismatch_ids"):
        ungradable = "replay_mismatch"
    elif mismatches(replay(proposal_rows, sealed.get("net_events", [])),
                    sealed.get("rows", []), sealed.get("reviewed_ids", [])):
        ungradable = "replay_disagrees"
    else:
        ungradable = None
    return {"rows": graded, "missed": missed, "ungradable": ungradable}
```

Note: `mismatches` compares the `reviewed` flag against `reviewed_ids`. The test fixtures pass `reviewed_ids: []` and contain no accept events, so they agree. Check `app/review/replay.py` for the exact signature.

- [ ] **Step 4: Run and watch pass:** `python -m pytest tests/review -q`.

- [ ] **Step 5: Commit** — "review: a seal that replay cannot reproduce is ungradable".

> **Milestone: pure grader** — state must be checkpointed here before continuing.

---

## Milestone B — campaign analysis (eval side)

### Task 4: shared synthetic records + `read_records`

**Files:**
- Create: `tests/eval/campaign_fixtures.py`, `app/eval/campaign_records.py`
- Test: `tests/eval/test_campaign_records.py`

- [ ] **Step 1: Write the fixture builder** — create `tests/eval/campaign_fixtures.py`. It writes a tar laid out exactly as `app/campaign/pull.py` writes one; check `pull.py` and copy its member names:

```python
"""Synthetic pull tars for C2 tests -- the exact layout app/campaign/pull.py
writes, built from dicts so no client data is ever needed."""
import io
import json
import tarfile


def proposal_row(id, **kw):
    base = {"id": id, "pos": 1, "char_type": "Distance", "nominal": "10",
            "upper_tol": "+0.1", "lower_tol": "-0.1", "needs_review": False,
            "suggested": False, "balloon_xy": [100.0, 100.0],
            "target_region": [10.0, 10.0, 50.0, 30.0], "source": "auto",
            "kind": "dimension", "confidence": 0.99}
    return {**base, **kw}


def campaign_facts(doc_hash, group="great", render_scale=300 / 72.0, **kw):
    return {"campaign": "pilot-1", "group": group, "source_run": "r5-control",
            "doc_hash": doc_hash, "file_name": f"{doc_hash}.pdf",
            "render_scale": render_scale, "page_rect": [0.0, 0.0, 842.0, 595.0],
            "page_px": [3508, 2479], "run_config": {}, "policy": {}, **kw}


def drawing(doc_hash, proposal, final, net_events, *, group="great",
            events_lines=None, approved=True, mismatch=(), **facts):
    return {"doc_hash": doc_hash, "group": group, "approved": approved,
            "header": {"review_schema_version": 1,
                       "campaign": campaign_facts(doc_hash, group, **facts)},
            "proposal": {"rows": proposal},
            "sealed": {"revision": 1, "rows": final, "net_events": net_events,
                       "reviewed_ids": [], "replay_mismatch_ids": list(mismatch),
                       "final_seq": len(net_events), "sealed_at": "t"},
            "events_lines": events_lines if events_lines is not None else
                            [json.dumps(e) for e in net_events]}


def write_tar(path, drawings, skipped=(), campaign="pilot-1"):
    docs = []
    with tarfile.open(path, "w") as tar:
        def add(name, data: bytes):
            info = tarfile.TarInfo(f"{campaign}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for d in drawings:
            h = d["doc_hash"]
            add(f"{h}/header.json", json.dumps(d["header"]).encode())
            add(f"{h}/proposal.json", json.dumps(d["proposal"]).encode())
            add(f"{h}/events.jsonl", "\n".join(d["events_lines"]).encode())
            if d["approved"]:
                add(f"{h}/sealed/r1.json", json.dumps(d["sealed"]).encode())
            docs.append({"doc_hash": h, "dir": h, "group": d["group"],
                         "state": "finished" if d["approved"] else "in_review",
                         "final_revision": 1 if d["approved"] else None,
                         "sealed_seq": 0, "sealed_revision": 1 if d["approved"] else 0})
        add("index.json", json.dumps({"campaign": campaign, "documents": docs,
                                      "skipped": list(skipped)}).encode())
    return path
```

- [ ] **Step 2: Write the failing tests** — `tests/eval/test_campaign_records.py`:

```python
from app.eval.campaign_records import read_records
from tests.eval.campaign_fixtures import drawing, proposal_row, write_tar


def test_reads_index_and_each_drawing_in_place(tmp_path):
    p = [proposal_row("a")]
    tar = write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, []),
        drawing("bbbbbbbb", p, p, [], approved=False)])
    rec = read_records(tar)
    assert rec.campaign == "pilot-1"
    assert set(rec.drawings) == {"aaaaaaaa", "bbbbbbbb"}
    a = rec.drawings["aaaaaaaa"]
    assert a.header["campaign"]["doc_hash"] == "aaaaaaaa"
    assert a.proposal_rows[0]["id"] == "a" and a.sealed["revision"] == 1
    assert rec.drawings["bbbbbbbb"].sealed is None          # pending


def test_a_torn_events_tail_is_ignored_not_fatal(tmp_path):
    p = [proposal_row("a")]
    d = drawing("aaaaaaaa", p, p, [], events_lines=['{"seq": 1, "t_ms": 5}', '{"seq": 2, "t_m'])
    rec = read_records(write_tar(tmp_path / "r.tar", [d]))
    assert [e["seq"] for e in rec.drawings["aaaaaaaa"].events] == [1]


def test_skipped_drawings_are_carried(tmp_path):
    skip = [{"id8": "deadbeef", "state": "ready", "reason": "no_proposal",
             "doc_hash": "cccccccc", "group": "problem"}]
    rec = read_records(write_tar(tmp_path / "r.tar", [], skipped=skip))
    assert rec.skipped == skip
```

- [ ] **Step 3: Run and watch fail** — `python -m pytest tests/eval/test_campaign_records.py -q` → ModuleNotFoundError.

- [ ] **Step 4: Implement** — `app/eval/campaign_records.py`:

```python
"""Read a pulled campaign (app/campaign/pull.py) straight from its tar: no
extraction, so no second copy of client data lands anywhere.

Only `final_revision` decides which sealed revision is graded (pull.py's
docstring: the DB's final_revision is what the reviewer approved). events.jsonl
is read for review TIME only, and a torn last line -- a pull taken mid-append --
is dropped, not fatal."""
import json
import tarfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DrawingRecord:
    dir: str
    index: dict
    header: dict
    proposal_rows: List[dict]
    sealed: Optional[dict]
    events: List[dict] = field(default_factory=list)


@dataclass
class CampaignRecords:
    campaign: str
    drawings: Dict[str, DrawingRecord]      # keyed by index doc_hash (or dir if none)
    skipped: List[dict]


def _events(text: str) -> List[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            break       # a torn tail; nothing after it can be trusted either
    return out


def read_records(tar_path) -> CampaignRecords:
    with tarfile.open(tar_path, "r") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}

        def text(name):
            m = members.get(name)
            return tar.extractfile(m).read().decode("utf-8") if m else None

        index_name = next(n for n in members if n.endswith("/index.json")
                          and n.count("/") == 1)
        root = index_name.split("/")[0]
        index = json.loads(text(index_name))
        drawings = {}
        for d in index.get("documents", []):
            base = f"{root}/{d['dir']}"
            rev = d.get("final_revision")
            sealed_txt = text(f"{base}/sealed/r{rev}.json") if rev else None
            key = d.get("doc_hash") or d["dir"]
            drawings[key] = DrawingRecord(
                dir=d["dir"], index=d,
                header=json.loads(text(f"{base}/header.json") or "{}"),
                proposal_rows=json.loads(text(f"{base}/proposal.json") or '{"rows": []}')["rows"],
                sealed=json.loads(sealed_txt) if sealed_txt else None,
                events=_events(text(f"{base}/events.jsonl") or ""))
        return CampaignRecords(campaign=index.get("campaign", root),
                               drawings=drawings, skipped=index.get("skipped", []))
```

- [ ] **Step 5: Run and watch pass**, then commit `app/eval/campaign_records.py tests/eval/campaign_fixtures.py tests/eval/test_campaign_records.py` ("eval: read a pulled campaign in place").

### Task 5: unit conversion, review time, group metrics, reweighting

**Files:**
- Create: `app/eval/campaign_grade.py`
- Test: `tests/eval/test_campaign_grade.py`

- [ ] **Step 1: Write the failing tests**:

```python
"""Campaign metrics (C2 design §Metrics)."""
import math

from app.eval.campaign_grade import (IDLE_MS, group_metrics, px_to_mm,
                                     reweight, review_time_ms)


def test_px_to_mm_uses_render_scale():
    # 300 dpi: scale 300/72 px per pt; 300 px = 72 pt = 25.4 mm
    assert math.isclose(px_to_mm(300.0, 300 / 72.0), 25.4)


def test_review_time_sums_gaps_caps_idle_and_splits_sessions():
    ev = [{"t_ms": 0}, {"t_ms": 1000}, {"t_ms": 1000 + IDLE_MS + 1},  # idle gap ignored
          {"t_ms": 50}, {"t_ms": 2050}]                                 # reload: new session
    assert review_time_ms(ev) == 1000 + 2000


def graded(grade, flagged=False, bubble_px=None, anchor_iou=None, moved=False, fields=()):
    return {"grade": grade, "flagged": flagged, "fields": list(fields), "reason": None,
            "bubble_px": bubble_px, "anchor_iou": anchor_iou, "anchor_moved": moved}


def test_group_metrics_counts_precision_recall_and_placement():
    rows = [graded("confirmed", bubble_px=0.0), graded("confirmed", flagged=True, bubble_px=60.0),
            graded("edited", fields=["nominal"], bubble_px=0.0),
            graded("phantom", flagged=True),
            graded("confirmed", anchor_iou=0.5, moved=True, bubble_px=0.0),
            graded("suggestion_left"), graded("suggestion_deleted")]
    m = group_metrics([{"rows": rows, "missed": [{"id": "m"}], "scale": 300 / 72.0,
                        "time_ms": 60000}])
    assert m["counts"]["confirmed"] == 3 and m["counts"]["missed"] == 1
    assert math.isclose(m["precision"], 3 / 5)
    assert math.isclose(m["unflagged_precision"], 2 / 3)
    assert math.isclose(m["flag_precision"], 1 / 2)         # 1 of 2 flagged was wrong
    assert math.isclose(m["recall_lower_bound"], 4 / 5)     # 4 surviving balloons, 1 missed
    assert m["placement"]["bubble_moved"] == 1
    assert m["placement"]["anchor_nudged"] == 1 and m["placement"]["anchor_repointed"] == 0
    assert m["review_minutes_median"] == 1.0


def test_reweight_to_the_pool_mix_and_refuse_empty_groups():
    assert math.isclose(reweight({"great": 0.9, "problem": 0.5},
                                 {"great": 30, "problem": 10}), 0.8)
    assert reweight({"great": 0.9, "problem": None}, {"great": 30, "problem": 10}) is None
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement** — `app/eval/campaign_grade.py` (the first part; Tasks 6-7 append to it):

```python
"""Grade a pulled review campaign (C2 design). The client's review is the
truth; gold is a cross-check that never flips a grade.

Every output that leaves the protected root is counts + salted hashes only."""
import math
import statistics
from typing import Dict, List, Optional

MM_PER_PT = 25.4 / 72.0
IDLE_MS = 5 * 60 * 1000          # a gap longer than this is the reviewer away
BUBBLE_MOVED_MM = 0.5            # below this a "move" is a nudge of the mouse
FAR_MOVE_MM = 5.0
NUDGE_IOU = 0.3

VALUE_GRADES = ("confirmed", "edited", "phantom")
SUGGESTION_GRADES = ("suggestion_confirmed", "suggestion_edited",
                     "suggestion_deleted", "suggestion_left")


def px_to_mm(px: float, render_scale: float) -> float:
    """render_scale is page-image px per PDF point (campaign.json)."""
    return px / render_scale * MM_PER_PT


def review_time_ms(events: List[dict]) -> int:
    """Active review time: gaps between consecutive events within a page
    session. t_ms restarts at each page load (C1 state 'What's next' 5), so
    a decrease starts a new session and that gap is not counted; t_wall is
    the client's clock and is never used."""
    total, prev = 0, None
    for e in events:
        t = e.get("t_ms")
        if not isinstance(t, (int, float)):
            continue
        if prev is not None and 0 <= t - prev <= IDLE_MS:
            total += t - prev
        prev = t
    return int(total)


def _ratio(n, d):
    return (n / d) if d else None


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def group_metrics(drawings: List[dict]) -> dict:
    """drawings: [{"rows": grade_session rows, "missed": [...], "scale": render_scale,
    "time_ms": int}] for ONE group, gradable drawings only."""
    counts: Dict[str, int] = {g: 0 for g in VALUE_GRADES + SUGGESTION_GRADES}
    counts["missed"] = 0
    flagged = flagged_wrong = 0
    unflagged = {g: 0 for g in VALUE_GRADES}
    moves_mm, moved, far, nudged, repointed, placed = [], 0, 0, 0, 0, 0
    for d in drawings:
        counts["missed"] += len(d["missed"])
        for r in d["rows"]:
            g = r["grade"]
            counts[g] += 1
            if g in VALUE_GRADES:
                if r["flagged"]:
                    flagged += 1
                    flagged_wrong += g != "confirmed"
                else:
                    unflagged[g] += 1
            if r.get("bubble_px") is not None:
                placed += 1
                mm = px_to_mm(r["bubble_px"], d["scale"])
                if mm > BUBBLE_MOVED_MM:
                    moved += 1
                    moves_mm.append(mm)
                    far += mm > FAR_MOVE_MM
            if r.get("anchor_moved"):
                if (r.get("anchor_iou") or 0.0) >= NUDGE_IOU:
                    nudged += 1
                else:
                    repointed += 1
    graded_balloons = sum(counts[g] for g in VALUE_GRADES)
    surviving = counts["confirmed"] + counts["edited"]
    times = [d["time_ms"] / 60000 for d in drawings if d.get("time_ms")]
    return {
        "n_drawings": len(drawings),
        "counts": counts,
        "precision": _ratio(counts["confirmed"], graded_balloons),
        "unflagged_precision": _ratio(unflagged["confirmed"], sum(unflagged.values())),
        "flag_precision": _ratio(flagged_wrong, flagged),
        "recall_lower_bound": _ratio(surviving, surviving + counts["missed"]),
        "placement": {"placed": placed, "bubble_moved": moved,
                      "bubble_moved_share": _ratio(moved, placed),
                      "bubble_mm_median": statistics.median(moves_mm) if moves_mm else None,
                      "bubble_mm_p90": _pct(moves_mm, 0.9),
                      "bubble_far_share": _ratio(far, placed),
                      "anchor_nudged": nudged, "anchor_repointed": repointed},
        "review_minutes_median": statistics.median(times) if times else None,
    }


def reweight(by_group: Dict[str, Optional[float]], pool: Dict[str, int]) -> Optional[float]:
    """Pooled value at the selection POOL's great:problem mix (a 60/40 pick is
    not representative). None if any group lacks the value: an absent group
    would silently re-weight the rest."""
    total = sum(pool.get(g, 0) for g in by_group)
    if not total or any(v is None for v in by_group.values()):
        return None
    return sum(v * pool.get(g, 0) for g, v in by_group.items()) / total
```

Check the expected values against the test before running: precision 3/5, unflagged 2/3 (unflagged rows are confirmed×2 and edited×1), flag precision 1/2.

- [ ] **Step 4: Run and watch pass**, then commit ("eval: campaign metrics — mm, review time, groups, reweighting").

### Task 6: gold cross-check

**Files:**
- Modify: `app/eval/campaign_grade.py`
- Test: `tests/eval/test_campaign_grade.py`

- [ ] **Step 1: Write the failing test** — append:

```python
from app.eval.campaign_grade import gold_cross_check
from app.eval.models import GoldCharacteristic, GoldDoc


def _gold(chars):
    return GoldDoc(doc_id="D1", pdf="x", excel="y", page_rect=(0, 0, 842, 595),
                   characteristics=chars)


def _prow(id, box_pt, nominal="10", **kw):
    s = 300 / 72.0     # px per pt
    return {"id": id, "char_type": "Distance", "nominal": nominal, "upper_tol": "",
            "lower_tol": "", "target_region": [v * s for v in box_pt], "pos": 1,
            "needs_review": False, "suggested": False, **kw}


def test_cross_check_classifies_without_flipping_grades():
    gold = _gold([
        GoldCharacteristic(balloon=1, position_pt=(30, 20), char_type="Distance", nominal="10"),
        GoldCharacteristic(balloon=2, position_pt=(230, 220), char_type="Distance", nominal="7"),
        GoldCharacteristic(balloon=3, position_pt=(500, 400), char_type="Distance", nominal="3"),
    ])
    proposal = [_prow("ok", (20, 10, 40, 30)),                    # matches g1, correct
                _prow("bad", (220, 210, 240, 230), nominal="9"),  # matches g2, wrong
                _prow("ph", (700, 500, 720, 520))]                # no gold near: phantom
    final = proposal[:2]                                          # client deleted "ph"
    grades = {"ok": "confirmed", "bad": "confirmed", "ph": "phantom"}
    out = gold_cross_check(proposal, final, grades, gold,
                           render_scale=300 / 72.0, page_rect=(0, 0, 842, 595))
    assert out["verdicts"] == {"ok": "gold_correct", "bad": "gold_wrong", "ph": "gold_phantom"}
    assert out["matrix"]["gold_wrong&confirmed"] == 1          # client missed an error gold knows
    assert out["matrix"]["gold_phantom&phantom"] == 1
    assert out["neither_found"] == 1                           # g3: no final row near it
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement** — append to `campaign_grade.py`:

```python
from app.eval.dump import to_points
from app.eval.matching import Cand, match_candidates
from app.eval.models import MatchParams
from app.eval.score import _compare_fields
from app.models import Characteristic


def _center_pt(row, render_scale, page_rect):
    b = to_points(row["target_region"], render_scale, page_rect)
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _match(rows, gold_chars, render_scale, page_rect, diag, params):
    rows = [r for r in rows if r.get("target_region") and not r.get("suggested")]
    pairs = match_candidates(
        [Cand(key=r["id"], center_pt=_center_pt(r, render_scale, page_rect),
              nominal=r.get("nominal", "")) for r in rows],
        [Cand(key=g.balloon, center_pt=g.position_pt, nominal=g.nominal)
         for g in gold_chars], diag, params)
    return {pk: gk for pk, gk, _ in pairs}


def gold_cross_check(proposal, final, grades, gold, *, render_scale, page_rect,
                     params: MatchParams = None) -> dict:
    """Gold as a second lens (design §Gold cross-check). Never changes a grade.

    Proposal balloons -> gold verdict (correct / wrong / phantom), crossed with
    the client's grade. Final rows -> which gold rows nobody found. Matching is
    the scorer's own, in PDF points, so 'phantom' means what it means there."""
    params = params or MatchParams()
    gold_chars = [g for g in gold.characteristics if g.kind in params.score_kinds]
    by_num = {g.balloon: g for g in gold_chars}
    diag = math.dist(gold.page_rect[:2], gold.page_rect[2:])
    pm = _match(proposal, gold_chars, render_scale, page_rect, diag, params)
    verdicts, matrix = {}, {}
    for r in proposal:
        if r.get("suggested") or not r.get("target_region"):
            continue
        gk = pm.get(r["id"])
        if gk is None:
            v = "gold_phantom"
        else:
            errs = _compare_fields(Characteristic.model_validate(r), by_num[gk])
            v = "gold_wrong" if errs else "gold_correct"
        verdicts[r["id"]] = v
        key = f"{v}&{grades[r['id']]}"
        matrix[key] = matrix.get(key, 0) + 1
    fm = _match(final, gold_chars, render_scale, page_rect, diag, params)
    return {"verdicts": verdicts, "matrix": matrix,
            "neither_found": len(set(by_num) - set(fm.values())),
            "n_gold": len(gold_chars)}
```

If `Characteristic.model_validate` refuses a proposal row (extra keys like `reviewed`), use `Characteristic.model_validate({k: v for k, v in r.items() if k in Characteristic.model_fields})`. Check `_compare_fields`' attribute access on the gold side.

- [ ] **Step 4: Run and watch pass**, then commit ("eval: cross-check client grades against gold without flipping them").

### Task 7: ranked findings, levers, `grade_campaign`, reconciliation

**Files:**
- Modify: `app/eval/campaign_grade.py`
- Test: `tests/eval/test_campaign_grade.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
import json

from app.eval.campaign_grade import LEVERS, grade_campaign, ranked_findings
from app.eval.campaign_records import read_records
from tests.eval.campaign_fixtures import drawing, proposal_row, write_tar


def test_ranked_findings_by_kind_with_levers():
    per_drawing = [
        {"rows": [graded("edited", fields=["nominal"]), graded("edited", fields=["nominal"]),
                  {**graded("phantom"), "reason": "unballooned_real_dimension"}],
         "missed": [{}], "scale": 300 / 72.0},
        {"rows": [graded("edited", fields=["nominal", "upper_tol"]),
                  graded("confirmed", bubble_px=100.0)], "missed": [], "scale": 300 / 72.0}]
    f = ranked_findings(per_drawing)
    value = f["value"]
    assert value[0]["bucket"] == "fields:nominal" and value[0]["count"] == 2
    assert value[0]["drawings"] == 1 and math.isclose(value[0]["share"], 2 / 5)
    assert {b["bucket"] for b in value} == {"fields:nominal", "fields:nominal+upper_tol",
                                           "phantom:unballooned_real_dimension", "missed"}
    assert f["placement"][0]["bucket"] == "bubble_moved"
    assert all(b["lever"] for b in value)


def _manifest(roles, pool=None):
    return {"runs": [], "roles": roles, "pool": pool or {"great": 30, "problem": 20}}


def test_grade_campaign_end_to_end_and_reconciles(tmp_path):
    p = [proposal_row("a"), proposal_row("b")]
    tar = write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, [], group="great"),
        drawing("bbbbbbbb", p, [proposal_row("a")],
                [{"seq": 1, "type": "delete_row", "id": "b", "reason": "duplicate"}],
                group="problem"),
        drawing("cccccccc", p, p, [], group="problem", approved=False)])
    report, digest = grade_campaign(read_records(tar), gold_by_hash={},
                                    manifest=_manifest({"aaaaaaaa": "great",
                                                        "bbbbbbbb": "problem",
                                                        "cccccccc": "problem",
                                                        "dddddddd": "great"}))
    assert digest["drawings"] == {"approved": 2, "pending": 1, "ungradable": 0,
                                  "skipped": 0, "not_in_records": 1}
    assert digest["groups"]["great"]["counts"]["confirmed"] == 2
    assert digest["groups"]["problem"]["counts"]["phantom"] == 1
    assert digest["reconciles"] is True
    assert digest["pooled"]["precision"] is not None
    blob = json.dumps(digest)
    for forbidden in ('"upper_tol"', '"lower_tol"', '"characteristics"', "pilot-1.pdf"):
        assert forbidden not in blob
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement** — append to `campaign_grade.py`:

```python
from app.review.grading import grade_session

# Correction bucket -> the lever that could move it and that lever's status
# (C1 design §6, CLAUDE.md §2-3). Prefix match, first wins.
LEVERS = [
    ("phantom:unballooned_real_dimension",
     "ballooning-policy classifier learned on train gold", "open (verifier closed)"),
    ("phantom:", "phantom drops / ballooning policy", "stage-2 drops shipped"),
    ("fields:char_type", "read stage (Ø Diameter↔Distance)", "open"),
    ("fields:", "read input / box framing; render_target + adapter retrain for tolerances", "open"),
    ("missed", "detector weights", "open (Rung 3 moved isolated misses)"),
    ("suggestion_", "suggestion tray thresholds (drop stage 2)", "shipped; graded here"),
    ("bubble_moved", "whitespace balloon placement (fa1cb6f)", "new, unmeasured"),
    ("anchor_", "box framing (detect_regions)", "open; framing dominates read accuracy"),
]


def _lever(bucket):
    for prefix, lever, status in LEVERS:
        if bucket.startswith(prefix):
            return lever, status
    return "unmapped", "unmapped"


def _value_bucket(r):
    g = r["grade"]
    if g == "edited":
        return "fields:" + "+".join(r["fields"])
    if g == "phantom":
        return f"phantom:{r.get('reason') or 'none'}"
    if g in ("suggestion_confirmed", "suggestion_edited"):
        return g          # a dropped value the client wanted back
    return None


def ranked_findings(per_drawing: List[dict]) -> dict:
    tables = {"value": {}, "placement": {}}

    def add(kind, bucket, di):
        b = tables[kind].setdefault(bucket, {"count": 0, "drawings": set()})
        b["count"] += 1
        b["drawings"].add(di)

    for di, d in enumerate(per_drawing):
        for r in d["rows"]:
            vb = _value_bucket(r)
            if vb:
                add("value", vb, di)
            if r.get("bubble_px") is not None and px_to_mm(r["bubble_px"], d["scale"]) > BUBBLE_MOVED_MM:
                add("placement", "bubble_moved", di)
            if r.get("anchor_moved"):
                add("placement", "anchor_nudged" if (r.get("anchor_iou") or 0) >= NUDGE_IOU
                    else "anchor_repointed", di)
        for _ in d["missed"]:
            add("value", "missed", di)
    out = {}
    for kind, t in tables.items():
        total = sum(b["count"] for b in t.values())
        rows = []
        for bucket, b in t.items():
            lever, status = _lever(bucket)
            rows.append({"bucket": bucket, "count": b["count"],
                         "share": b["count"] / total if total else None,
                         "drawings": len(b["drawings"]), "lever": lever,
                         "lever_status": status})
        out[kind] = sorted(rows, key=lambda r: (-r["count"], r["bucket"]))
    return out


def grade_campaign(records, gold_by_hash: Dict, manifest: dict):
    """-> (report, digest). `report` holds per-row grades and values: it goes
    ONLY inside the protected root. `digest` is counts + salted hashes."""
    roles = manifest.get("roles", {})
    pool = manifest.get("pool", {})
    status = {"approved": 0, "pending": 0, "ungradable": 0,
              "skipped": len(records.skipped), "not_in_records": 0}
    by_group: Dict[str, List[dict]] = {}
    report_docs, gold_rows, problems = [], [], []
    for h, d in sorted(records.drawings.items()):
        facts = d.header.get("campaign", {})
        group = facts.get("group") or d.index.get("group") or "unknown"
        if roles and roles.get(h) not in (None, group):
            problems.append(f"group_differs:{h}")
        if d.sealed is None:
            status["pending"] += 1
            continue
        g = grade_session(d.proposal_rows, d.sealed)
        if len(g["rows"]) != len(d.proposal_rows):
            problems.append(f"partition:{h}")
        if g["ungradable"]:
            status["ungradable"] += 1
            report_docs.append({"doc_hash": h, "group": group, "ungradable": g["ungradable"]})
            continue
        status["approved"] += 1
        entry = {"doc_hash": h, "group": group, "rows": g["rows"], "missed": g["missed"],
                 "scale": facts["render_scale"], "time_ms": review_time_ms(d.events),
                 "source_run": facts.get("source_run")}
        by_group.setdefault(group, []).append(entry)
        gold = gold_by_hash.get(h)
        cross = None
        if gold is not None:
            grades = {r["id"]: r["grade"] for r in g["rows"]}
            cross = gold_cross_check(d.proposal_rows, d.sealed["rows"], grades, gold,
                                     render_scale=facts["render_scale"],
                                     page_rect=tuple(facts["page_rect"]))
            gold_rows.append(cross)
        report_docs.append({**entry, "proposal": d.proposal_rows,
                            "final": d.sealed["rows"], "cross": cross,
                            "has_note": any(e["type"] == "set_note" and e.get("text")
                                            for e in d.sealed.get("net_events", []))})
    seen = set(records.drawings) | {s.get("doc_hash") for s in records.skipped}
    status["not_in_records"] = len([h for h in roles if h not in seen])
    groups = {grp: group_metrics(ds) for grp, ds in sorted(by_group.items())}
    pooled = {k: reweight({grp: m[k] for grp, m in groups.items()}, pool)
              for k in ("precision", "unflagged_precision", "flag_precision",
                        "recall_lower_bound")} if groups else {}
    matrix: Dict[str, int] = {}
    for c in gold_rows:
        for k, v in c["matrix"].items():
            matrix[k] = matrix.get(k, 0) + v
    all_docs = [e for ds in by_group.values() for e in ds]
    digest = {
        "campaign": records.campaign,
        "drawings": status,
        "groups": groups,
        "pooled": pooled,
        "pool": pool,
        "gold_cross_check": {"n_drawings": len(gold_rows), "matrix": matrix,
                             "neither_found": sum(c["neither_found"] for c in gold_rows)},
        "findings": ranked_findings(all_docs),
        "notes_written": sum(1 for d in report_docs if d.get("has_note")),
        "by_source_run": {r: sum(1 for e in all_docs if e["source_run"] == r)
                          for r in sorted({e["source_run"] for e in all_docs})},
        "reconcile_problems": problems,
        "reconciles": not problems,
    }
    report = {"campaign": records.campaign, "drawings": report_docs, "digest": digest}
    return report, digest
```

**Reconciliation:** grades partition the proposal rows (checked per drawing). The manifest coverage is the `not_in_records` count. A group disagreement is `group_differs`. Make sure `digest` holds no per-row values. `fields:` signatures are prefixed keys and safe for the hook. `by_source_run` keys are run names, which are not client data.

- [ ] **Step 4: Run and watch pass**, then commit ("eval: ranked findings, levers and the campaign grade").

### Task 8: `share_select` records the eligible pool

**Files:**
- Modify: `app/eval/share_select.py` (`select`, `manifest`, `main`)
- Test: `tests/eval/test_share_select.py`

- [ ] **Step 1: Write the failing test**:

```python
def test_manifest_records_the_eligible_pool_for_reweighting():
    from app.eval.share_select import eligible_counts, manifest
    rows = [{"run": "r", "doc": f"{i:08x}", "n_gold": 20, "n_pred": 20,
             "recall": rec, "counts": {"escaped_error": 0}}
            for i, rec in enumerate([0.9, 0.8, 0.6, 0.4, 0.35, 0.1])]
    pool = eligible_counts(rows)
    assert pool == {"great": 3, "problem": 2}
    assert manifest([], pool)["pool"] == pool
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement.**
  - Factor the eligibility filters in `select()` into a helper used by both, so they can never drift.
  - Add `eligible_counts(rows, min_gold=15, great_recall=0.55, problem_recall_min=0.3, max_escaped=0.1) -> {"great": n, "problem": n}`, using the same `ok` filter and the same thresholds.
  - Give `manifest(picks, pool=None)` a `"pool"` key when `pool` is given.
  - In `main()`, pass `eligible_counts(rows, ...)` with the CLI thresholds.
  - Keep the existing manifest tests passing; `pool` is optional.

- [ ] **Step 4: Run and watch pass** (`python -m pytest tests/eval/test_share_select.py -q`), then commit ("eval: the campaign manifest records the eligible pool").

### Task 9: `score --reviewer-grade` in the runner

**Files:**
- Modify: `app/eval/runner.py` (score parser ~1065; `_cmd_score` ~630)
- Test: `tests/eval/test_runner_reviewer_grade.py`

- [ ] **Step 1: Write the failing tests**:

```python
"""score --reviewer-grade (C2 design §Outputs): the agent-safe surface is one
stdout line and the --grade-out digest; the full report must sit inside a
protected root."""
import json

from app.eval import runner
from tests.eval.campaign_fixtures import drawing, proposal_row, write_tar


def _args(tmp_path, tar, manifest, out, grade_out):
    return ["score", "--reviewer-grade", str(tar), "--gold", str(tmp_path / "gold"),
            "--manifest", str(manifest), "--name", "pilot-1",
            "--out", str(out), "--grade-out", str(grade_out)]


def test_reviewer_grade_writes_digest_and_report(tmp_path, monkeypatch, capsys):
    (tmp_path / "gold").mkdir()
    protected = tmp_path / "prot"
    protected.mkdir()
    monkeypatch.setattr("app.eval.review._protected_roots", lambda: [protected])
    p = [proposal_row("a")]
    tar = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])])
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"runs": [], "roles": {"aaaaaaaa": "great"},
                               "pool": {"great": 1, "problem": 1}}))
    out, gout = protected / "pilot-1.grade.json", tmp_path / "digest.json"
    assert runner.main(_args(tmp_path, tar, man, out, gout)) in (0, None)
    digest = json.loads(gout.read_text())
    assert digest["drawings"]["approved"] == 1 and out.is_file()
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith("reviewer-grade:") and "approved=1" in line


def test_reviewer_grade_refuses_a_report_outside_the_protected_root(tmp_path, monkeypatch):
    (tmp_path / "gold").mkdir()
    monkeypatch.setattr("app.eval.review._protected_roots", lambda: [tmp_path / "prot"])
    tar = write_tar(tmp_path / "r.tar", [])
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"runs": [], "roles": {}}))
    assert runner.main(_args(tmp_path, tar, man, tmp_path / "leak.json",
                             tmp_path / "d.json")) == 1
    assert not (tmp_path / "leak.json").exists()
```

Check first how `runner.main` is invoked in existing runner tests (`grep -n "runner.main\|def main" app/eval/runner.py tests/eval/*.py`) and adapt the call. Also check how `check_deck_path` / `_protected_roots` decide "inside a protected root", and patch the right symbol.

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement.**
  - **Parser:** `p.add_argument("--run", required=False)`. Add `--reviewer-grade` (path to the pull tar), `--manifest` and `--grade-out`, with help text saying the report holds client values and must sit inside the protected root.
  - **`_cmd_score`, first lines:**

```python
    if getattr(args, "reviewer_grade", None):
        return _cmd_reviewer_grade(args)
    if not args.run:
        print("ERROR: --run is required (except with --reviewer-grade)", file=sys.stderr)
        return 1
```

  - **New function:**

```python
def _cmd_reviewer_grade(args):
    """Grade a pulled review campaign (C2 design). Client-primary; gold is a
    cross-check. The full report (per-row values) may only be written inside
    a protected root; what reaches stdout and --grade-out is counts + salted
    hashes, the agent-safe surface."""
    from app.eval.campaign_grade import grade_campaign
    from app.eval.campaign_records import read_records
    from app.eval.review import ReviewRefused, check_deck_path
    if not args.manifest:
        print("ERROR: --reviewer-grade needs --manifest (roles + pool)", file=sys.stderr)
        return 1
    try:
        check_deck_path(args.out)
    except ReviewRefused as e:
        print(f"ERROR: --out refused: {e}", file=sys.stderr)
        return 1
    records = read_records(args.reviewer_grade)
    gold = _load_gold_dir(args.gold)
    by_hash = _anon(args).mapping(gold)
    gold_by_hash = {h: gold[d] for h, d in by_hash.items() if h in records.drawings}
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report, digest = grade_campaign(records, gold_by_hash, manifest)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.grade_out:
        Path(args.grade_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.grade_out).write_text(json.dumps(digest, indent=1), encoding="utf-8")
    s = digest["drawings"]
    print(f"reviewer-grade: approved={s['approved']} pending={s['pending']} "
          f"ungradable={s['ungradable']} skipped={s['skipped']} "
          f"not_in_records={s['not_in_records']} reconciles={digest['reconciles']}")
    return 0
```

  - **Check `check_deck_path`'s behaviour:** it may require a specific suffix, or refuse an existing file. If it does not fit a report path, add a sibling `check_report_path` in `app/eval/review.py` that checks only "inside a protected root" and test it.
  - **`--show-ids`:** `_anon(args)` honours it. With it the mapping keys are real ids, which would never match the 8-hex hashes, so refuse `--show-ids` together with `--reviewer-grade`.

- [ ] **Step 4: Run and watch pass**: `python -m pytest tests/eval/test_runner_reviewer_grade.py tests/eval -q`.

- [ ] **Step 5: Guard check**: `bash ~/.claude/hooks/test-sindri-guard.sh` → 32/0 (unchanged; this is an existing allowlisted subcommand).

- [ ] **Step 6: Commit** ("eval: score --reviewer-grade grades a pulled campaign").

> **Milestone: campaign analysis** — state must be checkpointed here before continuing.

---

## Milestone C — operator drill-down

### Task 10: choose the examples per bucket

**Files:**
- Create: `app/eval/campaign_deck.py`
- Test: `tests/eval/test_campaign_deck.py`

- [ ] **Step 1: Write the failing test**:

```python
from app.eval.campaign_deck import examples


def test_examples_pick_the_top_rows_per_bucket_with_their_boxes():
    prop = {"id": "a", "nominal": "10", "target_region": [10, 10, 50, 30],
            "balloon_xy": [100, 100]}
    fin = {**prop, "nominal": "12", "balloon_xy": [400, 100]}
    report = {"drawings": [{
        "doc_hash": "aaaaaaaa", "group": "great", "scale": 300 / 72.0,
        "rows": [{"id": "a", "grade": "edited", "fields": ["nominal"], "reason": "misread_digit",
                  "bubble_px": 300.0, "anchor_iou": 1.0, "anchor_moved": False}],
        "missed": [], "proposal": [prop], "final": [fin], "cross": None}]}
    ex = examples(report, per_bucket=5)
    assert [e["row_id"] for e in ex["fields:nominal"]] == ["a"]
    e = ex["fields:nominal"][0]
    assert e["proposed"]["nominal"] == "10" and e["final"]["nominal"] == "12"
    assert e["reason"] == "misread_digit" and e["doc_hash"] == "aaaaaaaa"
    assert ex["bubble_moved"][0]["mm"] > 20
```

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement** `examples(report, per_bucket=12) -> {bucket: [example]}`.
  - Reuse `campaign_grade._value_bucket` and the placement rules; import them.
  - Each example: `doc_hash`, `group`, `row_id`, `grade`, `reason`, `proposed` (the proposal row dict), `final` (the final row or None), `mm` (bubble displacement or None), `scale`.
  - **Ordering:** placement buckets by `mm` descending; value buckets in report order.
  - Missed rows come from `missed`, with `proposed=None`.

- [ ] **Step 4: Run and watch pass**, then commit.

### Task 11: the deck server and `runner campaign-deck`

**Files:**
- Modify: `app/eval/campaign_deck.py`, `app/eval/runner.py`
- Test: `tests/eval/test_campaign_deck.py`

- [ ] **Step 1: Write the failing tests.** Copy the structure of the existing review-server tests (`grep -rln "review_server" tests/`):
  - a request without the token gets 403;
  - with the token, `/api/examples` returns the buckets;
  - `/crop?doc=<hash>&row=<id>` returns a PNG for the synthetic fixture drawing.
  
  Build the drawing in the test by writing `tests/fixtures/sample.pdf`'s bytes to `tmp_path / f"{doc_id}.pdf"`, where `doc_id` is any stem whose salted hash the test computes with `Anonymizer(salt="t")`.

- [ ] **Step 2: Run and watch fail.**

- [ ] **Step 3: Implement.** `CampaignDeckApp(report_path, pdfs_dir, salt=None)`:
  - It loads the report, refusing it unless it is inside a protected root (`inside_protected`).
  - It resolves `doc_hash` to a PDF by hashing each stem in `pdfs_dir` with `Anonymizer`.
  - **The crop:**
    - the rect is the union of the proposed and final boxes plus both bubbles;
    - px are converted to points with `scale` and the page origin (`review_crops.page_rect`), with 40 pt of padding;
    - it calls `render_crop(pdf, rect, pred_box_pt=proposed box in points, gold_pt=final bubble in points)`;
    - it falls back to `placeholder_png` with a fixed sentence on any `CropError`.
  
  The handler copies review_server's security exactly: bind to 127.0.0.1 only, a token on every request (`hmac.compare_digest`), `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and no request logging. Serve one inline HTML page: a bucket list on the left, example cards (crop, proposed→final values, reason, note flag) on the right. `serve(report, pdfs, port=0)` prints the URL with the token, like `review-serve`.

  Add the runner subcommand `campaign-deck --report --pdfs [--port]`, wired like `review-serve` (`_cmd_campaign_deck`). **Do not add it to the guard allowlist**: it is the operator's, it reads client values, and an agent must never start it or accept its link.

- [ ] **Step 4: Run and watch pass**; also run `bash ~/.claude/hooks/test-sindri-guard.sh` → 32/0. Commit ("eval: campaign-deck — the operator's drill-down into graded corrections").

> **Milestone: drill-down** — state must be checkpointed here before continuing.

---

## Milestone D — wiring it up

### Task 12: seam test, docs, full verification

**Files:**
- Create: `tests/eval/test_campaign_c2_seam.py`
- Modify: `CLAUDE.md` (§2, after the C1 paragraph), `docs/deploy/vps.md` ("Get the records back" → add the grading step)

- [ ] **Step 1: Seam test.** Extend the C1 seam chain:

  `campaign_build.build_entry` (synthetic dump + sample fixture) → `app.campaign.load.load_bundle` → `Worker.run_once` → seal one drawing through `ReviewStore` → `app.campaign.pull.pull_campaign` → `read_records` → `grade_campaign` (with a synthetic GoldDoc mapped through a test salt).

  Assert that `approved == 1`, `reconciles` is True, and that the grade of each proposal row is `confirmed` for an untouched session. Reuse `tests/campaign/test_seam.py`'s setup; read it first.

- [ ] **Step 2: Docs.**
  - **CLAUDE.md:** one paragraph naming:
    - `score --reviewer-grade` (agent-safe: digest + one line);
    - the report inside the protected root;
    - `campaign-deck` as the operator's;
    - client-primary grading with gold as the cross-check;
    - the reweighting to `manifest.pool`.
  - **vps.md:** after pulling, the agent runs `score --reviewer-grade <protected tar> --gold <protected gold> --manifest ~/campaign-manifest.json --name pilot-1 --out <protected root>/reports/pilot-1.grade.json --grade-out docs/eval/pilot-1-grade.json`. The operator optionally runs `campaign-deck`. Use `<protected root>` placeholders; never the literal path.

- [ ] **Step 3: Full verification:**
  - `python -m pytest -q`: all pass. The 2 `title_block` tests pass on a checkout that has the root sample drawing.
  - `node --test "tests/js/*.test.mjs"`: 298 pass.
  - guard: 32/0.

- [ ] **Step 4: Commit** ("docs: grading a campaign (C2)"), then checkpoint the state file.

> **Milestone: C2 complete** — state must be checkpointed here.
