# Review-quality arms (flag policy, false-detection pruning, OCR proposals) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Read `CLAUDE.md` §1 (data rules) and §4 (conventions) before Task 1.** Every `runner` command below is single, unpiped and unchained, as the guard requires.

**Goal:** Attack the three largest review-cost buckets — escaped errors (via a better flag policy), false detections (via gold-free pruning rules) and isolated misses (via OCR proposals checked by the VLM) — and **keep a behaviour change only when it measurably improves quality** on data it was not selected on.

**Architecture:** Flagging and pruning are post-read decisions over a finished `Characteristic`, so they are priced EXACTLY from dumps already on disk: one gold-free rule registry in `app/pipeline/policy_rules.py` is used by both the pipeline and a CPU counterfactual in `app/eval/policy_check.py` (the same deliberate coupling `reparse.py` has with `parser`). A new `auto_accept` metric (precision of the rows the product leaves unflagged) guards against the cost model's flag-everything loophole. OCR proposals get a CPU feasibility gate (`score --proposal-check`) before any GPU arm.

**Tech Stack:** Python 3, pydantic v2, pytest, pytesseract (device prerequisite), PyMuPDF (render), Qwen2.5-VL-72B-AWQ on the GPU host for Part E only.

---

## 0. Why these three, and the numbers they start from

Dev, scoped, shipped config (`docs/eval/cropctx-scoped-summary.json`, 15 docs, 131.87):

| bucket | count | cost units | share |
|---|---|---|---|
| missed | 88 (contended 19, **isolated 58**, unlocated 11) | 880 | 44.5% |
| false detection | 346 (dimension 227, theoretical 54, note 38, gdt 19, surface 7, material 1) | 692 | 35.0% |
| escaped error | 64 | 320 | 16.2% |
| flagged (45 correct + 41 wrong) | 86 | 86 | 4.3% |
| correct, unflagged | 73 | 0 | — |

**The loophole this plan must close first:** flagging every matched row costs 223 against today's 406 (−12.2 per doc). The unflagged set is 64 wrong of 137 = 47% wrong, while leaving a row unflagged only pays when P(wrong) < 1/5. So review cost alone rewards flagging more — and `experiment.verdict`'s guards do not catch it (escaped_rate *falls*). Part A adds the metric that does.

## 1. The keep/revert rule (the user's requirement: keep only what improves quality)

A **behaviour change** is anything that alters what the pipeline outputs: an active flag rule, an active drop rule, the proposal stage. Diagnostics (new digest keys, `--policy-check`, `--proposal-check`, the auto-accept metric) change no output and stay regardless — they are how the decision is made.

**Protocol — select on one split, confirm on two others it never saw:**

| role | run | split | why |
|---|---|---|---|
| select | `r3-trainpredict` | train (60 docs) | largest, never used for any arm decision |
| validate | `r3-cropctx` | dev (15 scoped) | shipped config |
| confirm | `r3-awqtest` | test (11 scoped) | adversarial templates, dev was optimistic by +31 |

If Task 0 finds `r3-trainpredict` unscoreable, select on dev and confirm on test only; the result doc must say so.

**A flag rule is KEPT iff, on validate AND confirm (and on select when available):**
1. mean review cost falls,
2. it is better under **6 of 6** `report.WEIGHT_GRID` weightings,
3. `auto_accept.precision` **rises** (strictly) — this is what blocks flag-everything,
4. (field_acc and recall are untouched by flags by construction; the check asserts that).

**A drop rule is KEPT iff, on the same splits:**
1. mean review cost falls, 6 of 6 weightings,
2. recall falls by no more than `experiment.RECALL_TOLERANCE` (0.005),
3. field_acc falls by no more than `experiment.FIELD_ACC_TOLERANCE` (0.02),
4. escaped_rate does not rise, `auto_accept.precision` does not fall.

**The joint set** of kept rules is then priced together on validate and confirm and must pass the drop-rule conditions plus flag condition 3. If it fails, drop the rule with the smallest dev gain and re-price until it passes or the set is empty.

**The proposal arm is KEPT iff** `r4-proposals` beats `r4-control` by `python3 -m app.eval.experiment` (cost down, 6/6 weightings, field_acc/escaped/recall guards, and the new auto-accept guard), **and** `missed_diagnosis.isolated` falls, **and** the conditional test-split pair (Task 19) agrees in sign.

**Anything not kept is reverted in a commit that says why**, and gets a line in CLAUDE.md §3 (dead ends) with its numbers.

## 2. File map

| file | status | responsibility |
|---|---|---|
| `app/eval/report.py` | modify | `_auto_accept()` digest key; `_false_diagnosis_totals()` digest key |
| `app/eval/experiment.py` | modify | `auto_accept_precision` in `arm_row`, guard in `verdict` |
| `app/pipeline/policy_rules.py` | create | gold-free FLAG_RULES / DROP_RULES registries, `apply_*`, ACTIVE sets |
| `app/eval/policy_check.py` | create | CPU counterfactual: re-flag / prune dumps, re-score, price each rule |
| `app/eval/models.py` | modify | `DocScore.false_diagnosis`, `false_diagnosis_by_kind`, `proposal_matched`, `proposal_false`; `RunReport.reapplied_policy` |
| `app/eval/score.py` | modify | fill the new DocScore fields |
| `app/eval/runner.py` | modify | `score --policy-check/--policy-out/--flag-rules/--drop-rules/--reapply-policy/--proposal-check/--proposal-out` |
| `app/pipeline/review.py` | modify | `active_review_policy()` records active rule names |
| `app/pipeline/extract.py` | modify | apply active rules; optional proposal stage |
| `app/pipeline/proposals.py` | create | tesseract line proposals, rotation mapping, verification loop, env knob |
| `app/pipeline/detect.py` | modify | `Detection.source`; `active_knobs` records the proposal knob |
| `app/pipeline/ocr/vlm_backend.py` | modify | `_VERIFY_PROMPT`, `verify_callout`, prompt-hash inclusion only when enabled |
| `app/eval/proposal_check.py` | create | CPU feasibility count for proposals vs isolated misses |
| `run_gpu_queue.sh` | modify | stages `r4control`, `r4proposals` (+ conditional test pair) |
| tests | create/modify | one test file per new module, listed per task |

---

## Part A — the guard metric (do first; everything else is judged with it)

### Task 0: Confirm the three runs score (no code)

- [ ] **Step 1: Score the select split.** Run exactly (one command):

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-trainpredict" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split train --weights docs/eval/weights.json --name r3-trainpredict-scoped --out "$HOME/sindri-client-data/reports/r3-trainpredict-scoped.report.json"
```

Expected: a line `r3-trainpredict-scoped: docs=N mean_review_cost=...`. If it refuses with missing dumps or fails on detect-only dumps (empty `char_type`/`nominal` everywhere → `field_acc` ≈ 0 in the summary), record "select split unavailable" and use the dev/test fallback from §1.

- [ ] **Step 2: Summarise it** (separate command):

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/r3-trainpredict-scoped.report.json" --out docs/eval/trainpredict-scoped-summary.json
```

Check in the digest: `config.extra.review_low_conf`. If absent, those dumps predate LOW_CONF 0.8 — Task 4's base normalisation handles it; note it in the result doc.

- [ ] **Step 3: Re-score dev and test on current code** so every report carries today's fields (same command shape, `--run .../r3-cropctx --split dev --name r3-cropctx-scoped`, and `--run .../r3-awqtest --split test --name r3-awqtest-scoped`), then `summary` each to `docs/eval/cropctx-scoped-summary.json` / `docs/eval/awqtest-scoped-summary.json`. Expected dev headline: **131.87**. Dumps store the fields parsed at predict time, so the Ø-zone parser fix does not show in a plain re-score; its 131.20 is only reachable through Task 11's `--reapply-policy`.

- [ ] **Step 4: Commit the summaries.**

```bash
git add docs/eval/trainpredict-scoped-summary.json docs/eval/cropctx-scoped-summary.json docs/eval/awqtest-scoped-summary.json
git commit -m "eval: re-score select/validate/confirm runs for the policy arms"
```

### Task 1: `auto_accept` in the digest

**Files:**
- Modify: `app/eval/report.py` (new helper above `summarize`, new key inside it)
- Test: `tests/eval/test_auto_accept.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""auto_accept: the precision of the rows the product leaves UNFLAGGED.

It exists because review cost alone is gameable in the flagging direction:
flag=1 and escaped=5 mean that flagging every matched row cost 223 against
406 on dev (2026-09-25) while telling the reviewer nothing. A policy change
that lowers cost by flagging more must also make the unflagged set MORE
trustworthy, and this is the number that says whether it did."""
from app.eval.models import MatchParams, ReviewCostWeights, RunConfig
from app.eval.report import aggregate, summarize
from app.eval.models import DocScore


def _report(counts, n_gold):
    ds = DocScore(doc_id="D", gold_hash="h", n_gold=n_gold, n_pred=n_gold,
                  counts=counts)
    return aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [ds])


def test_auto_accept_is_correct_over_unflagged_matched_rows():
    r = _report({"correct": 73, "escaped_error": 64, "flagged_correct": 45,
                 "flagged_error": 41, "missed": 88}, n_gold=311)
    aa = summarize(r, lambda d: "x")["auto_accept"]
    assert aa["n_auto"] == 137
    assert aa["precision"] == round(73 / 137, 4)
    assert aa["rate"] == round(73 / 311, 4)


def test_flag_everything_has_no_auto_accept_precision_not_a_perfect_one():
    """Zero unflagged rows is 'nothing automated', never precision 1.0."""
    r = _report({"flagged_correct": 10, "flagged_error": 5}, n_gold=15)
    aa = summarize(r, lambda d: "x")["auto_accept"]
    assert aa["n_auto"] == 0
    assert aa["precision"] is None
    assert aa["rate"] == 0.0
```

- [ ] **Step 2: Run it to see it fail**

Run: `python -m pytest tests/eval/test_auto_accept.py -v`
Expected: FAIL with `KeyError: 'auto_accept'`

- [ ] **Step 3: Implement**

In `app/eval/report.py`, above `def summarize`:

```python
def _auto_accept(report: RunReport) -> Dict:
    """Precision and rate of the rows a reviewer is told NOT to check.

    flag=1 against escaped=5 makes review cost fall whenever more rows are
    flagged, down to flagging everything (223 vs 406 on dev, 2026-09-25) --
    which automates nothing. The product's value is the unflagged set, so a
    policy change must make it more trustworthy, not merely smaller. None when
    nothing is unflagged: an empty set has no precision, and 1.0 would reward
    flag-everything with a perfect score."""
    t = report.taxonomy
    correct, escaped = t.get("correct", 0), t.get("escaped_error", 0)
    n_auto = correct + escaped
    n_gold = sum(d.n_gold for d in report.doc_scores)
    return {"n_auto": n_auto, "correct": correct, "escaped": escaped,
            "precision": round(correct / n_auto, 4) if n_auto else None,
            "rate": round(correct / n_gold, 4) if n_gold else 0.0}
```

Inside `summarize`'s returned dict, directly after `"taxonomy": dict(report.taxonomy),`:

```python
        # Precision of the unflagged rows -- the guard against flag-everything,
        # which review cost alone rewards. See _auto_accept.
        "auto_accept": _auto_accept(report),
```

- [ ] **Step 4: Run to see it pass, then the whole suite**

Run: `python -m pytest tests/eval/test_auto_accept.py -v` → PASS
Run: `python -m pytest -q` → `961 passed, 2 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/eval/report.py tests/eval/test_auto_accept.py
git commit -m "eval: report auto-accept precision, the guard review cost lacks

Flagging every matched row costs 223 vs 406 on dev, so review cost alone
rewards flagging more. The unflagged set's precision is what a policy
change must improve."
```

### Task 2: Guard `experiment.verdict` with auto-accept precision

**Files:**
- Modify: `app/eval/experiment.py` (`arm_row`, `verdict`, a new tolerance constant)
- Test: `tests/eval/test_experiment.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/eval/test_experiment.py`)

