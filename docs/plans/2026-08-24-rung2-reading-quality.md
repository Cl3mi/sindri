# Rung 2 — Reading Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

> **Phase A is complete (Tasks 1–10).** Its results are in
> [§ Phase A results](#phase-a-results) at the end of this document, and they
> **overturn the read-prompt hypothesis this plan was written around**. Read that
> section before Phase C: `readtol` is dead, the two arms are `readcenter` and
> `detectbox`, and two of the cheapest wins found need no GPU at all.

**Goal:** Find out *which* part of the read stage is wrong, using measurements
that cost no GPU, and then spend exactly two GPU arms on the read prompt the
evidence points at.

**Architecture:** Three phases. Phase 0 fixes the reproduction gate so Phase A
can reuse it. Phase A adds four values-blind diagnostics to the eval harness and
runs them against artifacts already on this machine — no prediction, no GPU. It
splits the 196 matched-but-wrong rows by *which field* failed, by *how* it failed
(omitted vs misread vs invented), by whether the pair was even matched to the
right callout, and by read confidence. Phase C turns the five hard-coded prompts
into a variant registry selected by environment variable, which is what makes two
prompt arms runnable *concurrently* on the two H100s, and makes each arm name
itself in `RunConfig.extra` instead of only changing an opaque hash.

**Tech Stack:** Python 3, pydantic, pytest, bash. No new dependencies.

---

## Read this before Task 1

* `CLAUDE.md` in the repo root — NDA rules, house conventions, and the list of
  measured dead ends. Non-negotiable.
* `docs/plans/2026-08-21-direction-run-findings.md` — the evidence base. Where it
  disagrees with `docs/plans/2026-08-20-session-handoff.md`, the findings win.
* Every command that names `/home/clemi/sindri-client-data` must be a **single,
  unpiped, unchained** invocation of `python3 -m app.eval.runner <subcommand>`.
  No `| head`, no `&&`, no `>`. That mistake has already cost time in this
  session. Post-process in a separate call that names no protected path.
* `bash ~/.claude/hooks/test-sindri-guard.sh` must print `32 passed, 0 failed`
  after any task. Never widen the guard.

Shell shorthand used throughout: `R=/home/clemi/sindri-client-data`. Write it out
in full in real commands — the guard inspects the command string, and an
unexpanded variable is fine but a chained expansion is not.

---

## Decisions already closed — do not re-open these

**1. The `false_detection` metric question: no change to `MatchParams`.**

Findings §7.1 says 91 false detections (`theoretical` 85 + `material` 6) "have no
possible gold counterpart". The digest it was written from contradicts that:
`matched_by_pred_kind` records `theoretical: 17` and `material: 1`. The reason is
in `app/eval/normalize.py:46` — `_DIMENSION_WORDS` is built as
`list(CHAR_TYPE_SYNONYMS) + [...]`, and `CHAR_TYPE_SYNONYMS` contains
`"theoretical"`, `"theoretisch"`, `"material"` and `"werkstoff"`. So gold rows
with those labels return `"dimension"` from `char_type_kind`, are inside
`score_kinds`, and 18 of them actually matched. "No possible gold counterpart" is
a property of those 91 individual rows, not of the kind. There is nothing
structural to fix, so nothing that touches `MatchParams`, so comparability with
the 174.30 baseline is preserved. Charging w=2 is also correct under the metric's
own semantics: a reviewer must look at an unballooned prediction and reject it.

**2. The reading stage's headroom is −35.6 cost points.**

`10(169) + 5(129) + 2(522) + 1(107) = 3486`, ÷20 = 174.30. Of that, misses are
1690 (48.5%), false detections 1044 (30.0%), escaped errors 645 (18.5%), flagged
rows 107 (3.1%). Turning all 196 wrong rows correct is worth `645 + 67 = 712`,
i.e. **174.30 → 138.7**. Read a −4 arm as "captured ~11% of the available
headroom", not as noise: the noise floor on this corpus is exactly zero.

**3. Prompt arms are judged on `field_acc` rising, not only on cost falling.**

`needs_review` costs 1 and `escaped_error` costs 5, and 63.6% of matched rows are
wrong. A prompt edit shifts token-level confidences, which shifts
`app/pipeline/review.py:LOW_CONF` outcomes, which moves rows between
`escaped_error` (5) and `flagged_error` (1) for reasons that have nothing to do
with reading accuracy. `field_acc` is invariant to that; cost is not. So an arm
must lower cost **and** raise `field_acc` **and** move the specific Phase A
bucket it was designed to move.

This is the same exposure in a sharper form: flagging every matched row gives
`10(169) + 5(0) + 2(522) + 1(308) = 3042` → **152.10, i.e. −22.20**, with
`field_acc` unchanged at 0.3636 and `escaped_rate` → 0. It passes all three
conditions of the current verdict rule. **Do not ship that** — a pipeline that
flags everything delivers no review saving at all, which is the entire product.
It is recorded here because it is a weights question for the client
(`flag=1` vs `escaped=5`), not a code change.

**4. Two prompts are excluded a priori, saving a card each.** `_TITLE_PROMPT`
(title fields are never scored) and `_NOTES_PROMPT` (note rows are outside
`score_kinds`; its only path to the headline is `known_note_positions` flagging
the 5 matched `note`-kind rows). `_GDT_PROMPT` is deprioritised — its ceiling is
the 31 matched `gdt` rows.

---

## File Structure

| file | status | responsibility |
|---|---|---|
| `app/eval/gate.py` | **create** | the reproduction gate as a testable unit; absent comparison point is a failure, not a skip |
| `app/eval/reparse.py` | **create** | bounded-gain estimate for a parser change, from stored `raw_text`; the one place eval reaches into the parser on purpose |
| `app/eval/report.py` | modify | four new values-blind aggregates in `summarize` |
| `app/eval/score.py` | modify | two new values-blind per-pair note vocabularies (`missing:`/`wrong:`/`spurious:`, `conf:`) |
| `app/eval/runner.py` | modify | `--reparse-check` flag; `_prompt_sha256` over *effective* prompts; prompt variant names into `RunConfig.extra` |
| `app/pipeline/ocr/vlm_backend.py` | modify | prompt variant registry + accessors |
| `run_experiment_gpu.sh` | modify | delegate the gate, un-nest it, add `SKIP_PUSH`/`SKIP_BUILD`, register the two prompt arms |
| `tests/eval/test_gate.py` | **create** | gate behaviour |
| `tests/eval/test_reparse.py` | **create** | re-parse diagnostic behaviour + the coupling guard |
| `tests/eval/test_report.py` | modify | the four aggregates, their conservation identities, and their values-blindness |
| `tests/eval/test_score.py` | modify | the two note vocabularies |
| `tests/test_vlm_prompt.py` | modify | registry defaults, loud failure, and the `prompt_sha256` reproduction proof |
| `tests/test_experiment_script.py` | **create** | structural regression guards on the shell driver |

`score.py` stays free of pipeline imports (it is documented "Pure CPU; imports
nothing from the model stack"), which is why the re-parse diagnostic gets its own
module rather than living inside scoring.

---

# Phase 0 — the reproduction gate

## Task 1: Extract the reproduction gate into a testable module

The gate at `run_experiment_gpu.sh:149` is nested inside
`if [ -f "$CONTROL_REPORT" ]`. With no baseline report on disk the control arm
runs, prints no gate line, and the run proceeds as if gated (findings §8.1). A
gate an absent file can skip is not a gate. Extracting it also gives Phase A a
tested reproduction check for its re-score.

**Files:**
- Create: `app/eval/gate.py`
- Create: `tests/eval/test_gate.py`
- Modify: `run_experiment_gpu.sh` (replace the `PYGATE` heredoc, lines ~146–163)
- Create: `tests/test_experiment_script.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/eval/test_gate.py`:

```python
"""The reproduction gate. Each test pins a failure mode that has to be LOUD:
the version this replaced could be skipped by an absent file, which let a
direction run proceed as if it had been gated."""
import json

from app.eval.gate import check_reproduction, main


def _cmp(tmp_path, deltas, name="cmp.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"per_doc_deltas": deltas}), encoding="utf-8")
    return path


def test_absent_comparison_point_fails_instead_of_skipping(tmp_path):
    ok, message = check_reproduction(tmp_path / "does-not-exist.json")
    assert ok is False
    assert "no comparison point" in message


def test_all_zero_deltas_passes_and_states_the_count(tmp_path):
    ok, message = check_reproduction(_cmp(tmp_path, {"a": 0.0, "b": 0.0}))
    assert ok is True
    assert "2 per-document deltas" in message


def test_any_nonzero_delta_fails_and_names_the_drifted_documents(tmp_path):
    ok, message = check_reproduction(_cmp(tmp_path, {"a": 0.0, "b": -1.5}))
    assert ok is False
    assert "1 of 2" in message
    assert "'b': -1.5" in message


def test_empty_delta_map_fails_because_nothing_was_compared(tmp_path):
    ok, message = check_reproduction(_cmp(tmp_path, {}))
    assert ok is False
    assert "no per_doc_deltas" in message


def test_unreadable_file_fails_rather_than_raising(tmp_path):
    path = tmp_path / "truncated.json"
    path.write_text('{"per_doc_deltas": {"a": 0.0', encoding="utf-8")
    ok, message = check_reproduction(path)
    assert ok is False
    assert "unreadable" in message


def test_main_exits_nonzero_on_failure(tmp_path):
    assert main([str(tmp_path / "missing.json")]) == 1
    assert main([str(_cmp(tmp_path, {"a": 0.0}))]) == 0
```

Create `tests/test_experiment_script.py`:

```python
"""Structural guards on run_experiment_gpu.sh. The script cannot be unit-tested
end to end (it needs a GPU host), so these pin the two properties that were
actually wrong: the gate was inlined as a heredoc, and it sat inside the
`[ -f "$CONTROL_REPORT" ]` test that made it skippable."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "run_experiment_gpu.sh"


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_gate_is_delegated_to_the_tested_module_not_inlined():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "PYGATE" not in text, "the gate was re-inlined as a heredoc"
    assert "python3 -m app.eval.gate" in text


def test_gate_is_not_nested_inside_the_control_report_existence_test():
    """A missing baseline report must FAIL the control arm, not skip the gate."""
    text = SCRIPT.read_text(encoding="utf-8")
    guard = text.index('if [ -f "$CONTROL_REPORT" ]')
    gate = text.index("python3 -m app.eval.gate")
    assert text.count("\nfi\n", guard, gate) >= 1, (
        "no `fi` between the CONTROL_REPORT test and the gate call — "
        "the gate is still nested inside it")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_gate.py tests/test_experiment_script.py -q`

Expected: collection error for `test_gate.py`
(`ModuleNotFoundError: No module named 'app.eval.gate'`), and
`test_gate_is_delegated_to_the_tested_module_not_inlined` /
`test_gate_is_not_nested_inside_the_control_report_existence_test` FAIL.
`test_script_is_syntactically_valid` passes already — that is correct, it is a
regression guard, not a new requirement.

- [ ] **Step 3: Write the implementation**

Create `app/eval/gate.py`:

```python
"""The reproduction gate: a control arm that changed nothing must reproduce the
baseline exactly, and a MISSING comparison point must fail loudly.

Lives here rather than inline in run_experiment_gpu.sh because the shell version
was nested inside `if [ -f "$CONTROL_REPORT" ]`: with no baseline report on disk
the control arm ran, printed no gate line, and the whole run proceeded as if it
had been gated (findings §8.1). A gate an absent file can skip is not a gate.

Reads only a `runner compare` output. _cmd_compare has already replaced the
per-document keys with salted hashes, so the failure message can name the
drifted documents without naming a client part number."""
import json
import sys
from pathlib import Path
from typing import Tuple


def check_reproduction(cmp_path) -> Tuple[bool, str]:
    """Return (ok, message) for a compare JSON that must show no drift at all.

    Every non-pass is a failure, including the boring ones: an absent file, an
    unreadable file, and an empty delta map all mean "this run was not
    verified", and reporting any of them as a pass is the exact defect this
    module exists to remove."""
    path = Path(cmp_path)
    if not path.exists():
        return False, (
            f"REPRODUCTION GATE FAILED: no comparison point at {path.name} — "
            f"the control arm has nothing to reproduce against. Score the "
            f"baseline first; do NOT interpret any treatment arm.")
    try:
        cmp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, (f"REPRODUCTION GATE FAILED: {path.name} is unreadable "
                       f"({type(e).__name__})")
    deltas = cmp.get("per_doc_deltas") or {}
    if not deltas:
        return False, (f"REPRODUCTION GATE FAILED: {path.name} has no "
                       f"per_doc_deltas — nothing was compared")
    bad = {d: v for d, v in deltas.items() if v != 0.0}
    if bad:
        return False, (
            f"REPRODUCTION GATE FAILED: {len(bad)} of {len(deltas)} document(s) "
            f"drifted: {bad}. The predict path changed since the committed "
            f"baseline; find out why before interpreting any treatment arm.")
    return True, (f"reproduction gate OK: all {len(deltas)} per-document deltas "
                  f"are exactly 0.0")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: python -m app.eval.gate <compare.json>", file=sys.stderr)
        return 2
    ok, message = check_reproduction(argv[0])
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

In `run_experiment_gpu.sh`, replace the whole block from
`    if [ -f "$CONTROL_REPORT" ]; then` down to and including the `    fi` that
closes it (the `compare` call, the `PYGATE` heredoc, and the
`if [ $? -ne 0 ]` check) with:

```bash
    if [ -f "$CONTROL_REPORT" ]; then
        echo "-- compare $run vs committed baseline --"
        python3 -m app.eval.runner compare \
            "$CONTROL_REPORT" "$LOCAL_ROOT/reports/$run-$SPLIT.report.json" \
            --out "$HERE/docs/eval/$run-vs-control.json" >/dev/null \
            || echo "NOTE: not comparable — see stderr above" >&2
    else
        echo "NOTE: no control report at $CONTROL_REPORT — nothing to compare" >&2
    fi

    # REPRODUCTION GATE, deliberately OUTSIDE the existence test above. The
    # control arm changes no knob and scoring is deterministic on this corpus,
    # so every per-document delta must be exactly 0.0. A missing comparison
    # point must FAIL this arm rather than silently skip the gate, which is what
    # the previous nested version did (findings §8.1). app/eval/gate.py treats
    # an absent file as a failure, so no `-f` test belongs here.
    if [ "$arm" = "control" ]; then
        if ! python3 -m app.eval.gate "$HERE/docs/eval/$run-vs-control.json"; then
            echo "ABORTING: control did not reproduce" >&2
            FAILED+=("control-reproduction"); break
        fi
    fi
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_gate.py tests/test_experiment_script.py -q`
Expected: `9 passed`

Run: `python -m pytest -q`
Expected: `450 passed, 2 skipped` (441 + 9 new)

- [ ] **Step 5: Commit**

```bash
git add app/eval/gate.py tests/eval/test_gate.py tests/test_experiment_script.py run_experiment_gpu.sh
git commit -m "$(cat <<'EOF'
fix(eval): make the reproduction gate unskippable

The gate lived inside `if [ -f "$CONTROL_REPORT" ]`, so an absent baseline
report let the control arm run, print no gate line, and the whole direction run
proceed as if it had been verified. A gate an absent file can skip is not a
gate. Extracting it into app/eval/gate.py makes every non-pass loud — missing,
unreadable and empty all fail — and gives the Rung-2 re-score the same tested
check the control arm uses.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Phase A — four diagnostics, zero GPU

Everything in this phase is provable from artifacts already on this machine.

## Task 2: Which field each wrong row got wrong

`_compare_fields` requires `char_type`, `nominal`, `upper_tol` and `lower_tol` to
all agree, so `field_acc = 0.3636` collapses a four-way conjunction and says
nothing about which of the four moved. A dropped tolerance and a hallucinated
nominal route to different prompt text.

**Files:**
- Modify: `app/eval/report.py` (add `_FIELD_NAMES` + `_field_failure_counts`
  after `_note_counts`, which ends at line 63; add two keys to `summarize`)
- Test: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/test_report.py`:

```python
def _wrong_row_report(field_errors, taxonomy="escaped_error", notes=None):
    """One matched-but-wrong pair carrying real-looking client values, so every
    aggregate over it can be checked for leakage as well as for arithmetic."""
    pair = MatchedPair(gold_balloon=1, pred_pos=1, distance_frac=0.001,
                       fields_correct=False, field_errors=field_errors,
                       flagged=taxonomy.startswith("flagged"),
                       taxonomy=taxonomy, notes=notes or [])
    d = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=1, n_pred=1,
                 pairs=[pair], counts={taxonomy: 1}, review_cost=5.0,
                 recall=1.0, precision=1.0, escaped_rate=1.0)
    return aggregate("diag", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d])


