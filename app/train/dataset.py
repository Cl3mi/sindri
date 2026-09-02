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
