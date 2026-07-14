# Handoff: Extraction/Ballooning Quality Optimization

**Status:** Strategy agreed, not yet spec'd into an implementation plan.
**Purpose:** Hand off to another AI coding tool to produce a detailed implementation
plan (start with the `writing-plans` flow or equivalent). This doc is self-contained
— you should not need the originating conversation.

**Date:** 2026-07-14
**Trigger:** Client will provide **~100 ballooned PDFs + matching Excel sheets** (manually
extracted "gold standard" values). Goal: use this data to measure and improve the
quality of the extraction/ballooning pipeline.

---

## 1. Two decisions already made (these steer everything)

1. **Template diversity: "mostly same, some variation."** One dominant Intercable
   house-style template, with occasional layout/customer variants. → Prompt + few-shot
   is the *safe primary lever*; LoRA is viable but must hold out the variant drawings;
   template-specific deterministic heuristics are high-value for the dominant style.

2. **Success metric: "minimize review time."** A human reviews **every** output. The
   objective is **expected correction effort per drawing**, NOT raw accuracy. This is
   the single most important framing in this doc — see §4.

---

## 2. What the system is today (ground truth from the code)

Sindri is an **offline, single-container** tool: ballooned drawing PDF → reviewable
inspection `.xlsx`. It is **100% zero-shot prompted Qwen2.5-VL**. There is:

- **No training loop, no fine-tuning, no few-shot exemplars.**
- **No evaluation harness.** The only "eval" today is `app/pipeline/diagnose.py` run by
  hand on a **single** PDF (`test_docs/T1025300_B.pdf`).
- All "model behavior" lives in **five prompt strings** in
  `app/pipeline/ocr/vlm_backend.py` (`_PROMPT`, `_DETECT_PROMPT`, `_GDT_PROMPT`,
  `_NOTES_PROMPT`, `_TITLE_PROMPT`).

**Model:** `Qwen/Qwen2.5-VL-7B-Instruct` default (`_DEFAULT_MODEL`), overridable via
`VLM_MODEL_ID`; GPU deploy uses `Qwen2.5-VL-72B-Instruct-AWQ`. Falls back to Tesseract
(no GPU) — but auto-ballooning **requires** the VLM backend (`extract.py:118`).

### Pipeline stages (`app/pipeline/extract.py` orchestrates)

1. **Render** — `render.py:render_page` → PNG @ 300 dpi.
2. **Structured blocks + mask** — locate & read, then mask out so they aren't misread
   as dimensions:
   - Marks/notes legend: CV locator `marks_block.locate_marks_block` + VLM
     `read_marks_block`/`read_notes_block`. The shared parser is
     `legend_parse.parse_rows` (JSON-first, tolerant text fallback).
   - Notes (separate tables elsewhere): `notes_block.locate_notes_block` (VLM).
     De-conflicted vs the legend by `extract._regions_overlap` so one physical legend
     is owned once.
   - Title block: `title_block.locate_title_block` + `read_title_block`.
3. **Detect callouts** — `detect.detect_characteristics(image, backend)`: **tiled** VLM
   detection via `backend.detect_regions` (`_DETECT_PROMPT`) → boxes + coarse `kind`
   (`dimension|gdt|surface|note|material`). `merge_adjacent` fuses stacked same-kind
   boxes with a **stack-height cap** (`max_lines`) so distinct stacked dims (Ø20/Ø15/…)
   don't collapse.
4. **Read each crop** — `extract._best_read`: tighten to ink (`boxes.tighten_to_ink`),
   crop+pad+upscale (`_prep_crop`, `_CROP_PAD=6`, `_MIN_CROP_H=40`, `_MAX_UPSCALE=3.0`),
   try rotations for vertical crops, `backend.read_region` / `read_region_gdt`, then the
   **deterministic parser** `parser.parse_value` → `Characteristic`.
