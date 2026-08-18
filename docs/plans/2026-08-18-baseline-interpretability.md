# Baseline Interpretability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Rung-0 baseline readable — surface the `cause:` split, the clamped-document comparison, and the prediction-kind breakdown as NDA-safe aggregates, so the handoff's §6 routing decision rests on numbers instead of inference.

**Architecture:** Three additive read-only views over data the harness already computes, plus one guard against a trap that has already cost a diagnostic. `score_doc` gains three recorded facts per document (effective dpi, predictions by kind, false detections by kind); `summarize()` gains four aggregations (error causes, misplaced matches, clamped-vs-unclamped split, kind breakdown). No scoring semantics change, no metric is redefined, and `SCHEMA_VERSION` stays at 1 — every new field carries a default so the existing report and all 20 dumps keep parsing.

**Tech Stack:** Python 3.14, pydantic v2, pytest. Package `app/eval/` (harness) and `app/pipeline/` (product). No new dependencies.

---

## 0. Context — read this before touching anything

This section is the handoff. You should not need the originating conversation.

### 0.1 The data rules are enforced, not advisory

The client's drawings and inspection sheets are under NDA. A guard hook
(`~/.claude/hooks/sindri-guard.py`, registered in `~/.claude/settings.json`)
blocks every agent from reading them, on this machine, in every session.

* Protected root: `/home/clemi/sindri-client-data`.
* **Any** command mentioning it is denied unless it is one of the reviewed
  aggregate-only tools, and even then **only as a single unpiped, unchained
  command**. `cmd | head`, `cmd && cmd`, and multi-line shell blocks are denied.
* Blocked everywhere by file type: `*.pdf`, spreadsheets, `*.gold.json`,
  `*.pred.json`, `*.report.json`, `doc_id_map*`.
* Sanctioned: `python -m app.eval.runner <probe|headers|ingest|split|predict|
  score|compare|summary|variants>`, plus `setup_client_data.py` and
  `sync_client_data.sh`.
* Verify the guard with `bash ~/.claude/hooks/test-sindri-guard.sh` (32 cases).
  Re-run it after ANY edit to the guard.
* Document ids are salted hashes in all output. Never use `--show-ids`. Never
  read a `*.report.json` — use `runner summary`.
* **Do not widen the guard.** A guard edit to permit data egress was previously
  denied by the permission classifier, correctly. If a transfer is needed, hand
  the user the command to run.

Full rationale: `docs/eval/DATA-HANDLING.md`.

**This plan never requires reading protected content.** Tasks 1–4 are pure code
and unit tests on synthetic fixtures. Task 5 runs two sanctioned `runner`
subcommands as single unpiped commands, which is explicitly permitted.

### 0.2 Where the project stands

Branch `worktree-eval-harness`, PR #2, HEAD `80a4009`, pushed to origin. Working
tree clean. Baseline suite: **391 passed, 2 skipped** (the 2 skips are
`tests/test_detect_gpu.py`, which need `RUN_GPU_TESTS=1` on a GPU host — they
have always skipped here). Guard: **32 passed, 0 failed**.

The three blocking fixes from the previous handoff are merged and verified:

| commit | fix |
|---|---|
| `b266367` | 80 MP render budget in `render_page`, effective scale threaded through `ExtractionResult.render_scale` into `PredictionDump.scale` |
| `1ac165a` | per-document error isolation + resume in `_cmd_predict` |
| `80a4009` | title-block cells upscaled above Qwen's 28 px patch factor |

The baseline GPU run then completed cleanly: **20/20 predicted, 0 failed**. Four
documents hit the render budget (109, 208, 225, 225 dpi against a requested 300)
— including the 14457×2384 pt sheet that crashed the previous run at 598 MP.
Fix A's predicted effective-dpi table (110/210/227) matched reality (109/208/225).

### 0.3 The baseline number, and what it decomposes into

From `runner summary` on `baseline-dev`:

```
n_docs=20  n_gold=477  n_pred=830
mean_review_cost=245.30  recall=0.350  precision=0.201  escaped_rate=0.182
taxonomy: missed=310  false_detection=663  escaped_error=87
          flagged_error=29  flagged_correct=16  correct=35
```

The taxonomy reconciles exactly — 310+87+29+16+35 = 477 = `n_gold`, and
830−663 = 477−310 = 167 matched on both sides. Cost decomposition (weights
miss=10, escaped=5, false=2, flag=1; total 4906 / 20 docs = 245.3):

| bucket | n | cost | share |
|---|---|---|---|
| missed | 310 | 3100 | **63.2%** |
| false_detection | 663 | 1326 | 27.0% |
| escaped_error | 87 | 435 | 8.9% |
| flagged | 45 | 45 | 0.9% |

Two secondary facts: of the 167 gold rows that were matched, only 51 have
correct fields (**30.5% field accuracy on rows it found**); and the review flag
caught 29 of 116 field errors (25%) while false-alarming on 16 of 51 correct
rows (31%).

### 0.4 The three gaps this plan closes

Handoff §6 says to route all later work on the taxonomy: `missed` dominant →
Rung 1 detection knobs; `cause:misparse` → parser hardening; `cause:misread` →
Rung 2 prompts then Rung 3 LoRA; `flagged_correct` large → flag calibration.
Three of those inputs are currently unavailable:

