import os
import sys
import traceback
from app.pipeline.ocr.base import OCRBackend, OcrResult
from app.pipeline.ocr.tesseract_backend import TesseractBackend


def _log(msg: str) -> None:
    print(f"[sindri.ocr] {msg}", file=sys.stderr, flush=True)


def _gpu_available() -> bool:
    try:
        import torch
        avail = torch.cuda.is_available()
        _log(f"torch.cuda.is_available() = {avail}")
        return avail
    except Exception as e:
        _log(f"torch unavailable: {e!r}")
        return False


def get_backend() -> OCRBackend:
    """Select the OCR backend from OCR_BACKEND. Falls back to Tesseract, but
    never *silently*: the reason for any fallback is logged to stderr."""
    choice = os.getenv("OCR_BACKEND", "tesseract").lower()
    if choice == "vlm":
        if _gpu_available():
            try:
                from app.pipeline.ocr.vlm_backend import VLMBackend
                backend = VLMBackend()
                _log("active backend: VLM")
                return backend
            except Exception as e:
                traceback.print_exc()
                _log(f"VLM backend FAILED to load ({e!r}); falling back to Tesseract")
        else:
            _log("OCR_BACKEND=vlm but no CUDA GPU detected; falling back to Tesseract")
    _log("active backend: Tesseract")
    return TesseractBackend()


def backend_status() -> dict:
    """Lightweight introspection for a health endpoint (no model load)."""
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
    except Exception:
        cuda = False
    return {"ocr_backend_requested": os.getenv("OCR_BACKEND", "tesseract"), "cuda": cuda}


__all__ = ["OCRBackend", "OcrResult", "get_backend", "backend_status", "TesseractBackend"]
