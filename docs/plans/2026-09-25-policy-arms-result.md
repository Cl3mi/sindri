# Policy arms — RESULT: flag by kind and by missing tolerance; drop contained duplicates

Measured 2026-09-25 with `score --policy-check` (exact counterfactual: rules
applied to stored dumps, re-scored). Predictions registered beforehand in
`docs/plans/2026-09-25-policy-arms-prediction.md` (committed before any
price was seen). Raw counts: `docs/eval/policy-{train,dev,test}.json` and
`docs/eval/policy-{train,dev,test}-joint.json`.

**One line:** three rules pass select (train), validate (dev) and confirm
(test) individually and jointly — `nondim_kind` and `no_tolerance` flags plus
the `contained_duplicate` drop. Jointly they cut dev review cost **131.87 →
118.73 (−13.13)**, test **165.18 → 149.73 (−15.45)**, raise auto-accept
precision **0.53 → 0.80 (dev) / 0.44 → 0.70 (test)** and hold recall,
field_acc and the auto-accept rate. They are DERIVED numbers (exact, from
stored dumps) until `r4-control` measures them natively.

## 1. Gates

| gate | expected | observed |
|---|---|---|
| dev `base.cost` | 131.87 | 131.8667 ✓ |
| test `base.cost` | 165.18 | 165.1818 ✓ |
| dev / test `base_low_conf_reflagged` | 0 | 0 / 0 ✓ (enforced) |
| train reflag | ≈ 38 | **97** — the estimate counted matched rows only; the base cost moved −2.80 (132.37 → 129.57) as predicted |

## 2. Per rule (cost delta per doc; ✓ = passes every §1 condition)

| rule | train | dev | test | registered | verdict |
|---|---|---|---|---|---|
| flag `nondim_kind` | −6.51 ✓ | −6.73 ✓ | −3.64 ✓ | PASS, dev ≈ −6.7 | **held to the decimal** → KEEP |
| flag `gdt_guessed` | −2.53 ✓ | −2.87 ✓ | −0.73 ✓ | PASS | held; a strict SUBSET of `nondim_kind` (it requires kind gdt) → adds nothing jointly, not kept |
| flag `tall_box` | ✗ rate −0.051 | ✗ rate −0.048 | ✗ rate −0.021 | FAIL on rate | **held** — cost falls a lot (−7 to −11) but it flags 15-61 correct rows; the rate guard is what stops it |
| flag `diameter_sign` | ✗ rate | ✗ rate | ✗ rate | lean FAIL | held |
| flag `multiline` | no-op | no-op | no-op | lean FAIL | the reader never emits a newline; the rule cannot fire |
| flag `no_tolerance` | −9.78 ✓ | −5.60 ✓ | −11.27 ✓ | **FAIL (negative control)** | **REFUTED** — see §3 → KEEP |
| flag `asymmetric_tol` | ✗ | ✗ | ✓ | uncertain | fails select and validate |
| drop `empty_read` | ✗ (+0.39) | ✗ precision | ✗ recall | PASS small | **refuted** — empty reads include matched rows (flagged_error 7 / 1 / 9), dropping them loses matches |
| drop `contained_duplicate` | −0.24 ✓ | −0.80 ✓ | −0.18 ✓ | small, lean PASS | held → KEEP |
| drop `repeated_value_nearby` | ✗ 4/6 | −1.40 ✓ | ✗ | small, lean PASS | fails select and confirm |
| drop `no_digit` | ✗ | ✗ | ✗ | tiny | fails (drops flagged matched rows) |
| drop `theoretical_no_nominal` | ✗ | ✗ precision | ✓ | tiny | fails |
| drop `note_kind` | ✗ recall | ✗ escaped_rate, precision | ✗ recall | FAIL on recall | held on train/test. On dev recall did NOT fall — the 4 dropped matched notes' gold rows re-paired with other predictions — but one re-paired row arrived unflagged and wrong (escaped +1), so it failed on the escaped/precision guards instead. Cost alone said −5.33 |
| drop `theoretical_kind` | ✗ recall | ✗ recall | ✓ | FAIL on recall | held on train/dev |

## 3. The refuted negative control, and why it is kept

