"""The operator's gdt review: a deck a human reviews, a tally an agent may read.

`gdt` has 8 rows wrong in char_type ONLY on the shipped config -- the one bucket
where a single-field fix passes the last-fault test (2026-09-22,
docs/plans/2026-09-22-theoretical-parser-result.md §5). The GPU-free counts
settle the prediction side: every one was predicted Flatness, and every one is
`parser._gdt_type`'s DEFAULT, so the type is a guess. What no count can settle
is what the drawing shows, whether the symbol is in the transcription in some
form the parser does not map, and whether gold's label names it. That needs
the drawing and gold's label, which are client content.

So this module is split along the data boundary, and the split is the design
(docs/plans/2026-09-25-gdt-review-ui-design.md):

  * the DECK (`write_deck`) carries CLIENT TEXT -- the label, the
    transcription, the part number -- plus the geometry the review app crops
    by, and the questions. It refuses any path outside a protected root, where
    the agent's guard will not read it, and never overwrites: answers are
    keyed to it.
  * the TALLY reads back only the closed-vocabulary answers and the deck's
    closed-vocabulary metadata, and returns counts. Notes are never read, so
    nothing the operator types can travel to an agent through it.

Rows are selected with `report.is_char_type_only`, the digest's own predicate,
so the operator reviews exactly the rows the counts describe.
"""
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.eval.reparse import gdt_symbol_recognised
from app.eval.report import _ctype_key, is_char_type_only

DECK_VERSION = 1
KIND = "gdt-char-type-only"

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

# The questions travel IN the deck, so the page renders whatever a deck asks
# and a later review is a new deck rather than new UI code. Hotkeys are scoped
# to the active question, which is why "n" can mean None here and No below.
QUESTIONS = (
    {"key": "drawing_shows", "prompt": "What does the frame show?",
     "help": "The characteristic of the GD&T frame at this balloon.",
     "filter": True,
     "options": [{"value": n, "label": n, "symbol": s}
                 for n, s in DRAWING_SHOWS]},
    {"key": "symbol_in_transcription",
     "prompt": "Is that symbol in the transcription?",
     "help": "Compare the frame with what the reader transcribed.",
     "options": [
         {"value": "glyph", "label": "Glyph", "hotkey": "g",
          "help": "a symbol is there, even a look-alike"},
         {"value": "word", "label": "Word", "hotkey": "w",
          "help": "the characteristic is written out"},
         {"value": "none", "label": "None", "hotkey": "n",
          "help": "the symbol is missing"}]},
    {"key": "gold_label_means_it",
     "prompt": "Does the inspection-sheet label mean it?",
     "help": "Does the label name the same characteristic as your first answer?",
     "options": [
         {"value": "yes", "label": "Yes", "hotkey": "y"},
         {"value": "no", "label": "No", "hotkey": "n"},
         {"value": "unsure", "label": "Unsure", "hotkey": "u"}]},
)
_KEYS = tuple(q["key"] for q in QUESTIONS)


class ReviewRefused(RuntimeError):
    """A review file would land where an agent could read it, would replace
    one that may hold answers, or does not belong to this deck."""


@dataclass
class ReviewRow:
    id: str
    doc_id: str
    balloon: int
    where: str
    gold_pt: Optional[Tuple[float, float]]
    pred_box_pt: Optional[Tuple[float, float, float, float]]
    gold_label: str             # client text -- deck only
    transcription: str          # client text -- deck only
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


def _pt_box(region, scale):
    """Render pixels -> PDF points: the crop is cut from the PDF itself."""
    if not region or not scale:
        return None
    return tuple(round(v / scale, 2) for v in region)


