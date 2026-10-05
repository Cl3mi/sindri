# Read-adapter re-pricing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Price `read-lora-v1` under the shipped policy from dumps already on disk, and apply the registered build/close rule.

**Architecture:** No pipeline code changes. Seven sanctioned `runner score` calls (six `--reapply-policy`, one plain), each followed by `runner summary` into `docs/eval/`; five `runner compare` calls; a scratchpad tabulator that reads ONLY `docs/eval/` and prints each prediction against its measured value plus the rule's verdict; then a result doc and the CLAUDE.md / `run_gpu_queue.sh` updates.

**Tech Stack:** `python3 -m app.eval.runner` (score / summary / compare), Python stdlib for the tabulator.

**Binding inputs, read before starting:**
`docs/plans/2026-10-05-read-adapter-repricing-design.md` (decision rule §2) and
`docs/plans/2026-10-05-read-adapter-repricing-prediction.md` (P1–P10). Both are
committed at `e355d1f`, before any score ran. **Do not edit either file after
Task 1 starts.** A prediction changed after a price is seen is void.

**Guard rules that bite here (CLAUDE.md §1, §5):** every command below runs
BARE, one per Bash call: no `|`, `&&`, `>`, heredoc. `git add` and `git commit`
are separate calls. Never open any file under the protected root, including the
reports these commands write there; only the `docs/eval/` summaries are
readable.

---

## File map

| path | action | responsibility |
|---|---|---|
| `docs/eval/reapply-<run>-scoped-summary.json` ×6 | create | counts-only digests, DERIVED |
| `docs/eval/lora72bnf4-scoped-summary.json` | create | plain scoped re-score, needed for P7 |
| `docs/eval/reapply-<pair>.json` ×5 | create | `runner compare` outputs |
| `$SCRATCH/tabulate_repricing.py` | create (scratchpad, not committed) | predictions vs measured, verdict |
| `docs/plans/2026-10-05-read-adapter-repricing-result.md` | create | result writeup |
| `CLAUDE.md` | modify §2 and §3 | the verdict, durably |
| `run_gpu_queue.sh` | modify `stage_why` for `loramerged` | records the scoping loss |

`$SCRATCH` = the session scratchpad directory. Set it in your head, not in a
shell variable: the shell does not persist state between calls, so write the
absolute path each time.

---

### Task 0: Verify state

- [ ] **Step 1:** `python -m pytest -q` → expect `1033 passed, 2 skipped`.
- [ ] **Step 2:** `bash ~/.claude/hooks/test-sindri-guard.sh` → expect `32 passed, 0 failed`.
- [ ] **Step 3:** `git log --oneline -3` → `e355d1f` (registration) must be in history, and `git status` clean apart from this plan.
- [ ] **Step 4:** Confirm none of the target digests exist yet (proves nothing was priced before registration):

```bash
ls docs/eval/
```

Expected: no file named `reapply-awqcontrol-scoped-summary.json`, `reapply-nf4control-scoped-summary.json`, `reapply-loraread-scoped-summary.json`, `reapply-vllmcontrol-scoped-summary.json`, `reapply-vllmlora-scoped-summary.json`, `reapply-lora72bnf4-scoped-summary.json`, `lora72bnf4-scoped-summary.json`. If any exists, STOP and report: it means a price may have been seen before registration.

---

### Task 1: Score the two control anchors (AWQ, NF4)

**Files:** create `docs/eval/reapply-awqcontrol-scoped-summary.json`, `docs/eval/reapply-nf4control-scoped-summary.json`

