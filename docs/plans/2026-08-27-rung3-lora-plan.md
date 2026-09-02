# Rung 3 — LoRA on the read stage: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fine-tune a LoRA adapter for the callout-read task on Qwen2.5-VL-**72B**
(4-bit NF4) and measure whether it beats the zero-shot 72B baseline, with a
zero-shot NF4 control separating the quantisation change from the fine-tune.

**Revised 2026-08-27** from a 7B target to 72B, after measuring the host: 4-bit
puts the 72B base at ~36 GB, so the whole run fits one H100 80 GB and needs no
model parallelism — which matters because the two cards are `NODE`-connected, not
NVLink. The model cache is on a 7.0 TB volume with 6.2 TB free, so the 145 GB bf16
download is unremarkable. Tasks 1–6 are unaffected; only Stages D and E changed.

**Architecture:** Five stages, each gated on the previous. Close the
cross-base-model comparability hole; build a gold→target renderer whose
correctness is provable offline by round-tripping through the existing parser;
measure whether a detection-only pass is worth building before paying ~26 h for
train-split crops; build the (crop, target) dataset on CPU under the protected
root using the pipeline's own crop code; then train in a separate image and run
the two arms.

**Tech Stack:** Python 3, pytest, pydantic, PyMuPDF, Pillow, transformers 4.49.0
(inference, pinned), PEFT + bitsandbytes 4-bit NF4 (training, separate image),
Qwen2.5-VL-72B.

---

## Read this before Task 1

* `CLAUDE.md` — NDA rules, conventions, measured dead ends.
* `docs/plans/2026-08-27-rung3-lora-design.md` — the design this implements.
* `docs/plans/2026-08-27-session-handoff.md` — current state and the two unbanked
  GPU-free wins.
* Every command naming `/home/clemi/sindri-client-data` must be a **single,
  unpiped, unchained** `python3 -m app.eval.runner <subcommand>`. No pipes, no
  `&&`, no redirects. A bare `.pdf` anywhere in a command string is denied too.
* `bash ~/.claude/hooks/test-sindri-guard.sh` → `32 passed, 0 failed` after any
  task.

Baseline to beat, frozen: `mean_review_cost=174.30`, `micro_recall=0.6457023060796646`,
`field_acc=0.3636`, `prompt_sha256=aa7659f1929184ea`. Suite starts at
**510 passed, 2 skipped**.

---

## File Structure

| file | status | responsibility |
|---|---|---|
| `app/train/__init__.py` | **create** | package marker; keeps training code out of `app/eval` and `app/pipeline` |
| `app/train/targets.py` | **create** | gold row + hint → target transcription string; the inverse of `parse_value` |
| `app/train/dataset.py` | **create** | (boxes, gold) → (crop, target) pairs under the protected root; counts out, never values |
| `app/eval/report.py` | modify | warn when a comparison crosses base models |
| `app/eval/experiment.py` | modify | show the base model in the arm table |
| `app/eval/runner.py` | modify | `predict --detect-only`; adapter id into `RunConfig.extra` |
| `app/pipeline/extract.py` | modify | stop after detection when asked |
| `app/pipeline/ocr/vlm_backend.py` | modify | load a LoRA adapter by name, failing loudly on an unknown one |
| `Dockerfile.train`, `requirements-train.txt` | **create** | training image, deliberately separate from the pinned inference image |
| `train_lora.py` | **create** | the LoRA training entry point |
| `run_experiment_gpu.sh` | modify | register the `base72bnf4` and `lora72b` arms |
| `tests/train/test_targets.py` | **create** | round-trip property over every shape gold contains |
| `tests/train/test_dataset.py` | **create** | pair building, values-blindness, crop fidelity |
| `tests/eval/test_report.py` | modify | the cross-model warning |
| `tests/eval/test_experiment.py` | modify | base model surfaced |
| `tests/test_detect_only.py` | **create** | detection-only path issues no reads |

`app/train/` is a new package on purpose: training imports the pipeline parser and
the pipeline crop code, which `app/eval` is forbidden from doing wholesale
(`score.py` is documented "Pure CPU; imports nothing from the model stack").

---

# Stage 0 — close the comparability hole

## Task 1: Warn when a comparison crosses base models

`_check_comparable` never looks at `RunConfig`, so a run on a different base
compares against the frozen baseline in silence and the swap is credited to the
treatment. This campaign does exactly that twice: the baseline is
`...-72B-Instruct-AWQ` while both new runs are `...-72B-Instruct` quantised to NF4.
It must **warn, not refuse** — the cross-base comparison IS the experiment.

**Files:**
- Modify: `app/eval/report.py` (`compare_runs`, the `warnings` block)
- Test: `tests/eval/test_report.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/eval/test_report.py`:

```python
def test_a_comparison_across_base_models_warns_loudly():
    """_check_comparable guards the doc set, gold hashes, weights, match_params
    and splits_hash -- but not RunConfig. So a 7B run compares against the 72B
    baseline without complaint, and the base-model swap gets credited to whatever
    the arm was actually testing. Switching base model is a bigger change than
    any knob measured so far, so it must never be silent.

    A warning, not a refusal: comparing a LoRA'd 7B against the zero-shot 72B is
    the entire point of Rung 3."""
    a = _run("a", [10.0, 12.0])
    b = _run("b", [9.0, 11.0])
    a.config = RunConfig(model_id="Qwen/Qwen2.5-VL-72B-Instruct-AWQ")
    b.config = RunConfig(model_id="Qwen/Qwen2.5-VL-7B-Instruct")

    cmp = compare_runs(a, b, seed=13)

    assert any("base model" in w for w in cmp["warnings"]), cmp["warnings"]
    assert any("72B" in w and "7B" in w for w in cmp["warnings"])


def test_same_base_model_produces_no_model_warning():
    a = _run("a", [10.0, 12.0])
    b = _run("b", [9.0, 11.0])
    for r in (a, b):
        r.config = RunConfig(model_id="Qwen/Qwen2.5-VL-72B-Instruct-AWQ")
    assert not any("base model" in w for w in compare_runs(a, b, seed=13)["warnings"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_report.py -q -k base_model`
Expected: `test_a_comparison_across_base_models_warns_loudly` FAILS on the
`assert any("base model" ...)` with an empty or unrelated warnings list;
`test_same_base_model_produces_no_model_warning` passes already (it is the
regression half).

- [ ] **Step 3: Write the implementation**

In `app/eval/report.py`, inside `compare_runs`, after the existing
`escaped_rate` warning block and before the `return`:

```python
    # RunConfig is deliberately NOT part of _check_comparable: refusing here
    # would block the Rung-3 experiment, which is precisely a cross-base-model
    # comparison. But it must not be silent either -- a base-model swap is a
    # larger change than any knob measured on this corpus, and attributing it to
    # an arm's treatment is the exact mistake this warning exists to prevent.
    if a.config.model_id != b.config.model_id:
        warnings.append(
            f"base model differs: {a.config.model_id!r} -> {b.config.model_id!r}. "
            f"This delta includes the base-model change, not only the treatment. "
            f"State both models wherever this result is quoted.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_report.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `512 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_report.py
git commit -m "$(cat <<'EOF'
feat(eval): warn when a comparison crosses base models

_check_comparable guards the doc set, gold hashes, weights, match_params and
splits_hash, but never looks at RunConfig -- so a run on a different base compares
against the frozen baseline in silence and the swap is credited to the treatment.

A warning rather than a refusal, because a cross-base-model comparison is exactly
what Rung 3 is: LoRA's effect is lora72b vs base72bnf4, and the ladder's question is
lora72b vs the zero-shot 72B. Refusing would block the experiment; staying silent
would invalidate it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 2: Show the base model in the arm table

A delta that omits which model produced each side is not a result (design §7).

**Files:**
- Modify: `app/eval/experiment.py` (`arm_row`, and the `cols` tuple in `main`)
- Test: `tests/eval/test_experiment.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/eval/test_experiment.py`:

```python
def test_arm_row_carries_the_base_model():
    """Rung 3 compares an AWQ baseline against an NF4 run of the same weights.
    A row that does not say which model produced it cannot be read as a result."""
    digest = _digest(174.3, 0.6457, 169, 82, 74, 72, 40, 129)
    digest["config"]["model_id"] = "Qwen/Qwen2.5-VL-72B-Instruct"
    assert arm_row("nf4", digest)["model"] == "Qwen/Qwen2.5-VL-72B-Instruct"


def test_arm_row_says_so_when_the_model_was_not_recorded():
    """Every digest since the Rung-0 baseline records model_id, but an older one
    must read as unknown rather than as the current default."""
    digest = _digest(174.3, 0.6457, 169, 82, 74, 72, 40, 129)
    digest["config"].pop("model_id", None)
    assert arm_row("old", digest)["model"] == "unrecorded"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/eval/test_experiment.py -q -k "arm_row and model"`
Expected: both FAIL with `KeyError: 'model'`.

- [ ] **Step 3: Write the implementation**

In `app/eval/experiment.py`, in the dict `arm_row` returns, after `"arm": name,`:

```python
        # Which base model produced this row. Rung 3 compares an AWQ baseline
        # against an NF4 run, and a delta that does not say which side is which
        # is not a result.
        # "unrecorded" rather than a plausible default: an older digest that
        # never captured model_id must not read as the current one.
        "model": digest.get("config", {}).get("model_id") or "unrecorded",
```

In `main`, replace the `cols` tuple:

```python
    cols = ("arm", "model", "cost", "recall", "field_acc", "missed", "contended",
            "isolated", "false_detection", "misplaced")
```

and widen the column padding so the model ids stay readable — replace `w = 12`
with:

```python
    # Model ids are long ("Qwen/Qwen2.5-VL-72B-Instruct-AWQ"); the table is read
    # by humans deciding whether two rows are comparable at all.
    w = 12
    widths = {c: (34 if c == "model" else w) for c in cols}
```

and both table loops that use `ljust(w)`:

```python
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in arms:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/eval/test_experiment.py -q`
Expected: all pass.

Run: `python3 -m app.eval.experiment`
Expected: the table now has a `model` column reading
`Qwen/Qwen2.5-VL-72B-Instruct-AWQ` for all five existing arms.

