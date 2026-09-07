"""Merge `read-lora-v1` into the bf16 base and re-quantise to AWQ.

Runs on the GPU host. Handoff §4 route 1: PEFT cannot attach an adapter to an
AWQ checkpoint at all -- autoawq replaces every q/k/v/o projection with
WQLinear_GEMM and PEFT injects only into the module types it lists -- so the
only way to serve the fine-tune on what production actually runs is to fold the
adapter into plain bf16 weights and quantise the result. The output is an
ordinary Qwen2.5-VL AWQ checkpoint with no adapter at all: no PEFT at serving
time, and none of the NF4 route's +6.35 review cost or its inference-time
penalty.

Touches NO client data. The calibration set is autoawq's own generic corpus;
the adapter is weights, not values.

Two runs are needed, and the control is not optional:

    --zero-scale   the CONTROL. Same round trip, adapter contribution scaled to
                   zero, so the merge is a numeric no-op. Re-quantised the same
                   way it must reproduce r3-awqcontrol (170.05). If it does not,
                   the round trip itself moved the model and no delta from this
                   route is attributable to the fine-tune.
    (default)      the ARM.

Prints counts and paths only.
"""
import argparse
import json
import sys
from pathlib import Path

from app.train.merge import zero_lora_scaling

_BASE = "Qwen/Qwen2.5-VL-72B-Instruct"


def preflight(base: str, adapter: Path, out: Path) -> dict:
    """Everything checkable before the 145 GB load, for the reason
    train_lora.py has --shape-check: two separate bugs there were each worth a
    whole run, and both were visible without a model."""
    cfg = adapter / "adapter_config.json"
    if not cfg.is_file():
        raise SystemExit(f"no adapter_config.json under {adapter}")
    conf = json.loads(cfg.read_text())
    if out.exists() and any(out.iterdir()):
        raise SystemExit(
            f"{out} is not empty. Refusing to write a checkpoint over another "
            f"one: the arm and its zero-scale control differ in nothing a "
            f"directory listing shows, and mixing them is unrecoverable.")
    return {"base": base,
            "adapter_base": conf.get("base_model_name_or_path"),
            "target_modules": sorted(conf.get("target_modules") or []),
            "r": conf.get("r"), "alpha": conf.get("lora_alpha")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--adapter", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path,
                    help="where the merged (and, unless --merge-only, "
                         "quantised) checkpoint is written")
    ap.add_argument("--base", default=_BASE)
    ap.add_argument("--zero-scale", action="store_true",
                    help="build the CONTROL: identical round trip, adapter "
                         "contribution zeroed")
    ap.add_argument("--merge-only", action="store_true",
                    help="stop after the bf16 merge (minutes); quantisation is "
                         "the hours-long half")
    ap.add_argument("--dry-run", action="store_true",
                    help="preflight only, no model load")
    args = ap.parse_args(argv)

    facts = preflight(args.base, args.adapter, args.out)
    print(json.dumps({**facts, "zero_scale": args.zero_scale}, indent=1))
    if args.dry_run:
        return 0

    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    # bf16, not NF4: merging into a quantised base would fold the adapter into
    # already-lossy weights and then quantise again, so the checkpoint would
    # carry two rounds of quantisation error and the delta would measure that.
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.base, torch_dtype=torch.bfloat16, device_map="cpu")
    model = PeftModel.from_pretrained(model, str(args.adapter))

    if args.zero_scale:
        print(f"zeroed {zero_lora_scaling(model)} LoRA layers (CONTROL run)")

    merged = model.merge_and_unload()
    args.out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(args.out), safe_serialization=True)
    AutoProcessor.from_pretrained(args.base).save_pretrained(str(args.out))
    print(f"merged checkpoint written to {args.out}")
    if args.merge_only:
        return 0

    from awq import AutoAWQForCausalLM
    # The same quantisation production already serves: 4-bit, group 128, GEMM.
    # Any departure here would be a second variable on top of the merge.
    quant = {"zero_point": True, "q_group_size": 128, "w_bit": 4,
             "version": "GEMM"}
    awq_out = args.out.parent / (args.out.name + "-awq")
    awq_model = AutoAWQForCausalLM.from_pretrained(str(args.out))
    awq_model.quantize(AutoProcessor.from_pretrained(args.base).tokenizer,
                       quant_config=quant)
    awq_model.save_quantized(str(awq_out))
    print(f"AWQ checkpoint written to {awq_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
