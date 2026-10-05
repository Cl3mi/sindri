from app.eval.splits import make_splits, save_splits, load_splits, splits_hash

DOCS = [f"T{i:03d}" for i in range(20)]
VARIANTS = ["T003", "T011", "T017"]


def test_split_is_document_level_disjoint_and_complete():
    s = make_splits(DOCS, VARIANTS, seed=13)
    all_ids = s["train"] + s["dev"] + s["test"]
    assert sorted(all_ids) == sorted(DOCS)
    assert not (set(s["train"]) & set(s["dev"]))
    assert not (set(s["train"]) & set(s["test"]))
    assert not (set(s["dev"]) & set(s["test"]))


def test_variants_forced_into_test():
    s = make_splits(DOCS, VARIANTS, seed=13)
    assert set(VARIANTS) <= set(s["test"])


def test_same_seed_same_split_different_seed_different():
    assert make_splits(DOCS, VARIANTS, seed=13) == make_splits(DOCS, VARIANTS, seed=13)
    assert make_splits(DOCS, VARIANTS, seed=13) != make_splits(DOCS, VARIANTS, seed=14)


def test_roundtrip_and_stable_hash(tmp_path):
    s = make_splits(DOCS, VARIANTS, seed=13)
    path = tmp_path / "splits.json"
    save_splits(s, path)
    s2 = load_splits(path)
    assert s2 == s
    assert splits_hash(s) == splits_hash(s2)