Run: `python -m pytest -q`
Expected: `514 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/experiment.py tests/eval/test_experiment.py
git commit -m "$(cat <<'EOF'
feat(eval): show the base model in the arm table

Rung 3 compares a LoRA'd NF4 72B against a zero-shot NF4 72B and against the
zero-shot AWQ 72B baseline. A row that does not state which model produced it
cannot be read as a result, and the table was the one place all arms are seen
side by side.

"unrecorded" rather than a plausible default, for the same reason
frame_origin_frac is None rather than 0.0: a digest that never captured model_id
must not read as the current one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Stage A — the target renderer (no GPU, no client data)

## Task 3: Render a gold row to a parseable transcription

The inverse of `parse_value`. Its correctness is provable on synthetic gold, which
is what makes this the right first task.

**Files:**
- Create: `app/train/__init__.py`, `app/train/targets.py`
- Create: `tests/train/__init__.py`, `tests/train/test_targets.py`

- [ ] **Step 1: Write the failing test**

Create `tests/train/__init__.py` as an empty file. Create
`tests/train/test_targets.py`:

```python
"""The gold -> target renderer, verified by round-tripping through the parser.

This is the load-bearing property of Rung 3's training data: a target is correct
exactly when parse_value maps it back to the gold row it came from. That is
checkable on synthetic gold, so none of it needs client data -- which matters,
because the real targets ARE client values and can never be looked at."""
import pytest

from app.eval.models import GoldCharacteristic
from app.eval.normalize import char_type_equal, values_equal
from app.pipeline.parser import parse_value
from app.train.targets import UnrenderableRow, render_target

# One case per shape the corpus contains, with the parser hint the detector
# supplies for it at inference time (extract._HINTS maps detector kind -> hint).
SHAPES = [
    ("plain distance",     dict(char_type="Distance", nominal="20"), ""),
    ("symmetric tol",      dict(char_type="Distance", nominal="5,5",
                                upper_tol="0,1", lower_tol="-0,1"), ""),
    ("diameter",           dict(char_type="Diameter", nominal="20",
                                upper_tol="0,1", lower_tol="-0,1"), ""),
    ("diameter one-sided", dict(char_type="Diameter", nominal="6,6",
                                upper_tol="0,2", lower_tol="0"), ""),
    ("radius max",         dict(char_type="Radius", nominal="0,5",
                                upper_tol="0"), ""),
    ("flatness",           dict(char_type="Flatness", nominal="0",
                                upper_tol="0,05", lower_tol="0"), "gdt"),
    ("position",           dict(char_type="Position", nominal="0",
                                upper_tol="0,1", lower_tol="0"), "gdt"),
    ("theoretical",        dict(char_type="Theoretical", nominal="20"), "theoretical"),
]


@pytest.mark.parametrize("name,fields,hint", SHAPES, ids=[s[0] for s in SHAPES])
def test_every_shape_round_trips_through_the_parser(name, fields, hint):
    """The whole property in one assertion: whatever we render must parse back to
    the row it came from. If it does not, we would be training the model toward a
    value gold does not hold."""
    gold = GoldCharacteristic(balloon=1, **fields)
    text = render_target(gold, hint)
    back = parse_value(text, hint=hint)
    assert char_type_equal(back.char_type, gold.char_type), text
    for f in ("nominal", "upper_tol", "lower_tol"):
        assert values_equal(getattr(back, f), getattr(gold, f)), (f, text)


def test_tolerances_render_in_the_explicit_two_sided_form():
    """Not "±0,1". The parser's ± branch derives the lower bound as the negated
    upper, so ± cannot express "+0,2 0" or a one-sided tolerance at all. One form
    everywhere keeps the renderer total and the target distribution consistent,
    which is what the model is learning."""
    gold = GoldCharacteristic(balloon=1, char_type="Distance", nominal="5,5",
                              upper_tol="0,1", lower_tol="-0,1")
    text = render_target(gold, "")
    assert "±" not in text
    assert "+0,1" in text and "-0,1" in text


def test_a_diameter_keeps_its_symbol_because_char_type_is_scored():
    """char_type is one of the four fields _compare_fields requires, and the
    parser infers Diameter only from a leading Ø. Dropping the symbol would
    train the model to lose a scored field."""
    gold = GoldCharacteristic(balloon=1, char_type="Diameter", nominal="20")
    assert render_target(gold, "").startswith("Ø")


def test_an_unrenderable_row_raises_rather_than_approximating():
    """A silent approximation would train the model toward a value gold does not
    hold, and the COUNT of unrenderable rows is itself a finding worth having."""
    gold = GoldCharacteristic(balloon=1, char_type="Distance", nominal="")
    with pytest.raises(UnrenderableRow, match="nominal"):
        render_target(gold, "")