- [ ] **Step 1: Score r3-awqcontrol, reapplied, scoped**

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-awqcontrol" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name reapply-awqcontrol-scoped --out "$HOME/sindri-client-data/reports/reapply-awqcontrol-scoped.report.json" --reapply-policy
```

Expected: exits 0, prints the `NOTE: --reapply-policy is scoring dumps re-parsed...` line and a headline with 15 documents. If it REFUSES for missing dumps, STOP: do not pass `--allow-partial`. Report which run and wait.

**Sanity anchor:** r3-awqcontrol and r3-cropctx share detection (n_pred 569), and cropctx reapplied is 118.73 at pad 24. awqcontrol reapplied at pad 6 should land ABOVE 118.73 (pad 24 read better). A number below 118.73 means something is wrong: stop and investigate before continuing.

- [ ] **Step 2: Summarise it**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/reapply-awqcontrol-scoped.report.json" --out docs/eval/reapply-awqcontrol-scoped-summary.json
```

- [ ] **Step 3: Score r3-nf4control** (same command as Step 1 with `r3-nf4control` and the name/out `reapply-nf4control-scoped`):

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-nf4control" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name reapply-nf4control-scoped --out "$HOME/sindri-client-data/reports/reapply-nf4control-scoped.report.json" --reapply-policy
```

- [ ] **Step 4: Summarise it**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/reapply-nf4control-scoped.report.json" --out docs/eval/reapply-nf4control-scoped-summary.json
```

- [ ] **Step 5: Commit** (separate calls)

```bash
git add docs/eval/reapply-awqcontrol-scoped-summary.json docs/eval/reapply-nf4control-scoped-summary.json
```

```bash
git commit -m "eval: reapplied scoped digests for the AWQ and NF4 controls" -m "DERIVED (--reapply-policy), pad 6, dev. Anchors for the serving overheads in the read-adapter re-pricing; predictions registered at e355d1f." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

If the pre-commit hook rejects a digest for a quoted `upper_tol` / `lower_tol` token, STOP and report. Do not use `SINDRI_ALLOW_DATA_COMMIT` (CLAUDE.md §5).

---

### Task 2: Score the NF4 adapter arm (r3-loraread)

**Files:** create `docs/eval/reapply-loraread-scoped-summary.json`

- [ ] **Step 1: Score**

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-loraread" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name reapply-loraread-scoped --out "$HOME/sindri-client-data/reports/reapply-loraread-scoped.report.json" --reapply-policy
```

- [ ] **Step 2: Summarise**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/reapply-loraread-scoped.report.json" --out docs/eval/reapply-loraread-scoped-summary.json
```

- [ ] **Step 3: Commit** (separate calls)

```bash
git add docs/eval/reapply-loraread-scoped-summary.json
```

```bash
git commit -m "eval: reapplied scoped digest for r3-loraread" -m "DERIVED, pad 6, dev. The NF4 side of the read-adapter gain G_nf4." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Score the vLLM pair (r3-vllmcontrol, r3-vllmlora)

**Files:** create `docs/eval/reapply-vllmcontrol-scoped-summary.json`, `docs/eval/reapply-vllmlora-scoped-summary.json`

- [ ] **Step 1: Score r3-vllmcontrol**

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-vllmcontrol" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name reapply-vllmcontrol-scoped --out "$HOME/sindri-client-data/reports/reapply-vllmcontrol-scoped.report.json" --reapply-policy
```

- [ ] **Step 2: Summarise**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/reapply-vllmcontrol-scoped.report.json" --out docs/eval/reapply-vllmcontrol-scoped-summary.json
```

- [ ] **Step 3: Score r3-vllmlora**

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-vllmlora" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name reapply-vllmlora-scoped --out "$HOME/sindri-client-data/reports/reapply-vllmlora-scoped.report.json" --reapply-policy
```

- [ ] **Step 4: Summarise**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/reapply-vllmlora-scoped.report.json" --out docs/eval/reapply-vllmlora-scoped-summary.json
```

- [ ] **Step 5: Commit** (separate calls)

```bash
git add docs/eval/reapply-vllmcontrol-scoped-summary.json docs/eval/reapply-vllmlora-scoped-summary.json
```

