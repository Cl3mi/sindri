# Session handoff — 2026-09-25: what changed, and where the cost now sits

Written 2026-09-25 at the end of a session, for a FRESH session whose job is to
**brainstorm where to improve next**. **Read `CLAUDE.md` first, then this.**
Supersedes `2026-09-21-session-handoff.md`; `2026-09-17-session-handoff.md`
stays the reference for device setup (its §1) and the standing list (its §4).

**One line:** three GPU-free arms closed this session — one reverted, one kept
(−0.67 derived), one withdrawn by its own cross-check — and an operator review
app now exists. The finding that should steer the brainstorm is not any of
them: **79.5% of the remaining review cost is DETECTION** (misses and false
detections), while the recent work all targeted silent read errors (16.2%).

---

## 0. Verified state

```bash
python -m pytest -q                          # 959 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -c "from app.eval.runner import _prompt_sha256; print(_prompt_sha256())"
                                             # aa7659f1929184ea
```

Branch `worktree-eval-harness`, PR #2, pushed up to and including this handoff.
`SCHEMA_VERSION` = 1. Split frozen at `6d174d5e4f1b9228`. Machine: the one with
the client corpus, the guard and `tesseract`; `python3` on PATH has the deps.

---

## 1. Where the review cost sits (dev, scoped, shipped config)

From `docs/eval/cropctx-scoped-summary.json` at today's weights (miss 10,
escaped 5, false 2, flag 1). It reconstructs the headline exactly: 131.87.

| component | cost units | per doc | share |
|---|---|---|---|
| **missed** gold rows (88) | 880 | 58.67 | **44.5%** |
| **false detections** (346) | 692 | 46.13 | **35.0%** |
| escaped (silent) errors (64) | 320 | 21.33 | 16.2% |
| flags (86) | 86 | 5.73 | 4.3% |

* **Misses:** `missed_diagnosis` contended 19, **isolated 58**, unlocated 11.
  Isolated misses moved only when the detector's WEIGHTS changed (an
  adapter run, 75 → 43 — CLAUDE.md §2, Rung 3; check which arm before citing
  it); every knob and prompt left them untouched (Rung 1 closed).
* **False detections by kind:** dimension 227, **theoretical 54 of 63
  predictions (86% false)**, note 38 of 42 (90% false), gdt 19, surface 7.
  The non-dimension kinds are mostly false.
* **The shares depend on the weights**, and `weights.json` is still waiting on
  the client (2026-09-17 handoff §4 item 1). Re-derive them before ranking
  levers by these percentages.
* The Ø-zone fix below moves escaped 64 → 62 (derived): 131.20.

---

## 2. What changed this session (since 2026-09-21)

### 2.1 The `theoretical` parser fix — measured, REVERTED
Bound exactly zero (`would_fix 0, would_break 0`), reverted at `89e0375`. The
labelling collision was real but was **masking a second fault**: 7 of 9 rows
are wrong in all four fields, and 86% of `theoretical` predictions are false
detections — a detection-quality bucket, never parser-reachable.
`docs/plans/2026-09-22-theoretical-parser-result.md`.

### 2.2 New GPU-free diagnostics (all counts, all in `score` / the digest)
* `read_accuracy_by_kind.wrong_fields` — each kind's wrong-field signatures;
  answers "wrong for one reason or two" before a fix is written.
* `read_accuracy_by_kind.char_type_only_confusion` — the confusion over rows
  a char_type fix would free.
* `score --reparse-check` → `gdt_char_type_only`: symbol recognised vs parser
  default, a **closed-vocabulary probe** (`app/eval/vocab_probe.py`: listed
  glyphs, listed ISO 1101 words, the characters before the value — by name
  only if on a fixed list, else by Unicode category), keyed per review-deck
  row, with each row's reparse outcome and taxonomy bucket.

### 2.3 The operator review app (reusable)
`score --review-deck <protected path>` writes a deck; the OPERATOR runs
`runner review-serve <deck>` (localhost, session token, crops of the clean and
stamped drawings, keyboard answers, autosave, Finish writes a counts-only
tally). `review-serve` / `review-tally` are deliberately off the agent guard's
allowlist; the guard does not cover a `curl` to the running app, so the link
must never reach an agent. Design `2026-09-25-gdt-review-ui-design.md`, plan
`2026-09-25-gdt-review-ui-plan.md`. Fixed after first use: the stamped crop now
inverts ingest's per-axis page scale (`505a667`) — 6 of 8 sheets differ in size.
Today the deck `kind` and questions are gdt-specific; a new review is a new
deck kind plus its tally routing.

