# Sindri — working notes for agents

Auto-balloons engineering drawings: detect inspection characteristics with a VLM,
read their values, and score the result against client gold in expected **reviewer
effort** rather than raw accuracy. `app/pipeline/` is the product; `app/eval/` is
the measurement harness. They are deliberately separate — eval never imports
pipeline internals that move under tuning.

## 1. Hard rules — the client corpus is under NDA

A guard hook (`~/.claude/hooks/sindri-guard.py`) enforces this. It is not
advisory, and it has been right every single time it fired.

* Protected root: `/home/clemi/sindri-client-data`. Full rationale in
  `docs/eval/DATA-HANDLING.md`.
* Only the reviewed CLI may touch it: `python3 -m app.eval.runner
  <probe|headers|ingest|split|predict|score|compare|summary|variants>`, plus
  `setup_client_data.py` and `sync_client_data.sh`.
* **`runner review-serve` and `review-tally` are the OPERATOR's, and are
  deliberately NOT in that allowlist.** They read a review deck `score
  --review-deck` writes inside the protected root, full of client text; only
  the counts-only tally in `docs/eval/` is for an agent. Do not add them to
  the guard. **The guard does not cover a plain `curl` to the running review
  app** — its session token is the protection, so never ask for, or accept,
  the link `review-serve` prints.
* **Single, unpiped, unchained commands only.** These are all denied and each one
  has actually cost time here: `cmd | tail`, `cmd && cmd`, `cmd > file`, a heredoc
  whose body mentions the root, and `ls` on the root itself. Run the sanctioned
  command bare, then post-process in a *separate* call that names no protected
  path.
* A bare `.pdf` anywhere in a command string is denied. Put throwaway scripts in
  the scratchpad and run them by path.
* Never read `*.pdf`, spreadsheets, `*.gold.json`, `*.pred.json`,
  `*.report.json`, `doc_id_map*`. Use `runner summary` — it is the only
  sanctioned view of a run: aggregate metrics, salted ids, no client values.
* Corpus layout (you cannot `ls` it; this is from `setup_client_data.py`):
  `corpus/{originals,stamped,excel}` + `gold/ runs/ reports/ meta/`.
  `originals` = clean drawings the pipeline reads. `stamped` = ballooned
  drawings gold positions come from. **They are different files.**
* **Never widen the guard.** Re-verify after any change:
  `bash ~/.claude/hooks/test-sindri-guard.sh` → `32 passed, 0 failed`.

## 2. Where things stand

**Read `docs/plans/2026-09-26-session-handoff.md` first — it is the current
state of play: the policy arms shipped (flag/drop rules, dev 131.87 → 118.73
DERIVED), OCR proposals closed, and what runs next.** It supersedes
`2026-09-25-session-handoff.md`. For device setup on a fresh clone,
`docs/plans/2026-09-17-session-handoff.md` §1 still applies. Everything below
is the durable summary.

**THE PIPELINE NOW FLAGS AND DROPS BY POLICY, BY DEFAULT** (2026-09-25/26,
`docs/plans/2026-09-25-policy-arms-result.md`). `app/pipeline/policy_rules.py`
holds gold-free post-read rules; three are ACTIVE — flags `nondim_kind`
(gdt/surface/note/boxed rows, which read at 0.00-0.18) and `no_tolerance` (a
dimension with no tolerance read; gold carries the general tolerance on nearly
every row, so it is wrong by construction), and the drop `contained_duplicate`.
They are recorded in `RunConfig.extra` (`flag_rules`/`drop_rules`), so every
dump predicted from now on differs in config from every `r3-*` dump.
**MEASURED on dev (2026-09-27, `r4-control`): 131.87 → 118.73, −13.13, ci95
[−18.0, −8.7], better on 15 of 15 documents and under 6 of 6 weightings**;
`escaped_error` 64 → 17, auto-accept precision **0.53 → 0.80**, recall
unchanged, field_acc 0.5291 → 0.5381. `experiment.py`: WIN, best arm on record.
It reproduced the DERIVED prediction (`score --reapply-policy`) on every digest
aggregate to the decimal — the offline pricing is exact, not an estimate.
**Test is now MEASURED too: `r4-controltest` = 149.82** (2026-10-06,
`docs/plans/2026-10-06-r4controltest-result.md`; derived was 149.73). Missed
reproduced exactly (108), but auto-accept precision is **0.684** (24 silent
errors of 76 auto-accepted) and pad 24's dev gain did not carry over to test
(+0.09, 3/6, precision −0.014). **`r4-control` (118.73) is the dev control and
`r4-controltest` (149.82) the test control for every later arm — never compare
a new arm against an `r3-*` control** (`compare_runs` warns when the review
policy differs; against a `--reapply-policy` report that warning is spurious).

**THE CLIENT RANKS PRECISION OVER RECALL** (operator, 2026-10-05): every
delivered value must be true, and that matters more than finding every value.
`weights.json` (miss 10 > escaped 5 > false 2) encodes the opposite, so judge
arms on auto-accept precision and silent errors first, then false detections,
then recall, until the client's own weights arrive.

**AND THE MATCHED-ONLY PRECISION HID MOST OF THE PROBLEM.**
`auto_accept.delivered_precision` (2026-10-06) also counts false detections
that ship UNFLAGGED, which are phantom values nobody is asked to check: **dev
0.419** (70 correct, 17 wrong, **80 phantoms**), **test 0.536** (52 / 24 / 21),
against matched-only 0.805 / 0.684. Under today's weights flagging a phantom
changes nothing (false = 2 either way), so review cost is blind to the client's
first priority. Quote delivered precision alongside the matched one.
Caveat: "phantom" means "not in gold", and gold balloons a strict subset of
what the client inspects, so some may be real dimensions the client chose not
to balloon. They are still balloons the client would have to delete.

**PHANTOM DROPS SHIPPED (2026-10-06, `docs/plans/2026-10-06-phantom-drops-result.md`)**
as drop STAGE 2: `conf_below_099 + material_kind + tight_cluster`, each firing
only on a row that would ship unflagged. Registered, selected on train, passed
dev and test (DERIVED, exact): **delivered precision train 0.577 → 0.888, dev
0.419 → 0.526, test 0.536 → 0.958**, matched precision up on all three. The
price: auto-accept rate dev 0.225 → 0.132, test 0.178 → 0.079, 29 correct values
per split dropped. Dev missed its band: **30 dev phantoms read at ≥ 0.99**, out
of reach of any confidence rule, and they are the verifier's target.
**Drops are STAGES** (`ACTIVE_DROP_STAGES`), each judged on what the previous
stage left, because that is how each was priced; `active_drop_rules()` is for
recording only. **Flags must precede drops** now, since stage 2 reads
`needs_review`. **MEASURED natively: `r5-control` (dev 117.87) and
`r5-controltest` (test 148.82) reproduce the derived digests identically on all
37 aggregates each. They are now the dev and test controls for every later
arm** (r4-* are one stage behind).

