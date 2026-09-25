# gdt review — RESULT: the reader is fine, the parser has no profile symbols

Reviewed 2026-09-25 by the operator in the local review app (8 of 8 rows,
none invalid). Decision table and prediction registered beforehand in
`docs/plans/2026-09-22-gdt-review.md` §4. Tally: `docs/eval/gdt-review-tally.json`
(counts only).

**One line:** on all 8 rows the reader DID transcribe the GD&T symbol, and 5 of
the 8 frames are PROFILE tolerances — a characteristic `parser._GDT_SYMBOLS`
has no entry for, so `_gdt_type` silently calls every one of them Flatness.

---

## 1. The tally

| | count |
|---|---|
| answered | 8 of 8 |
| symbol in the transcription (glyph or word) | **8** → `prediction_fix: parser` 8, `read_stage` 0 |
| drawing shows | **Profile of a line 3, Profile of a surface 2**, Flatness 1, Angularity 1, other 1 |
| gold side | `needs_synonym_word` 4, `scorer_reads_it_differently` 2, `gold_disagrees_with_drawing` 2, `scorer_already_matches` 0 |
| **freed without a GPU** | **4** (g4, g5, g7, g8 — all profile tolerances) |

## 2. Against what was registered

| registered | outcome |
|---|---|
| `freed_without_gpu` ≥ 3 → parser arm | **4 → parser arm** |
| `read_stage` majority → no parser arm | 0 of 8 — the reader does not drop GD&T symbols |
| `gold_disagrees_with_drawing` ≥ 2 → raise with the client | **2 → raise with the client** |
| PREDICTION: the 2 Position and 1 Parallelism rows come back `scorer_already_matches` | **REFUTED, 0 of 3** |

**The refuted prediction is a scoring finding, as registered.** The three rows
whose labels the scorer reads as Position or Parallelism came back: two where
the operator says the label names what the drawing shows (Flatness, and a
characteristic outside ISO 1101) while the scorer reads it as something else,
and one where gold disagrees with the drawing. So word-containment matching in
`canon_char_type` misreads at least two labels. On the Flatness row that is the
row's ONLY fault — the prediction is Flatness (by default) — so a correct
reading of that label alone would make it fully correct. Diagnosing it needs
the label, which is the operator's to read.

## 3. Why this is bigger than 4 rows

`normalize.py` already records the gap: profile is "DELIBERATELY NOT MAPPED …
the parser has no such char_type constant … Fixing these means extending
`parser._GDT_SYMBOLS`". `_GDT_SYMBOLS` covers 8 characteristics; ISO 1101 has
14. Straightness, both profiles, symmetry, runout and total runout all fall to
`_gdt_type`'s Flatness default. On dev, profile is 5 of the 8 char_type-only gdt
rows; corpus-wide the same default applies to every such frame, and a Flatness
guess that happens to be wrong ships as a silent error.

**Worth on dev**, at today's weights: the 4 freed rows include at least 1
silent error (5 of 8 are silent, at most 4 of those are outside the freed
set), so −0.33 to −1.33 review cost. The larger value is removing a systematic
mislabel.

## 4. What the parser arm needs, and what is not known yet

Three coherent changes: profile constants in `parser` (+ `_GDT_SYMBOLS`
entries), the same constants in `score._PARSER_CHAR_TYPES`, and synonym entries
so the scorer reads the 4 labels as profile of a line / surface.

**Two facts the review did not capture**, because the tally carries no text:

1. which glyph (or word) the reader wrote for the profile frames — the
   standard ⌒ / ⌓, or a look-alike;
2. which word in the 4 labels tells line from surface — the scorer finds no
   type word in them today, and "Profil" alone cannot separate the two.

**Gate, unchanged from the registration:** `--reparse-check` identity 223/223
on the unmodified parser; then `would_break` = 0 and `would_fix` = 4. A synonym
key must already be in `normalize._DIMENSION_WORDS` or it changes which gold
rows are scored at all (n_gold) — `tests/eval/test_normalize.py` guards that.

