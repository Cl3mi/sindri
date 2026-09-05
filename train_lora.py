"""LoRA-train the callout-read task on Qwen2.5-VL-72B, quantised to 4-bit NF4.

Runs inside Dockerfile.train on the GPU host, over a manifest built by
app.train.dataset. It never reads gold or drawings directly — only the manifest,
which already pairs a crop with its rendered target. It prints COUNTS only: the
manifest's `target` field is the client's inspection value.

Holdout discipline, from the ladder: train on the TRAIN split only. The frozen
corpus split forces all 18 variant drawings into test, so a model tuned on dev
and confirmed once on test measures generalisation rather than memorisation.
This script therefore takes one manifest and treats all of it as training data;
producing a train-only manifest is the caller's job.

Inside that manifest it holds out a further slice BY DOCUMENT (see
app.train.dataset.split_by_document) purely to choose between epoch
checkpoints. Without it, `save_strategy="epoch"` writes three adapters and
nothing says which to serve — and on 735 pairs against a 72B, that choice is
not a detail. Crops from one drawing share its house style and often its exact
tolerance values, so the holdout has to be by document or it measures
memorisation too.
"""
import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (AutoProcessor, BitsAndBytesConfig,
                          Qwen2_5_VLForConditionalGeneration, Trainer,
                          TrainingArguments)

from app.pipeline.ocr.vlm_backend import read_prompt
from app.train.dataset import filter_readable_crops, split_by_document

# The 72B, quantised to 4-bit NF4. AWQ -- what inference deploys -- is
# inference-only, so it cannot be trained against; 4-bit puts the base at ~36 GB
# and the whole run at ~42-48 GB, which fits ONE H100 80 GB. That matters: the
# two cards report NODE topology rather than NVLink, so PCIe-bound model
# parallelism is worth avoiding entirely. Measured at 38.8 GB by the Task 7 gate.
_BASE = "Qwen/Qwen2.5-VL-72B-Instruct"

# The tensors that carry a batch dimension. EVERYTHING ELSE MUST NOT BE INDEXED.
# Qwen2.5-VL's processor returns pixel_values as a flat [patches, dim] sequence
# with no batch dimension, and image_grid_thw as [images, 3]. Taking [0] of
# those -- which the original spec did to every key alike -- hands the vision
# tower one patch instead of 56 and crashes it inside
# `reshape(seq_len // spatial_merge_unit, ...)` with `shape '[0, 4, -1]'`.
_SEQ_KEYS = ("input_ids", "attention_mask", "labels")


def collate(rows):
    """Batch examples the way Qwen2.5-VL expects.

    Sequence tensors stack into [B, L]; the vision tensors CONCATENATE, because
    pixel_values is one flat patch sequence over the whole batch and
    image_grid_thw says how to cut it back up. Stacking those would invent a
    batch dimension the model does not read."""
    out = {}
    for key in rows[0]:
        if key in _SEQ_KEYS:
            out[key] = torch.stack([r[key] for r in rows])
        else:
            out[key] = torch.cat([r[key] for r in rows], dim=0)
    return out


def shape_check(dataset, n: int) -> None:
    """Assert a collated batch has the shapes the vision tower requires.

    This exists because the shape bug above cost a full overnight GPU window:
    training died four minutes in, at step 0 of 243, after the controls had
    already finished and freed the card. Every part of it was checkable on CPU
    in seconds -- the processor runs fine without a GPU -- so it is now checked
    before the 145 GB model load rather than after.

    It also asserts the labels are not entirely masked. If the prompt were as
    long as the full sequence, every label would be -100, the loss would be
    computed over nothing, and the run would look healthy for hours while
    learning nothing at all."""
    rows = [dataset[i] for i in range(min(n, len(dataset)))]
    for i, row in enumerate(rows):
        pv = row["pixel_values"]
        assert pv.dim() == 2, f"example {i}: pixel_values is {tuple(pv.shape)}, expected [patches, dim]"
        assert pv.shape[0] >= 4, (
            f"example {i}: only {pv.shape[0]} patch(es); the vision tower's "
            f"spatial_merge_unit is 4 and reshape would fail")
        assert row["image_grid_thw"].dim() == 2, (
            f"example {i}: image_grid_thw is {tuple(row['image_grid_thw'].shape)}, expected [images, 3]")
        for key in _SEQ_KEYS:
            assert row[key].dim() == 1, f"example {i}: {key} is {tuple(row[key].shape)}, expected [L]"
        assert row["labels"].shape == row["input_ids"].shape
        assert bool((row["labels"] != -100).any()), (
            f"example {i}: every label is masked, so this example contributes "
            f"no gradient")
    batch = collate(rows[:1])
    print(json.dumps({"examples_checked": len(rows),
                      "batch_shapes": {k: list(v.shape) for k, v in batch.items()}}),
          flush=True)
    print("shape check OK", flush=True)


