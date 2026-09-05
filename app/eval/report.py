"""Aggregate DocScores into a RunReport; compare two RunReports with paired
per-document deltas + bootstrap CIs. compare_runs is the comparability
gatekeeper: it RAISES on any mismatch (doc set, per-doc gold hash, weights,
match params) instead of producing a quietly meaningless number."""
import random
from typing import Dict, List, Tuple

from app.eval.models import (DocScore, MatchParams, ReviewCostWeights,
                             RunConfig, RunReport, SCHEMA_VERSION)
from app.eval.score import _PARSER_CHAR_TYPES

N_BOOTSTRAP = 10_000

# How many clamped documents `summarize` lists in detail. Deliberately NOT
# summarize()'s `top`, which sizes the worst-docs triage list: a diagnostic list
# must not shorten because someone retuned an unrelated knob. The true count is
# always clamped_vs_unclamped.clamped.n, so truncation is visible by comparing
# len(clamped_docs) against it.
CLAMP_LIST_MAX = 25


def aggregate(run_name: str, config: RunConfig, weights: ReviewCostWeights,
              match_params: MatchParams, doc_scores: List[DocScore],
              splits_hash: str = "", split_used: str = "all") -> RunReport:
    n_gold = sum(d.n_gold for d in doc_scores)
    n_pred = sum(d.n_pred for d in doc_scores)
    matched_gold = sum(round(d.recall * d.n_gold) for d in doc_scores)
    matched_pred = sum(round(d.precision * d.n_pred) for d in doc_scores)
    taxonomy: Dict[str, int] = {}
    for d in doc_scores:
        for k, v in d.counts.items():
            taxonomy[k] = taxonomy.get(k, 0) + v
    escaped = taxonomy.get("escaped_error", 0)
    return RunReport(
        run_name=run_name, config=config, weights=weights,
        match_params=match_params, splits_hash=splits_hash,
        split_used=split_used,
        doc_scores=sorted(doc_scores, key=lambda d: d.doc_id),
        mean_review_cost=(sum(d.review_cost for d in doc_scores)
                          / len(doc_scores)) if doc_scores else 0.0,
        micro_recall=(matched_gold / n_gold) if n_gold else 1.0,
        micro_precision=(matched_pred / n_pred) if n_pred else 1.0,
        escaped_rate=(escaped / n_gold) if n_gold else 0.0,
        taxonomy=taxonomy,
    )


def _note_counts(report: RunReport) -> Tuple[Dict[str, int], int]:
    """Aggregate the tags scoring left on matched pairs.

    Reads ONLY the `cause:` and `misplaced` tokens — a fixed vocabulary written
    by score._cause. It never touches `field_errors`, which spells out client
    values, so the digest stays safe to commit and to show an agent."""
    causes: Dict[str, int] = {}
    misplaced = 0
    for d in report.doc_scores:
        for p in d.pairs:
            for note in p.notes:
                if note.startswith("cause:"):
                    key = note.split(":", 1)[1]
                    causes[key] = causes.get(key, 0) + 1
                elif note == "misplaced":
                    misplaced += 1
    return causes, misplaced


# The field names score._compare_fields can report, in digest order. A closed
# vocabulary is what makes reading the left-hand side of a field_errors entry
# safe: anything else is bucketed as "other" instead of forwarded.
_FIELD_NAMES = ("char_type", "nominal", "upper_tol", "lower_tol")