```python
def test_arm_row_derives_auto_accept_precision():
    row = arm_row("control", CONTROL)
    assert row["auto_accept_precision"] == round(72 / (72 + 129), 4)


def test_cost_bought_by_flagging_more_is_not_a_win():
    """Flagging correct rows lowers cost (escaped 5 -> flagged 1) while making
    the unflagged set LESS trustworthy. Same recall, same field_acc, cost down
    -- and still a loss."""
    arm = _digest(170.0, 0.6457, 169, 82, 74, 20, 92, 129)
    v = verdict(arm_row("arm", arm), arm_row("control", CONTROL),
                comparison={"weight_sensitivity": {"robust": True,
                            "b_better_fraction": 1.0, "n_weight_vectors": 6}})
    assert not v["win"]
    assert "auto-accept precision fell" in v["why"]
```

- [ ] **Step 2: Run to see it fail**

Run: `python -m pytest tests/eval/test_experiment.py -v -k auto_accept`
Expected: FAIL with `KeyError: 'auto_accept_precision'`

- [ ] **Step 3: Implement**

In `app/eval/experiment.py`, below `RECALL_TOLERANCE = 0.005`:

```python
# Precision of the unflagged rows (correct / (correct + escaped_error)). The
# one condition that catches cost bought by flagging more: flag=1 < escaped=5
# lowers cost for every extra flag, down to flagging everything, which
# automates nothing. Same 0.02 as the other ratio guards.
AUTO_ACCEPT_TOLERANCE = 0.02
```

In `arm_row`, after the `"field_acc"` entry:

```python
        "auto_accept_precision": (
            round(t.get("correct", 0)
                  / (t.get("correct", 0) + t.get("escaped_error", 0)), 4)
            if (t.get("correct", 0) + t.get("escaped_error", 0)) else 0.0),
```

In `verdict`, after `d_rec = ...`:

```python
    d_aap = round(row["auto_accept_precision"]
                  - control["auto_accept_precision"], 4)
```

and after the recall reason block:

```python
    if d_aap < -AUTO_ACCEPT_TOLERANCE:
        reasons.append(f"auto-accept precision fell {d_aap:+.4f} — cost "
                       f"bought by flagging more, which leaves the unflagged "
                       f"rows less trustworthy")
```

and add `"auto_accept_delta": d_aap,` to the returned dict.

- [ ] **Step 4: Run tests, then re-print the decision table and check no historical verdict flipped**

Run: `python -m pytest -q` → all pass.
Run: `python3 -m app.eval.experiment` → compare every arm's `win` with the table in CLAUDE.md §2/§3 (only `r3-cropctx` and `r3-loraread` are wins). If any verdict flips, STOP and write it into the commit body with the numbers — do not loosen the tolerance to hide it.

- [ ] **Step 5: Commit**

```bash
git add app/eval/experiment.py tests/eval/test_experiment.py
git commit -m "eval: an arm must not lower auto-accept precision to win"
```

---

## Part B — Direction 1: price flag rules exactly, on CPU

### Task 3: The gold-free rule registry

**Files:**
- Create: `app/pipeline/policy_rules.py`
- Test: `tests/test_policy_rules.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Gold-free rules over a finished Characteristic. The pipeline and the eval
counterfactual call the SAME functions, which is what makes the offline price
exact rather than an estimate."""
import random

from app.models import Characteristic
from app.pipeline import policy_rules as pr


def _c(pos=1, box=(0, 0, 100, 40), **kw):
    return Characteristic(pos=pos, target_region=box, **kw)


def test_flag_rules_fire_on_what_they_name():
    assert pr.FLAG_RULES["nondim_kind"](_c(kind="theoretical"))
    assert not pr.FLAG_RULES["nondim_kind"](_c(kind="dimension"))
    assert pr.FLAG_RULES["gdt_guessed"](_c(kind="gdt", raw_text="0,05 A"))
    assert not pr.FLAG_RULES["gdt_guessed"](_c(kind="gdt", raw_text="⌖ Ø0,1 A"))
    assert pr.FLAG_RULES["tall_box"](_c(box=(0, 0, 50, 80)))
    assert not pr.FLAG_RULES["tall_box"](_c(box=(0, 0, 50, 79)))
    assert pr.FLAG_RULES["diameter_sign"](_c(raw_text="Ø20"))
    assert pr.FLAG_RULES["multiline"](_c(raw_text="20\n+0,1"))
    assert not pr.FLAG_RULES["multiline"](_c(raw_text="20\n"))
    assert pr.FLAG_RULES["no_tolerance"](_c(kind="dimension", nominal="20"))
    assert not pr.FLAG_RULES["no_tolerance"](
        _c(kind="dimension", nominal="20", upper_tol="0,1"))
    assert pr.FLAG_RULES["asymmetric_tol"](
        _c(upper_tol="+0,2", lower_tol="-0,1"))
    assert not pr.FLAG_RULES["asymmetric_tol"](
        _c(upper_tol="+0,1", lower_tol="-0,1"))


def test_apply_flag_rules_returns_one_reason_per_rule_that_fired():
    c = _c(kind="gdt", raw_text="0,05 A")
    assert pr.apply_flag_rules(c, ("nondim_kind", "gdt_guessed", "tall_box")) \
        == ["rule:nondim_kind", "rule:gdt_guessed"]


def test_contained_duplicate_drops_the_smaller_box_only():
    big = _c(pos=1, box=(0, 0, 100, 40), raw_text="20")
    small = _c(pos=2, box=(10, 5, 50, 35), raw_text="20")
    kept = pr.apply_drop_rules([big, small], ("contained_duplicate",))
    assert kept == [big]


def test_equal_boxes_are_never_both_dropped():
    """Ties keep both: an order-dependent tie-break would make the pipeline
    (pre-numbering order) and the dump (numbered order) disagree, and the
    offline price would stop being exact."""
    a = _c(pos=1, box=(0, 0, 100, 40))
    b = _c(pos=2, box=(0, 0, 100, 40))
    assert pr.apply_drop_rules([a, b], ("contained_duplicate",)) == [a, b]


def test_drop_rules_are_order_independent():
    rng = random.Random(7)
    chars = [_c(pos=i, box=(i * 7 % 50, i * 3 % 40, i * 7 % 50 + 30 + i,
                            i * 3 % 40 + 12), nominal=str(i % 3),
                raw_text=str(i % 3), kind=("note", "dimension")[i % 2])
             for i in range(1, 12)]
    names = tuple(pr.DROP_RULES)
    base = {c.pos for c in pr.apply_drop_rules(chars, names)}
    for _ in range(5):
        shuffled = chars[:]
        rng.shuffle(shuffled)
        assert {c.pos for c in pr.apply_drop_rules(shuffled, names)} == base


def test_other_drop_rules():
    assert pr.DROP_RULES["empty_read"](_c(raw_text="  "), [])
    assert pr.DROP_RULES["no_digit"](_c(kind="dimension", raw_text="A-A"), [])
    assert not pr.DROP_RULES["no_digit"](_c(kind="dimension", raw_text="R5"), [])
    assert pr.DROP_RULES["theoretical_no_nominal"](_c(kind="theoretical"), [])
    assert pr.DROP_RULES["note_kind"](_c(kind="note"), [])
    assert pr.DROP_RULES["theoretical_kind"](_c(kind="theoretical",
                                                nominal="20"), [])
    near_hi = _c(pos=1, box=(0, 0, 40, 20), nominal="20", confidence=0.99)
    near_lo = _c(pos=2, box=(0, 30, 40, 50), nominal="20", confidence=0.90)
    far = _c(pos=3, box=(900, 900, 940, 920), nominal="20", confidence=0.5)
    chars = [near_hi, near_lo, far]
    assert pr.DROP_RULES["repeated_value_nearby"](near_lo, chars)
    assert not pr.DROP_RULES["repeated_value_nearby"](near_hi, chars)
    assert not pr.DROP_RULES["repeated_value_nearby"](far, chars)


def test_nothing_is_active_until_a_rule_is_kept():
    """Behaviour changes only land when §1 of the plan keeps them."""
    assert pr.ACTIVE_FLAG_RULES == ()
    assert pr.ACTIVE_DROP_RULES == ()
```

- [ ] **Step 2: Run to see it fail**

Run: `python -m pytest tests/test_policy_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.policy_rules'`

- [ ] **Step 3: Implement** `app/pipeline/policy_rules.py`

```python
"""Gold-free rules over a finished Characteristic: which extra rows to FLAG for
review, and which predictions to DROP as false detections.

Both decisions happen after the read, so a dump already on disk holds every
input they need -- which is why app/eval/policy_check.py can price a rule
EXACTLY, in CPU seconds, by calling these same functions. Keep it that way:
a rule that needs anything a dump does not store (the image, the notes block,
rotation scores) cannot be priced offline and does not belong here.

Two invariants the counterfactual depends on:
  * flag rules only ADD flags, so stored flags + rules == what the pipeline
    would have produced;
  * drop rules are ORDER-INDEPENDENT and evaluated against the original list,
    because the pipeline applies them before numbering and the dump stores the
    numbered order. Ties therefore keep both rows, never break by position.

Nothing is active by default. A rule becomes active only when the keep/revert
rule in docs/plans/2026-09-25-review-quality-arms-plan.md §1 keeps it."""
import math
import re
from typing import Callable, Dict, List, Sequence

from app.models import Characteristic
from app.pipeline.parser import _GDT_SYMBOLS

# Kinds whose rows read at 0.00-0.18 on dev (read_accuracy_by_kind, 2026-09-17)
# and ship silently wrong 3-5x more often per row than `dimension`.
NON_DIMENSION_KINDS = frozenset({"gdt", "theoretical", "surface", "note"})
# Render pixels. Boxes >= 80 px (6.8 mm at 300 dpi) read at 0.47 against 0.59
# for 40-80 px (crop-height diagnostic, 2026-09-16).
TALL_BOX_PX = 80.0
_DIAMETER_SIGNS = ("Ø", "⌀", "ø")
_DIGIT = re.compile(r"\d")
# A box at least this share inside a strictly larger one is its fragment.
CONTAINED_FRAC = 0.6
# Same nominal within this many box-heights (centre to centre) is one callout
# read twice, not a value the drawing repeats.
NEAR_HEIGHTS = 3.0


def _height(c: Characteristic) -> float:
    r = c.target_region
    return abs(r[3] - r[1]) if r else 0.0


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


# ---- flag rules: Characteristic -> bool -----------------------------------

def _nondim_kind(c):
    return (c.kind or "") in NON_DIMENSION_KINDS


def _gdt_guessed(c):
    # parser._gdt_type returns Flatness when it recognises no symbol, so the
    # char_type of such a row is a guess (0 of 8 char_type-only gdt rows on
    # dev held a known symbol, 2026-09-22).
    text = (c.raw_text or "").replace("\n", " ")
    return c.kind == "gdt" and not any(s in text for s in _GDT_SYMBOLS)


def _tall_box(c):
    return _height(c) >= TALL_BOX_PX


def _diameter_sign(c):
    # Diameter <-> Distance is confused 11 times each way over a leading Ø.
    return any(s in (c.raw_text or "") for s in _DIAMETER_SIGNS)


def _multiline(c):
    return "\n" in (c.raw_text or "").strip()


def _no_tolerance(c):
    return c.kind == "dimension" and bool(c.nominal) \
        and not c.upper_tol and not c.lower_tol


def _asymmetric_tol(c):
    if not (c.upper_tol and c.lower_tol):
        return False
    return c.upper_tol.lstrip("+").replace(",", ".") \
        != c.lower_tol.lstrip("-").replace(",", ".")


FLAG_RULES: Dict[str, Callable[[Characteristic], bool]] = {
    "nondim_kind": _nondim_kind,
    "gdt_guessed": _gdt_guessed,
    "tall_box": _tall_box,
    "diameter_sign": _diameter_sign,
    "multiline": _multiline,
    "no_tolerance": _no_tolerance,
    "asymmetric_tol": _asymmetric_tol,
}


# ---- drop rules: (Characteristic, all rows) -> bool -----------------------

def _empty_read(c, chars):
    return not (c.raw_text or "").strip()


def _contained_duplicate(c, chars):
    a = c.target_region
    if a is None or _area(a) == 0:
        return False
    for o in chars:
        b = o.target_region
        if o is c or b is None:
            continue
        # STRICTLY larger: equal boxes keep both (see module docstring).
        if _area(b) > _area(a) and _inter(a, b) / _area(a) >= CONTAINED_FRAC:
            return True
    return False


def _repeated_value_nearby(c, chars):
    a = c.target_region
    if a is None or not c.nominal:
        return False
    for o in chars:
        b = o.target_region
        if o is c or b is None or o.nominal != c.nominal:
            continue
        reach = NEAR_HEIGHTS * max(_height(c), _height(o), 1.0)
        if math.dist(_center(a), _center(b)) > reach:
            continue
        # Keep the more confident read; equal confidence keeps both.
        if o.confidence > c.confidence:
            return True
    return False


def _no_digit(c, chars):
    return c.kind == "dimension" and not _DIGIT.search(c.raw_text or "")


def _theoretical_no_nominal(c, chars):
    return c.kind == "theoretical" and not c.nominal


def _note_kind(c, chars):
    return c.kind == "note"


def _theoretical_kind(c, chars):
    return c.kind == "theoretical"


DROP_RULES: Dict[str, Callable[[Characteristic, Sequence[Characteristic]], bool]] = {
    "empty_read": _empty_read,
    "contained_duplicate": _contained_duplicate,
    "repeated_value_nearby": _repeated_value_nearby,
    "no_digit": _no_digit,
    "theoretical_no_nominal": _theoretical_no_nominal,
    "note_kind": _note_kind,
    "theoretical_kind": _theoretical_kind,
}

# Filled ONLY by the keep/revert rule. Empty means the pipeline behaves
# exactly as every dump on disk was produced.
ACTIVE_FLAG_RULES: tuple = ()
ACTIVE_DROP_RULES: tuple = ()


def apply_flag_rules(c: Characteristic, names: Sequence[str]) -> List[str]:
    """The review reasons the named rules add for `c`, in `names` order."""
    return [f"rule:{n}" for n in names if FLAG_RULES[n](c)]


def apply_drop_rules(chars: List[Characteristic],
                     names: Sequence[str]) -> List[Characteristic]:
    """`chars` minus every row any named rule drops, judged against the
    ORIGINAL list so the result does not depend on evaluation order."""
    rules = [DROP_RULES[n] for n in names]
    return [c for c in chars if not any(r(c, chars) for r in rules)]
```