def collect_gdt_rows(dumps: Dict, golds: Dict, scores: List) -> List[ReviewRow]:
    """The gdt rows wrong in char_type ONLY, grouped by document so each
    drawing is looked at once, balloons in order within it."""
    found = []
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
            found.append((score.doc_id, g.balloon, dict(
                doc_id=score.doc_id, balloon=g.balloon,
                where=_where(g.position_pt, gold.page_rect),
                gold_pt=tuple(g.position_pt) if g.position_pt else None,
                pred_box_pt=_pt_box(p.target_region, dump.scale),
                gold_label=g.char_type, transcription=p.raw_text or "",
                predicted=p.char_type or "",
                parser_defaulted=not gdt_symbol_recognised(p.raw_text),
                scorer_reads_gold_as=_scorer_side(pair),
                silent=pair.taxonomy == "escaped_error")))
    found.sort(key=lambda t: (t[0], t[1]))
    return [ReviewRow(id=f"g{i}", **kw)
            for i, (_, _, kw) in enumerate(found, 1)]


def build_deck(rows: List[ReviewRow], run: str, originals_dir,
               stamped_dir) -> Dict:
    return {"version": DECK_VERSION, "kind": KIND, "run": run,
            "originals_dir": str(originals_dir),
            "stamped_dir": str(stamped_dir),
            "questions": [dict(q) for q in QUESTIONS],
            "rows": [asdict(r) for r in rows]}


def _protected_roots() -> List[Path]:
    f = Path(os.environ.get("SINDRI_PROTECTED_PATHS",
                            Path.home() / ".claude" / "sindri-protected-paths"))
    if not f.is_file():
        return []
    return [Path(line.strip()).resolve()
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def inside_protected(path) -> bool:
    target = Path(path).resolve()
    return any(target.is_relative_to(root) for root in _protected_roots())


def check_deck_path(path) -> Path:
    """Inside a protected root and not already there. Called before scoring
    too, so a bad path fails in seconds rather than after a re-score."""
    target = Path(path).resolve()
    if not inside_protected(target):
        raise ReviewRefused(
            "the review deck holds client text, so it may only be written "
            "inside a protected root (one listed in "
            "~/.claude/sindri-protected-paths, e.g. the client-data reports/ "
            "folder)" + ("" if _protected_roots() else " -- and no protected "
                         "roots are configured on this machine"))
    if target.exists():
        raise ReviewRefused(
            "a deck already exists at that path and its answers are keyed to "
            "it; move it away deliberately to regenerate")
    return target


def write_deck(path, deck: Dict) -> int:
    target = check_deck_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(deck, indent=1, ensure_ascii=False),
                      encoding="utf-8")
    return len(deck["rows"])


def load_deck(path) -> Dict:
    deck = json.loads(Path(path).read_text(encoding="utf-8"))
    if deck.get("version") != DECK_VERSION or deck.get("kind") != KIND:
        raise ReviewRefused(f"not a {KIND} v{DECK_VERSION} review deck")
    return deck