def test_field_failures_name_the_field_and_never_the_value():
    digest = summarize(_wrong_row_report(["nominal: '6,5'!='5,5'"]),
                       lambda d: "hashed")
    assert digest["field_failures"] == {"nominal": 1}
    blob = json.dumps(digest, ensure_ascii=False)
    for leak in ("6,5", "5,5"):
        assert leak not in blob, f"field-failure aggregate leaked {leak!r}"


def test_field_failure_signature_records_the_combination_not_just_the_count():
    """'both tolerances wrong' and 'nominal wrong' are different fixes, and a
    per-field histogram alone cannot tell them apart."""
    digest = summarize(_wrong_row_report(
        ["upper_tol: '0,1'!='0,2'", "lower_tol: ''!='-0,2'"]),
        lambda d: "hashed")
    assert digest["field_failure_signatures"] == {"upper_tol+lower_tol": 1}
    assert digest["field_failures"] == {"upper_tol": 1, "lower_tol": 1}


def test_field_failure_signatures_reconcile_with_the_error_taxonomy():
    """House rule: every aggregate must cross-check against a count that already
    exists. Each wrong row contributes exactly one signature, so the signatures
    must sum to escaped_error + flagged_error."""
    digest = summarize(_wrong_row_report(["nominal: '1'!='2'"],
                                         taxonomy="flagged_error"),
                       lambda d: "hashed")
    t = digest["taxonomy"]
    assert sum(digest["field_failure_signatures"].values()) == (
        t.get("escaped_error", 0) + t.get("flagged_error", 0))


def test_unrecognised_field_name_is_bucketed_rather_than_passed_through():
    """If score._compare_fields ever changes format, the digest must degrade to
    'other' rather than forward an unvetted string into a values-blind file."""
    digest = summarize(_wrong_row_report(["SOMETHING_NEW: 'a'!='b'"]),
                       lambda d: "hashed")
    assert digest["field_failures"] == {"other": 1}
    assert "SOMETHING_NEW" not in json.dumps(digest)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_report.py -q -k field_failure`
Expected: 4 failures, each `KeyError: 'field_failures'`.

- [ ] **Step 3: Write the implementation**

In `app/eval/report.py`, after `_note_counts` (which ends at line 63) add:

```python
# The field names score._compare_fields can report, in digest order. A closed
# vocabulary is what makes reading the left-hand side of a field_errors entry
# safe: anything else is bucketed as "other" instead of forwarded.
_FIELD_NAMES = ("char_type", "nominal", "upper_tol", "lower_tol")


def _field_failure_counts(report: RunReport) -> Tuple[Dict[str, int],
                                                      Dict[str, int]]:
    """Which FIELD each matched-but-wrong row got wrong, and in what combination.

    field_acc collapses a four-way conjunction — _compare_fields requires
    char_type, nominal, upper_tol and lower_tol to agree — so "196 wrong rows"
    says nothing about which of the four moved. A tolerance the reader dropped
    and a nominal it hallucinated need different prompt text, and nothing in the
    digest distinguished them.

    Reads ONLY the field NAME: the text left of the first ":" in each entry,
    which score._compare_fields writes from the tuple above. The value text to
    the right is never touched, so this is as values-blind as _note_counts.

    Two aggregates, because they answer different questions. The per-field
    histogram sizes each field's contribution; the signature histogram says
    whether failures co-occur, which is what separates "the reader omits
    tolerances" from "the reader misreads whole callouts"."""
    per_field: Dict[str, int] = {}
    signatures: Dict[str, int] = {}
    for d in report.doc_scores:
        for p in d.pairs:
            if not p.field_errors:
                continue
            names = set()
            for err in p.field_errors:
                name = err.split(":", 1)[0].strip()
                names.add(name if name in _FIELD_NAMES else "other")
            for n in names:
                per_field[n] = per_field.get(n, 0) + 1
            # Fixed order, so the same combination always produces the same key.
            key = "+".join(n for n in _FIELD_NAMES + ("other",) if n in names)
            signatures[key] = signatures.get(key, 0) + 1
    return per_field, signatures
```

In `summarize`, after the `causes, misplaced = _note_counts(report)` line add:

```python
    field_failures, field_signatures = _field_failure_counts(report)
```

and after the `"misplaced_matches": misplaced,` entry in the returned dict add:

```python
        # Which FIELD the matched-but-wrong rows got wrong. field_acc is a
        # four-way conjunction, so without this a read-prompt arm cannot be
        # aimed. Signatures sum to escaped_error + flagged_error.
        "field_failures": field_failures,
        "field_failure_signatures": field_signatures,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_report.py -q`
Expected: all pass, including the pre-existing
`test_summarize_is_value_free_and_anonymized`.

Run: `python -m pytest -q`
Expected: `454 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "$(cat <<'EOF'
feat(eval): report which field each matched-but-wrong row got wrong

field_acc collapses a four-way conjunction (char_type, nominal, upper_tol,
lower_tol), so 0.3636 on 308 matched rows gave no way to aim a read-prompt
change: a dropped tolerance and a hallucinated nominal need different prompt
text and were indistinguishable in the digest.

Reads only the field NAME — the text left of the first ":" — from a closed
vocabulary, so the aggregate stays as values-blind as _note_counts, and an
unrecognised name degrades to "other" instead of forwarding an unvetted string.
Signatures sum to escaped_error + flagged_error, which is the cross-check.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 3: Cross-tab `cause` against `misplaced`

`misread` (144) is read as "perception failure", but a pair matched further from
its balloon than `misplaced_frac` (80 of them) may have read a *different*
callout perfectly well. Those are pairing failures and no prompt edit can move
them. Both tags are already on every pair; only the join was missing. This is the
measurement that decides whether a prompt is even the right tool.

**Files:**
- Modify: `app/eval/report.py` (add `_cause_crosstab` after
  `_field_failure_counts`; add one key to `summarize`)
- Test: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/test_report.py`:

```python
def _crosstab_report(rows):
    """rows: (cause, misplaced, taxonomy) triples, one matched-but-wrong pair
    each, all on one document."""
    pairs = []
    for i, (cause, misplaced, taxonomy) in enumerate(rows, start=1):
        notes = [f"cause:{cause}"] + (["misplaced"] if misplaced else [])
        pairs.append(MatchedPair(
            gold_balloon=i, pred_pos=i, distance_frac=0.09 if misplaced else 0.001,
            fields_correct=False, field_errors=["nominal: '1'!='2'"],
            flagged=taxonomy.startswith("flagged"), taxonomy=taxonomy,
            notes=notes))
    counts = {}
    for _, _, taxonomy in rows:
        counts[taxonomy] = counts.get(taxonomy, 0) + 1
    d = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=len(rows),
                 n_pred=len(rows), pairs=pairs, counts=counts,
                 review_cost=float(len(rows)), recall=1.0, precision=1.0,
                 escaped_rate=0.0)
    return aggregate("diag", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d])


def test_crosstab_splits_misread_by_whether_the_pair_was_misplaced():
    """A misplaced pair may have read a DIFFERENT callout correctly. No prompt
    edit can move those, so they must be separable before an arm is costed."""
    digest = summarize(_crosstab_report([
        ("misread", True, "escaped_error"),
        ("misread", False, "escaped_error"),
        ("misparse", False, "flagged_error"),
    ]), lambda d: "hashed")
    ct = digest["error_cause_crosstab"]
    assert ct["misread"]["misplaced"] == 1
    assert ct["misread"]["on_target"] == 1
    assert ct["misparse"]["misplaced"] == 0


def test_crosstab_rows_reconcile_on_both_axes():
    digest = summarize(_crosstab_report([
        ("misread", True, "escaped_error"),
        ("misread", False, "flagged_error"),
    ]), lambda d: "hashed")
    row = digest["error_cause_crosstab"]["misread"]
    assert row["misplaced"] + row["on_target"] == row["total"]
    assert row["escaped"] + row["flagged"] == row["total"]