**`read-lora-v1` IS NOW HARMFUL UNDER THE SHIPPED POLICY — deployment CLOSED**
(2026-10-05, `docs/plans/2026-10-05-read-adapter-repricing-result.md`;
DERIVED, `--reapply-policy` on stored dev dumps, pad 6). The scoped adapter's
old −3.40 / −2.47 became **+3.33 (NF4) and +4.27 (vLLM)**, both significant,
0/6 weightings, auto-accept precision 0.75 → 0.66. Mechanism, reconciling
exactly: the adapter fills missing tolerances with plausible wrong ones, so
`no_tolerance` stops firing and those rows go from flagged to silently wrong
(`flagged_error` −20/−23, `escaped_error` +13/+14). **A flag rule keyed on a
field being ABSENT is defeated by any model change that fills the field, right
or wrong. Price every read-stage arm under the active rules.** Side findings:
NF4's serving overhead is now +0.20 (was +3.53); the unscoped / merged shape
costs +17.20, and `contained_duplicate` absorbs none of its +183 false
detections. **A merged checkpoint cannot keep read/detect scoping**: it has no
base weights for `detect_regions`.

**AUTO-ACCEPT PRECISION IS THE GUARD REVIEW COST LACKS.** flag=1 < escaped=5
means flagging more always lowers cost — flagging every matched row cost 223 vs
406 on dev and automates nothing. The digest's `auto_accept` (`precision` =
correct / (correct + escaped_error), `rate` = correct / n_gold) and
`experiment.verdict`'s two guards (precision must not fall, rate must not fall
> 0.02) catch it; precision alone cannot see RANDOM flagging, the rate can.

**`score --policy-check` prices a flag/drop rule EXACTLY in CPU seconds**
(`app/eval/policy_check.py`): it applies the rule to stored dumps and
re-scores, matching included, against a LOW_CONF-normalised base. It refuses
dumps that already carry active rules. `score --reapply-policy`
(`app/eval/reapply.py`) scores stored dumps as today's post-read code would —
re-parse, re-derived flags, active rules — and marks the report and digest
`reapplied_policy`, i.e. DERIVED. **`false_diagnosis`** in the digest
partitions false detections (sibling of `missed_diagnosis`): duplicates of a
match are only 7-11% of them; the bulk sits near gold that paired elsewhere
(45-61%) or far from any gold (21-46%) — unballooned callout-like text no
gold-free rule reaches.

**On a NEW MACHINE, run `./install-hooks.sh` before your first commit.** The
client-data pre-commit guard is versioned in `hooks/` and wired up by that
script; it used to live only in `.git/hooks`, which git neither clones nor
tracks, so a fresh checkout had no guard and nothing said so. Three more things
live outside the repo and are listed in §1 of the handoff — the protected-roots
file, the agent's Bash guard, and **`~/.claude/sindri-doc-salt`, which must be
copied by hand and never committed**: a different salt silently stops every
hashed id in `docs/eval/` from joining to what is already published.

**Scope policy (2026-09-09): single-sheet drawings at a size the renderer
handles at full resolution** — `score --max-pages 1 --dpi 300
--exclude-clamped`. Both OFF by default, so pre-policy numbers keep their
meaning, and `_check_comparable` refuses a scoped report against an unscoped
one. It keeps 15 of 20 dev documents. Under it, production is **133.93**, recall
**0.7170**, missed **28.3%**, silent-wrong **22.8%**.

Branch `worktree-eval-harness`, PR #2. Suite: **1233 passed, 2 skipped** (the 2
skips need `RUN_GPU_TESTS=1` on a GPU host). **`tesseract` is a device
prerequisite** — without the binary six tests fail as `TesseractNotFoundError`
and read as broken code. `SCHEMA_VERSION` = 1 — do not bump it. Split frozen
at `6d174d5e4f1b9228` — do not regenerate it.

**THE HYBRID ARM IS MEASURED AND LOST, and its finding is the biggest of the
campaign** (2026-09-11, `docs/plans/2026-09-11-hybrid-arm-result.md`;
prediction registered beforehand in `2026-09-09-hybrid-arm-prediction.md`).
32B localising + 72B transcribing: **173.93, +40.00**, ci95 [26.07, 56.27],
significant, **0 of 6 weightings better**, robust.

**BOX FRAMING DOMINATES READ ACCURACY, BY ~5x OVER THE READER'S WEIGHTS.** Hold
the reader and change the boxes: `field_acc` 0.4798 -> **0.2739** (-0.206). Hold
the boxes and change the reader (32B -> 72B): **+0.013**. The best reader change
ever measured on fixed boxes, `read-lora-v1`, is +0.039. And the boxes are in
the right PLACE -- `misplaced_matches` 19.7% vs 19.9% -- so this is framing, not
the matcher pairing gold with distant junk. `misplaced_matches` is the aggregate
that settles that question; read it before proposing any box-related arm.

**A better reader on bad boxes is HARMFUL**: 32B -> 72B on the same crops moved
8 rows out of `flagged_error` and 5 into `escaped_error`, because the stronger
model is more confident on a crop it is misreading. It removes the warning
without fixing the value.

**So the 32B's recall advantage is recall of BOXES, not of values.** It finds 18
more gold rows and delivers **41 fewer fully-correct ones**, while shipping 34
more silent errors -- a failure on the product goal that no reweighting can
rescue. Never quote "recall 0.7749 vs 0.7170" as a quality improvement.

**THE CROP ARM WON, and it is the campaign's first FREE win** (2026-09-15,
`docs/plans/2026-09-15-crop-context-arm-result.md`). `SINDRI_CROP_PAD` 6 -> 24
px: **131.87 against 133.93**, better under **6 of 6** weightings, `field_acc`
0.4798 -> **0.5291**. That **+0.049 is larger than `read-lora-v1`'s +0.039**,
which needs a trained adapter, an NF4 base costing +6.35 to serve, and a
deployment route blocked three failures deep. This is a constant.

**Cleanest single-variable arm in the campaign**: detection came back
BIT-IDENTICAL — `n_pred` 569, `missed` 88, `false_detection` 346, `pred_kinds`,
`matched_by_pred_kind` and `missed_diagnosis` all term for term — where even
`loraread`'s gate moved `false_detection` by one. The whole delta is **7 silent
errors converted for 4 extra flags**, reconciling exactly to `5(-7) + 1(+4) =
-31`, ÷15.

**The registered mechanism was the smaller half.** Of the 11 rows that became
fully correct, ~4 are tolerance-only; the rest were wrong in other fields, and
`wrong:nominal`, `wrong:char_type` and all-four-wrong all fell too. The crop was
starving the reader of context generally. **Together with the hybrid this is the
campaign's central finding: the INPUT to the read holds the remaining quality,
not the model doing the reading** — degrading the boxes costs -0.206, improving
what the reader sees of the same boxes buys +0.049.

**SHIPPED 2026-09-16: `_CROP_PAD` is 24.** The dose response settled it and the
curve is **peaked** — `cropctx48` (pad 48) reads MORE accurately and costs MORE:
132.73 with `field_acc` 0.5426 and `escaped_rate` 0.2154, against pad 24's
131.87 / 0.5291 / 0.2058. Three rows moved into fully-correct and three into
`escaped_error` between the doses, and a silent error is worth 5 where the flag
it displaced was worth 1. **The hybrid's finding from the other side: extra
context makes the reader more confident, and on a row it is still misreading,
confidence removes the warning without fixing the value.** Do not test 96.

**`active_crop_knobs` compares against a FROZEN `_BASELINE_CROP_KNOBS` of
6/40/3.0, not against the current default** — so a run at today's shipped 24
records its knobs out loud, and `SINDRI_CROP_PAD=6` still reproduces every
pre-2026-09-16 dump config and all. Comparing against the default instead would
make a pad-24 run look identical in `RunConfig` to every pad-6 dump on disk and
`_reusable_dump` would reuse them straight across the change.

