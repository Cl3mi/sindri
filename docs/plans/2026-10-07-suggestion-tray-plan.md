# Suggestion Tray Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rows dropped by drop stage 2 and later appear in the reviewer's existing table and drawing, highlighted violet. They are edited in place, become balloons only when confirmed, and are never exported unconfirmed.

**Architecture:** The pipeline splits drops into "deleted" (stage 1, duplicates) and "suggested" (stages ≥ 2), and returns the latter in a new `ExtractionResult.suggestions`, which the scorer never reads. The API merges them into `rows` with `suggested: true`. The UI renders that flag, and both the UI and the server keep suggested rows out of every export.

**Tech Stack:** Python 3 / pydantic / FastAPI (TestClient), vanilla ES modules, CSS custom properties. Spec: `docs/plans/2026-10-07-suggestion-tray-design.md`.

**Guard rules:** single bare commands; never open anything under the protected root; code that names dump files goes through the file tools, not heredocs.

---

## File map

| file | change |
|---|---|
| `app/models.py` | `Characteristic.suggested: bool = False`; `ExtractionResult.suggestions` |
| `app/pipeline/policy_rules.py` | `split_drop_stages`; `apply_drop_stages` delegates to it |
| `app/pipeline/extract.py` | keep stage ≥ 2 drops as suggestions; number and place kept rows first |
| `app/eval/reapply.py` | start from `characteristics + suggestions`; emit suggestions |
| `app/main.py` | payload merges suggestions; both exports filter `suggested` |
| `app/static/styles/tokens.css`, `components.css` | violet tokens, row and marker styles |
| `app/static/index.html` | fourth filter pill |
| `app/static/js/state.js` | renumber skips suggestions; filter; counts; `opConfirmSuggestions` |
| `app/static/js/table.js` | row class, pos cell, count, bulk-accept rule |
| `app/static/js/viewer.js` | marker class and label |
| `app/static/js/shortcuts.js` | key `4`; `Y` rule |
| `app/static/js/main.js` | export sends non-suggested rows |

---

### Task 1: `split_drop_stages` and the model fields

**Files:** Modify `app/pipeline/policy_rules.py`, `app/models.py`. Test `tests/test_policy_rules.py`, `tests/test_models.py`.

- [ ] **Step 1: Failing tests** (append to `tests/test_policy_rules.py`)

```python
def test_split_drop_stages_keeps_what_apply_keeps_and_names_each_stage():
    """The tray needs to know WHICH stage dropped a row: stage 1 deletes a
    duplicate, later stages make a suggestion. `kept` must be exactly
    apply_drop_stages's, or the shipped behaviour would drift."""
    outer = _c(pos=1, box=(0, 0, 200, 60), kind="dimension", raw_text="20",
               nominal="20", confidence=0.995)
    inner = _c(pos=2, box=(60, 10, 140, 50), kind="dimension", raw_text="20",
               nominal="20", confidence=0.99)
    low = _c(pos=3, box=(500, 0, 600, 40), kind="dimension", raw_text="7",
             nominal="7", confidence=0.9)
    kept, dropped = pr.split_drop_stages([outer, inner, low],
                                         pr.ACTIVE_DROP_STAGES)
    assert [c.pos for c in kept] == [1]
    assert [[c.pos for c in s] for s in dropped] == [[2], [3]]
    assert [c.pos for c in pr.apply_drop_stages([outer, inner, low],
                                                pr.ACTIVE_DROP_STAGES)] == [1]
```

Append to `tests/test_models.py`:

```python
def test_suggestion_fields_default_to_nothing_so_old_dumps_load_unchanged():
    from app.models import Characteristic, ExtractionResult
    assert Characteristic(pos=1).suggested is False
    assert ExtractionResult(characteristics=[]).suggestions == []
```

- [ ] **Step 2:** `python -m pytest -q tests/test_policy_rules.py tests/test_models.py` → FAIL (`split_drop_stages` and the fields are missing).
- [ ] **Step 3: Implement.** In `policy_rules.py` replace `apply_drop_stages` with:

