"""Backend selection robustness: retrying a transient VLM load failure and
surfacing an actionable reason instead of silently degrading to Tesseract."""
import pytest

import app.pipeline.ocr as ocr
from app.pipeline.ocr import (
    _load_vlm_with_retry, get_backend, get_vlm_fallback_reason,
    backend_status, TesseractBackend,
)


def test_load_vlm_retries_until_success():
    calls = {"n": 0}
    slept = []

    def factory():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("CUDA out of memory")
        return "BACKEND"

    result = _load_vlm_with_retry(factory, attempts=3, delay=5, sleep=slept.append)
    assert result == "BACKEND"
    assert calls["n"] == 3
    assert slept == [5, 5]      # slept between the three attempts, not after the last


def test_load_vlm_reraises_after_exhausting_attempts():
    def factory():
        raise RuntimeError("no gpu memory")

    with pytest.raises(RuntimeError, match="no gpu memory"):
        _load_vlm_with_retry(factory, attempts=2, delay=0, sleep=lambda d: None)


def test_get_backend_records_fallback_reason_on_vlm_failure(monkeypatch):
    monkeypatch.setenv("OCR_BACKEND", "vlm")
    monkeypatch.setattr(ocr, "_gpu_available", lambda: True)

    def boom():
        raise RuntimeError("device_map contains a CPU device")
    monkeypatch.setattr(ocr, "_vlm_factory", boom)
    # no real sleeping between retries
    monkeypatch.setattr(ocr.time, "sleep", lambda d: None)

    backend = get_backend()
    assert isinstance(backend, TesseractBackend)
    reason = get_vlm_fallback_reason()
    assert reason and "device_map contains a CPU device" in reason
    assert backend_status()["vlm_fallback_reason"] == reason


def test_get_backend_clears_reason_when_not_requesting_vlm(monkeypatch):
    monkeypatch.setenv("OCR_BACKEND", "tesseract")
    get_backend()
    assert get_vlm_fallback_reason() is None
