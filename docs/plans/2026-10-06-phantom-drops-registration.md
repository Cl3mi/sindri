# Phantom drop rules — registration (frozen before any rule is priced)

Registered 2026-10-06, after the TRAIN profile
(`docs/eval/phantom-profile-train.json`) and before any candidate has been
priced on any split. Dev and test have never been profiled. Design:
`2026-10-06-phantom-arm-design.md`. Operator decisions: drops, not flags;
precision at ANY recall; this candidate set and selection rule.

## 1. Candidates (gold-free; each fires ONLY on a row that would ship unflagged)

A drop rule runs after the flag pass (`extract._apply_active_policy`: flags,
then drops), so `needs_review` is final when it is read. A flagged row is never
dropped by these rules: it is not delivered, so dropping it could only cost
recall.

| rule | drops an unflagged row when |
|---|---|
| `conf_below_090` | `confidence < 0.90` |
| `conf_below_095` | `confidence < 0.95` |
| `conf_below_099` | `confidence < 0.99` |
| `material_kind` | `kind == "material"` |
| `tight_cluster` | another prediction's box centre lies within **50 px** |

`tight_cluster` deviates from the profiled feature: the profile measured the
nearest centre as a fraction of the page diagonal (< 0.01), but a drop rule
sees the characteristics and not the page. 50 px is about 1% of the diagonal at
300 dpi, between A4 (~43 px) and A3 (~61 px). The deviation is registered here
rather than discovered later.

**Configurations priced** (singles plus registered joint sets):

* singles: the five rules above;
* joints, for each dose t in {090, 095, 099}: `conf_below_t + material_kind`,
  and `conf_below_t + material_kind + tight_cluster`.

That makes 11 configurations, each against the same control.

## 2. Control, metric, keep rule

Control per split = `reapply_current_code` of the stored dumps (today's
post-read code, active flags and drops): train `r3-trainpredict`, dev
`r4-control`, test `r4-controltest`, all scoped. The arm is the control with
the configuration's drops applied after the active policy, then re-scored with
matching.

**Keep (binding), on train, dev and test, each against its own control:**

1. delivered precision rises strictly;
2. matched-only auto-accept precision does not fall;
3. the delivered set is not empty.

Recall, review cost and the six weightings are reported in full and decide
nothing (precision at any recall).

**Selection (binding):** among the configurations that pass on TRAIN, take the
one with the highest train delivered precision (ties: more delivered correct
values, then the order the configurations are listed above, so the simpler
configuration wins an exact tie; added before any pricing, to make the order
total). Only that configuration is then priced on dev and test, and it is kept
iff it passes on both. If it fails either, nothing ships: there is no second
pick, because a fallback chosen after seeing dev would be selected on dev.

## 3. Predictions (from the train profile; re-pairing can move them slightly)

Train control: delivered 475 = 274 correct / 89 wrong / 112 phantom,
delivered precision 0.577.

| configuration | train delivered precision | correct values lost |
|---|---|---|
| `material_kind` | 0.597 | 0 |
| `tight_cluster` | 0.58-0.62 | 5-15 |
| `conf_below_090` | 0.646 | 7 |
| `conf_below_095` | 0.720 | 30 |
| `conf_below_099` | 0.879 | 100 |
| joints | +0.00 to +0.03 over their confidence dose | +0 to +10 |

* **Selected configuration:** a `conf_below_099` joint, train delivered
  precision 0.87-0.91.
* **Dev** (control 0.419, 70/17/80): rises to 0.70-0.92, correct values lost
  20-35.
* **Test** (control 0.536, 52/24/21): rises to 0.65-0.92, correct values lost
  15-30. Test is the atypical split, so confidence may be calibrated differently
  there, which is the main risk.
* Matched-only precision rises on every split, because escaped rows sit mostly
  below 0.99 (train: 83 of 89).