```python
def split_drop_stages(chars, stages):
    """(kept, [rows dropped by stage 0, by stage 1, ...]). Each stage is judged
    on what the previous one kept, exactly as it was priced."""
    dropped = []
    for names in stages:
        kept = apply_drop_rules(chars, names)
        kept_ids = {id(c) for c in kept}
        dropped.append([c for c in chars if id(c) not in kept_ids])
        chars = kept
    return chars, dropped


def apply_drop_stages(chars, stages):
    """Each stage applied to what the previous one kept -- order-independent
    WITHIN a stage, ordered ACROSS stages, exactly as each stage was priced."""
    return split_drop_stages(chars, stages)[0]
```

In `app/models.py`, add to `Characteristic` after `verifier_p`:

```python
    # True for a row a drop stage >= 2 removed (low confidence etc.): shown to
    # the reviewer, never exported until confirmed. The API and the UI carry
    # it; the scorer never sees such rows (they live in
    # ExtractionResult.suggestions, not characteristics).
    suggested: bool = False
```

and to `ExtractionResult` after `marks`:

```python
    # Rows removed by drop stages >= 2, kept for the reviewer's tray. NOT
    # characteristics: scoring, delivered precision and exports read
    # `characteristics` only (docs/plans/2026-10-07-suggestion-tray-design.md).
    suggestions: List[Characteristic] = []
```

- [ ] **Step 4:** Rerun → PASS. Then the full suite → all pass.
- [ ] **Step 5:** Commit: `feat(policy): split_drop_stages; Characteristic.suggested, ExtractionResult.suggestions`.

### Task 2: extract keeps stage ≥ 2 drops as suggestions

**Files:** Modify `app/pipeline/extract.py` (`_apply_active_policy` and its call site). Test `tests/test_extract_policy.py`.

- [ ] **Step 1: Failing tests**

```python
def test_stage_two_drops_become_suggestions_and_stage_one_drops_vanish():
    from app.pipeline.extract import _policy_with_suggestions
    outer = Characteristic(pos=0, kind="dimension", raw_text="20 ±0,1",
                           nominal="20", upper_tol="0,1", lower_tol="-0,1",
                           confidence=0.995, target_region=(0, 0, 200, 60))
    dup = outer.model_copy(update={"confidence": 0.99,
                                   "target_region": (60, 10, 140, 50)})
    low = outer.model_copy(update={"confidence": 0.9,
                                   "target_region": (500, 0, 600, 40)})
    kept, suggestions = _policy_with_suggestions([outer, dup, low])
    assert kept == [outer]
    assert suggestions == [low] and low.suggested is True
    assert "low confidence — not ballooned; confirm to add" in low.review_reasons
    assert dup not in suggestions


def test_apply_active_policy_is_unchanged_for_its_callers():
    a = Characteristic(pos=0, kind="dimension", raw_text="20 ±0,1",
                       nominal="20", upper_tol="0,1", lower_tol="-0,1",
                       confidence=0.9, target_region=(0, 0, 10, 10))
    assert _apply_active_policy([a]) == []
```

- [ ] **Step 2:** Run → FAIL (`_policy_with_suggestions` is missing).
- [ ] **Step 3: Implement** in `extract.py`:

```python
SUGGESTION_REASON = "low confidence — not ballooned; confirm to add"


def _policy_with_suggestions(results, drop_stages=None):
    """(kept, suggestions): the active policy, with rows dropped by stage 2 or
    later KEPT ASIDE for the reviewer instead of deleted. Stage 1 removes
    duplicates of a balloon, which are not suggestions."""
    for c in results:
        extra = pr.apply_flag_rules(c, pr.ACTIVE_FLAG_RULES)
        if extra:
            c.needs_review = True
            c.review_reasons = [*c.review_reasons, *extra]
    stages = pr.ACTIVE_DROP_STAGES if drop_stages is None else drop_stages
    kept, dropped = pr.split_drop_stages(results, stages)
    suggestions = [c for stage in dropped[1:] for c in stage]
    for c in suggestions:
        c.suggested = True
        c.pos = 0
        c.review_reasons = [*c.review_reasons, SUGGESTION_REASON]
    return kept, suggestions


def _apply_active_policy(results, drop_stages=None):
    """(docstring unchanged)"""
    return _policy_with_suggestions(results, drop_stages)[0]
```