def deck_sha(deck: Dict) -> str:
    """Keys the answers file to its deck, so answers are never read against a
    regenerated deck whose row ids now mean different callouts."""
    blob = json.dumps(deck, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --- answers ------------------------------------------------------------------

_NOTE_MAX = 2000


def _norm(v: str) -> str:
    return " ".join(v.split()).casefold()


_OPTIONS = {q["key"]: {_norm(o["value"]): o["value"] for o in q["options"]}
            for q in QUESTIONS}


def _canon(key: str, value) -> Optional[str]:
    """'' for blank, the canonical option for a valid answer, None otherwise."""
    v = _norm(str(value or ""))
    if not v:
        return ""
    return _OPTIONS[key].get(v)


def answers_path(deck_path) -> Path:
    p = Path(deck_path)
    stem = p.name[:-5] if p.name.endswith(".json") else p.name
    return p.with_name(stem + ".answers.json")


def load_answers(deck_path, deck: Dict) -> Dict[str, Dict]:
    ap = answers_path(deck_path)
    if not ap.exists():
        return {}
    data = json.loads(ap.read_text(encoding="utf-8"))
    if data.get("deck_sha") != deck_sha(deck):
        raise ReviewRefused(
            "the answers file belongs to a different deck; move one of them "
            "away rather than mixing answers across decks")
    return data.get("answers", {})


def save_answer(deck_path, deck: Dict, rid: str,
                answer: Dict) -> Dict[str, Dict]:
    """Merge one row's answers and write atomically, so a crash mid-write can
    never leave a half file that loses every earlier answer."""
    if not inside_protected(deck_path):
        raise ReviewRefused("answers are kept beside the deck, inside a "
                            "protected root")
    if rid not in {r["id"] for r in deck["rows"]}:
        raise ReviewRefused(f"unknown row {rid!r}")
    unknown = set(answer) - set(_KEYS) - {"note"}
    if unknown:
        raise ReviewRefused(f"unknown answer field(s): {sorted(unknown)}")
    answers = load_answers(deck_path, deck)
    row = dict(answers.get(rid, {}))
    for k in _KEYS:
        if k in answer:
            c = _canon(k, answer[k])
            if c is None:
                raise ReviewRefused(f"{k}: not one of the allowed answers")
            row[k] = c
    if "note" in answer:
        note = str(answer["note"] or "")
        if len(note) > _NOTE_MAX:
            raise ReviewRefused(f"note longer than {_NOTE_MAX} characters")
        row["note"] = note
    answers[rid] = row
    ap = answers_path(deck_path)
    tmp = ap.with_name(ap.name + ".tmp")
    tmp.write_text(json.dumps(
        {"deck_sha": deck_sha(deck), "answers": answers,
         "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ap)
    return answers


# --- the tally ----------------------------------------------------------------

def _bump(d: Dict[str, int], k: str) -> None:
    d[k] = d.get(k, 0) + 1


def tally(deck: Dict, answers: Dict[str, Dict]) -> Dict:
    """Counts only. Reads the three closed-vocabulary answers per row and the
    deck's closed-vocabulary `scorer_reads_gold_as` -- never a label, a
    transcription, a part number or a note.

    A row is freed WITHOUT A GPU only when both halves of its fix are CPU
    changes: the symbol reached the text (a parser mapping), and the scorer
    already reads gold's label as that characteristic or would with one
    synonym word the operator supplies."""
    out = {"kind": deck["kind"], "run": deck["run"],
           "n_rows": len(deck["rows"]), "answered": 0, "incomplete": [],
           "invalid": [], "drawing_shows": {}, "prediction_fix": {},
           "gold_side": {}, "freed_without_gpu": 0, "rows": {}}
    for row in deck["rows"]:
        rid, given = row["id"], answers.get(row["id"], {})
        vals = {k: _canon(k, given.get(k)) for k in _KEYS}
        if any(v is None for v in vals.values()):
            out["invalid"].append(rid)
            continue
        if any(not v for v in vals.values()):
            out["incomplete"].append(rid)
            continue
        out["answered"] += 1
        shows, scorer = vals["drawing_shows"], row["scorer_reads_gold_as"]
        pred_fix = ("read_stage" if vals["symbol_in_transcription"] == "none"
                    else "parser")
        means = vals["gold_label_means_it"]
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
        freed = pred_fix == "parser" and gold_side in (
            "scorer_already_matches", "needs_synonym_word")
        _bump(out["drawing_shows"], shows)
        _bump(out["prediction_fix"], pred_fix)
        _bump(out["gold_side"], gold_side)
        out["freed_without_gpu"] += int(freed)
        out["rows"][rid] = {"drawing_shows": shows, "prediction_fix": pred_fix,
                            "gold_side": gold_side, "freed_without_gpu": freed}
    return out


def check_tally_out(path) -> Path:
    target = Path(path).resolve()
    if inside_protected(target):
        raise ReviewRefused(
            "the tally is the output meant for the agent; inside a protected "
            "root it could never be read -- write it under docs/eval/")
    return target


def write_tally(path, t: Dict) -> Path:
    target = check_tally_out(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(t, indent=1, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return target