`no_tolerance` flags a `dimension` row that has a nominal but no tolerance.
Registered as a negative control on the belief that gold often carries no
tolerance. It flags **1 correct row on train, 0 on dev, 0 on test** against
120 / 21 / 31 escaped. Mechanism: gold carries a tolerance on essentially
every row — the general tolerance from the title-block table
(`dropped_tolerances`: rows ≫ distinct per document) — so a dimension read
with NO tolerance is wrong against gold by construction. Flagging it is
honest, not a scoring artefact: the reviewer genuinely has to supply the
tolerance. (The better long-term fix is to fill the general tolerance from the
title block; this rule is what makes that gap visible to the reviewer now.)

## 4. The joint set

`--flag-rules nondim_kind,no_tolerance --drop-rules contained_duplicate`:

| | train | dev | test |
|---|---|---|---|
| cost | 129.57 → 113.04 (**−16.53**) | 131.87 → 118.73 (**−13.13**) | 165.18 → 149.73 (**−15.45**) |
| escaped_error | 292 → 89 | 64 → 17 | 64 → 22 |
| auto-accept precision | 0.489 → **0.755** | 0.533 → **0.805** | 0.444 → **0.699** |
| auto-accept rate | 0.231 → 0.227 | 0.235 → 0.225 | 0.175 → 0.175 |
| recall / field_acc | −0.0008 / +0.0029 | 0 / 0 | 0 / 0 |
| better under | 6/6 | 6/6 | 6/6 |
| passes | ✓ | ✓ | ✓ |

What it means for the product: on dev the reviewer is told to skip 87 rows
instead of 137, and 80% of those are right instead of 53%. The rows still
automated barely shrink (70 correct vs 73). This improves the REVIEW, not the
READ: no value changes.

## 5. False-detection diagnosis (Task 9)

See the prediction doc §2. Duplicates of a match are 7-11% of false
detections; `contained_duplicate` removes 6 of 346 on dev. The bulk
(near-gold 45-61%, far-numeric 21-46%) is text that looks like a callout where
gold has no balloon. **No gold-free rule registered here reaches it**, and no
new rule is added after seeing prices (not eligible under the protocol). The
false-detection bucket therefore stays essentially where it was (346 → 340 on
dev); it needs a different mechanism (a verifier, or the client's weight for a
false detection) and is recorded as open.

## 6. Decision

KEEP (activate in the pipeline, Task 10): `ACTIVE_FLAG_RULES = ("nondim_kind",
"no_tolerance")`, `ACTIVE_DROP_RULES = ("contained_duplicate",)`.
Not kept: every other rule, for the reasons above. Derived until a predict run
on current code (`r4-control`) reproduces them; the registered prediction for
that run is "equal to the derived numbers to the decimal".

## 7. OCR proposals (direction 4) — NO-GO at the CPU gate

`score --proposal-check` on dev, registered go/no-go in the prediction doc §4.
Counts: `docs/eval/proposal-check-dev.json`.

| | observed | registered |
|---|---|---|
| gates `scale_mismatch` / `isolated` | 0 / 58 ✓ | 0 / 58 |
| **isolated misses with an OCR proposal in the gate** | **6 of 58** | GO iff ≥ 12 (expected 15-30) |
| proposals | 385 (253 far from any gold, 112 near matched gold, 20 near unmatched) | hundreds far ✓ |
| unverified bound 9·covered − 2·far | −452 units | negative ✓ |

**NO-GO.** Even a perfect VLM verifier could recover at most 6 isolated misses
(≈ −3.6 per doc) before paying for any false detection, so the GPU arm cannot
reach the gate's ceiling. The registered expectation (15-30) was wrong: the
callouts the VLM detector misses are, overwhelmingly, ones tesseract does not
find either — consistent with them being missed for visual reasons (small,
rotated, crowded, overlapping geometry) that a second, weaker text detector
shares. Tuning tesseract (upscaling, binarisation) after seeing this number
would be selecting on dev; it would need its own registered prediction.

**Reverted** per §1: `app/pipeline/proposals.py`, `Detection.source`,
`app/eval/proposal_check.py`, the `--proposal-check` flag and their tests.
Isolated misses remain reachable only through the detector's weights (the
Rung 3 finding, 75 → 43 with an adapter); that stays the route.
