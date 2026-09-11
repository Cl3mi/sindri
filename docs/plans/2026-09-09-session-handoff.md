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

**The corpus is 99 drawings, and 91 of them are single-sheet** (measured
2026-09-11, `probe --summary`): 110 pages over 99 documents, **8 multi-page**,
median 1, max 4. So the first exclusion costs **8.1%** of the corpus.

The second exclusion's corpus-wide count was unobtainable — `score
--exclude-clamped` only reaches the 20 documents that have gold — and is now one
command away: `probe --summary` computes the clamp from page size, dpi and the
pixel budget, and reports `render_clamped_docs`, `supported_docs` and
`excluded_docs` alongside `multi_page_docs`. **Re-run it to finish the answer.**

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

1. **Get the corpus counts. HALF DONE 2026-09-11** — 99 drawings, 8
   multi-page, so 91 are single-sheet (§1). The oversized half was not
   computable then and is now: re-run
   `python3 -m app.eval.runner probe <corpus>/originals --summary` on current
   code and read `render_clamped_docs`, `supported_docs`, `excluded_docs`.
   That is the complete answer to "how many of our drawings do you support?".
2. **Compare the 32B properly. DONE 2026-09-11.** `+38.80, ci95 [25.53,
   55.13]`, significant, **0 of 6 weightings better**, robust, and **all 15
   documents worse** (deltas +6 to +123). `compare_runs` warns that the base
   model differs, so state both models wherever it is quoted. Digest:
   `docs/eval/32bawq-scoped-vs-awqcontrol-scoped.json`. The lead was never the
   32B's cost -- it is its `missed`.
3. **THE HYBRID ARM (§3). BUILT, and RUNNING since 2026-09-11 09:11Z** --
   `run_gpu_queue.sh 0,1 hybrid`, tmux session `hybrid`, ~10 h, judged against
   `r3-awqcontrol` under the scope policy. Both checkpoints loaded (32B on card
   0 at 31.2 GB, 72B on card 1 at 41.7 GB, `active backend: hybrid`), which also
   proves `device_map={"": "cuda:N"}` works for an AWQ checkpoint on
   transformers 4.49.0. The prediction, the void gates (`n_pred` exactly 890,
   `field_acc` >= 0.40), the per-weighting arithmetic and the launch procedure
   are in `docs/plans/2026-09-09-hybrid-arm-prediction.md`, written BEFORE the
   code. **It is predicted to lose under all six grid weightings**; §4 of that
   document is why it is still worth the night.
4. **Surface the policy in the digest** (§5, last bullet).
5. **Re-derive `weights.json` with the client** (§5, second bullet).
6. **Balloon overlap is fixed** (`place.py`, commit `ea2dd8c`) and is
   measurement-neutral — `balloon_xy` appears nowhere in `app/eval`. Worth
   showing in a demo; it changes no number.

---

## 7. Reference

**Runs on the GPU host** (`~/sindri-eval-data/runs/`): `r3-awqcontrol`,
`r3-nf4control`, `r3-loraread`, `r3-vllmcontrol`, `r3-vllmlora`, `r3-7bawq`,
`r3-32bawq`, `cap40` (7B at a 40 GB cap, diagnostic only).

**Images**: `sindri-gpu-nf4` (pinned serving, transformers 4.49.0 / autoawq
0.2.8 — MUST NOT MOVE), `sindri-vllm` (vLLM 0.8.5.post1 + torch 2.6.0+cu124 —
**0.28 cannot run here**, the driver is CUDA 12.4), `sindri-compress`
(llm-compressor, torch 2.13.0+cu126), `sindri-gpu-ct` (pinned + compressed-tensors
0.9.4).

**Traps added this session**
* `torch.cuda.is_available()` returned **True** on this host while a real matmul
  failed with "driver too old". **Prove GPU usability with a real op.**
* `podman ... | tee` with `&&` takes *tee's* exit status. A failed control
  reported `EXIT=0` and launched the arm anyway. Use `${PIPESTATUS[0]}`.
* vLLM V1 captures **67 CUDA-graph shapes up to batch 512** — still capturing at
  49 minutes on a 72B. Cap `cudagraph_capture_sizes`; `enforce_eager` is the
  only way to disable compilation, and it is measurement-neutral because both
  control and arm carry it.
* llm-compressor's registry mappings are **unscoped** for Qwen2.5-VL: the vision
  blocks share `mlp.up_proj` names with the decoder, so `up_proj → down_proj`
  resolved **112** sets (80 decoder + 32 vision) **silently**. And GQA makes
  `v_proj → o_proj` resolve 80 and survive **0**. Resolution counts alone cannot
  tell you whether AWQ smooths anything.
