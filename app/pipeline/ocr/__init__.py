import os
from app.pipeline.ocr.base import OCRBackend, OcrResult
from app.pipeline.ocr.tesseract_backend import TesseractBackend


def _gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def get_backend() -> OCRBackend:
    choice = os.getenv("OCR_BACKEND", "tesseract").lower()
    if choice == "vlm" and _gpu_available():
        try:
            from app.pipeline.ocr.vlm_backend import VLMBackend
            return VLMBackend()
        except Exception:
            pass  # fall through to tesseract
    return TesseractBackend()


__all__ = ["OCRBackend", "OcrResult", "get_backend", "TesseractBackend"]
