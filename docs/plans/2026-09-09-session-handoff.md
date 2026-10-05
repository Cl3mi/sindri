# Session handoff — the scope policy, seven models, and where the quality actually is

Written 2026-09-09. **Read `CLAUDE.md` first, then this.** Supersedes
`docs/plans/2026-09-07-rung3-loraread-result.md` and
`docs/plans/2026-09-05-session-handoff.md` where they disagree.

**One line:** a scope policy (single-sheet, supported-size) now defines what the
product claims; under it production scores **133.93** with **28.3% of values
missed** and **22.8% shipped silently wrong** — and the 32B model turned out to
be the best *detector* and the worst *reader* measured, which is the first
credible lead on the missed rate in the whole campaign.

---

## 0. Verified state

```bash
cd /home/clemi/mci/sindri/.claude/worktrees/eval-harness
python -m pytest -q                          # 770 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -c "from app.eval.runner import _prompt_sha256; print(_prompt_sha256())"
                                             # aa7659f1929184ea — must not move
```

Branch `worktree-eval-harness`, **34 commits ahead of origin, unpushed**. Both
H100s idle. `SCHEMA_VERSION` = 1. Split frozen at `6d174d5e4f1b9228`.

**NDA unchanged.** `score` / `compare` / `summary` / `probe` are declined for an
agent every time — they are the operator's to run. `./rescore_onepage.sh` is the
one command that does the whole batch.

---

## 1. THE SCOPE POLICY — what the product now claims

Set 2026-09-09. The deliverable covers **single-sheet drawings at a size the
renderer handles at full resolution**. Both exclusions are structural, not model
faults, so scoring them measured a capability never claimed:

* `--max-pages 1` — `render_page` takes `page_index=0` and always has, so gold
  on sheet 2 is unreachable, charged at `w=10`, the heaviest weight there is.
* `--exclude-clamped` — `render.py` clamps any page over the pixel budget. The
  clamped drawings scored 283.75 review cost at 0.371 recall against 141.62 at
  0.728 for the rest, and raising the budget was measured and LOST
  (`CLAUDE.md` §3), so they need tiling that does not exist.

Both are **OFF by default**, so every pre-policy number keeps its meaning, and
`_check_comparable` refuses a scoped report against an unscoped one.

**It keeps 15 of the 20 dev documents.**

**THE ANSWER: the product claims 75 of the client's 99 drawings — 75.8%**
(measured 2026-09-11, `probe --summary`, both exclusions over the whole corpus):

| | docs | share |
|---|---|---|
| **supported** | **75** | **75.8%** |
| oversized (render-clamped) | 19 | 19.2% |
| multi-page | 8 | 8.1% |
| both at once | 3 | 3.0% |
| excluded (the union) | 24 | 24.2% |

`supported + excluded == 99` exactly; the two exclusion counts do not sum to 24
because 3 drawings fail both tests. 110 pages over 99 documents, median 1,
max 4.

**The oversized exclusion is 2.4x the multi-page one, and it was the invisible
half.** Every earlier statement of scope leaned on `multi_page_docs`, which is
the SMALLER problem. It is also the one with no built path: `--max-pages 1`
excludes sheets the renderer could reach if `extract()` asked for them
(`render_page` already takes `page_index`, and gold for those sheets exists and
is currently charged as misses at `w=10`), whereas the clamped sheets need
tiling that does not exist.

**Do not read CLAUDE.md §3's "do not build tiled rendering" as covering this.**
That dead end is about resolution recovering MISSES inside drawings already
supported — 80 to 150 MP moved isolated misses 251 to 252 and lost. This is a
COVERAGE question about 19 drawings that are out of claimed scope entirely, and
it is a different proposition that has never been measured.

**The dev split is representative on both axes**, which is what licenses
quoting 133.93 / 0.7170 / 28.3% as the product's numbers rather than the dev
split's: 4 of 20 clamped (20.0%) against 19.2% corpus-wide, and 1-2 of 20
multi-page (5-10%) against 8.1%.

**Excluded is not "fails".** The 19 oversized drawings still produce output, at
recall 0.371 and 283.75 review cost against 0.728 and 141.62 for the rest --
about half as good, not nothing. The 8 multi-page drawings lose everything after
sheet 1 completely. Say it that way to the client; "we support 75 of 99" alone
overstates the cliff.

**And the dev split's own breakdown was already derivable from two numbers
this repo had recorded separately.** `rescore_onepage.sh` records **4 clamped
drawings in dev** ("the other sixteen"), and the policy keeps 15 of 20, so 5 are
excluded. Since the exclusions overlap rather than sum, that fixes it: **exactly
4 are oversized, and 1 or 2 are multi-page** — 2 only if one drawing is both.
The corpus rate (8 of 99, 8.1%) predicts 1.6 in a 20-document split, so 1 is the
likely answer. `score` prints both counts on separate `excluded N ...` lines, so
the next `rescore_onepage.sh` confirms it without any new work.

