# Read accuracy by kind — 15% of rows produce 41% of the silent errors

Measured 2026-09-17 from `read_accuracy_by_kind`, built the day before after
`r3-tallpad` showed the worst crop-height band was a KIND effect. **No GPU time
was spent.** Shipped configuration (`r3-cropctx`, pad 24), dev split, 223 matched
rows.

> **Update 2026-09-22:** the `theoretical` part of this analysis is corrected.
> The labelling collision below is real, but fixing it is worth **zero rows**:
> they are wrong in their values as well. Priced with `--reparse-check` and
> reverted; see `docs/plans/2026-09-22-theoretical-parser-result.md`.

**One line: every non-dimension kind reads at or near ZERO, and three of them at
exactly 0.0000 — and the reason is not the model. The pipeline emits a
`char_type` the client's inspection sheet never uses, so those rows are wrong by
construction.**

---

## 1. The table

| kind | n | share | `field_acc` | escaped | escaped/row |
|---|---|---|---|---|---|
| `dimension` | 189 | 84.8% | **0.6085** | 38 | 0.20 |
| `gdt` | 17 | 7.6% | **0.1765** | 11 | 0.65 |
| `theoretical` | 9 | 4.0% | **0.0000** | 8 | 0.89 |
| `surface` | 4 | 1.8% | **0.0000** | 4 | 1.00 |
| `note` | 4 | 1.8% | **0.0000** | 3 | 0.75 |

Reconciles: 223 rows, `not_measured` 0, and the escaped column sums to 64 —
the taxonomy's `escaped_error` exactly.

**Non-dimension kinds are 34 rows (15.2%) and produce 26 of the 64 silent
errors (41%).** A non-dimension row is **3 to 5 times** more likely to ship
silently wrong than a dimension row. And `dimension` alone reads at 0.6085,
well above the 0.5291 headline — the headline was being dragged down by a
sixth of the corpus.

---

## 2. It is a labelling collision, not a read failure

`gold char_type → predicted Theoretical` totals **exactly 9** — every one of the
9 `theoretical` predictions:

| gold said | count |
|---|---|
| `Distance` | 4 |
| `Flatness` | 2 |
| unmapped / none | 3 |

The same shape holds for `note`: `gold → Note` totals **exactly 4**, the 4 note
predictions (`Distance` 2, `Diameter` 1, unmapped 1).

The mechanism is deterministic. `extract._HINTS` maps detector kind → parser
hint, and `parser.parse_value` then does:

```python
if hint == "theoretical":
    c.char_type = THEORETICAL        # unconditional, ignores the text
```

`normalize.char_type_equal` is strict equality on canonical forms, and
`_compare_fields` requires all four fields to agree. **So a `theoretical`
prediction can never be scored correct unless gold also says "Theoretical" —
and gold's vocabulary has no such value.** The corpus-wide
`unlocated_char_types` histogram lists Abstand, Distance, Diameter, Durchmesser,
Perpendicularity, Surface shape, "Diameter MIN" — never "Theoretical".

**This is a product defect, not a scoring artefact.** The reviewer would correct
every one of these rows, because the client's sheet says `Distance` and the
output says `Theoretical`. The box on the drawing means *this dimension carries
no tolerance* — it is not a different characteristic type, and `subtype` already
records that it was boxed, so nothing is lost by classifying the text instead.

---

## 3. What it is worth, and how to find out for free

**Bounded above, honestly.** Of the 9 `theoretical` rows, at most **4** (those
whose gold says `Distance`) could be fixed by classifying from the text; the 2
whose gold says `Flatness` and the 3 with no mappable gold type are not reachable
this way. And fixing `char_type` only makes a row *fully correct* if `nominal`
and both tolerances already agree — so the real number is lower still.

**`app/eval/reparse.py` exists for exactly this question** and answers it in CPU
seconds: dumps store `raw_text`, so `parse_value` can be re-run offline over
every stored row and compared against gold. Its docstring is explicit —
*"turns 'would this parser change help?' from a 9 h GPU arm into a CPU second"* —
and it duplicates `_HINTS` with a test that fails if the copy drifts.

The gate it defines: with an unmodified parser `identical == n_pairs`, because
that is where the stored fields came from. With a candidate edit in place,
**`would_fix - would_break` is the bound**.

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-cropctx" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --name reparse-probe --out "$HOME/sindri-client-data/reports/reparse-probe.report.json" --reparse-check
```

---

## 4. This is NOT the closed char_type dead end

`CLAUDE.md` §3 closes **synonym-map additions** — adding German words to
`CHAR_TYPE_SYNONYMS` moved exactly nothing, and the note says "do not propose
more synonym entries". That is about GOLD's vocabulary.

This is the **prediction** side: the pipeline emitting a value gold never uses,
for a whole detector kind, 100% of the time. Different mechanism, different fix,
and §3 itself points here — it names `parser.py` inferring Diameter from a
leading Ø as "a read-stage fault", which is the same function and the same kind
of defect.

## 5. What this does NOT license

* **Not a change to `char_type_equal` or the synonym map.** The fix belongs on
  the side that is wrong, which is the emission.
* **Not touching `note`'s `nominal = text`.** A note reference legitimately
  carries its text as the value; only its `char_type` is in question, and that
  is 4 rows.
* **Not `gdt` yet.** 17 rows at 0.1765 is the largest non-dimension bucket and
  its confusion (`gold → Flatness` totals 12, mostly from unmapped gold) has a
  different shape. Measure the `theoretical` change first; it is the clean case.
