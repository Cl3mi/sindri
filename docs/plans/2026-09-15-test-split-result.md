# The TEST split — the first honest generalization number

Measured 2026-09-15, run `r3-awqtest`: production's exact serving configuration
(72B AWQ, same image, same prompts `aa7659f1929184ea`, no adapter, default crop)
on the frozen test split. **Nothing had ever predicted or scored there.**

**One line: 165.18 against dev's 133.93 — +31.25, or +23% more reviewer effort
on drawings nothing was tuned against. Roughly half of that gap is gold
coverage rather than the model, and the half that is not is read quality.**

---

## 1. The numbers

| | dev (`r3-awqcontrol`) | **TEST (`r3-awqtest`)** |
|---|---|---|
| documents scored | 15 | **11** |
| gold values | 311 | 292 |
| **review cost** | 133.93 | **165.18** |
| recall | 0.7170 | **0.6301** |
| missed | 28.3% | **37.0%** |
| `field_acc` | 0.4798 | **0.3804** |
| silently wrong | 22.8% | 21.9% |
| silent per MATCHED row | 31.8% | **34.8%** |
| gold values per document | 20.7 | **26.5** |

Every identity reconciles: matched + missed = `n_gold` on both sides, and
`missed_diagnosis` sums to `missed` on both.

---

## 2. Three caveats, all material

**It is not a comparison.** Different document set, so `_check_comparable`
refuses it against any dev report — correctly. There is no `ci95`, no
weight-robustness, and no per-document pairing. It is a standalone number.

**n = 11 documents**, after the scope policy dropped 6 multi-page and 2
oversized, and after one gold document was excluded for having no drawing at
all (§4).

**The test split is DELIBERATELY adversarial and is not a sample of the
corpus.** `splits.py` forces the structurally atypical `variants` into it. The
evidence is in the exclusions: **6 of its 19 predicted documents are multi-page
(32%)** against **1 of 20 in dev (5%)** and **8 of 99 corpus-wide (8.1%)** — so
six of the corpus's eight multi-sheet drawings sit in this one split. And the 11
that survived the policy carry **28% more gold values per document** than dev's
15.

So this is a stress number, not an unbiased estimate. **The honest range to
quote is 134 to 165**: dev is representative of typical drawings (20.0% clamped
against 19.2% corpus-wide, 5% multi-page against 8.1%), test is the hard end.
Quoting either alone overstates something.

---

## 3. The decomposition — over half the recall gap is not the model

`missed_diagnosis` splits the misses, and the split differs sharply:

| | dev | TEST |
|---|---|---|
| contended | 19 | 39 |
| isolated | 58 | 38 |
| **unlocated** | **11 (3.5% of gold)** | **31 (10.6% of gold)** |

`unlocated` is a missed gold row whose BALLOON could not be located
(`score.py:298` — `gold_pos(...) is None`). It is a property of the ingest of
the stamped drawings, not of detection: the pipeline was never given a position
to find. Each one costs `w=10`, the heaviest weight there is.

* **Recall on located gold only: dev 0.7433, test 0.7050.** The gap narrows from
  0.087 to **0.038** — less than half of what the headline shows.
* At dev's unlocated share of missed, the test cost would be **~149.7** rather
  than 165.18. So of the +31.25, roughly **+15.5 is gold coverage** and
  **+15.7 is genuine difficulty**.

**And `ingest` already knows how to settle what those rows are.**
`_unlocated_kind_histogram` exists precisely for this, and its docstring says:
*"a verbal requirement never had a balloon, so counting it as unlocated
understates how well the DIMENSIONS are covered."* `ingest --summary` reports
`unlocated_kinds` and `unlocated_char_types`. Running it is GPU-free — but see
the warning in §4 before pointing it at the real gold directory.

---

## 4. What is NOT explained away

**`field_acc` 0.4798 -> 0.3804.** It is computed on MATCHED rows only, so
unlocated gold cannot touch it. That is a **21% relative drop in read quality**
on unfamiliar templates, and it is the finding that should travel.

It is also exactly what this campaign's central result predicts. `r3-hybrid` and
`r3-cropctx` between them showed that read quality is dominated by what the
reader is handed, not by the reader: degrading the boxes costs -0.206, and
giving the same reader 18 more pixels of context buys +0.049. An unfamiliar
template produces unfamiliar layouts, hence worse crops, hence worse reads —
without the model being any worse at reading.

**A corpus fact surfaced on the way:** gold covers **one more document than
`corpus/originals` contains** (100 against 99). `predict` selects on the
drawings it can find, so it stopped at 19 of 20 test documents and reported no
failure; `score` now excludes and prints those. **Do not re-run `ingest` over
the real gold directory to investigate this** — with a drawing missing, a
re-ingest would silently drop that gold document and the frozen split
(`6d174d5e4f1b9228`) would then name a document gold no longer has. Ingest to a
throwaway `--out` instead.

---

## 5. What this changes

**For the client.** The figures shown so far are dev figures, and dev is the
split ten-plus arms were selected against. The product should be described with
a range and a reason: **~134 review cost on typical single-sheet drawings,
~165 on structurally atypical ones**, with 75 of 99 drawings in claimed scope
at all. That is a better story than a single number, because it is the true one
and it is defensible.

**For the campaign.** Three things, in order:

1. **Settle the unlocated rows** (GPU-free, one command, throwaway `--out`). If
   they are verbal requirements that never had balloons, the test gap is
   materially smaller than 165 suggests and the harness is charging `w=10` for
   rows no detector could ever find. If they are dimensions, gold coverage is a
   real defect worth fixing before any further arm is judged on `missed`.
2. **`cropctx48`** — already registered and built, unaffected by any of this.
3. **Re-measure the crop win on test** once the dose is settled. A -2.07 that
   only exists on the tuned split is worth much less than one that survives here,
   and this is the split where a context change should matter MOST, because
   unfamiliar layouts are where the crop is most likely to be starving the
   reader.

**For the harness.** Every future arm's headline should carry which split it is
from. The dev number is not wrong; it is answering a narrower question than
anyone reading it assumes.