1. **`cause:` is never aggregated.** `app/eval/score.py:84` writes
   `cause:misparse` / `cause:misread` into `MatchedPair.notes`, but
   `app/eval/report.py:40 summarize()` never reads it. That split is the whole
   parser-vs-perception decision, and it lives only inside `*.report.json`,
   which is guard-blocked. **This is the highest-value gap.**

2. **The clamped-document list is unjoinable.** `run_baseline_gpu.sh` does not
   pass `SINDRI_DOC_SALT` into the container and `~/.claude/sindri-doc-salt`
   does not exist there, so `anon.ensure_salt()` minted a fresh
   `os.urandom(32)` salt inside a `--rm` container and destroyed it on exit.
   The run log's `clamped_dpi_docs` ids share no salt with the locally-scored
   report — zero overlap, verified. "Did the misses cluster on the clamped
   drawings?", the question fix A's logging existed to answer, is unanswerable
   from the log. The count (4) survives; the identities do not. **The dumps are
   local and carry `scale`, so this is recoverable locally — that is Task 2.**

3. **`false_detection` is inflated by an unquantified amount.**
   `score.py:53-57` filters *gold* to `MatchParams.score_kinds == ("dimension",)`
   but does not filter *predictions* by kind at all. `Characteristic.kind` is
   one of `dimension|gdt|surface|note|material` (`app/models.py:14`). So every
   GD&T, surface-finish, note or material characteristic the pipeline correctly
   detects has no in-scope gold to match and lands in the 663. That is 27% of
   the review cost resting on a possibly-miscounted bucket.

**Task 3 measures gap 3; it does not fix it.** Whether predictions should be
filtered to `score_kinds`, or verbal gold should be admitted under the existing
value-matching mode, is a metric-definition decision for the user — and
`MatchParams` is the comparability guard, so changing it invalidates comparison
with this baseline. Produce the number, then stop and ask.

### 0.5 House rules

* **TDD, strictly**: failing test → watch it fail → minimal implementation →
  watch it pass → commit. One task, one commit.
* Do not bump `SCHEMA_VERSION` (`app/eval/models.py:17`). Every field this plan
  adds carries a default, so the existing report and all 20 dumps keep parsing.
  Bumping it would invalidate the run you are trying to interpret.
* Do not regenerate the split — `6d174d5e4f1b9228` is frozen and every report
  embeds it.
* Do not touch the test split.
* Commit messages: imperative subject, a body explaining *why*, and the trailer
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `app/eval/models.py` | Modify (`DocScore`, ~line 125) | Add `effective_dpi`, `pred_kinds`, `false_kinds` — per-document facts scoring already knows but discards |
| `app/eval/score.py` | Modify (`score_doc`, ~line 100-117) | Record those three facts |
| `app/eval/report.py` | Modify (`summarize`, line 40) | Four new aggregations; stays values-blind |
| `app/eval/anon.py` | Modify (after `ensure_salt`, line 35) | Add `salt_is_persistent()` |
| `app/eval/runner.py` | Modify (`_cmd_predict`) | Warn when doc ids are throwaway |
| `run_baseline_gpu.sh` | Modify (comment near line 58) | Document why container ids are not joinable |
| `tests/eval/test_report.py` | Modify | Tasks 1, 2, 3 aggregation tests |
| `tests/eval/test_score.py` | Modify | Tasks 2, 3 per-document recording tests |
| `tests/eval/test_anon.py` | Modify | Task 4 |
| `tests/eval/test_runner_e2e.py` | Modify | Task 4 warning test |

---

## Task 1: Aggregate error causes and misplaced matches

This is the one that unblocks the routing decision, and it needs no re-score —
the causes are already inside the existing report.

**Files:**
- Modify: `app/eval/report.py:40` (`summarize`)
- Test: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/eval/test_report.py`, and extend the imports at the top of that
file to `from app.eval.models import (DocScore, MatchedPair, MatchParams,
ReviewCostWeights, RunConfig)` and `from app.eval.report import (aggregate,
compare_runs, summarize)`:

```python
def _pair(balloon, taxonomy, notes, correct=False, flagged=False):
    return MatchedPair(gold_balloon=balloon, pred_pos=balloon,
                       distance_frac=0.01, fields_correct=correct,
                       flagged=flagged, taxonomy=taxonomy, notes=notes)


def test_summary_aggregates_error_causes_and_misplaced_matches():
    """Handoff §6 routes on the cause split: misparse -> parser hardening,
    misread -> Rung 2/3 perception. It is written into MatchedPair.notes and was
    never aggregated, so the decision had no number behind it."""
    pairs = [
        _pair(1, "escaped_error", ["cause:misread"]),
        _pair(2, "flagged_error", ["misplaced", "cause:misparse"], flagged=True),
        _pair(3, "escaped_error", ["cause:misread"]),
        _pair(4, "correct", [], correct=True),
    ]
    doc = DocScore(doc_id="D1", gold_hash="g" + "0" * 15, n_gold=4, n_pred=4,
                   pairs=pairs, counts={"escaped_error": 2, "flagged_error": 1,
                                        "correct": 1},
                   review_cost=11.0, recall=1.0, precision=1.0, escaped_rate=0.5)
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [doc])

    digest = summarize(report, lambda d: "hashed")

    assert digest["error_causes"] == {"misread": 2, "misparse": 1}
    assert digest["misplaced_matches"] == 1


