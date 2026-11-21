# Experiments

<!-- Append-only. One entry per experiment/eval run. Never edit prior entries. -->

Raw results only. Decision rationale lives in `docs/adr/`, system shape in `ARCHITECTURE.md`,
status in `ROADMAP.md`. Backfilled 2026-11-21 from `docs/plans/*-result.md`, `*-prediction.md`,
`*-registration.md`, the 2026-09-09 handoff and the committed digests in `docs/eval/`. Every figure
below was checked against a digest or the result doc it cites. Where `CLAUDE.md` and a digest
disagreed, the digest won (one case is noted in the 2026-09-15 crop entry).

## How to read this log (read before using any entry)

**A result here is a measurement under named conditions, not a verdict on an idea.** Most entries
say "closed" or "killed". That means *this variant, under these conditions, failed its registered
rule*. It does not mean the direction can never work. Each entry therefore ends with
`Revisit if:`, which names the condition that would change the answer. Before acting on an entry,
check whether that condition still holds today. Several already have changed.

**Tags at the start of each `Config:` line**

- `Era N`: the project state the number was measured in (table below). A number from one era
  is not comparable to a number from another unless the entry says so.
- `dev` / `test` / `train`: which split. Dev is 20 documents unscoped, or 15 under the scope policy.
  Test is 11 scoped documents and deliberately adversarial. Train is 49-60 documents.
- `MEASURED` = a native predict run, then scored. `DERIVED` = CPU re-score of stored dumps
  (`--reapply-policy`, `--policy-check`, `--reparse-check`). Derived numbers were exact every time
  they were later checked natively (r4-control, r5-control, r5-controltest), but they are not runs.
  `DIAGNOSTIC` = no treatment, only a measurement of existing dumps.

**Project state by era, and how it shaped what was tried**

| Era | Dates | State of the project | Effect on experiments |
|---|---|---|---|
| 0 | to 2026-09-02 | 20-doc unscoped dev split. `LOW_CONF` 0.6. A char_type scoring bug existed (174.30 reference). Weights miss 10 > escaped 5 > false 2 > flag 1. Crop pad 6. No policy rules. | Question was "which pipeline knob or prompt reduces review cost". Detection and prompt knobs were tried first because they were cheap. Absolute costs here are one scoring policy behind later eras. |
| 1 | 09-03 to 09-14 | `LOW_CONF` 0.8 (170.05 unscoped reference). Scope policy 2026-09-09: single-sheet, full-resolution drawings only, so 15 dev documents (133.93 reference). Still pad 6. | Question moved to "read quality" (LoRA adapter, serving stacks, model size). All model-swap arms were priced on 15 docs, where significance is weak. |
| 2 | 09-15 to 09-24 | Crop pad 24 shipped (131.87). Test split measured for the first time (165.18). | Question became "what does the reader see". Input-side levers (crop, parser) replaced model-side ones. Test showed dev is optimistic. |
| 3 | 09-25 to 10-06 | Gold-free flag/drop policy shipped by default, then drop stage 2. Controls: `r5-control` dev 117.87, `r5-controltest` test 148.82. Client ranked precision over recall (2026-10-05), which `weights.json` still does not encode. | Question became "what is delivered unflagged, and is it true". Auto-accept precision and delivered precision became the first-class guards, ahead of review cost. Older arms were re-priced under the active rules (the adapter flipped from win to loss). |
| 4 | 10-07 to now | Automated-precision direction closed and written up. Product work is the batch/async review app, the HITL capture, the client review campaign (C1/C2) and its hardening. Pilot `pilot-1` is built but not yet inspected, run or graded. | No pipeline experiment is open. The next measurements come from the client's own review actions, not from gold. |

**Constraints that shaped every experiment** (a future session may be able to relax some of them):

- The client corpus is under NDA, so every result is a counts-only digest. An agent never sees a
  drawing, so diagnoses rest on aggregates and operator review, not on looking at rows.
- Decoding is greedy, so the noise floor is zero and one arm per hypothesis is enough. Any non-zero
  delta is causal. This also means "no repeats" is by design, not an omission.
- GPU time is scarce (two shared H100s, an unreliable host). That is why most arms were first
  priced on CPU from stored dumps, and why later arms were registered before they ran.
- Gold is a strict subset of what the client inspects (they balloon fewer values than they check).
  "Phantom", "false detection" and "missed" all mean "not in gold" or "in gold", not "wrong about
  the drawing". Several closed leads depend on this and could reopen if the client's ballooning
  policy turns out to be learnable or changeable.