def _field_failure_counts(report: RunReport) -> Tuple[Dict[str, int],
                                                      Dict[str, int]]:
    """Which FIELD each matched-but-wrong row got wrong, and in what combination.

    field_acc collapses a four-way conjunction — _compare_fields requires
    char_type, nominal, upper_tol and lower_tol to agree — so "196 wrong rows"
    says nothing about which of the four moved. A tolerance the reader dropped
    and a nominal it hallucinated need different prompt text, and nothing in the
    digest distinguished them.

    Reads ONLY the field NAME: the text left of the first ":" in each entry,
    which score._compare_fields writes from the tuple above. The value text to
    the right is never touched, so this is as values-blind as _note_counts.

    Two aggregates, because they answer different questions. The per-field
    histogram sizes each field's contribution; the signature histogram says
    whether failures co-occur, which is what separates "the reader omits
    tolerances" from "the reader misreads whole callouts".

    BOTH key spaces are namespaced (`field:<name>`, `fields:<a>+<b>`) rather
    than bare. The pre-commit hook blocks any staged .json containing
    `"upper_tol"` or `"lower_tol"` as a quoted token, because that is the shape
    of a gold record and it cannot tell a COUNT keyed by a field name from a
    VALUE stored under one. Digests are committed, so a bare key would make
    every future digest need the SINDRI_ALLOW_DATA_COMMIT bypass — weakening a
    data guard permanently to save six characters.

    The signature space needs the prefix for a reason that is easy to miss: a
    multi-field key like `char_type+nominal+upper_tol+lower_tol` is already
    safe, because the token is not quote-delimited there. It is the SINGLE-field
    signature — a row where only `upper_tol` was wrong — that emits a bare
    `"upper_tol"` key. Prefixing the whole space removes the special case
    instead of relying on nobody rediscovering it."""
    per_field: Dict[str, int] = {}
    signatures: Dict[str, int] = {}
    for d in report.doc_scores:
        for p in d.pairs:
            if not p.field_errors:
                continue
            names = set()
            for err in p.field_errors:
                name = err.split(":", 1)[0].strip()
                names.add(name if name in _FIELD_NAMES else "other")
            for n in names:
                per_field[f"field:{n}"] = per_field.get(f"field:{n}", 0) + 1
            # Fixed order, so the same combination always produces the same key.
            key = "fields:" + "+".join(n for n in _FIELD_NAMES + ("other",)
                                       if n in names)
            signatures[key] = signatures.get(key, 0) + 1
    return per_field, signatures


# The closed vocabulary score._ctype_label may emit. Imported rather than
# copied: it IS score's whitelist, and a digest that accepted a wider set than
# score produces would forward whatever a future note format put there.
_CTYPE_VOCAB = frozenset(
    _PARSER_CHAR_TYPES | {"empty"}
    | {f"unmapped({p})" for p in _PARSER_CHAR_TYPES | {"none", "ambiguous"}})


def _char_type_confusion(report: RunReport) -> Tuple[Dict[str, int], int]:
    """Gold char_type -> predicted char_type, over the rows where they disagree.

    `wrong:char_type` is the largest single failure mode on this corpus — 115 of
    308 matched pairs — and `field_failure_modes` records only THAT the type is
    wrong. Which pair of types was confused is what routes the work, and the
    routes are genuinely different: `parser.py` infers Diameter from a leading
    Ø, so a gold Diameter predicted as Distance is a dropped symbol the read
    stage can fix, the reverse is an invented one, and a Position/Flatness swap
    is `parser._GDT_SYMBOLS` rather than anything the model did.

    It also settles a question policy alone could not. A synonym-map gap and a
    real perception failure produce the same `wrong:char_type` count; only this
    aggregate separates them, and the `unmapped` bucket is what says which.

    Selects rows exactly as `_field_failure_counts` does — the field name left
    of the first ":" — so the counts reconcile against `field:char_type` by
    construction rather than by coincidence. Both label sides arrive already
    canonicalised by `score._ctype_label`; anything outside that vocabulary is
    bucketed `other` rather than forwarded, for the same reason an unknown field
    name becomes `field:other`: a values-blind file must not inherit an upstream
    format change. `not_measured` counts char_type-wrong rows from reports that
    predate the note, for the same reason `field_failure_modes_not_measured`
    exists — a silent `{}` would read as "no confusions" for a run with 115."""
    out: Dict[str, int] = {}
    not_measured = 0
    for d in report.doc_scores:
        for p in d.pairs:
            if not any(e.split(":", 1)[0].strip() == "char_type"
                       for e in p.field_errors):
                continue
            note = next((n.split(":", 1)[1] for n in p.notes
                         if n.startswith("ctype:")), None)
            if note is None:
                not_measured += 1
                continue
            gold, _, pred = note.partition("->")
            key = (f"chartype:{gold if gold in _CTYPE_VOCAB else 'other'}"
                   f"->{pred if pred in _CTYPE_VOCAB else 'other'}")
            out[key] = out.get(key, 0) + 1
    return out, not_measured


