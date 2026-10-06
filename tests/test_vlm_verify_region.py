"""VLMBackend.verify_region on a fake model: one decoding step, P(yes) from the
softmax over the "yes"/"Yes" and "no"/"No" token ids
(docs/plans/2026-10-07-verifier-registration.md §2). The 72B itself only runs
on the GPU host; what is tested here is everything around the forward pass."""
import pytest
import torch
from PIL import Image

from app.pipeline.ocr.vlm_backend import VLMBackend
from app.pipeline.verifier import VERIFY_PROMPT

VOCAB = {"yes": 1, "Yes": 2, "no": 3, "No": 4, "maybe": 5}


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        return [VOCAB[text]] if text in VOCAB else [7, 8]


class FakeInputs(dict):
    def to(self, device):
        return self


class FakeProcessor:
    tokenizer = FakeTokenizer()

    def __init__(self):
        self.prompts = []

    def apply_chat_template(self, messages, **kw):
        self.prompts.append(messages[0]["content"][1]["text"])
        return FakeInputs(input_ids=torch.zeros((1, 3), dtype=torch.long))


class FakeOut:
    def __init__(self, logits):
        self.scores = (logits.unsqueeze(0),)
        self.sequences = torch.zeros((1, 4), dtype=torch.long)


class FakeModel:
    device = "cpu"

    def __init__(self, logits):
        self.logits = logits
        self.kwargs = None

    def generate(self, **kw):
        self.kwargs = kw
        return FakeOut(self.logits)


def _backend(logits):
    b = object.__new__(VLMBackend)
    b.torch = torch
    b.processor = FakeProcessor()
    b.model = FakeModel(logits)
    b.adapter = None
    return b


def _logits(**probs):
    """Logits whose softmax puts exactly `probs` on the named tokens and the
    rest on token 0."""
    p = torch.zeros(10)
    for word, v in probs.items():
        p[VOCAB[word]] = v
    p[0] = 1.0 - float(p.sum())
    return torch.log(p.clamp_min(1e-12))


def test_probability_is_yes_over_yes_plus_no():
    b = _backend(_logits(yes=0.5, Yes=0.1, no=0.2, No=0.0))
    assert b.verify_region(Image.new("RGB", (300, 200))) == \
        pytest.approx(0.6 / 0.8, rel=1e-4)


def test_one_greedy_step_with_the_registered_prompt():
    b = _backend(_logits(yes=0.9, no=0.1))
    b.verify_region(Image.new("RGB", (300, 200)))
    assert b.processor.prompts == [VERIFY_PROMPT]
    assert b.model.kwargs["max_new_tokens"] == 1
    assert b.model.kwargs["do_sample"] is False
    assert b.model.kwargs["output_scores"] is True


def test_no_answer_is_none():
    b = _backend(_logits(maybe=1.0))
    assert b.verify_region(Image.new("RGB", (300, 200))) is None


def test_a_non_finite_logits_row_is_none():
    """float16 AWQ can produce a degenerate logits row whose softmax is NaN
    (the r3-awqtest incident). It must come back as no verdict, never as a
    number."""
    b = _backend(torch.full((10,), float("nan")))
    assert b.verify_region(Image.new("RGB", (300, 200))) is None