Two other facts from that probe worth carrying: **17 of 99 drawings have no
vector text at all** (`without_vector_text`), so the VLM is the only reader
available on a sixth of the corpus; and 50 of 99 carry duplicate recovered
balloon numbers, which is a property of the CV circle finder on clean originals,
not of gold.

---

## 2. Quality under the policy — all seven runs

15 documents, 311 gold values, identical corpus in every row, every taxonomy
reconciling to `n_gold`.

| approach | cost | recall | field_acc | silent-wrong | missed | n_pred |
|---|---|---|---|---|---|---|
| **72B AWQ — production** | **133.93** | 0.7170 | 0.4798 | 0.2283 | 28.3% | 569 |
| 72B NF4 + adapter | 134.07 | 0.7299 | 0.5066 | 0.2219 | 27.0% | 597 |
| 72B NF4 base | 137.47 | 0.7299 | 0.4714 | 0.2605 | 27.0% | 597 |
| 72B AWQ/vLLM + adapter | 138.73 | 0.7170 | **0.5157** | **0.2058** | 28.3% | 620 |
| 72B AWQ/vLLM | 141.20 | 0.7203 | 0.4688 | 0.2412 | 28.0% | 620 |
| **32B AWQ** | 172.73 | **0.7749** | 0.2614 | 0.3215 | **22.5%** | 890 |
| 7B AWQ | 180.00 | 0.2605 | 0.1852 | 0.1254 | 74.0% | 168 |

### Human effort per document, production path

| action | per doc | share of values |
|---|---|---|
| add by hand (missed) | 5.87 | 28.3% |
| correct a flagged error | 3.00 | 14.5% |
| re-check, then accept (wasted) | 2.47 | 11.9% |
| delete spurious detection | 23.07 | — |
| **ships wrong, no warning** | **4.73** | **22.8%** |

---

## 3. THE LEAD: the 32B detects better than the 72B

The most useful result of the session, and the only thing that has ever moved
`missed`.

| | 72B production | 32B | delta |
|---|---|---|---|
| recall | 0.7170 | **0.7749** | +0.058 |
| missed | 88 (28.3%) | **70 (22.5%)** | **−18 rows** |
| field_acc | 0.4798 | 0.2614 | −0.218 |
| escaped_error | 71 | 100 | +29 |
| false_detection | 346 | **649** | **+303** |

Cost reconciles exactly at **+38.80/doc**: `missed −18 ×10 = −180`,
`escaped +29 ×5 = +145`, `false_detection +303 ×2 = +606`, `flagged +11 ×1`.

**It over-detects, buys real recall, and drowns it in spurious boxes.**

Rung 1 threw render resolution, tile size and both merge knobs at missed values
and every one lost; `CLAUDE.md` records `isolated` as "provably untouched". The
only other thing that ever moved it was the void arm `lora72bnf4` (adapter over
the whole model, `missed_isolated` 75 → 43) at the cost of +324 false
detections. **The detector's WEIGHTS move missed values. Its knobs never did.**

**The pipeline already separates detection from reading** — that is the scoping
built for the adapter (`VLMBackend._base_weights`,
`vllm_backend.adapter_for_pass`). A hybrid — **32B detecting, 72B reading** — is
architecturally natural and is the first credible attack on the 28.3%.

**Unmeasured and the thing that could kill it:** whether those +303 false
detections survive the split. At `w=2` they cost +606 against the −180 the
recovered misses earn. The hybrid only wins if the reader can reject what the
detector over-produces, or if the client's weights value a found value far above
a deleted box.

---

## 4. Closed questions — do not re-open

* **The 7B is not viable.** +46.07 cost (+34.4%), significant, ci95
  [10.6, 84.6], worse under 6 of 6 weightings, recall 0.2605 — it finds 81 of
  311 values. Its *low* silent rate (0.1254) is an artefact of finding almost
  nothing: of rows it DOES match, **48.1% ship silently wrong** against 31.8%
  for the 72B.
* **Confidence thresholding cannot reduce silent errors.** All 109 escaped
  errors sit at confidence **≥0.8**; below 0.8 there are only 24 rows, every one
  already a flagged error. The signal is saturated and does not separate silent
  errors from correct reads. Raising the threshold sweeps up 77 correct and 40
  flagged-correct rows with them. This is the first thing anyone suggests — it
  is measured and it does not work.