# The three ways score._failure_modes can describe a wrong field.
_FAILURE_MODES = ("missing", "wrong", "spurious")


def _failure_mode_counts(report: RunReport) -> Tuple[Dict[str, int], int]:
    """`<mode>:<field>` histogram over matched-but-wrong rows, plus the number
    of wrong rows that carry no mode tag at all.

    The second number is not decoration. A report scored before
    score._failure_modes existed carries no tags, and an empty histogram would
    read as "no field failed" — the same confident wrong answer that
    DocScore.frame_origin_frac's None default exists to prevent, which fooled
    this author once already. A non-zero not_measured means: re-score before
    believing this block.

    These keys need no `field:` prefix — the pre-commit guard screens for a
    quote-delimited `"upper_tol"`, and `"missing:upper_tol"` is not that."""
    counts: Dict[str, int] = {}
    not_measured = 0
    for d in report.doc_scores:
        for p in d.pairs:
            if not p.field_errors:
                continue
            tags = [n for n in p.notes
                    if n.split(":", 1)[0] in _FAILURE_MODES]
            if not tags:
                not_measured += 1
                continue
            for t in tags:
                counts[t] = counts.get(t, 0) + 1
    return counts, not_measured


def _dropped_tolerances(report: RunReport, anonymizer) -> Dict:
    """Rows where the pipeline produced no tolerance, and how many DISTINCT gold
    tolerance pairs those rows use — per document.

    This is the winnability test for the biggest failure bucket. `missing:` on a
    tolerance has two opposite explanations and the same count:

      few distinct values  -> a general tolerance (ISO 2768, title block) that is
                              not printed beside the callout at all, so NO reader
                              working from a callout crop can produce it. Those
                              rows are unwinnable by any prompt or detector, and
                              field accuracy has a ceiling well below 1.0.
      many distinct values -> the tolerances really are printed per callout and
                              the pipeline produced nothing for them, i.e. the
                              box clipped them off. That IS fixable.

    Per document, never pooled: a general tolerance is one value repeated within
    ONE drawing, so pooling 20 drawings' separate defaults would report 20
    "distinct" values and destroy exactly the signal this measures.

    Cardinalities only. A count of distinct tolerances says nothing about what
    any of them is."""
    measured = [d for d in report.doc_scores
                if d.dropped_tol_rows is not None
                and d.dropped_tol_distinct is not None]
    return {
        "rows": sum(d.dropped_tol_rows for d in measured),
        # Non-zero means re-score before believing this block.
        "not_measured": len(report.doc_scores) - len(measured),
        "docs": [{"doc": anonymizer(d.doc_id), "rows": d.dropped_tol_rows,
                  "distinct": d.dropped_tol_distinct}
                 for d in sorted(measured, key=lambda d: -d.dropped_tol_rows)
                 if d.dropped_tol_rows],
    }


def _confidence_by_taxonomy(report: RunReport) -> Tuple[Dict[str, Dict[str, int]],
                                                        int]:
    """Read-confidence band × taxonomy over matched pairs, plus the pairs with
    no band recorded.

    This is the price list for a review-flag threshold move: rows below a
    candidate threshold become flagged (w=1), and whether that is a win depends
    entirely on how many of them were wrong (w=5 if they escape). It also sizes
    the confound in every prompt arm — a prompt edit changes token confidences,
    so some of an arm's cost delta is threshold churn rather than reading.

    Bands are the fixed labels score._conf_bucket writes; not_measured counts
    pairs from reports that predate the tag, for the same reason
    field_failure_modes_not_measured exists."""
    out: Dict[str, Dict[str, int]] = {}
    not_measured = 0
    for d in report.doc_scores:
        for p in d.pairs:
            band = next((n.split(":", 1)[1] for n in p.notes
                         if n.startswith("conf:")), None)
            if band is None:
                not_measured += 1
                continue
            row = out.setdefault(band, {})
            key = p.taxonomy or "unset"
            row[key] = row.get(key, 0) + 1
    return out, not_measured


