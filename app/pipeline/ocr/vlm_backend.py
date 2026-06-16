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