- [ ] **Step 4: Run to see it pass**

Run: `python -m pytest tests/test_policy_rules.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/policy_rules.py tests/test_policy_rules.py
git commit -m "feat(policy): gold-free flag and drop rule registry, nothing active

One registry used by both the pipeline and the offline counterfactual, so a
rule's price is exact. Rules are additive (flags) and order-independent
(drops) because the counterfactual depends on both."
```

### Task 4: The counterfactual engine

**Files:**
- Create: `app/eval/policy_check.py`
- Test: `tests/eval/test_policy_check.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""policy_check prices a rule by applying it to dumps and RE-SCORING, so
matching changes (a dropped duplicate letting its neighbour pair) are counted,
not assumed."""
import json

from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.policy_check import policy_report
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _setup():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100),
                           char_type="Distance", nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(400, 100),
                           char_type="Distance", nominal="30"),
    ])
    chars = [
        # correct, unflagged
        Characteristic(pos=1, kind="dimension", char_type="Distance",
                       nominal="20", raw_text="20", confidence=0.99,
                       target_region=_box(100, 100)),
        # wrong, unflagged, gdt kind -> escaped; nondim_kind would flag it
        Characteristic(pos=2, kind="gdt", char_type="Flatness", nominal="0",
                       raw_text="0,05", confidence=0.99,
                       target_region=_box(400, 100)),
        # phantom with an empty read -> false detection; empty_read drops it
        Characteristic(pos=3, kind="dimension", raw_text="", confidence=0.0,
                       needs_review=True, review_reasons=["empty read"],
                       target_region=_box(800, 600)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    return {"D": dump}, {"D": gold}


def test_flag_rule_converts_an_escaped_row_and_raises_auto_accept():
    dumps, golds = _setup()
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=("nondim_kind",), drop_rules=())
    fr = r["flag_rules"]["nondim_kind"]
    assert fr["newly_flagged"] == {"escaped_error": 1}
    assert fr["delta"]["cost"] == -4.0          # 5 -> 1 on one doc
    assert fr["delta"]["auto_accept_precision"] > 0
    assert fr["better_under"] == 6
    assert fr["passes"] is True


def test_drop_rule_removes_a_false_detection_and_is_repriced():
    dumps, golds = _setup()
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=(), drop_rules=("empty_read",))
    dr = r["drop_rules"]["empty_read"]
    assert dr["dropped"] == {"false_detection": 1}
    assert dr["delta"]["cost"] == -2.0
    assert dr["passes"] is True


def test_a_flag_rule_that_only_hits_correct_rows_fails():
    """no_tolerance fires on the correct dimension row and on nothing escaped:
    +1 cost, and the unflagged set shrinks without getting more trustworthy."""
    dumps, golds = _setup()
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=("no_tolerance",), drop_rules=())
    fr = r["flag_rules"]["no_tolerance"]
    assert fr["newly_flagged"] == {"correct": 1}
    assert fr["delta"]["cost"] == 1.0
    assert fr["passes"] is False


def test_base_gate_reports_no_reflag_on_current_code_dumps():
    dumps, golds = _setup()
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=(), drop_rules=())
    assert r["base_low_conf_reflagged"] == 0


def test_joint_set_is_priced_together():
    dumps, golds = _setup()
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=("nondim_kind",), drop_rules=("empty_read",),
                      joint=(("nondim_kind",), ("empty_read",)))
    assert r["joint"]["delta"]["cost"] == -6.0
    assert r["joint"]["passes"] is True


def test_output_is_values_blind():
    dumps, golds = _setup()
    r = policy_report(dumps, golds, ["D"], ReviewCostWeights(), MatchParams(),
                      flag_rules=tuple(__import__(
                          "app.pipeline.policy_rules",
                          fromlist=["FLAG_RULES"]).FLAG_RULES),
                      drop_rules=())
    blob = json.dumps(r)
    for value in ("0,05", "Flatness", '"20"', '"30"'):
        assert value not in blob
    assert "upper_tol" not in blob and "lower_tol" not in blob
```

- [ ] **Step 2: Run to see it fail**

Run: `python -m pytest tests/eval/test_policy_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.eval.policy_check'`

- [ ] **Step 3: Implement** `app/eval/policy_check.py`

```python
"""Price a flag rule or a drop rule from dumps already on disk — no GPU.

Both are post-read decisions over a finished Characteristic, so applying the
rule to a stored dump reproduces exactly what the pipeline would have emitted
with the rule active, and re-scoring that dump prices it exactly -- matching
included, since dropping a prediction can let a neighbour pair. The rules come
from app.pipeline.policy_rules, the second deliberate pipeline import in the
eval package after reparse's `parser`: pricing a COPY of a rule would price
something the pipeline does not run.

Counts and deltas only; never a value. The keep/revert conditions are those
of docs/plans/2026-09-25-review-quality-arms-plan.md §1, and the tolerances
are experiment.py's, so there is one threshold per question."""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.eval.experiment import FIELD_ACC_TOLERANCE, RECALL_TOLERANCE
from app.eval.models import MatchParams, ReviewCostWeights
from app.eval.report import WEIGHT_GRID, recompute_cost
from app.eval.score import score_doc
from app.pipeline.policy_rules import (DROP_RULES, FLAG_RULES,
                                       apply_drop_rules, apply_flag_rules)

# The live threshold, copied for the base normalisation below. Dumps predicted
# before 2026-09-02 were flagged at 0.6; pricing a rule on top of them would
# credit it with part of the 0.6 -> 0.8 move. test_policy_check pins this to
# app.pipeline.review.LOW_CONF so the copy cannot drift.
_LOW_CONF = 0.8


def _normalise_base(dump):
    """Today's LOW_CONF on every row, so all splits share one base policy.
    Returns (dump copy, rows newly flagged). On current-code dumps the count
    must be 0 -- that is this function's gate."""
    d = dump.model_copy(deep=True)
    n = 0
    for c in d.result.characteristics:
        if (c.raw_text or "").strip() and c.confidence < _LOW_CONF \
                and not c.needs_review:
            c.needs_review = True
            c.review_reasons = [*c.review_reasons, "low OCR confidence"]
            n += 1
    return d, n


def _with_flags(dump, names):
    d = dump.model_copy(deep=True)
    for c in d.result.characteristics:
        extra = apply_flag_rules(c, names)
        if extra and not c.needs_review:
            c.needs_review = True
        c.review_reasons = [*c.review_reasons, *extra]
    return d


def _with_drops(dump, names):
    d = dump.model_copy(deep=True)
    d.result.characteristics = apply_drop_rules(d.result.characteristics,
                                                names)
    return d


def _stats(scores) -> Dict:
    counts: Dict[str, int] = {}
    n_gold = 0
    for s in scores:
        n_gold += s.n_gold
        for k, v in s.counts.items():
            counts[k] = counts.get(k, 0) + v
    matched = n_gold - counts.get("missed", 0)
    correct, escaped = counts.get("correct", 0), counts.get("escaped_error", 0)
    right = correct + counts.get("flagged_correct", 0)
    return {
        "cost": round(sum(s.review_cost for s in scores) / len(scores), 4),
        "counts": counts,
        "recall": round(matched / n_gold, 4) if n_gold else 0.0,
        "field_acc": round(right / matched, 4) if matched else 0.0,
        "escaped_rate": round(escaped / n_gold, 4) if n_gold else 0.0,
        "auto_accept_precision": (round(correct / (correct + escaped), 4)
                                  if correct + escaped else 0.0),
    }


def _delta(a: Dict, b: Dict) -> Dict:
    return {k: round(b[k] - a[k], 4) for k in
            ("cost", "recall", "field_acc", "escaped_rate",
             "auto_accept_precision")}


def _better_under(base_counts, new_counts) -> int:
    return sum(1 for w in WEIGHT_GRID
               if recompute_cost(new_counts, w) < recompute_cost(base_counts, w))


def _passes_flag(d: Dict, better: int) -> bool:
    # Flags cannot move matching, so recall and field_acc must be untouched;
    # a non-zero delta there means the offline reconstruction is broken.
    assert d["recall"] == 0 and d["field_acc"] == 0, d
    return d["cost"] < 0 and better == len(WEIGHT_GRID) \
        and d["auto_accept_precision"] > 0


def _passes_drop(d: Dict, better: int) -> bool:
    return (d["cost"] < 0 and better == len(WEIGHT_GRID)
            and d["recall"] >= -RECALL_TOLERANCE
            and d["field_acc"] >= -FIELD_ACC_TOLERANCE
            and d["escaped_rate"] <= 0
            and d["auto_accept_precision"] >= 0)


def _score_all(dumps, golds, doc_ids, weights, params):
    return [score_doc(dumps[d], golds[d], weights, params) for d in doc_ids]


def _bump(d, k):
    d[k] = d.get(k, 0) + 1


def policy_report(dumps: Dict, golds: Dict, doc_ids: Sequence[str],
                  weights: ReviewCostWeights, params: MatchParams,
                  flag_rules: Iterable[str] = tuple(FLAG_RULES),
                  drop_rules: Iterable[str] = tuple(DROP_RULES),
                  joint: Optional[Tuple[Sequence[str], Sequence[str]]] = None
                  ) -> Dict:
    base_dumps, reflagged = {}, 0
    for d in doc_ids:
        base_dumps[d], n = _normalise_base(dumps[d])
        reflagged += n
    base_scores = _score_all(base_dumps, golds, doc_ids, weights, params)
    base = _stats(base_scores)
    # pos -> base taxonomy, per doc, to say WHICH rows a rule touched.
    tax = {s.doc_id: ({p.pred_pos: p.taxonomy for p in s.pairs},
                      set(s.false_positions)) for s in base_scores}
    out = {"n_docs": len(doc_ids), "base": base,
           "base_low_conf_reflagged": reflagged,
           "flag_rules": {}, "drop_rules": {}}

    for name in flag_rules:
        new_dumps = {d: _with_flags(base_dumps[d], (name,)) for d in doc_ids}
        touched: Dict[str, int] = {}
        for d in doc_ids:
            by_pos, _ = tax[d]
            before = {c.pos: c.needs_review
                      for c in base_dumps[d].result.characteristics}
            for c in new_dumps[d].result.characteristics:
                if c.needs_review and not before[c.pos] and c.pos in by_pos:
                    _bump(touched, by_pos[c.pos])
        new = _stats(_score_all(new_dumps, golds, doc_ids, weights, params))
        delta = _delta(base, new)
        better = _better_under(base["counts"], new["counts"])
        out["flag_rules"][name] = {"delta": delta, "newly_flagged": touched,
                                   "better_under": better,
                                   "passes": _passes_flag(delta, better)}

    for name in drop_rules:
        new_dumps = {d: _with_drops(base_dumps[d], (name,)) for d in doc_ids}
        dropped: Dict[str, int] = {}
        for d in doc_ids:
            by_pos, false = tax[d]
            kept = {c.pos for c in new_dumps[d].result.characteristics}
            for c in base_dumps[d].result.characteristics:
                if c.pos in kept or c.target_region is None:
                    continue
                _bump(dropped, "false_detection" if c.pos in false
                      else by_pos.get(c.pos, "unscored"))
        new = _stats(_score_all(new_dumps, golds, doc_ids, weights, params))
        delta = _delta(base, new)
        better = _better_under(base["counts"], new["counts"])
        out["drop_rules"][name] = {"delta": delta, "dropped": dropped,
                                   "better_under": better,
                                   "passes": _passes_drop(delta, better)}

    if joint is not None:
        flags, drops = joint
        new_dumps = {d: _with_flags(_with_drops(base_dumps[d], drops), flags)
                     for d in doc_ids}
        new = _stats(_score_all(new_dumps, golds, doc_ids, weights, params))
        delta = _delta(base, new)
        better = _better_under(base["counts"], new["counts"])
        passes = _passes_drop(delta, better) and (
            not flags or delta["auto_accept_precision"] > 0)
        out["joint"] = {"flag_rules": list(flags), "drop_rules": list(drops),
                        "after": new, "delta": delta, "better_under": better,
                        "passes": passes}
    return out
```