def _cause_crosstab(report: RunReport) -> Dict[str, Dict[str, int]]:
    """cause × misplaced × silent-or-flagged, for every matched-but-wrong row.

    `misread` is routinely read as "perception failure", but a pair matched
    further from its balloon than misplaced_frac may have read a DIFFERENT
    callout perfectly. Those are pairing failures, and no prompt edit can move
    them — so the share of misread that is misplaced is the number that decides
    whether a read-prompt arm is worth a card at all. Both tags were already on
    every pair (score_doc writes them); only the join was missing.

    Reads the same fixed note vocabulary as _note_counts, plus the taxonomy
    string, so it carries no client text."""
    out: Dict[str, Dict[str, int]] = {}
    for d in report.doc_scores:
        for p in d.pairs:
            cause = next((n.split(":", 1)[1] for n in p.notes
                          if n.startswith("cause:")), None)
            if cause is None:
                continue
            row = out.setdefault(cause, {"total": 0, "misplaced": 0,
                                         "on_target": 0, "escaped": 0,
                                         "flagged": 0})
            row["total"] += 1
            row["misplaced" if "misplaced" in p.notes else "on_target"] += 1
            row["escaped" if p.taxonomy == "escaped_error" else "flagged"] += 1
    return out


def _clamp_split(report: RunReport, anonymizer,
                 limit: int = CLAMP_LIST_MAX) -> Tuple[List, Dict]:
    """Clamped documents, and clamped-vs-unclamped macro means.

    render.py reduces dpi on sheets that would exceed the 80 MP budget, so those
    documents are extracted at up to a third of the requested resolution. Before
    anyone reads a low recall as a statement about the model, this says whether
    the misses concentrate there. Ids come from the local salt, so they join to
    worst_docs — unlike the predict log's, which are minted per container.

    THREE buckets, not two. effective_dpi is 0.0 in any DocScore written before
    the field existed, and folding those into "unclamped" would report "nothing
    was clamped" for a run where four documents were — a confident wrong answer
    from a view whose entire job is interpretability. Unknown gets its own
    bucket, so `unknown_dpi.n > 0` reads as "re-score before believing this"."""
    requested = report.config.dpi
    known = [d for d in report.doc_scores if d.effective_dpi > 0.0]
    unknown = [d for d in report.doc_scores if d.effective_dpi <= 0.0]
    clamped = sorted((d for d in known if d.effective_dpi < requested - 0.5),
                     key=lambda d: d.effective_dpi)
    clamped_ids = {d.doc_id for d in clamped}
    rest = [d for d in known if d.doc_id not in clamped_ids]

    def block(docs):
        n = len(docs)
        return {
            "n": n,
            # MACRO: unweighted mean over documents. The headline micro_recall
            # pools rows across the run, so the two are different statistics —
            # the name says so, because an unlabelled "mean_recall" next to a
            # micro headline is exactly the mis-read this plan exists to prevent.
            "macro_mean_recall": (round(sum(d.recall for d in docs) / n, 4)
                                  if n else None),
            "mean_review_cost": (round(sum(d.review_cost for d in docs) / n, 2)
                                 if n else None),
            # A SUM, not a mean: it answers "how much of the undetected-miss
            # mass sits in this bucket?", which is what decides between tiled
            # rendering for oversized sheets and detector tile-size work.
            "missed_isolated": sum(d.missed_isolated for d in docs),
        }

    listed = [{"doc": anonymizer(d.doc_id),
               "effective_dpi": round(d.effective_dpi),
               "recall": round(d.recall, 4),
               "review_cost": d.review_cost,
               # Per sheet, because the fix is per sheet: a page that merely
               # overshot the budget can be un-clamped by raising it, while a
               # 598 MP sheet needs true tiling. Averaging the two hides that.
               "missed_isolated": d.missed_isolated} for d in clamped[:limit]]
    return listed, {"clamped": block(clamped), "unclamped": block(rest),
                    "unknown_dpi": block(unknown)}