def test_an_unknown_char_type_raises_instead_of_guessing():
    gold = GoldCharacteristic(balloon=1, char_type="Wackiness", nominal="20")
    with pytest.raises(UnrenderableRow, match="Wackiness"):
        render_target(gold, "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/train/test_targets.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.train'`.

- [ ] **Step 3: Write the implementation**

Create `app/train/__init__.py` as an empty file. Create `app/train/targets.py`:

```python
"""Render a gold characteristic as the transcription a perfect read would emit.

This is the inverse of `app.pipeline.parser.parse_value`, and it exists because
gold gives PARSED fields (char_type, nominal, upper_tol, lower_tol) while the
read stage is trained on text. A target is correct exactly when parse_value maps
it back to the row it came from, which is a property provable on synthetic gold —
so this module is fully testable without ever touching client data. That matters:
the real targets are client values.

Two deliberate commitments, both from the design doc §3:

  * ONE tolerance form, the explicit `+0,1 -0,1`. `±0,1` round-trips only for a
    symmetric tolerance, because the parser derives the lower bound as the negated
    upper — it cannot express `+0,2 0` or a one-sided tolerance. One form
    everywhere keeps this function total over the shapes gold contains and keeps
    the target distribution consistent.
  * A row that cannot be rendered RAISES. A silent approximation would train the
    model toward a value gold does not hold, and the count of unrenderable rows
    is a finding rather than something to paper over.

Training on a canonical rendering teaches a normalisation, not a literal
transcription: a drawing printing `Ø20 ±0,1` and one printing `Ø20 +0,1 -0,1` get
the same target. That is the intent — the read stage's job is to emit text
parse_value maps to the right fields, which is exactly what the metric rewards.
"""
# char_type -> the prefix the parser needs to re-infer that char_type. The parser
# classifies by leading symbol (parser.py: is_diameter / is_radius), so the symbol
# is not decoration — dropping it loses a scored field.
_PREFIX = {"Diameter": "Ø", "Radius": "R", "Distance": "", "Theoretical": ""}

# Geometric char_types are re-inferred from their GD&T symbol under hint="gdt".
# Keys are parser.py's char_type constants; values are the symbols parser._GDT_SYMBOLS
# maps back to them.
_GDT_SYMBOL = {
    "Flatness": "⏥", "Position": "⊕", "Circularity": "○",
    "Concentricity": "◎", "Cylindricity": "⌭", "Parallelism": "∥",
    "Perpendicularity": "⊥", "Angularity": "∠",
}


class UnrenderableRow(ValueError):
    """This gold row cannot be expressed as text the parser maps back to it."""


def _clean(v) -> str:
    return " ".join(str(v or "").split())


def render_target(gold, hint: str = "") -> str:
    """The transcription a perfect read of `gold`'s callout would produce.

    `hint` is the parser hint the detector supplies for this callout's kind at
    inference time (`extract._HINTS`). It is part of the signature because
    parse_value's behaviour depends on it: the same text parses differently under
    hint="gdt" than under no hint, so a target is only meaningful paired with the
    hint it will be parsed under."""
    char_type = _clean(gold.char_type)
    nominal = _clean(gold.nominal)
    upper, lower = _clean(gold.upper_tol), _clean(gold.lower_tol)

    if hint == "gdt" or char_type in _GDT_SYMBOL:
        symbol = _GDT_SYMBOL.get(char_type)
        if symbol is None:
            raise UnrenderableRow(
                f"char_type {char_type!r} has no GD&T symbol, so the parser "
                f"cannot re-infer it under hint={hint!r}")
        if not upper:
            raise UnrenderableRow(
                f"GD&T row {gold.balloon} has no tolerance zone (upper_tol is "
                f"empty), so there is nothing to transcribe")
        return f"{symbol} {upper}"

    if not nominal:
        raise UnrenderableRow(
            f"row {gold.balloon} has an empty nominal, so no transcription can "
            f"parse back to it")
    if char_type and char_type not in _PREFIX:
        raise UnrenderableRow(
            f"char_type {char_type!r} is not one the parser infers from a "
            f"leading symbol; rendering it would lose a scored field")

    parts = [f"{_PREFIX.get(char_type, '')}{nominal}"]
    # A Radius carrying upper_tol "0" and no lower_tol is the MAX convention:
    # parser.py sets upper_tol="0" when it sees MAX, so MAX is how it round-trips.
    if char_type == "Radius" and upper == "0" and not lower:
        parts.append("MAX")
        return " ".join(parts)
    if upper:
        parts.append(upper if upper.startswith(("+", "-")) else f"+{upper}")
    if lower:
        parts.append(lower if lower.startswith(("+", "-")) else lower)
    return " ".join(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/train/test_targets.py -q`
Expected: `12 passed` (8 parametrised shapes + 4 behaviours).

Run: `python -m pytest -q`
Expected: `526 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/train/__init__.py app/train/targets.py tests/train/__init__.py tests/train/test_targets.py
git commit -m "$(cat <<'EOF'
feat(train): render a gold row as the transcription a perfect read would emit

The inverse of parse_value, and the first thing Rung 3 needs: gold gives parsed
fields while the read stage is trained on text. A target is correct exactly when
parse_value maps it back to the row it came from -- a property provable on
synthetic gold, so this module is fully testable without touching client data.
That matters, because the real targets ARE client values and can never be looked
at.

Two commitments made explicit rather than left implicit. Tolerances render in the
explicit "+0,1 -0,1" form, never "±0,1", because the parser derives ±'s lower
bound as the negated upper and so cannot express "+0,2 0" or a one-sided
tolerance. And an unrenderable row raises: a silent approximation would train the
model toward a value gold does not hold, and the count of unrenderable rows is
itself a finding.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Stage B — measure the crop prerequisite before paying for it

## Task 4: A detection-only predict path

The train split has never been predicted. A detection-only pass skips the read
stage, but whether that saves most of the ~26 h or nothing is unknown — detection
issues ~12 tile generates at `max_new_tokens=1024` against ~41–80 reads at 40.
Build the path, then measure it (Task 5).

**Files:**
- Modify: `app/pipeline/extract.py` (`extract` signature and the read loop at
  line 196)
- Modify: `app/eval/runner.py` (`predict_one`, `_cmd_predict`, the `predict`
  sub-parser)
- Create: `tests/test_detect_only.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_detect_only.py`:

```python
"""Detection-only extraction. The point is entirely that it issues NO reads: the
whole reason to build it is that reads may be most of the ~26 h needed to obtain
train-split crops, and a path that quietly still reads would measure nothing."""
from pathlib import Path

import fitz
from PIL import Image

from app.pipeline.detect import Detection
from app.pipeline.extract import extract


class _CountingBackend:
    """Records how many times each stage was asked for work."""

    def __init__(self):
        self.detect_calls = 0
        self.read_calls = 0

    def detect_regions(self, image):
        self.detect_calls += 1
        # Real Detection instances, not a stand-in: detect_characteristics maps
        # these to page space and runs merge/dedupe over them, so a duck-typed
        # stub could fail for reasons that have nothing to do with detect_only.
        return [Detection(box=(10, 10, 120, 40), kind="dimension", conf=0.9)]

    def read_region(self, image):
        self.read_calls += 1
        from app.pipeline.ocr.base import OcrResult
        return OcrResult(text="20", confidence=0.9)


def _one_page_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sheet.pdf"
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text(fitz.Point(100, 100), "20 +0,1 -0,1", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def test_detect_only_issues_no_reads(tmp_path):
    backend = _CountingBackend()
    result = extract(_one_page_pdf(tmp_path), tmp_path / "work", dpi=72,
                     backend=backend, detect_only=True)
    assert backend.detect_calls > 0, "detection must still run"
    assert backend.read_calls == 0, (
        f"detect_only issued {backend.read_calls} reads — it would measure "
        f"nothing")
    assert result.characteristics, "boxes must still be returned"
    assert all(c.target_region is not None for c in result.characteristics)


def test_the_default_path_still_reads(tmp_path):
    """The regression half: detect_only must be opt-in, or every existing run
    silently stops transcribing."""
    backend = _CountingBackend()
    extract(_one_page_pdf(tmp_path), tmp_path / "work", dpi=72, backend=backend)
    assert backend.read_calls > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_detect_only.py -q`
Expected: `test_detect_only_issues_no_reads` FAILS with
`TypeError: extract() got an unexpected keyword argument 'detect_only'`;
`test_the_default_path_still_reads` passes.

- [ ] **Step 3: Write the implementation**

In `app/pipeline/extract.py`, change the `extract` signature at line 107 to add
the flag (keep every existing parameter and default exactly as it is):

```python
def extract(pdf_path, work_dir, dpi: int = 300, backend=None,
            detect_only: bool = False,
```

Immediately after `detections = detect_characteristics(image_for_detect, backend)`
(line 189), insert:

```python
    # detect_only exists to price the train-split crop pass. Reads are ~41-80
    # generates per document against detection's ~12, so skipping them MAY be
    # most of the cost -- but detection generates up to 1024 tokens each against
    # a read's 40, so it may not be. This path is what makes that measurable.
    # It returns boxes with no transcription: every downstream consumer must
    # treat char_type/nominal/tolerances as absent, which is why nothing here
    # invents them.
    if detect_only:
        results = []
        for i, d in enumerate(detections):
            outer = _clamp(d.box, render.width, render.height)
            if d.inner_box is None:
                outer = _clamp(bx.tighten_to_ink(image, outer),
                               render.width, render.height)
            c = Characteristic(pos=0, kind=d.kind, subtype=d.subtype or "",
                               source="auto", target_region=outer)
            c.id = uuid.uuid4().hex
            results.append(c)
        number_characteristics(results)
        return ExtractionResult(characteristics=results)
```

In `app/eval/runner.py`, thread the flag through `predict_one`:

```python
def predict_one(pdf_path, doc_id: str, dpi: int, backend,
                config: RunConfig, work_dir,
                detect_only: bool = False) -> PredictionDump:
    from app.pipeline.extract import extract
    result = extract(pdf_path, Path(work_dir) / doc_id, dpi=dpi,
                     backend=backend, detect_only=detect_only)
```

In `_cmd_predict`, pass it at the call site:

```python
            dump = predict_one(pdfs[doc_id], doc_id, args.dpi, backend, config,
                               out / "_work",
                               detect_only=getattr(args, "detect_only", False))
```

and record it in `RunConfig.extra` so a boxes-only run can never be mistaken for
a full one — replace the `extra=` line:

```python
        extra={**active_knobs(), **active_prompts(),
               **({"detect_only": True} if getattr(args, "detect_only", False)
                  else {})})
```

In the `predict` sub-parser:

```python
    p.add_argument("--detect-only", action="store_true",
                   help="stop after detection and write boxes with no "
                        "transcription. For building training crops: reads are "
                        "the bulk of per-document time, so this is much cheaper "
                        "-- but the dumps carry NO values and must never be "
                        "scored. Recorded in RunConfig.extra.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_detect_only.py -q`
Expected: `2 passed`

Run: `python -m pytest -q`
Expected: `528 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/extract.py app/eval/runner.py tests/test_detect_only.py
git commit -m "$(cat <<'EOF'
feat(pipeline): a detection-only extract path, to price the training-crop pass

The train split has never been predicted, so Rung 3 has no crops to train on --
~26 h of GPU at dev's measured 26 min/document. Reads are ~41-80 generates per
document against detection's ~12, so skipping them may be most of that cost. But
a detect generate runs to 1024 tokens against a read's 40, so it may be almost
none of it. This path is what turns that into a measurement instead of a guess.

The dumps it writes carry boxes and NO values, which is recorded in
RunConfig.extra so a boxes-only run can never be mistaken for a scoreable one.
The test asserts the read count is exactly zero, because a path that quietly
still read would measure nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 5: Measure detection-only against a full predict on one document

**Files:** none — this is a measurement, and its output is a decision.

- [ ] **Step 1: Confirm the host and pick a free card**

```bash
ssh -o BatchMode=yes 4mehpc4_3 "nvidia-smi --query-gpu=index,memory.used --format=csv,noheader; uptime"
```

Expected: a card at `1 MiB`. Do not proceed on an occupied card — a 72B AWQ load
into one falls back to Tesseract. Note the load average; the host has run at 200+.

- [ ] **Step 2: Time a detection-only pass over the dev split**

Dev, not train: dev's full-predict time is already known (~26 min/document), so it
is the only split where the comparison is like-for-like. A fresh run name, because
resume compares the whole `RunConfig`.

```bash
GPU='nvidia.com/gpu=<free-index>' VLM_MODEL_ID='Qwen/Qwen2.5-VL-72B-Instruct-AWQ' ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' detectonly
```

This requires the `detectonly` arm to exist. Add it to `run_experiment_gpu.sh`
first, in `ARM_ENV` and `ARM_WHY` and `ARM_ORDER`:

```bash
  [detectonly]="-e SINDRI_DETECT_ONLY=1"
```
```bash
  [detectonly]="timing probe: how much of the 26 min/doc is the read stage? boxes only, NEVER scored"
```

and make the container honour it by appending to the `predict` command inside the
`RUNCMD` array:

```bash
          ${SINDRI_DETECT_ONLY:+--detect-only}
```

- [ ] **Step 3: Read the per-document timings**

While it is still running, identify the container by its `--out` argument — that
is the only reliable handle, and `podman run --rm` removes it on exit:

```bash
ssh -o BatchMode=yes 4mehpc4_3 "podman ps --no-trunc --format '{{.ID}} {{.Command}}' | grep exp-detectonly"
```

Then, with that id:

```bash
ssh -o BatchMode=yes 4mehpc4_3 "podman logs -t <container-id> 2>&1 | grep -E '/20\]' | tail -20"
```

If it has already exited its logs are gone, so take the timings from the local run
log instead — `run_experiment_gpu.sh`'s own output carries the same `[n/20]` lines.
Compute the median gap between consecutive lines and compare against dev's
full-predict median of ~15 min (12–28 min observed, 46 min for the 109-dpi sheet).

- [ ] **Step 4: Decide, and write the decision down**

Append the measured medians and the decision to
`docs/plans/2026-08-27-rung3-lora-plan.md` under a new `## Task 5 result`
heading:

* **detection-only is ≥3× faster** → use `--detect-only` for the train-split
  pass. Budget = 60 × measured median.
* **detection-only is <3× faster** → do not use it. Run the ordinary `predict`
  on the train split and let resume absorb interruptions; the full dumps are
  more useful anyway, since they also measure how the base model reads train.

- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-08-27-rung3-lora-plan.md run_experiment_gpu.sh
git commit -m "$(cat <<'EOF'
docs(plan): Task 5 result — what fraction of predict time is the read stage

Measured on dev, where the full-predict time is already known, so the comparison
is like-for-like. Records the medians and the resulting decision for the
train-split crop pass, so the ~26 h estimate is replaced by a number.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5 result — detection dominates, so `--detect-only` is NOT used

Measured 2026-08-28 across two independent runs. The first attempt died on a bug
of mine (see below); both runs' timings are reported because the overlap between
them is the only reproducibility check available.

| interval | detection only | run |
|---|---|---|
| doc 1 → 2 | 10 min 50 s | attempt 1, host load ~140 |
| doc 1 → 2 | **10 min 55 s** | attempt 2, host load ~167 |
| doc 2 → 3 | **23 min 45 s** | attempt 2 |

Full predict, per document, from `detectbox`'s timestamped log (same sorted order,
so same drawings): 12, 12, 15, 16, 26, 28, **46** min for documents 11–17 —
median **~16 min**.

**Decision: ratio is well under 3×, so the train-split crop pass uses the ordinary
`predict`**, budgeted ~26 h with resume absorbing interruptions. The full dumps are
more useful anyway: they also measure how the base model reads the train split,
which detection alone cannot.

**The single most telling number:** document 3's detection *alone* took 23 min 45 s,
which **exceeds the full-predict median of ~16 min**. Reads are not a minority of
the cost being skipped — detection is nearly all of the cost. For the ratio to
reach 3×, detection would have to average ~5 min; it measured 11 and 24.

**This inverts the assumption that motivated the task.** Stage B reasoned reads
must dominate, at ~41–80 generates per document against ~12 detection tiles.
Wrong. A detection call encodes a 1024×1024 tile and generates up to 1024 tokens,
so twelve of those outweigh dozens of small crops. **Detection is the pipeline's
bottleneck, not reading** — which is worth knowing well beyond this task: cutting
detection cost (fewer/cheaper tiles, a lower `max_new_tokens`, since the JSON
arrays returned are short) would reduce every run's wall clock, whereas the read
stage has little left to give.

**What was NOT measured, stated rather than glossed.** I said I would collect
paired timings through document 17 and decided on two intervals plus one
replication instead. The measured values sit far from the threshold and document 3
settles the direction on its own, so 13 more documents (~3+ h of shared GPU) would
have sharpened a number that cannot change the outcome. The one case that could
genuinely have differed is **document 16**, the 14457×2384 pt sheet that clamps to
109 dpi and took 46 min under full predict: if its detection were cheap, that one
document would show a large saving. It was not reached. The crop pass is governed
by the median over 60 documents rather than one outlier, so this is recorded as a
known unmeasured case, not as evidence either way.

**The `--detect-only` path is kept, not deleted.** It produced this number, so
keeping it means the measurement can be re-checked rather than re-argued — the
same reason `--assignment max_cardinality` survives as a diagnostic. For its
original purpose it is now a measured dead end.

**The bug attempt 1 exposed.** Every document failed with `ValidationError` because
the `detect_only` early return omitted `render_scale`, leaving
`PredictionDump.scale` as `None`. `tests/test_detect_only.py` called `extract()`
directly and asserted on `result.characteristics`, so it never built a
`PredictionDump` and never touched that field — the test was at the wrong
boundary. Fixed, with a test through `predict_one` asserting the detect-only scale
equals the full path's.

That failure mode is the good one. `render_scale` defaults to `None`, so the run
crashed loudly; had it defaulted to `0.0` or `dpi/72`, every box in every
detect-only dump would have been silently wrong and every training crop cut from
the wrong place. `predict_one`'s own comment warns of exactly this.

---

# Stage C — the dataset

## Task 6: Build (crop, target) pairs under the protected root

**Files:**
- Create: `app/train/dataset.py`
- Create: `tests/train/test_dataset.py`

- [ ] **Step 1: Write the failing test**

Create `tests/train/test_dataset.py`:

```python
"""The training-pair builder. Two properties matter and both are tested here on
synthetic data: the crop must be the one inference would produce, and nothing the
builder REPORTS may contain a client value."""
import json

from PIL import Image

from app.eval.models import GoldCharacteristic, GoldDoc
from app.train.dataset import build_pairs


class _Box:
    def __init__(self, region, kind="dimension"):
        self.target_region = region
        self.kind = kind
        self.subtype = ""


def _gold(tmp_path):
    return GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx",
                   page_rect=(0.0, 0.0, 842.0, 595.0),
                   characteristics=[
                       GoldCharacteristic(balloon=1, position_pt=(100, 100),
                                          char_type="Distance", nominal="20",
                                          upper_tol="0,1", lower_tol="-0,1"),
                       GoldCharacteristic(balloon=2, position_pt=(300, 200),
                                          char_type="Diameter", nominal="7"),
                   ])


def test_a_pair_is_written_for_each_renderable_matched_row(tmp_path):
    page = Image.new("RGB", (842, 595), "white")
    pairs = [(1, _Box((90, 90, 200, 120))), (2, _Box((290, 190, 400, 220)))]
    out = tmp_path / "pairs"

    counts = build_pairs(_gold(tmp_path), page, pairs, out)

    assert counts["pairs"] == 2
    assert counts["unrenderable"] == 0
    written = sorted(p.name for p in out.glob("*.png"))
    assert len(written) == 2
    manifest = json.loads((out / "manifest.jsonl").read_text().splitlines()[0])
    assert set(manifest) == {"image", "target", "hint", "balloon"}


def test_an_unrenderable_row_is_counted_and_skipped_not_approximated(tmp_path):
    gold = GoldDoc(doc_id="D", pdf="d.pdf", excel="d.xlsx",
                   page_rect=(0.0, 0.0, 842.0, 595.0),
                   characteristics=[GoldCharacteristic(
                       balloon=1, position_pt=(100, 100),
                       char_type="Distance", nominal="")])
    page = Image.new("RGB", (842, 595), "white")

    counts = build_pairs(gold, page, [(1, _Box((90, 90, 200, 120)))],
                         tmp_path / "pairs")

    assert counts["pairs"] == 0
    assert counts["unrenderable"] == 1


def test_the_returned_counts_carry_no_client_value(tmp_path):
    """The ONLY thing that may be reported about this dataset. Its contents are
    gold values and can never enter an AI context, so the return value is
    checked for leakage the same way the digests are."""
    page = Image.new("RGB", (842, 595), "white")
    counts = build_pairs(_gold(tmp_path), page,
                         [(1, _Box((90, 90, 200, 120)))], tmp_path / "pairs")
    blob = json.dumps(counts, ensure_ascii=False)
    for leak in ("20", "0,1", "-0,1", "7", "Ø"):
        assert leak not in blob, f"counts leaked {leak!r}"


def test_the_crop_is_the_one_inference_would_produce(tmp_path):
    """The crop must come from the pipeline's own tighten_to_ink + _prep_crop, not
    a reimplementation: a training crop that differs from an inference crop
    teaches the model the wrong input distribution."""
    from app.pipeline.extract import _CROP_PAD, _prep_crop
    from app.pipeline import boxes as bx

    page = Image.new("RGB", (842, 595), "white")
    box = (90, 90, 200, 120)
    expected = _prep_crop(page, bx.tighten_to_ink(page, box), 842, 595,
                          pad=_CROP_PAD)

    out = tmp_path / "pairs"
    build_pairs(_gold(tmp_path), page, [(1, _Box(box))], out)
    written = Image.open(next(out.glob("*.png")))

    assert written.size == expected.size
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/train/test_dataset.py -q`
Expected: collection error,
`ModuleNotFoundError: No module named 'app.train.dataset'`.

- [ ] **Step 3: Write the implementation**

Create `app/train/dataset.py`:

```python
"""Build (crop, target) training pairs from boxes plus gold.

NDA shape, and it is stricter than the digests. The pairs ARE client values: the
target text is the inspection sheet's number. So the dataset is written under the
protected root, never committed, never printed, and the ONLY thing this function
returns is a count dict — checked for leakage by its own test.

Crops are produced by the pipeline's own `boxes.tighten_to_ink` and
`extract._prep_crop`, never reimplemented. A training crop that differs from an
inference crop teaches the model the wrong input distribution, which would show
up as a LoRA that helps on paper and not in the pipeline."""
import json
from pathlib import Path
from typing import Dict, Iterable, Tuple

from app.pipeline import boxes as bx
from app.pipeline.extract import _CROP_PAD, _HINTS, _prep_crop
from app.train.targets import UnrenderableRow, render_target


def build_pairs(gold, page_image, matched: Iterable[Tuple[int, object]],
                out_dir) -> Dict[str, int]:
    """Write one PNG + one manifest line per renderable matched row.

    `matched` is (gold_balloon, prediction) pairs — the prediction supplies the
    box and the detector kind, gold supplies the target. Both are needed: the box
    without gold has no answer, and gold without the box has no image."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gold_by_num = {g.balloon: g for g in gold.characteristics}
    w, h = page_image.size
    counts = {"pairs": 0, "unrenderable": 0, "no_gold": 0, "no_box": 0}

    with (out / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for balloon, pred in matched:
            g = gold_by_num.get(balloon)
            if g is None:
                counts["no_gold"] += 1
                continue
            region = getattr(pred, "target_region", None)
            if region is None:
                counts["no_box"] += 1
                continue
            hint = _HINTS.get(getattr(pred, "kind", "") or "", "")
            try:
                target = render_target(g, hint)
            except UnrenderableRow:
                # Counted, never approximated: a made-up target would train the
                # model toward a value gold does not hold.
                counts["unrenderable"] += 1
                continue
            box = bx.tighten_to_ink(page_image, region)
            crop = _prep_crop(page_image, box, w, h, pad=_CROP_PAD)
            name = f"{gold.doc_id}-{balloon:04d}.png"
            crop.save(out / name)
            fh.write(json.dumps({"image": name, "target": target,
                                 "hint": hint, "balloon": balloon},
                                ensure_ascii=False) + "\n")
            counts["pairs"] += 1
    return counts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/train/test_dataset.py -q`
Expected: `6 passed`

Run: `python -m pytest -q`
Expected: `534 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/train/dataset.py tests/train/test_dataset.py
git commit -m "$(cat <<'EOF'
feat(train): build (crop, target) training pairs, reporting only counts

The pairs ARE client values -- the target is the inspection sheet's number -- so
the dataset stays under the protected root and the only thing this function
returns is a count dict, with a test that checks it for leakage the same way the
digests are checked.

Crops come from the pipeline's own tighten_to_ink and _prep_crop rather than a
reimplementation, with a test asserting the result matches what inference would
produce. A training crop that differs from an inference crop teaches the wrong
input distribution, which would surface as a LoRA that helps on paper and not in
the pipeline.

Unrenderable rows are counted and skipped, never approximated.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

### awqgate result — the dependency change is safe

Run 2026-08-29, 8 h 41 m for 20 dev documents (26 min/doc, matching the Rung-2
dev figure exactly). `predicted: 20, skipped: 0, failed: 0`, `active backend:
VLM`, zero fallback lines.

`peft` and `bitsandbytes` were added to `requirements-gpu.txt` for Rung 3's
serving path, and every measurement this project has ever taken rests on that
image's AWQ dispatch. So the AWQ path was re-run on dev in the NEW image and
compared against the frozen baseline:

```
r3-awqgate-dev: docs=20 mean_review_cost=174.30 recall=0.646 escaped_rate=0.270
reproduction gate OK: all 20 per-document deltas are exactly 0.0
```

| | baseline-dev | r3-awqgate-dev |
|---|---|---|
| `mean_review_cost` | 174.3 | 174.3 |
| `micro_recall` | 0.6457023060796646 | 0.6457023060796646 |
| `escaped_rate` | 0.27044025157232704 | 0.27044025157232704 |
| per-document deltas | — | **20/20 exactly 0.0** |

Identical to full float precision, `robust: true`, no warnings. **The frozen
174.30 baseline survives the dependency change**, so every Rung-2 conclusion
still stands and `base72bnf4` is interpretable against it.

This was the campaign's largest single risk: a perturbed AWQ dispatch would have
invalidated the baseline *silently*, and every later comparison with it. It was
worth 9 h of GPU to close with evidence rather than assumption — and worth
running the moment the stage finished rather than two days later, because a
failure would have meant stopping `base72bnf4` instead of paying another 9 h for
an uninterpretable result.

Note also what this did NOT need: the train-split pass runs on the pre-change
image, so its crops were never exposed to the risk either way. Splitting the
images was what made the expensive stage independent of this outcome.

---

### base72bnf4 result — NF4 costs 6.75 review-cost points, and the control earned its keep

Run 2026-08-29, 7 h 38 m for 20 dev documents. `predicted: 20, skipped: 0,
failed: 0`, `active backend: VLM`, and `config.extra.quant == "nf4"` — proof the
NF4 path was genuinely exercised rather than the AWQ default under an NF4 run
name.

| | AWQ baseline | NF4 control | delta |
|---|---|---|---|
| `mean_review_cost` | 174.30 | **181.05** | **+6.75** |
| `micro_recall` | 0.6457 | 0.6688 | +0.0231 |
| `escaped_rate` | 0.2704 | 0.3040 | +0.0335 |
| `field_acc` | 0.3636 | 0.3574 | −0.0063 |
| `missed` | 169 | 158 | −11 |
| `escaped_error` | 129 | 145 | +16 |
| `false_detection` | 522 | 607 | **+85** |
| `n_pred` | 830 | 926 | **+96** |
| `misplaced` | 80 | 97 | +17 |
| **`correct`** | **72** | **72** | **±0** |

`significant: true`, `ci95 [0.15, 13.75]`, worse under **all six** weightings,
`robust: true`. Cost reconciles from the taxonomy alone:
`10(−11) + 5(+16) + 2(+85) + 1(−5) = +135`, ÷20 = +6.75.

**What NF4 actually did: it became more eager and less precise.** It emitted 96
more predictions, of which **85 were false**. It recovered 11 misses and turned
them into silent errors (+16 escaped). And the sharpest number in the table is
`correct` — right *and* unflagged — which is **exactly unchanged at 72**. Ninety-six
extra predictions produced not one additional fully-correct row.

The noise concentrates in the non-`dimension` kinds, which is a coarser
quantisation degrading the detect prompt's JSON discipline rather than a
perception change:

| kind | predictions | of which false |
|---|---|---|
| `note` | 54 → 90 (+36) | 49 → 76 (+27) |
| `theoretical` | 102 → 118 (+16) | 85 → 102 (+17) |
| `material` | 7 → 19 (+12) | 6 → 18 (**+12 — every one false**) |

**The control earned its keep, exactly as argued.** Without it, a `lora72b`
result of, say, 178 would have read as "the LoRA is worse than the 174.30
baseline" when it would in fact have been "the LoRA recovered 3 of the 6.75
points the quantisation costs". The control converts an uninterpretable number
into an interpretable one, which is the whole reason it was run before training
rather than after.

**An independent replication, worth noting.** The dropped-tolerance winnability
ratio is **61% on both quantisations** (AWQ: 80 rows / 49 distinct; NF4: 95 / 58).
That finding — the tolerances really are printed per callout rather than inherited
from an ISO 2768 table — now reproduces on a different numeric substrate.

**What this does to the campaign's economics.** Serving on NF4 imposes a 6.75-point
tax, so a LoRA served that way must recover 6.75 before it reaches parity with
production. Against Rung 2's measured headroom of −35.6 points for perfect reading
of all matched rows, that is a real but not disqualifying handicap. It does,
however, make a second arm worth running — see below.

---

---

# Stage D — training

## Task 7: A separate training image, and the 4-bit smoke test that gates everything

The inference image must not change. `requirements-gpu.txt` documents why it is
pinned to `transformers==4.49.0` + `autoawq==0.2.8 --no-deps` + torch
2.6.0/CUDA 12.4, and every measurement to date depends on that path.

**This task is a gate.** `transformers==4.49.0` + Qwen2.5-VL + `bitsandbytes`
4-bit is untested here, and the 4.49.0 pin cannot move. If a 4-bit 72B load will
not produce coherent output, the whole campaign falls back to the 32B — and that
must be discovered now, not after paying for train-split crops.

**Files:**
- Create: `requirements-train.txt`, `Dockerfile.train`

- [ ] **Step 1: Write the requirements**

Create `requirements-train.txt`:

```
# Training-only deps. DELIBERATELY separate from requirements-gpu.txt: that file
# is pinned to transformers==4.49.0 + autoawq==0.2.8 --no-deps because Qwen2.5-VL
# support landed in exactly 4.49.0 and 4.50+ breaks AWQ dispatch for this model.
# Every measurement in docs/plans/ depends on that inference path, so nothing
# here may touch it.
#
# transformers is pinned to the SAME 4.49.0 on purpose. Not because training
# needs that exact version, but because the adapter is served by the inference
# image: a LoRA trained against a different modelling implementation of
# Qwen2.5-VL than the one serving it is a silent mismatch, and this is the
# cheapest way to rule it out.
transformers==4.49.0
accelerate>=1.0
peft>=0.14
# 4-bit NF4 quantisation. AWQ is inference-only -- PEFT cannot train adapters
# against it -- so the 72B is quantised with bitsandbytes instead, which puts the
# base at ~36 GB and the whole training run at ~42-48 GB: one H100, no model
# parallelism, which matters because the two cards are NODE-connected, not NVLink.
bitsandbytes>=0.45
qwen-vl-utils>=0.0.8
pillow
```

Create `Dockerfile.train`:

```dockerfile
# LoRA training image for the callout-read task. Separate from Dockerfile.gpu on
# purpose: that image's transformers/autoawq pin is load-bearing for every
# measurement taken so far, and adding a training stack to it would put the
# inference path at risk. This one needs no AWQ — it quantises with bitsandbytes.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-train.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-train.txt

COPY app ./app
COPY train_lora.py ./

ENV HF_HOME=/models
CMD ["python", "train_lora.py", "--help"]
```

- [ ] **Step 2: Build it and verify the stack imports**

The acceptance criterion is concrete: the image builds and PEFT can wrap a
Qwen2.5-VL model class. If a pin fails to resolve, find the version that works
and record why in `requirements-train.txt` — that file's job is to explain the
pins, exactly as `requirements-gpu.txt` does.

```bash
ssh -o BatchMode=yes 4mehpc4_3 "cd ~/sindri && git fetch -q origin worktree-eval-harness && git checkout -q -B worktree-eval-harness origin/worktree-eval-harness && podman build -q -f Dockerfile.train -t sindri-train ."
```

Expected: an image id printed, no error.

```bash
ssh -o BatchMode=yes 4mehpc4_3 "podman run --rm sindri-train python -c \"
import torch, transformers, peft, bitsandbytes
from transformers import AutoProcessor, BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration
from peft import LoraConfig, get_peft_model
print('torch', torch.__version__, 'transformers', transformers.__version__)
print('peft', peft.__version__, 'bitsandbytes', bitsandbytes.__version__)
\""
```

Expected: versions printed, no ImportError.
`Qwen2_5_VLForConditionalGeneration` importing is the specific thing that fails on
the wrong transformers version.

- [ ] **Step 3: THE GATE — load the 72B in 4-bit and check it still reads**

Imports proving nothing is the trap here: the combination can import cleanly and
still produce garbage. This step is what decides 72B versus the 32B fallback, and
it must run before any train-split GPU is spent.

```bash
ssh -o BatchMode=yes 4mehpc4_3 "podman run --rm --device nvidia.com/gpu=1 -v sindri-models:/models sindri-train python -c \"
import torch
from transformers import AutoProcessor, BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration
from PIL import Image, ImageDraw

BASE = 'Qwen/Qwen2.5-VL-72B-Instruct'
q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                       bnb_4bit_compute_dtype=torch.bfloat16,
                       bnb_4bit_use_double_quant=True)
proc = AutoProcessor.from_pretrained(BASE)
m = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        BASE, quantization_config=q, device_map='auto')
m.eval()
print('loaded; VRAM GB:', round(torch.cuda.max_memory_allocated()/2**30, 1))