Also append to `tests/eval/test_policy_check.py`:

```python
def test_low_conf_copy_matches_the_pipeline():
    from app.eval.policy_check import _LOW_CONF
    from app.pipeline.review import LOW_CONF
    assert _LOW_CONF == LOW_CONF
```

- [ ] **Step 4: Run to see it pass**

Run: `python -m pytest tests/eval/test_policy_check.py -v` → PASS
Run: `python -m pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add app/eval/policy_check.py tests/eval/test_policy_check.py
git commit -m "eval: price flag/drop rules exactly by re-scoring transformed dumps"
```

### Task 5: Wire `score --policy-check`

**Files:**
- Modify: `app/eval/runner.py` (`_cmd_score` after the reparse block; the `score` subparser)
- Test: `tests/eval/test_runner_policy_check.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""score --policy-check writes counts to a file an agent may read, and leaves
the scored report byte-identical: it is a diagnostic, not a scoring mode."""
import json
from pathlib import Path

from app.eval import runner
from app.eval.dump import save_dump
from tests.eval.test_policy_check import _setup


def _write(tmp_path):
    dumps, golds = _setup()
    run = tmp_path / "run"
    save_dump(dumps["D"], run)
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "D.gold.json").write_text(golds["D"].model_dump_json())
    return run, gold_dir


def _score(tmp_path, run, gold_dir, *extra):
    return runner.main(["score", "--run", str(run), "--gold", str(gold_dir),
                        "--name", "t", "--out", str(tmp_path / "r.json"),
                        *extra])


def test_policy_check_writes_counts_and_keeps_the_report(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir) == 0
    plain = (tmp_path / "r.json").read_text()
    out = tmp_path / "policy.json"
    assert _score(tmp_path, run, gold_dir, "--policy-check",
                  "--policy-out", str(out)) == 0
    assert (tmp_path / "r.json").read_text() == plain
    r = json.loads(out.read_text())
    assert set(r["flag_rules"]) and set(r["drop_rules"])


def test_joint_set_from_the_command_line(tmp_path):
    run, gold_dir = _write(tmp_path)
    out = tmp_path / "policy.json"
    assert _score(tmp_path, run, gold_dir, "--policy-check",
                  "--policy-out", str(out), "--flag-rules", "nondim_kind",
                  "--drop-rules", "empty_read") == 0
    r = json.loads(out.read_text())
    assert r["joint"]["flag_rules"] == ["nondim_kind"]
    assert r["joint"]["drop_rules"] == ["empty_read"]


def test_unknown_rule_name_is_refused(tmp_path):
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir, "--policy-check",
                  "--flag-rules", "no_such_rule") == 1
```

(`_load_gold_dir` globs `*.gold.json`, which is why the fixture is named `D.gold.json`.)

- [ ] **Step 2: Run to see it fail**

Run: `python -m pytest tests/eval/test_runner_policy_check.py -v`
Expected: FAIL — `unrecognized arguments: --policy-check`

- [ ] **Step 3: Implement**

In `main()`'s `score` subparser, after the `--reparse-check` argument:

```python
    p.add_argument("--policy-check", action="store_true",
                   help="price every flag/drop rule in policy_rules by "
                        "re-scoring transformed dumps; the report is untouched")
    p.add_argument("--policy-out", default=None,
                   help="write the --policy-check / --proposal-check JSON here "
                        "(counts only) -- the guard denies '>' redirects")
    p.add_argument("--flag-rules", default="",
                   help="comma-separated flag rules to price as a JOINT set")
    p.add_argument("--drop-rules", default="",
                   help="comma-separated drop rules to price as a JOINT set")
```

In `_cmd_score`, before `gold = _load_gold_dir(args.gold)` (fail fast, like the deck check):

```python
    joint = None
    if getattr(args, "policy_check", False):
        from app.pipeline.policy_rules import DROP_RULES, FLAG_RULES
        flags = tuple(n for n in args.flag_rules.split(",") if n)
        drops = tuple(n for n in args.drop_rules.split(",") if n)
        unknown = ([n for n in flags if n not in FLAG_RULES]
                   + [n for n in drops if n not in DROP_RULES])
        if unknown:
            print(f"ERROR: unknown rule(s) {unknown}", file=sys.stderr)
            return 1
        joint = (flags, drops) if (flags or drops) else None
```

After the `--reparse-check` block:

```python
    if getattr(args, "policy_check", False):
        from app.eval.policy_check import policy_report
        pr = policy_report(dumps, gold, doc_ids, weights, params, joint=joint)
        blob = json.dumps(pr, indent=1)
        if args.policy_out:
            Path(args.policy_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.policy_out).write_text(blob, encoding="utf-8")
        print(blob)
```

- [ ] **Step 4: Run to see it pass**

Run: `python -m pytest tests/eval/test_runner_policy_check.py -v` → PASS; `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/eval/runner.py tests/eval/test_runner_policy_check.py
git commit -m "eval: score --policy-check prices rules without touching the report"
```

### Task 6: Register the predictions BEFORE pricing (no code)

**Files:** Create `docs/plans/2026-09-25-policy-arms-prediction.md`