def _kind_totals(report: RunReport) -> Tuple[Dict[str, int], Dict[str, int],
                                             Dict[str, int]]:
    """Predictions, unmatched predictions, and MATCHED predictions by detector
    kind, summed over the run. Kind names are a fixed detector vocabulary, never
    client text.

    The three reconcile per kind: preds[k] == matched[k] + false[k], because a
    prediction either paired with in-scope gold or it did not. That identity is
    what turns "N false detections are out-of-scope kinds" from a quoted number
    into a checked one."""
    preds: Dict[str, int] = {}
    false: Dict[str, int] = {}
    matched: Dict[str, int] = {}
    for d in report.doc_scores:
        for k, v in d.pred_kinds.items():
            preds[k] = preds.get(k, 0) + v
        for k, v in d.false_kinds.items():
            false[k] = false.get(k, 0) + v
        for k, v in d.matched_kinds.items():
            matched[k] = matched.get(k, 0) + v
    return preds, false, matched


# 1% of the page diagonal. NOT an epsilon on float noise: PDF rects round-trip
# with deviations around 1e-5 of the diagonal (a few thousandths of a point),
# which is physically nothing. A 1e-6 threshold put 19 of 20 documents in the
# "mismatched" bucket and left n=1 in the comparison group, making the contrast
# meaningless. 0.01 of the diagonal is the smallest difference that could move a
# callout appreciably against a 0.10 match gate.
FRAME_MISMATCH_EPS = 0.01


def _frame_mismatch(report: RunReport, anonymizer) -> Dict:
    """Documents whose gold and dump page frames disagree.

    score_doc converts predictions to points with dump.page_rect but scales the
    match gate by gold.page_rect's diagonal, and nothing validates the two. Gold
    geometry is CV-recovered from the ballooned drawing while predictions come
    from the clean original, so the two files can disagree. Only an ORIGIN
    disagreement actually translates predictions -- to_points ignores width and
    height -- so a large frac here is a lead to confirm, not a proven fault."""
    def worst(d):
        return max(d.frame_origin_frac, d.frame_extent_frac)

    # THREE groups, for the same reason the dpi split has three: a report written
    # before these fields existed measured nothing, and calling that "agrees"
    # reports a clean bill of health for a run with 14 mismatched documents.
    measured = [d for d in report.doc_scores
                if d.frame_origin_frac is not None
                and d.frame_extent_frac is not None]
    unmeasured = [d for d in report.doc_scores if d not in measured]
    bad = sorted((d for d in measured if worst(d) > FRAME_MISMATCH_EPS),
                 key=lambda d: -worst(d))
    ok = [d for d in measured if worst(d) <= FRAME_MISMATCH_EPS]

    def micro_recall(docs):
        """MICRO, pooling rows -- deliberately not the macro mean used elsewhere.
        A document with a single gold row scored recall 1.0 while carrying the
        second-largest extent mismatch, and under a macro mean that one row
        outweighed a 67-row sheet. Pooling rows stops one-row documents from
        dominating the very comparison they cannot inform."""
        g = sum(d.n_gold for d in docs)
        m = sum(d.n_gold - d.counts.get("missed", 0) for d in docs)
        return round(m / g, 4) if g else None

    return {
        "n_docs_affected": len(bad),
        "n_docs_frames_agree": len(ok),
        # Non-zero means re-score before believing this block.
        "n_docs_not_measured": len(unmeasured),
        "max_frac": max((worst(d) for d in bad), default=0.0),
        # The comparison that says whether this matters: recall on the documents
        # whose frames agree vs the ones where they do not.
        "micro_recall_frames_agree": micro_recall(ok),
        "micro_recall_frames_differ": micro_recall(bad),
        # recall alongside each doc, because a frame fault shows up as recall ~0
        # with predictions still present -- the pairing is the evidence.
        "docs": [{"doc": anonymizer(d.doc_id),
                  "origin": d.frame_origin_frac, "extent": d.frame_extent_frac,
                  "recall": round(d.recall, 4)} for d in bad],
    }