```bash
git commit -m "eval: reapplied scoped digests for the vLLM pair" -m "DERIVED, pad 6, dev. G_vllm and the vLLM serving overhead." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Score the unscoped arm (r3-lora72bnf4), reapplied AND plain

The plain scoped score exists for P7 only. `contained_duplicate` is the only
active rule that removes predictions, so the difference in false detections
between plain and reapplied, net of the same difference on the control, is
what that rule absorbed.

**Files:** create `docs/eval/reapply-lora72bnf4-scoped-summary.json`, `docs/eval/lora72bnf4-scoped-summary.json`

- [ ] **Step 1: Score reapplied**

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-lora72bnf4" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name reapply-lora72bnf4-scoped --out "$HOME/sindri-client-data/reports/reapply-lora72bnf4-scoped.report.json" --reapply-policy
```

- [ ] **Step 2: Summarise**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/reapply-lora72bnf4-scoped.report.json" --out docs/eval/reapply-lora72bnf4-scoped-summary.json
```

- [ ] **Step 3: Score plain** (no `--reapply-policy`)

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-lora72bnf4" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name lora72bnf4-scoped --out "$HOME/sindri-client-data/reports/lora72bnf4-scoped.report.json"
```

- [ ] **Step 4: Summarise**

```bash
python3 -m app.eval.runner summary "$HOME/sindri-client-data/reports/lora72bnf4-scoped.report.json" --out docs/eval/lora72bnf4-scoped-summary.json
```

- [ ] **Step 5: Commit** (separate calls)

```bash
git add docs/eval/reapply-lora72bnf4-scoped-summary.json docs/eval/lora72bnf4-scoped-summary.json
```

```bash
git commit -m "eval: scoped digests for the unscoped adapter arm, reapplied and plain" -m "U in the read-adapter re-pricing: what a merged checkpoint (adapter over detection too) costs under today's policy. The plain score prices P7, how much of its false-detection increase contained_duplicate absorbs." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The five comparisons

`runner compare` takes two reports, control first. Each writes a JSON with
`mean_delta`, `ci95`, `weight_sensitivity.b_better_fraction` and `warnings`.

**Files:** create `docs/eval/reapply-loraread-vs-nf4control.json`, `docs/eval/reapply-vllmlora-vs-vllmcontrol.json`, `docs/eval/reapply-nf4control-vs-awqcontrol.json`, `docs/eval/reapply-vllmcontrol-vs-awqcontrol.json`, `docs/eval/reapply-lora72bnf4-vs-nf4control.json`

- [ ] **Step 1: G_nf4**

```bash
python3 -m app.eval.runner compare "$HOME/sindri-client-data/reports/reapply-nf4control-scoped.report.json" "$HOME/sindri-client-data/reports/reapply-loraread-scoped.report.json" --out docs/eval/reapply-loraread-vs-nf4control.json
```

- [ ] **Step 2: G_vllm**

```bash
python3 -m app.eval.runner compare "$HOME/sindri-client-data/reports/reapply-vllmcontrol-scoped.report.json" "$HOME/sindri-client-data/reports/reapply-vllmlora-scoped.report.json" --out docs/eval/reapply-vllmlora-vs-vllmcontrol.json
```

- [ ] **Step 3: O_nf4**

```bash
python3 -m app.eval.runner compare "$HOME/sindri-client-data/reports/reapply-awqcontrol-scoped.report.json" "$HOME/sindri-client-data/reports/reapply-nf4control-scoped.report.json" --out docs/eval/reapply-nf4control-vs-awqcontrol.json
```

- [ ] **Step 4: O_vllm**

```bash
python3 -m app.eval.runner compare "$HOME/sindri-client-data/reports/reapply-awqcontrol-scoped.report.json" "$HOME/sindri-client-data/reports/reapply-vllmcontrol-scoped.report.json" --out docs/eval/reapply-vllmcontrol-vs-awqcontrol.json
```

- [ ] **Step 5: U**

```bash
python3 -m app.eval.runner compare "$HOME/sindri-client-data/reports/reapply-nf4control-scoped.report.json" "$HOME/sindri-client-data/reports/reapply-lora72bnf4-scoped.report.json" --out docs/eval/reapply-lora72bnf4-vs-nf4control.json
```

Expected for all five: `n_docs` 15. Read `warnings` in each. A review-policy
warning must NOT appear, since both sides are reapplied under the same rules.
If one does, a side was not reapplied: stop. Warnings about quantisation,
serving backend or adapter are expected; record them verbatim in the result.

- [ ] **Step 6: Commit** (separate calls)

```bash
git add docs/eval/reapply-loraread-vs-nf4control.json docs/eval/reapply-vllmlora-vs-vllmcontrol.json docs/eval/reapply-nf4control-vs-awqcontrol.json docs/eval/reapply-vllmcontrol-vs-awqcontrol.json docs/eval/reapply-lora72bnf4-vs-nf4control.json
```

```bash
git commit -m "eval: the five read-adapter re-pricing comparisons" -m "G_nf4, G_vllm, O_nf4, O_vllm, U, all DERIVED on scoped dev under the shipped policy. Verdict follows in the result doc." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Tabulate predictions against measured values, and apply the rule