- [ ] **Step 1: Write the registration.** It must contain, verbatim from this plan: the keep/revert rule (§1), the select/validate/confirm table, and the candidate list (the 7 flag + 7 drop rules as registered in `policy_rules.py` at this commit's SHA). Add these registered expectations, each with the mechanism it assumes:
  - `nondim_kind`: newly flags ≈26 escaped rows on dev (26 of 64 escaped are non-dimension, 2026-09-17) against ≈5 correct (gdt reads 0.18) → PASS expected. Mechanism: kind predicts error; damage counter `newly_flagged.correct`.
  - `tall_box`: ≥80 px rows read at 0.47, which is above 0.2 error → cost falls; but precision rises only if their unflagged error share exceeds 47% — **registered as uncertain**.
  - `note_kind` / `theoretical_kind` drops: arithmetic says ≈ −3.7 / −3.9 per doc on dev. **This is NOT a re-run of the closed `score_kinds` filter** (that one also dropped gdt/surface, destroying 61 matches); the damage counter is `dropped.correct + dropped.flagged_correct`, which must stay ≤ 1 per 5 false dropped.
  - `contained_duplicate` / `repeated_value_nearby`: no prior; Task 8's `false_diagnosis.inside_matched` is the counter that must predict them.
  - `base_low_conf_reflagged` must be **0** on dev and test (current code); non-zero on train only if Task 0 found `review_low_conf` absent.
- [ ] **Step 2: Commit** (`git add` the file, then `git commit -m "plan: register the flag/drop rule predictions before pricing"`).

### Task 7: Price the flag rules (no code) → keep or revert

- [ ] **Step 1: Select split** (skip if Task 0 found it unavailable):

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-trainpredict" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split train --weights docs/eval/weights.json --name policy-train --out "$HOME/sindri-client-data/reports/policy-train.report.json" --policy-check --policy-out docs/eval/policy-train.json
```

- [ ] **Step 2: Validate split** — same command with `--run .../r3-cropctx --split dev --name policy-dev --out .../policy-dev.report.json --policy-out docs/eval/policy-dev.json`.

- [ ] **Step 3: Confirm split** — same with `--run .../r3-awqtest --split test --name policy-test --out .../policy-test.report.json --policy-out docs/eval/policy-test.json`.

- [ ] **Step 4: Check the gates first.** In each JSON: `base.cost` equals that split's headline from Task 0 (dev 131.87) and `base_low_conf_reflagged` matches the registration. If either is off, STOP — the reconstruction is wrong and no rule price means anything.

- [ ] **Step 5: Select.** The candidate flag set = rules with `passes: true` on select (or on dev under the fallback). Price the joint set on validate and confirm (Step 2/3 commands plus `--flag-rules a,b,c --policy-out docs/eval/policy-{dev,test}-joint.json`). Apply §1's joint pruning loop until `joint.passes` is true on both, or the set is empty.

- [ ] **Step 6: Record the result** in `docs/plans/2026-09-25-policy-arms-result.md`: per rule a row of (select / validate / confirm) cost delta, auto-accept delta, `newly_flagged`, `better_under`, verdict; then the joint row. Compare each against its registered expectation and say which mechanism held. Commit JSONs + result doc.

---

## Part C — Direction 2: what the false detections are, and gold-free pruning

### Task 8: `false_diagnosis` in `score_doc` and the digest

**Files:**
- Modify: `app/eval/models.py` (DocScore), `app/eval/score.py` (`score_doc`), `app/eval/report.py` (`summarize`)
- Test: `tests/eval/test_false_diagnosis.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Why each false detection is false. missed_diagnosis exists for misses;
nothing said whether 346 false detections are fragments of a match, a callout
read twice, surplus near gold, or text far from anything ballooned -- and each
routes to a different fix. The categories partition false_detection exactly."""
from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.report import aggregate, summarize
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _score():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(500, 400), nominal="7"),
    ])
    chars = [
        Characteristic(pos=1, kind="dimension", nominal="20", raw_text="20",
                       target_region=_box(100, 100)),          # matched
        Characteristic(pos=2, kind="dimension", nominal="2", raw_text="2",
                       target_region=_box(102, 100, 5, 3)),     # inside_matched
        Characteristic(pos=3, kind="note", raw_text="",
                       target_region=_box(900, 700)),           # empty_read
        Characteristic(pos=4, kind="dimension", nominal="20", raw_text="20",
                       target_region=_box(160, 100)),           # same_value
        Characteristic(pos=5, kind="theoretical", nominal="99", raw_text="99",
                       target_region=_box(1100, 50)),           # far_numeric
        Characteristic(pos=6, kind="dimension", raw_text="A-A",
                       target_region=_box(50, 800)),            # far_other
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    return score_doc(dump, gold, ReviewCostWeights(), MatchParams())


def test_each_false_detection_gets_exactly_one_category():
    s = _score()
    assert s.false_diagnosis == {"inside_matched": 1, "empty_read": 1,
                                 "same_value_as_matched": 1,
                                 "far_numeric": 1, "far_other": 1}
    assert sum(s.false_diagnosis.values()) == s.counts["false_detection"]
    assert s.false_diagnosis_by_kind["theoretical"] == {"far_numeric": 1}


def test_near_gold_splits_by_whether_that_gold_was_matched():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20")])
    chars = [
        Characteristic(pos=1, kind="dimension", nominal="20", raw_text="20",
                       target_region=_box(100, 100)),
        Characteristic(pos=2, kind="dimension", nominal="8", raw_text="8",
                       target_region=_box(100, 140)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    assert s.false_diagnosis == {"near_matched_gold": 1}


def test_digest_sums_and_reconciles():
    s = _score()
    r = aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [s])
    fd = summarize(r, lambda d: "x")["false_diagnosis"]
    assert fd["total"] == r.taxonomy["false_detection"]
    assert fd["not_measured_docs"] == 0
    assert fd["categories"]["far_numeric"] == 1
```

- [ ] **Step 2: Run to see it fail**

Run: `python -m pytest tests/eval/test_false_diagnosis.py -v`
Expected: FAIL — `AttributeError` / pydantic error on `false_diagnosis`

- [ ] **Step 3: Implement**

`app/eval/models.py`, in `DocScore` after `missed_unlocated`:

```python
    # Why each false detection is false -- the sibling of missed_diagnosis,
    # and like it a partition: the categories sum to counts["false_detection"].
    # First matching category wins, in this order:
    #   empty_read              the read produced no text at all
    #   inside_matched          >= 50% of its box inside a matched box, or the
    #                           reverse: a fragment or duplicate of a match
    #   same_value_as_matched   same nominal as a matched prediction within the
    #                           match gate: one callout read twice
    #   near_matched_gold       inside the gate of gold that paired elsewhere
    #   near_missed_gold        inside the gate of gold nothing paired with
    #   far_numeric             nothing gold nearby, but it parsed a nominal:
    #                           unballooned real dimension, table or title text
    #   far_other               nothing gold nearby, no nominal
    # None = NOT MEASURED (reports written before the field). Re-score.
    false_diagnosis: Optional[Dict[str, int]] = None
    false_diagnosis_by_kind: Optional[Dict[str, Dict[str, int]]] = None
```

`app/eval/score.py`, a helper above `score_doc`:

```python
def _frac_inside(a, b) -> float:
    """Share of box a's area that lies inside box b."""
    area = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return (w * h / area) if area > 0 and w > 0 and h > 0 else 0.0


def _false_category(p, matched_preds, dump, gold_pts, matched_gold, diag,
                    params) -> str:
    if not (p.raw_text or "").strip():
        return "empty_read"
    box = p.target_region
    for m in matched_preds:
        if (_frac_inside(box, m.target_region) >= 0.5
                or _frac_inside(m.target_region, box) >= 0.5):
            return "inside_matched"
    pc = _center_pt(p, dump)
    if p.nominal:
        for m in matched_preds:
            if (values_equal(p.nominal, m.nominal)
                    and math.dist(pc, _center_pt(m, dump)) / diag
                    <= params.max_geo_frac):
                return "same_value_as_matched"
    near = [b for b, pos in gold_pts.items()
            if math.dist(pc, pos) / diag <= params.max_geo_frac]
    if near:
        return ("near_matched_gold" if any(b in matched_gold for b in near)
                else "near_missed_gold")
    return "far_numeric" if canon_value(p.nominal) else "far_other"
```

In `score_doc`, after `pred_centers = ...`/the missed-diagnosis loop and before `n_gold, n_pred = ...`:

```python
    # Read-only over geometry already computed; it moves no pairing.
    matched_preds = [pred_by_pos[pk] for pk in matched_p]
    gold_pts = {g.balloon: gold_pos(g) for g in scored_gold
                if gold_pos(g) is not None}
    false_diag: Dict[str, int] = {}
    false_diag_kind: Dict[str, Dict[str, int]] = {}
    for pk in false:
        p = pred_by_pos[pk]
        cat = _false_category(p, matched_preds, dump, gold_pts, matched_g,
                              diag, params)
        false_diag[cat] = false_diag.get(cat, 0) + 1
        per = false_diag_kind.setdefault(_kind(p), {})
        per[cat] = per.get(cat, 0) + 1
```

and pass `false_diagnosis=false_diag, false_diagnosis_by_kind=false_diag_kind,` in the `DocScore(...)` constructor.

`app/eval/report.py`, helper above `summarize`:

```python
def _false_diagnosis_totals(report: RunReport) -> Dict:
    """Sum of DocScore.false_diagnosis. `total` must equal
    taxonomy.false_detection whenever not_measured_docs is 0."""
    cats: Dict[str, int] = {}
    by_kind: Dict[str, Dict[str, int]] = {}
    missing = 0
    for d in report.doc_scores:
        if d.false_diagnosis is None:
            missing += 1
            continue
        for k, v in d.false_diagnosis.items():
            cats[k] = cats.get(k, 0) + v
        for kind, per in (d.false_diagnosis_by_kind or {}).items():
            tgt = by_kind.setdefault(kind, {})
            for k, v in per.items():
                tgt[k] = tgt.get(k, 0) + v
    return {"categories": cats, "by_kind": by_kind,
            "total": sum(cats.values()), "not_measured_docs": missing}
```

and in `summarize`, right after the `"missed_diagnosis"` entry:

```python
        # Sibling of missed_diagnosis: why each false detection is false.
        "false_diagnosis": _false_diagnosis_totals(report),
```

- [ ] **Step 4: Run to see it pass**

Run: `python -m pytest tests/eval/test_false_diagnosis.py -v` → PASS; `python -m pytest -q` → all pass (existing score tests must be unaffected: the new fields do not change any pairing or count).

- [ ] **Step 5: Commit**

```bash
git add app/eval/models.py app/eval/score.py app/eval/report.py tests/eval/test_false_diagnosis.py
git commit -m "eval: diagnose why each false detection is false

missed_diagnosis routed three detection arms; false detections are 35% of
review cost and had no equivalent. A partition of false_detection, by kind."
```

### Task 9: Read the diagnosis, then price the drop rules (no code) → keep or revert

- [ ] **Step 1: Re-score dev and test** with the Task 0 Step 3 commands, then `summary` each to its digest. Check `false_diagnosis.total == taxonomy.false_detection` and `not_measured_docs == 0`.
- [ ] **Step 2: Write the diagnosis** into `docs/plans/2026-09-25-policy-arms-result.md` §"False detections": the category × kind table for dev and test. Check it against Task 6's registered expectations (e.g. `inside_matched` should predict `contained_duplicate`'s `dropped.false_detection`).
- [ ] **Step 3: If a large category has no gold-free rule** (e.g. `far_numeric` dominating), write a new rule into `policy_rules.py` via TDD (test in `tests/test_policy_rules.py`), **register it in the prediction doc with its expected count BEFORE pricing**, then commit. A rule added after seeing its price is not eligible for keeping on that split.
- [ ] **Step 4: Price** using the Task 7 Step 1-3 commands (the same run prices flags and drops together; re-run only if rules were added). Select, joint-price with `--flag-rules <kept flags> --drop-rules <candidates>`, prune per §1 until both splits pass.
- [ ] **Step 5: Record** the per-rule table and the final joint set in the result doc. Commit JSONs + doc.

### Task 10: Adopt the kept set into the pipeline (skip entirely if nothing was kept)

**Files:**
- Modify: `app/pipeline/policy_rules.py` (ACTIVE sets), `app/pipeline/extract.py`, `app/pipeline/review.py` (`active_review_policy`)
- Test: `tests/test_policy_rules.py` (replace `test_nothing_is_active_until_a_rule_is_kept`), `tests/test_extract_policy.py` (create)

- [ ] **Step 1: Write the failing tests**

In `tests/test_policy_rules.py`, replace the "nothing active" test with one pinning the kept set, e.g.:

```python
def test_active_sets_are_exactly_what_the_result_doc_kept():
    """docs/plans/2026-09-25-policy-arms-result.md is the authority; change
    both together or not at all."""
    assert pr.ACTIVE_FLAG_RULES == ("nondim_kind",)      # <- the kept set
    assert pr.ACTIVE_DROP_RULES == ("empty_read",)       # <- the kept set
```

(Write the actual kept names from the result doc — the two tuples above are an example of the shape only.)

`tests/test_extract_policy.py`:

```python
"""The pipeline applies the ACTIVE rules through the same functions the
counterfactual priced them with, and records them in RunConfig."""
from app.models import Characteristic
from app.pipeline import policy_rules as pr
from app.pipeline.extract import _apply_active_policy
from app.pipeline.review import active_review_policy


def test_active_policy_flags_and_drops_through_the_registry(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ("empty_read",))
    a = Characteristic(pos=0, kind="gdt", raw_text="0,05",
                       target_region=(0, 0, 10, 10))
    b = Characteristic(pos=0, kind="dimension", raw_text="",
                       target_region=(20, 0, 30, 10))
    out = _apply_active_policy([a, b])
    assert out == [a]
    assert a.needs_review and "rule:nondim_kind" in a.review_reasons


def test_run_config_records_the_active_rules(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ())
    pol = active_review_policy()
    assert pol["flag_rules"] == ["nondim_kind"]
    assert "drop_rules" not in pol


def test_no_active_rules_leaves_run_config_as_it_was(monkeypatch):
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ())
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ())
    assert active_review_policy() == {"review_low_conf": 0.8}
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_extract_policy.py tests/test_policy_rules.py -v`
Expected: FAIL — `ImportError: cannot import name '_apply_active_policy'`

- [ ] **Step 3: Implement**

`app/pipeline/policy_rules.py`: set `ACTIVE_FLAG_RULES` / `ACTIVE_DROP_RULES` to the kept tuples, each with a comment citing the result doc and its dev/test deltas.

`app/pipeline/review.py`, `active_review_policy`:

```python
    from app.pipeline import policy_rules as pr
    out = {"review_low_conf": LOW_CONF}
    # Recorded only when non-empty, so a run with no active rule keeps the
    # RunConfig every existing dump has, and a run with rules can never be
    # mistaken for one without them (the _reusable_dump failure, again).
    if pr.ACTIVE_FLAG_RULES:
        out["flag_rules"] = list(pr.ACTIVE_FLAG_RULES)
    if pr.ACTIVE_DROP_RULES:
        out["drop_rules"] = list(pr.ACTIVE_DROP_RULES)
    return out
```

`app/pipeline/extract.py`: add the import `from app.pipeline import policy_rules as pr` and, above `def extract`:

```python
def _apply_active_policy(results):
    """The kept flag and drop rules, through the SAME functions
    app/eval/policy_check.py priced them with -- so the offline price and the
    shipped behaviour cannot drift apart. Flags first on every row, then the
    drop judged on the original list (drop rules are order-independent)."""
    for c in results:
        extra = pr.apply_flag_rules(c, pr.ACTIVE_FLAG_RULES)
        if extra:
            c.needs_review = True
            c.review_reasons = [*c.review_reasons, *extra]
    return pr.apply_drop_rules(results, pr.ACTIVE_DROP_RULES)
