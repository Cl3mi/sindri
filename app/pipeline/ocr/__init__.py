import os
import sys
import time
import traceback
from app.pipeline.ocr.base import OCRBackend, OcrResult
from app.pipeline.ocr.tesseract_backend import TesseractBackend

# Why the last get_backend() fell back to Tesseract despite OCR_BACKEND=vlm, or
# None if it did not fall back. Surfaced via get_vlm_fallback_reason() /
# backend_status() and folded into the auto-balloon error so a VRAM-contention
# failure reads as an actionable message instead of a bare capability error.
_fallback_reason = None


def _log(msg: str) -> None:
    print(f"[sindri.ocr] {msg}", file=sys.stderr, flush=True)


def get_vlm_fallback_reason():
    """Reason the VLM backend was unavailable on the last get_backend(), or None."""
    return _fallback_reason


def _vlm_factory() -> OCRBackend:
    """Construct the VLM backend. Isolated so the load can be retried and so
    tests can substitute a failing factory without importing torch."""
    from app.pipeline.ocr.vlm_backend import VLMBackend
    return VLMBackend()


def _load_vlm_with_retry(factory, attempts: int = None, delay: float = None,
                         sleep=None):
    """Call `factory` up to `attempts` times, sleeping `delay` seconds between
    tries, and return the first success. VLM load failures on this HPC host are
    usually transient GPU-VRAM contention (another job holding the card), so a
    short retry often recovers. Re-raises the last error if all attempts fail.
    attempts/delay default from VLM_LOAD_RETRIES / VLM_LOAD_RETRY_DELAY."""
    if attempts is None:
        try:
            attempts = int(os.getenv("VLM_LOAD_RETRIES", "3"))
        except ValueError:
            attempts = 3
    if delay is None:
        try:
            delay = float(os.getenv("VLM_LOAD_RETRY_DELAY", "8"))
        except ValueError:
            delay = 8.0
    if sleep is None:
        sleep = time.sleep      # resolved at call time so it stays monkeypatchable
    attempts = max(1, attempts)
    last = None
    for i in range(attempts):
        try:
            return factory()
        except Exception as e:
            last = e
            _log(f"VLM load attempt {i + 1}/{attempts} failed: {e!r}")
            if i < attempts - 1:
                sleep(delay)
    raise last


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
    never *silently*: any fallback records `_fallback_reason` (logged, exposed on
    the health endpoint, and folded into the auto-balloon error) and the VLM load
    is retried first to ride out transient GPU-VRAM contention."""
    global _fallback_reason
    _fallback_reason = None
    choice = os.getenv("OCR_BACKEND", "tesseract").lower()
    if choice != "vlm":
        _log("active backend: Tesseract")
        return TesseractBackend()

    if not _gpu_available():
        _fallback_reason = ("OCR_BACKEND=vlm requested but no CUDA GPU was "
                            "detected")
        _log(_fallback_reason + "; falling back to Tesseract")
        return TesseractBackend()

    try:
        backend = _load_vlm_with_retry(_vlm_factory)
        _log("active backend: VLM")
        return backend
    except Exception as e:
        traceback.print_exc()
        _fallback_reason = (
            "OCR_BACKEND=vlm requested but the VLM backend failed to load "
            f"({e!r}) — the GPU is likely out of memory (another job holding "
            "the card). Free the GPU or lower VLM_MODEL_ID, then retry.")
        _log(_fallback_reason + "; falling back to Tesseract")
        return TesseractBackend()


def backend_status() -> dict:
    """Lightweight introspection for a health endpoint (no model load)."""
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
    except Exception:
        cuda = False
    return {"ocr_backend_requested": os.getenv("OCR_BACKEND", "tesseract"),
            "cuda": cuda, "vlm_fallback_reason": _fallback_reason}


__all__ = ["OCRBackend", "OcrResult", "get_backend", "backend_status",
           "get_vlm_fallback_reason", "TesseractBackend"]