**THE REGISTERED DECISION RULE FOR THAT DOSE WAS WRONG, and it is the lesson.**
It said "`field_acc` up AND `misplaced_matches` <= 46 -> take 48". **Both
conditions held** and 48 is the worse setting. The rule failed because
`misplaced_matches` never moved at all (42 at both doses) while the real damage
landed in the flagged/silent split, which the rule never looked at. Second
registered gate in three arms whose PREMISE was the flawed part.
**A damage counter must be one the treatment can actually move — check it
responded to the PREVIOUS dose before registering it.** `misplaced_matches` was
already flat at 44 -> 42, so the evidence was on the table beforehand.

**THE LARGEST REMAINING READ DEFICIT IS TALL BOXES** (2026-09-16,
`docs/plans/2026-09-16-crop-height-diagnostic.md`). **126 of 223 matched rows
(56.5%) are boxes >=80 px tall and they read at 0.3968**, against 0.5909 for the
40-80 px band. At 300 dpi 80 px is 6.8 mm, so these span more than one line — a
nominal with stacked tolerances, a multi-cell GD&T frame, or a swallowed
neighbour.

**And the crop-pad win was almost entirely theirs**: `>=80` went 0.3968 ->
0.4724 -> 0.4921 across pads 6/24/48 (**+0.095**) while 40-80 moved +0.011.
**That says WHICH WAY tall boxes are wrong — they are CLIPPED, not
over-filled**, because more context helps them monotonically and was still
helping at 48. It also explains the dose response: the optimum differs by box
height and a single global pad splits the difference.

**The lead is a HEIGHT-DEPENDENT pad**, and its damage counter is `escaped_error`
(71 -> 64 -> 67 across the three pads — responsive, unlike `misplaced_matches`
which was flat at 44 -> 42 -> 42 and is disqualified by §4). Still missing: the
80 px threshold is a bucket boundary, not a measured knee — one more GPU-free
re-score with finer boundaries would place it.

**15% OF ROWS PRODUCE 41% OF THE SILENT ERRORS, AND THEY ARE WRONG BY
CONSTRUCTION** (2026-09-17, `docs/plans/2026-09-17-read-accuracy-by-kind.md`).
`read_accuracy_by_kind` on the shipped config: `dimension` 189 rows at **0.6085**,
`gdt` 17 at **0.1765**, and `theoretical` (9), `surface` (4), `note` (4) at
**exactly 0.0000**. The 34 non-dimension rows are 15.2% of matched and carry 26
of the 64 escaped errors — **3 to 5x more likely to ship silently wrong per
row**. `dimension` alone reads at 0.6085 against the 0.5291 headline, so a sixth
of the corpus was dragging the whole number down.

**THE LABELLING COLLISION WAS REAL AND FIXING IT IS WORTH EXACTLY ZERO**
(2026-09-22, `docs/plans/2026-09-22-theoretical-parser-result.md`). An earlier
note here read "It is a labelling collision, not a read failure"; that is
**refuted, and it was the premise of a whole arm.** The collision is exactly as
diagnosed — `gold -> Theoretical` totals **exactly 9**, `gold -> Note` exactly
4, `parser.parse_value` set `char_type = THEORETICAL` unconditionally for
`hint == "theoretical"`, `char_type_equal` is strict equality, **gold's
vocabulary has no "Theoretical"**, so those rows could never score correct. It
was fixed, priced with `--reparse-check`, and the bound came back
**`would_fix 0, would_break 0`**. Reverted at `89e0375`.

**It was MASKING a second fault, not standing in for one.** `identical` fell
223 -> 214, exactly the 9 rows and no others, so the edit reached everything it
aimed at; none flipped to correct because a `char_type` fix only pays when
`nominal` and both tolerances already agree, and on these rows they do not.
**A correctness fix pays only when it is the LAST fault on the row** — the
`r3-tallpad` lesson from the other direction, and neither `read_accuracy_by_kind`
(0.0000) nor the escaped share (8 of 9) could tell "wrong for one reason" from
"wrong for two". Do not re-propose a char_type fix for this bucket.

**`read_accuracy_by_kind.wrong_fields` now answers that question up front**
(`5262672`): the signature histogram of each kind's wrong rows. `theoretical`
has **0** char_type-only rows, 7 of 9 wrong in all four fields, and 86% of its
predictions are false detections. It is a detection-quality bucket and was
never parser-reachable. **`gdt` has 8 of 14 wrong rows wrong in `char_type`
ONLY, at least 5 of them escaped: at least −1.67 at today's weights, the size of
the crop win, with no GPU.** It is the next lead. Which SIDE is wrong is
undetermined (gold labels with no recognised word vs `_gdt_type`'s `Flatness`
default); see §5 of `docs/plans/2026-09-22-theoretical-parser-result.md`.
**Read `wrong_fields` before proposing any fix to a kind.**

**The prediction side is now settled: all 8 are GUESSES.** `char_type_only_confusion`
says every one was predicted Flatness, and `--reparse-check`'s
`gdt_char_type_only` says **0 of 8** transcriptions hold a symbol the parser
knows — so every Flatness is `_gdt_type`'s default. Gold side: 3 labels the
scorer reads as Position/Parallelism, 5 it reads as nothing. **What decides the
fix is an OPERATOR review** (dropped symbol vs unmapped look-alike, and what the
5 labels mean). It is a **local review app** (2026-09-25,
`docs/plans/2026-09-25-gdt-review-ui-design.md`): `score --review-deck` writes
the deck, the operator runs `review-serve` and answers one screen per row with
drawing crops, and Finish writes the tally. The decision table is registered in
`docs/plans/2026-09-22-gdt-review.md`. **The deck holds client text: an agent
never reads it, and the guard blocks every file route to it.**

**DONE 2026-09-25, and the probe overturned the review's headline**
(`docs/plans/2026-09-25-gdt-review-result.md`). The tally said the reader
transcribes every symbol and 4 rows were parser-fixable. A closed-vocabulary
probe (`vocab_probe`, in `score --reparse-check`, per deck row) showed the
characteristic's symbol is ABSENT from at least 6 of 8 transcriptions —
nothing before the value on 3, only the Ø zone sign on 3 — so it is a
read-stage majority and **there is no profile-symbol parser arm**. What it
exposed instead: `_gdt_type` defaulted Ø-zone frames to Flatness, which never
has a cylindrical zone. **`49f0629` defaults them to Position: would_fix 2
(exactly the two rows registered, both escaped), would_break 0 → kept, −0.67
review cost on dev, DERIVED from the re-parse** (131.87 → 131.20, field_acc
0.5381, escaped_rate 0.1994); quote it as derived until a predict run scores
it. **Lesson: cross-check operator answers against a closed-vocabulary probe
before acting on them, and write review questions that name the known traps
(Ø is usually the zone sign; stacked frames carry two characteristics).**

**These are now TWO separate closed dead ends, and they close for different
reasons.** §3's synonym-map entry is about GOLD's vocabulary and closed because
the map was matching the wrong way. This one is the PREDICTION side and closed
because the rows have a second fault. What §3 still points at as live is
neither: the symmetric `Diameter <-> Distance` confusion over a leading Ø, 11
each way, which is a read-stage fault and Rung 3's target.