## 5. For the client

* The 2 `gold_disagrees_with_drawing` rows (one Angularity frame, one profile
  of a line) — gold names a different characteristic than the drawing shows.
* The 2 `scorer_reads_it_differently` rows — worth a look at the label wording
  when the weights are re-derived, since they point at a scoring-policy defect.

---

## 6. The probe corrects the tally (same day)

The operator chose a closed-vocabulary probe (`57f30f8`, `52ce9d6`) to supply
the two facts §4 names, with no client text reaching an agent. Per row, beside
the tally:

| row | operator: frame | operator: label | before the value | label words | scorer |
|---|---|---|---|---|---|
| g1 | Flatness | means it | Ø | position | Position |
| g2 | other | means it | Ø | position | Position |
| g3 | Angularity | disagrees | nothing | parallelität | Parallelism |
| g4 | Profile of a surface | means it | Ø | profile+surface | none |
| g5 | Profile of a line | means it | a capital letter | line+profile | none |
| g6 | Profile of a line | **disagrees** | nothing | **line**+profile | none |
| g7 | Profile of a line | **means it** | a capital letter | profile+**surface** | none |
| g8 | Profile of a surface | means it | nothing | profile+surface | none |

**The label side is conclusive:** the 5 labels the scorer cannot read carry
`profile` with `line` (2) or `surface` (3).

**The transcription side contradicts the tally.** On g3, g6, g8 nothing stands
before the value, and the probe found no symbol-class character and no listed
word anywhere in those transcriptions. On g1, g2, g4 only Ø does — normally a
cylindrical-zone sign, which profile tolerances never take. So on at least 6
of 8 rows the characteristic's symbol is not in the transcription in any form a
parser could map: a **read-stage** majority, not the parser majority the tally
reported. Only g5 and g7 hold a candidate (an unidentified capital letter).

**Why the tally overstated it:** Q2 asked "is that symbol in the transcription
(even a look-alike)?" without warning that Ø is usually the zone sign — that
wording is the review's defect, not the operator's. Four rows also conflict
with the label words: g6/g7 look swapped, and g1/g2 carry `position` labels and
a Ø zone against frame answers of Flatness / other (a stacked two-line frame,
e.g. flatness over position, would explain it). They may have been answered
before `505a667` fixed the stamped crop, which was wrong on 6 of 8 rows.

**Under the registered table, on the probe's evidence:** read-stage majority →
**no parser arm for profile symbols.** The dropped GD&T symbol becomes a
concrete, countable Rung-3 target (6 of 8 here). The profile SCORER side
(`profile`+`line` / `profile`+`surface`) is only worth adding together with a
prediction side that can emit profile types, so it waits too.

## 7. Registered before measuring: a Ø zone is never flatness

**Change:** in `parser._gdt_type`, when no GD&T symbol is recognised and the
text begins with a diameter sign (Ø / ⌀), return Position instead of Flatness.

**Mechanism:** the Flatness default is impossible for such a frame — flatness
never has a cylindrical zone. A Ø-zone frame whose symbol was dropped is most
often Position (straightness of an axis, orientation of an axis and coaxiality
also take one), so the default only has to beat something that cannot be right.
It needs no operator answer: it rests on the probe's Ø prefix and gold's label.

**Prediction:** identity gate 223/223 on the unmodified parser (held above).
With the change: `would_fix` ≥ 2 (g1, g2 — gold says position, the scorer reads
Position, and char_type is their only fault); g4 moves Flatness → Position and
stays wrong. `identical` drops by at least 3 (g1, g2, g4) and by exactly the
matched gdt rows that start with Ø and have no recognised symbol.

**Damage counter: `would_break`**, over all 223 pairs — any currently-correct
row the change flips wrong. It can move (the change touches every Ø-prefixed
gdt row), so it can falsify.

**Rule:** keep iff `would_break` = 0 and `would_fix` ≥ 1; otherwise revert the
single commit. A kept change still needs one scored run before it is quoted.
