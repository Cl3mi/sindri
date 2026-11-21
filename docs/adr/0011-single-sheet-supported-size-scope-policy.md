# 0011. The product's claimed scope is single-sheet drawings at a size the renderer handles at full resolution

Status: Accepted
Date: 2026-09-09 (set); measured over the corpus 2026-09-11

## Context

Two structural properties of the pipeline made some drawings unscoreable for reasons
unrelated to model quality:

- `render_page` takes `page_index=0`, so gold on sheet 2 was always an unrecoverable miss,
  charged at the heaviest weight (`w=10`).
- `render.py` clamps any page above an 80 MP pixel budget (large-format sheets of 2.5 m ×
  1.7 m exist) to a reduced effective dpi, to avoid PIL's decompression-bomb limit. The
  clamped drawings scored 283.75 review cost at 0.371 recall against 141.62 at 0.728 for
  the rest, and raising the budget was measured and lost (80 → 150 MP: isolated misses
  251 → 252; quoted from `CLAUDE.md` §3, not reproducible from the committed
  `render150-*` digests, which predate the coordinate fix).

Scoring them measured a capability the product never claimed, and it made arm comparisons
noisy.

## Decision

The deliverable covers **single-sheet drawings at a size the renderer handles at full
resolution**. In the harness this is `score --max-pages 1 --dpi 300 --exclude-clamped`
(both off by default, so pre-policy numbers keep their meaning), and
`_check_comparable` refuses a scoped report against an unscoped one. In the product the
same two tests are surfaced at import: `app/batch/pdf_facts.py` asks the renderer's
public `effective_dpi()` the same question the probe asks, and staging marks
multi-page / oversized drawings *out of scope* — Include is off by default and including
one stores `allowed_out_of_scope`.

## Alternatives considered

- **Tiled rendering for oversized sheets** — not built. Note the distinction recorded in
  the handoff: the dead end "do not build tiled rendering" is about resolution recovering
  misses *inside* supported drawings; covering the 19 clamped drawings is a *coverage*
  question that has never been measured.
- **Reading later sheets** — `render_page` already takes `page_index` and gold exists for
  them, so this is reachable, but extraction does not ask for it; deferred.
- **Scoring everything and quoting a single number** — rejected: the dev/test gap showed
  the number's meaning depends on structure.

## Consequences

Measured over all 99 client drawings: **75 supported (75.8%)**, 19 oversized, 8 multi-page,
3 both, 24 excluded (`supported + excluded == 99`). Dev keeps 15 of 20 documents and is
representative on both axes (4 of 20 clamped against 19.2% corpus-wide).

Accepted costs:

- **A quarter of the client's drawings are out of claimed scope.** The oversized exclusion is
  2.4× the multi-page one and was the invisible half: every earlier statement of scope
  leaned on `multi_page_docs`.
- **The numbers need caveats.** Production quoted as a range (~134 on typical single-sheet
  drawings, ~165 on the structurally atypical frozen test split); dev was optimistic by
  +31.25, and about a third of that gap is an artefact of how unlocated gold is priced.
- Scope lives in two places: harness flags and the staging badge. Both ask the same public
  function precisely so they cannot disagree, and `render.effective_dpi()` was made public
  for exactly this reason.
- Gold covers one more document than `corpus/originals` contains; `score` excludes those
  explicitly instead of charging them as misses.

## Lessons

- A guard that reports the policy as a count (`probe --summary`, whole corpus) is worth more
  than one that describes it, and the first version of the partial-run guard misdiagnosed a
  finished run as unfinished — a guard that misdiagnoses costs what the warning it replaced
  cost.
- Related: [0010](0010-chunked-resumable-upload-protocol.md),
  [0008](0008-two-stage-vlm-detect-then-read-with-crop-context.md),
  [0006](0006-pipeline-and-eval-kept-separate.md).
