# `fill_general_tolerance` — registration (frozen before any split is touched)

Registered 2026-10-05, before `score --gentol-check` exists and before any
split has been scored with a fill. Everything in §1–§5 is frozen. A pattern,
table entry or threshold changed after a split's numbers are seen makes that
split ineligible. Operator decisions (2026-10-05): DIN 7168 is dropped
completely; filled rows are AUTO-ACCEPTED (unflagged).

Background: `no_tolerance` flags dimensions whose read has no tolerance.
Gold carries the general tolerance on nearly every row, so those rows are
flagged_error by construction (`2026-09-25-policy-arms-result.md`). Applying the
ISO 2768-1 general tolerance named on the drawing turns them into correct rows,
for −1 each, and raises the auto-accept rate. The risk is a printed tolerance
the crop missed: it would be filled with the general value and ship silently,
+4 each (flagged 1 → escaped 5).

## 1. Frozen class pattern

```python
GENTOL_RE = re.compile(
    r"(?<!\d)2768(?:\s*-\s*1)?\s*[-–]?\s*([fmcvFMCV])[hklHKL]?(?![A-Za-z])")
```

* Only the FIRST class letter is used, lower-cased. The geometric letter
  (H/K/L) is accepted and ignored.
* Must match: `ISO 2768-mK`, `ISO 2768 mK`, `ISO 2768 - fH`, `2768-m`,
  `DIN ISO 2768-1 c`, `ISO 2768-1-m`. Must not match: `22768-m`,
  `2768 medium`, `ISO 2768` with no class letter.
* Searched in, per document: title-block grid fields (`label` and `value`),
  notes (`text_en`, `text_de`, `raw_text`), loose text (title fields with
  empty label and no "missing caption" reason, which is how `loose_text`
  emits them). **Source** of a document = the first of title → notes → loose
  that yields a match.
* **Conflict:** if two different class letters are found anywhere in the
  document, nothing is filled in it, and the document is counted as
  `conflict`.

## 2. Frozen ISO 2768-1 table: `iso2768(cls, nominal, char_type) -> ±value | None`

Permissible deviations in mm, symmetric. Ranges are "over lower, up to and
including upper"; the first range includes 0.5.

Linear (Table 1), for char_type **Distance** and **Diameter**:

| nominal | f | m | c | v |
|---|---|---|---|---|
| 0.5–3 | 0.05 | 0.1 | 0.2 | None |
| >3–6 | 0.05 | 0.1 | 0.3 | 0.5 |
| >6–30 | 0.1 | 0.2 | 0.5 | 1 |
| >30–120 | 0.15 | 0.3 | 0.8 | 1.5 |
| >120–400 | 0.2 | 0.5 | 1.2 | 2.5 |
| >400–1000 | 0.3 | 0.8 | 2 | 4 |
| >1000–2000 | 0.5 | 1.2 | 3 | 6 |
| >2000–4000 | None | 2 | 4 | 8 |

Radius and chamfer heights (Table 2), for char_type **Radius**:

| nominal | f | m | c | v |
|---|---|---|---|---|
| 0.5–3 | 0.2 | 0.2 | 0.4 | 0.4 |
| >3–6 | 0.5 | 0.5 | 1 | 1 |
| >6 | 1 | 1 | 2 | 2 |

`None` for: nominal < 0.5 or > 4000 (linear), a None cell, any other
char_type (angles, Reference, Theoretical, unknown or empty), and a nominal
that does not parse as a positive decimal (comma or period). A filled row gets
`upper_tol = "<v>"` and `lower_tol = "-<v>"` in the parser's own comma format,
which is exactly what `parse_value("N ±v")` emits, plus `tol_source="general"`.

## 3. Eligibility and the fit-notation guard (gold-free, on the READ)

A row is filled iff: `kind == "dimension"`, char_type is not `Reference`,
the nominal is non-empty, `upper_tol` and `lower_tol` are both empty, the
document has exactly one class, the table returns a value, and the guard
below does NOT match the row's `raw_text`.

