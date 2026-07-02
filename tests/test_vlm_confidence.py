from app.pipeline.ocr.vlm_backend import _mean_token_confidence


def test_mean_token_confidence_high_for_confident_scores():
    # per-step max-softmax probabilities
    assert _mean_token_confidence([0.99, 0.98, 0.97]) > 0.9


def test_mean_token_confidence_low_for_uncertain_scores():
    assert _mean_token_confidence([0.4, 0.5, 0.3]) < 0.6


def test_mean_token_confidence_empty_is_zero():
    assert _mean_token_confidence([]) == 0.0