class ReadDataset(torch.utils.data.Dataset):
    """One example = the read prompt + a callout crop -> its rendered target.

    The prompt is the SAME one inference sends (vlm_backend.read_prompt),
    because a LoRA trained against a different instruction than it is served
    with measures the mismatch rather than the task."""

    def __init__(self, rows, root: Path, processor):
        self.rows = rows
        self.root = root
        self.processor = processor
        self.prompt = read_prompt()

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        row = self.rows[i]
        image = Image.open(self.root / row["image"]).convert("RGB")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": self.prompt},
        ]}]
        prompt_text = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False)
        full = prompt_text + row["target"] + self.processor.tokenizer.eos_token
        enc = self.processor(text=[full], images=[image], return_tensors="pt",
                             padding=True)
        # Squeeze the batch dimension from the SEQUENCE tensors only -- see
        # _SEQ_KEYS. Indexing [0] into pixel_values takes patch zero of 56.
        enc = {k: (v[0] if k in _SEQ_KEYS else v) for k, v in enc.items()}
        # Loss on the answer only: the prompt is byte-identical in every
        # example, so training on it teaches nothing and dilutes the gradient.
        prompt_len = len(self.processor(text=[prompt_text], images=[image],
                                        return_tensors="pt")["input_ids"][0])
        labels = enc["input_ids"].clone()
        labels[:prompt_len] = -100
        enc["labels"] = labels
        return enc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True,
                    help="manifest.jsonl written by app.train.dataset")
    ap.add_argument("--out", required=True, help="where the adapter is written")
    ap.add_argument("--base", default=_BASE)
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=8,
                    help="LoRA rank. 8 rather than 16 because the corpus yields "
                         "735 pairs: r=16 over q/k/v/o of a 72B is ~84M "
                         "trainable parameters, ~114k per example, and "
                         "overfitting is the ladder's named primary risk here.")
    ap.add_argument("--holdout-frac", type=float, default=0.1,
                    help="fraction of pairs held out BY DOCUMENT to choose the "
                         "epoch checkpoint")
    ap.add_argument("--seed", type=int, default=13,
                    help="holdout seed; 13 matches the frozen corpus split")
    ap.add_argument("--shape-check", type=int, default=0, metavar="N",
                    help="build N examples, assert the shapes the vision tower "
                         "requires, and exit WITHOUT loading the model. Runs on "
                         "CPU in seconds. A shape bug here once cost a whole "
                         "overnight GPU window by failing at step 0 of 243.")
    args = ap.parse_args()

    manifest = Path(args.manifest)
    rows = [json.loads(line) for line in
            manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Before the split, so a dropped crop cannot distort the holdout fraction.
    rows, unreadable = filter_readable_crops(rows, manifest.parent)
    train_rows, val_rows = split_by_document(
        rows, holdout_frac=args.holdout_frac, seed=args.seed)

    def n_docs(rs):
        return len({r["image"].split("/", 1)[0] for r in rs})

    # Counts only. The manifest's target field is the client's value.
    print(json.dumps({"pairs": len(rows), "dropped_unreadable_crops": unreadable,
                      "train_pairs": len(train_rows), "train_docs": n_docs(train_rows),
                      "val_pairs": len(val_rows), "val_docs": n_docs(val_rows),
                      "rank": args.rank, "epochs": args.epochs,
                      "seed": args.seed}), flush=True)

    # Processor and datasets FIRST, model second. Loading 145 GB before finding
    # out the data is malformed is how the shape bug burned an overnight window.
    processor = AutoProcessor.from_pretrained(args.base)
    root = manifest.parent
    train_ds = ReadDataset(train_rows, root, processor)
    val_ds = ReadDataset(val_rows, root, processor)

    if args.shape_check:
        shape_check(train_ds, args.shape_check)
        return 0

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.base, device_map="auto",
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True))
    # Casts layer norms to fp32 and enables input grads, both of which a 4-bit
    # base needs before LoRA layers will train stably.
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=True)
    # Attention projections only. The vision tower is left frozen: the task is
    # transcription of a crop the tower already resolves, and adapting it on
    # ~700 examples from one house style is the fastest route to the
    # memorisation the ladder warns about.
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]))
    model.print_trainable_parameters()

    Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=args.out, num_train_epochs=args.epochs,
            per_device_train_batch_size=1, per_device_eval_batch_size=1,
            gradient_accumulation_steps=8,
            learning_rate=args.lr, bf16=True, gradient_checkpointing=True,
            # paged_adamw keeps optimizer state off the card during spikes; with
            # a 38.8 GB 4-bit base there is headroom, but a single oversized crop
            # should not be what ends a multi-hour run.
            optim="paged_adamw_8bit",
            # Evaluate and checkpoint on the same cadence, which
            # load_best_model_at_end requires. Without this the run writes three
            # adapters and nothing says which one to serve.
            eval_strategy="epoch", save_strategy="epoch",
            load_best_model_at_end=True, metric_for_best_model="eval_loss",
            greater_is_better=False, save_total_limit=2,
            # The dataset is a torch Dataset returning tensor dicts; column
            # pruning is for datasets.Dataset and would strip them.
            remove_unused_columns=False,
            seed=args.seed,
            logging_steps=10, report_to=[]),
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=collate,
    ).train()

    # load_best_model_at_end leaves the best-eval_loss adapter in memory, so
    # this saves the selected checkpoint rather than the last epoch's.
    model.save_pretrained(args.out)
    print(f"adapter written to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