At the call site:

```python
    results, suggestions = _policy_with_suggestions(results)

    emit("place", "Placing balloons")
    number_characteristics(results)
    place_balloons(results, dpi=render.dpi)
    # Suggestions are placed AFTER the real balloons, in their own pass, so
    # they can never push a real balloon away from its callout.
    place_balloons(suggestions, dpi=render.dpi)
```

and pass `suggestions=suggestions` to `ExtractionResult(...)`.

- [ ] **Step 4:** Run → PASS, then the full suite.
- [ ] **Step 5:** Commit: `feat(extract): stage >= 2 drops become reviewer suggestions`.

### Task 3: measurement stays exact

**Files:** Modify `app/eval/reapply.py`. Test `tests/eval/test_reapply.py`, `tests/eval/test_score.py` (or the file that holds score tests).

- [ ] **Step 1: Failing tests**

```python
def test_suggestions_never_reach_the_scorer():
    """Scoring reads characteristics only; a dump with suggestions scores
    exactly as without them."""
    from app.eval.models import GoldCharacteristic, GoldDoc, MatchParams, ReviewCostWeights
    from app.eval.score import score_doc
    c = Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", upper_tol="0,1", lower_tol="-0,1",
                       raw_text="20 ±0,1", confidence=0.995,
                       target_region=(400, 400, 520, 440))
    s = c.model_copy(update={"pos": 0, "suggested": True,
                             "target_region": (900, 900, 1000, 940)})
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[GoldCharacteristic(balloon=1, position_pt=(110, 101),
                                                       char_type="Distance",
                                                       nominal="20")])
    plain = _dump([c])
    with_s = _dump([c]); with_s.result.suggestions = [s]
    w, p = ReviewCostWeights(), MatchParams()
    a = score_doc(plain, gold, w, p)
    b = score_doc(with_s, gold, w, p)
    assert a.model_dump() == b.model_dump()


def test_reapply_starts_from_characteristics_plus_suggestions():
    """A dump predicted WITH the tray holds stage-2 drops in suggestions;
    reapplying the policy must see the same pre-drop set the pipeline saw."""
    keep = Characteristic(pos=1, kind="dimension", char_type="Distance",
                          nominal="20", upper_tol="0,1", lower_tol="-0,1",
                          raw_text="20 ±0,1", confidence=0.995,
                          target_region=(0.0, 0.0, 100.0, 40.0))
    sugg = keep.model_copy(update={"pos": 0, "confidence": 0.9, "suggested": True,
                                   "target_region": (500.0, 0.0, 600.0, 40.0)})
    d = _dump([keep]); d.result.suggestions = [sugg]
    out = reapply_current_code(d)
    assert [c.pos for c in out.result.characteristics] == [1]
    assert len(out.result.suggestions) == 1
    assert out.result.suggestions[0].suggested is True
```

- [ ] **Step 2:** Run → the second FAILS (reapply ignores suggestions); the first may already PASS, which is the invariant pinned.
- [ ] **Step 3: Implement** in `reapply.py`: iterate over `dump.result.characteristics + dump.result.suggestions` when building `new_chars`, with each fresh row's `suggested` reset to False. On the non-fill path:

```python
    if not fill:
        kept, suggestions = _policy_with_suggestions(new_chars, stages)
        dump.result.characteristics = kept
        dump.result.suggestions = suggestions
        return dump
```

Import `_policy_with_suggestions` from `app.pipeline.extract`.
- [ ] **Step 4:** Run → PASS, then the full suite.
- [ ] **Step 5:** Commit: `feat(eval): suggestions never scored; reapply round-trips them`.

### Task 4: API payload and export guard

**Files:** Modify `app/main.py`. Test `tests/test_api.py`.

- [ ] **Step 1: Failing tests**