```

and in `extract()`, directly before `emit("place", "Placing balloons")`:

```python
    results = _apply_active_policy(results)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/policy_rules.py app/pipeline/review.py app/pipeline/extract.py tests/test_policy_rules.py tests/test_extract_policy.py
git commit -m "feat(policy): ship the flag/drop rules that passed select, validate and confirm"
```

The body must list each kept rule with its dev and test deltas, and name the rejected ones.

### Task 11: `score --reapply-policy` — the derived shipped number (skip if nothing was kept)

**Files:**
- Modify: `app/eval/models.py` (`RunReport.reapplied_policy`), `app/eval/runner.py`
- Test: `tests/eval/test_runner_policy_check.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_reapply_policy_scores_with_the_active_rules_and_says_so(
        tmp_path, monkeypatch):
    from app.pipeline import policy_rules as pr
    monkeypatch.setattr(pr, "ACTIVE_FLAG_RULES", ("nondim_kind",))
    monkeypatch.setattr(pr, "ACTIVE_DROP_RULES", ("empty_read",))
    run, gold_dir = _write(tmp_path)
    assert _score(tmp_path, run, gold_dir, "--reapply-policy") == 0
    r = json.loads((tmp_path / "r.json").read_text())
    assert r["reapplied_policy"] == {"flag_rules": ["nondim_kind"],
                                     "drop_rules": ["empty_read"],
                                     "reparsed": True}
    assert r["taxonomy"].get("false_detection", 0) == 0   # empty read dropped
    assert r["taxonomy"].get("escaped_error", 0) == 0     # gdt row flagged
```

- [ ] **Step 2: Run to see it fail** — `unrecognized arguments: --reapply-policy`.

- [ ] **Step 3: Implement**

`app/eval/models.py`, `RunReport` after `missing_dumps`:

```python
    # Set when `score --reapply-policy` re-parsed raw_text with today's parser
    # and applied today's ACTIVE flag/drop rules to the dumps before scoring:
    # the number is DERIVED from older dumps, exactly (both are post-read), and
    # must be quoted as derived until a predict run on current code confirms
    # it. None = scored as predicted, which is every report before this field.
    reapplied_policy: Optional[Dict] = None
```

`app/eval/runner.py`: add `p.add_argument("--reapply-policy", action="store_true", help=...)` to `score`; in `_cmd_score`, right after `dumps = {...}` is built:

```python
    reapplied = None
    if getattr(args, "reapply_policy", False):
        # Re-parse first (the Ø-zone default and any later parser fix live in
        # parse_value), then the active rules -- the order extract() runs them.
        from app.eval.reparse import _HINTS
        from app.pipeline import policy_rules as pr
        from app.pipeline.extract import _apply_active_policy
        from app.pipeline.parser import parse_value
        for doc_id, dump in dumps.items():
            fresh = []
            for c in dump.result.characteristics:
                p = parse_value(c.raw_text or "",
                                hint=_HINTS.get(c.kind or "", ""))
                c.char_type, c.nominal = p.char_type, p.nominal
                c.upper_tol, c.lower_tol = p.upper_tol, p.lower_tol
                fresh.append(c)
            dump.result.characteristics = _apply_active_policy(fresh)
        reapplied = {"flag_rules": list(pr.ACTIVE_FLAG_RULES),
                     "drop_rules": list(pr.ACTIVE_DROP_RULES),
                     "reparsed": True}
```

Check `extract.py` for any post-parse mutation of `char_type`/`nominal` between `parse_value` and `review_flags` (the `note_ref` relabel sets hint/subtype/kind BEFORE parsing, so the stored `kind` already carries it). If there is any other, replicate it here and add a test. Then pass `reapplied` into the report: after `report = aggregate(...)`, set `report.reapplied_policy = reapplied`.

**Gate:** with `ACTIVE_*` empty, `--reapply-policy` on `r3-cropctx` must give **131.20** (the Ø-zone derived number, CLAUDE.md §2). If not, the re-parse is not reproducing `reparse.py` and nothing derived from it is quotable.

- [ ] **Step 4: Run tests** → all pass. Run the gate on dev (Task 0 Step 3 command with `--reapply-policy --name reapply-dev --out .../reapply-dev.report.json`), then again after the ACTIVE sets are filled; record both.

- [ ] **Step 5: Commit** with the derived dev and test headlines in the body.

---

## Part D — Direction 4: OCR proposals for isolated misses

### Task 12: Proposal generator (pure parts first)

**Files:**
- Create: `app/pipeline/proposals.py`
- Modify: `app/pipeline/detect.py` (`Detection.source`)
- Test: `tests/test_proposals.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""OCR line proposals: text regions the VLM detector did not return. The pure
parts are tested without OCR quality in the loop -- rotation mapping and the
line filter -- because tesseract on a synthetic image would test tesseract."""
import pytest

from app.pipeline import proposals as pp


def test_unrotate_maps_a_box_from_the_90_degree_image_back():
    # Original 200x100. PIL rotate(90, expand=True) is counter-clockwise, so
    # original (x, y) -> rotated (y, W - x). A box at x 10..30, y 40..50 in the
    # original sits at x 40..50, y 170..190 in the rotated image.
    assert pp._unrotate_box((40, 170, 50, 190), orig_w=200) == (10, 40, 30, 50)


def _tess(lines):
    """A minimal image_to_data dict: one word per entry."""
    keys = ("text", "conf", "left", "top", "width", "height",
            "block_num", "par_num", "line_num")
    d = {k: [] for k in keys}
    for (text, conf, box, line) in lines:
        d["text"].append(text); d["conf"].append(conf)
        d["left"].append(box[0]); d["top"].append(box[1])
        d["width"].append(box[2] - box[0]); d["height"].append(box[3] - box[1])
        d["block_num"].append(1); d["par_num"].append(1)
        d["line_num"].append(line)
    return d


def test_lines_group_words_and_keep_only_dimension_shaped_text():
    data = _tess([
        ("Ø20", 90, (10, 10, 50, 30), 1), ("±0,1", 85, (55, 10, 90, 30), 1),
        ("SCALE", 90, (10, 60, 60, 80), 2),                       # no digit
        ("1234567890", 90, (10, 100, 150, 120), 3),               # part no.
        ("7", 10, (10, 140, 20, 160), 4),                          # low conf
    ])
    lines = pp._dimension_lines(data)
    assert lines == [((10, 10, 90, 30), "Ø20 ±0,1")]


def test_proposals_overlapping_an_existing_detection_are_dropped():
    cand = [(10, 10, 90, 30), (300, 300, 340, 320)]
    existing = [(80, 5, 200, 40)]
    assert pp._not_covered(cand, existing, margin=8) == [(300, 300, 340, 320)]


def test_resolve_proposals_rejects_a_typo(monkeypatch):
    assert pp.resolve_proposals({}) is None
    assert pp.resolve_proposals({"SINDRI_PROPOSALS": "ocr"}) == "ocr"
    with pytest.raises(ValueError):
        pp.resolve_proposals({"SINDRI_PROPOSALS": "orc"})


def test_detection_source_defaults_to_the_vlm():
    from app.pipeline.detect import Detection
    assert Detection(box=(0, 0, 1, 1), kind="dimension", conf=1.0).source == "vlm"
```

- [ ] **Step 2: Run to see it fail** — `ModuleNotFoundError: app.pipeline.proposals`.

- [ ] **Step 3: Implement**

`app/pipeline/detect.py`, `Detection` dataclass, last field:

```python
    source: str = "vlm"         # "vlm" | "proposal" (OCR, VLM-verified)
```

`app/pipeline/proposals.py`:

```python
"""OCR line proposals for callouts the VLM detector never returned.

58 of 88 dev misses are ISOLATED -- nothing was detected anywhere near the
gold balloon -- and in eight arms only the detector's WEIGHTS ever moved them
(Rung 3, 75 -> 43, at +324 false detections). This is the other route: a
cheap, independent text detector proposes dimension-shaped lines the VLM did
not box, the VLM is asked only "is this one callout?", and the survivors enter
the normal read loop FLAGGED, so a recovered row costs 1 instead of a miss's
10 and a wrong proposal costs a false detection's 2. Break-even precision is
therefore 2 / (2 + 9) = 18%.

Off unless SINDRI_PROPOSALS=ocr; recorded in detect.active_knobs, and the
verify prompt joins the prompt hash only when on, so a control run's
RunConfig is byte-identical to every dump taken before this module existed."""
import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_MODES = {"ocr"}
PROPOSAL_REASON = "ocr proposal"
_DIGIT = re.compile(r"\d")
_MIN_CONF = 50.0          # tesseract word confidence, 0-100
_MAX_CHARS = 24           # a callout, not a sentence
_MAX_DIGIT_RUN = 6        # longer runs are part / drawing numbers
_MIN_H, _MAX_H = 12, 120  # render px; outside this it is not a callout line
_TESS_CONFIG = "--psm 11"  # sparse text: find text anywhere, no layout


def resolve_proposals(env: Optional[Dict] = None) -> Optional[str]:
    """None when off. A value outside _MODES RAISES: a typo must lose the arm,
    not run a control under the arm's name."""
    v = (os.environ if env is None else env).get("SINDRI_PROPOSALS", "")
    v = v.strip().lower()
    if not v:
        return None
    if v not in _MODES:
        raise ValueError(f"SINDRI_PROPOSALS={v!r} (expected one of {_MODES})")
    return v


def _unrotate_box(box, orig_w: int) -> Tuple[int, int, int, int]:
    """Box in the rotate(90, expand=True) image -> box in the original.
    Forward: (x, y) -> (y, W - x); so rotated (xr, yr) -> (W - yr, xr)."""
    xr0, yr0, xr1, yr1 = box
    return (orig_w - yr1, xr0, orig_w - yr0, xr1)


def _dimension_lines(data) -> List[Tuple[Tuple[int, int, int, int], str]]:
    """Group tesseract words into lines; keep dimension-shaped ones."""
    lines: Dict[Tuple[int, int, int], List[int]] = {}
    for i, text in enumerate(data["text"]):
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if not str(text).strip() or conf < _MIN_CONF:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(i)
    out = []
    for key in sorted(lines):
        idx = lines[key]
        text = " ".join(str(data["text"][i]).strip() for i in idx)
        if not _DIGIT.search(text) or len(text) > _MAX_CHARS:
            continue
        if max(len(m) for m in re.findall(r"\d+", text)) > _MAX_DIGIT_RUN:
            continue
        x0 = min(data["left"][i] for i in idx)
        y0 = min(data["top"][i] for i in idx)
        x1 = max(data["left"][i] + data["width"][i] for i in idx)
        y1 = max(data["top"][i] + data["height"][i] for i in idx)
        # The SHORT side is the text line height whatever the orientation.
        h = min(y1 - y0, x1 - x0)
        if not (_MIN_H <= h <= _MAX_H):
            continue
        out.append(((x0, y0, x1, y1), text))
    return out


def _not_covered(cands, existing, margin: int = 8):
    """Candidates that touch no existing detection box grown by `margin`."""
    def hit(a, b):
        return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                    or a[3] < b[1] - margin or a[1] > b[3] + margin)
    return [c for c in cands if not any(hit(c, e) for e in existing)]


def ocr_proposals(image, existing: Sequence[tuple]) -> List[tuple]:
    """Dimension-shaped OCR lines, horizontal and vertical, not already
    covered by a detection. Boxes in the image's pixel space."""
    import pytesseract
    from app.pipeline.detect import Detection, dedupe
    w, _ = image.size
    boxes = []
    for rotated in (False, True):
        im = image.rotate(90, expand=True) if rotated else image
        data = pytesseract.image_to_data(
            im, lang="deu+eng", config=_TESS_CONFIG,
            output_type=pytesseract.Output.DICT)
        for box, _text in _dimension_lines(data):
            boxes.append(_unrotate_box(box, w) if rotated else box)
    # The two passes can find the same line; dedupe as detections do.
    uniq = dedupe([Detection(box=b, kind="dimension", conf=0.0)
                   for b in boxes])
    return _not_covered([d.box for d in uniq], existing)


