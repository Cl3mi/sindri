"""The operator's gdt review: a worksheet a human fills in, a tally an agent may read.

`gdt` has 8 rows wrong in char_type ONLY on the shipped config -- the one bucket
where a single-field fix passes the last-fault test (2026-09-22,
docs/plans/2026-09-22-theoretical-parser-result.md §5). The GPU-free counts
settle the prediction side: every one was predicted Flatness, and every one is
`parser._gdt_type`'s DEFAULT, so the type is a guess. What no count can settle
is what the drawing shows, whether the symbol is in the transcription in some
form the parser does not map, and whether gold's label names it. That needs
the drawing and gold's label, which are client content.

So this module is split along the data boundary, and the split is the design:

  * `write_worksheet` emits CLIENT TEXT -- the label, the transcription, the
    part number -- and refuses any path outside a protected root, where the
    agent's guard will not read it. It never overwrites, because re-running
    `score` must not erase someone's answers.
  * `tally` reads back only the three closed-vocabulary answers per row and the
    generator's own closed-vocabulary metadata, and returns counts. Free text
    the operator writes (`note:`) is never read at all, so nothing typed into
    the worksheet can travel to an agent through the tally.

Rows are selected with `report.is_char_type_only`, the digest's own predicate,
so the operator reviews exactly the rows the counts describe.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.eval.reparse import gdt_symbol_recognised
from app.eval.report import _ctype_key, is_char_type_only

# ISO 1101 characteristics, with the symbol an operator looks for on the frame.
# Wider than parser._GDT_SYMBOLS on purpose: a frame showing Straightness or a
# profile is exactly the case where the parser has no entry at all, and the
# review must be able to say so.
DRAWING_SHOWS = (
    ("Straightness", "⏤"), ("Flatness", "⏥"), ("Circularity", "○"),
    ("Cylindricity", "⌭"), ("Profile of a line", "⌒"),
    ("Profile of a surface", "⌓"), ("Parallelism", "∥"),
    ("Perpendicularity", "⊥"), ("Angularity", "∠"), ("Position", "⌖"),
    ("Concentricity", "◎"), ("Symmetry", "⌯"), ("Runout", "↗"),
    ("Total runout", "⌰"), ("other", ""), ("unsure", ""))
SYMBOL_IN_TRANSCRIPTION = ("glyph", "word", "none")
GOLD_LABEL_MEANS_IT = ("yes", "no", "unsure")

_QUESTIONS = ("drawing_shows", "symbol_in_transcription", "gold_label_means_it")
_HEADER = "<!-- sindri-gdt-review v1 rows={n} -->"


class WorksheetRefused(RuntimeError):
    """The worksheet would land somewhere an agent could read it, or on top of
    one that may already hold answers."""


@dataclass
class ReviewRow:
    doc_id: str
    drawing: str
    balloon: int
    where: str
    gold_label: str             # client text -- worksheet only
    transcription: str          # client text -- worksheet only
    predicted: str              # parser constant
    parser_defaulted: bool
    scorer_reads_gold_as: str   # closed vocabulary, from score._ctype_label
    silent: bool                # escaped_error today


def _where(pos, rect) -> str:
    """A rough location, so the balloon is found without scanning the sheet."""
    if pos is None:
        return "not located on the sheet"
    x0, y0, x1, y1 = rect
    fx = (pos[0] - x0) / ((x1 - x0) or 1)
    fy = (pos[1] - y0) / ((y1 - y0) or 1)
    col = "left" if fx < 1 / 3 else "centre" if fx < 2 / 3 else "right"
    row = "upper" if fy < 1 / 3 else "middle" if fy < 2 / 3 else "lower"
    return f"{row} {col}"


def _scorer_side(pair) -> str:
    """How the scorer reads gold's label: a characteristic name, `none` (no
    type word it knows), `ambiguous`, or `empty`. Closed vocabulary only --
    taken from the `ctype:` note, never from the label itself."""
    key = _ctype_key(pair)
    if key is None:
        return "not_measured"
    gold = key[len("chartype:"):].partition("->")[0]
    m = re.fullmatch(r"unmapped\((.*)\)", gold)
    return m.group(1) if m else gold


def collect_gdt_rows(dumps: Dict, golds: Dict, scores: List) -> List[ReviewRow]:
    """The gdt rows wrong in char_type ONLY, grouped by drawing so each drawing
    is opened once, balloons in order within it."""
    rows = []
    for score in scores:
        dump, gold = dumps[score.doc_id], golds[score.doc_id]
        preds = {c.pos: c for c in dump.result.characteristics}
        gold_by_num = {g.balloon: g for g in gold.characteristics}
        for pair in score.pairs:
            p = preds.get(pair.pred_pos)
            g = gold_by_num.get(pair.gold_balloon)
            if p is None or g is None or p.kind != "gdt":
                continue
            if not is_char_type_only(pair):
                continue
            rows.append(ReviewRow(
                doc_id=score.doc_id, drawing=gold.pdf, balloon=g.balloon,
                where=_where(g.position_pt, gold.page_rect),
                gold_label=g.char_type, transcription=p.raw_text or "",
                predicted=p.char_type or "",
                parser_defaulted=not gdt_symbol_recognised(p.raw_text),
                scorer_reads_gold_as=_scorer_side(pair),
                silent=pair.taxonomy == "escaped_error"))
    rows.sort(key=lambda r: (r.doc_id, r.balloon))
    return rows


def _cell(text: str) -> str:
    """Client text inside a table cell: keep it verbatim, but never let it
    break the table or close the code span it sits in."""
    return "`" + " ".join(str(text).split()).replace("`", "'").replace(
        "|", "\\|") + "`"


def build_worksheet(rows: List[ReviewRow]) -> str:
    n = len(rows)
    guessed = sum(r.parser_defaulted for r in rows)
    silent = sum(r.silent for r in rows)
    key = "\n".join(f"  - `{name}`" + (f"  {sym}" if sym else "")
                    for name, sym in DRAWING_SHOWS)
    out = [
        f"# gdt review — {n} rows, about {max(n, 1)} minutes",
        "",
        _HEADER.format(n=n),
        "",
        "> **This file contains client data.** Keep it inside the client-data "
        "folder. Do not paste it, attach it, or show it to an AI agent. Only "
        "the tally at the bottom may be shared.",
        "",
        "## Why these rows",
        "",
        f"Each row below is a GD&T callout the pipeline got right in every "
        f"field except its TYPE. Fix the type and the row is fully correct, "
        f"which no other bucket offers. On {guessed} of {n} the type is a "
        f"GUESS: the parser found no GD&T symbol in what the reader "
        f"transcribed and fell back to its default, Flatness. {silent} of {n} "
        f"reach the customer today as silent errors. Your answers say where "
        f"the fix lives: the parser, the reader, or the scorer's vocabulary.",
        "",
        "## How to do it (about a minute a row)",
        "",
        "1. Open the **stamped** drawing named in the row. The balloons are "
        "printed on it. Find the balloon number; the rough location helps.",
        "2. Look at the feature-control frame (the boxed GD&T callout) at that "
        "balloon.",
        "3. Fill in the three answer lines under the row. Leave a line blank if "
        "you are not sure: a blank row is reported as unanswered, never "
        "guessed.",
        "4. Write only on the answer lines. `note:` is for you; the tally "
        "never reads it.",
        "",
        "## Answer key",
        "",
        "**drawing_shows** — which characteristic the frame's symbol is:",
        "",
        key,
        "",
        "**symbol_in_transcription** — compare the frame with *What the reader "
        "transcribed*:",
        "",
        "  - `glyph` — a symbol is there, even a look-alike. The parser just "
        "did not recognise it.",
        "  - `word` — the characteristic is written out as a word.",
        "  - `none` — the symbol is missing from the transcription.",
        "",
        "**gold_label_means_it** — does the inspection-sheet label name the "
        "same characteristic as `drawing_shows`? `yes`, `no` or `unsure`.",
        "",
        "Capitals and extra spaces do not matter.",
    ]
    for i, r in enumerate(rows, 1):
        rid = f"g{i}"
        cost = ("**silent error** — reaches the customer unflagged"
                if r.silent else "flagged for review")
        guess = ("a guess: no GD&T symbol recognised, so the parser fell back "
                 "to its default" if r.parser_defaulted else
                 "read from a symbol the parser recognised")
        scorer = ("no type word it knows" if r.scorer_reads_gold_as == "none"
                  else r.scorer_reads_gold_as)
        out += [
            "",
            "---",
            "",
            f"## {rid} · {r.drawing} · balloon {r.balloon}",
            "",
            f"<!-- row {rid} scorer={r.scorer_reads_gold_as} -->",
            "",
            "| | |",
            "|---|---|",
            f"| Part | {_cell(r.doc_id)} — stamped drawing {_cell(r.drawing)}, "
            f"sheet 1 |",
            f"| Balloon | **{r.balloon}**, {r.where} of the sheet |",
            f"| Inspection-sheet label | {_cell(r.gold_label)} |",
            f"| What the reader transcribed | {_cell(r.transcription)} |",
            f"| Predicted type | {r.predicted} — {guess} |",
            f"| Scorer reads the label as | {scorer} |",
            f"| Cost today | {cost} |",
            "",
            "drawing_shows:",
            "symbol_in_transcription:",
            "gold_label_means_it:",
            "note:",
        ]
    out += [
        "",
        "---",
        "",
        "## When you are done",
        "",
        "Run this in your own terminal (not through an agent), then tell the "
        "agent \"done\". It writes counts only, into the repo:",
        "",
        "```bash",
        "python3 -m app.eval.runner review-tally <this file> "
        "--out docs/eval/gdt-review-tally.json",
        "```",
        "",
    ]
    return "\n".join(out)


def _protected_roots() -> List[Path]:
    f = Path(os.environ.get("SINDRI_PROTECTED_PATHS",
                            Path.home() / ".claude" / "sindri-protected-paths"))
    if not f.is_file():
        return []
    return [Path(line.strip()).resolve()
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def check_worksheet_path(path) -> Path:
    """Refuse unless `path` resolves INSIDE a protected root and is not already
    there. Called before scoring too, so a bad path fails in seconds rather
    than after a full re-score."""
    target = Path(path).resolve()
    roots = _protected_roots()
    if not any(target.is_relative_to(root) for root in roots):
        raise WorksheetRefused(
            "the gdt worksheet holds client text, so it may only be written "
            "inside a protected root (one listed in "
            "~/.claude/sindri-protected-paths, e.g. the client-data reports/ "
            "folder)" + ("" if roots else " -- and no protected roots are "
                         "configured on this machine"))
    if target.exists():
        raise WorksheetRefused(
            "a worksheet already exists at that path and may hold answers; "
            "move or delete it deliberately to regenerate")
    return target


def write_worksheet(path, rows: List[ReviewRow]) -> int:
    target = check_worksheet_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_worksheet(rows), encoding="utf-8")
    return len(rows)


def _norm(v: str) -> str:
    return " ".join(v.split()).casefold()


_CANON = {
    "drawing_shows": {_norm(n): n for n, _ in DRAWING_SHOWS},
    "symbol_in_transcription": {v: v for v in SYMBOL_IN_TRANSCRIPTION},
    "gold_label_means_it": {v: v for v in GOLD_LABEL_MEANS_IT},
}
_ROW_RE = re.compile(r"^## (g\d+) ", re.M)
_META_RE = re.compile(r"<!-- row (g\d+) scorer=([^ ]*) -->")


def _bump(d: Dict[str, int], k: str) -> None:
    d[k] = d.get(k, 0) + 1


def tally(text: str) -> Dict:
    """Counts only. Reads each row's three answer lines and the generator's
    closed-vocabulary `scorer=` tag -- nothing else in the file, so no label,
    transcription, part number or note can reach the output."""
    m = re.search(r"<!-- sindri-gdt-review v1 rows=(\d+) -->", text)
    starts = [(mm.group(1), mm.start()) for mm in _ROW_RE.finditer(text)]
    out = {"n_rows": int(m.group(1)) if m else len(starts), "answered": 0,
           "incomplete": [], "invalid": [], "drawing_shows": {},
           "prediction_fix": {}, "gold_side": {}, "freed_without_gpu": 0,
           "rows": {}}
    for i, (rid, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        block = text[start:end].split("\n## ", 1)[0]
        meta = _META_RE.search(block)
        scorer = meta.group(2) if meta and meta.group(1) == rid else "not_measured"
        answers: Dict[str, Optional[str]] = {}
        bad = False
        for q in _QUESTIONS:
            mm = re.search(rf"^{q}:[ \t]*(.*)$", block, re.M)
            raw = _norm(mm.group(1)) if mm else ""
            if not raw:
                answers[q] = None
            elif raw in _CANON[q]:
                answers[q] = _CANON[q][raw]
            else:
                bad = True
        if bad:
            out["invalid"].append(rid)
            continue
        if any(a is None for a in answers.values()):
            out["incomplete"].append(rid)
            continue
        out["answered"] += 1
        shows = answers["drawing_shows"]
        pred_fix = ("read_stage" if answers["symbol_in_transcription"] == "none"
                    else "parser")
        means = answers["gold_label_means_it"]
        if means == "no":
            gold_side = "gold_disagrees_with_drawing"
        elif means == "unsure":
            gold_side = "unsure"
        elif _norm(scorer) == _norm(shows):
            gold_side = "scorer_already_matches"
        elif scorer == "none":
            gold_side = "needs_synonym_word"
        else:
            gold_side = "scorer_reads_it_differently"
        freed = (pred_fix == "parser"
                 and gold_side in ("scorer_already_matches",
                                   "needs_synonym_word"))
        _bump(out["drawing_shows"], shows)
        _bump(out["prediction_fix"], pred_fix)
        _bump(out["gold_side"], gold_side)
        out["freed_without_gpu"] += int(freed)
        out["rows"][rid] = {"drawing_shows": shows, "prediction_fix": pred_fix,
                            "gold_side": gold_side, "freed_without_gpu": freed}
    return out