A throwaway script. It reads only `docs/eval/`, so it names no protected path.
The rule is coded exactly as registered. The script is not a test target and is
not committed: its output is pasted into the result doc.

**Files:** create `<scratchpad>/tabulate_repricing.py`

- [ ] **Step 1: Write the script** (Write tool, absolute scratchpad path)

```python
"""Predictions (registered at e355d1f) vs measured, and the binding rule.

Reads only docs/eval/. Run from the repo root."""
import json

E = "docs/eval/"


def load(name):
    with open(E + name) as f:
        return json.load(f)


def find(d, key):
    """First value stored under `key` anywhere in a nested digest."""
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = find(v, key)
            if r is not None:
                return r
    return None


S = {r: load(f"reapply-{r}-scoped-summary.json")
     for r in ("awqcontrol", "nf4control", "loraread",
               "vllmcontrol", "vllmlora", "lora72bnf4")}
plain_U = load("lora72bnf4-scoped-summary.json")
plain_C = load("nf4control-scoped-summary.json")   # pre-existing, plain scoped
C = {k: load(f"reapply-{f}.json") for k, f in (
    ("G_nf4", "loraread-vs-nf4control"),
    ("G_vllm", "vllmlora-vs-vllmcontrol"),
    ("O_nf4", "nf4control-vs-awqcontrol"),
    ("O_vllm", "vllmcontrol-vs-awqcontrol"),
    ("U", "lora72bnf4-vs-nf4control"))}


def delta(k):
    return C[k]["mean_delta"]


def better(k):
    return C[k]["weight_sensitivity"]["b_better_fraction"]


def aa(run):
    return find(S[run], "auto_accept")


def m(run, key):
    return find(S[run], key)


def band(x, lo, hi):
    return "IN" if lo <= x <= hi else "OUT"


print("== quantities ==")
for k in C:
    print(f"{k:7s} delta {delta(k):+.2f}  ci95 {C[k]['ci95']}  "
          f"better {better(k):.3f}  warnings {len(C[k]['warnings'])}")

print("\n== predictions ==")
g1, g2 = delta("G_nf4"), delta("G_vllm")
print(f"P1  G_nf4  {g1:+.2f}  band [-1.8,-0.3]  {band(g1, -1.8, -0.3)}")
print(f"P2  G_vllm {g2:+.2f}  band [-1.6,-0.2]  {band(g2, -1.6, -0.2)}")
print(f"P3  |gap|  {abs(g1 - g2):.2f}  <= 0.6  {'IN' if abs(g1 - g2) <= 0.6 else 'OUT'}")
o1, o2 = delta("O_nf4"), delta("O_vllm")
print(f"P4  O_nf4  {o1:+.2f}  band [+1.0,+4.0]  {band(o1, 1.0, 4.0)}")
print(f"P5  O_vllm {o2:+.2f}  band [+3.5,+7.5]  {band(o2, 3.5, 7.5)}")
u = delta("U")
print(f"P6  U      {u:+.2f}  >= +6, 0/6 better  "
      f"{'IN' if u >= 6 and better('U') == 0 else 'OUT'} (better {better('U'):.3f})")

# P7: false detections contained_duplicate removed on U, net of what it
# removed on the control, as a share of U's plain-scored increase.
fU_p, fU_r = find(plain_U, "false_detection"), m("lora72bnf4", "false_detection")
fC_p, fC_r = find(plain_C, "false_detection"), m("nf4control", "false_detection")
increase = fU_p - fC_p
absorbed = (fU_p - fU_r) - (fC_p - fC_r)
share = absorbed / increase if increase else float("nan")
print(f"P7  absorbed {absorbed} of +{increase} = {share:.1%}  < 25%  "
      f"{'IN' if share < 0.25 else 'OUT'}")

for arm, ctl, tag in (("loraread", "nf4control", "nf4"),
                      ("vllmlora", "vllmcontrol", "vllm")):
    de = m(arm, "escaped_error") - m(ctl, "escaped_error")
    dr = aa(arm)["rate"] - aa(ctl)["rate"]
    dn = m(arm, "n_pred") - m(ctl, "n_pred")
    print(f"P8  {tag:4s} escaped {de:+d}  falls 3-8  {'IN' if -8 <= de <= -3 else 'OUT'}")
    print(f"P9  {tag:4s} rate {dr:+.4f}  +0.01..+0.03  {band(dr, 0.01, 0.03)}")
    print(f"P10 {tag:4s} n_pred {dn:+d}  +-1  {'IN' if abs(dn) <= 1 else 'OUT'}")

print("\n== binding rule (design section 2) ==")
ok_all = True
for k, arm, ctl in (("G_nf4", "loraread", "nf4control"),
                    ("G_vllm", "vllmlora", "vllmcontrol")):
    pa, pc = aa(arm)["precision"], aa(ctl)["precision"]
    ra, rc = aa(arm)["rate"], aa(ctl)["rate"]
    conds = {
        "cost <= -1.50": delta(k) <= -1.50,
        "6/6 weightings": better(k) == 1.0,
        "precision not falling": pa >= pc,
        "rate rising strictly": ra > rc,
    }
    ok = all(conds.values())
    ok_all &= ok
    print(f"{k}: " + ", ".join(f"{c}={'PASS' if v else 'FAIL'}" for c, v in conds.items())
          + f"  (precision {pc:.4f}->{pa:.4f}, rate {rc:.4f}->{ra:.4f})")
print("VERDICT:", "BUILD a scoped serving path" if ok_all else "CLOSE (dead end)")
```