def summarize(report: RunReport, anonymizer, top: int = 10) -> Dict:
    """Privacy-safe digest of a run: aggregate metrics only, doc ids hashed.

    A RunReport embeds client values — `DocScore.pairs[].field_errors` spells out
    gold vs predicted (e.g. "nominal: '6,5'!='5,5'"). This function is the only
    sanctioned way to look at a run: it reads none of that, so the result can be
    shown to an AI agent, committed, or pasted into a ticket."""
    causes, misplaced = _note_counts(report)
    field_failures, field_signatures = _field_failure_counts(report)
    failure_modes, modes_not_measured = _failure_mode_counts(report)
    conf_taxonomy, conf_not_measured = _confidence_by_taxonomy(report)
    ctype_confusion, ctype_not_measured = _char_type_confusion(report)
    clamped_docs, clamp_split = _clamp_split(report, anonymizer)
    pred_kinds, false_kinds, matched_kinds = _kind_totals(report)
    worst = sorted(report.doc_scores, key=lambda d: (-d.review_cost, d.doc_id))
    return {
        "run": report.run_name,
        "split": report.split_used,
        "splits_hash": report.splits_hash,
        "n_docs": len(report.doc_scores),
        "n_gold": sum(d.n_gold for d in report.doc_scores),
        "n_pred": sum(d.n_pred for d in report.doc_scores),
        "mean_review_cost": report.mean_review_cost,
        "micro_recall": report.micro_recall,
        "micro_precision": report.micro_precision,
        "escaped_rate": report.escaped_rate,
        "taxonomy": dict(report.taxonomy),
        # Handoff §6 routing: misparse -> parser hardening (Rung 1),
        # misread -> prompts (Rung 2) then LoRA (Rung 3).
        "error_causes": causes,
        # The same rows, split by whether the pair is even on the right callout.
        # misplaced+on_target == total == escaped+flagged for every cause, and
        # the totals reproduce error_causes above.
        "error_cause_crosstab": _cause_crosstab(report),
        # Matched, but further from its gold balloon than misplaced_frac — a
        # geometry-quality signal that is not an error in its own right.
        "misplaced_matches": misplaced,
        # Which FIELD the matched-but-wrong rows got wrong. field_acc is a
        # four-way conjunction, so without this a read-prompt arm cannot be
        # aimed. Signatures sum to escaped_error + flagged_error.
        "field_failures": field_failures,
        "field_failure_signatures": field_signatures,
        # HOW each wrong field failed: omitted, disagreeing, or invented. Sums
        # to the same total as field_failures. not_measured > 0 means the report
        # predates the tags — re-score, do not read the histogram as complete.
        "field_failure_modes": failure_modes,
        "field_failure_modes_not_measured": modes_not_measured,
        # WHICH two types each char_type-wrong row confused. The largest single
        # failure mode here, and the only aggregate that can tell a synonym-map
        # gap ('unmapped') from a real perception failure (Diameter->Distance).
        # Sums to field_failures['field:char_type'] by construction.
        "char_type_confusion": ctype_confusion,
        "char_type_confusion_not_measured": ctype_not_measured,
        # The price list for a review-flag threshold move, and the size of the
        # threshold-churn confound in any prompt arm. Covers every matched pair.
        "confidence_by_taxonomy": conf_taxonomy,
        "confidence_not_measured": conf_not_measured,
        # Is the dropped-tolerance bucket winnable? rows vs distinct per
        # document: few distinct means a general tolerance no reader can see,
        # many means a box clipping a printed one off.
        "dropped_tolerances": _dropped_tolerances(report, anonymizer),
        "clamped_docs": clamped_docs,
        "clamped_vs_unclamped": clamp_split,
        # Gold is filtered to match_params.score_kinds; predictions are not. A
        # non-dimension kind here is a detection the metric cannot credit.
        "pred_kinds": pred_kinds,
        "false_detections_by_kind": false_kinds,
        # Kinds that DID match in-scope gold. A non-"dimension" entry here is a
        # match that filtering predictions to score_kinds would destroy, turning
        # a w=2 false detection into a w=10 miss — so this is the number that
        # says whether that filter helps or hurts.
        "matched_by_pred_kind": matched_kinds,
        # Which Rung 1 knob the misses actually point at. contended means a
        # detection was inside the match gate but the matcher spent it on a
        # neighbour (merge_adjacent/dedupe collapsed siblings); isolated means
        # nothing was detected there (tile size, overlap, confidence);
        # unlocated means the gold row has no balloon position, so no detection
        # change can reach it. They sum to taxonomy.missed.
        "frame_mismatch": _frame_mismatch(report, anonymizer),
        "missed_diagnosis": {
            "contended": sum(d.missed_contended for d in report.doc_scores),
            "isolated": sum(d.missed_isolated for d in report.doc_scores),
            "unlocated": sum(d.missed_unlocated for d in report.doc_scores),
        },
        "config": report.config.model_dump(),
        "weights": report.weights.model_dump(),
        "match_params": report.match_params.model_dump(),
        "worst_docs": [{"doc": anonymizer(d.doc_id),
                        "review_cost": d.review_cost} for d in worst[:top]],
    }