5. **Number + place balloons** — `place.number_characteristics`, `place.place_balloons`.
6. **Review flags** — `review.review_flags` sets `needs_review` + `review_reasons`.
   Confidence is real: mean per-token top-softmax prob from the VLM decode
   (`vlm_backend._mean_token_confidence`).
7. **Excel** — `app/excel.py`; served via FastAPI `app/main.py` (upload → review UI →
   download).

### Data models (`app/models.py`)

- `Characteristic`: `pos, char_type, nominal, upper_tol, lower_tol, raw_text,
  confidence, kind, subtype, source, needs_review, review_reasons, balloon_xy,
  target_region, note_ref_pos`.
- `Note` / `NoteBlock`, `Mark` / `MarkBlock`, `TitleField`, `ExtractionResult`.

### Existing diagnostics to reuse (`app/pipeline/diagnose.py`)

`summarize_result` (kind/char-type histograms, `potential_duplicates` via IoU,
low-confidence reads, box-size stats), `capture_raw_reads`, `probe_legend_read`,
`_annotate` (writes an annotated PNG: marks=red, title=blue, notes=green,
callouts=orange, balloon leader lines). **The eval harness should extend this module's
patterns, not reinvent them.**

---

## 3. The gold data — what it supervises, and the hidden prep project

The 100 PDFs + Excel supervise **four distinct sub-tasks**, with very different data
density:

| Sub-task | Gold signal | Data volume (~100 docs) | Notes |
|---|---|---|---|
| Detection / localization | balloon positions on the PDF | ~100 whole-page examples (thin) | recall is the pain |
| Reading / transcription | Excel value per balloon | ~2,000–4,000 crop→value pairs (rich) | real LoRA-scale set |
| Structured blocks (notes/marks/title) | block values | ~100 (thin) | brittle parsing today |
| Placement + numbering | balloon# ↔ row mapping | ~100 | balloon-on-wrong-callout |

### HIDDEN PREREQUISITE — the data-join / matching problem

- The Excel gives **values keyed by the client's balloon number**.
- The ballooned PDF shows **where each balloon sits** (approximate callout position).
- **Nothing gives the exact ink box per callout.** Result: **strong value labels +
  weak box labels.**
- **CRITICAL:** the client's balloon numbers **do not match** the tool's own
  auto-numbering order (`place.number_characteristics`). You **cannot** compare row-by-row.

So step zero is a real data-engineering task:
1. Parse each client ballooned PDF to recover `(balloon_number, position)` — likely
   detect the client's balloon glyphs (circled numbers) on the rendered page.
2. Join to Excel `(balloon_number → value fields)`.
3. Produce a gold record set: `{doc_id, balloon_number, position(approx), char_type,
   nominal, upper_tol, lower_tol, raw}`.
4. Match predictions ↔ gold by **bipartite assignment on geometry + value similarity**
   (Hungarian or greedy), NOT by number.

**Open question for the planner:** confirm how the client's balloons are encoded in the
PDF (vector circles + text? raster stamps? a separate layer?). This determines whether
`(balloon#, position)` recovery is a clean vector parse or needs its own detector. Check
the actual client files before committing to an approach.

---

## 4. The objective function: review cost, not accuracy

Because a human reviews every drawing, score errors by **correction effort**, not by a
flat accuracy count:

| Error type | Review cost | Rationale |
|---|---|---|
| **Missed callout** (false negative) | **Highest** | human must notice absence + add row from scratch |
| **Escaped error** (wrong value, NOT flagged) | **High** | silent; erodes trust in the sheet |
| **Flagged row** (right or wrong) | **Low** | human just verifies/fixes |
| **False detection** (phantom callout) | **Moderate** | human deletes |