def test_summary_cause_aggregation_never_reads_client_values():
    """field_errors spells out gold vs predicted ("nominal: '6,5'!='5,5'").
    summarize() is the one sanctioned view of a run; it must stay values-blind."""
    pairs = [_pair(1, "escaped_error", ["cause:misread"])]
    pairs[0].field_errors = ["nominal: '6,5'!='5,5'"]
    doc = DocScore(doc_id="D1", gold_hash="g" + "0" * 15, n_gold=1, n_pred=1,
                   pairs=pairs, counts={"escaped_error": 1}, review_cost=5.0,
                   recall=1.0, precision=1.0, escaped_rate=1.0)
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), [doc])

    blob = json.dumps(summarize(report, lambda d: "hashed"))

    assert "6,5" not in blob and "5,5" not in blob
    assert "nominal" not in blob
```

`tests/eval/test_report.py` starts with `import pytest` and does not import
`json`. Add it, so the first two lines read:

```python
import json

import pytest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_report.py -k "error_causes or client_values" -v`

Expected: FAIL with `KeyError: 'error_causes'`.

- [ ] **Step 3: Write minimal implementation**

In `app/eval/report.py`, insert this helper directly above `def summarize`:

```python
def _note_counts(report: RunReport) -> Tuple[Dict[str, int], int]:
    """Aggregate the tags scoring left on matched pairs.

    Reads ONLY the `cause:` and `misplaced` tokens — a fixed vocabulary written
    by score._cause. It never touches `field_errors`, which spells out client
    values, so the digest stays safe to commit and to show an agent."""
    causes: Dict[str, int] = {}
    misplaced = 0
    for d in report.doc_scores:
        for p in d.pairs:
            for note in p.notes:
                if note.startswith("cause:"):
                    key = note.split(":", 1)[1]
                    causes[key] = causes.get(key, 0) + 1
                elif note == "misplaced":
                    misplaced += 1
    return causes, misplaced
```

Extend the typing import at the top of `app/eval/report.py` to
`from typing import Dict, List, Tuple`.

Inside `summarize`, add as the first line of the body after the docstring:

```python
    causes, misplaced = _note_counts(report)
```

and add these two entries to the returned dict, directly after `"taxonomy"`:

```python
        # Handoff §6 routing: misparse -> parser hardening (Rung 1),
        # misread -> prompts (Rung 2) then LoRA (Rung 3).
        "error_causes": causes,
        # Matched, but further from its gold balloon than misplaced_frac — a
        # geometry-quality signal that is not an error in its own right.
        "misplaced_matches": misplaced,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_report.py -v`

Expected: PASS, all tests in the file green.

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "feat(eval): aggregate error causes into the run summary

Handoff §6 routes every later rung on the cause split -- misparse means the
reader saw the right glyphs and parsing lost them (parser hardening), misread
means it did not (Rung 2 prompts, then Rung 3 LoRA). score._cause has been
writing that tag into MatchedPair.notes since the scorer landed, and summarize()
never read it, so the decision had no number behind it and the tag was reachable
only by opening a guard-blocked report.

Also counts \`misplaced\` pairs: matched, but further from the gold balloon than
misplaced_frac. Not an error on its own, but it separates \"detection is landing
in the wrong place\" from \"detection is not landing at all\".

The aggregation reads only the cause:/misplaced vocabulary, never field_errors,
so the digest stays values-blind and safe to commit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Record effective render dpi and split clamped vs unclamped

**Files:**
- Modify: `app/eval/models.py:125` (`DocScore`)
- Modify: `app/eval/score.py:108` (`score_doc` return)
- Modify: `app/eval/report.py` (`summarize`)
- Test: `tests/eval/test_score.py`, `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing test for the per-document field**

Append to `tests/eval/test_score.py` (it already defines `SCALE`, `RECT`,
`_pt_box`, `_gold`, `_dump` at the top of the file):

```python
def _clamped_dump():
    """The same page rendered under the 80 MP budget. Boxes are in the CLAMPED
    render's pixels, which is what the pipeline actually produces after
    b266367 — so the geometry must still round-trip through dump.scale."""
    clamped_scale = 109 / 72.0                 # the 598 MP sheet's real dpi

    def box(x, y):
        return (clamped_scale * (x - 15), clamped_scale * (y - 5),
                clamped_scale * (x + 15), clamped_scale * (y + 5))

    chars = [Characteristic(pos=1, char_type="Diameter", nominal="20",
                            upper_tol="0,1", lower_tol="-0,1",
                            raw_text="Ø20 +0,1 -0,1", kind="dimension",
                            target_region=box(100, 100))]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=clamped_scale, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_doc_score_records_the_effective_render_dpi():
    s = score_doc(_dump(), _gold(), ReviewCostWeights(), MatchParams())
    assert s.effective_dpi == pytest.approx(300.0)


def test_doc_score_records_a_clamped_dpi_and_still_matches():
    """A clamped document is scored at reduced resolution, not scored wrongly:
    the box still lands on gold balloon 1."""
    s = score_doc(_clamped_dump(), _gold(), ReviewCostWeights(), MatchParams())
    assert s.effective_dpi == pytest.approx(109.0)
    assert s.counts.get("correct") == 1
```

