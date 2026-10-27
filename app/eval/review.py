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
from app.eval.report import is_char_type_only, scorer_side

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
                scorer_reads_gold_as=scorer_side(pair),
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


class ProtectedRootsError(ReviewRefused):
    """The roots file exists but cannot be trusted (unreadable, undecodable,
    or a line that is not an absolute path). A ReviewRefused, so every caller
    that already refuses on one refuses on this too: a broken roots file
    must fail CLOSED, never read as "no roots" or as a root nobody meant."""


_ROOTS_BROKEN = ("the protected-roots file cannot be used (unreadable, or a "
                 "line that is not an absolute path); fix it before writing "
                 "any output")


def _protected_roots() -> List[Path]:
    """Parsed exactly as the Bash guard parses the same file: `#` starts a
    comment, `~` expands. Before this, every non-blank line counted, so the
    file's own comment lines resolved against the cwd into roots like
    `<cwd>/# ...` -- and a path under them passed the inside-a-root checks.
    The guard would accept a relative line by anchoring it at ITS cwd, which
    is not ours; here it is a config error, raised with a fixed sentence
    (no path: the message prints where an agent reads)."""
    override = os.environ.get("SINDRI_PROTECTED_PATHS")
    f = Path(override if override is not None else
             Path.home() / ".claude" / "sindri-protected-paths")
    try:
        if not f.is_file():
            # Only the DEFAULT file may be absent (the GPU container has no
            # ~/.claude). An explicit override that names no regular file is
            # a typo or a broken setup: reading it as "no roots" would skip
            # the root rule on a machine that has client data, so fail
            # closed. An empty file is how to say "no roots" on purpose.
            if override is not None:
                raise ProtectedRootsError(_ROOTS_BROKEN)
            return []
        text = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        raise ProtectedRootsError(_ROOTS_BROKEN) from None
    roots = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        expanded = os.path.expanduser(line)
        if not os.path.isabs(expanded):
            raise ProtectedRootsError(_ROOTS_BROKEN)
        try:
            roots.append(Path(expanded).resolve())
        except (OSError, RuntimeError, ValueError):
            raise ProtectedRootsError(_ROOTS_BROKEN) from None
    return roots


def inside_protected(path) -> bool:
    target = Path(path).resolve()
    return any(target.is_relative_to(root) for root in _protected_roots())


def check_deck_path(path, avoid=()) -> Path:
    """The class-B file rule (outputs.check_client_file_out): directly under
    <root>/reports/, `.json`, never a link (dangling ones included), never
    one of the run's inputs, and never replacing anything -- answers are
    keyed to the deck. Before review round 2 it was "anywhere inside a
    root", which let a deck land in gold/ under a gold file's name. Unlike
    class B it also refuses when NO roots are configured: a deck is only
    ever safe inside one. Called before scoring too, so a bad path fails in
    seconds rather than after a re-score."""
    from app.eval.outputs import OutputRefused, check_client_file_out
    if not _protected_roots():
        raise ReviewRefused(
            "the review deck holds client text, so it may only be written "
            "inside a protected root -- and no protected roots are "
            "configured on this machine")
    try:
        return check_client_file_out(path, subdir="reports", suffix=".json",
                                     kind=None, avoid=avoid)
    except OutputRefused as e:
        if "already exists" in str(e):
            raise ReviewRefused(
                "a deck already exists at that path and its answers are "
                "keyed to it; move it away deliberately to regenerate") from None
        raise ReviewRefused(f"the review deck: {e}") from None


_MARKER_PEEK = 4096


def _carries_marker(target: Path, kind: str) -> bool:
    """A regular file whose FIRST key is `"kind": <kind>`. Only the opening
    bytes are read: a report can be large, and the writer puts the marker
    first precisely so this never has to parse one."""
    if not target.is_file():
        return False
    try:
        with open(target, "rb") as f:
            head = f.read(_MARKER_PEEK).decode("utf-8", errors="replace")
    except OSError:
        return False
    return re.match(r'\A\s*\{\s*"kind"\s*:\s*' + re.escape(json.dumps(kind))
                    + r"\s*[,}]", head) is not None


def _check_grade_output(path, suffix: str, kind: str, avoid, what: str) -> Path:
    """Shared by report and digest. An agent picks both paths, so each one
    must be unable to replace anything else: not through a link (checked
    BEFORE resolving, or the link is followed to gold or a dump), not under
    another file's name, not over a file that is not a previous output of
    this same kind, and not over the inputs being graded. Fixed messages
    only: they are printed where an agent reads, and a path can be client
    text.

    `avoid` is compared the way outputs.py compares it (`_same`): by any
    spelling, as a hardlink, and -- for an input DIRECTORY such as --gold --
    as anything inside it. ValueError (a NUL byte) joins OSError and a
    symlink loop's RuntimeError as a fixed refusal, never a traceback."""
    from app.eval.outputs import _same
    try:
        if Path(path).is_symlink():
            raise ReviewRefused(f"the {what} path is a symbolic link")
        target = Path(path).resolve()
        if not target.name.endswith(suffix) or target.name == suffix:
            raise ReviewRefused(f"the {what} file name must end in {suffix}")
        if any(a and _same(target, a) for a in avoid):
            raise ReviewRefused(f"the {what} path is one of the run's inputs")
        if target.exists() and not _carries_marker(target, kind):
            raise ReviewRefused(
                f"a file that is not a previous {what} already exists there; "
                "move it away deliberately")
    except (OSError, RuntimeError, ValueError):
        raise ReviewRefused(f"the {what} path cannot be checked") from None
    return target