- Scope was fixed in 2026-09-09: 24 of 99 corpus drawings are out of scope (oversized or multi-page).
  Nothing here says anything about those.

**Pending, no entry yet:** the pilot campaign grade (`score --reviewer-grade`). Its digest will be
the first measurement of reviewer effort against the client's own actions. Several `Revisit if:`
lines below point at it.

---

## 2026-08-18 — Rung 0 baseline (historical, pre-ROADMAP)
Config: [Era 0 · dev, 20 docs, unscoped · MEASURED] 72B AWQ, two-stage detect then read, pad 6, committed prompts (`prompt_sha256 aa7659f1929184ea`), frame-corrected gold, weights 10/5/2/1. `docs/eval/baseline-summary.json`.
Result: review cost 173.05 (174.30 before the 2026-09-02 char_type scoring fix), recall 0.6457, precision 0.3711, field_acc 0.3799, escaped_rate 0.2600. missed 169 (contended 82 / isolated 74 / unlocated 13), false_detection 522. An older 245.30 in git history measured a coordinate bug, not the model.
Decision: Use as the Era 0 reference only. Every later arm is judged against the control named in its own comparison file, never against this number.
Revisit if: never quote this as current quality. Era 3 controls (r5-control, r5-controltest) replaced it.

## 2026-08-19 — Historical: render budget and matching mode
Config: [Era 0 · dev, 20 docs · MEASURED] (a) pixel budget 80 → 150 MP, un-clamping two sheets to 300 dpi (`render150`). (b) `--assignment max_cardinality` matching instead of greedy.
Result: (a) `render150`: +1.65 cost, ci95 [−0.70, 4.65], not significant; isolated misses 251 → 252, so resolution recovered nothing. Its absolute cost (246.95) comes from the coordinate-bug era. (b) max_cardinality recovered 26 misses by destroying 27 correct pairings. field_acc on matched rows fell 36.4% → 25.4% while review cost fell (168.60), which is why cost alone cannot judge an arm.
Decision: (a) reverted, do not build tiled rendering on the premise that resolution recovers misses. (b) kept only as the `--assignment max_cardinality` diagnostic, never the default.
Revisit if: (a) a different model or serving stack makes a drawing's resolution the binding constraint. (b) not worth revisiting. The greedy default is what the field_acc guard exists to protect.

## 2026-08-21 — Historical: detection knobs (direction run, 4 arms)
Config: [Era 0 · dev, 20 docs · MEASURED] control plus one knob per arm: `tightmerge` (`merge_y_gap` 20 → 8), `nomerge` (`merge_max_lines` 2 → 1), `finetiles` (tile 1024 → 768). Control reproduced the baseline on 20/20 documents at delta 0.0. Scored on the 174.30 scoring. `docs/plans/2026-08-21-direction-run-findings.md`.
Result: tightmerge +0.35 (ci95 [−1.35, 1.9], 2 of 6 weightings, not robust). nomerge +5.60 (significant, 0/6). finetiles +5.00 (ci95 [−1.7, 13.45], 0/6; damage concentrated 12× on render-clamped sheets, `missed_isolated` 16 → 28 there). Break-even is 5.0 false detections per recovered miss. Measured 6.50 and 8.75, above break-even in both directions. Only 8 of 82 contended misses were merge artefacts.
Decision: Committed detection defaults kept as locally optimal on all three knobs. Merge artefacts are not the explanation for contended misses.
Revisit if: detection changes in kind (new detector weights or a different localiser). These knobs were tuned for one detector. The one thing that did move isolated misses later was changing the detector's weights (2026-09-05).

## 2026-08-26 — Historical: read-stage diagnostics (Rung 2 Phase A)
Config: [Era 0 · dev, 20 docs · DIAGNOSTIC] four values-blind aggregates over the 196 matched-but-wrong rows: failing field, failure mode, whether the pair hit the right callout, and read confidence. No GPU.
Result: all four fields fail on about three fifths of wrong rows. 42% of wrong rows are wrong in the entire value. Single-field ceilings: tolerance-only fixes could convert at most 29 rows (about −5.8 cost), char_type-only 23 rows (about −4.6). All 105 escaped errors sit at confidence ≥ 0.8.
Decision: Killed the registered `readtol` prompt arm (it could reach at most 29 of 196 rows). Two arms left: `readcenter` and `detectbox`. Confidence thresholding for silent errors was rejected as saturated.
Revisit if: the confidence signal changes, for example calibrated confidence from a different model. The saturation finding is about this reader's token confidence only.