```python
FIT_RE = re.compile(
    r"\d\s?(?:CD|EF|FG|JS|Z[ABC]|cd|ef|fg|js|z[abc]"
    r"|[A-HJKMNPR-VXYZ]|[a-hjkmnpr-vxyz])(?:01|1[0-8]|[0-9])(?![0-9])")
```

This is a number followed (with at most one space) by an ISO 286
fundamental-deviation letter or letters (I, L, O, Q and W excluded) and an IT
grade from 01 to 18. It matches `Ø20H7`, `20 h6`, `12 js5`, `Ø20 H7/g6`. It
also matches `3x5` and `M8x1` (x is a shaft letter). That is accepted as a
conservative skip, because threads and multiplicity callouts are not
general-tolerance rows either.

## 4. CPU gate — `score --gentol-check`, train (`r3-trainpredict`, scoped) first

Control = `--reapply-policy` dumps as today. Arm = the same dumps reapplied
with the fill (drops → fill → flags). Matching cannot move, because the fill
touches only tolerances and matching reads nominal and position. Every matched
pair's taxonomy transition is counted:

* **would_fix** = flagged_error → correct (−1 each)
* **would_break** = flagged_* → escaped_error (+4 each), broken down, first
  match wins:
  `fit_notation` (gold nominal matches FIT_RE) →
  `other:field` (filled tolerance equals gold, so another field was wrong and
  unflagging exposed it) →
  `other:gold_no_tolerance` (gold has neither tolerance) →
  `other:class_or_table` (gold equals the table value under a different class
  or table) →
  `printed_missed` (gold has a tolerance that is not the table value: printed
  at the callout, and the crop or read missed it)
* neutral transitions (still flagged by another reason) are reported, not
  gated.

**Gate (binding):** with n = would_fix + would_break and U = the Wilson 95%
upper bound (z = 1.96) on the break rate would_break / n,

    would_fix >= 4 * U * n   AND   would_fix >= MIN_FIX = 20

`MIN_FIX = 20` because 20 fixes over train's 49 scoped documents is about
−0.4 per document. That is below the smallest change this project has
shipped (Ø-zone, −0.67), so anything smaller is not worth a new pipeline
step.

**Kill rule:** if the gate fails on train, stop. Nothing is implemented in
the pipeline, nothing ships, and the negative result goes into the report.

**Exactness cross-check (registered):** the gate's fix and break counts
must reconcile exactly with the Stage 3 `--reapply-policy` taxonomy deltas on
the same split: correct +fix, flagged_error −fix−(its breaks), escaped +break.
If they disagree, a path is wrong and nothing is quotable.

## 5. Keep rule (binding) — Stage 3 pricing

Each split against its own reapplied control (fill off): train
`r3-trainpredict`, dev `r4-control`, test `r3-awqtest`, all scoped
(`--max-pages 1 --dpi 300 --exclude-clamped`). Keep iff, on **all three**:

1. mean review cost falls;
2. better under 6 of 6 `WEIGHT_GRID` weightings;
3. auto-accept precision does not fall;
4. auto-accept rate rises strictly.

If the gate passed but the keep rule fails: revert the flag-pass move and the
fill, and ship nothing. Test is DERIVED until the next GPU run confirms it.

**Invariant (registered):** with the fill disabled, the reordered pipeline
produces byte-identical flags to today's on train, dev and test. Checked as
reapplied digests identical to pre-change digests except for the `run` name.

## 6. Predictions (for the record; not part of any rule)

* Coverage: a class on 60-85% of train documents, mostly `m`, mostly from the
  title block.
* would_fix 60-110 on train; would_break 5-20, mostly `printed_missed`, then
  `other:field`.
* Gate passes on train. Dev cost −0.8 to −1.5 per document; auto-accept rate
  up by +0.03 to +0.07 on dev.
