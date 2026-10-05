# Policy arms — registered predictions (before any rule is priced)

Registered 2026-09-25, BEFORE `score --policy-check` was run on any split.
Plan: `docs/plans/2026-09-25-review-quality-arms-plan.md` (§1 is the
keep/revert rule and is binding; it is restated below so this file stands
alone). Rule code as registered: `app/pipeline/policy_rules.py` at the commit
that adds this file.

## 1. Protocol

| role | run | split | docs (scoped) | base cost | auto-accept precision / rate |
|---|---|---|---|---|---|
| select | `r3-trainpredict` | train | 49 | 132.37 | 0.4631 / 0.2336 |
| validate | `r3-cropctx` | dev | 15 | 131.87 | 0.5328 / 0.2347 |
| confirm | `r3-awqtest` | test | 11 | 165.18 | 0.4435 / 0.1747 |

`r3-trainpredict` predates `review.LOW_CONF` 0.8 (no `review_low_conf` in its
config) and uses crop pad 6; dev and test are current-threshold dumps.

**Flag rule KEPT iff, on train AND dev AND test:** cost falls; better under
6/6 `WEIGHT_GRID` weightings; auto-accept precision rises strictly;
auto-accept rate falls by ≤ 0.02. (recall/field_acc untouched — asserted.)

**Drop rule KEPT iff, on train AND dev AND test:** cost falls, 6/6; recall
falls ≤ 0.005; field_acc falls ≤ 0.02; escaped_rate does not rise;
auto-accept precision does not fall (None = fail); rate falls ≤ 0.02.

**Joint set:** kept rules priced together on dev and test must pass the drop
conditions plus precision rising; otherwise drop the rule with the smallest dev
gain and re-price.

A rule added after any price has been seen is not eligible on that split.

## 2. What the diagnosis already says (seen before registering)

`false_diagnosis` (Task 8), counts of false detections:

| | dev (346) | test (174) | train (655) |
|---|---|---|---|
| near_matched_gold | 157 | 107 | 387 |
| far_numeric | 158 | 36 | 179 |
| inside_matched + same_value_as_matched | 23 | 16 | 71 |
| empty_read | 3 | 15 | 13 |
| far_other | 5 | 0 | 5 |

**So the drop direction is registered as SMALL at best.** The categories a
gold-free duplicate rule can reach (inside/same-value/empty) are 7-15% of false
detections. The bulk looks like real callouts where gold has no balloon; no
rule here can tell those apart without gold. Caveat recorded: the match gate is
10% of the page diagonal (~5 cm), so "near gold" is a loose category.

## 3. Per-rule expectations

Base gates: `base_low_conf_reflagged` = **0 on dev and test** (enforced — a
current-threshold dump that needs re-flagging raises); on train it is the
unflagged 0.6-0.8 rows, **≈ 38** (35 escaped + 3 correct in that band).
`base.cost` on dev must equal **131.87** exactly and on test **165.18**; train
will differ from 132.37 by the normalisation (≈ −2.8, 35 escaped → flagged
for 3 correct flagged).

### Flag rules

| rule | expectation | mechanism assumed | damage counter |
|---|---|---|---|
| `nondim_kind` | **PASS** on all three. dev: ≈26 escaped newly flagged vs ≤3 correct → Δ ≈ −6.7; precision 0.533 → ≈0.65; rate −0.01 | kind predicts error: non-dimension rows read at 0.00-0.18 | `newly_flagged.correct` (≤ 1/4 of escaped) |
| `gdt_guessed` | PASS on dev and train (subset of the above); test has only 5 gdt rows — may be too small to move, PASS or no-op | the char_type of a symbol-less gdt row is a guess | same |
| `tall_box` | **FAIL** — rate guard. ≥80 px rows read ≈0.47-0.52, so the rule flags about as many correct rows as escaped ones: precision flat-to-slightly-up, rate falls by far more than 0.02 | a height band is a weak error predictor | `delta.auto_accept_rate` |
| `diameter_sign` | uncertain; lean FAIL (Ø rows are mostly read correctly; the 11+11 confusion is small against all Ø rows) | Diameter↔Distance confusion concentrates on Ø rows | rate |
| `multiline` | uncertain; lean FAIL | stacked text = clipped/swallowed neighbour | rate |
| `no_tolerance` | **FAIL** — flags many correct rows (gold often carries no tolerance) | none; registered as a negative control | cost, precision |
| `asymmetric_tol` | uncertain, small either way | tolerance-shaped output is where the read fails | precision |

### Drop rules

| rule | expectation | mechanism | damage counter |
|---|---|---|---|
| `empty_read` | **PASS** where it fires, small: dev −0.4, test ≈ −2.7 per doc | an empty read is never a matchable callout | `dropped.correct` must be 0 |
| `contained_duplicate` | small (≤ inside_matched: dev 10, train 35); lean PASS, but may drop a matched row whose fragment re-pairs | fragment of a larger same-kind read | `dropped` non-false entries |
| `repeated_value_nearby` | small (≤ same_value: dev 13); lean PASS | one callout read twice | same |
| `no_digit` | tiny (far_other ≤ 5) | a dimension read with no digit is not a dimension | `dropped.correct` |
| `theoretical_no_nominal` | tiny | — | — |
| `note_kind` | **FAIL — recall guard.** dev: −76 false units but 4 matched note rows become misses → recall −0.013 > 0.005 (cost itself likely falls ≈ −3.5) | not the closed `score_kinds` filter (that dropped gdt/surface too); registered to show the recall guard, not the cost, decides | `delta.recall` |
| `theoretical_kind` | **FAIL — recall guard** (9 matched rows on dev → recall −0.029) | same | `delta.recall` |

**Expected outcome overall:** the kept set is `nondim_kind` (possibly with
`gdt_guessed`, which it subsumes) plus `empty_read`, and perhaps one of the two
duplicate rules; dev gain ≈ −7, driven almost entirely by flagging. If
`nondim_kind` fails anywhere, the direction-1 premise (kind predicts error) is
wrong and must be written up as such.

## 4. OCR proposals — CPU go/no-go (registered before `--proposal-check` ran)

`score --proposal-check` on dev (`r3-cropctx`, 15 docs, 58 isolated misses).

* **Gates:** `scale_mismatch == 0` (else the geometry is wrong and coverage
  means nothing); `isolated == 58` (the identity gate raises otherwise).
* **GO iff `isolated_covered >= 12`** — a ceiling of −7.2 per doc before any
  false detection (each covered miss recovered and flagged saves 9).
* Expectation, stated so it can be wrong: tesseract finds dimension text on a
  clean 300 dpi drawing (a synthetic 40 px check found every horizontal
  callout), so coverage should be moderate — **~15-30 of 58** — and
  `far_from_gold` large (**hundreds**), because drawings are full of numbers
  the client never balloons. The unverified bound `9·covered − 2·far` is
  therefore expected NEGATIVE: the arm lives or dies on the VLM verifier
  rejecting far proposals, and must keep ≥ 18% precision to break even.
* NO-GO → revert the proposal code (`proposals.py`, `proposal_check.py`,
  their runner flag and tests) and record the numbers in CLAUDE.md §3.