- [ ] **Step 2: Run it from the repo root**

```bash
python3 <scratchpad>/tabulate_repricing.py
```

Expected: every line prints, ending in `VERDICT: ...`. A `KeyError` or
`TypeError` means a digest lacks a key, e.g. `auto_accept` missing because a
summary was not re-scored. Fix by re-running that task's score + summary, never
by editing the digest.

- [ ] **Step 3: Cross-check one number by hand.** From
`reapply-loraread-scoped-summary.json` and `reapply-nf4control-scoped-summary.json`, the
difference in `mean_review_cost` must equal `G_nf4`'s `mean_delta` to 0.01.
Paired deltas over the same 15 documents average to the difference of means.
If not, the compare picked up different document sets: stop.

---

### Task 7: Result doc

**Files:** create `docs/plans/2026-10-05-read-adapter-repricing-result.md`

- [ ] **Step 1: Write it** with exactly these sections, filled from Task 6's output:

```markdown
# Read-adapter re-pricing — result

Run 2026-10-05 (or the date executed). DERIVED: `--reapply-policy`, scoped dev,
crop pad 6. Registered: `2026-10-05-read-adapter-repricing-prediction.md` (e355d1f).

## 1. Verdict
<BUILD or CLOSE, one paragraph, quoting the failing/passing conditions by name>

## 2. The five quantities
<table: quantity | pair | old-policy scoped | reapplied | ci95 | better-fraction | warnings>

## 3. Predictions vs measured
<table P1–P10: predicted | band | measured | IN/OUT>
<one line per OUT: which mechanism in prediction §3 it refutes>

## 4. What this does and does not license
<pad-6 caveat (G a probable upper bound at pad 24); dev only; a BUILD licenses
building, keeping needs native dev + native test per design §3>

## 5. U and the detect-trained adapter
<P7 share; whether the detect-adapter direction gets a brainstorm (≥50%) or
inherits this evidence against it>
```