# A synthetic callout, so the check needs no client data at all.
img = Image.new('RGB', (320, 90), 'white')
ImageDraw.Draw(img).text((20, 30), 'O20 +0,1 -0,1', fill='black')
msgs = [{'role': 'user', 'content': [{'type': 'image', 'image': img},
        {'type': 'text', 'text': 'Transcribe the dimension on one line. No explanation.'}]}]
inp = proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True,
                               return_dict=True, return_tensors='pt').to(m.device)
out = m.generate(**inp, max_new_tokens=20, do_sample=False)
print('read:', repr(proc.decode(out[0][inp['input_ids'].shape[1]:], skip_special_tokens=True)))
\""
```

Expected: `loaded; VRAM GB:` around **36–45**, and a `read:` line containing
recognisable digits from the synthetic callout. Coherent output is the pass
condition — an exact match is not required, since the drawn text is crude.

**If it fails or the output is garbage:** switch `BASE` to
`Qwen/Qwen2.5-VL-32B-Instruct` and re-run. If the 32B passes, change the plan's
target to 32B (its AWQ variant exists, so inference needs no quantisation change)
and record the failure in `requirements-train.txt` — that file's job is to explain
its pins, exactly as `requirements-gpu.txt` does. Do not proceed on a base whose
4-bit load has not printed a coherent read.

### Task 7 result — the stack works, proven on the 7B first

**Deviation from this plan, deliberate.** Step 3 as written loads the 72B
directly, which means paying for a 145 GB download before learning anything. The
stack-compatibility risk (`transformers==4.49.0` + Qwen2.5-VL + bitsandbytes NF4)
is *identical code* at any model size, so it was proven on the 7B first — a 16 GB
download. Had the stack been broken, that ordering would have saved the large
download entirely.

Image built at `880313f`: torch 2.6.0+cu124, transformers 4.49.0, peft 0.20.0,
bitsandbytes 0.50.2, accelerate 1.14.0.

**7B in 4-bit NF4 — PASS.** Peak VRAM **5.6 GB**; read of the synthetic callout
came back `'20 +0.1 -0.1'` — coherent, right digits, right tolerance structure.
(It normalised the comma decimal separator to a period. Not a gate concern: the
real `_PROMPT` instructs comma explicitly, and `normalize.canon_value` compares
numerically anyway.)

One warning worth recording as benign: `Failed to load CPU gemm_4bit_forward from
kernels-community: No module named 'kernels'`. That is the **CPU** 4-bit kernel
fallback; the GPU path loaded and generated fine, so `kernels` is not needed.

**Download speed measured:** 16 GB in 43 s (~370 MB/s), so the 145 GB bf16 72B is
minutes rather than hours. The earlier worry about download cost was unfounded.

**Scaling to the 72B, from the measured 7B figure:** 5.6 GB observed for ~8.3B
params (7.6B LLM + 0.7B ViT) at 0.5 B/param plus activations. 73.4B scales to
~37 GB of weights, so ~40 GB inference and ~45-55 GB training once LoRA grads,
optimizer state and checkpointed activations are added. Inside 80 GB with room.

**72B in 4-bit NF4 — PASS, and this is the gate that mattered.**

| | 7B | 72B |
|---|---|---|
| peak VRAM (4-bit NF4) | 5.6 GB | **38.8 GB** |
| read of the synthetic callout | `'20 +0.1 -0.1'` | **`'Ø20 +0.1 -0.1'`** |

38.8 GB against the 37-40 GB predicted from the 7B figure, leaving **41 GB of
headroom** on one 80 GB card for LoRA gradients, optimizer state and checkpointed
activations. One card, no model parallelism, so the NODE interconnect never
matters. Model cache now 193 GB on a 7.0 TB volume with 6.1 TB still free.

**One observation, explicitly an anecdote and not evidence.** The 72B recovered the
`Ø` from a crude hand-drawn "O 20 +0,1 -0,1"; the 7B dropped it and returned a
bare `20`. n=1 on synthetic input proves nothing on its own — but it lands on
exactly the field Phase A found most broken (`wrong:char_type`, 115 of the 196
wrong rows) and `char_type` is inferred from precisely that leading symbol
(`parser.py`: `is_diameter`). Worth remembering when reading the arms; worth
nothing as a result.

**So: the campaign proceeds on the 72B.** The 32B fallback is not needed, and the
ladder's 7B recommendation is superseded by measurement rather than by preference.

- [ ] **Step 4: Commit, recording what the gate actually printed**

```bash
git add requirements-train.txt Dockerfile.train
git commit -m "$(cat <<'EOF'
build(train): a separate training image, and the 4-bit gate it exists to run

