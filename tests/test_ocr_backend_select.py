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


# --- OCR_BACKEND=vllm: route B's serving stack ------------------------------
#
# Route B serves the same base and the same adapter as the transformers path but
# through vLLM, so selection has to tell them apart on one character. The
# fallback machinery above is shared deliberately: both are GPU backends on a
# contended host, and both fail the same way when another job holds the card.

def test_vllm_is_selected_by_its_own_name(monkeypatch):
    """`vlm` and `vllm` differ by one character and mean different serving
    stacks, recorded separately by runner._serving_backend(). Matching loosely
    -- a prefix or a `startswith` -- would run route B under route A's run name
    or the reverse, and the dumps would be indistinguishable."""
    monkeypatch.setenv("OCR_BACKEND", "vllm")
    monkeypatch.setattr(ocr, "_gpu_available", lambda: True)
    monkeypatch.setattr(ocr, "_vllm_factory", lambda: "VLLM-BACKEND")
    monkeypatch.setattr(ocr, "_vlm_factory",
                        lambda: pytest.fail("route B must not build VLMBackend"))

    assert get_backend() == "VLLM-BACKEND"
    assert get_vlm_fallback_reason() is None


def test_the_transformers_path_is_undisturbed(monkeypatch):
    """Every committed measurement in the campaign was predicted on this path,
    and route B is an experiment against them. Adding a backend must not change
    which one OCR_BACKEND=vlm builds."""
    monkeypatch.setenv("OCR_BACKEND", "vlm")
    monkeypatch.setattr(ocr, "_gpu_available", lambda: True)
    monkeypatch.setattr(ocr, "_vlm_factory", lambda: "VLM-BACKEND")
    monkeypatch.setattr(ocr, "_vllm_factory",
                        lambda: pytest.fail("route A must not build VLLMBackend"))

    assert get_backend() == "VLM-BACKEND"


def test_an_unknown_backend_still_falls_back_to_tesseract(monkeypatch):
    """The default. Only the two names above are GPU backends."""
    monkeypatch.setenv("OCR_BACKEND", "vllm-ish")
    assert isinstance(get_backend(), TesseractBackend)


def test_a_vllm_load_failure_names_the_backend_that_was_requested(monkeypatch):
    """The fallback reason is folded into the auto-balloon error and the health
    endpoint. On a host where both serving stacks are plausible, a reason that
    said "vlm" for a vLLM run would send the reader to the wrong code."""
    monkeypatch.setenv("OCR_BACKEND", "vllm")
    monkeypatch.setattr(ocr, "_gpu_available", lambda: True)

    def boom():
        raise RuntimeError("EngineCore failed to start")
    monkeypatch.setattr(ocr, "_vllm_factory", boom)
    monkeypatch.setattr(ocr.time, "sleep", lambda d: None)

    assert isinstance(get_backend(), TesseractBackend)
    reason = get_vlm_fallback_reason()
    assert "OCR_BACKEND=vllm" in reason
    assert "EngineCore failed to start" in reason


def test_selecting_a_backend_imports_no_vllm():
    """Hard constraint, not a nicety: the CPU image has no vllm, and
    app.pipeline.ocr is imported by the API process on every host. A
    module-scope `import vllm` anywhere under it would make the whole package
    unimportable off the GPU box -- which is also why every double in
    tests/test_vllm_backend.py is duck-typed."""
    import sys

    import app.pipeline.ocr.vllm_backend  # noqa: F401
    assert "vllm" not in sys.modules


# --- OCR_BACKEND=hybrid: two checkpoints, one pipeline ----------------------
#
# The hybrid arm (handoff 2026-09-09 §3): the 32B localises, the 72B
# transcribes. Selection has to name it exactly like the other two, because
# runner._serving_backend() records the choice and a run served by the wrong
# stack is a complete, plausible, wrong measurement.

def test_hybrid_is_selected_by_its_own_name(monkeypatch):
    """`vlm`, `vllm` and `hybrid` are three serving stacks. A loose match would
    run one under another's run name, and RunConfig would record the lie."""
    monkeypatch.setenv("OCR_BACKEND", "hybrid")
    monkeypatch.setattr(ocr, "_gpu_available", lambda: True)
    monkeypatch.setattr(ocr, "_hybrid_factory", lambda: "HYBRID-BACKEND")
    monkeypatch.setattr(ocr, "_vlm_factory",
                        lambda: pytest.fail("the hybrid must not build a single VLMBackend"))
    monkeypatch.setattr(ocr, "_vllm_factory",
                        lambda: pytest.fail("the hybrid is not route B"))

    assert get_backend() == "HYBRID-BACKEND"
    assert get_vlm_fallback_reason() is None


def test_a_hybrid_that_cannot_load_names_itself_in_the_fallback_reason(monkeypatch):
    """Two checkpoints need two free cards, and this host has 24+ other users.
    The reason has to name `hybrid` so the reader goes to the right code and the
    right card: a message saying "the VLM" would send them to the single-model
    path, which is not what failed."""
    monkeypatch.setenv("OCR_BACKEND", "hybrid")
    monkeypatch.setattr(ocr, "_gpu_available", lambda: True)
    monkeypatch.setattr(ocr.time, "sleep", lambda d: None)

    def boom():
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(ocr, "_hybrid_factory", boom)

    assert isinstance(get_backend(), TesseractBackend)
    assert "OCR_BACKEND=hybrid" in get_vlm_fallback_reason()
