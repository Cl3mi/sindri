from PIL import Image
from app.pipeline.ocr.base import OcrResult

_PROMPT = (
    "You are reading one dimension callout from a mechanical drawing. "
    "Return ONLY the exact characters you see, no explanation. "
    "Preserve symbols like Ø, R, ± and comma decimals."
)


class VLMBackend:
    """Local GPU vision-LLM doing constrained per-region reads only."""

    def __init__(self, model_id: str = "Qwen/Qwen2.5-VL-7B-Instruct"):
        # Imported lazily so the default image needs no torch.
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto"
        )

    def read_region(self, image: Image.Image) -> OcrResult:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": _PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        out = self.model.generate(**inputs, max_new_tokens=40, do_sample=False)
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return OcrResult(text=text, confidence=0.85)