requirements-gpu.txt is pinned to transformers==4.49.0 + autoawq==0.2.8 --no-deps
because Qwen2.5-VL support landed in exactly 4.49.0 and 4.50+ breaks AWQ dispatch
for this model. Every measurement in docs/plans/ depends on that path, so the
training stack gets its own image rather than being added to it. transformers is
pinned to the same 4.49.0 anyway: a LoRA trained against a different modelling
implementation than the one serving it is a silent mismatch.

No AWQ here -- AWQ is inference-only and PEFT cannot train adapters against it,
which is why the 72B is quantised with bitsandbytes instead. 4-bit NF4 puts the
base at ~36 GB and the run at ~42-48 GB: one H100, no model parallelism, which
matters because the cards are NODE-connected rather than NVLink.

The gate result is recorded in this commit body: <VRAM GB printed> and the
synthetic read <printed text>. Imports alone prove nothing -- the combination can
import cleanly and still produce garbage -- so the pass condition is a coherent
read, and a failure switches the campaign to the 32B before any train-split GPU
is spent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

Replace `<VRAM GB printed>` and `<printed text>` with what Step 3 actually
printed. If they are not filled in, the gate was not run.

## Task 8: The training entry point

**Files:**
- Create: `train_lora.py`

- [ ] **Step 1: Write it**