**`app/eval/reparse.py` prices a parser change in CPU SECONDS** from dumps
already on disk — `score --reparse-check`, with `would_fix - would_break` as the
bound. Use it before any parser arm; it has now closed one for free. **Read the
gate first**: `identical == n_pairs` on an UNMODIFIED parser, or the
reconstruction is broken and no bound means anything. It held exactly (223/223)
on 2026-09-22.

**But it prices the PREDICTION path only, and a parser edit moves two paths.**
`targets.render_target` verifies through `parse_value` under the same hint
(`_verified`), so a parser change also decides which gold rows are renderable as
TRAINING targets — which `reparse.py` never re-parses and cannot see. A zero
bound does not mean "this change does nothing". Measure the target path on its
own terms: `UnrenderableRow("not_round_tripping")` counts on a train-split
build, with and without.

**THE WORST CROP-HEIGHT BAND IS A KIND EFFECT, NOT A HEIGHT ONE** (2026-09-17).
`read_accuracy_by_crop_height` now reports composition, and `80-120` px — the
worst band on the page at 0.242, which no pad could touch — is the ONLY mixed
band: **61% non-dimension** (gdt 8, theoretical 7, surface 3, note 2 of 33)
against 91-97% dimension in every other band. 80-120 px is simply the size of a
GD&T frame or a surface-finish symbol. **The registered hypothesis that `>=200`
recovers because those are gdt/note boxes on their own prompts is REFUTED** —
that band is 28 of 29 plain `dimension`, reading at 0.586 and insensitive to
every pad tried.

**Field accuracy BY KIND is now reported** (`read_accuracy_by_kind`, built
2026-09-17, and since 2026-09-22 with `wrong_fields` per kind). It is what
routed the `theoretical` arm, and then what explained why that arm was worth
zero.

**THE TEST SPLIT IS MEASURED, AND DEV WAS OPTIMISTIC BY +31.25**
(2026-09-15, `docs/plans/2026-09-15-test-split-result.md`). Production's exact
configuration on the frozen test split: **165.18** against dev's 133.93, recall
**0.6301** against 0.7170, `field_acc` **0.3804** against 0.4798, missed
**37.0%** against 28.3%. **Quote the product as a RANGE — ~134 on typical
single-sheet drawings, ~165 on structurally atypical ones — never the dev number
alone.**

**But read the caveats before repeating the 165.** It is 11 documents, it is NOT
a comparison (different doc set, so no `ci95`), and **the test split is
deliberately adversarial rather than a sample**: `splits.py` forces the
structurally atypical `variants` into it, and 6 of its 19 predicted documents
are multi-page (32%) against 1 of 20 in dev and 8 of 99 corpus-wide. The 11 that
survived the policy carry 28% more gold values per document than dev's 15.

**About a THIRD of the dev/test gap is an artefact of how unlocated gold is
priced** — an earlier note here said "over half"; that estimate was made before
the denominator existed and is superseded. `missed_unlocated` is **31 of 292
test gold rows against 11 of 311 on dev**, at `w=10` each, and on located gold
only the recall gap narrows from 0.087 to **0.038**.

**The mechanism, measured 2026-09-15: value matching rescues 1 unlocated row in
12** (0 on the NF4/vLLM arms). That does NOT argue for excluding them. An
unlocated row is not unreachable — the characteristic is still printed on the
drawing, and an exact nominal match costs 0.0 in `matching.py`, cheaper than any
geometric pair, so a correctly-read row WOULD pair. Eleven of twelve failing
means the pipeline did not produce those values, which is a real miss.

**What IS a distortion is the price.** A misread on a LOCATED row pairs
geometrically and costs 5 as an escaped error; the identical misread on an
unlocated row cannot pair and costs 10 as a miss. So unlocated gold converts
read failures into misses at double weight. Bounding it by charging those rows
at `w=5`: dev 133.93 → 130.27, test 165.18 → 151.09, gap −31.25 → **−20.8**.

**GOLD COVERAGE, measured corpus-wide 2026-09-15** (`ingest --summary` to a
throwaway `--out`): gold is **100 documents / 3594 sheet rows / 3103 balloons**.
**621 rows have no usable position, and the decomposition is exact: 491 with no
balloon at all + 130 whose balloon is on a later page.** By kind: `note` 401,
**`dimension` 201**, unknown 19 — and only the dimension bucket is scored, so
**201 of 2489 scored gold rows (8.1%)** have no position. **Dev's 3.5% is less
than half the corpus rate and test's 10.6% is close to it, so DEV is the outlier
on this axis.** `pdf_only_total` is 0: every balloon has a sheet row, so the
client balloons a strict SUBSET of what they inspect — `unlocated_char_types`
lists real dimensional characteristics among the unballooned (Abstand 42,
Distance 26, Diameter 11). **`recovered_by_cv_total: 0` means NOT ATTEMPTED** —
it is gated on `ingest --cv`. The digest's new `gold_coverage` key
(`unlocated_gold`, `carried_by_value`, `not_measured`) says how many of those
rows `matching.py` pairs by value anyway; it is a SIBLING of `missed_diagnosis`
because those three buckets must keep partitioning `missed`. **Re-score any run
to populate it — old reports report `not_measured`, never 0.**

**What is NOT explained away: `field_acc` 0.4798 -> 0.3804**, computed on
matched rows only, so unlocated gold cannot touch it. A 21% relative drop in
read quality on unfamiliar templates — and exactly what the crop/hybrid finding
predicts, since an unfamiliar layout yields a worse crop without the model being
any worse at reading.

**Every future arm's headline must say which split it is from.** The dev number
is not wrong; it answers a narrower question than a reader assumes.

**The read crop is a recorded knob** (`SINDRI_CROP_PAD` / `SINDRI_CROP_MIN_H` /
`SINDRI_CROP_MAX_UPSCALE`, `extract.active_crop_knobs`). All three keys are
recorded or none, relative to the frozen baseline above. `app/train/dataset.py`
resolves the SAME knob — a training crop that differs from an inference crop is
the failure its docstring exists to prevent. **`_MIN_CROP_H` (40),
`_MAX_UPSCALE` (3.0) and `boxes.tighten_to_ink`'s own `pad=3` are still
untested**, and each is a separate variable. **They also cannot be PROPOSED
until the digest's `read_accuracy_by_crop_height` has numbers in it** — it is
the bucket §2 and §4 require, it is GPU-free, and if short crops are not
over-represented among wrong rows it closes the whole family for nothing.
Re-score any run to populate it; older reports say `not_measured`.

**What runs next and why, including what was rejected:**
`docs/plans/2026-09-14-next-steps-decision.md`.

Rung-0 baseline (dev split, 20 docs), the current reference:
`mean_review_cost=173.05 micro_recall=0.646 micro_precision=0.371`,
`field_acc=0.3799`, `escaped_rate=0.2600`,
`missed=169 (contended 82 / isolated 74 / unlocated 13)`, `false_detection=522`.