```python
def test_extract_payload_merges_suggestions_flagged(monkeypatch, sample_pdf):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import Characteristic, ExtractionResult
    monkeypatch.setattr(main, "extract", lambda *a, **kw: ExtractionResult(
        characteristics=[Characteristic(pos=1, id="a", nominal="20")],
        suggestions=[Characteristic(pos=0, id="b", nominal="7",
                                    suggested=True)]))
    data = upload_pdf(TestClient(main.app), sample_pdf, filename="x.pdf")
    assert [(r["id"], r["suggested"]) for r in data["rows"]] == \
        [("a", False), ("b", True)]


@pytest.mark.parametrize("endpoint", ["/api/export", "/api/export/pdf"])
def test_exports_drop_unconfirmed_suggestions_server_side(endpoint, sample_pdf,
                                                          stub_backend, monkeypatch):
    """Defence in depth: even if a client sends a suggested row, it is not
    exported -- an unconfirmed value must never reach the client's files."""
    import app.main as main
    seen = {}
    monkeypatch.setattr(main, "write_workbook",
                        lambda rows, out, **kw: (seen.setdefault("rows", rows),
                                                 out.write_bytes(b"x")))
    monkeypatch.setattr(main, "render_ballooned_pdf",
                        lambda src, rows, out, **kw: (seen.setdefault("rows", rows),
                                                      out.write_bytes(b"%PDF")))
    up = upload_pdf(client, sample_pdf)
    rows = up["rows"] + [{**up["rows"][0], "id": "s", "suggested": True}]
    r = client.post(endpoint, json={"session_id": up["session_id"], "rows": rows})
    assert r.status_code == 200
    assert all(not row.suggested for row in seen["rows"])
    assert len(seen["rows"]) == len(up["rows"])
```

(Check the exact `render_ballooned_pdf` call signature in `export_pdf` first, and make the stub accept it.)

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** in `main.py`. The payload's `rows` becomes:

```python
                "rows": [r.model_dump() for r in
                         [*result.characteristics, *result.suggestions]],
```

Add a helper used by both export endpoints before writing:

```python
def _exportable(rows):
    """Confirmed rows only. The UI already omits unconfirmed suggestions;
    this is the server-side guarantee, because an unconfirmed value in the
    client's Excel is exactly the failure the drops exist to prevent."""
    return [r for r in rows if not r.suggested]
```

and pass `_exportable(req.rows)` wherever `req.rows` is used in `export` and `export_pdf`.
- [ ] **Step 4:** Run → PASS, then the full suite.
- [ ] **Step 5:** Commit: `feat(api): suggestions in the payload; exports never include unconfirmed ones`.

### Task 5: UI — tokens, styles, pill

**Files:** `app/static/styles/tokens.css`, `app/static/styles/components.css`, `app/static/index.html`.

- [ ] **Step 1:** In `tokens.css`, under accent:

```css
  --suggest:    #a371f7;                  /* low confidence, not ballooned */
  --suggest-bg: rgba(163, 113, 247, 0.12);
```

- [ ] **Step 2:** In `components.css`:

```css
/* Low-confidence suggestions: not ballooned, not exported until confirmed.
   Violet, never amber -- amber means "needs review" on a row that IS exported. */
table.inspection tbody tr.suggested { background: var(--suggest-bg);
  box-shadow: inset 2px 0 0 var(--suggest); }
table.inspection tbody tr.suggested td { color: color-mix(in srgb, var(--suggest) 45%, var(--fg)); }
table.inspection tr.suggested td.pos .pos-num { color: var(--suggest); }
#marker-layer .marker.suggested { border: 2px dashed var(--suggest);
  color: var(--suggest); background: var(--suggest-bg); }
```

- [ ] **Step 3:** In `index.html`, after the OK pill:

```html
          <button data-filter="suggested" class="suggest">Low confidence <span class="count" id="cnt-suggested">0</span></button>
```

- [ ] **Step 4:** Commit: `feat(ui): violet suggestion tokens, styles and filter pill`.

### Task 6: UI — state, table, viewer, shortcuts, export

**Files:** `state.js`, `table.js`, `viewer.js`, `shortcuts.js`, `main.js`.

- [ ] **Step 1: `state.js`**
  - `setSession` keeps `suggested` (rows already carry it).
  - `renumber()`: sort as today, then number only confirmed rows:

```js
  let n = 0;
  state.rows.forEach((r) => (r.pos = r.suggested ? 0 : ++n));
```

  - `isVisibleRow`: `if (state.filter === 'suggested' && !r.suggested) return false;` For 'review' and 'ok', exclude suggestions: `if (state.filter !== 'all' && state.filter !== 'suggested' && r.suggested) return false;`
  - `counts()` returns `suggested` too, and `review`/`ok` count confirmed rows only.
  - New op:

```js
export function opConfirmSuggestions(ids) {
  return {
    label: 'confirm suggestions',
    do() {
      for (const id of ids) {
        const r = state.rows.find((x) => x.id === id);
        if (r && r.suggested) { r.suggested = false; r.reviewed = true; r._wasSuggested = true; }
      }
      renumber();
    },
    undo() {
      for (const id of ids) {
        const r = state.rows.find((x) => x.id === id);
        if (r && r._wasSuggested) { r.suggested = true; r.reviewed = false; delete r._wasSuggested; }
      }
      renumber();
    },
  };
}
```

- [ ] **Step 2: `table.js`**
  - In `renderRows`: `if (r.suggested) tr.classList.add('suggested');`, and the pos cell shows `◌ –` (`num.textContent = r.suggested ? '◌ –' : r.pos;`).
  - `renderCounts`: set `cnt-suggested`.
  - `bindBulkAccept`: split the target ids. Selected suggested rows (explicit checkboxes), or all visible suggestions when `state.filter === 'suggested'`, go through `opConfirmSuggestions`. Otherwise suggestions are excluded from "Accept all". Bundle both ops when both apply:

```js
    const ids = targetIds;
    const sugg = ids.filter((id) => state.rows.find((r) => r.id === id)?.suggested);
    const plain = ids.filter((id) => !sugg.includes(id));
    const allowSugg = selectedIds.length > 0 || state.filter === 'suggested';
    if (allowSugg && sugg.length) apply(opConfirmSuggestions(sugg));
    if (plain.length) apply(opBulkReview(plain, true));
```

- [ ] **Step 3: `viewer.js` `renderMarkers`:** `m.classList.toggle('suggested', !!r.suggested);` and the label shows `r.suggested ? '?' : r.pos`. The `review` class applies only to non-suggested rows.
- [ ] **Step 4: `shortcuts.js`:** `if (e.key === '4') clickFilter('suggested');`. In the `Y` handler, keep suggestions out unless the filter is `'suggested'`; inside it, use `opConfirmSuggestions` on the visible suggestions.
- [ ] **Step 5: `main.js`:** both exports send `rows: state.rows.filter((r) => !r.suggested)`.
- [ ] **Step 6: Browser check** on the synthetic sample drawing, with a stub backend whose reads return confidence 0.9 for one box (so it becomes a suggestion). Check, and screenshot:
  - the violet row with `◌ –`, and the dashed violet `?` marker;
  - the pill count;
  - an inline edit of the suggestion;
  - `Y` on All leaving it unconfirmed;
  - `4` then `Y` confirming it (it gets a number, the marker turns normal);
  - undo restoring it;
  - an export with one unconfirmed suggestion: the Excel row count equals the confirmed rows.
- [ ] **Step 7:** Commit: `feat(ui): suggestions in the table and on the drawing; confirm, dismiss, export rule`.

### Task 7: exactness on measured runs and docs

- [ ] **Step 1:** Re-score `r5-control` (dev) and `r5-controltest` (test) through the new code with plain `score`, write the summaries to a scratch name, and compare with `docs/eval/r5control*-summary.json`: identical on every aggregate except `run`. Those dumps predate the tray, so nothing they score may move.
- [ ] **Step 2:** Update CLAUDE.md §2 (one paragraph) and memory; commit; push to `main` and the branch.

---

## Self-review

* Spec coverage: §1 pipeline → Tasks 1-2; §2 measurement → Task 3 and Task 7; §3 API → Task 4; §4 UI → Tasks 5-6; §5 verification → Task 6 step 6 and Task 7.
* Names: `split_drop_stages`, `_policy_with_suggestions`, `SUGGESTION_REASON`, `opConfirmSuggestions`, `suggested`, `suggestions`, `--suggest` are used consistently.
* `pos = 0` for suggestions: the UI renumber also gives them 0, consistently.