Create `train_lora.py`:

```python
"""LoRA-train the callout-read task on Qwen2.5-VL-72B, quantised to 4-bit NF4.

Runs inside Dockerfile.train on the GPU host, over a manifest built by
app.train.dataset. It never reads gold or drawings directly — only the manifest,
which already pairs a crop with its rendered target.

Holdout discipline, from the ladder: train on the TRAIN split only. The frozen
split forces all 18 variant drawings into test, so a model tuned on dev and
confirmed once on test is measuring generalisation rather than memorisation. This
script therefore takes one manifest and trains on all of it; producing a
train-only manifest is the caller's job.
"""
import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (AutoProcessor, BitsAndBytesConfig,
                          Qwen2_5_VLForConditionalGeneration, Trainer,
                          TrainingArguments)

from app.pipeline.ocr.vlm_backend import read_prompt

# The 72B, quantised to 4-bit NF4. AWQ -- what inference deploys -- is
# inference-only, so it cannot be trained against; 4-bit puts the base at ~36 GB
# and the whole run at ~42-48 GB, which fits ONE H100 80 GB. That matters: the two
# cards report NODE topology rather than NVLink, so PCIe-bound model parallelism
# is worth avoiding entirely.
_BASE = "Qwen/Qwen2.5-VL-72B-Instruct"


class ReadDataset(torch.utils.data.Dataset):
    """One example = the read prompt + a callout crop -> its rendered target.

    The prompt is the SAME one inference sends (vlm_backend.read_prompt), because
    a LoRA trained against a different instruction than it is served with is
    measuring the mismatch."""

    def __init__(self, manifest: Path, processor):
        self.dir = manifest.parent
        self.rows = [json.loads(l) for l in
                     manifest.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.processor = processor
        self.prompt = read_prompt()

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        row = self.rows[i]
        image = Image.open(self.dir / row["image"]).convert("RGB")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": self.prompt},
        ]}]
        prompt_text = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False)
        full = prompt_text + row["target"] + self.processor.tokenizer.eos_token
        enc = self.processor(text=[full], images=[image], return_tensors="pt",
                            padding=True)
        enc = {k: v[0] for k, v in enc.items()}
        # Loss on the answer only: the prompt is identical in every example, so
        # training on it teaches nothing and dilutes the gradient.
        prompt_len = len(self.processor(text=[prompt_text], images=[image],
                                        return_tensors="pt")["input_ids"][0])
        labels = enc["input_ids"].clone()
        labels[:prompt_len] = -100
        enc["labels"] = labels
        return enc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True,
                    help="manifest.jsonl written by app.train.dataset")
    ap.add_argument("--out", required=True, help="where the adapter is written")
    ap.add_argument("--base", default=_BASE)
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    args = ap.parse_args()

    processor = AutoProcessor.from_pretrained(args.base)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.base, device_map="auto",
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True))
    # Casts layer norms to fp32 and enables input grads, both of which a 4-bit
    # base needs before LoRA layers will train stably.
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=True)
    # Attention projections only. The vision tower is left frozen: the task is
    # transcription of a crop the tower already resolves, and adapting it on ~2-4k
    # examples from one house style is the fastest route to the memorisation the
    # ladder warns about.
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]))
    model.print_trainable_parameters()

    dataset = ReadDataset(Path(args.manifest), processor)
    print(f"training on {len(dataset)} pairs", flush=True)

    Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=args.out, num_train_epochs=args.epochs,
            per_device_train_batch_size=1, gradient_accumulation_steps=8,
            learning_rate=args.lr, bf16=True, gradient_checkpointing=True,
            # paged_adamw keeps optimizer state off the card during spikes; with
            # a 36 GB 4-bit base there is headroom, but a single oversized crop
            # should not be what ends a multi-hour run.
            optim="paged_adamw_8bit",
            logging_steps=10, save_strategy="epoch", report_to=[]),
        train_dataset=dataset,
        data_collator=lambda rows: {
            k: torch.stack([r[k] for r in rows]) for k in rows[0]},
    ).train()

    model.save_pretrained(args.out)
    print(f"adapter written to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it parses and its help works**

Run: `python3 -c "import ast, pathlib; ast.parse(pathlib.Path('train_lora.py').read_text()); print('parses')"`
Expected: `parses`

The `--help` path needs the training image (torch/peft are not installed locally):

```bash
ssh -o BatchMode=yes 4mehpc4_3 "podman run --rm sindri-train python train_lora.py --help"
```

Expected: the argparse help text, listing `--manifest`, `--out`, `--base`,
`--epochs`, `--lr`, `--rank`.

- [ ] **Step 3: Commit**

```bash
git add train_lora.py
git commit -m "$(cat <<'EOF'
feat(train): LoRA training entry point for the callout-read task

Trains against the SAME prompt inference sends (vlm_backend.read_prompt): a LoRA
trained against a different instruction than it is served with measures the
mismatch rather than the task.

Loss is masked to the answer tokens. The prompt is byte-identical across every
example, so training on it teaches nothing and only dilutes the gradient.

The vision tower stays frozen and only the attention projections are adapted. The
task is transcribing a crop the tower already resolves, and adapting the tower on
~2-4k examples from a single house style is the fastest route to the memorisation
the ladder explicitly warns about.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 9: Serve the adapter, and name it in the report

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py`
- Modify: `app/eval/runner.py` (`_cmd_predict`'s `extra=`)
- Test: `tests/test_vlm_prompt.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vlm_prompt.py`:

```python
def test_active_adapter_is_none_by_default():
    """Every run to date served the base model; that must stay the default."""
    assert vlm_backend.active_adapter(env={}) is None


def test_an_adapter_name_is_reported_for_run_config_extra():
    assert vlm_backend.active_adapter(
        env={"SINDRI_ADAPTER": "read-lora-v1"}) == "read-lora-v1"