**It was 174.30 / 0.3636 until 2026-09-02**, when a char_type scoring bug was
fixed (see §3's note on the synonym map). Same dumps, corrected scoring; every
arm was re-scored the same way and **no verdict flipped**. Numbers quoted from
before that date are one policy behind — check the date before comparing.

An older `245.30 / 0.350` appears in git history and in `docs/eval/render150-*`.
That number was measuring a coordinate bug, not the model. Do not quote it as the
baseline.

**Rung 1 and Rung 2 are closed: seven arms, seven losses.** Every detection knob
and both prompt levers were tested and all lost — and neither prompt arm moved the
bucket it targeted. Treat the committed configuration as tuned, and do not propose
another knob or prompt without a mechanism that predicts which bucket moves and
why. Evidence: `docs/plans/2026-08-21-direction-run-findings.md` (detection) and
`docs/plans/2026-08-24-rung2-reading-quality.md` (prompts, plus the Phase A
diagnostics that route the residual).

**Rung 3 WON on its second arm — the first win of the campaign.**
`r3-loraread` (the adapter scoped to the read pass) vs its matched control
`r3-nf4control` (176.40): **172.00, −4.40**, `ci95 [−7.9, −1.2]` excluding zero,
better under **6 of 6** weightings, no `compare_runs` warnings. `field_acc`
0.3730 → **0.4119**, `escaped_rate` 0.2579 → **0.2096**, `escaped_error`
123 → **100**. Cost reconciles exactly: `10(+1) + 5(−23) + 2(+1) + 1(+15) = −88`,
÷20 = −4.40 — **the whole win is 23 silent errors converted for 15 extra flags.**

**But it is NOT shippable yet: 172.00 is +1.95 WORSE than the 170.05 production
serves**, because the NF4 base an adapter must be served on costs +6.35 by
itself. That is what the merge-and-requantise route (§3, `merge_lora.py`, stages
`loramerged`/`mergedcontrol`) exists to remove. Full writeup:
`docs/plans/2026-09-07-rung3-loraread-result.md`.

**The first arm, `lora72bnf4`, was void — it never tested the read stage.**
`resolve_adapter` wraps the whole model in `PeftModel`, so `detect_regions` ran
through adapted weights too and `false_detection` went 607 → 931 (+10.00
overall). `detect_regions` now generates inside `VLMBackend._base_weights()`.

**The scoping gate passed, and taught a lesson about the matcher.** `n_pred`
returned to exactly 926, with `dimension`/`gdt`/`surface`/`material` bit-identical
in both matched and false counts. But `false_detection` came back 608, not the
registered 607: **matching is not purely geometric.** `matching.py:82` gives a
`value_bonus` when a prediction's parsed `nominal` equals gold's, and
`matching.py:68-76` matches an `unlocated` gold row by value similarity ALONE, so
read text reaches the matcher through `nominal`. 25 predictions moved
`note`→`theoretical` (`extract.py:251` relabels on the READ), and one unlocated
row lost its partner. **The sound detection-identity gate is `n_pred` exact PLUS
per-kind matched/false identity on kinds not subject to read-driven relabelling.**

**The finding worth more than the verdict:** `missed_isolated` 75 → **43**. Rung
1 threw render resolution, tile size and both merge knobs at isolated misses and
this file records `isolated` as "provably untouched". The detector's WEIGHTS
move it; its knobs and prompts never did.

The read stage learned exactly what it was trained on, and **this survives the
scoping**: `dropped_tolerances` 95 → 33, `missing:*_tol` −101 combined — but
`wrong:*_tol` +88 and `spurious:*_tol` +23, close to a wash on raw counts. It
learned to always emit tolerance-SHAPED output, because `render_target` renders
an explicit `+x -y` whenever gold has one. **That is now the largest identified
read-stage fault, and it lives in the TARGETS, not the serving.** The named
`char_type` target moved only half: `Distance→Diameter` 7 → 2, but
`Diameter→Distance` unchanged at 11.

**Corpus policy (2026-09-07): score and train on single-sheet drawings only.**
`render_page` takes `page_index=0`, so gold on sheet 2 was always an
unrecoverable miss at `w=10`. Use `score --pdfs <dir> --max-pages 1`. It is OFF
by default because all four reference numbers were scored unfiltered, and
`_check_comparable` already refuses a filtered report against an unfiltered one —
so **every arm must be re-scored under the same setting before its delta means
anything.** `probe --summary` reports the policy over the WHOLE corpus —
`multi_page_docs`, `render_clamped_docs`, `supported_docs`, `excluded_docs` —
because the clamp is a pure function of page size, dpi and the pixel budget and
needs no gold. **Measured 2026-09-11 over all 99 client drawings: 75 supported
(75.8%), 19 oversized, 8 multi-page, 3 both, 24 excluded.** The OVERSIZED
exclusion is the larger one by 2.4x and every earlier statement of scope missed
it, because only `multi_page_docs` existed. The dev split is representative on
both axes (4 of 20 clamped = 20.0%; 1-2 of 20 multi-page), which is what lets
the dev numbers be quoted as the product's. `render.effective_dpi()` is the
public entry point probe asks; do not copy the budget into `app/eval`.

**Rung 3 background.** The 72B trains at 4-bit NF4 in
**38.8 GB on one H100** — gate passed. Its GPU phase is done: the dependency
change to the inference image is proven safe (AWQ reproduces the baseline to full
float precision, 20/20 deltas `0.0`), and serving on NF4 costs **+6.75** review
cost, so a LoRA served that way must recover 6.75 before reaching parity with
production. Plan: `docs/plans/2026-08-27-rung3-lora-plan.md`; design:
`docs/plans/2026-08-27-rung3-lora-design.md`.

**The data-owner decision is GRANTED** (2026-09-02): rendered target values may
be pushed to the GPU host for training. The adapter `read-lora-v1` is TRAINED
(2026-09-04) from 731 verified pairs — 645 train / 86 validation over 47 / 7
documents, r=8, best checkpoint chosen on a by-document holdout at epoch 2
(`eval_loss` 0.2951 → **0.2816** → 0.2876, so epoch 3 was already overfitting
while train loss kept falling to 0.21). Both arms are registered and running.

**Both GPU-free wins from handoff §6 are banked**, and only one paid what was
predicted:

* `review.LOW_CONF` 0.6 → 0.8: **−3.00**, exactly as derived, and now **measured**
  by a fresh predict run (`r3-awqcontrol`, 2026-09-03). The 0.6–0.8 band was 18
  matched pairs, 100% wrong, zero correct.

  **So there are two AWQ reference numbers and they differ only by this
  threshold.** `baseline-dev` = 173.05 is the pre-change scoring reference;
  **`r3-awqcontrol` = 170.05 is what current code produces**, and it is what a
  new arm must be compared against. Runs are told apart by
  `config.extra.review_low_conf`, absent on every dump predicted before
  2026-09-02. The gate result: cost −3.00 with recall, `n_pred`, `missed`,
  `false_detection`, `correct`, `field_acc` and every field aggregate
  **bit-identical**; only 15 rows moved escaped → flagged. That also
  re-confirms decoding determinism on a run taken 9.5 h later.

  The NF4 side has the same pair and passed the same way: `r3-base72bnf4` =
  179.80 is pre-change, **`r3-nf4control` = 176.40 is current code**, delta
  −3.40 over exactly 17 rows, `field_acc` 0.3730 both sides, and `micro_recall`,
  `n_pred`, `missed`, `false_detection`, `field_failures`,
  `field_failure_modes` and `char_type_confusion` all bit-identical. So BOTH
  quantisations hit a pre-registered prediction to the decimal.
* The `char_type` rows: predicted ~−4.6, **measured −1.25**. The premise was
  wrong; see §3.

## 3. Measured dead ends — do not retry these

Each was implemented, measured, and reverted or rejected. Re-deriving them costs
GPU days.

* **Render resolution / pixel budget.** 80 → 150 MP un-clamped two sheets to full
  300 dpi and changed nothing: isolated misses 251 → 252, review cost *worse*
  under all six weightings. Reverted. Also do not build tiled rendering on the
  premise that resolution recovers misses.
* **Maximum-cardinality matching.** Recovered 26 misses by destroying 27 correct
  pairings; field accuracy on matched rows fell 36.4% → 25.4% while review cost
  *fell*. Kept only as the `--assignment max_cardinality` diagnostic. Never the
  default.
* **Filtering predictions to `score_kinds`.** Would delete 61 of 308 matches that
  non-`dimension` kinds legitimately make, because gold's `dimension` bucket
  includes GD&T and surface callouts (`normalize._DIMENSION_WORDS`).
* **The `merge_adjacent` knobs, in either direction.** Break-even is 5.0 false
  detections per recovered miss (`miss=10`, `false=2`). Measured 6.50 at
  `merge_y_gap` 20→8 and 8.75 at `merge_max_lines` 2→1 — above break-even at both
  doses and *worsening* as merging is reduced, so no intermediate setting wins.
  Also: only 8 of 82 contended misses are merge artefacts, and `isolated` is
  provably untouched by merge knobs (74 in all three arms).
* **Detect tile size (`VLM_TILE=768`).** Found nothing (`n_pred` 830→833, `missed`
  *up* 2, recall *down*), moved 16 gold rows contended→isolated, and cost
  −0.0662 field accuracy on matched rows plus +0.0336 `escaped_rate` — the worst
  arm measured. Damage concentrates 12× on the render-clamped sheets. This is a
  *different* lever from the render pixel budget above; both ends of the
  resolution family are now closed.
* **The read prompt, toward callout selection** (`readcenter`, +0.90 cost,
  `field_acc` −0.0162). Detection came back bit-identical, so it was an isolated
  test of the read prompt — and its target bucket, `misread.misplaced`, was
  **provably untouched at 64 → 64**. Naming the centre callout recovered none of
  them, so those rows are not an ambiguity about *which* callout to read.
* **The detect prompt, toward tighter boxes** (`detectbox`). `experiment.py` once
  called it a WIN on a −0.25 cost delta; it is not. Not robust, `ci95` spanning
  zero, both `compare_runs` guards fired, it destroyed 18 legitimate
  `gdt`/`theoretical` matches by the `score_kinds` mechanism above, and its target
  bucket moved the **wrong way** (49 → 54). It also shows that suppressing
  non-`dimension` detections is that same dead end wearing a different hat.
  Under the corrected char_type policy it no longer has even a nominal cost win:
  **+0.75, better under 3 of 6 weightings** (was −0.25 and 4 of 6).
* **Serving a LoRA adapter on the AWQ base. Not a loss — an IMPOSSIBILITY.**
  `lora72bawq` was the "deployment question" arm: attach `read-lora-v1` to what
  production actually serves. On a clean card (`memory.used=1 MiB`) PEFT refused
  at load:

      ValueError: Target module WQLinear_GEMM(in_features=8192, out_features=8192,
      bias=True, w_bit=4, group_size=128) is not supported. Currently, only the
      following modules are supported: torch.nn.Linear, torch.nn.Embedding, ...

  autoawq replaces every `q_proj`/`k_proj`/`v_proj`/`o_proj` with
  `WQLinear_GEMM`, and PEFT can only inject into the module types listed above.
  The later `device_map contains a CPU or disk device` errors in that log are
  DOWNSTREAM: the failed first attempt still held ~40 GB, so `device_map="auto"`
  spilled to CPU and AWQ refuses that. Do not chase the device_map message.

  So the design's open question — "the mismatch's size is unknown and one arm
  measures it" — has an answer, and it is that the mismatch cannot be measured
  this way at all. Deploying a LoRA means one of: serve the NF4 base (pay the
  measured +6.35 review cost AND ~2.3x inference wall-clock), merge the adapter
  into bf16 and re-quantise to AWQ, or move to a serving stack with native LoRA
  support. All three are decisions, not experiments.

  **Route 2 is now measured and route 1 is now BUILT.** Serving NF4 + the scoped
  adapter is `r3-loraread` at 172.00 — a win over its NF4 control but still
  +1.95 worse than production. `merge_lora.py` plus the `loramerged` /
  `mergedcontrol` stages implement the merge route. Run `mergedcontrol` FIRST:
  the merged checkpoint is not one any AWQ number was measured on, so its
  zero-scale control must reproduce 170.05 before any `loramerged` delta is
  attributable to the fine-tune.
* **Swapping the DETECTOR for a cheaper model (the hybrid arm).** 32B AWQ
  localising, 72B AWQ transcribing, one checkpoint per H100: **+40.00**, 0 of 6
  weightings better, robust, and worse on the product goal regardless of
  weights (silent errors 71 -> 105 while missed fell 88 -> 70, and fully-correct
  values 107 -> 66). The detector's weights DID move the bucket they were aimed
  at -- `missed_diagnosis` contended 19 -> 10 and isolated 58 -> 49, `unlocated`
  untouched -- so the mechanism was real; the recovered rows simply arrive
  attached to boxes nothing can read. Full result and the box-framing finding:
  `docs/plans/2026-09-11-hybrid-arm-result.md`. **The registered `field_acc`
  gate tripped and the arm was still valid**: the gate assumed read quality
  follows the reader, and greedy determinism proves the reads DID run on the 72B
  (had they not, the run would be bit-identical to `r3-32bawq`; it differs in
  five taxonomy counts). Register the mechanism a gate assumes, not only its
  threshold.
* **The crop-RESOLUTION knobs (`_MIN_CROP_H`, `_MAX_UPSCALE`). Refuted without
  a GPU run** (2026-09-16, `docs/plans/2026-09-16-crop-height-diagnostic.md`),
  by the `read_accuracy_by_crop_height` aggregate built for the decision. Two
  independent reasons: only **9 of 223 matched rows (4.0%)** sit below
  `_MIN_CROP_H` at all, so the knobs cannot reach 96% of the corpus; and
  accuracy **falls** with crop height (0.71 at 28-40 px, 0.59 at 40-80, **0.40
  at >=80**), so "more pixels helps the reader" is backwards, not merely small.
  `boxes.tighten_to_ink`'s `pad=3` is not in this family — it is the same
  CONTEXT lever as `_CROP_PAD`, whose curve is already peaked.
* **The HEIGHT-DEPENDENT crop pad** (`r3-tallpad`, 2026-09-17,
  `docs/plans/2026-09-17-tall-pad-arm-result.md`). Pad 48 for boxes >=120 px,
  24 elsewhere. **132.53 against r3-cropctx's 131.87, better under 0 of 6
  weightings.** The most precisely targeted arm in the campaign — the
  `120-200` band went 0.523 -> 0.585 and **every other band came back
  bit-identical** — and it still lost. Do not re-run it, and do not try another
  pad dose: three global doses plus this one have mapped the curve and the
  lever is spent.

  **The reason it lost is worth more than the arm, and it generalises.**
  `flagged_error` and `flagged_correct` BOTH cost 1, so **fixing a read on a row
  the reviewer was already going to check saves NOTHING**. This arm fixed 6
  flagged rows (zero saving) and broke 2 unflagged ones (+5 each). Only
  converting an ESCAPED error pays, which is exactly what `read-lora-v1`'s win
  was made of. **Before proposing any read-quality change, ask which taxonomy
  bucket the fixed rows are currently in.**
* **The `char_type` bucket as a synonym-map problem.** `wrong:char_type` is the
  largest single failure mode (115 of 308 matched pairs), and the standing
  hypothesis was that gold's German labels were missing from
  `normalize.CHAR_TYPE_SYNONYMS`. Adding the obvious words (breite/höhe/tiefe/
  dicke, rundheit, parallelität) moved **exactly nothing** — a re-score was
  byte-identical. The real fault was that the map matched the WHOLE label while
  `char_type_kind` matches on word containment, so compound gold labels
  ("Diameter MIN") were admitted to scoring by one function and judged unequal by
  the other. Fixing that is worth **−1.25, not the ~−4.6 the handoff estimated**:
  it corrects 16 of the 115 rows, of which only 5 were wrong in `char_type` ALONE
  and so became fully correct. **Do not propose more synonym entries.** The
  residual is real: `Diameter→Distance` 11 and `Distance→Diameter` 11, a
  symmetric confusion over the leading Ø that `parser.py` infers Diameter from —
  a read-stage fault, which is Rung 3's target. `char_type_confusion` in the
  digest is the aggregate that settles this; read it before touching the map.
* **Classifying boxed (`theoretical`) callouts from their text instead of
  emitting `THEORETICAL`** (`874771c`, reverted `89e0375`, 2026-09-22,
  `docs/plans/2026-09-22-theoretical-parser-result.md`). The defect it removed
  was real and the edit was surgical — `identical` 223 -> 214, exactly the 9
  boxed rows, `would_break 0` — and the bound is **`would_fix 0`**. Those rows
  are wrong in their VALUES as well, so unblocking the `char_type` comparison
  exposes them to scoring and they stay wrong. **Cheapest closed arm in the
  campaign: two CPU-second re-scores, no GPU.** The commit stays in history
  because it also made boxed gold rows renderable as TRAINING targets, which the
  bound cannot price — cherry-pick it back if an adapter ever needs them.
* **Flag/drop rules that failed the policy arms** (2026-09-25,
  `docs/plans/2026-09-25-policy-arms-result.md` §2; each priced exactly on
  train/dev/test). Flags: `tall_box` (cost −7 to −11 but flags 15-61 correct
  rows — auto-accept rate −0.05), `diameter_sign` (rate −0.03/−0.04),
  `asymmetric_tol` (precision falls on train and dev), `multiline` (never fires
  — the reader emits no newlines), `gdt_guessed` (passes, but a strict subset
  of `nondim_kind`). Drops: `empty_read` and `no_digit` (they hit matched
  flagged rows — recall), `note_kind` and `theoretical_kind` (recall on
  train/test; on dev a re-pairing made a row escape — cost alone said −5.3),
  `repeated_value_nearby` (4/6 weightings on train, fails test),
  `theoretical_no_nominal` (precision). **Do not re-propose a kind-drop:** it
  is the closed `score_kinds` family again, now measured rule by rule.
* **OCR (tesseract) proposals for isolated misses** (2026-09-26, result doc
  §7). CPU feasibility gate: an OCR proposal lands in the match gate of only
  **6 of 58** isolated misses (registered GO ≥ 12, expected 15-30), against 253
  proposals far from any gold. Even a perfect verifier caps at ≈ −3.6/doc.
  Built and reverted; the misses the VLM makes are misses tesseract makes too.
  Isolated misses stay a detector-WEIGHTS problem (Rung 3: 75 → 43).
* **Deploying `read-lora-v1` by ANY route** (NF4, vLLM, merge-and-requantise)
  (2026-10-05, `docs/plans/2026-10-05-read-adapter-repricing-result.md`).
  Under the shipped policy the adapter itself costs +3.33 / +4.27 before any
  serving overhead, on two independent stacks. **Do not run `mergedcontrol` /
  `loramerged`.** A re-trained adapter is NOT closed: fix `render_target` so a
  target never renders a tolerance the drawing does not print, then price it
  under the active rules with `escaped_error` as the damage counter.
* **Filling the ISO 2768 general tolerance from the title block**
  (2026-10-05, `docs/plans/2026-10-05-general-tolerance-result.md`).
  Registered CPU gate on train: **would_fix 12, would_break 63** (break rate
  0.84, Wilson upper 0.91). It fails with the worst document removed too.
  Re-scored +4.90/doc, 0/6, precision 0.755 → 0.653. Killed; nothing shipped.
  **The premise was wrong:** of 93 matched rows read without a tolerance, gold
  holds the ISO-m value on only 17. 45 carry a different *printed* tolerance
  (not a format artefact: 0 hold the table magnitude in another shape), and 16
  carry none. The handoff's "~120 train rows wrong only for the general
  tolerance" is refuted. Kept as measurement only (`app/eval/general_tolerance.py`,
  `score --gentol-check`); a test forbids any pipeline import of it. Dev and
  test were never scored with it and remain unseen.