Every number in the doc must come from Task 6's output or a `docs/eval/` file
committed in Tasks 1–5. No client values: counts and aggregates only.

- [ ] **Step 2: Commit** (separate calls)

```bash
git add docs/plans/2026-10-05-read-adapter-repricing-result.md
```

```bash
git commit -m "docs: read-adapter re-pricing result — <BUILD|CLOSE>" -m "<two-line summary: G_nf4, G_vllm, which rule condition decided it>" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

(Replace the angle-bracket text with the actual verdict and numbers before committing.)

---

### Task 8: Record the verdict durably

**Files:** modify `CLAUDE.md` (§2, and §3 if CLOSE), `run_gpu_queue.sh` (`stage_why`, the `loramerged)` line)

- [ ] **Step 1: `run_gpu_queue.sh`.** The scoping loss is true whatever the verdict. Append to the END of the `loramerged)` `stage_why` string, inside its quotes:

```
 CAVEAT (2026-10-05): a merged checkpoint has no base weights to scope detection back to, so detect_regions runs through the adapter -- the shape of the void arm lora72bnf4, not of r3-loraread. Serve it only as the READ model with the official AWQ checkpoint detecting (VLM_DETECT_MODEL_ID, two cards). See docs/plans/2026-10-05-read-adapter-repricing-result.md.
```

- [ ] **Step 2: Run the queue's behaviour tests** (the file is parsed by them):

```bash
python -m pytest -q tests/test_gpu_queue_behaviour.py
```

Expected: all pass.

- [ ] **Step 3: `CLAUDE.md` §2.** Add one bold-led paragraph after the policy-arms paragraph: the verdict, G_nf4 / G_vllm / O_nf4 / O_vllm / U with ci95, marked DERIVED / pad 6, plus the scoping-loss fact. **If CLOSE**, also add a §3 bullet "**Deploying `read-lora-v1`**" with the numbers and "do not run `mergedcontrol`/`loramerged` as staged". **If BUILD**, add instead a line naming the next step (scoped two-card serving vs the 4.49 loader), and that keeping it needs native dev, then native test.

- [ ] **Step 4: Full verification**

```bash
python -m pytest -q
```

Expected: `1033 passed, 2 skipped`.

```bash
bash ~/.claude/hooks/test-sindri-guard.sh
```

Expected: `32 passed, 0 failed`.

- [ ] **Step 5: Commit** (separate calls)

```bash
git add CLAUDE.md run_gpu_queue.sh
```

```bash
git commit -m "docs: record the read-adapter re-pricing verdict, and the merge route's scoping loss" -m "<verdict and the deciding numbers>" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Stop conditions (any one halts the plan and goes to the operator)

1. Task 0 Step 4 finds a target digest already present.
2. Any `score` refuses for missing dumps. Never `--allow-partial` here.
3. Task 1's awqcontrol anchor lands at or below 118.73.
4. A `compare` carries a review-policy warning.
5. Task 6 Step 3's hand cross-check disagrees.
6. The pre-commit hook rejects a digest.