## 2026-08-27 — Historical: read prompt toward the centre callout (`readcenter`)
Config: [Era 0 · dev, 20 docs · MEASURED] read prompt names the centre callout. Detection unchanged (bit-identical), so it isolates the read prompt.
Result: +0.90 cost (ci95 [−0.50, 2.45]), field_acc −0.0162. Target bucket `misread.misplaced` unchanged at 64 → 64.
Decision: Closed. Rows in that bucket are not an ambiguity about which callout to read.
Revisit if: a failure bucket is found whose cause is callout selection. This one was refuted for that bucket only.

## 2026-08-28 — Historical: detect prompt toward tighter boxes (`detectbox`)
Config: [Era 0 · dev, 20 docs · MEASURED] detect prompt asks for tighter boxes.
Result: first scored −0.25 (4 of 6 weightings). Under the corrected char_type policy: +0.75, ci95 [−5.75, 7.45], 3 of 6 weightings. It destroyed 18 legitimate gdt/theoretical matches by the `score_kinds` mechanism. Its target bucket moved the wrong way (49 → 54).
Decision: Closed. Also settles that suppressing non-dimension detections is the closed `score_kinds` family again.
Revisit if: gold's `dimension` bucket stops including GD&T and surface callouts (`normalize._DIMENSION_WORDS`). The mechanism depends on that scoring choice.

## 2026-09-02 — Historical: char_type scoring fix
Config: [Era 0 · dev, 20 docs · DERIVED] `char_type_kind` matched on word containment while the synonym map matched the whole label, so compound gold labels ("Diameter MIN") were admitted to scoring and judged unequal. Fixed the matcher. Earlier, adding the obvious German synonyms moved nothing (byte-identical re-score).
Result: 174.30 → 173.05 (−1.25). Predicted about −4.6. It corrected 16 of 115 char_type rows, of which only 5 were wrong in char_type alone. Residual: `Diameter→Distance` 11 and `Distance→Diameter` 11, a symmetric confusion over the leading Ø.
Decision: Kept. Do not propose more synonym entries. No arm verdict flipped when all arms were re-scored.
Revisit if: new gold labels arrive in a vocabulary the map has not seen. `char_type_confusion` in the digest is the aggregate to read first.

## 2026-09-03 — Historical: `review.LOW_CONF` 0.6 → 0.8
Config: [Era 0→1 · dev, 20 docs · MEASURED on both quantisations] threshold change only. `r3-awqcontrol`, `r3-nf4control`.
Result: AWQ 173.05 → 170.05 (−3.00), exactly as derived. The 0.6 to 0.8 band was 18 matched pairs, all wrong. NF4 179.80 → 176.40 (−3.40, 17 rows). Recall, n_pred, missed, false_detection and field aggregates bit-identical. A re-run 9.5 h later reproduced it.
Decision: Kept. 170.05 (AWQ) and 176.40 (NF4) are the Era 1 controls.
Revisit if: the confidence distribution changes (a different model or serving stack). A threshold tuned on this reader's confidence does not transfer.

## 2026-09-05 — Later: "Retrain the read adapter after the `render_target` fix; price it under the active rules" (void arm)
Config: [Era 1 · dev, 20 docs · MEASURED] `lora72bnf4`: `read-lora-v1` (r=8, 731 verified pairs, 645 train / 86 validation, best checkpoint epoch 2) on an NF4 base. `resolve_adapter` wrapped the whole model, so detection ran through adapted weights too.
Result: +10.00 vs `r3-nf4control` (186.40 vs 176.40). false_detection 607 → 931, `n_pred` 926 → 1291. The only observed movement of isolated misses: 75 → 43.
Decision: Void, because it never tested the read stage alone. Fixed scoping so `detect_regions` generates inside `_base_weights()`.
Revisit if: a detection-scoped adapter is trained on real box targets. Gold has balloon positions, not callout boxes, so box targets would have to be synthesised from `tighten_to_ink`. That is the blocker, not compute. The 75 → 43 movement is the one evidence that detector weights can move isolated misses.

