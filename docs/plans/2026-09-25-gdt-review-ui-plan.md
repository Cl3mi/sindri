# gdt review UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Markdown gdt worksheet with a local, localhost-only review app: drawing crops per row, click/keyboard answers, autosave, and a counts-only tally.

**Architecture:** `score --review-deck` writes a JSON deck (rows + questions) inside a protected root. `runner review-serve <deck>`, run by the operator, serves one inline HTML page, row data, on-demand PyMuPDF crops and save/finish endpoints from a stdlib `ThreadingHTTPServer` bound to 127.0.0.1 with a session token. Answers autosave beside the deck; Finish writes the values-blind tally into `docs/eval/`.

**Tech Stack:** Python 3 stdlib (`http.server`, `json`, `secrets`, `hmac`), PyMuPDF (`fitz`, already a dependency), vanilla HTML/CSS/JS in one file, pytest.

**Spec:** `docs/plans/2026-09-25-gdt-review-ui-design.md`.

**Repo rules that bite here** (CLAUDE.md): strict TDD, one task one commit, commit trailer `Co-Authored-By: …`. The agent's Bash guard denies any command string containing the protected root, a bare `.pdf`, or `.report.json` — so tests name outputs `scored.json`, synthetic PDFs are created in code (`tmp_path / "x.pdf"` is fine inside a Python file, never typed in a shell command), and file edits are split from their `git add`.

---

## File structure

| file | responsibility |
|---|---|
| `app/eval/review.py` (rework) | rows, deck build/load, path rules, answers persistence, tally |
| `app/eval/review_crops.py` (new) | crop geometry (pure) and PNG rendering |
| `app/eval/review_server.py` (new) | `ReviewApp` state + HTTP handler + `serve()` |
| `app/eval/review_page.html` (new) | the single page the operator uses |
| `app/eval/runner.py` (modify) | `score --review-deck`, `review-tally <deck>`, `review-serve <deck>`; remove `--gdt-worksheet` |
| `tests/eval/test_review.py` (rework) | deck, answers, tally, path rules, CLI |
| `tests/eval/test_review_crops.py` (new) | geometry and rendering |
| `tests/eval/test_review_server.py` (new) | HTTP behaviour against the real handler |

---

### Task 1: Deck — rows and the deck file

**Files:**
- Modify: `app/eval/review.py` (replace `ReviewRow`, `collect_gdt_rows`; add `QUESTIONS`, `build_deck`, `write_deck`, `load_deck`, `deck_sha`, path helpers; delete `build_worksheet`, `_cell`, `write_worksheet`, `check_worksheet_path`, the Markdown `tally`, `_ROW_RE`, `_META_RE`, `_HEADER`)
- Test: `tests/eval/test_review.py` (rewrite top half)

