# Baseline Interpretability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Rung-0 baseline readable — surface the `cause:` split, the clamped-document comparison, and the prediction-kind breakdown as NDA-safe aggregates, so the handoff's §6 routing decision rests on numbers instead of inference.

**Architecture:** Three additive read-only views over data the harness already computes, plus one guard against a trap that has already cost a diagnostic. `score_doc` gains three recorded facts per document (effective dpi, predictions by kind, false detections by kind); `summarize()` gains six keys via three pure helpers (`_note_counts`, `_clamp_split`, `_kind_totals`). No scoring semantics change, no metric is redefined, and `SCHEMA_VERSION` stays at 1 — every new field carries a default so the existing report and all 20 dumps keep parsing.

Every aggregate is required to reconcile against a number that already exists: the cause counts must sum to the matched-but-wrong rows, the kind counts to `n_pred` and to `false_detection`, and the dpi buckets must partition the corpus. Those identities are the Definition of Done (see the last section), not decoration — an interpretability view that cannot be cross-checked is just another number to trust.

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
  `sync_client_data.sh`. The guard's pattern is `python3?`, so `python` and
  `python3` are both accepted — Task 5 uses `python3` to match
  `run_baseline_gpu.sh`. Do not "correct" one to the other.
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

From `docs/eval/baseline-summary.json` (the committed digest, written by
`runner summary` on `baseline-dev`), condensed:

```
n_docs=20  n_gold=477  n_pred=830
mean_review_cost=245.30  recall=0.350  precision=0.201  escaped_rate=0.182
taxonomy: missed=310  false_detection=663  escaped_error=87
          flagged_error=29  flagged_correct=16  correct=35
```

That block is a rendering, not literal output — mind the two naming gaps or you
will go looking for keys that do not exist. In the JSON the recall/precision keys
are `micro_recall` (0.350104821802935) and `micro_precision`
(0.20120481927710843); there is no `recall` or `precision` key. And `_cmd_score`'s
one-line stdout prints `docs= mean_review_cost= recall= escaped_rate=` only — it
never prints precision.

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
   `app/eval/report.py:40 summarize()` never reads it, and it lives only inside
   `*.report.json`, which is guard-blocked. **This is the highest-value gap.**

   Be precise about its reach: `score.py:83-84` appends the tag only when
   `_compare_fields` returned errors, so it covers exactly the rows the pipeline
   **found and got wrong** — 116 of 477 (`flagged_error` 29 + `escaped_error`
   87). It says nothing about the 310 misses that carry 63% of the cost. So it
   decides where *reading* work goes (parser vs perception); it does not decide
   whether reading work outranks detection work. Task 5 Step 5 items 1 and 4
   report it under exactly that framing.

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
| `app/eval/report.py` | Modify (`summarize`, line 40) | Three helpers → six new keys; stays values-blind |
| `app/eval/anon.py` | Modify (after `ensure_salt`, line 35) | Add `salt_is_persistent()` |
| `app/eval/runner.py` | Modify (`_cmd_predict`) | Warn when doc ids are throwaway |
| `run_baseline_gpu.sh` | Modify (comment near line 58) | Document why container ids are not joinable |
| `tests/eval/test_report.py` | Modify | Tasks 1, 2, 3 aggregation tests |
| `tests/eval/test_score.py` | Modify | Tasks 2, 3 per-document recording tests |
| `tests/eval/test_anon.py` | Modify | Task 4 |
| `tests/eval/test_runner_e2e.py` | Modify | Task 4 warning test |

### Task order, and what is readable when

Task 1 reads a field the existing report already carries, so it works on the
current `baseline-dev.report.json` with no re-score. Tasks 2 and 3 add fields to
`DocScore`, which the existing report does **not** have — so between Task 2 and
Task 5 the new views are structurally present but empty:

| after | `error_causes` / `misplaced_matches` | `clamped_*` | `pred_kinds` / `false_detections_by_kind` |
|---|---|---|---|
| Task 1 | real | absent | absent |
| Task 2 | real | `clamped_docs: []`, all 20 docs in `unknown_dpi` | absent |
| Task 3 | real | same | `{}` |
| Task 5 | real | real | real |

**Do not read an intermediate summary as a result.** Empty is the correct output
for a stale report, and `unknown_dpi.n > 0` is the machine-readable way to say
"re-score before believing this block" — that is why the dpi split has an unknown
bucket instead of folding those documents into `unclamped`. Task 5 Step 3 asserts
the views are non-empty before anything is committed or reported.

---

## Task 1: Aggregate error causes and misplaced matches

This is the one that unblocks the routing decision, and it needs no re-score —
the causes are already inside the existing report.

**Files:**
- Modify: `app/eval/report.py:40` (`summarize`)
- Test: `tests/eval/test_report.py`

**Done when:**

| indicator | how it is measured | passes at |
|---|---|---|
| both tests were watched failing before the implementation existed | Step 2 output | `KeyError: 'error_causes'` |
| `python -m pytest tests/eval/test_report.py -q` | exit code + count | 12 passed (10 existing + 2) |
| the digest stays values-blind | `test_summary_cause_aggregation_never_reads_client_values` | no `6,5` / `5,5` / `nominal` in the JSON |
| `summarize` reads no new report field | `git diff app/eval/models.py` | empty — Task 1 touches no schema |

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

**Done when:**

| indicator | how it is measured | passes at |
|---|---|---|
| all five tests were watched failing first | Steps 2 and 6 output | `AttributeError: ... 'effective_dpi'`, then `KeyError: 'clamped_docs'` |
| `python -m pytest tests/eval/test_report.py tests/eval/test_score.py -q` | count | 23 passed (15 report + 8 score) |
| the dpi buckets partition the corpus | `clamped.n + unclamped.n + unknown_dpi.n == n_docs` in every test report | exact equality |
| a doc with no recorded dpi is never called "unclamped" | `test_summary_puts_documents_without_a_recorded_dpi_in_their_own_bucket` | 0.0-dpi docs land in `unknown_dpi` |
| clamping is scored, not mis-scored | `test_doc_score_records_a_clamped_dpi_and_still_matches` | box at 109 dpi still matches gold balloon 1 |
| the list cap is not the worst-docs knob | `grep -n "CLAMP_LIST_MAX" app/eval/report.py` | 2 hits (constant + `_clamp_split` default); `_clamp_split` takes `limit`, never `top` |

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
    split = digest["clamped_vs_unclamped"]

    assert [c["doc"] for c in digest["clamped_docs"]] == ["hash-D3", "hash-D4"]
    assert digest["clamped_docs"][0]["effective_dpi"] == 109
    assert split["clamped"]["n"] == 2
    assert split["unclamped"]["n"] == 2
    assert split["unknown_dpi"]["n"] == 0
    # Macro means: unweighted over documents. NOT comparable to the headline
    # micro_recall, which pools rows — hence the name.
    assert split["clamped"]["macro_mean_recall"] == 0.25
    assert split["unclamped"]["macro_mean_recall"] == 0.55
    # The three buckets partition the corpus, so nothing can be double-counted
    # or silently dropped.
    assert (split["clamped"]["n"] + split["unclamped"]["n"]
            + split["unknown_dpi"]["n"]) == digest["n_docs"]