## 2026-09-07 — Later: "Retrain the read adapter after the `render_target` fix; price it under the active rules"
Config: [Era 1 · dev, 20 docs · MEASURED] `r3-loraread`: adapter scoped to the read pass, NF4 base, vs matched control `r3-nf4control` (176.40). `docs/plans/2026-09-07-rung3-loraread-result.md`.
Result: 172.00 (−4.40, ci95 [−7.9, −1.2], 6 of 6 weightings). field_acc 0.3730 → 0.4119, escaped_rate 0.2579 → 0.2096, escaped_error 123 → 100. Cost reconciles: 10(+1) + 5(−23) + 2(+1) + 1(+15) = −88, over 20 docs. The win is 23 silent errors converted for 15 extra flags. Detection identical (`n_pred` 926). `dropped_tolerances` 95 → 33 but `wrong:*_tol` +88 (it learned to always emit a tolerance-shaped output, because `render_target` renders `+x -y` whenever gold has one). Still +1.95 worse than the 170.05 production serves.
Decision: First win of the campaign, but not shippable alone. It later reversed under the shipped policy (2026-10-05).
Revisit if: `render_target` is fixed so a target never renders a tolerance the drawing does not print, and the retrained adapter is priced under the active flag rules with `escaped_error` as the damage counter. This is still an open Later item.

## 2026-09-08 — Later: "Retrain the read adapter after the `render_target` fix; price it under the active rules" (serving)
Config: [Era 1 · dev, 20 docs · MEASURED] two serving questions. (a) `lora72bawq`: attach the adapter to the AWQ base. (b) vLLM native LoRA (route B) with and without the adapter. `docs/plans/2026-09-08-deployment-routes.md`, `docs/eval/vllm*`.
Result: (a) PEFT refused at load: autoawq replaces q/k/v/o_proj with `WQLinear_GEMM`, which PEFT cannot inject into. The later `device_map ... CPU or disk` errors are downstream of a leaked 40 GB, not a separate fault. (b) vLLM control 178.70 (+8.65 vs 170.05 AWQ, ci95 [−1.95, 20.65], not significant). vLLM + adapter 174.05, −4.65 vs its control (ci95 [−9.6, −0.45]). So the adapter gain replicated on a second stack; the serving stack costs more than the gain.
Decision: (a) an impossibility, not a loss. Routes left were NF4 serving, merge-and-requantise, or vLLM. Neither route moves ballooning at all. The adapter was scoped away from detection.
Revisit if: the base weights or serving stack change. The merge route (`merge_lora.py`, `loramerged` / `mergedcontrol`) was built but never run, and is marked do-not-run after 2026-10-05. A merged checkpoint cannot keep read/detect scoping.

## 2026-09-09 — Later: "Cut detection cost (fewer/cheaper tiles, lower `max_new_tokens`)" (model-size ladder)
Config: [Era 1 · dev, 15 scoped docs · MEASURED] same pipeline, different localising-and-reading model: 32B AWQ and 7B AWQ vs the 72B AWQ production (133.93). `docs/plans/2026-09-09-session-handoff.md` §2.
Result: 32B 172.73 (+38.8), recall 0.7749, field_acc 0.2614, silent-wrong 0.3215, n_pred 890. 7B 180.00, recall 0.2605, n_pred 168, missed 74%. 72B production: recall 0.7170, field_acc 0.4798.
Decision: Smaller models rejected on quality. The 32B finds more rows but delivers far fewer correct ones (see the hybrid, next entry).
Revisit if: a smaller model is evaluated for cost reasons and quality is the secondary question. Detection is about two thirds of per-document cost (10 m 55 s and 23 m 45 s detect-only on two dev docs, against a roughly 16 min full-predict median), so a cheaper detection path would still shorten every run.

## 2026-09-11 — Historical: hybrid detector (32B localises, 72B transcribes)
Config: [Era 1 · dev, 15 scoped docs · MEASURED] `r3-hybrid-scoped`. Registered beforehand in `2026-09-09-hybrid-arm-prediction.md`. `docs/plans/2026-09-11-hybrid-arm-result.md`.
Result: 173.93, +40.00 vs `r3-awqcontrol-scoped` (ci95 [26.07, 56.27], significant, 0 of 6 weightings better). Detection moved as aimed (contended 19 → 10, isolated 58 → 49), but silent errors 71 → 105 and fully-correct values 107 → 66. Holding the reader and changing the boxes: field_acc 0.4798 → 0.2739 (−0.206). Holding the boxes and changing the reader 32B → 72B: +0.013. The registered `field_acc >= 0.40` gate tripped and the arm was still valid, because greedy determinism proves the reads ran on the 72B.
Decision: Detector swap closed. The central finding is that the input to the read (the crop) holds more read quality than the reader does, which redirected the campaign to crop preparation.
Revisit if: the swap is paired with a way to get usable boxes from the cheaper detector. The 32B's extra recall is recall of boxes, not values. Never quote its 0.7749 as an improvement.