**Headline metric = weighted review-cost per drawing**, e.g.
`cost = w_miss·(missed) + w_escaped·(unflagged errors) + w_flag·(flagged rows) +
w_false·(false detections)`, with default weights roughly `w_miss ≫ w_escaped >
w_false > w_flag`. Make the weights configurable; expose accuracy/recall/precision as
secondary diagnostics. This re-ranks all optimization work: **recall** and
**review-flag calibration** dominate; raw read-accuracy matters only via the
flagged/escaped balance.

---

## 5. The optimization ladder (cheapest → heaviest)

**Rung 0 — Eval harness + gold pipeline. FIRST DELIVERABLE. Prerequisite for all else.**
- Data-join + matching scorer (§3), review-cost metric (§4), document-level train/dev/test
  split (put the *variant* drawings in test so cross-template generalization is visible),
  bootstrap confidence intervals on every delta, and an **error taxonomy** tagger
  (missed / false / misread / misparsed / misplaced / block-error).
- Baseline the current default model. This *is* the "compare default vs optimized"
  machinery and delivers immediate value (a real baseline + where the error lives).
- Cost: moderate (data parsing is the fiddly part). Deploy risk: none.

**Rung 1 — Pipeline & parser tuning (no model change). Highest ROI, zero deploy risk.**
- Tune knobs already in code against the dev split: tile size/overlap in
  `detect_characteristics`, `merge_adjacent` `max_lines`/`x_tol`/`y_gap`,
  `tighten_to_ink`, `_CROP_PAD`/`_MIN_CROP_H`/`_MAX_UPSCALE`, rotation logic
  (`_is_vertical`, `ROTATION_EPS`), `_MAX_READ_LONG_EDGE`.
- Push detection **recall → ~100%**, accepting more false positives (cheap to delete).
- **Review-flag calibration:** learn confidence→error mapping from gold, set
  `needs_review` threshold (in `review.py`) to the review-cost-optimal operating point.
  Consider **per-field** flagging (flag just the tolerance, not the whole row).
- Harden `parser.py` regexes against the *actual* value formats found in the Excel.
- **Template-structural sanity checks** (nearly free, unique to "mostly same template"):
  expected callout count / char_type mix / legend+title positions → a drawing that
  yields 8 callouts when the norm is ~30 is itself a review flag. Catches whole-drawing
  failures per-callout confidence can't.

**Rung 2 — Prompt optimization + few-shot (frozen weights).**
- Systematically optimize the 5 prompts against dev.
- **In-context image exemplars** (Qwen2.5-VL is multi-image): prepend 1–3
  `crop→correct-answer` examples, optionally *retrieved* to match the current crop /
  dominant template. Training-free adaptation that exploits the consistent house style.
- Cost: low–moderate (eval compute). Benefit: reading + structured-block format
  compliance (comma decimals, Ø/GD&T symbols).

**Rung 3 — LoRA fine-tune of the read task (only if residual is perception).**
- ~2,000–4,000 crop→value pairs is a genuine LoRA set. Best experiment: LoRA the **7B**
  and see if it beats zero-shot 72B (would also cut inference cost). Adapters are small,
  load in the offline container, base stays shared.
- Detection LoRA is possible but data-thin — needs augmentation.
- **Risk: overfitting to one template.** Hold out **by document**, and ensure variant
  drawings are in the holdout, or you'll measure memorization not generalization.

**Rung 4 — Full fine-tune / larger base.** Highest ceiling, heaviest infra, real deploy
risk, diminishing returns at 100-doc scale. Only if Rung 3 plateaus below the bar.

**Recommended sequence:** Rung 0 → 1 → 2, measure, **then** decide Rung 3 from the error
taxonomy (perception residual → climb; pipeline/parse residual → stay).

---

## 6. Comparing default vs optimized (protocol)

- Split **by document**; force the ~10–20% variant drawings into the test set.
- Test set is **frozen** — touched only for final config comparison. Tune on dev only.
- Report per config: **review-cost score** (headline), **recall**, **escaped-error
  rate**, plus the error taxonomy.