def test_summary_reports_no_clamped_documents_when_none_were_clamped():
    docs = [_dpi_doc("D1", 300.0, 0.6, 100.0), _dpi_doc("D2", 300.0, 0.5, 140.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")

    assert digest["clamped_docs"] == []
    assert digest["clamped_vs_unclamped"]["clamped"]["n"] == 0
    assert digest["clamped_vs_unclamped"]["clamped"]["macro_mean_recall"] is None


def test_summary_puts_documents_without_a_recorded_dpi_in_their_own_bucket():
    """effective_dpi is 0.0 in every DocScore written before the field existed —
    i.e. in the report this plan exists to interpret, until Task 5 re-scores it.
    Calling those "unclamped" would report "nothing was clamped" for a run where
    four documents were, which is worse than reporting nothing. So: third bucket,
    and clamped/unclamped stay empty until the run is actually re-scored."""
    docs = [_dpi_doc("D1", 0.0, 0.60, 100.0), _dpi_doc("D2", 0.0, 0.50, 140.0)]
    report = aggregate("r", RunConfig(model_id="stub", dpi=300),
                       ReviewCostWeights(), MatchParams(), docs)

    digest = summarize(report, lambda d: f"hash-{d}")
    split = digest["clamped_vs_unclamped"]

    assert digest["clamped_docs"] == []
    assert split["unknown_dpi"]["n"] == 2
    assert split["clamped"]["n"] == 0
    assert split["unclamped"]["n"] == 0          # NOT 2 — this is the whole point
    assert split["unclamped"]["macro_mean_recall"] is None
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/eval/test_report.py -k "clamped or recorded_dpi" -v`

Expected: FAIL with `KeyError: 'clamped_docs'`.

- [ ] **Step 7: Write minimal implementation**

In `app/eval/report.py`, add a module constant next to `N_BOOTSTRAP` (line 11):

```python
# How many clamped documents `summarize` lists in detail. Deliberately NOT
# summarize()'s `top`, which sizes the worst-docs triage list: a diagnostic list
# must not shorten because someone retuned an unrelated knob. The true count is
# always clamped_vs_unclamped.clamped.n, so truncation is visible by comparing
# len(clamped_docs) against it.
CLAMP_LIST_MAX = 25
```

Then insert above `def summarize`:

```python
def _clamp_split(report: RunReport, anonymizer,
                 limit: int = CLAMP_LIST_MAX) -> Tuple[List, Dict]:
    """Clamped documents, and clamped-vs-unclamped macro means.

    render.py reduces dpi on sheets that would exceed the 80 MP budget, so those
    documents are extracted at up to a third of the requested resolution. Before
    anyone reads a low recall as a statement about the model, this says whether
    the misses concentrate there. Ids come from the local salt, so they join to
    worst_docs — unlike the predict log's, which are minted per container.

    THREE buckets, not two. effective_dpi is 0.0 in any DocScore written before
    the field existed, and folding those into "unclamped" would report "nothing
    was clamped" for a run where four documents were — a confident wrong answer
    from a view whose entire job is interpretability. Unknown gets its own
    bucket, so `unknown_dpi.n > 0` reads as "re-score before believing this"."""
    requested = report.config.dpi
    known = [d for d in report.doc_scores if d.effective_dpi > 0.0]
    unknown = [d for d in report.doc_scores if d.effective_dpi <= 0.0]
    clamped = sorted((d for d in known if d.effective_dpi < requested - 0.5),
                     key=lambda d: d.effective_dpi)
    clamped_ids = {d.doc_id for d in clamped}
    rest = [d for d in known if d.doc_id not in clamped_ids]

    def block(docs):
        n = len(docs)
        return {
            "n": n,
            # MACRO: unweighted mean over documents. The headline micro_recall
            # pools rows across the run, so the two are different statistics —
            # the name says so, because an unlabelled "mean_recall" next to a
            # micro headline is exactly the mis-read this plan exists to prevent.
            "macro_mean_recall": (round(sum(d.recall for d in docs) / n, 4)
                                  if n else None),
            "mean_review_cost": (round(sum(d.review_cost for d in docs) / n, 2)
                                 if n else None),
        }

    listed = [{"doc": anonymizer(d.doc_id),
               "effective_dpi": round(d.effective_dpi),
               "recall": round(d.recall, 4),
               "review_cost": d.review_cost} for d in clamped[:limit]]
    return listed, {"clamped": block(clamped), "unclamped": block(rest),
                    "unknown_dpi": block(unknown)}
```

Inside `summarize`, add after the `causes, misplaced = ...` line:

```python
    clamped_docs, clamp_split = _clamp_split(report, anonymizer)
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
salt (so they join to worst_docs) alongside clamped-vs-unclamped macro mean
recall and review cost. \"Did the misses cluster on the clamped drawings?\" is
now a number.

Three buckets, not two. effective_dpi defaults to 0.0, so every DocScore written
before this commit has no recorded dpi -- folding those into \"unclamped\" would
make a stale report answer \"nothing was clamped\" for the very run where four
documents were. unknown_dpi carries them instead, and a non-zero count there
reads as \"re-score before believing this block\". The three counts sum to
n_docs, so the split cannot silently drop or double-count a document.

The recall figure is named macro_mean_recall because it is an unweighted mean
over documents while the headline micro_recall pools rows; an unlabelled
\"mean_recall\" sitting next to a micro headline invites exactly the mis-read
this change exists to prevent. The detail list is capped by its own
CLAMP_LIST_MAX, not by summarize()'s worst-docs `top`.

SCHEMA_VERSION is unchanged and effective_dpi defaults to 0.0, so the existing
report and all 20 dumps keep parsing.

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

The instrument works because `app/pipeline/extract.py:225` sets `c.kind = kind`
from the `Detection`, so production characteristics carry a real kind. The
`or "unset"` fallback below exists because `Characteristic.kind` defaults to `""`
(`app/models.py:14`) — if `unset` ever dominates the breakdown, the detector
stopped labelling and the number means nothing. That is a signal, not noise.

**Done when:**

| indicator | how it is measured | passes at |
|---|---|---|
| both tests were watched failing first | Steps 2 and 6 output | `AttributeError: ... 'pred_kinds'`, then `KeyError: 'pred_kinds'` |
| `python -m pytest tests/eval/test_report.py tests/eval/test_score.py -q` | count | 25 passed (16 report + 9 score) |
| kinds account for every prediction | `sum(pred_kinds.values()) == n_pred`, asserted in both new tests | exact equality |
| false kinds account for every false detection | `sum(false_kinds.values()) == counts["false_detection"]` | exact equality |
| scoring is unchanged | `python -m pytest tests/eval/test_score.py -q` | the 6 pre-existing tests still pass untouched |
| the metric was not redefined | `git diff -U0 app/eval/models.py app/eval/score.py \| grep -c "score_kinds\|max_geo_frac\|value_bonus\|misplaced_frac\|value_sim_min"` | `0` — no match-param or gold-filter line touched |

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
    # Conservation: the breakdown must account for every prediction and every
    # false detection, or "N of 663" is quoting an unverified denominator.
    assert sum(s.pred_kinds.values()) == s.n_pred
    assert sum(s.false_kinds.values()) == s.counts["false_detection"]
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
        # counts is derived from false_kinds, never stated independently: a
        # fixture that contradicts its own taxonomy cannot test a conservation
        # identity.
        return DocScore(doc_id=doc_id, gold_hash="g" + "0" * 15, n_gold=5,
                        n_pred=sum(pred_kinds.values()),
                        counts={"correct": 5,
                                "false_detection": sum(false_kinds.values())},
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
    # Same conservation identity at run level. Task 5 reports "N of 663 false
    # detections are kinds the metric removed from gold"; this is what makes 663
    # a checked denominator rather than a quoted one.
    assert sum(digest["pred_kinds"].values()) == digest["n_pred"]
    assert (sum(digest["false_detections_by_kind"].values())
            == digest["taxonomy"]["false_detection"])
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

**Done when:**

| indicator | how it is measured | passes at |
|---|---|---|
| all five tests were watched failing first | Steps 2 and 6 output | `ImportError: cannot import name 'salt_is_persistent'`, then the missing-warning assert |
| `python -m pytest tests/eval/test_anon.py tests/eval/test_runner_e2e.py -q` | count | 29 passed (8 anon + 21 e2e) |
| the probe never creates what it is probing for | `salt_is_persistent` body contains no `write_text` / `urandom`, and the call sits above `anon = _anon(args)` | `grep -n "salt_is_persistent\|_anon(args)" app/eval/runner.py` shows the check first |
| the developer's real salt is untouched by the suite | `sha256sum ~/.claude/sindri-doc-salt` before and after `python -m pytest -q` | identical digest |
| the warning is actionable, not just alarming | `test_predict_warns_when_the_doc_ids_are_throwaway` | stderr names both `throwaway` and `runner summary` |
| no salt is shipped to the GPU host | `grep -c -- "-e SINDRI_DOC_SALT" run_baseline_gpu.sh` | `0` — the fix is a warning, not a transfer (Step 9's comment mentions the var by name, so grep for the `-e` flag, not the bare string) |

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
- Modify: `docs/eval/baseline-summary.json` (regenerated; untracked today)

  It is committable, and all three gates were checked: `.gitignore` blocks
  `*.gold.json`, `*.pred.json`, `*.report.json` and `doc_id_map*.json` — this
  name matches none of them; `.git/hooks/pre-commit`'s `blocked_name` cases do
  not match it either; and that hook's JSON **content** scan greps staged JSON
  for `"(field_errors|raw_text|characteristics|position_pt|upper_tol|lower_tol)"`,
  none of which any of the six new keys or their values can produce. If a commit
  is ever rejected here, the digest leaked something — investigate, do not reach
  for `SINDRI_ALLOW_DATA_COMMIT=1`.

**Done when:**

| indicator | how it is measured | passes at |
|---|---|---|
| the headline did not move | Step 2 stdout vs §0.3 | `docs=20 mean_review_cost=245.30 recall=0.350 escaped_rate=0.182` |
| every new view carries signal | Step 3's invariant command | exits 0, prints `interpretability DoD OK` |
| the routing decision is answerable without opening a report | Step 5 | all four items answered from `docs/eval/baseline-summary.json` alone |
| nothing value-bearing was committed | `git show --stat HEAD` | one file: `docs/eval/baseline-summary.json` |

- [ ] **Step 1: Verify the whole suite and the guard**

Run: `python -m pytest -q`
Expected: `405 passed, 2 skipped` — 391 before this plan plus the 14 tests it
adds (2 in Task 1, 5 in Task 2, 2 in Task 3, 5 in Task 4). The 2 skips are
`tests/test_detect_gpu.py` and are expected off a GPU host. Zero failures.

A count below 405 with zero failures means tests were skipped or never written,
not that the plan is done — check the per-file counts in each task's "Done when"
table (report 16, score 9, anon 8, e2e 21).

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

Then prove it rather than eyeball it. This command names no protected path, so
the guard is not involved; it reads only the committed values-blind digest:

```bash
python3 -c "import json; s=json.load(open('docs/eval/baseline-summary.json')); t=s['taxonomy']; c=s['clamped_vs_unclamped']; assert sum(s['pred_kinds'].values()) == s['n_pred'], 'pred_kinds does not sum to n_pred'; assert sum(s['false_detections_by_kind'].values()) == t['false_detection'], 'false kinds do not sum to false_detection'; assert sum(s['error_causes'].values()) == t['flagged_error'] + t['escaped_error'], 'causes do not cover the matched-but-wrong rows'; assert c['clamped']['n'] + c['unclamped']['n'] + c['unknown_dpi']['n'] == s['n_docs'], 'dpi buckets do not partition the corpus'; assert c['unknown_dpi']['n'] == 0, 'some documents have no recorded dpi -- re-score before reading the split'; assert len(s['clamped_docs']) == c['clamped']['n'] == 4, 'expected exactly 4 clamped documents'; assert s['pred_kinds'].get('unset', 0) == 0, 'detector stopped labelling kinds -- the breakdown is meaningless'; print('interpretability DoD OK')"
```

Expected: `interpretability DoD OK`. Every assertion ties a new view to a number
that already existed, so a view that is empty, double-counted, or quoting a
denominator it cannot reproduce fails here instead of in a routing decision.

If `unknown_dpi` is non-zero, Step 2 did not actually re-score with the Task 2
code — re-run it. If `unset` is non-zero, `app/pipeline/extract.py` stopped
setting `Characteristic.kind`; stop and report that, because Task 3's whole
measurement depends on it.

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
2. **`clamped_vs_unclamped`.** If clamped `macro_mean_recall` is far below the
   unclamped one, part of the 63% miss cost is the render budget rather than the
   model, and Rung 1 should consider tiled rendering for oversized sheets before
   touching detection thresholds. Two constraints on how you say it: with n=4 vs
   n=16 this is a signal, not a result; and compare the two blocks **to each
   other only** — both are macro means over documents, while the 0.350 headline
   is `micro_recall` over rows, so "clamped recall is X vs the 0.350 baseline" is
   a category error. State the `unknown_dpi` count as 0 to show the split is
   based on real recorded dpi.
3. **`false_detections_by_kind`.** The non-`dimension` share is the measurement
   artefact from §0.4 gap 3. Report it as "N of 663 false detections are kinds
   the metric removed from gold" — and only after Step 3's invariant command
   confirmed the bucket sums to 663, so 663 is a checked denominator rather than
   a quoted one. Then present the options — filter predictions to `score_kinds`,
   or admit verbal gold under the existing value-matching mode — **without
   picking one**. Both change `MatchParams`, which is the comparability guard, so
   either one makes this baseline incomparable with everything scored after it.
   That is the user's decision.
4. **The standing read.** `missed` carries 63% of the review cost, which points
   at Rung 1 detection under handoff §6. Field accuracy on matched rows is
   30.5%, so perception work will matter regardless of what the cause split
   says — the question the cause split answers is whether it starts at the
   parser or at the prompts.

---

## Definition of Done

Each row is a pass/fail check with the command that decides it. No row is
satisfied by inspection, and none is satisfied by "the tests pass" — the point of
this plan is a *readable* baseline, so the bar includes whether the output can be
cross-checked and whether the routing decision is actually answerable at the end.

### A. Gates — must all pass before Task 5 Step 4 commits

- [ ] **Suite green at the exact count.** `python -m pytest -q` →
      `405 passed, 2 skipped, 0 failed`. Not "≥ 391": an exact count is what
      catches tests that were planned and never written. Per-file: report 16,
      score 9, anon 8, e2e 21.
- [ ] **Guard intact.** `bash ~/.claude/hooks/test-sindri-guard.sh` →
      `guard: 32 passed, 0 failed`. Re-run after any guard edit (there should be
      none).
- [ ] **Schema frozen.** `grep -n "^SCHEMA_VERSION" app/eval/models.py` → `= 1`.
- [ ] **Old artifacts still parse.** Two separate guarantees, both checked:
      `PredictionDump` is untouched by this plan, so Task 5 Step 2 loading all 20
      dumps without a validation error proves the dump side. For the report side,
      every added `DocScore` field carries a default — demonstrated by the tests
      that build `DocScore` without them (`_doc`, `_pair`'s doc,
      `_report_with_client_values`) and still pass through `summarize`.
- [ ] **Headline unmoved.** Step 2 stdout is character-identical to
      `baseline-dev: docs=20 mean_review_cost=245.30 recall=0.350 escaped_rate=0.182`.
      Any drift means scoring semantics changed — stop and investigate.
- [ ] **Commit shape.** `git log --oneline -5` → four commits from Tasks 1–4,
      one from Task 5, each with a body explaining *why* and the `Co-Authored-By`
      trailer.

### B. Conservation — every new number reconciles against an old one

Verified in one shot by Task 5 Step 3's command (`interpretability DoD OK`):

- [ ] `sum(pred_kinds.values()) == n_pred` → 830
- [ ] `sum(false_detections_by_kind.values()) == taxonomy.false_detection` → 663
- [ ] `sum(error_causes.values()) == flagged_error + escaped_error` → 116
- [ ] `clamped.n + unclamped.n + unknown_dpi.n == n_docs` → 20
- [ ] `len(clamped_docs) == clamped.n` → 4 (equal means nothing was truncated)

An aggregate that cannot be tied back to a pre-existing count is not a finding;
it is a number asking to be trusted. That is the failure mode this section exists
to prevent.

### C. Non-degeneracy — the views carry signal, not just structure

- [ ] `unknown_dpi.n == 0` — the split rests on recorded dpi, not on the 0.0
      default. Non-zero means the report was not re-scored.
- [ ] `pred_kinds["unset"]` absent or 0 — the detector is still labelling kinds.
      A large `unset` bucket makes Task 3's entire measurement meaningless and
      must be reported, not worked around.
- [ ] `error_causes` has at least one non-zero key, and its total is 116 — not
      an empty dict from a stale report.
- [ ] `clamped_docs` has 4 entries at roughly 109 / 208 / 225 / 225 dpi,
      matching §0.2's observed clamps.

### D. Safety — nothing about this plan widens data exposure

- [ ] **Digest is values-blind.**
      `grep -cE '"(field_errors|raw_text|characteristics|position_pt|upper_tol|lower_tol)"' docs/eval/baseline-summary.json`
      → `0`. This is the same expression `.git/hooks/pre-commit` uses, so a pass
      here predicts a clean commit.
- [ ] **No client values in the new code paths.** `_note_counts` reads only the
      `cause:` / `misplaced` vocabulary and `_kind_totals` only detector kind
      names — neither touches `field_errors`. Covered by
      `test_summary_cause_aggregation_never_reads_client_values`.
- [ ] **Guard not widened.** `git diff --stat` names no file under
      `~/.claude/hooks/`.
- [ ] **No salt left the machine.** `grep -c -- "-e SINDRI_DOC_SALT" run_baseline_gpu.sh` → `0`.
- [ ] **Real salt untouched.** `sha256sum ~/.claude/sindri-doc-salt` is identical
      before and after the full suite run.
- [ ] **Nothing value-bearing staged.** `git status --short` before every commit;
      `git show --stat HEAD` after Task 5 names exactly one file,
      `docs/eval/baseline-summary.json`.

### E. Decision readiness — the actual deliverable

- [ ] All four items in Task 5 Step 5 are answerable from
      `docs/eval/baseline-summary.json` **alone**, with no `*.report.json` opened
      at any point. That is the whole purpose: handoff §6's routing rule stops
      resting on inference.
- [ ] The `cause:` split is reported with its scope stated — 116 of 477 rows,
      deciding parser-vs-perception, **not** reading-vs-detection.
- [ ] The clamped comparison is reported as a signal at n=4 vs n=16, and its
      macro means are not compared to the micro headline.
- [ ] The `false_detection` options are presented without a recommendation, and
      the plan stops. No Rung 1 work begins.

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
decision and needs no re-score. Tasks 2 and 3 add DocScore fields, so their
views stay empty until Task 5 re-scores — do not read an intermediate summary as
a result. Task 3 measures the false-detection asymmetry but must NOT change what
is scored: MatchParams is the comparability guard.

Every task has a "Done when" table, and the plan ends with a Definition of Done
in five parts (gates, conservation, non-degeneracy, safety, decision readiness).
Treat those as the acceptance bar, not the checkbox list. In particular each new
aggregate must reconcile against a count that already exists — Task 5 Step 3 has
a single command that checks all five identities and prints
`interpretability DoD OK`.

Full suite must stay green (391 tests today, 405 after this plan — an exact
count, not a floor) plus `bash ~/.claude/hooks/test-sindri-guard.sh` (32 cases).
Do not bump SCHEMA_VERSION — it would invalidate the report and all 20 dumps.

Task 5 re-scores locally (no GPU needed — the dumps are already here), then
report the routing read from section Task 5 step 5 and stop. Do not start Rung 1
work without my go-ahead.
```