## 2026-09-15 — Next: "Turn the test-run findings into a registered improvement plan" (crop pad)
Config: [Era 1→2 · dev, 15 scoped docs · MEASURED] `SINDRI_CROP_PAD` 6 → 24 px. Registered in `2026-09-14-crop-context-arm-prediction.md`. `docs/eval/cropctx-scoped-*`.
Result: 131.87 vs 133.93: −2.07, ci95 [−5.33, +0.47], not significant on cost but better under 6 of 6 weightings. field_acc 0.4798 → 0.5291 (+0.049, larger than the adapter's +0.039). Detection bit-identical. Cost reconciles: 5(−7) + 1(+4) = −31, over 15 docs. Only about 4 of 11 rows that became correct were tolerance-only. The registered mechanism was the smaller half.
Decision: Shipped (`_CROP_PAD` = 24). `active_crop_knobs` compares against a frozen 6/40/3.0 baseline so older dumps stay reproducible.
Revisit if: `CLAUDE.md` calls this a significant win. The digest says robust across weightings but not significant on cost. On test the gain did not carry over (2026-10-06).

## 2026-09-15 — Historical: first measurement of the test split
Config: [Era 2 · test, 11 scoped docs · MEASURED] production configuration at pad 6, never before predicted on test. `r3-awqtest-scoped`. `docs/plans/2026-09-15-test-split-result.md`.
Result: 165.18 vs dev's 133.93. Recall 0.6301 vs 0.7170, field_acc 0.3804 vs 0.4798, missed 37.0% vs 28.3%. `missed_unlocated` 31 of 292 test gold rows vs 11 of 311 on dev. Charging unlocated rows at w=5 gives dev 130.27, test 151.09, gap −31.25 → −20.8. A NaN confidence made one dump write-only on this run (guarded since).
Decision: Quote the product as a range, never the dev number alone. Not a comparison (different documents, no ci95), and the test split is adversarial by construction (6 of 19 predicted documents multi-page, vs 1 of 20 in dev).
Revisit if: a test number is quoted without its split or its 11-document caveat. About a third of the gap is how unlocated gold is priced. What remains is the field_acc fall on unfamiliar layouts.

## 2026-09-16 — Historical: crop pad dose 48
Config: [Era 2 · dev, 15 scoped docs · MEASURED] `cropctx48`, pad 48 vs pad 24. Registered rule: "field_acc up AND misplaced_matches ≤ 46 → take 48".
Result: 132.73 vs 131.87, field_acc 0.5426 vs 0.5291, escaped_rate 0.2154 vs 0.2058. Both registered conditions held and 48 is still the worse setting. `misplaced_matches` never moved (42 at both doses), while the damage landed in the flagged/silent split. Three rows became fully correct and three became escaped between the doses.
Decision: Stay at 24, do not test 96. The rule's counter had to respond to the previous dose to be allowed to decide, and it had not.
Revisit if: the review policy changes what a silent error costs. This curve was measured at the weights and flag rules of Era 2.

## 2026-09-16 — Historical: crop height diagnostic (`_MIN_CROP_H`, `_MAX_UPSCALE`)
Config: [Era 2 · dev, 15 scoped docs · DIAGNOSTIC] `read_accuracy_by_crop_height` over matched rows. No GPU.
Result: only 9 of 223 matched rows (4.0%) sit below `_MIN_CROP_H`. Accuracy falls with height: 0.71 at 28-40 px, 0.59 at 40-80, 0.40 at ≥80 px (126 of 223 rows). The pad-6/24/48 win was almost entirely in the ≥80 band (0.3968 → 0.4724 → 0.4921). Those boxes are clipped, not over-filled.
Decision: The two resolution knobs refuted without a run (they cannot reach 96% of rows, and more pixels is backwards). Registered the height-dependent pad as the one remaining lead.
Revisit if: `tighten_to_ink`'s `pad=3` is treated as untested. It is the same context lever as the crop pad, whose curve is peaked, so it is not a resolution knob.

## 2026-09-17 — Historical: height-dependent crop pad (`r3-tallpad`)
Config: [Era 2 · dev, 15 scoped docs · MEASURED] pad 48 for boxes ≥120 px, 24 elsewhere. Registered in `2026-09-16-tall-pad-arm-prediction.md`.
Result: 132.53 vs 131.87 (+0.67, ci95 [0.0, 1.53]), 0 of 6 weightings better. The `120-200` band moved 0.523 → 0.585 and every other band came back bit-identical. It fixed 6 flagged rows (no saving, since flagged_error and flagged_correct both cost 1) and broke 2 unflagged ones (+5 each).
Decision: Closed. Fixing a read only pays if the row was an escaped error. Before proposing any read-quality change, ask which taxonomy bucket the fixed rows are currently in.
Revisit if: the weights change so that a flagged error costs more than 1, or a flag rule stops flagging those rows. The lesson depends on today's flag/escaped cost ratio.

## 2026-09-17 — Historical: read accuracy by kind
Config: [Era 2 · dev, 15 scoped docs · DIAGNOSTIC] `read_accuracy_by_kind` on the shipped configuration (later extended with `wrong_fields`).
Result: dimension 189 rows at 0.6085, gdt 17 at 0.1765, theoretical 9 / surface 4 / note 4 at 0.0000. The 34 non-dimension rows are 15.2% of matched rows and carry 26 of the 64 escaped errors.
Decision: Routed the next two arms (theoretical parser, gdt review) and the non-dimension flag rule.
Revisit if: read `wrong_fields` before proposing a fix to a kind. A low per-kind accuracy does not say the kind is parser-reachable.

## 2026-09-22 — Historical: theoretical (boxed callout) parser fix
Config: [Era 2 · dev, 15 scoped docs · DERIVED] `parse_value` classifies boxed callouts from their text instead of emitting `THEORETICAL`, which gold's vocabulary does not contain (gold → Theoretical totals exactly 9). Priced with `score --reparse-check` (gate `identical == n_pairs` held 223/223). Commit `874771c`, reverted `89e0375`.
Result: `would_fix 0, would_break 0`. `identical` 223 → 214, exactly the 9 boxed rows. Those rows are wrong in their values too (7 of 9 in all four fields; 86% of their predictions are false detections), so unblocking the char_type comparison exposed them and they stayed wrong.
Decision: Reverted. The commit stays in history because it also made boxed gold rows renderable as training targets, which the bound cannot price (`reparse.py` prices only the prediction path).
Revisit if: an adapter is retrained and needs those targets (cherry-pick the commit). A correctness fix pays only when it is the row's last fault.

## 2026-09-25 — Historical: gdt review and the Ø-zone default
Config: [Era 2→3 · dev, 15 scoped docs · DERIVED] operator reviewed 8 gdt char_type-only rows in the local review app, then a closed-vocabulary probe (`vocab_probe`) cross-checked the answers. Registered table in `2026-09-22-gdt-review.md`. `docs/eval/gdt-review-tally.json`.
Result: the tally said the reader transcribes every symbol (8 of 8) and 4 rows were parser-fixable. The probe withdrew that: the characteristic's symbol is absent from at least 6 of 8 transcriptions (nothing before the value on 3, only the Ø zone sign on 3). What survived: `_gdt_type` defaulted Ø-zone frames to Flatness, which never has a cylindrical zone. Defaulting them to Position (`49f0629`): `would_fix 2, would_break 0`, −0.67 cost on dev (131.87 → 131.20), derived. The registered prediction that 3 rows would come back `scorer_already_matches` was refuted 0 of 3.
Decision: Kept. No profile-symbol parser arm. Raise 2 `gold_disagrees_with_drawing` rows with the client.
Revisit if: the −0.67 is quoted as measured. Cross-check operator answers against a closed-vocabulary probe before acting, and name the known traps (Ø is usually the zone sign, stacked frames carry two characteristics) in review questions.

## 2026-09-25 — Later: "Turn the test-run findings into a registered improvement plan" (policy rules)
Config: [Era 2→3 · train / dev / test · DERIVED, then MEASURED 2026-09-27] gold-free post-read flag and drop rules, each priced on train (select), dev (validate), test (confirm). Registered in `2026-09-25-policy-arms-prediction.md`. `docs/eval/policy-*.json`, `docs/eval/policy-*-joint.json`.
Result: kept `nondim_kind` flag (dev −6.73, test −3.64), `no_tolerance` flag (dev −5.60, test −11.27) and `contained_duplicate` drop (dev −0.80). Joint: dev 131.87 → 118.73 (−13.13, ci95 [−18.0, −8.7], better on 15 of 15 documents and 6 of 6 weightings), test 165.18 → 149.73. Auto-accept precision 0.53 → 0.80 (dev), 0.44 → 0.70 (test). Measured by `r4-control`: reproduced the derived digest to the decimal. Failed: flags `tall_box`, `diameter_sign`, `asymmetric_tol`, `multiline` (never fires), `gdt_guessed` (subset of nondim_kind); drops `empty_read`, `no_digit`, `note_kind`, `theoretical_kind`, `repeated_value_nearby`, `theoretical_no_nominal`. `no_tolerance` was registered as a negative control and refuted: gold carries a tolerance on nearly every row (general tolerance from the title block), so a dimension with none is wrong by construction.
Decision: Shipped by default (ADR 0003). Do not re-propose a kind-drop: it is the closed `score_kinds` family again, measured rule by rule.
Revisit if: a model change fills the field a flag keys on. A flag on an ABSENT field is defeated by any model that fills it, right or wrong (this is exactly what happened to the adapter, 2026-10-05).

## 2026-09-26 — Later: "Cut detection cost (fewer/cheaper tiles, lower `max_new_tokens`)" (OCR proposals)
Config: [Era 3 · dev, 15 scoped docs · DIAGNOSTIC / CPU feasibility gate] tesseract OCR proposals as extra candidates for isolated misses (`score --proposal-check`). Registered GO threshold ≥ 12 of 58 isolated misses. `docs/plans/2026-09-25-policy-arms-result.md` §7, `docs/eval/proposal-check-dev.json`.
Result: an OCR proposal lands in the match gate of only 6 of 58 isolated misses (expected 15-30), against 253 of 385 proposals far from any gold. A perfect verifier would cap at about −3.6 per document.
Decision: Built and reverted. The misses the VLM makes are misses tesseract makes too.
Revisit if: a different detector (weights, not knobs) is available. Isolated misses remain a detector-weights problem.

## 2026-10-05 — Later: "Retrain the read adapter after the `render_target` fix; price it under the active rules" (repricing)
Config: [Era 3 · dev, 15 scoped docs · DERIVED] `score --reapply-policy` on stored dev dumps of the scoped adapter arms (NF4 and vLLM), under the shipped flag/drop rules, pad 6. Registered in `2026-10-05-read-adapter-repricing-prediction.md`.
Result: the adapter's old −3.40 / −2.47 became +3.33 (NF4, ci95 [+0.87, +6.33]) and +4.27 (vLLM, ci95 [+0.73, +8.07]), both significant, 0 of 6 weightings. Auto-accept precision 0.75 → 0.66 (NF4). Mechanism, reconciling exactly: the adapter fills missing tolerances with plausible wrong ones, `no_tolerance` stops firing, and rows go from flagged to silently wrong (flagged_error −20/−23, escaped_error +13/+14). NF4's serving overhead is +0.20 here (was +3.53). The unscoped/merged shape costs +17.20 and `contained_duplicate` absorbs none of its +183 false detections.
Decision: Deploying `read-lora-v1` by any route (NF4, vLLM, merge-and-requantise) closed. Do not run `mergedcontrol` / `loramerged`. The adapter itself is not closed, only this checkpoint.
Revisit if: a retrained adapter whose targets never render a tolerance the drawing does not print. Then price under the active rules with escaped_error as the damage counter. Price every read-stage arm under the active flag rules before believing an older number.

## 2026-10-05 — Historical: fill the ISO 2768 general tolerance from the title block
Config: [Era 3 · train, 49 scoped docs · DERIVED] `score --gentol-check` on `r3-trainpredict`. Registered in `2026-10-05-general-tolerance-registration.md` before any split was touched. `docs/eval/gentol-check-train.json`.
Result: would_fix 12, would_break 63 (break rate 0.84, Wilson upper 0.91). Gate `fix ≥ 4·U·n` and `fix ≥ 20` both failed; also failed with the worst document removed. Of 93 matched rows read without a tolerance, gold holds the ISO-m value on only 17, a different printed tolerance on 45 (0 hold the table magnitude in another shape) and none on 16. Re-scored: +4.90 per doc, 0 of 6 weightings, precision 0.755 → 0.653.
Decision: Killed at the train gate, nothing shipped. Kept as measurement only (`app/eval/general_tolerance.py`); a test forbids any pipeline import. Dev and test were never scored with it.
Revisit if: gold's tolerance convention changes or the client confirms it. The finding is about this gold: missing tolerances are mostly tolerances the drawing prints and the read lost. Dev and test remain unseen for any re-registration.

## 2026-10-06 — Historical: shipped configuration measured on test (`r4-controltest`)
Config: [Era 3 · test, 11 scoped docs · MEASURED] shipped config (pad 24, policy stage 1) on GPU 0, 19 dumps, 0 failures. Registered in `run_gpu_queue.sh` `stage_why`. `docs/eval/r4controltest-scoped-summary.json`.
Result: 149.82 vs derived 149.73 (+0.09, ci95 [−1.55, 1.73], 3 of 6 weightings). Missed 108 exactly as registered, n_pred 354 (IN), auto-accept precision 0.6842 (registered > 0.6986, OUT), rate 0.1781 (IN). Pad 24's dev gain did not carry over: field_acc +0.027, 24 silent errors of 76 auto-accepted.
Decision: Quote the product as dev 118.73 / test 149.82, both measured. `r4-control` and `r4-controltest` were the controls until stage 2 shipped.
Revisit if: pad 24 is credited with a test improvement. It is cost-neutral on test, and field_acc rose on both splits while precision moved the other way.

## 2026-10-06 — Later: "High-confidence phantoms: ask the client what they leave unballooned, or learn that policy from train gold" (drop stage 2)
Config: [Era 3 · train select, dev validate, test confirm · DERIVED, then MEASURED] drop stage 2 candidates, each firing only on a row that would otherwise ship unflagged. Selected on train: `conf_below_099 + material_kind + tight_cluster`. Registered in `2026-10-06-phantom-drops-registration.md`. `docs/eval/drop-check-*.json`, `docs/eval/r5control*`.
Result: delivered precision train 0.577 → 0.888, dev 0.419 → 0.526, test 0.536 → 0.958. Matched-only precision up on all three. Cost of it: auto-accept rate dev 0.225 → 0.132, test 0.178 → 0.079, 29 correct values dropped per split; recall dev 0.717 → 0.666, test 0.630 → 0.569. Dev missed its band: 30 dev phantoms read at ≥ 0.99. Native runs `r5-control` (dev 117.87) and `r5-controltest` (test 148.82) reproduced the derived digests identically on all 37 aggregates.
Decision: Shipped as drop stage 2, with dropped rows going to the suggestion tray (ADR 0009). `r5-control` and `r5-controltest` are the controls for every later arm.
Revisit if: delivered precision is compared against the old matched-only figure. The two answer different questions. Test's 0.958 is on 24 delivered values, so it is thin.

## 2026-10-07 — Historical: parser fix for a positive second tolerance
Config: [Era 3 · train, dev, test · DERIVED] a second signed tolerance keeps its own sign (`parse_value("20 +0,2 +0,1")` returned `lower_tol = "-+0,1"`). `score --reparse-check`. Registered in `2026-10-07-parser-plus-lower-registration.md`, keep rule `would_break == 0` on all splits.
Result: `would_break` 0 on train (880 pairs), dev (207) and test (166). `would_fix` 0 on dev and test, and train's 6 are the earlier Ø-zone fix (those dumps predate it). No matched row in any split carries two "+" tolerances.
Decision: Kept as a pure correctness fix. It moves no current metric. It makes such gold rows renderable as training targets (`targets.render_target` now prints "5 +0,3 +0,1").
Revisit if: the expectation was a measurable gain. It was not, and the registered would_fix range was 0-3.

## 2026-10-07 — Later: "High-confidence phantoms: ask the client what they leave unballooned, or learn that policy from train gold" (VLM verifier)
Config: [Era 3 · train, 221 rows asked · DERIVED from `runner verify`] yes/no VLM verifier ("is the outlined region a callout an inspector would balloon?") on a 3× context crop, applied to rows delivered after stages 1 and 2. Registered in `2026-10-07-verifier-registration.md`. `docs/eval/verifier-check-train.json`, `docs/eval/verifier-profile-train.json`.
Result: control 196 delivered = 174 correct / 6 wrong / 16 phantom. All three thresholds (P(yes) < 0.5, 0.7, 0.9) raise delivered precision slightly and fail the matched-precision-must-not-fall rule: each drops 1-3 correct values and no wrong ones. 191 of 196 delivered values, including 14 of 16 phantoms, get P(yes) ≥ 0.9.
Decision: Closed at train selection, nothing shipped. Dev and test verdicts exist on the GPU host but were never priced, so both splits remain unseen for this question.
Revisit if: the client states or demonstrates what they leave unballooned. The surviving phantoms probably are real dimensions the client chose not to balloon, so no gold-free question about the drawing can see them. A classifier of the client's ballooning policy, learned on train gold, is a different experiment and was not run. The pilot grade may show whether these rows are what reviewers actually delete.