def check_report_path(path, avoid=()) -> Path:
    """The campaign grading report (C2) carries the client's per-row values
    and notes, so it goes inside a protected root like a deck. Unlike a deck
    it MAY replace a previous report: nothing is keyed to it, and it is a
    pure function of the pull tar, so re-grading regenerates it. `avoid`
    names the run's inputs (the pull tar, the manifest)."""
    from app.eval.campaign_grade import REPORT_KIND
    target = _check_grade_output(path, ".grade.json", REPORT_KIND, avoid,
                                 "grading report")
    if not inside_protected(target):
        raise ReviewRefused(
            "the grading report holds client values, so it may only be "
            "written inside a protected root (one listed in "
            "~/.claude/sindri-protected-paths)" + ("" if _protected_roots()
            else " -- and no protected roots are configured on this machine"))
    # The class-B subfolder rule (review round 2): a direct child of
    # <root>/reports/, so it can never land in gold/ or a run dir.
    if not any(target.parent == r / "reports" for r in _protected_roots()):
        raise ReviewRefused(
            "the grading report holds client values; it may only be written "
            "directly under <protected root>/reports/")
    return target


def check_digest_path(path, avoid=()) -> Path:
    """The counts-only grading digest is the agent's output (docs/eval/), so
    it goes OUTSIDE every protected root -- inside one it could never be
    read, and an agent-picked path there could replace client data. Named
    `-grade.json`, distinct from the report's `.grade.json`."""
    from app.eval.campaign_grade import DIGEST_KIND
    from app.eval.outputs import _FORBIDDEN_COMPONENTS
    target = _check_grade_output(path, "-grade.json", DIGEST_KIND, avoid,
                                 "grading digest")
    # Class A's rule (review round 3), on the RESOLVED path, so a link
    # named anything else that leads into .git still counts.
    if _FORBIDDEN_COMPONENTS.intersection(target.parts):
        raise ReviewRefused("the grading digest may not be written inside a "
                            ".git or .claude directory")
    if inside_protected(target):
        raise ReviewRefused(
            "the grading digest is the output meant for the agent; it may "
            "not be written inside a protected root -- write it under "
            "docs/eval/")
    return target


def _write_through_the_one_writer(target: Path, text: str, mode: int) -> None:
    """outputs.write_atomic (partial, fsync, rename; the approved parent
    re-checked), with its refusal turned into ours."""
    from app.eval.outputs import OutputRefused, prepare_parent, write_atomic
    try:
        prepare_parent(target)
        write_atomic(target, text, mode=mode, expect_parent=target.parent)
    except OutputRefused as e:
        raise ReviewRefused(str(e)) from None


def write_deck(path, deck: Dict, avoid=()) -> int:
    from app.eval.outputs import CLIENT_FILE_MODE
    target = check_deck_path(path, avoid=avoid)
    _write_through_the_one_writer(
        target, json.dumps(deck, indent=1, ensure_ascii=False), CLIENT_FILE_MODE)
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


def check_tally_out(path, avoid=()) -> Path:
    """Class-A semantics (outputs.check_digest_out, review round 3): `.json`,
    never a link (checked before resolving, or the atomic rename would
    replace the link's target), never under .git/.claude, outside every
    root, never an input (the deck), and replacing only a previous tally --
    whose marker is its existing first key, the deck kind, unchanged."""
    from app.eval.outputs import OutputRefused, check_digest_out
    try:
        if inside_protected(path):
            raise ReviewRefused(
                "the tally is the output meant for the agent; inside a "
                "protected root it could never be read -- write it under "
                "docs/eval/")
    except (OSError, RuntimeError, ValueError):
        raise ReviewRefused("the tally path cannot be resolved") from None
    try:
        return check_digest_out(path, KIND, avoid=avoid)
    except OutputRefused as e:
        raise ReviewRefused(f"the tally: {e}") from None


def write_tally(path, t: Dict, avoid=()) -> Path:
    from app.eval.outputs import DIGEST_FILE_MODE
    target = check_tally_out(path, avoid=avoid)
    _write_through_the_one_writer(
        target, json.dumps(t, indent=1, ensure_ascii=False) + "\n",
        DIGEST_FILE_MODE)
    return target