- [ ] **Step 1: Write the failing tests** — replace `tests/eval/test_review.py` with the fixtures below plus these tests (the fixture block `_box`, `_doc`, `_collect`, `_GDT`, `GOLD_POSITION`, `_roots` is carried over from today's file unchanged).

```python
from app.eval.review import (KIND, QUESTIONS, ReviewRefused, build_deck,
                             collect_gdt_rows, deck_sha, load_deck, write_deck)


def test_it_selects_exactly_the_gdt_rows_wrong_in_char_type_only():
    rows = _collect(_doc("P1", [
        (100, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION),
        (300, 100, dict(_GDT, raw_text="0,05 A", upper_tol="9"), GOLD_POSITION),
        (500, 100, dict(_GDT, raw_text="⏥ 0,05"),
         dict(GOLD_POSITION, char_type="Flatness")),
        (700, 100, dict(char_type="Distance", nominal="20", raw_text="20"),
         dict(char_type="Diameter", nominal="20")),
    ]))
    assert [(r.id, r.doc_id, r.balloon) for r in rows] == [("g1", "P1", 1)]


def test_each_row_carries_geometry_in_page_points():
    """The crop needs the pipeline's box in PDF points: target_region is in
    render pixels, so it is divided by the dump's scale."""
    r = _collect(_doc("P1", [
        (100, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION)]))[0]
    assert r.gold_pt == (100, 100)
    assert r.pred_box_pt == pytest.approx((85, 95, 115, 105))
    assert r.parser_defaulted is True and r.scorer_reads_gold_as == "Position"
    assert r.where == "upper left" and r.gold_label == "Position"


def test_ids_follow_document_then_balloon_order():
    rows = _collect(
        _doc("P2", [(100, 700, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION)]),
        _doc("P1", [(900, 100, dict(_GDT, raw_text="0,05 A"), GOLD_POSITION),
                    (100, 100, dict(_GDT, raw_text="0,05 B"), GOLD_POSITION)]))
    assert [(r.id, r.doc_id, r.balloon) for r in rows] == [
        ("g1", "P1", 1), ("g2", "P1", 2), ("g3", "P2", 1)]


def test_the_deck_carries_its_questions_so_the_page_needs_no_code_per_review():
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    assert deck["kind"] == KIND and deck["version"] == 1
    assert [q["key"] for q in deck["questions"]] == [
        "drawing_shows", "symbol_in_transcription", "gold_label_means_it"]
    assert [r["id"] for r in deck["rows"]] == ["g1", "g2", "g3"]
    assert deck["originals_dir"] == "/o" and deck["stamped_dir"] == "/s"


def test_a_deck_round_trips_and_its_sha_is_stable(tmp_path, monkeypatch):
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    write_deck(root / "d.json", deck)
    back = load_deck(root / "d.json")
    assert deck_sha(back) == deck_sha(json.loads(json.dumps(deck)))


def test_the_deck_is_written_only_inside_a_protected_root(tmp_path, monkeypatch):
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    with pytest.raises(ReviewRefused, match="protected root"):
        write_deck(tmp_path / "outside.json", deck)
    for bad in (root / ".." / "elsewhere.json", tmp_path / "client-copy" / "x.json"):
        with pytest.raises(ReviewRefused):
            write_deck(bad, deck)


def test_no_protected_roots_configured_means_refuse(tmp_path, monkeypatch):
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(tmp_path / "missing"))
    with pytest.raises(ReviewRefused):
        write_deck(tmp_path / "x.json", build_deck([], "r", "/o", "/s"))


def test_a_deck_is_never_overwritten(tmp_path, monkeypatch):
    """Answers are keyed to the deck; replacing it would orphan them."""
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")
    write_deck(root / "d.json", deck)
    with pytest.raises(ReviewRefused, match="exists"):
        write_deck(root / "d.json", deck)


def test_a_file_that_is_not_a_deck_is_refused(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"version": 99, "kind": "other"}))
    with pytest.raises(ReviewRefused, match="not a"):
        load_deck(p)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest -q tests/eval/test_review.py`
Expected: collection error — `ImportError: cannot import name 'KIND'`.

- [ ] **Step 3: Implement** — in `app/eval/review.py` keep `DRAWING_SHOWS`, `SYMBOL_IN_TRANSCRIPTION`, `GOLD_LABEL_MEANS_IT`, `_where`, `_scorer_side`, `_protected_roots`; rename `WorksheetRefused` → `ReviewRefused`; replace the rest of the row/worksheet code with:

```python
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Tuple

DECK_VERSION = 1
KIND = "gdt-char-type-only"

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


def _pt_box(region, scale):
    """Render pixels -> PDF points: the crop is cut from the PDF itself."""
    if not region or not scale:
        return None
    return tuple(round(v / scale, 2) for v in region)


def collect_gdt_rows(dumps, golds, scores):
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
    return [ReviewRow(id=f"g{i}", **kw) for i, (_, _, kw) in
            enumerate(found, 1)]


def build_deck(rows, run, originals_dir, stamped_dir):
    return {"version": DECK_VERSION, "kind": KIND, "run": run,
            "originals_dir": str(originals_dir),
            "stamped_dir": str(stamped_dir),
            "questions": [dict(q) for q in QUESTIONS],
            "rows": [asdict(r) for r in rows]}


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


def write_deck(path, deck) -> int:
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


def deck_sha(deck) -> str:
    blob = json.dumps(deck, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
```

Also add `def _rows3()` to the test file (three rows over P1/P2, as today) — it is used by later tasks.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest -q tests/eval/test_review.py`
Expected: all Task 1 tests pass. (Runner tests referencing `--gdt-worksheet` are removed from this file in Task 5; delete them now.)

- [ ] **Step 5: Commit** — `git add app/eval/review.py tests/eval/test_review.py`, message `feat(eval): a review deck -- rows, questions and geometry in one file`.

---

### Task 2: Answers and the tally on structured input

**Files:**
- Modify: `app/eval/review.py` (add `answers_path`, `load_answers`, `save_answer`, `tally`, `check_tally_out`, `write_tally`)
- Test: `tests/eval/test_review.py`

- [ ] **Step 1: Write the failing tests**

```python
from app.eval.review import (answers_path, check_tally_out, load_answers,
                             save_answer, tally)


def _deck3():
    return build_deck(_rows3(), run="r", originals_dir="/o", stamped_dir="/s")


def _a(drawing, symbol, gold):
    return {"drawing_shows": drawing, "symbol_in_transcription": symbol,
            "gold_label_means_it": gold}


def test_nothing_answered_tallies_as_incomplete():
    t = tally(_deck3(), {})
    assert t["n_rows"] == 3 and t["answered"] == 0
    assert t["incomplete"] == ["g1", "g2", "g3"]


def test_the_tally_routes_each_answered_row_to_where_its_fix_lives():
    t = tally(_deck3(), {"g1": _a("Position", "glyph", "yes"),
                         "g2": _a("Position", "none", "yes"),
                         "g3": _a("Parallelism", "glyph", "no")})
    assert t["answered"] == 3 and t["incomplete"] == [] and t["invalid"] == []
    assert t["drawing_shows"] == {"Position": 2, "Parallelism": 1}
    assert t["prediction_fix"] == {"parser": 2, "read_stage": 1}
    assert t["gold_side"] == {"scorer_already_matches": 2,
                              "gold_disagrees_with_drawing": 1}
    assert t["freed_without_gpu"] == 1


def test_a_label_the_scorer_cannot_read_needs_a_synonym_word():
    rows = _collect(_doc("P1", [
        (100, 100, dict(_GDT, raw_text="0,05 A"),
         dict(GOLD_POSITION, char_type="Lagetoleranz zu A"))]))
    deck = build_deck(rows, "r", "/o", "/s")
    t = tally(deck, {"g1": _a("Position", "word", "yes")})
    assert t["gold_side"] == {"needs_synonym_word": 1}
    assert t["freed_without_gpu"] == 1


def test_a_hand_edited_answer_outside_the_vocabulary_is_invalid():
    t = tally(_deck3(), {"g1": _a("Positon", "glyph", "yes")})
    assert t["invalid"] == ["g1"] and t["answered"] == 0


def test_the_tally_is_values_blind_whatever_the_answers_file_holds():
    rows = _collect(_doc("PARTNO-4711", [
        (100, 100, dict(_GDT, raw_text="0,05 SECRET-TEXT"),
         dict(GOLD_POSITION, char_type="Position SECRET-LABEL"))]))
    deck = build_deck(rows, "r", "/o", "/s")
    answers = {"g1": dict(_a("Position", "glyph", "yes"), note="SECRET-NOTE")}
    blob = json.dumps(tally(deck, answers), ensure_ascii=False)
    for leak in ("SECRET", "PARTNO", "4711", "0,05"):
        assert leak not in blob, leak


def _saved(tmp_path, monkeypatch):
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    deck = _deck3()
    write_deck(root / "d.json", deck)
    return root / "d.json", load_deck(root / "d.json")


def test_answers_persist_merge_and_canonicalise(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", {"drawing_shows": "total  RUNOUT"})
    save_answer(path, deck, "g1", {"symbol_in_transcription": "Glyph",
                                   "note": "mine"})
    got = load_answers(path, deck)
    assert got["g1"] == {"drawing_shows": "Total runout",
                         "symbol_in_transcription": "glyph", "note": "mine"}
    assert answers_path(path).name == "d.answers.json"


def test_a_blank_value_clears_an_answer(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", {"drawing_shows": "Position"})
    save_answer(path, deck, "g1", {"drawing_shows": ""})
    assert load_answers(path, deck)["g1"]["drawing_shows"] == ""


def test_save_refuses_unknown_rows_fields_and_values(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    for rid, ans in (("g9", {"drawing_shows": "Position"}),
                     ("g1", {"colour": "red"}),
                     ("g1", {"drawing_shows": "Positon"}),
                     ("g1", {"note": "x" * 2001})):
        with pytest.raises(ReviewRefused):
            save_answer(path, deck, rid, ans)
    assert load_answers(path, deck) == {}


def test_answers_made_against_another_deck_are_refused(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", {"drawing_shows": "Position"})
    other = dict(deck, run="another")
    with pytest.raises(ReviewRefused, match="different deck"):
        load_answers(path, other)


def test_the_tally_may_not_be_written_into_a_protected_root(tmp_path,
                                                            monkeypatch):
    """It is the one output meant for an agent; inside a root no agent can
    read it, so writing it there would silently lose the review."""
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    with pytest.raises(ReviewRefused):
        check_tally_out(root / "tally.json")
    assert check_tally_out(tmp_path / "docs" / "tally.json")
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest -q tests/eval/test_review.py` → ImportError on `answers_path`.

- [ ] **Step 3: Implement** (append to `app/eval/review.py`):

```python
from datetime import datetime, timezone

_NOTE_MAX = 2000
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


def load_answers(deck_path, deck) -> Dict[str, Dict]:
    ap = answers_path(deck_path)
    if not ap.exists():
        return {}
    data = json.loads(ap.read_text(encoding="utf-8"))
    if data.get("deck_sha") != deck_sha(deck):
        raise ReviewRefused(
            "the answers file belongs to a different deck; move one of them "
            "away rather than mixing answers across decks")
    return data.get("answers", {})


def save_answer(deck_path, deck, rid: str, answer: Dict) -> Dict[str, Dict]:
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


def tally(deck, answers) -> Dict:
    """Counts only. Reads the three closed-vocabulary answers per row and the
    deck's closed-vocabulary `scorer_reads_gold_as` -- never a label, a
    transcription, a part number or a note."""
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
```

(`_norm` and `_bump` already exist; move `_norm` above `_OPTIONS`.)

- [ ] **Step 4: Run to verify pass** — `python3 -m pytest -q tests/eval/test_review.py` → all pass.
- [ ] **Step 5: Commit** — message `feat(eval): review answers autosave beside the deck; the tally reads them`.

---

### Task 3: Crops

**Files:**
- Create: `app/eval/review_crops.py`
- Test: `tests/eval/test_review_crops.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Crops of a drawing around one review row -- geometry first, pixels second."""
import fitz
import pytest

from app.eval.review_crops import (CropError, crop_rect, page_rect,
                                   placeholder_png, render_crop)

PAGE = (0.0, 0.0, 1191.0, 842.0)
PNG = b"\x89PNG"


def test_the_region_covers_box_and_gold_point_with_padding():
    r = crop_rect(PAGE, (100, 100, 200, 130), (300, 110))
    assert r[0] <= 100 - 40 and r[2] >= 300 + 5 + 40
    assert r[1] <= 100 - 40 and r[3] >= 130 + 40


def test_a_tiny_region_grows_to_the_minimum_around_its_centre():
    x0, y0, x1, y1 = crop_rect(PAGE, None, (600, 400))
    assert x1 - x0 == pytest.approx(160) and y1 - y0 == pytest.approx(100)
    assert (x0 + x1) / 2 == pytest.approx(600)


def test_a_region_at_the_page_edge_is_shifted_inside_not_cut():
    x0, y0, x1, y1 = crop_rect(PAGE, None, (2, 2))
    assert (x0, y0) == (0, 0) and x1 - x0 == pytest.approx(160)


def test_nothing_to_show_means_no_region():
    assert crop_rect(PAGE, None, None) is None


def _pdf(tmp_path):
    path = tmp_path / "d.pdf"
    doc = fitz.open()
    page = doc.new_page(width=PAGE[2], height=PAGE[3])
    page.insert_text((120, 120), "0,05 A", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_a_crop_renders_as_png_and_leaves_the_drawing_untouched(tmp_path):
    path = _pdf(tmp_path)
    before = path.read_bytes()
    png = render_crop(path, (60, 60, 260, 200), (100, 100, 200, 130), (150, 115))
    assert png.startswith(PNG)
    assert path.read_bytes() == before


def test_page_rect_reads_the_first_page(tmp_path):
    assert page_rect(_pdf(tmp_path)) == pytest.approx(PAGE)
    assert page_rect(tmp_path / "missing.pdf") is None


def test_a_missing_drawing_is_a_crop_error_not_a_crash(tmp_path):
    with pytest.raises(CropError):
        render_crop(tmp_path / "missing.pdf", (0, 0, 100, 100))


def test_the_placeholder_is_a_png():
    assert placeholder_png("drawing not available").startswith(PNG)
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest -q tests/eval/test_review_crops.py` → ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/eval/review_crops.py`:

```python
"""Crops of a drawing around one review row, for the OPERATOR's eyes only.

The review app shows two crops per row so nobody opens a PDF or hunts for a
balloon: the clean original with the pipeline's box (red) and gold's balloon
position (blue) drawn on it, and the stamped sheet with the printed balloon.
Overlays go onto an in-memory page that is closed without saving, so the
drawing on disk is never modified.

Geometry is kept pure (`crop_rect`) so it is testable without any PDF.
"""
from typing import Optional, Tuple

import fitz

PAD_PT = 40.0
MIN_W_PT, MIN_H_PT = 160.0, 100.0
GOLD_HALF_PT = 5.0
MAX_PX = 1600
RED = (0.86, 0.15, 0.15)
BLUE = (0.12, 0.42, 0.95)


class CropError(RuntimeError):
    """The drawing or its page is not there; the server shows a placeholder."""


def _grow(a, b, minimum):
    if b - a >= minimum:
        return a, b
    c = (a + b) / 2
    return c - minimum / 2, c + minimum / 2


def _fit(a, b, lo, hi):
    """Shift [a, b] inside [lo, hi] without shrinking it, unless it is wider."""
    width = b - a
    if width >= hi - lo:
        return lo, hi
    if a < lo:
        return lo, lo + width
    if b > hi:
        return hi - width, hi
    return a, b


def crop_rect(page, pred_box_pt, gold_pt) -> Optional[Tuple[float, ...]]:
    boxes = []
    if pred_box_pt:
        boxes.append(tuple(pred_box_pt))
    if gold_pt:
        gx, gy = gold_pt
        boxes.append((gx - GOLD_HALF_PT, gy - GOLD_HALF_PT,
                      gx + GOLD_HALF_PT, gy + GOLD_HALF_PT))
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes) - PAD_PT
    y0 = min(b[1] for b in boxes) - PAD_PT
    x1 = max(b[2] for b in boxes) + PAD_PT
    y1 = max(b[3] for b in boxes) + PAD_PT
    x0, x1 = _grow(x0, x1, MIN_W_PT)
    y0, y1 = _grow(y0, y1, MIN_H_PT)
    x0, x1 = _fit(x0, x1, page[0], page[2])
    y0, y1 = _fit(y0, y1, page[1], page[3])
    return (x0, y0, x1, y1)


def page_rect(pdf_path) -> Optional[Tuple[float, float, float, float]]:
    try:
        with fitz.open(pdf_path) as doc:
            if doc.page_count < 1:
                return None
            r = doc[0].rect
            return (r.x0, r.y0, r.x1, r.y1)
    except Exception:
        return None


def render_crop(pdf_path, rect, pred_box_pt=None, gold_pt=None) -> bytes:
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise CropError("drawing not available") from e
    try:
        if doc.page_count < 1:
            raise CropError("drawing has no pages")
        page = doc[0]
        if pred_box_pt:
            page.draw_rect(fitz.Rect(pred_box_pt), color=RED, width=1.2)
        if gold_pt:
            page.draw_circle(fitz.Point(gold_pt), 3.5, color=BLUE, fill=BLUE)
        clip = fitz.Rect(rect) & page.rect
        if clip.is_empty:
            raise CropError("position is outside the page")
        zoom = min(3.0, MAX_PX / max(clip.width, clip.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip,
                              alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def placeholder_png(message: str) -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page(width=360, height=90)
        page.insert_text((16, 50), message, fontsize=13, color=(0.4, 0.4, 0.4))
        return page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    finally:
        doc.close()
```

- [ ] **Step 4: Run to verify pass** — `python3 -m pytest -q tests/eval/test_review_crops.py` → 9 passed.
- [ ] **Step 5: Commit** — message `feat(eval): drawing crops for the review, overlays never saved`.

---

### Task 4: The server

**Files:**
- Create: `app/eval/review_server.py`
- Create: `app/eval/review_page.html` (a one-line placeholder `<!doctype html><title>gdt review</title>` for now; Task 6 writes the real page)
- Test: `tests/eval/test_review_server.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The review server against the real handler, on a synthetic corpus."""
import json
import threading
import urllib.error
import urllib.request

import fitz
import pytest

from app.eval.review import (ReviewRefused, build_deck, load_answers,
                             load_deck, write_deck)
from app.eval.review_server import ReviewApp, make_server
from tests.eval.test_review import _roots, _rows3


@pytest.fixture
def running(tmp_path, monkeypatch):
    root = tmp_path / "client"
    for sub in ("originals", "stamped"):
        (root / sub).mkdir(parents=True)
        for doc_id in ("P1", "P2"):
            doc = fitz.open()
            doc.new_page(width=1191, height=842).insert_text((100, 100), "x")
            doc.save(root / sub / f"{doc_id}.pdf")
            doc.close()
    _roots(tmp_path, monkeypatch, root)
    deck_path = root / "reports" / "d.json"
    write_deck(deck_path, build_deck(_rows3(), "r", root / "originals",
                                     root / "stamped"))
    app = ReviewApp(deck_path, tmp_path / "docs" / "tally.json")
    srv = make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield app, base, deck_path, tmp_path
    srv.shutdown()
    srv.server_close()


def _get(url):
    return urllib.request.urlopen(url, timeout=5)


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5)


def test_every_route_refuses_a_request_without_the_token(running):
    app, base, _, _ = running
    for path in ("/", "/api/deck", "/crop/g1/clean.png", "/?t=wrong"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(base + path)
        assert e.value.code == 403


def test_it_listens_on_localhost_only(running):
    app, base, _, _ = running
    assert base.startswith("http://127.0.0.1:")


def test_the_page_and_deck_are_served_with_the_token(running):
    app, base, _, _ = running
    page = _get(f"{base}/?t={app.token}")
    assert page.headers["Content-Type"].startswith("text/html")
    assert page.headers["Referrer-Policy"] == "no-referrer"
    deck = json.load(_get(f"{base}/api/deck?t={app.token}"))
    assert [r["id"] for r in deck["rows"]] == ["g1", "g2", "g3"]
    assert deck["answers"] == {} and len(deck["questions"]) == 3
    assert all(r["stamped_approx"] is False for r in deck["rows"])


def test_crops_are_served_as_png_even_when_a_drawing_is_missing(running):
    app, base, deck_path, _ = running
    for which in ("clean", "stamped"):
        r = _get(f"{base}/crop/g1/{which}.png?t={app.token}")
        assert r.headers["Content-Type"] == "image/png"
        assert r.read().startswith(b"\x89PNG")
    (deck_path.parent.parent / "stamped" / "P2.pdf").unlink()
    r = _get(f"{base}/crop/g3/stamped.png?t={app.token}")
    assert r.read().startswith(b"\x89PNG")          # the placeholder


def test_an_unknown_row_is_404(running):
    app, base, _, _ = running
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{base}/crop/g9/clean.png?t={app.token}")
    assert e.value.code == 404


def test_an_answer_is_persisted_and_a_bad_one_is_400(running):
    app, base, deck_path, _ = running
    assert _post(f"{base}/api/answer?t={app.token}",
                 {"id": "g1", "answer": {"drawing_shows": "position"}}).status == 200
    assert load_answers(deck_path, load_deck(deck_path))["g1"] == {
        "drawing_shows": "Position"}
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(f"{base}/api/answer?t={app.token}",
              {"id": "g1", "answer": {"drawing_shows": "Positon"}})
    assert e.value.code == 400


def test_finish_writes_the_counts_only_tally(running):
    app, base, _, tmp_path = running
    _post(f"{base}/api/answer?t={app.token}", {"id": "g1", "answer": {
        "drawing_shows": "Position", "symbol_in_transcription": "glyph",
        "gold_label_means_it": "yes", "note": "SECRET-NOTE"}})
    body = json.load(_post(f"{base}/api/finish?t={app.token}", {}))
    written = json.loads((tmp_path / "docs" / "tally.json").read_text())
    assert written == body["tally"]
    assert written["answered"] == 1 and written["freed_without_gpu"] == 1
    assert "SECRET" not in json.dumps(written)


def test_a_foreign_host_header_is_refused(running):
    """DNS rebinding: a page on another name pointed at 127.0.0.1."""
    app, base, _, _ = running
    req = urllib.request.Request(f"{base}/api/deck?t={app.token}",
                                 headers={"Host": "evil.example:80"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403


def test_the_app_refuses_a_tally_path_inside_a_protected_root(tmp_path,
                                                             monkeypatch):
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    write_deck(root / "d.json", build_deck(_rows3(), "r", "/o", "/s"))
    with pytest.raises(ReviewRefused):
        ReviewApp(root / "d.json", root / "tally.json")
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest -q tests/eval/test_review_server.py` → ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/eval/review_server.py`:

```python
"""The operator's review server: localhost only, one session token.

Started by the OPERATOR (`runner review-serve <deck>`), never by an agent --
it serves client text, and `review-serve` is deliberately absent from the
agent guard's allowlist. It binds 127.0.0.1, checks the Host header (DNS
rebinding) and requires a random token on every request, so neither another
local process nor a web page in the same browser can read the deck. It sends
no-referrer and no-store so the token and the data stay out of history and
caches.
"""
import hmac
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.eval import review, review_crops

_PAGE = Path(__file__).with_name("review_page.html")
_CROP_RE = re.compile(r"^/crop/(g\d+)/(clean|stamped)\.png$")
_MAX_BODY = 64_000
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class ReviewApp:
    def __init__(self, deck_path, tally_out):
        self.deck_path = Path(deck_path)
        if not review.inside_protected(self.deck_path):
            raise review.ReviewRefused("the deck must be inside a protected root")
        self.deck = review.load_deck(self.deck_path)
        review.load_answers(self.deck_path, self.deck)   # fail fast on mismatch
        self.tally_out = review.check_tally_out(tally_out)
        self.token = secrets.token_urlsafe(18)
        self.rows = {r["id"]: r for r in self.deck["rows"]}
        self.approx = {d: self._sizes_differ(d)
                       for d in {r["doc_id"] for r in self.deck["rows"]}}

    def drawing(self, which, doc_id) -> Path:
        key = "originals_dir" if which == "clean" else "stamped_dir"
        return Path(self.deck[key]) / f"{doc_id}.pdf"

    def _sizes_differ(self, doc_id) -> bool:
        a = review_crops.page_rect(self.drawing("clean", doc_id))
        b = review_crops.page_rect(self.drawing("stamped", doc_id))
        if a is None or b is None:
            return False
        return any(abs(x - y) > 1.0 for x, y in zip(a, b))

    def deck_payload(self):
        return {"kind": self.deck["kind"], "run": self.deck["run"],
                "questions": self.deck["questions"],
                "rows": [dict(r, stamped_approx=self.approx[r["doc_id"]])
                         for r in self.deck["rows"]],
                "answers": review.load_answers(self.deck_path, self.deck)}

    def crop(self, rid, which) -> bytes:
        row = self.rows[rid]
        path = self.drawing(which, row["doc_id"])
        page = review_crops.page_rect(path)
        if page is None:
            return review_crops.placeholder_png("drawing not available")
        rect = review_crops.crop_rect(page, row["pred_box_pt"], row["gold_pt"])
        if rect is None:
            return review_crops.placeholder_png("no position recorded")
        clean = which == "clean"
        try:
            return review_crops.render_crop(
                path, rect, row["pred_box_pt"] if clean else None,
                row["gold_pt"] if clean else None)
        except review_crops.CropError as e:
            return review_crops.placeholder_png(str(e))


def make_handler(app: ReviewApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sindri-review"

        def log_message(self, *args):
            pass

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", _CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def _gate(self):
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}",
                                                    f"localhost:{port}"):
                self._json(403, {"error": "wrong host"})
                return None
            url = urlparse(self.path)
            token = parse_qs(url.query).get("t", [""])[0]
            if not hmac.compare_digest(token.encode(), app.token.encode()):
                self._json(403, {"error": "missing or wrong session token"})
                return None
            return url.path

        def do_GET(self):
            path = self._gate()
            if path is None:
                return
            if path == "/":
                self._send(200, _PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/deck":
                self._json(200, app.deck_payload())
            elif (m := _CROP_RE.match(path)) and m.group(1) in app.rows:
                self._send(200, app.crop(m.group(1), m.group(2)), "image/png")
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self._gate()
            if path is None:
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._json(413, {"error": "request too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": "not JSON"})
                return
            try:
                if path == "/api/answer":
                    review.save_answer(app.deck_path, app.deck,
                                       str(body.get("id", "")),
                                       body.get("answer") or {})
                    self._json(200, {"ok": True})
                elif path == "/api/finish":
                    t = review.tally(app.deck, review.load_answers(
                        app.deck_path, app.deck))
                    review.write_tally(app.tally_out, t)
                    self._json(200, {"tally": t,
                                     "written_to": str(app.tally_out)})
                else:
                    self._json(404, {"error": "not found"})
            except review.ReviewRefused as e:
                self._json(400, {"error": str(e)})

    return Handler


def make_server(app: ReviewApp, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


def serve(deck_path, tally_out, port: int = 0) -> int:
    app = ReviewApp(deck_path, tally_out)
    srv = make_server(app, port)
    print(f"gdt review: {len(app.rows)} rows. Open this in your browser:\n\n"
          f"  http://127.0.0.1:{srv.server_address[1]}/?t={app.token}\n\n"
          f"Answers save as you go. Ctrl+C stops the server.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
```

- [ ] **Step 4: Run to verify pass** — `python3 -m pytest -q tests/eval/test_review_server.py` → all pass.
- [ ] **Step 5: Commit** — message `feat(eval): localhost review server with a session token`.

---

### Task 5: Runner wiring

**Files:**
- Modify: `app/eval/runner.py` — `_cmd_score` (replace the `gdt_worksheet` block with `review_deck`), `_cmd_review_tally` (take a deck), new `_cmd_review_serve`, argparse
- Test: `tests/eval/test_review.py` (CLI section)

- [ ] **Step 1: Write the failing tests**

```python
from app.eval.runner import main  # noqa: E402
from tests.eval.test_score_scope_policy import _corpus  # noqa: E402


def _score_args(tmp_path, run_dir, gold_dir, pdfs, deck):
    return ["score", "--run", str(run_dir), "--gold", str(gold_dir),
            "--pdfs", str(pdfs), "--name", "ws",
            "--out", str(tmp_path / "scored.json"), "--review-deck", str(deck)]


def test_score_refuses_a_deck_outside_a_protected_root_before_scoring(
        tmp_path, monkeypatch):
    _roots(tmp_path, monkeypatch, tmp_path / "client")
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    assert main(_score_args(tmp_path, run_dir, gold_dir, pdfs,
                            tmp_path / "d.json")) == 1
    assert not (tmp_path / "d.json").exists()
    assert not (tmp_path / "scored.json").exists()


def test_score_needs_pdfs_to_know_where_the_drawings_are(tmp_path, monkeypatch):
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    args = [a for a in _score_args(tmp_path, run_dir, gold_dir, pdfs,
                                   root / "d.json")]
    i = args.index("--pdfs"); del args[i:i + 2]
    assert main(args) == 1


def test_score_writes_the_deck_and_prints_only_a_count(tmp_path, monkeypatch,
                                                       capsys):
    root = tmp_path / "client"; root.mkdir()
    _roots(tmp_path, monkeypatch, root)
    pdfs, gold_dir, run_dir = _corpus(tmp_path)
    deck = root / "reports" / "d.json"
    assert main(_score_args(tmp_path, run_dir, gold_dir, pdfs, deck)) == 0
    d = load_deck(deck)
    assert d["originals_dir"] == str(pdfs)
    assert d["stamped_dir"] == str(pdfs.parent / "stamped")
    out = capsys.readouterr().out
    assert "review deck:" in out and '"rows"' not in out


def test_review_tally_reads_the_answers_beside_the_deck(tmp_path, monkeypatch):
    path, deck = _saved(tmp_path, monkeypatch)
    save_answer(path, deck, "g1", _a("Position", "glyph", "yes"))
    out = tmp_path / "docs" / "tally.json"
    assert main(["review-tally", str(path), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["freed_without_gpu"] == 1


def test_review_serve_refuses_a_tally_path_inside_a_root(tmp_path, monkeypatch):
    path, _ = _saved(tmp_path, monkeypatch)
    assert main(["review-serve", str(path),
                 "--tally-out", str(path.parent / "t.json")]) == 1
```

- [ ] **Step 2: Run to verify failure** — argparse exits on unknown `--review-deck` → SystemExit / failures.

- [ ] **Step 3: Implement** in `app/eval/runner.py`:

At the top of `_cmd_score`, replacing the `gdt_worksheet` block:

```python
    deck_out = getattr(args, "review_deck", None)
    if deck_out:
        # Checked BEFORE scoring: a bad path fails in seconds, and a refusal
        # that had already done the work would read as a warning.
        from app.eval.review import ReviewRefused, check_deck_path
        if not getattr(args, "pdfs", None):
            print("ERROR: --review-deck needs --pdfs -- the review crops are "
                  "cut from the drawings, and --pdfs is where they are",
                  file=sys.stderr)
            return 1
        try:
            check_deck_path(deck_out)
        except ReviewRefused as e:
            print(f"ERROR: --review-deck refused: {e}", file=sys.stderr)
            return 1
```

At the end of `_cmd_score`, replacing the worksheet write:

```python
    if deck_out:
        from app.eval.review import build_deck, collect_gdt_rows, write_deck
        pdfs = Path(args.pdfs)
        n = write_deck(deck_out, build_deck(
            collect_gdt_rows(dumps, gold, scores), args.name,
            originals_dir=pdfs, stamped_dir=pdfs.parent / "stamped"))
        print(f"review deck: {n} row(s) for a human reviewer. It contains "
              f"client data -- the operator opens it with review-serve, "
              f"never an agent.")
    return 0
```

Replace `_cmd_review_tally` and add `_cmd_review_serve`:

```python
def _cmd_review_tally(args):
    """For the OPERATOR's terminal: counts from a reviewed deck. Not in the
    agent guard's allowlist -- the deck lives in a protected root. Its output
    is closed-vocabulary counts only."""
    from app.eval.review import (ReviewRefused, load_answers, load_deck,
                                 tally, write_tally)
    try:
        deck = load_deck(args.deck)
        t = tally(deck, load_answers(args.deck, deck))
        if args.out:
            write_tally(args.out, t)
    except ReviewRefused as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(json.dumps(t, indent=1, ensure_ascii=False))
    return 0


def _cmd_review_serve(args):
    """For the OPERATOR: the local review app. Never an agent's command."""
    from app.eval.review import ReviewRefused
    from app.eval.review_server import serve
    try:
        return serve(args.deck, args.tally_out, args.port)
    except ReviewRefused as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
```

Argparse — replace the `--gdt-worksheet` argument and the `review-tally` parser:

```python
    p.add_argument("--review-deck", default=None,
                   help="write the operator's review deck -- the gdt rows "
                        "wrong in char_type ONLY -- to this path. It holds "
                        "client text, so it must be inside a protected root "
                        "and must not exist yet. Needs --pdfs. Only a row "
                        "count is printed. Open it with review-serve.")
    p.set_defaults(fn=_cmd_score)

    p = sub.add_parser("review-tally", parents=[common])
    p.add_argument("deck")
    p.add_argument("--out", default=None,
                   help="write the counts here, e.g. docs/eval/"
                        "gdt-review-tally.json -- counts only, safe to commit")
    p.set_defaults(fn=_cmd_review_tally)

    p = sub.add_parser("review-serve", parents=[common])
    p.add_argument("deck")
    p.add_argument("--tally-out", default="docs/eval/gdt-review-tally.json",
                   help="where Finish writes the counts-only tally; relative "
                        "paths resolve against the current directory")
    p.add_argument("--port", type=int, default=0,
                   help="default: any free port")
    p.set_defaults(fn=_cmd_review_serve)
```

- [ ] **Step 4: Run** — `python3 -m pytest -q tests/eval/test_review.py tests/eval/test_review_server.py` → all pass; then the full suite `python3 -m pytest -q`.
- [ ] **Step 5: Commit** — message `feat(eval): score --review-deck, review-serve and review-tally replace the worksheet`.

---

### Task 6: The review page

**Files:**
- Modify: `app/eval/review_page.html` (replace the placeholder)
- Test: `tests/eval/test_review_server.py` (one contract test)

The page is the one piece without automated behaviour tests (spec §6); it is
written with the `frontend-design` skill and checked against this contract.

**Contract** (every item is visible in the code and checked by reading it):

1. Reads the token from `location.search` (`t`) and appends it to every
   request: `GET /api/deck`, `GET /crop/<id>/<clean|stamped>.png`,
   `POST /api/answer`, `POST /api/finish`. No other URLs, no external assets.
2. Header: title, `row N of M`, one clickable dot per row (filled = all three
   answered), save status (`saving…` / `saved ✓` / `not saved — retrying`).
3. Row view: part and drawing name, balloon, rough location, `SILENT ERROR
   today` badge when `silent`; the two crops side by side (stacked on narrow
   screens), captions explaining red box / blue dot, and "position
   approximate" on the stamped crop when `stamped_approx`; the label and the
   transcription as verbatim text (monospace, `textContent`, never
   `innerHTML`); the predicted type with "a guess: no symbol recognised" when
   `parser_defaulted`; what the scorer reads the label as.
4. Questions rendered from `deck.questions` (no question text hardcoded):
   option buttons (a symbol option shows symbol AND name, with the name
   as its `title` too); the question with `filter: true` also gets a
   type-to-filter box (Enter picks the only/first match); option `hotkey`s
   work for the active question when focus is not in a text field; clicking
   the selected option again clears it. The active question advances after
   an answer. `note` is a text field, saved 400 ms after typing stops.
5. Keys: `→`/`Enter` next row, `←` previous (not while typing in the note
   except Enter, which also moves on). Starts at the first incomplete row.
6. Every change POSTs `{id, answer: {key: value}}`; failures retry with
   backoff and keep the status visible.
7. Finish button always present; with unanswered rows it confirms "N rows
   unanswered — finish anyway?"; then shows the tally summary (answered,
   parser vs read stage, gold side, freed without GPU) and the line "Done —
   tell the agent: done". The tally shown is the server's response.
8. Readable in light and dark (`prefers-color-scheme`), keyboard focus
   visible, buttons ≥ 36 px tall, works at 1280 px and at 800 px wide.

- [ ] **Step 1: Write the failing contract test**

```python
def test_the_page_is_self_contained_and_uses_only_the_served_routes(running):
    app, base, _, _ = running
    html = _get(f"{base}/?t={app.token}").read().decode()
    for route in ("/api/deck", "/api/answer", "/api/finish", "/crop/"):
        assert route in html, route
    for external in ("http://", "https://", "//cdn", "<link"):
        assert external not in html, external
    assert "innerHTML" not in html     # client text is set with textContent
```

- [ ] **Step 2: Run** — fails against the placeholder page.
- [ ] **Step 3: Write the page** (frontend-design skill; the contract above).
- [ ] **Step 4: Run** — `python3 -m pytest -q tests/eval/test_review_server.py` → pass. Then serve a synthetic deck locally (`tests` fixture corpus) and look at it in a browser.
- [ ] **Step 5: Commit** — message `feat(eval): the review page`.

---

### Task 7: Real data, docs, verification

- [ ] **Step 1:** generate the real deck through the sanctioned command (agent-safe; prints a count):

```
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-cropctx" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --weights docs/eval/weights.json --name r3-cropctx-scoped --out "$HOME/sindri-client-data/reports/r3-cropctx-scoped.report.json" --review-deck "$HOME/sindri-client-data/reports/gdt-review.deck.json"
```

Expected: `review deck: 8 row(s) …`.

- [ ] **Step 2:** re-run the guard probe (scratchpad script) extended with `review-serve` and the deck/answers paths: all agent reads DENY, sanctioned `score` ALLOW. `bash ~/.claude/hooks/test-sindri-guard.sh` → 32 passed.
- [ ] **Step 3:** docs — `2026-09-22-gdt-review.md` §2 now says: run `review-serve`, open the link, answer, Finish, say "done"; CLAUDE.md §1 names `review-serve` alongside `review-tally` as operator-only; §2's worksheet paragraph points to the app; suite count.
- [ ] **Step 4:** `python3 -m pytest -q` → all pass; `_prompt_sha256` unchanged.
- [ ] **Step 5:** commit docs — message `docs: the gdt review is an app now`.