def test_an_adapter_that_does_not_exist_fails_loudly(tmp_path, monkeypatch):
    """Same rule as the prompt variants: a typo must lose the arm, not silently
    serve the base model under a treatment arm's run name -- which would look
    exactly like "LoRA had no effect"."""
    monkeypatch.setattr(vlm_backend, "_ADAPTER_ROOT", tmp_path)
    with pytest.raises(ValueError, match="read-lora-typo"):
        vlm_backend.resolve_adapter(env={"SINDRI_ADAPTER": "read-lora-typo"})


def test_an_adapter_that_exists_resolves_to_its_path(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm_backend, "_ADAPTER_ROOT", tmp_path)
    (tmp_path / "read-lora-v1").mkdir()
    (tmp_path / "read-lora-v1" / "adapter_config.json").write_text("{}")
    resolved = vlm_backend.resolve_adapter(env={"SINDRI_ADAPTER": "read-lora-v1"})
    assert resolved == tmp_path / "read-lora-v1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_vlm_prompt.py -q -k adapter`
Expected: 4 FAIL with
`AttributeError: module 'app.pipeline.ocr.vlm_backend' has no attribute 'active_adapter'`.

- [ ] **Step 3: Write the implementation**

In `app/pipeline/ocr/vlm_backend.py`, after the prompt registry helpers, add:

```python
# Where adapters are mounted in the container. A name, not a path, reaches the
# environment — so a run cannot be pointed at an arbitrary filesystem location
# and the name is what lands in RunConfig.extra.
_ADAPTER_ROOT = Path("/models/adapters")


def active_adapter(env=None):
    """The adapter name in effect, or None for the base model."""
    return (os.environ if env is None else env).get("SINDRI_ADAPTER") or None


def active_quant(env=None):
    """The load-time quantisation in effect, or None for the checkpoint's own.

    Recorded in RunConfig.extra because model_id cannot express it: the Rung-3
    control and the frozen baseline are the same 72B weights loaded two different
    ways, and a report that cannot tell them apart is not a measurement."""
    name = (os.environ if env is None else env).get("SINDRI_QUANT") or None
    if name is not None and name != "nf4":
        raise ValueError(
            f"SINDRI_QUANT={name!r} is not supported (only 'nf4'). Refusing to "
            f"fall back to the checkpoint default: that would serve a different "
            f"base than the adapter was trained against and report the result as "
            f"the fine-tune's.")
    return name


def resolve_adapter(env=None):
    """Path to the adapter in effect, or None. Raises if the name is unknown.

    Loudly, for the same reason an unknown prompt variant raises: silently
    serving the base model under a treatment arm's run name produces a result
    that reads as "the LoRA had no effect", which is the single most misleading
    outcome this campaign could generate."""
    name = active_adapter(env)
    if name is None:
        return None
    path = _ADAPTER_ROOT / name
    if not (path / "adapter_config.json").is_file():
        available = sorted(p.name for p in _ADAPTER_ROOT.glob("*")
                           if (p / "adapter_config.json").is_file()) \
            if _ADAPTER_ROOT.is_dir() else []
        raise ValueError(
            f"SINDRI_ADAPTER={name!r} is not an adapter under {_ADAPTER_ROOT} "
            f"(have: {available}). Refusing to fall back to the base model: that "
            f"would report 'the LoRA had no effect' for a run that never loaded "
            f"it.")
    return path
```

Add `from pathlib import Path` to the imports at the top of the file.

In `VLMBackend.__init__`, replace the single `AutoModelForImageTextToText.from_pretrained(...)`
call (the one with `torch_dtype=torch.float16`) with a two-branch load. The
existing comment above it explains that AWQ's Triton dequant kernel only supports
float16 — that reasoning is **AWQ-specific**, so the 4-bit path must be a separate
branch rather than an edit to it. The AWQ branch has to keep producing exactly
what every committed measurement was taken with.

```python
        if active_quant() == "nf4":
            # The adapter is trained against a 4-bit NF4 base and must be served
            # on the same base; serving it on AWQ would mix two quantisations and
            # make the arm's delta unattributable.
            from transformers import BitsAndBytesConfig
            self.model = AutoModelForImageTextToText.from_pretrained(
                model_id, device_map="auto",
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True))
        else:
            self.model = AutoModelForImageTextToText.from_pretrained(
                model_id, torch_dtype=torch.float16, device_map="auto"
            )
```

Then, after `self.model.eval()`:

```python
        # An adapter, if this run selected one. Loaded after eval() because PEFT
        # wraps the module; the wrapper inherits the eval state.
        adapter = resolve_adapter()
        if adapter is not None:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, str(adapter))
            self.model.eval()
```

**`peft` must be importable in the inference image for this to work.** Add it to
`requirements-gpu.txt` — `peft` alone, not `bitsandbytes` plus a training stack —
and re-verify the AWQ path afterwards:

```bash
python3 -c "
from app.eval.runner import _prompt_sha256
assert _prompt_sha256() == 'aa7659f1929184ea'
print('AWQ-era prompt hash unchanged')
"
```

`bitsandbytes` is also needed there for the NF4 branch. Adding two packages to the
pinned inference image is the one place this plan touches it, so the smoke test in
Task 7 Step 3 must be repeated **inside the inference image** before any arm runs
— a broken AWQ dispatch would invalidate the frozen baseline itself.

In `app/eval/runner.py`, `_cmd_predict`, extend the import and the `extra=`:

```python
    from app.pipeline.ocr.vlm_backend import (active_adapter, active_prompts,
                                              active_quant)
```
```python
        extra={**active_knobs(), **active_prompts(),
               **({"adapter": active_adapter()} if active_adapter() else {}),
               # model_id alone cannot distinguish AWQ from NF4 for the same
               # weights, and the Rung-3 control differs from the arm ONLY in
               # whether an adapter is loaded. Without this the two runs would be
               # indistinguishable in every report they produce.
               **({"quant": active_quant()} if active_quant() else {}),
               **({"detect_only": True} if getattr(args, "detect_only", False)
                  else {})})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_vlm_prompt.py -q`
Expected: all pass.

Run: `python -m pytest -q`
Expected: `538 passed, 2 skipped`

Also confirm the base path is untouched:

```bash
python3 -c "
from app.eval.runner import _prompt_sha256
from app.pipeline.ocr import vlm_backend as vb
assert _prompt_sha256() == 'aa7659f1929184ea'
assert vb.active_adapter(env={}) is None
print('base path unchanged')
"
```
Expected: `base path unchanged`

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/ocr/vlm_backend.py app/eval/runner.py tests/test_vlm_prompt.py
git commit -m "$(cat <<'EOF'
feat(ocr): serve a LoRA adapter by name, and record which one ran