def recompute_cost(counts: Dict[str, int], weights: ReviewCostWeights) -> float:
    """Review cost for any weight vector, straight from the taxonomy counts.

    Scoring already recorded what happened; only the price tag changes. This is
    what lets the client's real weights arrive late without re-running anything
    — and lets a verdict be checked against every plausible weighting."""
    flagged = counts.get("flagged_correct", 0) + counts.get("flagged_error", 0)
    return (weights.miss * counts.get("missed", 0)
            + weights.escaped * counts.get("escaped_error", 0)
            + weights.false * counts.get("false_detection", 0)
            + weights.flag * flagged)


# Plausible reviewer economics, spanning the range the client's real numbers
# could land in: a miss always costs most, a flag always least, but how much
# more varies. If a verdict holds across all of these, the exact numbers do not
# change the decision.
WEIGHT_GRID = (
    ReviewCostWeights(miss=10, escaped=5, false=2, flag=1),    # documented default
    ReviewCostWeights(miss=20, escaped=8, false=2, flag=1),    # misses dominate
    ReviewCostWeights(miss=5, escaped=4, false=2, flag=1),     # flatter
    ReviewCostWeights(miss=10, escaped=5, false=4, flag=2),    # phantoms costly
    ReviewCostWeights(miss=8, escaped=8, false=1, flag=1),     # silent errors as bad
    ReviewCostWeights(miss=3, escaped=2, false=1, flag=1),     # nearly flat
)


def _weight_sensitivity(a: RunReport, b: RunReport) -> Dict:
    """How often B beats A across the plausible weightings."""
    wins, deltas = 0, []
    for weights in WEIGHT_GRID:
        cost_a = sum(recompute_cost(d.counts, weights) for d in a.doc_scores)
        cost_b = sum(recompute_cost(d.counts, weights) for d in b.doc_scores)
        n = max(1, len(a.doc_scores))
        deltas.append(round((cost_b - cost_a) / n, 4))
        if cost_b < cost_a:
            wins += 1
    fraction = wins / len(WEIGHT_GRID)
    return {
        "n_weight_vectors": len(WEIGHT_GRID),
        "b_better_fraction": fraction,
        "mean_delta_per_weighting": deltas,
        # A verdict that flips with the weights needs the client's real numbers;
        # one that holds everywhere does not.
        "robust": fraction in (0.0, 1.0),
    }