`tests/eval/test_score.py` has no `pytest` import today — its first line is
`from app.eval.models import (...)`. Add one above it:

```python
import pytest

from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_score.py -k effective_dpi -v`

Expected: FAIL with `AttributeError: 'DocScore' object has no attribute 'effective_dpi'`.

- [ ] **Step 3: Write minimal implementation**

In `app/eval/models.py`, add to `DocScore` immediately after
`excluded_by_kind: int = 0`:

```python
    # Resolution this document was actually rendered at. render.py clamps dpi
    # to an 80 MP budget on large-format sheets, so this can sit below
    # config.dpi — and "did the misses cluster on the clamped drawings?" cannot
    # be answered without it. 0.0 in reports written before this field existed.
    effective_dpi: float = 0.0
```

In `app/eval/score.py`, add to the `DocScore(...)` construction in the return of
`score_doc`, directly after `excluded_by_kind=excluded_by_kind,`:

```python
        effective_dpi=dump.scale * 72.0,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_score.py -v`

Expected: PASS.

- [ ] **Step 5: Write the failing test for the aggregation**

Append to `tests/eval/test_report.py`:

```python
def _dpi_doc(doc_id, dpi, recall, cost):
    return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=10, n_pred=10,
                    counts={"correct": 10}, review_cost=cost, recall=recall,
                    precision=1.0, escaped_rate=0.0, effective_dpi=dpi)


def test_summary_separates_clamped_documents_from_the_rest():
    """The run log's clamped ids came from a throwaway container salt and cannot
    be joined to a locally-scored report. The dumps carry the real scale, so the
    comparison is recoverable here — with local-salt ids that DO join."""
    docs = [_dpi_doc("D1", 300.0, 0.60, 100.0),
            _dpi_doc("D2", 300.0, 0.50, 140.0),
            _dpi_doc("D3", 109.0, 0.20, 400.0),
            _dpi_doc("D4", 225.0, 0.30, 300.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")

    assert [c["doc"] for c in digest["clamped_docs"]] == ["hash-D3", "hash-D4"]
    assert digest["clamped_docs"][0]["effective_dpi"] == 109
    assert digest["clamped_vs_unclamped"]["clamped"]["n"] == 2
    assert digest["clamped_vs_unclamped"]["unclamped"]["n"] == 2
    assert digest["clamped_vs_unclamped"]["clamped"]["mean_recall"] == 0.25
    assert digest["clamped_vs_unclamped"]["unclamped"]["mean_recall"] == 0.55


def test_summary_reports_no_clamped_documents_when_none_were_clamped():
    docs = [_dpi_doc("D1", 300.0, 0.6, 100.0), _dpi_doc("D2", 300.0, 0.5, 140.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")

    assert digest["clamped_docs"] == []
    assert digest["clamped_vs_unclamped"]["clamped"]["n"] == 0
    assert digest["clamped_vs_unclamped"]["clamped"]["mean_recall"] is None
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_report.py -k clamped -v`

Expected: FAIL with `KeyError: 'clamped_docs'`.

- [ ] **Step 7: Write minimal implementation**

In `app/eval/report.py`, insert above `def summarize`:

```python
def _clamp_split(report: RunReport, anonymizer, top: int = 10) -> Tuple[List, Dict]:
    """Clamped documents, and clamped-vs-unclamped means.

    render.py reduces dpi on sheets that would exceed the 80 MP budget, so those
    documents are extracted at up to a third of the requested resolution. Before
    anyone reads a low recall as a statement about the model, this says whether
    the misses concentrate there. Ids come from the local salt, so they join to
    worst_docs — unlike the predict log's, which are minted per container."""
    requested = report.config.dpi
    clamped = sorted((d for d in report.doc_scores
                      if d.effective_dpi and d.effective_dpi < requested - 0.5),
                     key=lambda d: d.effective_dpi)
    clamped_ids = {d.doc_id for d in clamped}
    rest = [d for d in report.doc_scores if d.doc_id not in clamped_ids]

    def block(docs):
        n = len(docs)
        return {
            "n": n,
            "mean_recall": round(sum(d.recall for d in docs) / n, 4) if n else None,
            "mean_review_cost": (round(sum(d.review_cost for d in docs) / n, 2)
                                 if n else None),
        }

    listed = [{"doc": anonymizer(d.doc_id),
               "effective_dpi": round(d.effective_dpi),
               "recall": round(d.recall, 4),
               "review_cost": d.review_cost} for d in clamped[:top]]
    return listed, {"clamped": block(clamped), "unclamped": block(rest)}
```

Inside `summarize`, add after the `causes, misplaced = ...` line:

```python
    clamped_docs, clamp_split = _clamp_split(report, anonymizer, top)
```

and add to the returned dict, directly after `"misplaced_matches"`:

```python
        "clamped_docs": clamped_docs,
        "clamped_vs_unclamped": clamp_split,
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_report.py tests/eval/test_score.py -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/eval/models.py app/eval/score.py app/eval/report.py tests/eval/test_score.py tests/eval/test_report.py
git commit -m "feat(eval): report effective render dpi and split clamped documents

Fix A logs the clamped dpi per document on the GPU host, but those lines are
labelled with doc ids hashed under a salt the container minted itself: no
SINDRI_DOC_SALT is passed in and ~/.claude/sindri-doc-salt does not exist there,
so ensure_salt() wrote a fresh urandom salt into a --rm container and destroyed
it on exit. The run log's clamped ids therefore share nothing with a
locally-scored report -- the diagnostic that logging existed to serve was not
answerable.

The dumps carry the real scale, so recover it locally instead: DocScore records
the effective dpi, and summarize() lists the clamped documents under the LOCAL
salt (so they join to worst_docs) alongside clamped-vs-unclamped mean recall and
review cost. \"Did the misses cluster on the clamped drawings?\" is now a number.

DocScore.effective_dpi defaults to 0.0 and SCHEMA_VERSION is unchanged, so the
existing report and all 20 dumps keep parsing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Break predictions and false detections down by kind

Measurement only. Do not change what is scored.

**Files:**
- Modify: `app/eval/models.py` (`DocScore`)
- Modify: `app/eval/score.py` (`score_doc`)
- Modify: `app/eval/report.py` (`summarize`)
- Test: `tests/eval/test_score.py`, `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/eval/test_score.py`:

```python
def _kinded_dump():
    """Two in-scope dimensions plus two predictions of kinds the metric removed
    from gold — the asymmetry this test exists to measure."""
    chars = [
        Characteristic(pos=1, char_type="Diameter", nominal="20", upper_tol="0,1",
                       lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1", kind="dimension",
                       target_region=_pt_box(100, 100)),
        Characteristic(pos=2, char_type="Distance", nominal="5,5", raw_text="5,5",
                       kind="dimension", target_region=_pt_box(400, 200)),
        # a surface-finish callout and a note: correctly detected, but gold was
        # filtered to score_kinds=("dimension",), so neither can ever match
        Characteristic(pos=6, char_type="Surface", nominal="Ra1,6",
                       raw_text="Ra 1,6", kind="surface",
                       target_region=_pt_box(250, 650)),
        Characteristic(pos=7, char_type="Note", nominal="", raw_text="see note 3",
                       kind="note", target_region=_pt_box(600, 700)),
    ]
    return PredictionDump(doc_id="D", config=RunConfig(model_id="stub", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))