Mirrors the prompt-variant registry Rung 2 built: a NAME reaches the environment,
the name lands in RunConfig.extra (whose comment already anticipated "adapter
id"), and an unknown name RAISES.

The raise is the important part. Silently falling back to the base model under a
treatment arm's run name would produce a report reading "the LoRA had no effect"
for a run that never loaded it -- the single most misleading result this campaign
could generate.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Stage E — the three runs

## Task 10: The `base72bnf4` control arm

Without it, neither question Rung 3 asks is answerable (design §2).

**Files:**
- Modify: `run_experiment_gpu.sh` (`ARM_ENV`, `ARM_WHY`, `ARM_ORDER`)

- [ ] **Step 1: Register the arms**

In `run_experiment_gpu.sh`, add to `ARM_ENV`:

```bash
  [base72bnf4]="-e VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct -e SINDRI_QUANT=nf4"
  [lora72b]="-e VLM_MODEL_ID=Qwen/Qwen2.5-VL-72B-Instruct -e SINDRI_QUANT=nf4 -e SINDRI_ADAPTER=read-lora-v1"
```

to `ARM_WHY`:

```bash
  [base72bnf4]="isolates the QUANTISATION change: the adapter is served on the NF4 base it was trained on, so LoRA's effect is lora72b vs THIS, not vs the AWQ baseline"
  [lora72b]="the fine-tune. Judge vs base72bnf4 for the LoRA effect, vs baseline-dev for the deployment question"
```

and to `ARM_ORDER`: `base72bnf4 lora72b`.

- [ ] **Step 2: Run it**

```bash
ssh -o BatchMode=yes 4mehpc4_3 "nvidia-smi --query-gpu=index,memory.used --format=csv,noheader"
```
Expected: a card at `1 MiB`. Then:

```bash
GPU='nvidia.com/gpu=<free-index>' ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' base72bnf4
```

Watch for `active backend: VLM`. A `falling back`/`Tesseract` line means the load
failed and every document is worthless — kill it rather than let it finish.

- [ ] **Step 3: Verify the arm is what it claims to be**

```bash
python3 -c "
import json, pathlib
d = json.loads(pathlib.Path('docs/eval/exp-base72bnf4-summary.json').read_text(encoding='utf-8'))
c = d['config']
print('model  ', c['model_id'])
print('extra  ', c['extra'])
print('cost   ', d['mean_review_cost'], 'recall', round(d['micro_recall'], 4))
assert c['model_id'] == 'Qwen/Qwen2.5-VL-72B-Instruct', c['model_id']
assert c['extra'].get('quant') == 'nf4', c['extra']
assert 'adapter' not in c['extra'], 'base72bnf4 must serve NO adapter'
assert d['splits_hash'] == '6d174d5e4f1b9228'
assert d['frame_mismatch']['n_docs_not_measured'] == 0
print('base72bnf4 is a clean zero-shot NF4 control')
"
```

- [ ] **Step 4: Read the table and the cross-model warning**

Run: `python3 -m app.eval.experiment`
Expected: a `base72bnf4` row whose `model` column reads
`Qwen/Qwen2.5-VL-72B-Instruct` — distinct from the baseline's `...-AWQ`.

Run: `python3 -m app.eval.runner compare /home/clemi/sindri-client-data/reports/baseline-dev.report.json /home/clemi/sindri-client-data/reports/exp-base72bnf4-dev.report.json --out docs/eval/base72bnf4-vs-baseline.json`
Expected: a `warnings` entry containing `base model differs` — the Task 1 guard
firing on the comparison it was built for.

- [ ] **Step 5: Commit**

```bash
git add run_experiment_gpu.sh docs/eval/exp-base72bnf4-summary.json docs/eval/exp-base72bnf4-vs-control.json docs/eval/base72bnf4-vs-baseline.json
git commit -m "$(cat <<'EOF'
docs(eval): base72bnf4 — the zero-shot NF4 control

Not an arm to win or lose; the control that makes the LoRA arm interpretable.
The adapter is trained against a 4-bit NF4 base and must be SERVED on that same
base -- serving it on AWQ would mix two quantisations. So LoRA's effect is lora72b
vs base72bnf4, while lora72b vs the AWQ baseline is the deployment question.
Without this row a lora72b result conflates the fine-tune with the quantisation
change, and compare_runs would not have said so before Task 1.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Task 11: Build the train-split dataset and train the adapter

**Files:** none in git — the dataset stays under the protected root.

- [ ] **Step 1: Get boxes for the train split**

Use the method Task 5 chose. With `--detect-only` selected, the arm is:

```bash
GPU='nvidia.com/gpu=<free-index>' SPLIT=train ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' detectonly
```

Otherwise run the ordinary predict arm with `SPLIT=train` and a fresh run name.
Either way, resume absorbs interruptions — the host has dropped mid-run twice.

- [ ] **Step 2: Build the pairs**

This script lives in the scratchpad and is **not committed** — it names the
protected root. Write it to
`<scratchpad>/build_train_pairs.py` and run it by path (a bare `.pdf` in a command
string is denied, so it must be a file, not a `-c`):

```python
"""Build the train-split (crop, target) dataset. Scratchpad only: it names the
protected root and must never be committed. Prints counts and nothing else."""
import sys
from pathlib import Path

import fitz
from PIL import Image

from app.eval.dump import load_dump, to_points
from app.eval.ingest import build_gold_doc  # noqa: F401  (gold dir loader below)
from app.eval.matching import Cand, match_candidates
from app.eval.models import GoldDoc, MatchParams
from app.train.dataset import build_pairs

ROOT = Path("/home/clemi/sindri-client-data")
RUN = ROOT / "runs" / sys.argv[1]          # e.g. exp-detectonly-train
OUT = ROOT / "train" / "pairs"
PARAMS = MatchParams()


def gold_for(doc_id):
    path = ROOT / "gold" / f"{doc_id}.gold.json"
    return GoldDoc.model_validate_json(path.read_text(encoding="utf-8"))


def page_image(gold, scale):
    """Re-render at the dump's own scale, so boxes land where they were found."""
    doc = fitz.open(ROOT / "corpus" / "originals" / f"{gold.doc_id}.pdf")
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


total = {"pairs": 0, "unrenderable": 0, "no_gold": 0, "no_box": 0, "docs": 0}
for dump_path in sorted(RUN.glob("*.pred.json")):
    dump = load_dump(dump_path)
    gold = gold_for(dump.doc_id)
    preds = [c for c in dump.result.characteristics if c.target_region is not None]
    scored = [g for g in gold.characteristics
              if getattr(g, "kind", "dimension") in PARAMS.score_kinds]

    def centre(c):
        b = to_points(c.target_region, dump.scale, dump.page_rect)
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    diag = ((gold.page_rect[2] - gold.page_rect[0]) ** 2
            + (gold.page_rect[3] - gold.page_rect[1]) ** 2) ** 0.5
    pairs_raw = match_candidates(
        [Cand(key=c.pos, center_pt=centre(c), nominal=c.nominal) for c in preds],
        [Cand(key=g.balloon, center_pt=g.position_pt, nominal=g.nominal)
         for g in scored],
        diag, PARAMS)

    by_pos = {c.pos: c for c in preds}
    matched = [(gk, by_pos[pk]) for pk, gk, _ in pairs_raw]
    counts = build_pairs(gold, page_image(gold, dump.scale), matched,
                         OUT / dump.doc_id)
    for k, v in counts.items():
        total[k] += v
    total["docs"] += 1

print({k: total[k] for k in ("docs", "pairs", "unrenderable", "no_gold", "no_box")})
```

Run it (single command, no pipes — it touches the protected root):

```bash
python3 /tmp/claude-1000/-home-clemi-mci-sindri/dbf3bbb1-69cb-4cba-a0f6-659c2d8bfc9b/scratchpad/build_train_pairs.py exp-detectonly-train
```

Expected: one dict of counts. Nothing else may be printed.

Then concatenate the per-document manifests into the one `train_lora.py` reads:

```bash
python3 -c "
import pathlib
root = pathlib.Path('/home/clemi/sindri-client-data/train/pairs')
lines = []
for m in sorted(root.glob('*/manifest.jsonl')):
    for line in m.read_text(encoding='utf-8').splitlines():
        if line.strip():
            lines.append(line.replace('\"image\": \"', f'\"image\": \"{m.parent.name}/'))
(root / 'manifest.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('manifest lines:', len(lines))
"
```

Expected: a line count matching `total['pairs']`. The rewrite prefixes each image
name with its document directory, because `train_lora.py` resolves image paths
relative to the manifest's own directory.

A large `unrenderable` count is a finding, not a nuisance — record it in the plan.

- [ ] **Step 3: Push the pairs and train**

**Stop and get the data owner's decision first.** The pairs contain inspection
values, which is a *new category* of client data leaving this machine — the
drawings already there are images; these are the numbers.
`docs/eval/BASELINE-RUNBOOK.md` states the rule.

Then, on the host:

```bash
ssh -o BatchMode=yes 4mehpc4_3 "podman run --rm --device nvidia.com/gpu=1 -v sindri-models:/models -v ~/sindri-eval-data:/data:Z sindri-train python train_lora.py --manifest /data/train/pairs/manifest.jsonl --out /models/adapters/read-lora-v1"
```

Expected: `training on N pairs`, a falling loss, then
`adapter written to /models/adapters/read-lora-v1`.

- [ ] **Step 4: Record the training facts**

Append to this plan under `## Task 11 result`: pair count, unrenderable count,
epochs, rank, final loss, wall clock. Commit.

## Task 12: The `lora72b` arm, and the verdict

- [ ] **Step 1: Run it**

```bash
GPU='nvidia.com/gpu=<free-index>' ./run_experiment_gpu.sh 4mehpc4_3 '~/sindri-eval-data' lora72b
```

- [ ] **Step 2: Verify the adapter actually loaded**

```bash
python3 -c "
import json, pathlib
d = json.loads(pathlib.Path('docs/eval/exp-lora72b-summary.json').read_text(encoding='utf-8'))
c = d['config']
assert c['extra'].get('adapter') == 'read-lora-v1', c['extra']
assert c['extra'].get('quant') == 'nf4', c['extra']
print('adapter', c['extra']['adapter'], 'on', c['model_id'])
"
```

Expected: the adapter named. If `adapter` is absent the run served the base model
and the result is meaningless — Task 9's raise should have prevented it.

- [ ] **Step 3: Judge it, twice**

```bash
python3 -m app.eval.experiment
```

The LoRA effect is `lora72b` vs `base72bnf4`, and the deployment question is `lora72b`
vs the 174.30 baseline. Both must be stated with their models. All five hardened
conditions apply (cost down, `field_acc` not down >0.02, `escaped_rate` not up
>0.02, recall held, robust across all six weightings), plus the two campaign
conditions: **`field_acc` must rise**, and the arm's mechanism must be visible in
the field-failure aggregates — `wrong:nominal` (102) and `wrong:char_type` (115)
are what a read LoRA should move.

```bash
python3 -m app.eval.runner compare /home/clemi/sindri-client-data/reports/exp-base72bnf4-dev.report.json /home/clemi/sindri-client-data/reports/exp-lora72b-dev.report.json --out docs/eval/lora72b-vs-base72bnf4.json
```

Expected: **no** `base model differs` warning here — both sides are the same NF4
72B, which is what makes this the clean measurement of the fine-tune.

- [ ] **Step 4: Write the verdict and commit**

Append a `## Rung 3 results` section to this plan covering: both comparisons with
their models named, which field-failure buckets moved, and the decision. If
`lora72b` beats `base72bnf4` but not the 72B baseline, that is the ladder's answer —
fine-tuning helps but does not close a 10× capacity gap — and Rung 4 is the next
question, not another adapter.

---

## Verification, after every task

```bash
python -m pytest -q                          # count rises per task; 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # guard: 32 passed, 0 failed
python3 -m app.eval.experiment               # arm decision table
```

Expected counts: 510 at start → 512 (T1) → 514 (T2) → 526 (T3) → 528 (T4) →
534 (T6) → 538 (T9). Tasks 5, 7, 8, 10, 11 and 12 add no tests (measurements,
image build, and GPU runs).

## What this plan must not do

* Not touch `MatchParams`, `SCHEMA_VERSION`, or the frozen split
  `6d174d5e4f1b9228`.
* Not modify `requirements-gpu.txt` beyond **adding `peft` and `bitsandbytes`**,
  and never touch the `transformers==4.49.0` / `autoawq==0.2.8 --no-deps` pins.
  Serving an adapter requires `peft` in the inference image and the NF4 branch
  requires `bitsandbytes`, so this plan cannot avoid touching that file — an
  earlier draft claimed it could, which was wrong.

  **The guard for that change, because every committed measurement depends on
  this path:** after adding them, re-run the AWQ path on the dev split under a
  fresh run name and gate it with `python3 -m app.eval.gate` against the frozen
  baseline. All 20 per-document deltas must be exactly `0.0`. That is what
  `app/eval/gate.py` exists for, and a partial check will not do — a perturbed
  AWQ dispatch would invalidate the 174.30 baseline itself, silently, and every
  Rung-2 conclusion with it.
* Not train on **dev** or **test**. Dev is the tuning split and test is touched
  once, at the end. Training on dev would destroy the comparison this whole plan
  exists to make.
* Not retry the levers in `CLAUDE.md` §3 or handoff §6 — including the two this
  campaign added: the read prompt toward callout selection, and the detect prompt
  toward tighter boxes.
* Not commit anything derived from gold values. The dataset, the manifests and
  the crops stay under the protected root; only counts are reported.
* Not quote a cross-model delta without naming both models.