def verified_proposals(image, detections, backend,
                       crop_fn: Callable) -> list:
    """Proposals the VLM confirms as ONE callout, as Detections tagged
    source="proposal". Refuses a backend without verify_callout rather than
    skipping: a silent skip would run a control under the arm's name."""
    from app.pipeline.detect import Detection
    if not hasattr(backend, "verify_callout"):
        raise RuntimeError("SINDRI_PROPOSALS needs a backend with "
                           "verify_callout (the transformers VLM path)")
    out = []
    for box in ocr_proposals(image, [d.box for d in detections]):
        if backend.verify_callout(crop_fn(box)):
            out.append(Detection(box=box, kind="dimension", conf=0.0,
                                 source="proposal"))
    return out
```

`detect.dedupe(detections, iou_thresh=0.5)` is greedy NMS within a kind, sorted by `conf`; every proposal has `conf=0.0` and kind `dimension`, so it keeps the first of any overlapping pair.

- [ ] **Step 4: Run to see it pass** — `python -m pytest tests/test_proposals.py -v` → PASS; full suite passes.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/proposals.py app/pipeline/detect.py tests/test_proposals.py
git commit -m "feat(proposals): OCR dimension-line proposals, off by default"
```

### Task 13: CPU feasibility check `score --proposal-check`

**Files:**
- Create: `app/eval/proposal_check.py`
- Modify: `app/eval/runner.py`
- Test: `tests/eval/test_proposal_check.py` (create)

- [ ] **Step 1: Write the failing test** (the geometry/counting core, with proposals injected so no OCR runs)

```python
"""Before any GPU arm: do OCR proposals even land on the isolated misses?
The counting core is tested with proposals injected; the identity gate --
the isolated set reproduces DocScore.missed_isolated exactly -- is what makes
the coverage number trustworthy."""
import pytest

from app.eval.models import (GoldCharacteristic, GoldDoc, MatchParams,
                             PredictionDump, ReviewCostWeights, RunConfig)
from app.eval.proposal_check import count_doc
from app.eval.score import score_doc
from app.models import Characteristic, ExtractionResult

SCALE = 300 / 72.0
RECT = (0.0, 0.0, 1191.0, 842.0)


def _box(x, y, w=15, h=5):
    return (SCALE * (x - w), SCALE * (y - h), SCALE * (x + w), SCALE * (y + h))


def _case():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20"),
        GoldCharacteristic(balloon=2, position_pt=(700, 500), nominal="7"),
        GoldCharacteristic(balloon=3, position_pt=(1000, 100), nominal="9"),
    ])
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=[
        Characteristic(pos=1, nominal="20", raw_text="20",
                       target_region=_box(100, 100))]))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    return dump, gold, s


def test_coverage_counts_isolated_misses_with_a_proposal_in_the_gate():
    dump, gold, s = _case()
    assert s.missed_isolated == 2
    props = [_box(705, 500), _box(300, 800)]   # near gold 2; far from all
    c = count_doc(dump, gold, s, props, MatchParams())
    assert c == {"isolated": 2, "isolated_covered": 1, "proposals": 2,
                 "near_unmatched_gold": 1, "near_matched_gold": 0,
                 "far_from_gold": 1}


def test_identity_gate_refuses_a_diverging_isolated_set():
    dump, gold, s = _case()
    s.missed_isolated = 1          # simulate a drifted predicate
    with pytest.raises(AssertionError):
        count_doc(dump, gold, s, [], MatchParams())
```

- [ ] **Step 2: Run to see it fail** — `ModuleNotFoundError: app.eval.proposal_check`.

- [ ] **Step 3: Implement** `app/eval/proposal_check.py`

```python
"""CPU feasibility count for OCR proposals, before any GPU is spent.

For each document: render the ORIGINAL at the dump's scale, mask the CV-found
legend and title block the way extract() does (the VLM notes locator is
skipped -- so `far_from_gold` is an upper bound), run proposals.ocr_proposals
against the dump's own boxes, and count how many ISOLATED misses get a
proposal inside the match gate. Counts only.

Registered go/no-go (docs/plans/2026-09-25-review-quality-arms-plan.md,
Task 14): the GPU arm runs only if isolated_covered >= 12 on dev."""
import math
import tempfile
from pathlib import Path
from typing import Dict, List

from app.eval.dump import to_points
from app.eval.models import MatchParams


def _center(box_px, dump):
    b = to_points(box_px, dump.scale, dump.page_rect)
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def count_doc(dump, gold, score, proposals_px: List[tuple],
              params: MatchParams) -> Dict[str, int]:
    """Counting core, geometry as score_doc with reconcile_frames='none'."""
    assert params.reconcile_frames == "none", "only the default frame mode"
    diag = math.dist(gold.page_rect[:2], gold.page_rect[2:])
    scored = [g for g in gold.characteristics
              if getattr(g, "kind", "dimension") in params.score_kinds]
    pred_c = [_center(c.target_region, dump)
              for c in dump.result.characteristics if c.target_region]
    matched = {p.gold_balloon for p in score.pairs}
    missed = set(score.missed_balloons)
    isolated = [g for g in scored if g.balloon in missed
                and g.position_pt is not None
                and not any(math.dist(g.position_pt, pc) / diag
                            <= params.max_geo_frac for pc in pred_c)]
    # Identity gate: the same predicate score_doc uses, so the same count.
    assert len(isolated) == score.missed_isolated, \
        (len(isolated), score.missed_isolated)
    prop_c = [_center(b, dump) for b in proposals_px]

    def near(pos, pts):
        return any(math.dist(pos, p) / diag <= params.max_geo_frac
                   for p in pts)

    out = {"isolated": len(isolated),
           "isolated_covered": sum(near(g.position_pt, prop_c)
                                   for g in isolated),
           "proposals": len(prop_c), "near_unmatched_gold": 0,
           "near_matched_gold": 0, "far_from_gold": 0}
    located = [g for g in scored if g.position_pt is not None]
    for pc in prop_c:
        hits = [g for g in located
                if math.dist(pc, g.position_pt) / diag <= params.max_geo_frac]
        if not hits:
            out["far_from_gold"] += 1
        elif any(g.balloon not in matched for g in hits):
            out["near_unmatched_gold"] += 1
        else:
            out["near_matched_gold"] += 1
    return out


def proposal_check(dumps, golds, scores, pdf_dir, params) -> Dict:
    from PIL import Image
    from app.pipeline import marks_block as mb, title_block as tb
    from app.pipeline.proposals import ocr_proposals
    from app.pipeline.render import render_page
    total: Dict[str, int] = {"docs": 0, "scale_mismatch": 0}
    with tempfile.TemporaryDirectory() as tmp:
        for s in scores:
            dump, gold = dumps[s.doc_id], golds[s.doc_id]
            r = render_page(Path(pdf_dir) / f"{s.doc_id}.pdf",
                            dpi=dump.config.dpi, out_dir=Path(tmp) / s.doc_id)
            if abs(r.scale - dump.scale) > 1e-6:
                total["scale_mismatch"] += 1
                continue
            image = Image.open(r.png_path).convert("RGB")
            masked = image
            legend = mb.locate_marks_block(image)
            if legend is not None:
                masked = mb.mask_region(masked, legend)
            tbr = tb.locate_title_block(image)
            if tbr is not None:
                masked = tb.mask_region(masked, tbr)
            existing = [c.target_region for c in dump.result.characteristics
                        if c.target_region]
            c = count_doc(dump, gold, s, ocr_proposals(masked, existing),
                          params)
            total["docs"] += 1
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
    # Unverified bound: every covered miss recovered and flagged (10 -> 1),
    # every far proposal kept as a false detection (+2). The VLM verifier's
    # job is to beat this by rejecting far ones; it cannot raise the first term.
    total["unverified_net_units"] = (9 * total.get("isolated_covered", 0)
                                     - 2 * total.get("far_from_gold", 0))
    return total
```

`app/eval/runner.py`: add `p.add_argument("--proposal-check", action="store_true", help="CPU: count OCR proposals landing on isolated misses (needs --pdfs)")` to `score`; in `_cmd_score`, fail fast next to the deck check if `--proposal-check` is set without `--pdfs`; after the policy-check block:

```python
    if getattr(args, "proposal_check", False):
        from app.eval.proposal_check import proposal_check
        pc = proposal_check(dumps, gold, scores, args.pdfs, params)
        blob = json.dumps(pc, indent=1)
        if args.policy_out:
            Path(args.policy_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.policy_out).write_text(blob, encoding="utf-8")
        print(blob)
```

(`RenderResult` carries `png_path`, `width`, `height`, `scale`, `page_rect`.)

- [ ] **Step 4: Run tests** → PASS / full suite passes.

- [ ] **Step 5: Commit**

```bash
git add app/eval/proposal_check.py app/eval/runner.py tests/eval/test_proposal_check.py
git commit -m "eval: score --proposal-check, the CPU go/no-go for OCR proposals"
```

### Task 14: Run the feasibility gate (no code) → go / no-go

- [ ] **Step 1: Register** in `docs/plans/2026-09-25-policy-arms-prediction.md` (append, commit BEFORE running): GO iff `isolated_covered >= 12` of dev's 58 (ceiling −7.2 per doc before false detections). Also record the expected `far_from_gold` order of magnitude you believe (hundreds) — the verifier must remove ≥ (2·far − 9·covered)/2 of them to break even.
- [ ] **Step 2: Run** (≈15-30 min CPU):

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-cropctx" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name proposal-probe --out "$HOME/sindri-client-data/reports/proposal-probe.report.json" --proposal-check --policy-out docs/eval/proposal-check-dev.json
```

- [ ] **Step 3: Decide.** Gate: `scale_mismatch == 0` (else the geometry is wrong — fix before reading coverage), `isolated == 58`. If `isolated_covered < 12`: **NO-GO** — `git revert` both the Task 12 and Task 13 commits (`proposal_check.py` imports `proposals`, so it cannot outlive it), write the result into the result doc and CLAUDE.md §3 with the numbers, and stop Part D. If GO: continue.
- [ ] **Step 4: Commit** the JSON and the decision paragraph.

### Task 15: VLM verification method (GO only)

**Files:**
- Modify: `app/pipeline/ocr/vlm_backend.py`
- Test: `tests/test_vlm_prompt.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_verify_prompt_joins_the_hash_only_when_proposals_are_on():
    """A control run's prompt hash must stay byte-identical to every dump on
    disk; an arm with proposals must hash differently."""
    from app.pipeline.ocr import vlm_backend as vb
    off = vb.effective_prompts({})
    on = vb.effective_prompts({"SINDRI_PROPOSALS": "ocr"})
    assert vb._VERIFY_PROMPT not in off
    assert on == off + [vb._VERIFY_PROMPT]


def test_verify_callout_reads_a_yes(monkeypatch):
    from app.pipeline.ocr import vlm_backend as vb
    be = vb.VLMBackend.__new__(vb.VLMBackend)
    monkeypatch.setattr(be, "_generate_text",
                        lambda prompt, image, n: ("Yes.", 0.9), raising=False)
    assert be.verify_callout(object()) is True
    monkeypatch.setattr(be, "_generate_text",
                        lambda prompt, image, n: ("no", 0.9), raising=False)
    assert be.verify_callout(object()) is False