* **`predict --detect-only` as a way to cheapen the crop pass.** Detection is
  ~2/3 of per-document cost, not the reads: detection-only measured 10 m 55 s and
  23 m 45 s on dev documents 2 and 3 against a full-predict median of ~16 min, and
  document 3's detection alone exceeded that median. Kept as a diagnostic because
  it produced the measurement. **The corollary is useful though:** cutting
  detection cost — fewer or cheaper tiles, a lower `max_new_tokens`, since the
  returned JSON arrays are short — would shorten *every* run, whereas the read
  stage has little left to give.

## 4. Conventions — match these, they are load-bearing

* **TDD, strictly.** Failing test → watch it fail → minimal implementation →
  watch it pass → commit. One task, one commit. The tests here document *why* a
  behaviour exists, not just that it does; write the docstring accordingly.
* **Every aggregate must reconcile against a count that already exists.** The
  digest's identities (taxonomy sums to `n_gold`, `pred_kinds` to `n_pred`,
  per-kind `matched + false == pred`, `missed_diagnosis` to `missed`) are the
  acceptance bar. An aggregate that cannot be cross-checked is a number asking to
  be trusted.
* **A new field's default must not lie about historical data.** `Optional[...] =
  None` plus a `*_not_measured` count, never a plausible-looking `0.0`. A stale
  report once reported "all 20 frames agree" for a run where 14 disagreed, and it
  cost a full analysis cycle. See `DocScore.frame_origin_frac`.
* **Re-score, don't just re-summarise.** `runner summary` on an old report shows
  defaults for fields that report never had. If a view looks empty or perfect,
  suspect staleness first.
* **`MatchParams` is the comparability guard.** Anything that changes what a match
  *means* belongs in it, so `compare_runs` refuses a cross-mode comparison instead
  of crediting the difference as an improvement. Default to current behaviour.
* **Never judge a change on review cost alone.** An arm must also hold field
  accuracy on matched rows and not raise `escaped_rate`. `app/eval/experiment.py`
  encodes this; `weights.miss=10 > weights.escaped=5` makes cost gameable.
  **And a policy change must not buy cost by flagging more:** auto-accept
  precision must not fall and the auto-accept rate must hold (§2).
* **A behaviour change is kept only if it passes on data it was not selected
  on**: select on train (`r3-trainpredict`), validate on dev, confirm on test,
  under every guard and 6/6 weightings; register predictions BEFORE pricing;
  a rule added after its price was seen is not eligible on that split.
* **Register the MECHANISM a gate assumes, not only its threshold — and pick a
  damage counter the treatment can actually move.** Two of the last three arms
  had a registered gate whose premise was the flawed part, and in both cases the
  arm's own data had to overrule the rule that was supposed to decide it:
  - the hybrid's `field_acc >= 0.40` gate assumed read quality follows the
    READER. It tripped, and the arm was valid anyway — greedy determinism proved
    the reads had run on the 72B.
  - the crop dose's rule said "`field_acc` up AND `misplaced_matches` <= 46 ->
    take 48". Both held; 48 is the worse setting. `misplaced_matches` never
    moved (42 at both doses) while the damage landed in the flagged/silent
    split, which the rule never looked at.

  **The check is cheap: did the counter respond to the PREVIOUS dose?**
  `misplaced_matches` was already flat at 44 -> 42 before that rule was written.
  A counter that has never moved cannot falsify anything.
* **Comment the *why*, not the what.** This codebase explains the trade a line
  makes and what breaks without it. Match that density.
* Commits: imperative subject, body explaining *why*, trailer
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## 5. Traps that are structural, not mistakes

* **Container `git_sha` is always `"unknown"`** (`.git` is dockerignored). Resume
  compares the whole `RunConfig`, so before detection knobs were recorded in
  `RunConfig.extra` a re-run silently skipped all 20 documents as "already
  predicted". Always use a fresh run name per experiment arm.
* **The GPU container mints and destroys its own doc-id salt**, so ids in a
  `predict` log join to nothing. Per-document facts come from `runner summary`
  under the local salt. `predict` warns about this; the warning is correct.
* **Check which GPU is free before running** — occupancy varies between the two
  H100s, and a 72B AWQ load into an occupied card falls back to Tesseract, which
  then fails every document. MIG is not currently configured despite what the
  stale CDI spec lists.
* **Scoring is deterministic here** (greedy VLM decoding): 16 unchanged documents
  gave per-document deltas of exactly `0.0` across a GPU device change. So one arm
  per hypothesis is enough, no repeats, and any non-zero delta is causal.
* **The digest-key trap.** The pre-commit hook blocks any staged `.json` whose
  content carries `"upper_tol"` or `"lower_tol"` as a quoted token — it cannot tell
  a COUNT keyed by a field name from a VALUE stored under one. Aggregate keys are
  therefore namespaced (`field:lower_tol`, `fields:char_type+nominal`). Do **not**
  reach for `SINDRI_ALLOW_DATA_COMMIT`: every future digest commit would then need
  it, which trades a permanent hole in a data guard for six characters.
* **The Bash guard denies more shapes than it first appears.** `cmd | head`,
  `a && b`, `> file`, a bare `.pdf` anywhere in the command string, and even
  `git add <file-whose-CONTENTS-mention-the-protected-root>` are all refused. Run
  sanctioned commands bare, and split a file edit from its `git add` into separate
  calls. `sync_client_data.sh` is **not** in the allowlist regex, so pulls are the
  operator's to run, not an agent's.
* **Never `git checkout` on the GPU host while a queue is running.** Bash reads
  a script incrementally, so replacing `run_gpu_queue.sh` on disk mid-run can
  corrupt the executing queue — and a checkout is exactly how code reaches that
  host. If something must be deployed while a queue runs, `scp` it to a path
  **outside** `~/sindri` (that is why `run_train_lora.sh` takes no `$REPO` and
  can run from `~`). Note also that the host **cannot fetch from GitHub** at
  all: its remote is credential-less HTTPS and its ssh key is a deploy key for
  a different repo, so code arrives by push. **The obvious recipe works exactly
  once and then blocks itself**: push to `from-operator`, check it out, and the
  next push is refused with `refusing to update checked out branch` — git will
  not update the branch a non-bare repo has checked out. The fix is to keep the
  host's HEAD **detached**, so no branch is ever current:

      # operator
      git push ssh://<host>/home/rebe_test3/sindri <branch>:refs/heads/from-operator
      # host — still detached afterwards, so the next push works too
      ssh <host> 'cd ~/sindri && git checkout --detach from-operator'

  If the host is already sitting on a branch, `git checkout --detach` once
  first. Do NOT set `receive.denyCurrentBranch=updateInstead`: that makes a
  bare `git push` rewrite the host's working tree, and a push is a far lighter
  gesture than an ssh checkout — which is the opposite of what the
  queue-corruption rule above needs. Deploying must stay a deliberate two-step
  act with a place to check that no queue is running.
* **A partial run used to score silently, and the warning that should have
  caught it could not.** On 2026-09-14 two runs were pulled and scored while
  still predicting — 5 of 15 documents and 7 of 19 — and printed headline numbers
  that read like results (118.80 against a 133.93 control; it was an artefact of
  which documents had finished). `score` now REFUSES when split members have no
  dump, because the split is the contract for which documents a run covers.
  `--allow-partial` overrides deliberately and records `missing_dumps` in the
  report, so a partial number is never quotable later without its caveat.
  The lesson generalises: `WARNING: gold docs without dumps` fires on EVERY
  healthy dev score, naming the ~80 documents in other splits, so it could never
  carry this signal. **A warning that fires identically on every healthy run is
  not a warning.**
* **Gold covers one more document than `corpus/originals` contains** (100 vs
  99, surfaced 2026-09-15 by the test split: 20 gold members, 19 drawings). A
  gold document with no drawing can never be predicted — `predict` selects on
  the drawings it can find, so it simply stops short and reports no failure.
  `score` now EXCLUDES those and prints them, like multi-sheet and oversized,
  because charging their gold rows as missed at `w=10` would measure a
  data-delivery gap rather than the model. It needs `--pdfs` to tell that apart
  from an unfinished run; without it the two are indistinguishable and the
  message says so. **The first version of the partial-run guard asserted the
  timing cause and told the operator to wait for a run that had finished hours
  earlier** — a guard that misdiagnoses costs what the warning it replaced cost.
* **A NaN confidence makes a dump WRITE-ONLY, and dodges flagging on the way.**
  pydantic accepts NaN at construction, `model_dump_json` serialises it as
  `null`, and reloading that null raises — so `r3-awqtest` cost nine GPU hours
  and could not be scored at all. float16 AWQ can produce a degenerate logits
  row whose softmax is NaN; dev never produced one in ten-plus arms and the TEST
  split did on its first outing. Worse than the crash: `review.LOW_CONF` is a
  `<` comparison and every comparison against NaN is False, so such a row is
  never flagged and ships as a silent error. Guarded now at both ends
  (`models.Confidence`, `_mean_token_confidence`,
  `mean_confidence_from_logprobs`) — **if you add a float that comes from a
  model, guard it the same way**; `float` in a pydantic model is not a promise
  that it round-trips.
* **The GPU host is unreliable, not merely slow.** 24+ users, load 80–200. In one
  evening it dropped an ssh channel mid-run, killed a container two documents from
  the end, and left the network for ~14 h *without rebooting*. Long runs belong in
  `tmux` **on the host** (`KillUserProcesses=false` is confirmed; another user's
  server has 123 days' uptime) — see `run_gpu_queue.sh`, which is resumable via
  `.complete` markers and never scores, because gold is not there.

## 6. Verify before claiming anything works

```bash
python -m pytest -q                          # 1233 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # guard: 32 passed, 0 failed
python3 -m app.eval.experiment               # arm decision table
```

`experiment.py` now judges each arm against **the control its comparison file
names** (`run_a`), and prints it as `[vs <run>]`. The old single global control
was AWQ-and-0.6-era and reported `lora72bnf4` at +13.35 when the matched answer
is +10.00. An arm whose named control has no digest here is reported and NOT
judged, rather than silently falling back.