### 2.4 The gdt review — result, and its correction
`docs/plans/2026-09-25-gdt-review-result.md`. The tally said the reader
transcribes every symbol and 4 profile rows were parser-fixable. The per-row
probe overturned it: the characteristic's symbol is **absent from ≥ 6 of 8
transcriptions** (nothing before the value on 3, only the Ø zone sign on 3) —
a read-stage majority, so **no profile-symbol parser arm**. Four operator
answers conflict with the label words (g1/g2/g6/g7), possibly from before the
crop fix.

### 2.5 The Ø-zone default — KEPT (`49f0629`)
`_gdt_type` defaulted to Flatness when it saw no symbol, even for a frame
opening with a Ø zone, which flatness never has. It now defaults those to
Position. Registered first (`d8625b1`); measured `would_fix 2` — exactly the
two registered rows, both escaped — `would_break 0`. **Derived** dev numbers:
131.87 → **131.20**, `field_acc` 0.5291 → 0.5381, `escaped_rate` 0.2058 →
0.1994. Derived, not scored: flagging and matching are provably unaffected,
but quote it as derived until a `predict` run scores it.

---

## 3. Open threads — raw material for the brainstorm

Each with the evidence that exists and what would move it. Not ranked: the
ranking is the brainstorm's job, and it depends on the re-derived weights.

**Detection (79.5% of cost at today's weights)**
1. **Isolated misses (58).** Only detector weights have ever moved them
   (75 → 43 in a Rung 3 adapter run). Adapters are blocked on deployment
   (NF4 costs +6.35; AWQ cannot take PEFT; the merge route is built,
   `mergedcontrol` not yet run).
2. **False detections (346).** 86-90% of `theoretical`/`note` predictions are
   false; suppressing non-dimension kinds is a CLOSED dead end (it destroyed
   legitimate matches). A different mechanism — a verifier pass, or kind-aware
   confidence — would need a registered prediction.
3. **Oversized sheets (19 of 99 corpus drawings excluded)** and **multi-page
   (8)**: tiled rendering / multi-page coverage extend scope rather than cost.

**Reading (16.2%)**
4. **Dropped GD&T symbols** — a concrete read-stage bucket now: 6 of 8 gdt
   char_type-only rows. Would feed the next adapter's targets; profile types
   (parser + scorer, `profile`+`line` / `profile`+`surface`) only pay once the
   reader emits symbols.
5. **Tall boxes are clipped** (126 of 223 matched ≥ 80 px, reading 0.47 at the
   shipped pad 24); the
   crop-pad lever is spent (four doses mapped). **Diameter ↔ Distance** 11
   each way. The adapter's tolerance-shaped targets are the largest known
   read-stage fault (CLAUDE.md §2).

**Scoring and gold**
6. `canon_char_type` word containment misreads at least two labels (the
   review's refuted prediction); 2 gold rows disagree with their drawings.
7. **`weights.json` with the client** — decides whether `read-lora-v1` ever
   ships, and how the table in §1 ranks. Zero GPU.

**Validation**
8. Re-measure the crop win on the test split; dev was optimistic by +31.25.
9. Score the Ø-zone fix with a real run (or add a CPU "score re-parsed dumps"
   mode, which is exact for parse-only changes).

---

## 4. Lessons from this session worth carrying

* **Cross-check operator answers against a closed-vocabulary probe before
  acting on them.** It turned "4 fixable" into "2 fixed, by a different fix".
* **Write review questions that name the known traps** (Ø is usually the zone
  sign; a stacked frame carries two characteristics). The Q2 wording was the
  review's defect.
* **A fix pays only when it is the LAST fault on the row, and only on an
  escaped row.** `wrong_fields` and per-row taxonomy now answer both before a
  fix is written.
* **Look at what dominates the cost before choosing the next lever.** Three
  arms this session worked the 16% slice.