```

(`effective_prompts(env=None)` already takes an env mapping, and the class is `VLMBackend`.)

- [ ] **Step 2: Run to see it fail** — `AttributeError: _VERIFY_PROMPT`.

- [ ] **Step 3: Implement** in `vlm_backend.py`, next to the other prompts:

```python
# Proposal verification (app/pipeline/proposals.py). A yes/no question, not a
# read: the crop then goes through the ordinary read prompt like any detection.
# It names the look-alikes the OCR proposer is known to pick up, because an
# unnamed trap is the one a model walks into (gdt review, 2026-09-25).
_VERIFY_PROMPT = (
    "This crop is from a mechanical engineering drawing. Answer with one "
    "word, yes or no: does it show exactly one dimension callout -- a "
    "measured value such as a length, diameter, radius or angle, optionally "
    "with a tolerance -- that an inspector would measure? Answer no for "
    "title-block or table text, part or drawing numbers, scale, revision, "
    "sheet numbers, note text, section or view labels, and grid references."
)
```

In `effective_prompts(env=None)`, after building the list:

```python
    from app.pipeline.proposals import resolve_proposals
    if resolve_proposals(env):
        prompts.append(_VERIFY_PROMPT)
```

(adapt to the function's actual local name for the list). Add the method next to `read_region_gdt`:

```python
    def verify_callout(self, image: Image.Image) -> bool:
        """Is this OCR proposal one inspectable callout? Greedy, 4 tokens."""
        text, _conf = self._generate_text(_VERIFY_PROMPT, image, 4)
        return (text or "").strip().lower().startswith("yes")
```

The detection-scope rule applies: if an adapter is ever active, verification is a DETECTION decision and must run inside `self._base_weights()` like `detect_regions` does — wrap it the same way if `detect_regions` is wrapped.

- [ ] **Step 4: Run tests** → PASS; `python3 -c "from app.eval.runner import _prompt_sha256; print(_prompt_sha256())"` must still print **`aa7659f1929184ea`** with `SINDRI_PROPOSALS` unset.

- [ ] **Step 5: Commit** (`feat(vlm): verify_callout for OCR proposals; hash unchanged when off`).

### Task 16: Wire proposals into `extract` and the knobs (GO only)

**Files:**
- Modify: `app/pipeline/extract.py`, `app/pipeline/detect.py` (`active_knobs`)
- Test: `tests/test_extract_proposals.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Proposals enter the ordinary read loop and are always FLAGGED: a recovered
row then costs 1 not 10, and the reviewer is told these came from OCR."""
from app.pipeline import detect
from app.pipeline.extract import _mark_proposal


def test_a_proposal_row_is_flagged_with_its_reason():
    from app.models import Characteristic
    c = Characteristic(pos=0, raw_text="20", confidence=0.99)
    _mark_proposal(c)
    assert c.needs_review
    assert "ocr proposal" in c.review_reasons


def test_active_knobs_record_proposals_only_when_on():
    assert "proposals" not in detect.active_knobs({})
    assert detect.active_knobs({"SINDRI_PROPOSALS": "ocr"})["proposals"] == "ocr"
```

- [ ] **Step 2: Run to see it fail.**

- [ ] **Step 3: Implement**

`detect.py` `active_knobs`: after building the dict,

```python
    from app.pipeline.proposals import resolve_proposals
    mode = resolve_proposals(env)
    if mode:
        # Only when on: a control keeps the RunConfig of every existing dump.
        out["proposals"] = mode
```

(restructure the `return {...}` into `out = {...}; ...; return out`).

`extract.py`: import `from app.pipeline import proposals as pp`; above `def extract`:

```python
def _mark_proposal(c):
    """Every proposal row is flagged: break-even precision for the arm is 18%
    only because a recovered row costs a flag (1), not an unflagged read."""
    c.needs_review = True
    if pp.PROPOSAL_REASON not in c.review_reasons:
        c.review_reasons = [*c.review_reasons, pp.PROPOSAL_REASON]
```

In `extract()`, directly after `detections = detect_characteristics(image_for_detect, backend)`:

```python
    if pp.resolve_proposals():
        emit("detect", "Verifying OCR proposals")
        detections = detections + pp.verified_proposals(
            image_for_detect, detections, backend,
            crop_fn=lambda box: _prep_crop(
                image, _clamp(box, render.width, render.height),
                render.width, render.height,
                pad=crop_pad_for(box[3] - box[1])))
```

and in the read loop, after `c.needs_review, c.review_reasons = review_flags(...)`:

```python
        if d.source == "proposal":
            _mark_proposal(c)
```

(Place `_apply_active_policy` from Task 10, if present, AFTER this so flag rules see the final row.)

- [ ] **Step 4: Run tests** → full suite passes.

- [ ] **Step 5: Commit** (`feat(proposals): verified OCR proposals enter the read loop, flagged`).

### Task 17: Count proposal rows in scoring (GO only)

**Files:** `app/eval/models.py`, `app/eval/score.py`, `app/eval/report.py`; test `tests/eval/test_false_diagnosis.py` (append)

- [ ] **Step 1: Failing test** (append):

```python
def test_proposal_rows_are_counted_matched_and_false():
    gold = GoldDoc(doc_id="D", pdf="d", excel="d", page_rect=RECT,
                   characteristics=[
        GoldCharacteristic(balloon=1, position_pt=(100, 100), nominal="20")])
    chars = [
        Characteristic(pos=1, nominal="20", raw_text="20", needs_review=True,
                       review_reasons=["ocr proposal"],
                       target_region=_box(100, 100)),
        Characteristic(pos=2, nominal="5", raw_text="5", needs_review=True,
                       review_reasons=["ocr proposal"],
                       target_region=_box(900, 700)),
    ]
    dump = PredictionDump(doc_id="D", config=RunConfig(model_id="s", dpi=300),
                          scale=SCALE, page_rect=RECT,
                          result=ExtractionResult(characteristics=chars))
    s = score_doc(dump, gold, ReviewCostWeights(), MatchParams())
    assert (s.proposal_matched, s.proposal_false) == (1, 1)
    r = aggregate("r", RunConfig(), ReviewCostWeights(), MatchParams(), [s])
    assert summarize(r, lambda d: "x")["proposal_rows"] == {
        "matched": 1, "false": 1, "not_measured_docs": 0}
```

- [ ] **Step 2: Run to see it fail.**
- [ ] **Step 3: Implement.** `DocScore`: `proposal_matched: Optional[int] = None` and `proposal_false: Optional[int] = None` (None = report predates the field; a re-scored old dump honestly gives 0 because the reason string did not exist). In `score_doc`, `_PROPOSAL_REASON = "ocr proposal"` as a module constant (a copy, with a test pinning it to `proposals.PROPOSAL_REASON` — append `assert score._PROPOSAL_REASON == PROPOSAL_REASON` as its own test), then count `sum(1 for pk in matched_p if _PROPOSAL_REASON in pred_by_pos[pk].review_reasons)` and the same over `false`. In `summarize`, a `"proposal_rows"` key summing both with a `not_measured_docs` count, like `_false_diagnosis_totals`.
- [ ] **Step 4: Run tests** → pass.
- [ ] **Step 5: Commit.**

### Task 18: The GPU queue stages and the run (GO only for `r4proposals`; `r4control` runs regardless)

**Files:** Modify `run_gpu_queue.sh`; test `tests/test_gpu_queue.py` (follow the existing per-stage assertions)

- [ ] **Step 1: Failing test.** Read `tests/test_gpu_queue.py` and add, in its style, that `r4control` and `r4proposals` appear in the usage line, map to runs `r4-control` / `r4-proposals`, split `dev`, and that only `r4proposals`'s env carries `SINDRI_PROPOSALS=ocr`.
- [ ] **Step 2: Run to see it fail.**
- [ ] **Step 3: Implement** in `run_gpu_queue.sh`: add both names to the usage echo; `stage_run`: `r4control) echo "r4-control" ;;` and `r4proposals) echo "r4-proposals" ;;`; `stage_env`: `r4proposals) echo "-e VLM_MODEL_ID=$MODEL -e SINDRI_PROPOSALS=ocr" ;;` (`r4control` falls through to the default); `stage_why`:
  - `r4control`: "CURRENT CODE, no new knob. Confirms natively what Parts A-C derived: the Ø-zone parse and the kept flag/drop rules. PREDICTION: its scoped dev numbers equal `score --reapply-policy` on r3-cropctx TO THE DECIMAL (greedy decoding; both are post-read). If not, the derivation is wrong and no derived number is quotable. Also the control for r4proposals."
  - `r4proposals`: "OCR proposals, VLM-verified, flagged. Only variable vs r4-control: SINDRI_PROPOSALS=ocr. TARGET: missed_diagnosis.isolated falls from r4-control's value; proposal_rows.matched*9 > proposal_rows.false*2. IDENTITY GATE: every non-proposal row is bit-identical to r4-control (proposals are appended after detection and read independently) -- check n_pred - proposal rows == r4-control n_pred. Judge with experiment.py incl. the auto-accept guard."
- [ ] **Step 4: Tests pass. Commit.**
- [ ] **Step 5: Deploy and run** per CLAUDE.md §5 (never while a queue runs): push the branch to the host's `from-operator`, `git checkout --detach from-operator` there, rebuild `sindri-gpu-nf4` as for `r3-cropctx` (2026-09-17 handoff §1), check which card is free, then on the host: `tmux new -d -s r4 '~/sindri/run_gpu_queue.sh <free-gpu> r4control r4proposals'` (just `r4control` on NO-GO). ~4-5 h per stage.
- [ ] **Step 6: Pull dumps, score, summarise, compare** (operator pulls; agent runs each as its own command): `rescore_onepage.sh`-style score for `r4-control` and `r4-proposals` on dev, `summary` → `docs/eval/r4control-scoped-summary.json` / `r4proposals-scoped-summary.json`, `compare` → `docs/eval/r4proposals-scoped-vs-r4control-scoped.json`, then `python3 -m app.eval.experiment`.
- [ ] **Step 7: Decide** per §1. First the r4-control prediction (exact match with the derived number). Then the r4proposals verdict + isolated delta + identity gate. **Not kept → revert Tasks 12, 13, 15, 16, 17's proposal code in one commit naming the numbers**; add a CLAUDE.md §3 entry.

### Task 19: Confirm a dev win on test (conditional: only if r4proposals won on dev)

- [ ] **Step 1:** `stage_split` maps only `awqtest` to test and its comment says that is deliberate; `tests/test_gpu_queue.py` may pin it. Add `r4controltest` / `r4proposalstest` (split `test`, envs as their dev twins) and update that test and comment with the reason: "a dev win must be confirmed on templates dev never saw (dev was optimistic by +31)". TDD as in Task 18.
- [ ] **Step 2:** Run both on the host, score with `SPLIT=test`, compare. Kept only if the test delta has the same sign on cost and isolated misses and passes `experiment.verdict`. Otherwise revert as in Task 18 Step 7.

---

## Part E — Close out

### Task 20: Handoff and CLAUDE.md

- [ ] Update CLAUDE.md §2 with what was kept (derived vs measured, which split), §3 with every rule/arm that was not kept and its numbers, and §4 with the new convention: **a policy change must raise auto-accept precision, not only lower cost.** Write `docs/plans/<date>-session-handoff.md` superseding 2026-09-25's, with the re-derived cost table (§1 of the old handoff) under whatever shipped.
- [ ] Verify: `python -m pytest -q` (all pass), `bash ~/.claude/hooks/test-sindri-guard.sh` (`32 passed, 0 failed`), `python3 -m app.eval.experiment`.
- [ ] Commit and push to PR #2.

---

## Self-review notes (for the executor)

* **Exactness claims rest on two invariants** tested in Task 3 (additive flags, order-independent drops) and one gate each in Tasks 7 (`base.cost` = headline), 11 (131.20 reproduced) and 18 (r4-control = derived). If any gate fails, stop — do not reason around it.
* **Selection bias:** rules added after seeing a split's prices are not eligible on that split (Task 9 Step 3). The test split is used for confirmation only.
* **Weights:** every pass condition requires 6/6 `WEIGHT_GRID` vectors, so the decisions survive whatever `weights.json` the client returns.
* **Order:** Parts A–C and Task 14 are CPU-only and come first. The one GPU queue (Task 18) runs after them, so `r4-control` confirms Parts A–C and the Ø-zone fix natively in the same run that controls the proposal arm.