* **A cheap GPU box is not viable today.** 7B at **24 GB OOMs on a 7.75 GiB
  vision allocation and silently drops the marks and title blocks** (via
  `extract._safe_read`, which swallows read exceptions); at **40 GB it is
  clean** — 20/20 documents, 0 OOM, 0 swallowed reads. The 32B needs ~44 GB and
  is slower than the 72B. **The peak is set by vision activations on large
  crops, not by weights** (7B weights ≈ 6 GB).
* **Route A (merge + re-quantise) is BLOCKED at serving**, three distinct
  failures deep: missing processor files → duplicate processor config →
  `'dict' object has no attribute 'to_dict'` (transformers 4.49.0 +
  compressed-tensors 0.9.4). Both AWQ checkpoints ARE built and on disk at
  `/models/merged/{zero-scale,read-lora-v1}-awq`, 42.9 GB each, layout matching
  Qwen's official checkpoint. `Dockerfile.serve-ct` holds the verified pins.
* **autoawq is a dead end for this model** — see `CLAUDE.md` §3 and the
  2026-09-07 writeup. Do not retry it.

---

## 5. Caveats that must travel with these numbers

* **Both adapter arms LOSE significance under the policy.** `loraread` is −3.40
  with ci95 **[−7.67, 0.13]** over 15 documents, against −4.40 and significant
  over 20. Still better under 6 of 6 weightings and still robust — but at this
  corpus size it is not proven. Do not present the adapter as a confirmed win.
* **Everything is conditional on `weights.json`** (`miss=10, escaped=5,
  false=2, flag=1`). The adapter beats production once a silent error costs
  **≥9.4×** a wasted re-check; today's weights assume 5×. Re-deriving them with
  the client may be worth more than another arm.
* **Timings are from a shared host** at load 80–200 — upper bounds, not
  benchmarks. 7B 17.0 min/doc, 72B/vLLM 18.7, 72B+adapter 32.4, 32B 36.3.
  Throughput tracks the ~900 VLM calls per page, **not** parameter count: the
  7B is barely faster than the 72B and the 32B is slower.
* **The digests do not record the policy.** `max_pages` and `exclude_clamped`
  are on `RunReport` but absent from the summary digest, which `CLAUDE.md` calls
  the only sanctioned view of a run. A reader of a digest cannot tell a scoped
  run from a partial one. Same class of gap as `adapter_scope`; ~10 minutes.

---

## 6. Next steps, in order

1. **Get the corpus counts. DONE 2026-09-11: 75 of 99 supported** (§1). The
   follow-on question is now a product decision rather than a measurement:
   **19 drawings are excluded for size against 8 for page count**, and the size
   exclusion has no built path. Worth putting to the client next to the
   weights (§5) -- "half-quality on a fifth of your drawings" and "a fifth of
   your drawings out of scope" are different products.
2. **Compare the 32B properly. DONE 2026-09-11.** `+38.80, ci95 [25.53,
   55.13]`, significant, **0 of 6 weightings better**, robust, and **all 15
   documents worse** (deltas +6 to +123). `compare_runs` warns that the base
   model differs, so state both models wherever it is quoted. Digest:
   `docs/eval/32bawq-scoped-vs-awqcontrol-scoped.json`. The lead was never the
   32B's cost -- it is its `missed`.
3. **THE HYBRID ARM (§3). MEASURED 2026-09-11 and LOST: 173.93, +40.00**,
   ci95 [26.07, 56.27], significant, 0 of 6 weightings better, robust. Full
   result, the scoring of the registered prediction, and the finding that
   matters more than the verdict: `docs/plans/2026-09-11-hybrid-arm-result.md`.
   **Box framing dominates read accuracy by ~5x over the reader's weights**, and
   the 32B's recall advantage is recall of boxes, not of values -- 18 more gold
   rows matched, 41 fewer fully-correct values delivered, 34 more silent errors.
   The lead in §3 of this document is CLOSED; read §3 only for its history.
4. **Everything after the hybrid is decided and written down**, including the
   rejections: `docs/plans/2026-09-14-next-steps-decision.md`. In short —
   **(a)** score the frozen TEST split (`awqtest`, built 2026-09-14; nothing has
   ever run there and every client-facing number is from dev), with the
   zero-GPU precursor of scoring the existing `r3-trainpredict` dumps;
   **(b)** re-derive `weights.json` with the client, which is the single number
   deciding whether `read-lora-v1` ships; **(c)** the `cropctx` arm, registered
   in `docs/plans/2026-09-14-crop-context-arm-prediction.md`. Then route A
   serving, multi-page coverage (+8 drawings), tiled rendering (+19).
5. **Surface the policy in the digest** (§5, last bullet).
6. **Balloon overlap is fixed** (`place.py`, commit `ea2dd8c`) and is
   measurement-neutral — `balloon_xy` appears nowhere in `app/eval`. Worth
   showing in a demo; it changes no number.