def _check_comparable(a: RunReport, b: RunReport, anonymizer=None) -> None:
    """Raises on any incomparability. Messages must not name a real document:
    this is an exception path, so it reaches a terminal and a log without passing
    through the digest that normally does the hashing. Without an anonymizer it
    identifies the document by position instead of by id -- never by part
    number."""
    def where(doc_id, i):
        return anonymizer(doc_id) if anonymizer else f"document #{i} (of {len(a.doc_scores)})"

    ids_a = [d.doc_id for d in a.doc_scores]
    ids_b = [d.doc_id for d in b.doc_scores]
    if ids_a != ids_b:
        raise ValueError(f"doc set differs: {len(ids_a)} vs {len(ids_b)} docs "
                         f"(runs are only comparable on the identical corpus)")
    for i, (da, db) in enumerate(zip(a.doc_scores, b.doc_scores), 1):
        if da.gold_hash != db.gold_hash:
            raise ValueError(f"gold differs for {where(da.doc_id, i)}: scored "
                             f"against different gold data — re-score both runs")
    if a.weights != b.weights:
        raise ValueError("weights differ between runs — re-score with one set")
    if a.match_params != b.match_params:
        raise ValueError("match params differ between runs — re-score with one set")
    if a.splits_hash and b.splits_hash and a.splits_hash != b.splits_hash:
        raise ValueError("splits differ between runs")


def compare_runs(a: RunReport, b: RunReport, seed: int = 13,
                 n_boot: int = N_BOOTSTRAP, anonymizer=None) -> Dict:
    """Paired comparison: delta = b - a per document (negative = b better).
    Returns headline deltas, a bootstrap CI on the mean delta, and regression
    warnings. Deterministic for fixed seed.

    The bootstrap CI is uninformative for very small doc sets (n < ~10): with
    n=1 any nonzero delta is reported significant."""
    _check_comparable(a, b, anonymizer)
    deltas = [db.review_cost - da.review_cost
              for da, db in zip(a.doc_scores, b.doc_scores)]
    n = len(deltas)
    mean_delta = sum(deltas) / n if n else 0.0

    rng = random.Random(seed)
    boot_means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(n_boot)) if n else [0.0]
    ci95 = (boot_means[int(0.025 * len(boot_means))],
            boot_means[int(0.975 * len(boot_means)) - 1])
    significant = bool(deltas) and (ci95[1] < 0.0 or ci95[0] > 0.0)

    warnings = []
    improved = mean_delta < 0
    if improved and b.micro_recall < a.micro_recall - 0.005:
        warnings.append(
            f"review-cost improved but recall dropped "
            f"{a.micro_recall:.3f} -> {b.micro_recall:.3f} — likely a net "
            f"review-time LOSS on missed callouts (handoff §6 regression guard)")
    if improved and b.escaped_rate > a.escaped_rate + 0.005:
        warnings.append(
            f"review-cost improved but escaped-error rate rose "
            f"{a.escaped_rate:.3f} -> {b.escaped_rate:.3f} — silent errors up")
    # RunConfig is deliberately NOT part of _check_comparable: refusing here
    # would block the Rung-3 experiment, which IS a cross-base comparison (an
    # AWQ baseline against the same weights loaded as 4-bit NF4). But it must not
    # be silent either — a base change is larger than any knob measured on this
    # corpus, and attributing it to an arm's treatment is the exact mistake this
    # warning exists to prevent. It fires regardless of direction: a base swap
    # invalidates the reading whether the cost went up or down.
    if a.config.model_id != b.config.model_id:
        warnings.append(
            f"base model differs: {a.config.model_id!r} -> {b.config.model_id!r}. "
            f"This delta includes the base-model change, not only the treatment. "
            f"State both models wherever this result is quoted.")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_a": a.run_name, "run_b": b.run_name, "n_docs": n,
        "mean_delta": round(mean_delta, 4),
        "ci95": [round(ci95[0], 4), round(ci95[1], 4)],
        "significant": significant,
        "per_doc_deltas": {da.doc_id: round(d, 4)
                           for da, d in zip(a.doc_scores, deltas)},
        "headline": {
            a.run_name: {"mean_review_cost": a.mean_review_cost,
                         "recall": a.micro_recall,
                         "escaped_rate": a.escaped_rate},
            b.run_name: {"mean_review_cost": b.mean_review_cost,
                         "recall": b.micro_recall,
                         "escaped_rate": b.escaped_rate},
        },
        "weight_sensitivity": _weight_sensitivity(a, b),
        "warnings": warnings,
    }