- **Bootstrap CIs on every delta** (100 docs is small — without CIs you ship noise).
- **Paired** comparison (same documents both configs).
- **Regression guard:** an "improvement" that lifts read-accuracy but drops recall
  likely *raised* review cost — the harness must surface this automatically.

---

## 7. Where the biggest gains likely are (prioritized hypothesis)

1. **Detection recall on dense/stacked callouts** — a miss is unrecoverable; a misread is
   fixable. (Rung 1, then Rung 2/3 if perception-bound.)
2. **Hard-glyph reading** — Ø, stacked GD&T frames, comma decimals, rotated text.
3. **Review-flag calibration** — cheap, big human-time win, directly serves the objective.
4. **Structured blocks** — brittle today, lower volume.

---

## 8. Constraints the planner must respect

- **Offline, single container.** Any optimization output (fine-tuned adapter, few-shot
  exemplar bank, tuned config) must ship inside the offline image / mounted volume. No
  network at inference. Training happens off-box; only the artifact ships.
- **GPU-optional runtime**, but auto-ballooning needs the VLM. Eval/training run on a GPU
  box; the eval harness itself (matching/scoring) is pure-Python and CPU-friendly — keep
  it runnable without the model so scoring a saved prediction dump needs no GPU.
- **TDD is the house style** (see `docs/plans/2026-07-02-extraction-hardening.md` and
  `tests/`): every code task = failing test → minimal impl → passing test → commit.
- Reuse `diagnose.py` patterns; don't duplicate its region/annotation logic.

---

## 9. First concrete deliverable to plan (Rung 0)

Produce an implementation plan for the **eval harness + gold-data pipeline**:

1. **Gold ingestion** — parse client ballooned PDFs → `(balloon#, position)`; join Excel
   → gold records. (Resolve the balloon-encoding open question in §3 first.)
2. **Matching** — bipartite prediction↔gold assignment on geometry + value similarity.
3. **Scoring** — review-cost metric (§4) + secondary accuracy/recall/precision + error
   taxonomy tagger.
4. **Splits** — document-level train/dev/test with variants forced into test.
5. **Reporting** — per-config JSON + bootstrap CIs; a baseline run on the default model.
6. **Prediction dump format** — a stable serialized `ExtractionResult` + gold format so
   scoring runs without re-running the model.

Later rungs (1–4) each become their own spec once Rung 0 reveals where the residual
error lives.

---

## 10. Open questions to resolve before/while planning

- **Balloon encoding** in the client PDFs (vector vs raster vs layer) — gates the gold
  ingestion approach. Inspect real files.
- **Excel schema** — column layout, how balloon number maps to rows, how tolerances/
  units/char_type are encoded. Inspect real files; the `parser.py` normalization must
  match the gold's conventions for value comparison.
- **Review-cost weights** — get rough relative costs from the client/reviewer (how long
  to add a missed row vs verify a flag vs delete a phantom).
- **Value-match tolerance** — exact string vs normalized numeric (e.g. `1,2` vs `1.20`);
  define per field. Must be decided before scoring means anything.
- **Position reliability** — how accurately the client's balloon center localizes the
  callout, which sets how much geometry vs value-similarity weighs in matching.

---

## Key files reference

- `app/pipeline/extract.py` — orchestration (read this first).
- `app/pipeline/ocr/vlm_backend.py` — the 5 prompts + model load + confidence.
- `app/pipeline/detect.py` — tiled detection, `merge_adjacent`.
- `app/pipeline/parser.py` — deterministic value parsing (the comparison target).
- `app/pipeline/place.py` — numbering + balloon placement.
- `app/pipeline/review.py` — `needs_review` flagging (calibration target).
- `app/pipeline/diagnose.py` — existing eval/annotation patterns to extend.
- `app/models.py` — data models.
- `docs/plans/2026-07-02-extraction-hardening.md` — TDD style + prior hardening context.