def test_crosstab_totals_match_the_existing_error_causes_histogram():
    digest = summarize(_crosstab_report([
        ("misread", True, "escaped_error"),
        ("misparse", False, "escaped_error"),
    ]), lambda d: "hashed")
    ct = digest["error_cause_crosstab"]
    assert {k: v["total"] for k, v in ct.items()} == digest["error_causes"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_report.py -q -k crosstab`
Expected: 3 failures, `KeyError: 'error_cause_crosstab'`.

- [ ] **Step 3: Write the implementation**

In `app/eval/report.py`, after `_field_failure_counts` add:

```python
def _cause_crosstab(report: RunReport) -> Dict[str, Dict[str, int]]:
    """cause × misplaced × silent-or-flagged, for every matched-but-wrong row.

    `misread` is routinely read as "perception failure", but a pair matched
    further from its balloon than misplaced_frac may have read a DIFFERENT
    callout perfectly. Those are pairing failures, and no prompt edit can move
    them — so the share of misread that is misplaced is the number that decides
    whether a read-prompt arm is worth a card at all. Both tags were already on
    every pair (score_doc writes them); only the join was missing.

    Reads the same fixed note vocabulary as _note_counts, plus the taxonomy
    string, so it carries no client text."""
    out: Dict[str, Dict[str, int]] = {}
    for d in report.doc_scores:
        for p in d.pairs:
            cause = next((n.split(":", 1)[1] for n in p.notes
                          if n.startswith("cause:")), None)
            if cause is None:
                continue
            row = out.setdefault(cause, {"total": 0, "misplaced": 0,
                                         "on_target": 0, "escaped": 0,
                                         "flagged": 0})
            row["total"] += 1
            row["misplaced" if "misplaced" in p.notes else "on_target"] += 1
            row["escaped" if p.taxonomy == "escaped_error" else "flagged"] += 1
    return out
```

In `summarize`, immediately after the `"error_causes": causes,` entry add:

```python
        # The same rows, split by whether the pair is even on the right callout.
        # misplaced+on_target == total == escaped+flagged for every cause, and
        # the totals reproduce error_causes above.
        "error_cause_crosstab": _cause_crosstab(report),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_report.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `457 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "$(cat <<'EOF'
feat(eval): cross-tab error cause against misplaced pairs

144 misread vs 52 misparse routed the whole Rung-2 decision toward perception,
but a pair matched further from its balloon than misplaced_frac may have read a
DIFFERENT callout correctly — a pairing failure no prompt edit can move. With
80 misplaced pairs and 196 wrong rows the overlap could be large enough to
change which lever is worth a GPU card, and both tags were already on every
pair. Only the join was missing.

Reconciles two ways per cause (misplaced+on_target == total == escaped+flagged)
and its totals reproduce error_causes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 4: Regenerate every committed digest and read the answer

The two new aggregates read the frozen reports directly, so no re-score is
needed. Regenerating all five digests also validates the aggregates for free:
`finetiles` is already known to have cost −0.0662 `field_acc`, so its field
failures must look visibly worse than control's.

**Files:**
- Modify: `docs/eval/baseline-summary.json`,
  `docs/eval/exp-{control,nomerge,tightmerge,finetiles}-summary.json`

- [ ] **Step 1: Snapshot the current digests so the regeneration can be checked**

```bash
mkdir -p /tmp/claude-1000/-home-clemi-mci-sindri/dbf3bbb1-69cb-4cba-a0f6-659c2d8bfc9b/scratchpad/olddigests
cp docs/eval/baseline-summary.json docs/eval/exp-control-summary.json docs/eval/exp-nomerge-summary.json docs/eval/exp-tightmerge-summary.json docs/eval/exp-finetiles-summary.json /tmp/claude-1000/-home-clemi-mci-sindri/dbf3bbb1-69cb-4cba-a0f6-659c2d8bfc9b/scratchpad/olddigests/
```

- [ ] **Step 2: Regenerate each digest — five separate bare commands**

Each is a single unpiped, unchained `runner` invocation. Do not combine them.

```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/baseline-dev.report.json --out docs/eval/baseline-summary.json
```
```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/exp-control-dev.report.json --out docs/eval/exp-control-summary.json
```
```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/exp-nomerge-dev.report.json --out docs/eval/exp-nomerge-summary.json
```
```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/exp-tightmerge-dev.report.json --out docs/eval/exp-tightmerge-summary.json
```
```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/exp-finetiles-dev.report.json --out docs/eval/exp-finetiles-summary.json
```

Expected: each prints the digest to stdout. Confirm
`baseline-summary.json` still reports `"mean_review_cost": 174.3` and
`"micro_recall": 0.6457023060796646`.

- [ ] **Step 3: Verify no pre-existing key changed value**

The new keys must be purely additive. Anything else means the aggregates
perturbed the digest.

```bash
python3 -c "
import json, pathlib
old = pathlib.Path('/tmp/claude-1000/-home-clemi-mci-sindri/dbf3bbb1-69cb-4cba-a0f6-659c2d8bfc9b/scratchpad/olddigests')
new = pathlib.Path('docs/eval')
for f in sorted(old.glob('*.json')):
    a = json.loads(f.read_text(encoding='utf-8'))
    b = json.loads((new / f.name).read_text(encoding='utf-8'))
    for k, v in a.items():
        assert k in b, f'{f.name}: lost key {k}'
        assert b[k] == v, f'{f.name}: {k} changed {v!r} -> {b[k]!r}'
    print(f.name, 'unchanged, added:', sorted(set(b) - set(a)))
"
```

Expected: five lines, each ending
`added: ['error_cause_crosstab', 'field_failure_signatures', 'field_failures']`.

- [ ] **Step 4: Read the routing answer**

```bash
python3 -c "
import json, pathlib
for name in ('baseline', 'exp-finetiles'):
    d = json.loads((pathlib.Path('docs/eval')/f'{name}-summary.json').read_text())
    t = d['taxonomy']; wrong = t['escaped_error'] + t['flagged_error']
    print(f'--- {name}  wrong_rows={wrong}')
    print('  per field  :', d['field_failures'])
    print('  signatures :', dict(sorted(d['field_failure_signatures'].items(), key=lambda kv: -kv[1])))
    print('  crosstab   :', d['error_cause_crosstab'])
"
```

Record both blocks verbatim — they are the input to the Task 11 arm decision.

- [ ] **Step 5: Commit**

```bash
git add docs/eval/baseline-summary.json docs/eval/exp-control-summary.json docs/eval/exp-nomerge-summary.json docs/eval/exp-tightmerge-summary.json docs/eval/exp-finetiles-summary.json
git commit -m "$(cat <<'EOF'
docs(eval): regenerate every digest with the field-failure aggregates

Re-summarised, not re-scored: both new aggregates read field_errors and notes
that the frozen reports already carry, so every pre-existing key is byte-equal
and the additions are purely additive (checked key by key against the previous
digests).

Regenerating all five arms rather than just the baseline is the validation:
finetiles is independently known to have cost -0.0662 field accuracy, so its
field-failure profile has to look worse than control's. An aggregate that
cannot reproduce a difference we already measured is not measuring anything.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 5: Record *how* each field failed, not just that it did

"The reader omitted the tolerance", "the reader read a different tolerance" and
"the reader invented a tolerance gold does not have" are three different fixes.
Only emptiness is inspected, never the value, so the tags stay digest-safe.

**Files:**
- Modify: `app/eval/score.py` (add `_failure_modes` after `_compare_fields`,
  line 34; extend the notes in `score_doc`, line 115-117)
- Test: `tests/eval/test_score.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/test_score.py`:

```python
def _one_pair(pred_kwargs, gold_kwargs):
    """One gold row and one prediction on top of it, so a single pair's tags can
    be inspected without the four-row fixture's other rows interfering."""
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[GoldCharacteristic(
                       balloon=1, position_pt=(100, 100), **gold_kwargs)])
    dump = PredictionDump(
        doc_id="D", config=RunConfig(model_id="stub", dpi=300), scale=SCALE,
        page_rect=RECT, result=ExtractionResult(characteristics=[
            Characteristic(pos=1, target_region=_pt_box(100, 100),
                           **pred_kwargs)]))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    return s.pairs[0]


def test_missing_tag_when_the_pipeline_produced_nothing_for_the_field():
    """A dropped tolerance is an instruction problem ('transcribe every number
    printed'); a disagreeing one is a perception problem. Same field, different
    prompt text, so the two must not share a bucket."""
    pair = _one_pair(
        dict(char_type="Distance", nominal="20", raw_text="20"),
        dict(char_type="Distance", nominal="20", upper_tol="0,1"))
    assert "missing:upper_tol" in pair.notes
    assert not any(n.startswith("wrong:") for n in pair.notes)


def test_wrong_tag_when_both_sides_have_a_value_and_they_disagree():
    pair = _one_pair(
        dict(char_type="Distance", nominal="21", raw_text="21"),
        dict(char_type="Distance", nominal="20"))
    assert "wrong:nominal" in pair.notes


def test_spurious_tag_when_the_pipeline_invented_a_value_gold_lacks():
    pair = _one_pair(
        dict(char_type="Distance", nominal="20", upper_tol="0,1",
             raw_text="20 +0,1"),
        dict(char_type="Distance", nominal="20"))
    assert "spurious:upper_tol" in pair.notes


def test_one_failure_mode_per_field_error_so_the_two_cannot_disagree():
    """The modes and field_errors are computed from the same predicates; if the
    counts ever diverge, one of the two aggregates is lying."""
    pair = _one_pair(
        dict(char_type="Radius", nominal="21", raw_text="R21"),
        dict(char_type="Distance", nominal="20", upper_tol="0,1"))
    modes = [n for n in pair.notes
             if n.split(":", 1)[0] in ("missing", "wrong", "spurious")]
    assert len(modes) == len(pair.field_errors)


def test_correct_rows_carry_no_failure_mode_tag():
    pair = _one_pair(
        dict(char_type="Distance", nominal="20", raw_text="20"),
        dict(char_type="Distance", nominal="20"))
    assert pair.fields_correct
    assert not any(n.split(":", 1)[0] in ("missing", "wrong", "spurious")
                   for n in pair.notes)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_score.py -q -k "missing_tag or wrong_tag or spurious_tag or one_failure_mode or no_failure_mode"`
Expected: 4 failures on the assertions (`missing:upper_tol` not in notes …);
`test_correct_rows_carry_no_failure_mode_tag` passes already — correct, it is a
regression guard.

- [ ] **Step 3: Write the implementation**

In `app/eval/score.py`, after `_compare_fields` (ends line 34) add:

```python
def _failure_modes(pred, gold) -> List[str]:
    """Values-blind tags saying HOW each wrong field is wrong.

    `missing:<field>` the pipeline produced nothing and gold has a value;
    `wrong:<field>`   both are non-empty and disagree;
    `spurious:<field>` the pipeline invented a value gold does not have.

    This is the routing decision for a read-prompt arm. A dropped tolerance is
    an instruction problem — the prompt never says "transcribe every number
    printed, including a zero" — while a disagreeing one is a perception
    problem, and they need different prompt text. field_errors already records
    which field, but the digest cannot read its values, so the distinction had
    nowhere to live.

    Only emptiness is inspected, never the value itself, which is what makes
    these safe for the values-blind digest. The predicates are exactly
    _compare_fields', so the two can never disagree on WHICH fields are wrong."""
    modes = []
    if gold.char_type and not char_type_equal(pred.char_type, gold.char_type):
        empty = not str(pred.char_type or "").strip()
        modes.append(f"{'missing' if empty else 'wrong'}:char_type")
    for f in _FIELDS:
        pv, gv = getattr(pred, f), getattr(gold, f)
        if values_equal(pv, gv):
            continue
        if not canon_value(pv):
            modes.append(f"missing:{f}")
        elif not canon_value(gv):
            modes.append(f"spurious:{f}")
        else:
            modes.append(f"wrong:{f}")
    return modes
```

In `score_doc`, replace:

```python
        if errors:
            taxonomy = "flagged_error" if p.needs_review else "escaped_error"
            notes.append(f"cause:{_cause(p, g)}")
```

with:

```python
        if errors:
            taxonomy = "flagged_error" if p.needs_review else "escaped_error"
            notes.append(f"cause:{_cause(p, g)}")
            notes.extend(_failure_modes(p, g))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_score.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `462 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/score.py tests/eval/test_score.py
git commit -m "$(cat <<'EOF'
feat(eval): tag HOW each wrong field failed, not just that it did

A tolerance the reader omitted, one it read differently, and one it invented are
three different fixes: the first is an instruction problem, the second is
perception, the third is over-eager transcription. field_errors already names the
field but spells out client values, so the digest cannot read it and the
distinction had nowhere to live.

Only emptiness is inspected, never the value, so the tags are digest-safe. The
predicates are _compare_fields' own, so the mode count per pair always equals
its field_errors count -- the cross-check that stops the two aggregates from
telling different stories.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 6: Aggregate the failure modes, and admit when a report predates them

An old report carries no mode tags, and an empty dict in the digest would read as
"nothing failed" — the exact confident-wrong-answer this codebase already paid
for once with `frame_origin_frac`. So the aggregate ships with a
`not_measured` count.

**Files:**
- Modify: `app/eval/report.py` (extend `_field_failure_counts`'s caller; add
  `_failure_mode_counts`; add two keys to `summarize`)
- Test: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/test_report.py`:

```python
def test_failure_modes_are_aggregated_by_mode_and_field():
    digest = summarize(_wrong_row_report(
        ["upper_tol: ''!='0,1'", "nominal: '21'!='20'"],
        notes=["cause:misread", "missing:upper_tol", "wrong:nominal"]),
        lambda d: "hashed")
    assert digest["field_failure_modes"] == {"missing:upper_tol": 1,
                                            "wrong:nominal": 1}


def test_failure_modes_reconcile_against_the_per_field_histogram():
    """Both count one entry per wrong field per pair, so their totals must
    agree. A mismatch means one of the two is reading the report wrongly."""
    digest = summarize(_wrong_row_report(
        ["upper_tol: ''!='0,1'", "lower_tol: ''!='-0,1'"],
        notes=["cause:misread", "missing:upper_tol", "missing:lower_tol"]),
        lambda d: "hashed")
    assert (sum(digest["field_failure_modes"].values())
            == sum(digest["field_failures"].values()))
    assert digest["field_failure_modes_not_measured"] == 0


def test_a_report_written_before_the_tags_existed_says_not_measured():
    """An empty dict would read as 'no failures'. A stale report once reported
    'all 20 frames agree' for a run where 14 disagreed and cost a full analysis
    cycle; the same mistake is not available here."""
    digest = summarize(_wrong_row_report(["nominal: '21'!='20'"],
                                         notes=["cause:misread"]),
                       lambda d: "hashed")
    assert digest["field_failure_modes"] == {}
    assert digest["field_failure_modes_not_measured"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_report.py -q -k failure_mode`
Expected: 3 failures, `KeyError: 'field_failure_modes'`.

- [ ] **Step 3: Write the implementation**

In `app/eval/report.py`, after `_cause_crosstab` add:

```python
# The three ways score._failure_modes can describe a wrong field.
_FAILURE_MODES = ("missing", "wrong", "spurious")


def _failure_mode_counts(report: RunReport) -> Tuple[Dict[str, int], int]:
    """`<mode>:<field>` histogram over matched-but-wrong rows, plus the number
    of wrong rows that carry no mode tag at all.

    The second number is not decoration. A report scored before
    score._failure_modes existed carries no tags, and an empty histogram would
    read as "no field failed" — the same confident wrong answer that
    DocScore.frame_origin_frac's None default exists to prevent, which fooled
    this author once already. A non-zero not_measured means: re-score before
    believing this block."""
    counts: Dict[str, int] = {}
    not_measured = 0
    for d in report.doc_scores:
        for p in d.pairs:
            if not p.field_errors:
                continue
            tags = [n for n in p.notes
                    if n.split(":", 1)[0] in _FAILURE_MODES]
            if not tags:
                not_measured += 1
                continue
            for t in tags:
                counts[t] = counts.get(t, 0) + 1
    return counts, not_measured
```

In `summarize`, after the `field_failures, field_signatures = ...` line add:

```python
    failure_modes, modes_not_measured = _failure_mode_counts(report)
```

and after the `"field_failure_signatures": field_signatures,` entry add:

```python
        # HOW each wrong field failed: omitted, disagreeing, or invented. Sums
        # to the same total as field_failures. not_measured > 0 means the report
        # predates the tags — re-score, do not read the histogram as complete.
        "field_failure_modes": failure_modes,
        "field_failure_modes_not_measured": modes_not_measured,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_report.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `465 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "$(cat <<'EOF'
feat(eval): aggregate field failure modes with a not-measured count

Sums to the same total as field_failures, which is the cross-check. Ships with
field_failure_modes_not_measured because every report written before the tags
existed carries none, and an empty histogram would read as "no field failed" --
the same class of confident wrong answer that DocScore.frame_origin_frac's None
default exists to prevent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 7: Record each pair's read confidence bucket

Needed to size the confound in decision 3: a prompt edit shifts token
confidences, which shifts `LOW_CONF` outcomes, which moves rows between
`escaped_error` (5) and `flagged_error` (1) for reasons unrelated to reading. The
bucket edges put `LOW_CONF = 0.6` on a boundary so the flag threshold's effect is
directly readable.

**Files:**
- Modify: `app/eval/score.py` (add `_CONF_EDGES` + `_conf_bucket`; append the tag
  in `score_doc` for every pair)
- Test: `tests/eval/test_score.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/test_score.py`:

```python
def test_confidence_bucket_boundary_sits_on_the_review_threshold():
    """review.LOW_CONF is 0.6, so 0.6 must open a bucket rather than close one:
    the whole point is reading off how many rows a threshold move would flag."""
    from app.eval.score import _conf_bucket
    assert _conf_bucket(0.59) == "0.4-0.6"
    assert _conf_bucket(0.6) == "0.6-0.8"


def test_confidence_bucket_covers_the_extremes():
    from app.eval.score import _conf_bucket
    assert _conf_bucket(0.0) == "<0.2"
    assert _conf_bucket(1.0) == ">=0.8"


def test_every_matched_pair_carries_a_confidence_bucket_including_correct_ones():
    """The escaped/flagged trade needs the confidence of the rows that are
    RIGHT too -- flagging them is what a lower threshold costs."""
    pair = _one_pair(
        dict(char_type="Distance", nominal="20", raw_text="20", confidence=0.95),
        dict(char_type="Distance", nominal="20"))
    assert pair.fields_correct
    assert "conf:>=0.8" in pair.notes
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_score.py -q -k confidence_bucket`
Expected: 2 collection/attribute failures
(`ImportError: cannot import name '_conf_bucket'`) and one assertion failure.

- [ ] **Step 3: Write the implementation**

In `app/eval/score.py`, after `_FIELDS = ("nominal", "upper_tol", "lower_tol")`
(line 18) add:

```python
# Read-confidence buckets. 0.6 is a boundary on purpose: it is
# app.pipeline.review.LOW_CONF, the threshold that decides needs_review, so the
# joint histogram of bucket x taxonomy answers "how many rows would a threshold
# move flag, and how many of those are actually wrong?" without a GPU re-run.
# Duplicated rather than imported: eval must not import the pipeline module
# whose constant is under review, and a drifting copy would change only this
# diagnostic's bucket labels, not any scored result.
_CONF_EDGES = (0.2, 0.4, 0.6, 0.8)


def _conf_bucket(conf: float) -> str:
    """Label for the confidence band `conf` falls in, lower edge inclusive."""
    lo = 0.0
    for edge in _CONF_EDGES:
        if conf < edge:
            return f"<{edge:.1f}" if lo == 0.0 else f"{lo:.1f}-{edge:.1f}"
        lo = edge
    return f">={_CONF_EDGES[-1]:.1f}"
```

In `score_doc`, in the pair loop, replace:

```python
        errors = _compare_fields(p, g)
        notes = []
```

with:

```python
        errors = _compare_fields(p, g)
        notes = [f"conf:{_conf_bucket(p.confidence)}"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_score.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `468 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/score.py tests/eval/test_score.py
git commit -m "$(cat <<'EOF'
feat(eval): tag each matched pair with its read-confidence bucket

A prompt edit shifts token-level confidences, which shifts review.LOW_CONF
outcomes, which moves rows between escaped_error (w=5) and flagged_error (w=1)
for reasons that have nothing to do with reading accuracy. That confound sits in
the cost column of every prompt arm about to be run, and sizing it needed the
confidence of the RIGHT rows too -- flagging those is what a lower threshold
costs.

0.6 is a bucket boundary because it is LOW_CONF itself, so the joint histogram
answers the threshold question without a GPU re-run.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 8: Aggregate confidence against taxonomy

**Files:**
- Modify: `app/eval/report.py` (add `_confidence_by_taxonomy`; add two keys to
  `summarize`)
- Test: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/test_report.py`:

```python
def _conf_report(rows):
    """rows: (conf_bucket, taxonomy) pairs, one matched pair each."""
    pairs, counts = [], {}
    for i, (bucket, taxonomy) in enumerate(rows, start=1):
        wrong = taxonomy.endswith("error")
        pairs.append(MatchedPair(
            gold_balloon=i, pred_pos=i, distance_frac=0.001,
            fields_correct=not wrong,
            field_errors=["nominal: '1'!='2'"] if wrong else [],
            flagged=taxonomy.startswith("flagged"), taxonomy=taxonomy,
            notes=[f"conf:{bucket}"]))
        counts[taxonomy] = counts.get(taxonomy, 0) + 1
    d = DocScore(doc_id="T1025300_B", gold_hash="g" * 16, n_gold=len(rows),
                 n_pred=len(rows), pairs=pairs, counts=counts,
                 review_cost=float(len(rows)), recall=1.0, precision=1.0,
                 escaped_rate=0.0)
    return aggregate("diag", RunConfig(model_id="stub"), ReviewCostWeights(),
                     MatchParams(), [d])


def test_confidence_is_crossed_with_taxonomy_so_a_threshold_move_is_priceable():
    digest = summarize(_conf_report([("0.6-0.8", "escaped_error"),
                                     ("0.6-0.8", "correct"),
                                     (">=0.8", "correct")]),
                       lambda d: "hashed")
    assert digest["confidence_by_taxonomy"]["0.6-0.8"] == {"escaped_error": 1,
                                                           "correct": 1}
    assert digest["confidence_by_taxonomy"][">=0.8"] == {"correct": 1}


def test_confidence_histogram_covers_every_matched_pair():
    digest = summarize(_conf_report([("<0.2", "flagged_error"),
                                     (">=0.8", "correct")]),
                       lambda d: "hashed")
    total = sum(sum(v.values())
                for v in digest["confidence_by_taxonomy"].values())
    assert total == digest["n_gold"] - digest["taxonomy"].get("missed", 0)
    assert digest["confidence_not_measured"] == 0


def test_a_report_written_before_the_conf_tag_says_not_measured():
    report = _wrong_row_report(["nominal: '1'!='2'"], notes=["cause:misread"])
    digest = summarize(report, lambda d: "hashed")
    assert digest["confidence_by_taxonomy"] == {}
    assert digest["confidence_not_measured"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_report.py -q -k confidence`
Expected: 3 failures, `KeyError: 'confidence_by_taxonomy'`.

- [ ] **Step 3: Write the implementation**

In `app/eval/report.py`, after `_failure_mode_counts` add:

```python
def _confidence_by_taxonomy(report: RunReport) -> Tuple[Dict[str, Dict[str, int]],
                                                        int]:
    """Read-confidence band × taxonomy over matched pairs, plus the pairs with
    no band recorded.

    This is the price list for a review-flag threshold move: rows below a
    candidate threshold become flagged (w=1), and whether that is a win depends
    entirely on how many of them were wrong (w=5 if they escape). It also sizes
    the confound in every prompt arm — a prompt edit changes token confidences,
    so some of an arm's cost delta is threshold churn rather than reading.

    Bands are the fixed labels score._conf_bucket writes; not_measured counts
    pairs from reports that predate the tag, for the same reason
    field_failure_modes_not_measured exists."""
    out: Dict[str, Dict[str, int]] = {}
    not_measured = 0
    for d in report.doc_scores:
        for p in d.pairs:
            band = next((n.split(":", 1)[1] for n in p.notes
                         if n.startswith("conf:")), None)
            if band is None:
                not_measured += 1
                continue
            row = out.setdefault(band, {})
            key = p.taxonomy or "unset"
            row[key] = row.get(key, 0) + 1
    return out, not_measured
```

In `summarize`, after the `failure_modes, modes_not_measured = ...` line add:

```python
    conf_taxonomy, conf_not_measured = _confidence_by_taxonomy(report)
```

and after the `"field_failure_modes_not_measured": modes_not_measured,` entry
add:

```python
        # The price list for a review-flag threshold move, and the size of the
        # threshold-churn confound in any prompt arm. Covers every matched pair.
        "confidence_by_taxonomy": conf_taxonomy,
        "confidence_not_measured": conf_not_measured,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_report.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `471 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "$(cat <<'EOF'
feat(eval): cross confidence bands with the taxonomy

Two uses, one histogram. It prices a review-flag threshold move -- rows below a
candidate threshold become flagged (w=1) instead of possibly escaping (w=5), and
whether that wins depends on how many of them were wrong. And it sizes the
threshold-churn confound in every prompt arm, since a prompt edit changes token
confidences and therefore moves rows between those two buckets for reasons that
are not reading quality.

Carries confidence_not_measured for reports that predate the tag.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 9: Re-score the baseline and prove it did not move

Tasks 5 and 7 write new tags, which only exist in a report produced by a fresh
scoring pass. House rule: re-score, don't just re-summarise.

**Files:**
- Create: `docs/eval/baseline-diag-vs-baseline.json`,
  `docs/eval/baseline-diag-summary.json`

- [ ] **Step 1: Re-score into a NEW report, leaving the frozen one alone**

```bash
python3 -m app.eval.runner score --run /home/clemi/sindri-client-data/runs/baseline --gold /home/clemi/sindri-client-data/gold --splits /home/clemi/sindri-client-data/meta/splits.json --split dev --weights docs/eval/weights.json --name baseline-dev-diag --out /home/clemi/sindri-client-data/reports/baseline-dev-diag.report.json
```

Expected stdout:
`baseline-dev-diag: docs=20 mean_review_cost=174.30 recall=0.646 escaped_rate=0.270`

If `mean_review_cost` is not `174.30`, **stop**. Tasks 5 and 7 were supposed to
add notes and nothing else; a moved cost means one of them changed scoring.

- [ ] **Step 2: Compare against the frozen baseline**

```bash
python3 -m app.eval.runner compare /home/clemi/sindri-client-data/reports/baseline-dev.report.json /home/clemi/sindri-client-data/reports/baseline-dev-diag.report.json --out docs/eval/baseline-diag-vs-baseline.json
```

Expected: JSON on stdout with `"mean_delta": 0.0` and
`"ci95": [0.0, 0.0]`. Per-document keys are salted hashes.

- [ ] **Step 3: Gate it with the module from Task 1**

```bash
python3 -m app.eval.gate docs/eval/baseline-diag-vs-baseline.json
```

Expected: `reproduction gate OK: all 20 per-document deltas are exactly 0.0`

- [ ] **Step 4: Summarise and read the new blocks**

```bash
python3 -m app.eval.runner summary /home/clemi/sindri-client-data/reports/baseline-dev-diag.report.json --out docs/eval/baseline-diag-summary.json
```

```bash
python3 -c "
import json, pathlib
d = json.loads(pathlib.Path('docs/eval/baseline-diag-summary.json').read_text())
assert d['field_failure_modes_not_measured'] == 0, d['field_failure_modes_not_measured']
assert d['confidence_not_measured'] == 0, d['confidence_not_measured']
assert sum(d['field_failure_modes'].values()) == sum(d['field_failures'].values())
t = d['taxonomy']
assert sum(sum(v.values()) for v in d['confidence_by_taxonomy'].values()) == d['n_gold'] - t['missed']
print('modes  :', dict(sorted(d['field_failure_modes'].items(), key=lambda kv: -kv[1])))
print('conf   :', json.dumps(d['confidence_by_taxonomy'], indent=1, sort_keys=True))
print('identities OK')
"
```

Expected: the two blocks printed and `identities OK`. Record both verbatim.

- [ ] **Step 5: Commit**

```bash
git add docs/eval/baseline-diag-vs-baseline.json docs/eval/baseline-diag-summary.json
git commit -m "$(cat <<'EOF'
docs(eval): re-scored baseline digest with the failure-mode and confidence tags

Re-scored rather than re-summarised, because the new tags only exist in a fresh
scoring pass -- `runner summary` on the old report would have shown empty
histograms for fields that report never carried.

Gated with app/eval/gate.py: all 20 per-document deltas are exactly 0.0 against
the frozen baseline, so the two diagnostics added notes and moved no score. The
frozen baseline-dev report is untouched and remains the comparison point for
every arm.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 10: Bound a parser change without a GPU arm

Dumps store `raw_text`, so `parse_value` can be re-run over them offline. That
turns "would this parser change help?" from a 9 h GPU arm into a CPU second. The
gate on the *unmodified* parser is what keeps the coupling to `extract.py`'s hint
mapping honest.

**Files:**
- Create: `app/eval/reparse.py`
- Create: `tests/eval/test_reparse.py`
- Modify: `app/eval/runner.py` (`_cmd_score`; the `score` sub-parser at line 673)

- [ ] **Step 1: Write the failing tests**

Create `tests/eval/test_reparse.py`:

```python
"""The re-parse dry run. Its value depends entirely on the identity gate: if the
hint reconstruction drifts from extract.py, `identical` stops covering every
pair and the numbers would silently attribute extract's behaviour to the
parser."""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.reparse import _HINTS, reparse_report
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _pt_box(x, y):
    return (SCALE * (x - 15), SCALE * (y - 5), SCALE * (x + 15), SCALE * (y + 5))


def _case(pred_kwargs, gold_kwargs):
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx", page_rect=RECT,
                   characteristics=[GoldCharacteristic(
                       balloon=1, position_pt=(100, 100), **gold_kwargs)])
    dump = PredictionDump(
        doc_id="D", config=RunConfig(model_id="stub", dpi=300), scale=SCALE,
        page_rect=RECT, result=ExtractionResult(characteristics=[
            Characteristic(pos=1, target_region=_pt_box(100, 100),
                           **pred_kwargs)]))
    score = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    return reparse_report({"D": dump}, {"D": gold}, [score])


def test_unmodified_parser_reproduces_every_stored_field():
    """The gate. Stored fields came from parse_value at predict time, so
    re-parsing must reproduce them exactly -- otherwise the hint reconstruction
    is wrong and every other number here is measuring that instead."""
    r = _case(dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1"))
    assert r["n_pairs"] == 1
    assert r["identical"] == r["n_pairs"]
    assert r["would_fix"] == 0 and r["would_break"] == 0


def test_would_fix_counts_a_row_a_better_parse_would_correct():
    """A stored parse that lost the value the raw text plainly contains: this is
    the bucket a candidate parser change is trying to grow."""
    r = _case(dict(char_type="Diameter", nominal="2", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20", upper_tol="0,1",
                   lower_tol="-0,1"))
    assert r["would_fix"] == 1
    assert r["identical"] == 0


def test_would_break_counts_a_row_the_reparse_makes_wrong():
    r = _case(dict(char_type="Distance", nominal="20", raw_text="totally other"),
              dict(char_type="Distance", nominal="20"))
    assert r["would_break"] == 1


def test_hint_map_matches_extract_so_the_coupling_cannot_drift_silently():
    from app.pipeline.extract import _HINTS as pipeline_hints
    assert _HINTS == pipeline_hints


def test_report_is_values_blind():
    import json
    r = _case(dict(char_type="Diameter", nominal="2", raw_text="Ø20 +0,1 -0,1"),
              dict(char_type="Diameter", nominal="20"))
    blob = json.dumps(r, ensure_ascii=False)
    for leak in ("Ø20", "0,1", "'20'", "raw_text"):
        assert leak not in blob, f"reparse report leaked {leak!r}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_reparse.py -q`
Expected: collection error,
`ModuleNotFoundError: No module named 'app.eval.reparse'`.

- [ ] **Step 3: Write the implementation**

Create `app/eval/reparse.py`:

```python
"""Bounded-gain estimate for a parser change, computed from prediction dumps
already on disk — no GPU.

Dumps store `raw_text`, so `app.pipeline.parser.parse_value` can be re-run over
them offline and the result compared against gold. That turns "would this parser
change help?" from a 9 h GPU arm into a CPU second, which is the whole reason
the 52 misparse rows are worth looking at at all.

This is the ONE place the eval package reaches into the pipeline on purpose, and
it imports only `parser` — a stdlib-and-pydantic module — so the CPU-only score
path stays free of the model stack. The hint mapping is duplicated rather than
imported from `extract` for the same reason; `test_hint_map_matches_extract...`
fails if the two diverge, and the `identical` count below fails if the
reconstruction is wrong for any other reason.

Read `identical == n_pairs` as the gate: with an UNMODIFIED parser every stored
field must be reproducible, because that is where the stored fields came from.
Once a candidate parser edit is in place `identical` drops by design, and
`would_fix - would_break` is the bound on what that edit is worth."""
from typing import Dict, List

from app.eval.normalize import char_type_equal, values_equal
from app.pipeline.parser import parse_value

# Copy of extract._HINTS: detector kind -> parser hint. Duplicated so this
# module never imports extract (which pulls in render/detect/ocr); the equality
# test in tests/eval/test_reparse.py is what stops the copy from drifting.
_HINTS = {"material": "material", "note": "note", "gdt": "gdt",
          "theoretical": "theoretical"}

_FIELDS = ("nominal", "upper_tol", "lower_tol")


def _matches_gold(c, gold) -> bool:
    """Same verdict score._compare_fields reaches, expressed as a bool."""
    if gold.char_type and not char_type_equal(c.char_type, gold.char_type):
        return False
    return all(values_equal(getattr(c, f), getattr(gold, f)) for f in _FIELDS)


def _same_parse(a, b) -> bool:
    return (a.char_type == b.char_type
            and all(getattr(a, f) == getattr(b, f) for f in _FIELDS))


def reparse_report(dumps: Dict, golds: Dict, scores: List) -> Dict[str, int]:
    """Counts only — never a value — over every matched pair in `scores`.

    would_fix / would_break are the two directions that matter: a parser change
    is worth shipping when it flips wrong rows to right without flipping right
    rows to wrong, and the second number is the one a cost-only reading of the
    first would miss."""
    out = {"n_pairs": 0, "identical": 0, "would_fix": 0, "would_break": 0,
           "still_wrong": 0, "still_correct": 0}
    for score in scores:
        dump, gold = dumps[score.doc_id], golds[score.doc_id]
        preds = {c.pos: c for c in dump.result.characteristics}
        gold_by_num = {g.balloon: g for g in gold.characteristics}
        for pair in score.pairs:
            p = preds.get(pair.pred_pos)
            g = gold_by_num.get(pair.gold_balloon)
            if p is None or g is None:
                continue
            out["n_pairs"] += 1
            fresh = parse_value(p.raw_text or "",
                               hint=_HINTS.get(p.kind or "", ""))
            if _same_parse(fresh, p):
                out["identical"] += 1
            was_right = pair.fields_correct
            now_right = _matches_gold(fresh, g)
            if was_right and not now_right:
                out["would_break"] += 1
            elif not was_right and now_right:
                out["would_fix"] += 1
            elif was_right:
                out["still_correct"] += 1
            else:
                out["still_wrong"] += 1
    return out
```

In `app/eval/runner.py`, in `_cmd_score`, immediately before the final
`return 0` add:

```python
    # A DIAGNOSTIC that deliberately does not touch the written report: it
    # re-parses raw_text with today's parser and prices a hypothetical parser
    # change, so the report stays exactly as comparable as it was.
    if getattr(args, "reparse_check", False):
        from app.eval.reparse import reparse_report
        print(json.dumps(reparse_report(dumps, gold, scores), indent=1))
```

and in the `score` sub-parser (after the `--assignment` argument) add:

```python
    p.add_argument("--reparse-check", action="store_true",
                   help="DIAGNOSTIC: re-parse each matched pair's raw_text with "
                        "today's parser and print how many rows a parser change "
                        "would fix or break. Counts only, no values. Does not "
                        "alter the written report. On an unmodified parser "
                        "identical must equal n_pairs -- anything else means the "
                        "hint reconstruction is wrong, not the parser.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_reparse.py -q`
Expected: `5 passed`

Run: `python -m pytest -q`
Expected: `476 passed, 2 skipped`

- [ ] **Step 5: Run the gate against the real corpus**

```bash
python3 -m app.eval.runner score --run /home/clemi/sindri-client-data/runs/baseline --gold /home/clemi/sindri-client-data/gold --splits /home/clemi/sindri-client-data/meta/splits.json --split dev --weights docs/eval/weights.json --name baseline-dev-reparse --out /home/clemi/sindri-client-data/reports/baseline-dev-reparse.report.json --reparse-check
```

Expected: the score line reporting `mean_review_cost=174.30`, then a JSON block
with `"n_pairs": 308` and `"identical": 308`. If `identical < n_pairs`, the hint
reconstruction is wrong for some kind — **fix that before trusting any
`would_fix` number**, and do not proceed to a parser edit.

- [ ] **Step 6: Commit**

```bash
git add app/eval/reparse.py tests/eval/test_reparse.py app/eval/runner.py
git commit -m "$(cat <<'EOF'
feat(eval): price a parser change from stored dumps, no GPU

Dumps carry raw_text, so parse_value can be re-run offline and compared against
gold. That turns "would this parser change help?" from a 9 h GPU arm into a CPU
second, which is what makes the 52 misparse rows worth examining at all.

Reports would_fix AND would_break: a parser change that flips wrong rows to
right while flipping right rows to wrong is the same trap max-cardinality
matching was, and a one-sided count would hide it.

The identity gate is load-bearing. With an unmodified parser every stored field
must be reproducible, because that is where it came from; identical < n_pairs
means the hint reconstruction is wrong and every other number is measuring
extract.py rather than the parser. Imports only parser, and keeps its own copy
of the hint map, so the CPU score path stays free of the model stack -- with an
equality test against extract._HINTS so the copy cannot drift quietly.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Phase B — the arm decision

## Task 11: Pick the two arms from the Phase A numbers

**STOP HERE AND GET APPROVAL.** This is the second gate. Do not start Phase C
until the arms are agreed.

**Files:**
- Modify: `docs/plans/2026-08-24-rung2-reading-quality.md` (append a
  "Phase A results" section)

- [ ] **Step 1: Assemble the evidence**

Collect, from Tasks 4, 9 and 10:

1. `field_failures` and `field_failure_signatures` (baseline)
2. `field_failure_modes` (baseline, re-scored)
3. `error_cause_crosstab` — specifically `misread.misplaced` vs
   `misread.on_target`
4. `confidence_by_taxonomy`
5. `n_pairs` / `identical` from `--reparse-check`
6. The same blocks for `exp-finetiles`, as the validity check

- [ ] **Step 2: Apply the selection rule**

| if Phase A shows | the arm is | prompt |
|---|---|---|
| `missing:upper_tol` + `missing:lower_tol` dominate the modes | **`readtol`** | `_PROMPT`: demand every printed number, including zero and one-sided tolerances |
| `misread.misplaced` is a large share of `misread` | **`readcenter`** | `_PROMPT`: transcribe the ONE callout nearest the image centre, ignore neighbours |
| `wrong:nominal` dominates and `misread.on_target` >> `misread.misplaced` | **`readsymbol`** | `_PROMPT`: explicit symbol vocabulary, refuse to guess |
| failures are diffuse across all four fields | **`detectbox`** | `_DETECT_PROMPT`: the box must enclose the complete callout including tolerances, and nothing else |
| `char_type` dominates *alone* | **no arm** | this is `normalize.CHAR_TYPE_SYNONYMS` or `parser`, not perception — a CPU fix, and it must not ride along with a prompt arm |

Two arms get cards. If the table selects fewer than two, run one and say so —
padding the campaign with a hypothesis the evidence does not support is how the
detection run spent 18 h on two arms that could not win.

- [ ] **Step 3: Write the decision into this document and stop**

Append a `## Phase A results` section: the six evidence items verbatim, the two
arms chosen, and the specific bucket each is predicted to move (that prediction
is what Phase C checks — a prompt arm whose targeted bucket does not move has
not been attributed even if cost falls).

- [ ] **Step 4: Commit and request approval**

```bash
git add docs/plans/2026-08-24-rung2-reading-quality.md
git commit -m "$(cat <<'EOF'
docs(plan): Phase A results and the two prompt arms they select

Records the field-level, failure-mode, pairing and confidence evidence, and the
bucket each chosen arm must move. The bucket prediction is the attribution
test: prompt_sha256 proves A prompt changed, and RunConfig.extra names WHICH
variant, but only a targeted-bucket delta shows the change did what it was
designed to do.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Phase C — two prompt arms in parallel

## Task 12: Turn the prompts into an env-selected variant registry

Two arms differing only in a source-code constant cannot run concurrently:
`run_experiment_gpu.sh` does `git checkout -B "$BRANCH"` in a single `~/sindri`
clone. A registry also makes each arm *name itself* in `RunConfig.extra`, which
closes the gap where `prompt_sha256` proves only that *a* prompt changed.

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py`
- Modify: `app/eval/runner.py` (`_prompt_sha256`, `_cmd_predict`)
- Test: `tests/test_vlm_prompt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vlm_prompt.py`:

```python
import pytest


def test_prompt_sha256_is_unchanged_by_the_registry():
    """The comparability proof. Every report from the Rung-0 baseline through
    the four direction-run arms carries prompt_sha256 aa7659f1929184ea; if the
    refactor moves it, no prompt arm can be compared against any of them."""
    from app.eval.runner import _prompt_sha256
    assert _prompt_sha256() == "aa7659f1929184ea"


def test_read_and_detect_prompts_default_to_base():
    assert vlm_backend.read_prompt(env={}) == vlm_backend._PROMPT
    assert vlm_backend.detect_prompt(env={}) == vlm_backend._DETECT_PROMPT


def test_unknown_variant_name_fails_loudly_instead_of_using_base():
    """A typo in an arm's env must lose the arm, not silently produce a control
    run wearing a treatment arm's name."""
    with pytest.raises(ValueError, match="SINDRI_READ_PROMPT"):
        vlm_backend.read_prompt(env={"SINDRI_READ_PROMPT": "typo"})


def test_active_prompts_names_the_variant_for_run_config_extra():
    assert vlm_backend.active_prompts(env={}) == {"read_prompt": "base",
                                                  "detect_prompt": "base"}


def test_selecting_a_variant_changes_the_effective_prompt_hash(monkeypatch):
    """What makes a prompt arm attributable: the hash must move with the
    variant, not with the file."""
    import hashlib
    monkeypatch.setitem(vlm_backend._READ_VARIANTS, "probe", "a different prompt")
    base = hashlib.sha256("\n".join(
        vlm_backend.effective_prompts(env={})).encode()).hexdigest()[:16]
    other = hashlib.sha256("\n".join(vlm_backend.effective_prompts(
        env={"SINDRI_READ_PROMPT": "probe"})).encode()).hexdigest()[:16]
    assert base != other
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_vlm_prompt.py -q`
Expected: 4 failures with
`AttributeError: module 'app.pipeline.ocr.vlm_backend' has no attribute 'read_prompt'`
(and `_READ_VARIANTS`). `test_prompt_sha256_is_unchanged_by_the_registry` passes
already — it is the invariant the refactor must not break.

- [ ] **Step 3: Write the implementation**

In `app/pipeline/ocr/vlm_backend.py`, after `_MAX_READ_LONG_EDGE` (line 86) add:

```python
# Prompt variants, selected per run by environment variable. Registries rather
# than edited constants for three reasons, each one paid for:
#   * two arms whose only difference is a source constant cannot run
#     concurrently — run_experiment_gpu.sh keeps one checkout on the GPU host,
#     so a code-level prompt edit serialises the campaign at ~9 h per arm.
#   * `prompt_sha256` proves that A prompt changed, never WHICH. The variant
#     name reaches RunConfig.extra, so the digest says so out loud.
#   * an unknown name raises instead of falling back, so a typo loses the arm
#     rather than producing a control run wearing a treatment arm's name.
# "base" must stay byte-identical to the constants above: it is what every
# report from the Rung-0 baseline onward was produced with.
_READ_VARIANTS = {"base": _PROMPT}
_DETECT_VARIANTS = {"base": _DETECT_PROMPT}


def _select(variants, env_key, env):
    name = (os.environ if env is None else env).get(env_key, "base")
    if name not in variants:
        raise ValueError(
            f"{env_key}={name!r} is not a known prompt variant "
            f"(have: {sorted(variants)}). Refusing to fall back to 'base': a "
            f"silent fallback turns a treatment arm into a control arm with a "
            f"treatment arm's run name.")
    return name


def read_prompt(env=None) -> str:
    """The per-callout read prompt in effect for this run."""
    return _READ_VARIANTS[_select(_READ_VARIANTS, "SINDRI_READ_PROMPT", env)]


def detect_prompt(env=None) -> str:
    """The tile detection prompt in effect for this run."""
    return _DETECT_VARIANTS[_select(_DETECT_VARIANTS, "SINDRI_DETECT_PROMPT", env)]


def active_prompts(env=None) -> dict:
    """Variant names in effect, for RunConfig.extra — the same role
    detect.active_knobs plays for the detection knobs."""
    return {"read_prompt": _select(_READ_VARIANTS, "SINDRI_READ_PROMPT", env),
            "detect_prompt": _select(_DETECT_VARIANTS, "SINDRI_DETECT_PROMPT",
                                     env)}


def effective_prompts(env=None) -> list:
    """The five prompts this run will actually send, in the order
    runner._prompt_sha256 hashes them. With no variant selected this is exactly
    [_PROMPT, _DETECT_PROMPT, _GDT_PROMPT, _NOTES_PROMPT, _TITLE_PROMPT], so the
    default hash is unchanged."""
    return [read_prompt(env), detect_prompt(env), _GDT_PROMPT, _NOTES_PROMPT,
            _TITLE_PROMPT]
```

Then in `read_region`, replace `_PROMPT` with `read_prompt()`:

```python
    def read_region(self, image: Image.Image) -> OcrResult:
        text, conf = self._generate_text(read_prompt(), image,
                                         self.max_new_tokens)
        return OcrResult(text=text, confidence=conf)
```

and in `detect_regions`, replace `{"type": "text", "text": _DETECT_PROMPT}` with
`{"type": "text", "text": detect_prompt()}`.

In `app/eval/runner.py`, replace the body of `_prompt_sha256`:

```python
def _prompt_sha256() -> str:
    try:
        from app.pipeline.ocr import vlm_backend as vb
        # The EFFECTIVE prompts, not the module constants: a variant selected by
        # environment variable has to move this hash, or two arms would be
        # indistinguishable in every report they produce.
        blob = "\n".join(vb.effective_prompts())
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unavailable"
```

and in `_cmd_predict`, replace `extra=active_knobs())` with:

```python
        extra={**active_knobs(), **_active_prompts()})
```

adding near the top of `_cmd_predict`, next to the `active_knobs` import:

```python
    from app.pipeline.ocr.vlm_backend import active_prompts as _active_prompts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_vlm_prompt.py -q`
Expected: `10 passed` (5 pre-existing + 5 new)

Run: `python -m pytest -q`
Expected: `481 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py app/eval/runner.py tests/test_vlm_prompt.py
git commit -m "$(cat <<'EOF'
feat(ocr): select read/detect prompts by env variant, not by editing constants

Two arms whose only difference is a source constant cannot run concurrently:
run_experiment_gpu.sh keeps a single checkout on the GPU host, so a code-level
prompt edit serialises the campaign at ~9 h per arm. With a registry both arms
run from one commit and one image, differing only by -e SINDRI_READ_PROMPT.

It also closes an attribution gap. prompt_sha256 hashes all five prompts
concatenated, so it proves A prompt changed and never which; the variant name
now reaches RunConfig.extra and is printed in the experiment table. An unknown
name raises rather than falling back to base, because a silent fallback would
produce a control run wearing a treatment arm's run name.

_prompt_sha256 now hashes the EFFECTIVE prompts, and with no variant selected
that is byte-identical to before -- pinned by a test asserting the value is
still aa7659f1929184ea, which is what keeps every report from the Rung-0
baseline onward comparable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 13: Let a second arm reuse the first arm's push and build

Two concurrent invocations would both `git checkout` and `podman build` in
`~/sindri`, and both `sync_client_data.sh push` the corpus. Since both arms run
the same commit, the second only needs to skip those steps.

**Files:**
- Modify: `run_experiment_gpu.sh`
- Test: `tests/test_experiment_script.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_experiment_script.py`:

```python
def test_push_and_build_are_skippable_for_a_concurrent_second_arm():
    """Two arms run concurrently on the two cards share one checkout and one
    corpus on the GPU host. The second must not re-push or re-build under the
    first, and since both arms run the same commit it has no reason to."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SKIP_PUSH" in text and "SKIP_BUILD" in text


def test_prompt_arms_are_registered_with_the_prompt_variant_env():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SINDRI_READ_PROMPT" in text or "SINDRI_DETECT_PROMPT" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_experiment_script.py -q`
Expected: 2 failures on the `assert`s.

- [ ] **Step 3: Write the implementation**

In `run_experiment_gpu.sh`, after the `SPLIT="${SPLIT:-dev}"` line add:

```bash
# Two arms can run concurrently on the two H100s, but they share one checkout
# and one corpus copy on the GPU host. The second invocation sets both of these
# so it neither re-pushes the drawings under the first arm nor rebuilds the
# image while the first arm's container is running from it. Safe only because
# concurrent prompt arms run the SAME commit and differ by env alone.
SKIP_PUSH="${SKIP_PUSH:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"
```

Replace the push block:

```bash
echo "== push drawings =="
"$HERE/sync_client_data.sh" push "$HOST" "$RROOT" || exit 1
```

with:

```bash
if [ "$SKIP_PUSH" = "1" ]; then
    echo "== push drawings: SKIPPED (SKIP_PUSH=1) =="
else
    echo "== push drawings =="
    "$HERE/sync_client_data.sh" push "$HOST" "$RROOT" || exit 1
fi
```

Replace the sync/build block:

```bash
echo "== sync code + build image on $HOST =="
ssh -o BatchMode=yes "$HOST" "
  set -euo pipefail
  cd ~/sindri
  git fetch -q origin '$BRANCH'
  git checkout -q -B '$BRANCH' 'origin/$BRANCH'
  echo \"code at \$(git rev-parse --short HEAD)\"
  podman build -q -f Dockerfile.gpu -t sindri-gpu . >/dev/null
  echo 'image built'
" || exit 1
```

with:

```bash
if [ "$SKIP_BUILD" = "1" ]; then
    echo "== sync code + build image: SKIPPED (SKIP_BUILD=1) =="
    ssh -o BatchMode=yes "$HOST" "cd ~/sindri && git rev-parse --short HEAD" \
        || exit 1
else
    echo "== sync code + build image on $HOST =="
    ssh -o BatchMode=yes "$HOST" "
      set -euo pipefail
      cd ~/sindri
      git fetch -q origin '$BRANCH'
      git checkout -q -B '$BRANCH' 'origin/$BRANCH'
      echo \"code at \$(git rev-parse --short HEAD)\"
      podman build -q -f Dockerfile.gpu -t sindri-gpu . >/dev/null
      echo 'image built'
    " || exit 1
fi
```

Then register the arms. Replace the `ARM_ENV` / `ARM_WHY` / `ARM_ORDER` block
with (substituting the two arm names chosen in Task 11 — the entries below assume
`readtol` and `readcenter`; delete the unused ones):

```bash
declare -A ARM_ENV=(
  [control]=""
  [nomerge]="-e SINDRI_MERGE_MAX_LINES=1"
  [tightmerge]="-e SINDRI_MERGE_Y_GAP=8"
  [finetiles]="-e VLM_TILE=768"
  [readtol]="-e SINDRI_READ_PROMPT=tol"
  [readcenter]="-e SINDRI_READ_PROMPT=center"
)
declare -A ARM_WHY=(
  [control]="reproduction check: must match the committed baseline's metrics"
  [nomerge]="82 contended misses: is merge_adjacent collapsing sibling callouts?"
  [tightmerge]="same hypothesis, softer — merge less rather than not at all"
  [finetiles]="74 isolated misses: does a finer grid find undetected callouts?"
  [readtol]="missing:upper_tol/lower_tol — does demanding every printed number recover the dropped tolerances?"
  [readcenter]="misread on misplaced pairs — does naming the centre callout stop the reader transcribing a neighbour?"
)
ARM_ORDER=(control nomerge tightmerge finetiles readtol readcenter)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_experiment_script.py -q`
Expected: `5 passed`

Run: `bash -n run_experiment_gpu.sh`
Expected: no output, exit 0.

Run: `python -m pytest -q`
Expected: `483 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add run_experiment_gpu.sh tests/test_experiment_script.py
git commit -m "$(cat <<'EOF'
feat(eval): register the prompt arms and let a second arm skip push/build

Two arms on the two H100s share one checkout and one corpus copy on the GPU
host, so concurrent invocations would race on `git checkout`, `podman build` and
the corpus push. SKIP_PUSH/SKIP_BUILD let the second arm reuse the first's --
safe only because concurrent prompt arms run the same commit and differ by
environment alone, which is exactly what the variant registry bought.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 14: Add the first arm's prompt variant

Substitute the arm chosen in Task 11. The text below is the `readtol`
formulation; if Task 11 selected a different arm, write that arm's text and keep
the structure — one variant, one commit.

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py` (`_READ_VARIANTS`)
- Test: `tests/test_vlm_prompt.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vlm_prompt.py`:

```python
def test_tol_variant_demands_every_printed_number_and_leaves_base_alone():
    """The arm's hypothesis, pinned as text: baseline drops tolerances, so the
    variant has to say explicitly that a zero and a one-sided tolerance are
    still tolerances. base must be untouched -- it is the comparison point."""
    from app.eval.runner import _prompt_sha256
    p = vlm_backend._READ_VARIANTS["tol"]
    assert "every number" in p.lower()
    assert "0" in p and "MAX" in p
    assert p != vlm_backend._READ_VARIANTS["base"]
    assert _prompt_sha256() == "aa7659f1929184ea"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_vlm_prompt.py -q -k tol_variant`
Expected: `KeyError: 'tol'`

- [ ] **Step 3: Write the implementation**

In `app/pipeline/ocr/vlm_backend.py`, replace
`_READ_VARIANTS = {"base": _PROMPT}` with:

```python
# Arm `readtol`. Hypothesis from the Phase A failure modes: the reader drops
# tolerances rather than misreading them, and the base prompt never says a zero
# or a one-sided tolerance is still a tolerance — its three examples are all
# symmetric or MAX. Targets missing:upper_tol / missing:lower_tol.
_PROMPT_TOL = (
    "This image is a single dimension callout cropped from a mechanical "
    "engineering drawing. Transcribe ONLY the dimension and its tolerances as "
    "plain text on one line. Copy EVERY number printed in the callout, in the "
    "order printed, including a tolerance of 0 and a tolerance that has only "
    "an upper or only a lower value, e.g. '1,2 +0,1 -0,1' or 'Ø7 +0,2 0' or "
    "'Ø7 +0,1' or 'R0,5 MAX'. Do not omit a tolerance because it looks small "
    "or redundant, and do not invent one that is not printed. Use a comma as "
    "the decimal separator. Preserve the symbols Ø, R and ±. Ignore leader "
    "lines, dimension lines and arrowheads. If there is no dimension text, "
    "output nothing. No explanation."
)

_READ_VARIANTS = {"base": _PROMPT, "tol": _PROMPT_TOL}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_vlm_prompt.py -q`
Expected: `11 passed`

Run: `python -m pytest -q`
Expected: `484 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py tests/test_vlm_prompt.py
git commit -m "$(cat <<'EOF'
feat(ocr): add the readtol prompt variant

One prompt, one arm. The base read prompt's three examples are all symmetric or
MAX, and it never states that a zero or a one-sided tolerance is still a
tolerance -- which is the shape of the missing:upper_tol / missing:lower_tol
failures Phase A measured. The variant demands every printed number in printed
order and forbids inventing one, so the arm can move either bucket and be seen
doing it.

base is byte-identical, pinned by prompt_sha256 == aa7659f1929184ea.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 15: Add the second arm's prompt variant

Substitute the second arm chosen in Task 11. The text below is the `readcenter`
formulation.

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py` (`_READ_VARIANTS`)
- Test: `tests/test_vlm_prompt.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vlm_prompt.py`:

```python
def test_center_variant_names_the_centre_callout_and_leaves_base_alone():
    """The arm's hypothesis, pinned as text: a crop containing a neighbouring
    callout is transcribed ambiguously, so the variant must say which callout
    to read."""
    from app.eval.runner import _prompt_sha256
    p = vlm_backend._READ_VARIANTS["center"]
    assert "centre" in p.lower() or "center" in p.lower()
    assert "neighbour" in p.lower() or "neighboring" in p.lower() \
        or "neighbouring" in p.lower()
    assert p != vlm_backend._READ_VARIANTS["base"]
    assert _prompt_sha256() == "aa7659f1929184ea"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_vlm_prompt.py -q -k center_variant`
Expected: `KeyError: 'center'`

- [ ] **Step 3: Write the implementation**

In `app/pipeline/ocr/vlm_backend.py`, before the `_READ_VARIANTS` assignment add:

```python
# Arm `readcenter`. Hypothesis from the Phase A cross-tab: a large share of the
# misread rows sit on misplaced pairs, i.e. the crop contains more than one
# callout and the base prompt's "ONLY the dimension" does not say which. Targets
# error_cause_crosstab.misread.misplaced.
_PROMPT_CENTER = (
    "This image is a crop from a mechanical engineering drawing containing one "
    "dimension callout at its centre, and possibly parts of neighbouring "
    "callouts near the edges. Transcribe ONLY the callout closest to the "
    "centre of the image, as plain text on one line, e.g. '1,2 +0,1 -0,1' or "
    "'Ø7 +0,1 -0,1' or 'R0,5 MAX'. Ignore any other callout, even a complete "
    "one, and never merge two callouts into one line. Use a comma as the "
    "decimal separator. Preserve the symbols Ø, R and ±. Ignore leader lines, "
    "dimension lines and arrowheads. If there is no dimension text at the "
    "centre, output nothing. No explanation."
)
```

and extend the registry:

```python
_READ_VARIANTS = {"base": _PROMPT, "tol": _PROMPT_TOL,
                  "center": _PROMPT_CENTER}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_vlm_prompt.py -q`
Expected: `12 passed`

Run: `python -m pytest -q`
Expected: `485 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py tests/test_vlm_prompt.py
git commit -m "$(cat <<'EOF'
feat(ocr): add the readcenter prompt variant

One prompt, one arm. The base prompt says "ONLY the dimension" for a crop that
may hold parts of neighbouring callouts, and never says which one to read. If
Phase A's cross-tab puts a large share of misread on misplaced pairs, the reader
is transcribing a neighbour correctly and being scored as a perception failure
-- which no amount of transcription guidance would fix.

base is byte-identical, pinned by prompt_sha256 == aa7659f1929184ea.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 16: Run both arms and judge them

**Files:**
- Create: `docs/eval/exp-{arm1,arm2}-summary.json`,
  `docs/eval/exp-{arm1,arm2}-vs-control.json`
- Modify: `docs/plans/2026-08-24-rung2-reading-quality.md` (results section)

- [ ] **Step 1: Push the branch so the GPU host can fetch it**

```bash
git push origin worktree-eval-harness
```

- [ ] **Step 2: Find a free card and pin it**

```bash
ssh -o BatchMode=yes 4mehpc4_3 "nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader"
```

A 72B AWQ load into an occupied card falls back to Tesseract and fails every
document. Note which indices are free; both arms need one each. Do not proceed
if only one is free — run the arms sequentially instead.

- [ ] **Step 3: Launch arm 1 (it does the push and build)**

```bash
GPU='nvidia.com/gpu=<free-index-1>' ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' readtol
```

Wait for `== push drawings ==` and `image built` to appear before Step 4 — arm 2
reuses both.

- [ ] **Step 4: Launch arm 2 on the other card, reusing arm 1's push and build**

```bash
SKIP_PUSH=1 SKIP_BUILD=1 GPU='nvidia.com/gpu=<free-index-2>' ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' readcenter
```

Budget ~9 h per arm, ~26 min per document. Watch by filtering each log to
`^===== arm`, `^\[(5|10|15|20)/20\]`, `arm .* done`, `ARM FAILED`, and
`Traceback|Tesseract|falling back|OOM|Killed` — about a dozen events per arm, and
`Tesseract|falling back` is the failure that must abort the arm immediately.

- [ ] **Step 5: Confirm the arms are actually distinguishable**

```bash
python3 -c "
import json, pathlib
for name in ('exp-readtol', 'exp-readcenter'):
    d = json.loads((pathlib.Path('docs/eval')/f'{name}-summary.json').read_text())
    c = d['config']
    print(name, c['prompt_sha256'], c['extra'])
    assert c['prompt_sha256'] != 'aa7659f1929184ea', 'prompt did not change!'
    assert d['splits_hash'] == '6d174d5e4f1b9228'
    assert d['match_params']['score_kinds'] == ['dimension']
    assert d['frame_mismatch']['n_docs_affected'] == 0
    assert d['frame_mismatch']['n_docs_not_measured'] == 0
print('arms are attributable')
"
```

Expected: two distinct `prompt_sha256` values, neither `aa7659f1929184ea`, each
`extra` naming its own `read_prompt` variant, and `arms are attributable`.

- [ ] **Step 6: Read the decision table**

```bash
python3 -m app.eval.experiment
```

An arm wins only if cost falls **and** `field_acc` does not fall more than 0.02
**and** `escaped_rate` does not rise more than 0.02.

- [ ] **Step 7: Apply the two extra conditions this campaign adds**

```bash
python3 -c "
import json, pathlib
base = json.loads(pathlib.Path('docs/eval/baseline-diag-summary.json').read_text())
for name in ('exp-readtol', 'exp-readcenter'):
    d = json.loads((pathlib.Path('docs/eval')/f'{name}-summary.json').read_text())
    t, bt = d['taxonomy'], base['taxonomy']
    acc = (t['correct'] + t['flagged_correct']) / (d['n_gold'] - t['missed'])
    bacc = (bt['correct'] + bt['flagged_correct']) / (base['n_gold'] - bt['missed'])
    print(f'--- {name}')
    print(f'  field_acc {bacc:.4f} -> {acc:.4f} ({acc - bacc:+.4f})')
    print(f'  modes     {d[\"field_failure_modes\"]}')
    print(f'  crosstab  {d[\"error_cause_crosstab\"]}')
    print(f'  RISES     {acc > bacc}')
"
```

Both conditions must hold, on top of `experiment.py`'s three:

* `field_acc` must **rise**, not merely hold. A cost fall with flat `field_acc`
  is the confidence-threshold confound from decision 3, not a reading win.
* the arm's **targeted bucket** must move: `missing:upper_tol` +
  `missing:lower_tol` down for `readtol`, `error_cause_crosstab.misread.misplaced`
  down for `readcenter`. A cost improvement with an unmoved target bucket means
  something else moved, and adopting the prompt on that basis is how this corpus
  has already produced two wrong verdicts.

- [ ] **Step 8: Commit the digests and write the verdict**

```bash
git add docs/eval/exp-readtol-summary.json docs/eval/exp-readtol-vs-control.json docs/eval/exp-readcenter-summary.json docs/eval/exp-readcenter-vs-control.json docs/plans/2026-08-24-rung2-reading-quality.md
git commit -m "$(cat <<'EOF'
docs(eval): Rung-2 prompt arms — results and verdict

Records both arms' digests, their compare output, and the verdict under all five
conditions: experiment.py's three, plus field_acc rising rather than merely
holding, plus the targeted Phase A bucket actually moving. The last two exist
because a cost fall alone is consistent with confidence-threshold churn or with
an unrelated bucket moving, and this corpus has produced a wrong verdict from a
cost-only reading twice already.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Verification, after every task

```bash
python -m pytest -q                          # count rises per the task; 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # guard: 32 passed, 0 failed
python3 -m app.eval.experiment               # baseline / arm decision table
```

Expected suite counts: 441 at start → 450 (T1) → 454 (T2) → 457 (T3) → 462 (T5)
→ 465 (T6) → 468 (T7) → 471 (T8) → 476 (T10) → 481 (T12) → 483 (T13) → 484 (T14)
→ 485 (T15). Tasks 4, 9, 11 and 16 add no tests.

## What this plan must not do

* Not touch `MatchParams`, `SCHEMA_VERSION`, or the frozen split
  `6d174d5e4f1b9228`.
* Not re-attempt the merge knobs, detect tile size, render resolution / pixel
  budget, max-cardinality matching, or filtering predictions to `score_kinds`.
  All five are measured dead ends; `CLAUDE.md` §3 has the numbers.
* Not change two prompts in one arm. `prompt_sha256` hashes all five
  concatenated, so it cannot attribute a joint change — and now that
  `RunConfig.extra` names the variants, a joint change is visible but still
  unattributable.
* Not ship a parser or `normalize` change while a prompt arm is outstanding. Both
  sit in the predict path, so shipping one makes an arm's dumps differ from the
  frozen baseline in two ways at once. Task 10's diagnostic exists precisely so
  the parser question can be answered without spending that.
* Not judge any arm on review cost alone. That has been wrong twice on this
  corpus: max-cardinality matching, and `finetiles` ranking cheaper than
  `nomerge` while being far worse.

---

# Phase A results

Tasks 1–10, executed 2026-08-26. All local, no GPU. Every number below is from
`docs/eval/baseline-diag-summary.json` (re-scored, reproduction-gated at 20/20
per-document deltas exactly 0.0 against the frozen baseline) or from
`docs/eval/baseline-summary.json` (re-summarised, every pre-existing key
byte-equal).

**The headline: the read prompt is the wrong lever, and the plan's own
`readtol` arm is dead.** The failures are not tolerance-shaped and they are not
mostly perception — they look like the pipeline reading the *wrong callout*.

## A.1 Which field fails — diffuse, not tolerance-shaped

Of the 196 matched-but-wrong rows (`escaped_error` 129 + `flagged_error` 67):

| field | rows | share of 196 |
|---|---|---|
| `lower_tol` | 133 | 67.9% |
| `upper_tol` | 122 | 62.2% |
| `nominal` | 119 | 60.7% |
| `char_type` | 115 | 58.7% |

All four fail on roughly three fifths of wrong rows. Signatures, biggest first:

| rows | share | signature |
|---|---|---|
| **49** | **25.0%** | `char_type+nominal+upper_tol+lower_tol` — *everything* wrong |
| 33 | 16.8% | `nominal+upper_tol+lower_tol` |
| 23 | 11.7% | `char_type` alone |
| 16 | 8.2% | `upper_tol+lower_tol` |
| 15 | 7.7% | `nominal` alone |
| 11 | 5.6% | `lower_tol` alone |
| 11 | 5.6% | `char_type+nominal` |
| 10 | 5.1% | `char_type+upper_tol+lower_tol` |
| 10 | 5.1% | `char_type+upper_tol` |
| 21 | 10.7% | seven smaller combinations |

Sums to 196 ✓ (= `escaped_error` + `flagged_error`).

**42% of wrong rows have the entire value wrong** (all four, or nominal plus both
tolerances). A row scores correct only if all four fields match, so the
addressable ceiling of any single-field fix is its *sole-failure* signature, not
its per-field count:

| candidate fix | rows it could convert | ceiling |
|---|---|---|
| tolerances only (`upper_tol`/`lower_tol` sole failures) | 11 + 2 + 16 = **29** (14.8%) | ~−5.8 cost |
| `char_type` only | **23** (11.7%) | ~−4.6 cost |

That kills `readtol`: a prompt that perfects tolerance transcription and nothing
else can reach at most 29 of 196 rows.

## A.2 How each field fails

`wrong` 314 instances, `missing` 142, `spurious` 33 (one per wrong field per row;
totals to 489, matching the per-field histogram ✓).

| mode | instances | share of 196 rows |
|---|---|---|
| `wrong:char_type` | 115 | 58.7% |
| `wrong:nominal` | 102 | 52.0% |
| `missing:lower_tol` | 76 | 38.8% |
| `missing:upper_tol` | 58 | 29.6% |
| `wrong:upper_tol` | 50 | 25.5% |
| `wrong:lower_tol` | 47 | 24.0% |
| `spurious:upper_tol` | 14 | 7.1% |
| `spurious:lower_tol` | 10 | 5.1% |
| `spurious:nominal` | 9 | 4.6% |
| `missing:nominal` | 8 | 4.1% |

The dominant modes are `wrong:`, not `missing:` — the pipeline produces a value
and it is a *different* value. `char_type` is never missing, only wrong. Dropped
tolerances are common as a co-occurring symptom (134 instances) but rarely a
row's only problem (29 rows).

## A.3 Nearly half of "perception failure" is a mispairing

| cause | total | misplaced | on_target | escaped | flagged |
|---|---|---|---|---|---|
| `misread` | 144 | **64 (44.4%)** | 80 | 98 | 46 |
| `misparse` | 52 | 6 (11.5%) | 46 | 31 | 21 |

Reconciles both ways per cause, and the totals reproduce `error_causes` ✓.

**70 of the 80 misplaced pairs are wrong rows (87.5%).** A pair matched more than
4% of the page diagonal from its balloon, getting every field wrong, is the
signature of transcribing a *neighbouring callout correctly* and being scored as
a perception failure. No amount of transcription guidance fixes that.

Combined with A.1: the 49 all-four-wrong rows and the 64 misplaced-misread rows
tell the same story from two directions. **The crop is wrong, not the reading.**

## A.4 Confidence is saturated, and `LOW_CONF` is miscalibrated

| band | pairs | errors | error rate |
|---|---|---|---|
| `<0.2` | 3 | 3 | 100% |
| `0.2–0.4` | 1 | 1 | 100% |
| `0.4–0.6` | 2 | 2 | 100% |
| `0.6–0.8` | 18 | 18 | **100%** |
| `≥0.8` | 284 | 172 | 60.6% |

Covers all 308 matched pairs ✓. Two consequences:

1. **The threshold-churn confound in a prompt arm is small.** 92% of pairs sit at
   ≥0.8, so an arm's cost delta is mostly real, not flag movement. Judging on
   `field_acc` as well as cost remains right, but the confound is not large.
2. **`review.LOW_CONF = 0.6` is set below where the signal separates.** Every one
   of the 24 pairs under 0.8 is wrong. Raising it to 0.8 flags the 15 rows in
   `0.6–0.8` that currently escape and flags **zero** correct rows:

   ```
   cost         174.30 -> 171.30   (-3.00, i.e. 15 rows x (5-1))
   escaped_rate 0.2704 -> 0.2390
   field_acc    0.3636 -> 0.3636   (unchanged)
   ```

   Unlike flag-everything (decision 3), this is not degenerate — it is a
   calibrated threshold on a band with a 100% observed error rate. Caveat: n=24
   is small, so the *rate* is uncertain even though the −3.00 is exact for these
   dumps. It is a `review.py` change, so it must NOT ship before the prompt arms
   (it would confound them); it is either its own arm or it lands afterwards.

## A.5 The parser is not silently losing anything

`--reparse-check` on the real corpus: `n_pairs 308, identical 308, would_fix 0,
would_break 0, still_wrong 196, still_correct 112`.

`identical == n_pairs` is the gate, and it passes: the hint reconstruction is
correct for every pair, so the diagnostic is calibrated and any `would_fix` from
a candidate parser edit is attributable to that edit. Note what this does *not*
say — it does not prove the parser is optimal, only that it is deterministic and
faithfully re-runnable. The 52 `misparse` rows (gold nominal present in the raw
text) remain the bucket a parser change could address, now priceable in a CPU
second instead of a 9 h arm.

## A.6 The two arms

Applying the plan's selection rule: "failures are diffuse across all four
fields" → `detectbox`; "`misread.misplaced` is a large share of `misread`" →
`readcenter`. `readtol` is not selected (14.8% ceiling). `char_type` is large but
not *dominant alone*, and its fix is CPU-only, so it does not take a card.

| arm | prompt | targets | bucket that must move | addressable rows |
|---|---|---|---|---|
| **`readcenter`** | `_PROMPT` — "transcribe the ONE callout nearest the image centre; ignore neighbours" | the reader transcribing a neighbouring callout | `error_cause_crosstab.misread.misplaced` 64 ↓ | 64 (32.7%) |
| **`detectbox`** | `_DETECT_PROMPT` — "the box must enclose the complete callout including its tolerances, and nothing else" | crop quality, and the 30%-of-cost false-detection term | `fields:char_type+nominal+upper_tol+lower_tol` 49 ↓ | 49 + up to 522 false detections |

Both are one prompt each, so both stay attributable, and with the Task 12
variant registry they run concurrently on the two H100s from one commit.

`detectbox` carries the larger risk: it changes crops for every downstream read,
which is exactly how `finetiles` did its damage (−0.0662 `field_acc`). Its
verdict must therefore lean on `field_acc` and `escaped_rate`, not on the false
detection count falling.

## A.7 Two GPU-free wins to bank after the arms

Neither may ship before or alongside the arms — both sit in the predict path, and
either one would make an arm's dumps differ from the frozen baseline in two ways
at once.

| fix | where | ceiling | measurable without GPU? |
|---|---|---|---|
| `LOW_CONF` 0.6 → 0.8 | `app/pipeline/review.py:16` | −3.00 cost, `escaped_rate` −0.031, `field_acc` flat | yes — exactly, from stored confidences (A.4) |
| `char_type` sole failures | `app/eval/normalize.py` `CHAR_TYPE_SYNONYMS` / `app/pipeline/parser.py` | 23 rows, ~−4.6 cost | partly — `--reparse-check` prices the parser side |

A caution on the second: `CHAR_TYPE_SYNONYMS` is *scoring* policy, not pipeline
behaviour. Changing it changes what "correct" means, and `compare_runs` does not
guard it — `MatchParams` is the only comparability fingerprint and a synonym-map
change leaves no trace in it. Any change there must re-score both sides and say
so loudly, or it will silently credit itself.

---

# Phase C results — readcenter, and what it eliminated

Executed 2026-08-26/27 on `4mehpc4_3`, GPU 0, one arm. Commit `3e3dc92`.

## C.1 readcenter loses, and its target bucket did not move

| | control | readcenter | delta |
|---|---|---|---|
| `mean_review_cost` | 174.30 | 175.20 | **+0.90** |
| `field_acc` | 0.3636 | 0.3474 | **−0.0162** |
| `micro_recall` | 0.6457 | 0.6457 | +0.0000 |
| `escaped_error` | 129 | 133 | +4 |
| `correct` | 72 | 70 | −2 |
| `flagged_correct` | 40 | 37 | −3 |

`ci95 [-0.5, 2.45]`, `significant: False`, worse under **all six** weightings,
`robust: True`, 9 of 20 documents changed. Fails condition 1 (cost) and the
campaign's condition 4 (`field_acc` must *rise* for a reading arm).

**The attribution is exact, and that is what makes this useful.** A read-prompt
change cannot touch detection, and the digest confirms it did not: `n_pred` 830,
`missed` 169, `contended` 82, `isolated` 74, `misplaced_matches` 80,
`false_detection` 522 are all **bit-identical** to control. So this was an
isolated test of the read prompt over the same 308 pairs — and
`error_cause_crosstab.misread.misplaced`, the bucket the arm was built to move,
is exactly **64 → 64**.

Naming the centre callout recovered *none* of the 64 misplaced-misread rows.

## C.2 What that eliminates

The 64 rows are therefore **not** a case of the crop being ambiguous about which
callout to read. If they were, an instruction naming the target would have moved
at least some of them. The crop must not contain the right callout at all — a
crop/geometry fault, upstream of the read.

Which is what `detectbox` tests, and it is now the last untested lever on the
dominant failure mode.

Secondary observation: `field:char_type` −4 but `field:lower_tol` +5,
`upper_tol` +2, `nominal` +2. A prompt that fixates on one callout classifies it
slightly better and transcribes its numbers slightly worse.

## C.3 Six arms, six losses

| arm | lever | cost | `field_acc` |
|---|---|---|---|
| tightmerge | `merge_y_gap` | +0.35 | +0.0009 |
| readcenter | **read prompt** | +0.90 | −0.0162 |
| finetiles | detect tile size | +5.00 | −0.0662 |
| nomerge | `merge_max_lines` | +5.60 | −0.0123 |
| render150 | render pixel budget | worse | — |
| max-cardinality | matcher assignment | *lower* | −0.110 |

Every perturbation of this pipeline, in every direction, has lost. Treat the
committed configuration as tuned, and treat "one more knob or prompt" as the
least likely remaining source of a win.

## C.4 Is the biggest bucket even winnable? Mostly yes

Built after readcenter, before spending another card
(`DocScore.dropped_tol_rows` / `dropped_tol_distinct`).

80 matched rows dropped a tolerance the gold has. Across 18 documents they use
**49 distinct gold (upper, lower) pairs — 61% distinct**:

| rows | distinct | reading |
|---|---|---|
| 16 | 10 | printed per callout |
| 8 | 2 | **general-tolerance signature** |
| 8 | 4 | mixed |
| 7 | 5 | printed per callout |
| 6 | 3 | mixed |
| 5 | 4 | printed per callout |
| 4 | 1 | **general-tolerance signature** |
| 4 | 1 | **general-tolerance signature** |
| 3 | 1 | **general-tolerance signature** |
| 4,3,3,2,2,2,1,1,1 | all distinct | printed per callout |

An ISO 2768 table in the title block would show as many rows sharing one pair.
The largest document is 16 rows sharing 10. **So the tolerances are genuinely
printed and vary row to row, and the pipeline simply produced nothing for
them** — consistent with a box clipping them off, which is `detectbox`'s
hypothesis. The bucket is not structurally unreachable.

The minority still matters: four documents do carry the signature (8/2, 4/1,
4/1, 3/1) = **19 of 80 rows** that plausibly cannot be read from a callout crop
at all. So `field_acc` has a modest ceiling below 1.0. Do not judge an arm by
how close to 1.0 it gets.

## C.5 Standing state

* `detectbox` remains registered and unrun; `prompt_sha256` `67a373609c367f3a`.
* Two GPU-free wins still parked, for the same sequencing reason (both sit in the
  predict path and would confound an arm): `review.LOW_CONF` 0.6 → 0.8 (−3.00
  cost, 15 silent errors flagged, zero correct rows flagged) and the 23
  `char_type`-only rows.
* `readcenter` is a **measured dead end**. Do not retune the read prompt toward
  callout selection; the target bucket was proven untouched, not merely
  unmoved-on-average.