def test_doc_score_breaks_predictions_and_false_detections_down_by_kind():
    """score_doc filters GOLD to score_kinds but never filters PREDICTIONS, so
    every correctly-detected surface/note/gdt callout lands in false_detection.
    Record the breakdown so that inflation can be measured before anyone reads
    precision as a statement about the model."""
    s = score_doc(_kinded_dump(), _gold(), ReviewCostWeights(), MatchParams())

    assert s.pred_kinds == {"dimension": 2, "surface": 1, "note": 1}
    assert s.false_kinds == {"surface": 1, "note": 1}
    assert s.counts["false_detection"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_score.py -k by_kind -v`

Expected: FAIL with `AttributeError: 'DocScore' object has no attribute 'pred_kinds'`.

- [ ] **Step 3: Write minimal implementation**

In `app/eval/models.py`, add to `DocScore` immediately after `effective_dpi`:

```python
    # Predictions by detector kind, and which of them went unmatched. Gold is
    # filtered to MatchParams.score_kinds; predictions are not, so a correctly
    # detected surface finish or note is charged as a false detection. These two
    # make that inflation measurable instead of assumed.
    pred_kinds: Dict[str, int] = {}
    false_kinds: Dict[str, int] = {}
```

In `app/eval/score.py`, add directly above the `n_gold, n_pred = ...` line in
`score_doc`:

```python
    def _kind(c) -> str:
        return c.kind or "unset"

    pred_kinds: Dict[str, int] = {}
    for c in preds:
        pred_kinds[_kind(c)] = pred_kinds.get(_kind(c), 0) + 1
    false_kinds: Dict[str, int] = {}
    for pk in false:
        k = _kind(pred_by_pos[pk])
        false_kinds[k] = false_kinds.get(k, 0) + 1
```

and add to the `DocScore(...)` construction, after `effective_dpi=...`:

```python
        pred_kinds=pred_kinds, false_kinds=false_kinds,
```

`app/eval/score.py:6` currently reads `from typing import List`. Change it to:

```python
from typing import Dict, List
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_score.py -v`

Expected: PASS.

- [ ] **Step 5: Write the failing aggregation test**

Append to `tests/eval/test_report.py`:

```python
def test_summary_aggregates_prediction_kinds_across_documents():
    def doc(doc_id, pred_kinds, false_kinds):
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=5,
                        n_pred=sum(pred_kinds.values()), counts={"correct": 5},
                        review_cost=10.0, recall=1.0, precision=1.0,
                        escaped_rate=0.0, pred_kinds=pred_kinds,
                        false_kinds=false_kinds)

    docs = [doc("D1", {"dimension": 20, "note": 5}, {"note": 5}),
            doc("D2", {"dimension": 18, "surface": 3}, {"surface": 3,
                                                        "dimension": 2})]
    report = aggregate("r", RunConfig(model_id="stub"), ReviewCostWeights(),
                       MatchParams(), docs)

    digest = summarize(report, lambda d: "hashed")

    assert digest["pred_kinds"] == {"dimension": 38, "note": 5, "surface": 3}
    assert digest["false_detections_by_kind"] == {"note": 5, "surface": 3,
                                                  "dimension": 2}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_report.py -k prediction_kinds -v`

Expected: FAIL with `KeyError: 'pred_kinds'`.

- [ ] **Step 7: Write minimal implementation**

In `app/eval/report.py`, insert above `def summarize`:

```python
def _kind_totals(report: RunReport) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Predictions and unmatched predictions by detector kind, summed over the
    run. Kind names are a fixed detector vocabulary, never client text."""
    preds: Dict[str, int] = {}
    false: Dict[str, int] = {}
    for d in report.doc_scores:
        for k, v in d.pred_kinds.items():
            preds[k] = preds.get(k, 0) + v
        for k, v in d.false_kinds.items():
            false[k] = false.get(k, 0) + v
    return preds, false
```

Inside `summarize`, add after the `clamped_docs, clamp_split = ...` line:

```python
    pred_kinds, false_kinds = _kind_totals(report)
```

and add to the returned dict, directly after `"clamped_vs_unclamped"`:

```python
        # Gold is filtered to match_params.score_kinds; predictions are not. A
        # non-dimension kind here is a detection the metric cannot credit.
        "pred_kinds": pred_kinds,
        "false_detections_by_kind": false_kinds,
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_report.py tests/eval/test_score.py -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/eval/models.py app/eval/score.py app/eval/report.py tests/eval/test_score.py tests/eval/test_report.py
git commit -m "feat(eval): break predictions and false detections down by kind

score_doc filters GOLD to MatchParams.score_kinds=(\"dimension\",) but applies no
kind filter to predictions, so every correctly-detected GD&T, surface-finish,
note or material callout has no in-scope gold to match and is charged as a false
detection. In the Rung-0 baseline that bucket is 663 predictions carrying 27% of
the review cost, and nothing said how much of it was this asymmetry.

Record the breakdown rather than change the metric: MatchParams is the
comparability guard, so filtering predictions to score_kinds -- or admitting
verbal gold under the existing value-matching mode -- would invalidate
comparison against this baseline. That is a decision for the user, and it needs
this number first.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Warn when doc ids are throwaway

Stops gap 2 from recurring. No data leaves this machine: this is a warning, not
a salt transfer.

**Files:**
- Modify: `app/eval/anon.py` (after `ensure_salt`, line 35)
- Modify: `app/eval/runner.py` (`_cmd_predict`)
- Modify: `run_baseline_gpu.sh` (comment near line 58)
- Test: `tests/eval/test_anon.py`, `tests/eval/test_runner_e2e.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/eval/test_anon.py`, extending its imports with
`from app.eval.anon import SALT_ENV, salt_is_persistent`:

```python
def test_salt_is_persistent_detects_an_absent_salt(tmp_path, monkeypatch):
    """False means the next ensure_salt() call mints a throwaway — which is
    exactly what happens inside the --rm GPU container."""
    monkeypatch.delenv(SALT_ENV, raising=False)
    assert salt_is_persistent(tmp_path / "absent") is False


def test_salt_is_persistent_detects_a_salt_file(tmp_path, monkeypatch):
    monkeypatch.delenv(SALT_ENV, raising=False)
    path = tmp_path / "salt"
    path.write_text("cafebabe", encoding="utf-8")
    assert salt_is_persistent(path) is True


def test_salt_is_persistent_honours_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv(SALT_ENV, "cafebabe")
    assert salt_is_persistent(tmp_path / "absent") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_anon.py -k salt_is_persistent -v`

Expected: FAIL with `ImportError: cannot import name 'salt_is_persistent'`.

- [ ] **Step 3: Write minimal implementation**

In `app/eval/anon.py`, add directly below `ensure_salt`:

```python
def salt_is_persistent(path=None) -> bool:
    """Whether a salt already exists, without creating one.

    False means the next ensure_salt() call mints a throwaway. That is what
    happens inside the GPU container: no SINDRI_DOC_SALT is passed in and
    ~/.claude/sindri-doc-salt is not present, so the ids in a predict log are
    hashed under a salt that dies with the container and cannot be joined to a
    locally-scored report."""
    if os.environ.get(SALT_ENV):
        return True
    path = Path(path) if path is not None else DEFAULT_SALT_FILE
    return path.exists()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_anon.py -v`

Expected: PASS.

- [ ] **Step 5: Write the failing test for the predict warning**

Append to `tests/eval/test_runner_e2e.py` (the file already defines
`_setup_corpus`, `_no_model`, `_stub_dump`, `_fake_predict`, `RECT`, and imports
`main`):

```python
def test_predict_warns_when_the_doc_ids_are_throwaway(tmp_path, capsys,
                                                       monkeypatch):
    """The GPU container has no persistent salt, so its hashed ids join to
    nothing. Say so in the log instead of letting them look authoritative."""
    import app.eval.anon as anon_mod
    monkeypatch.delenv("SINDRI_DOC_SALT", raising=False)
    monkeypatch.setattr(anon_mod, "DEFAULT_SALT_FILE", tmp_path / "absent-salt")
    pdfs, _ = _setup_corpus(tmp_path)
    _no_model(monkeypatch)
    _fake_predict(monkeypatch, lambda pdf_path, doc_id, dpi, backend, config,
                  work_dir: _stub_dump(doc_id, config))

    assert main(["predict", "--pdfs", str(pdfs),
                 "--out", str(tmp_path / "runs" / "base")]) == 0

    err = capsys.readouterr().err
    assert "throwaway" in err.lower()
    assert "runner summary" in err


def test_predict_is_quiet_when_the_salt_is_persistent(tmp_path, capsys,
                                                       monkeypatch):
    monkeypatch.setenv("SINDRI_DOC_SALT", "test-salt")
    pdfs, _ = _setup_corpus(tmp_path)
    _no_model(monkeypatch)
    _fake_predict(monkeypatch, lambda pdf_path, doc_id, dpi, backend, config,
                  work_dir: _stub_dump(doc_id, config))

    assert main(["predict", "--pdfs", str(pdfs),
                 "--out", str(tmp_path / "runs" / "base")]) == 0

    assert "throwaway" not in capsys.readouterr().err.lower()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_runner_e2e.py -k throwaway -v`

Expected: FAIL — `assert "throwaway" in err.lower()` fails, the warning is absent.

- [ ] **Step 7: Write minimal implementation**

In `app/eval/runner.py`, extend the anon import to include `salt_is_persistent`
(the module already imports `Anonymizer` from `app.eval.anon`). Then in
`_cmd_predict`, insert immediately **before** the `anon = _anon(args)` line —
the check must run before `Anonymizer` creates a salt:

```python
    # Must precede _anon(): constructing an Anonymizer mints the salt.
    if not getattr(args, "show_ids", False) and not salt_is_persistent():
        print("WARNING: no persistent doc-id salt (SINDRI_DOC_SALT unset, "
              "~/.claude/sindri-doc-salt absent) — the hashed ids below are "
              "throwaway and cannot be joined to a locally-scored report. "
              "Read per-document facts from `runner summary` instead.",
              file=sys.stderr)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/eval/test_runner_e2e.py tests/eval/test_anon.py -v`

Expected: PASS.

- [ ] **Step 9: Document it at the call site**

In `run_baseline_gpu.sh`, replace the comment block above the step-3 `ssh`
invocation (currently the two lines beginning `# Mirrors run-gpu.sh:`) with:

```bash
# Mirrors run-gpu.sh: rootless CDI overlay when a corrected spec exists,
# otherwise straight through.
#
# The container gets no SINDRI_DOC_SALT and has no ~/.claude/sindri-doc-salt, so
# the doc ids in this step's log are hashed under a salt it mints and then
# destroys. They are readable, but they join to NOTHING — not to the report from
# step 5, not to a previous run. Per-document facts come from `runner summary`,
# which hashes under the local salt (see summary.clamped_docs). Passing the salt
# in would make the logs joinable at the cost of putting it on the GPU host and
# in that host's process list; that is the user's call, not a default.
```

- [ ] **Step 10: Commit**

```bash
git add app/eval/anon.py app/eval/runner.py run_baseline_gpu.sh tests/eval/test_anon.py tests/eval/test_runner_e2e.py
git commit -m "feat(eval): warn when predict's doc ids are throwaway

Inside the --rm GPU container there is no SINDRI_DOC_SALT and no
~/.claude/sindri-doc-salt, so ensure_salt() mints a urandom salt, hashes the
whole predict log with it, and destroys it on exit. The ids look exactly like
the ones a local report carries and share nothing with them -- which is how the
baseline run's clamped-document list ended up unjoinable to its own scores.

Warn rather than ship the salt to the GPU host: the diagnostic is recoverable
locally from the dumps (summary.clamped_docs), so there is no reason to widen
what leaves this machine. run_baseline_gpu.sh now says the same thing where the
container is launched.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Re-score, re-summarize, and read the routing

No GPU is involved. The 20 dumps and the gold are already on this machine, so
scoring is local and takes seconds.

**Files:**
- Modify: `docs/eval/baseline-summary.json` (regenerated; untracked today, and
  `.gitignore` blocks only `*.report.json`, so this one is committable)

- [ ] **Step 1: Verify the whole suite and the guard**

Run: `python -m pytest -q`
Expected: `404 passed, 2 skipped` — 391 before this plan plus the 13 tests it
adds (2 in Task 1, 4 in Task 2, 2 in Task 3, 5 in Task 4). The 2 skips are
`tests/test_detect_gpu.py` and are expected off a GPU host. Zero failures.

Run: `bash ~/.claude/hooks/test-sindri-guard.sh`
Expected: `guard: 32 passed, 0 failed`.

- [ ] **Step 2: Re-score the existing dumps**

This is a sanctioned `runner` subcommand. Run it as a **single unpiped command**
— do not add `| tail`, `&&`, or a heredoc, or the guard will deny it:

```bash
python3 -m app.eval.runner score --run /home/clemi/sindri-client-data/runs/baseline --gold /home/clemi/sindri-client-data/gold --splits /home/clemi/sindri-client-data/meta/splits.json --split dev --weights docs/eval/weights.json --name baseline-dev --out /home/clemi/sindri-client-data/reports/baseline-dev.report.json
```

Expected: a `WARNING: gold docs without dumps (excluded): [...]` line naming 79
documents — that is correct, the dev split is 20 of 99 — then
`baseline-dev: docs=20 mean_review_cost=245.30 recall=0.350 escaped_rate=0.182`.
The headline numbers must be **unchanged**; this plan adds recorded facts, it
does not alter scoring. If any headline number moved, stop and investigate
before going further.

- [ ] **Step 3: Regenerate the summary**

Single unpiped command:

```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/baseline-dev.report.json --out docs/eval/baseline-summary.json
```

Expected: the previous digest plus `error_causes`, `misplaced_matches`,
`clamped_docs`, `clamped_vs_unclamped`, `pred_kinds`, and
`false_detections_by_kind`. `clamped_docs` should contain **4** entries at
roughly 109, 208, 225 and 225 dpi.

- [ ] **Step 4: Commit the artifact**

```bash
git add docs/eval/baseline-summary.json
git commit -m "docs(eval): Rung-0 baseline summary with the interpretability views

Regenerated from the same 20 dumps and the same frozen split
(6d174d5e4f1b9228); the headline numbers are unchanged. Adds the cause split,
the clamped-document comparison and the prediction-kind breakdown, which is what
makes the handoff §6 routing decision answerable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Read the routing and report it — do not start Rung 1 yet**

Report these four things to the user, in this order, and stop:

1. **`error_causes`.** `misparse` dominant → parser hardening. `misread`
   dominant → Rung 2 prompts, then Rung 3 LoRA. Note that this splits only the
   116 matched-but-wrong rows, not the 310 misses, so it decides where *reading*
   work goes — not whether reading work outranks detection work.
2. **`clamped_vs_unclamped`.** If clamped mean recall is far below unclamped,
   part of the 63% miss cost is the render budget rather than the model, and
   Rung 1 should consider tiled rendering for oversized sheets before touching
   detection thresholds. With n=4 vs n=16 this is a signal, not a result — say
   so.
3. **`false_detections_by_kind`.** The non-`dimension` share is the measurement
   artefact from §0.4 gap 3. Report it as "N of 663 false detections are kinds
   the metric removed from gold" and present the options — filter predictions to
   `score_kinds`, or admit verbal gold under the existing value-matching mode —
   **without picking one**. Both change `MatchParams`, which is the
   comparability guard, so either one makes this baseline incomparable with
   everything scored after it. That is the user's decision.
4. **The standing read.** `missed` carries 63% of the review cost, which points
   at Rung 1 detection under handoff §6. Field accuracy on matched rows is
   30.5%, so perception work will matter regardless of what the cause split
   says — the question the cause split answers is whether it starts at the
   parser or at the prompts.

---

## Verification Checklist

- [ ] Every new function has a test that was watched failing first
- [ ] `python -m pytest -q` → 395+ passed, 2 skipped, 0 failed
- [ ] `bash ~/.claude/hooks/test-sindri-guard.sh` → 32 passed, 0 failed
- [ ] `SCHEMA_VERSION` is still `1`
- [ ] `summarize()` still reads no `field_errors` — the values-blind test in
      Task 1 covers this
- [ ] Headline metrics after re-scoring are identical to §0.3
- [ ] No `*.report.json`, `splits.json`, `variants.txt` or `doc_id_map.json`
      staged — `git status --short` before every commit
- [ ] Four commits from Tasks 1–4, one from Task 5

---

## Prompt for the fresh session

Paste this verbatim:

```
Continue the Sindri Rung-0 eval work. Read
docs/plans/2026-08-18-baseline-interpretability.md first — it is self-contained
and includes the full handoff context in section 0.

Work in the git worktree /home/clemi/mci/sindri/.claude/worktrees/eval-harness
(branch worktree-eval-harness, PR #2). House style is TDD: failing test, watch
it fail, minimal implementation, watch it pass, commit. One task, one commit.

BEFORE ANYTHING ELSE: the client's drawings and inspection sheets are under NDA
and a guard hook enforces it. Section 0.1 of the plan has the rules. In short —
never read client PDFs, spreadsheets, *.gold.json, *.pred.json or *.report.json;
only the sanctioned `python -m app.eval.runner ...` commands may touch the
protected root, and only as single unpiped commands; document ids must stay
salted hashes. Do not widen the guard.

Background: the baseline GPU run completed cleanly (20/20 documents, 0 failures)
and produced mean_review_cost=245.30, recall=0.350, precision=0.201, with
missed=310 carrying 63% of the review cost. But three of the inputs the
handoff's §6 routing rule needs are not currently readable — the cause split is
never aggregated, the clamped-document ids are unjoinable because the GPU
container minted its own throwaway salt, and false_detection is inflated by an
unmeasured number of correctly-detected non-dimension callouts. Section 0.4 has
the root-cause evidence for all three.

Implement Tasks 1-5 in order. Task 1 is the one that unblocks the routing
decision and needs no re-score. Task 3 measures the false-detection asymmetry
but must NOT change what is scored — MatchParams is the comparability guard.

Full suite must stay green (391 tests today, 404 after this plan) plus
`bash ~/.claude/hooks/test-sindri-guard.sh` (32 cases). Do not bump
SCHEMA_VERSION — it would invalidate the report and all 20 dumps.

Task 5 re-scores locally (no GPU needed — the dumps are already here), then
report the routing read from section Task 5 step 5 and stop. Do not start Rung 1
work without my go-ahead.
```
