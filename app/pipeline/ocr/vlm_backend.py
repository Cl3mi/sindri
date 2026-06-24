import os
from typing import Optional
from PIL import Image
from app.pipeline.ocr.base import OcrResult

# Default model; override with VLM_MODEL_ID (e.g. a 3B variant for lower VRAM).
_DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

# Constrained transcription prompt: the model TRANSCRIBES the callout in the
# format the parser already understands; it does not reason or explain. This
# keeps reads faithful and low-hallucination, and reuses the tested parser.
_PROMPT = (
    "This image is a single dimension callout cropped from a mechanical "
    "engineering drawing. Transcribe ONLY the dimension and its tolerances as "
    "plain text on one line, e.g. '1,2 +0,1 -0,1' or 'Ø7 +0,1 -0,1' or "
    "'R0,5 MAX'. Use a comma as the decimal separator. Preserve the symbols "
    "Ø, R and ±. Ignore leader lines, dimension lines and arrowheads. "
    "If there is no dimension text, output nothing. No explanation."
)

# Detection prompt: the model LOCATES every inspection callout in the tile and
# returns JSON only. It does not need to transcribe accurately — Stage 2 re-reads
# each crop with read_region. kind is coarse; box is in pixels of THIS image.
_DETECT_PROMPT = (
    "This image is a tile cropped from a mechanical engineering drawing. Find "
    "EVERY inspection callout: linear/diameter/radius dimensions with their "
    "tolerances, GD&T feature-control frames, surface-finish symbols, numbered "
    "notes, and material/process specifications. Return ONLY a JSON array, no "
    "prose. Each element: {\"box\":[x0,y0,x1,y1],\"kind\":\"dimension|gdt|"
    "surface|note|material\"}. box is pixel coordinates within this image. If "
    "there are no callouts, return []."
)

# GD&T read prompt: the crop is the INNER content of a feature-control frame
# (border already stripped). Transcribe symbol, tolerance value and datum(s)
# on one line, e.g. "⊕ Ø0.1 A". The parser maps this to 0 / zone / 0.
_GDT_PROMPT = (
    "This image is the inner content of a GD&T feature control frame from a "
    "mechanical drawing, with the surrounding box border removed. Transcribe it "
    "on one line as: <symbol> <tolerance value> <datum letters>, e.g. "
    "'⊕ Ø0.1 A' or '⏥ 0,05'. Use a comma as the decimal separator. Preserve the "
    "geometric symbol and any Ø. Output nothing else, no explanation."
)


# Notes-block read prompt: the crop is the general-notes table from the
# drawing. Each row begins with a 3-digit number (101…); some rows contain
# inline numbered sub-bullets (1., 2., …). The model returns tab-separated
# triples so the parser can align EN and DE columns in one pass.
_NOTES_PROMPT = (
    "This image is the general-notes table from a mechanical engineering "
    "drawing. Each row begins with a 3-digit number (101, 102, …) followed "
    "by the English note and then the German note. Some rows contain inline "
    "numbered sub-bullets (1., 2., 3., …) — preserve them with their "
    "numbers. Output one row per line in the form:\n"
    "  <pos>\\t<english>\\t<german>\n"
    "For sub-bullets, prefix with the parent pos: e.g. "
    "\"101.1\\t<en>\\t<de>\". No prose, no headers, no explanations. Use a "
    "comma as the decimal separator."
)


class VLMBackend:
    """Local GPU vision-LLM doing constrained per-region reads only."""

    def __init__(self, model_id: Optional[str] = None, max_new_tokens: int = 40):
        # Imported lazily so the default (CPU) image needs no torch.
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.torch = torch
        self.max_new_tokens = max_new_tokens
        model_id = model_id or os.getenv("VLM_MODEL_ID", _DEFAULT_MODEL)
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto"
        )
        self.model.eval()

    def read_region(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.9 if text else 0.0)

    def read_region_gdt(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _GDT_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.9 if text else 0.0)

    def read_notes_block(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _NOTES_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=512, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.9 if text else 0.0)

    def detect_regions(self, image: Image.Image):
        from app.pipeline.detect import parse_detections
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": _DETECT_PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=1024, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return parse_detections(text)
