"""Document-level train/dev/test split. Generated ONCE from the ingested
corpus, committed to docs/eval/splits.json, and never regenerated (the test
set is frozen — handoff §6). Variant drawings are forced into test so
cross-template generalization stays visible."""
import hashlib
import json
import random
from pathlib import Path
from typing import Dict, List

from app.eval.models import SCHEMA_VERSION


def make_splits(doc_ids: List[str], variant_ids: List[str], seed: int = 13,
                dev_frac: float = 0.2, test_frac: float = 0.2) -> Dict:
    doc_ids = sorted(set(doc_ids))
    variants = sorted(set(variant_ids) & set(doc_ids))
    rest = [d for d in doc_ids if d not in variants]
    random.Random(seed).shuffle(rest)
    n = len(doc_ids)
    n_test = max(0, int(round(n * test_frac)) - len(variants))
    n_dev = int(round(n * dev_frac))
    test = sorted(variants + rest[:n_test])
    dev = sorted(rest[n_test:n_test + n_dev])
    train = sorted(rest[n_test + n_dev:])
    return {"schema_version": SCHEMA_VERSION, "seed": seed,
            "variants": variants, "train": train, "dev": dev, "test": test}


def splits_hash(splits: Dict) -> str:
    blob = json.dumps(splits, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def save_splits(splits: Dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(splits, indent=1), encoding="utf-8")
    return path


def load_splits(path) -> Dict:
    splits = json.loads(Path(path).read_text(encoding="utf-8"))
    if splits.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported splits schema_version in {path}")
    return splits
