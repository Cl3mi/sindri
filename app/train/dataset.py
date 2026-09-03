"""Build (crop, target) training pairs from boxes plus gold.

NDA shape, and it is stricter than the digests. The pairs ARE client values: the
target text is the inspection sheet's number. So the dataset is written under the
protected root, never committed, never printed, and the ONLY thing this function
returns is a count dict — checked for leakage by its own test.

Crops are produced by the pipeline's own `boxes.tighten_to_ink` and
`extract._prep_crop`, never reimplemented. A training crop that differs from an
inference crop teaches the model the wrong input distribution, which would show
up as a LoRA that helps on paper and not in the pipeline."""
import json
import random
from pathlib import Path
from typing import Dict, Iterable, Tuple

from app.pipeline import boxes as bx
from app.pipeline.extract import _CROP_PAD, _HINTS, _prep_crop
from app.train.targets import UnrenderableRow, render_target


def build_pairs(gold, page_image, matched: Iterable[Tuple[int, object]],
                out_dir) -> Dict[str, int]:
    """Write one PNG + one manifest line per renderable matched row.

    `matched` is (gold_balloon, prediction) pairs — the prediction supplies the
    box and the detector kind, gold supplies the target. Both are needed: the box
    without gold has no answer, and gold without the box has no image.

    Every rejection is counted rather than raised. One unusable row must not end a
    60-document build, and the counts are the only evidence anyone will ever see
    of what this produced."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gold_by_num = {g.balloon: g for g in gold.characteristics}
    w, h = page_image.size
    counts = {"pairs": 0, "unrenderable": 0, "no_gold": 0, "no_box": 0}

    with (out / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for balloon, pred in matched:
            g = gold_by_num.get(balloon)
            if g is None:
                counts["no_gold"] += 1
                continue
            region = getattr(pred, "target_region", None)
            if region is None:
                counts["no_box"] += 1
                continue
            hint = _HINTS.get(getattr(pred, "kind", "") or "", "")
            try:
                target = render_target(g, hint)
            except UnrenderableRow as e:
                # Counted, never approximated: a made-up target would train the
                # model toward a value gold does not hold.
                # Counted BY REASON as well, because a bare total is not a
                # diagnosis: the first train-split build reported 790 of these
                # and the only way to learn why was to read the code and guess.
                # The slug set is closed, so this stays values-blind.
                counts["unrenderable"] += 1
                key = "unrenderable:" + e.reason
                counts[key] = counts.get(key, 0) + 1
                continue
            box = bx.tighten_to_ink(page_image, region)
            crop = _prep_crop(page_image, box, w, h, pad=_CROP_PAD)
            name = f"{gold.doc_id}-{balloon:04d}.png"
            crop.save(out / name)
            fh.write(json.dumps({"image": name, "target": target,
                                 "hint": hint, "balloon": balloon},
                                ensure_ascii=False) + "\n")
            counts["pairs"] += 1
    return counts


def _doc_of(row) -> str:
    """The document a manifest row belongs to.

    build_pairs writes one directory per document and the merged manifest
    prefixes each image name with it, so the prefix IS the document id."""
    return str(row["image"]).split("/", 1)[0]


def split_by_document(rows, holdout_frac: float = 0.1, seed: int = 13):
    """Split manifest rows into (train, validation), never splitting a document.

    By document, not by row, and that is the whole point. Crops from one drawing
    share its house style, its scan quality and often its exact tolerance
    values, so a validation crop whose drawing also appears in training measures
    memorisation rather than generalisation. The ladder names that as Rung 3's
    primary risk, and 735 pairs against a 72B makes it sharper -- the frozen
    corpus split already applies the same rule at the document level.

    Why a holdout exists at all: with three epoch checkpoints and no eval, the
    choice between them is arbitrary. This makes it eval_loss.

    Deterministic given `seed`, because a training run that cannot be reproduced
    cannot be compared against, and every conclusion here rests on A/B runs
    being attributable. Documents are sorted before shuffling so the result does
    not depend on manifest order.

    Raises rather than returning an empty holdout: transformers'
    load_best_model_at_end would then select a checkpoint on no evidence while
    the run still looked healthy."""
    by_doc = {}
    for row in rows:
        by_doc.setdefault(_doc_of(row), []).append(row)
    if len(by_doc) < 2:
        raise ValueError(
            f"cannot hold out by document: the manifest covers {len(by_doc)} "
            f"document(s), so any holdout either is empty or takes the whole "
            f"training set. Build pairs over more documents first.")

    docs = sorted(by_doc)
    random.Random(seed).shuffle(docs)
    target = len(rows) * holdout_frac

    held, n = [], 0
    for doc in docs:
        if held and n >= target:
            break
        if len(held) == len(docs) - 1:      # always leave one to train on
            break
        held.append(doc)
        n += len(by_doc[doc])

    held_set = set(held)
    train = [r for r in rows if _doc_of(r) not in held_set]
    val = [r for r in rows if _doc_of(r) in held_set]
    return train, val
