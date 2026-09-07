"""Which weights each pass generates through.

Rung 3's first arm (`lora72bnf4`) was void as a read-stage measurement:
`resolve_adapter` wraps the WHOLE model in `PeftModel`, so `detect_regions`
generated through a read-trained adapter too. False detections went 607 -> 931
(`dimension` +202, `gdt` +67, `theoretical` +52, `note` +43) and the +648 review
cost that bought swamped the -410 the recovered misses earned. These tests pin
the scoping that makes the adapter's effect on READING measurable at all.

The doubles below are duck-typed rather than built on peft or torch on purpose:
neither is installed outside the GPU image, and the same reasoning already keeps
`app.train.dataset` importable on the operator's machine.
"""

import contextlib

import pytest
from PIL import Image

from app.pipeline.ocr import vlm_backend


class _Ids:
    """A token-id batch: the backend only ever asks for its prompt length."""
    shape = (1, 2)


class _Inputs(dict):
    """What the processor returns: a mapping that also answers `.to(device)`."""

    def to(self, device):
        return self


class _Row(list):
    """One decoding step's probability row, indexable and with a max()."""

    def max(self):
        return 0.9


class _Generated(list):
    """Stands in for both `generate()` return shapes at once: `detect_regions`
    indexes the batch directly, the read path reads `.sequences` / `.scores`."""

    def __init__(self):
        super().__init__([[7, 7, 11]])
        self.sequences = self
        self.scores = [[_Row([0.9])]]


class _Torch:
    """The two torch entry points the backend touches."""

    @staticmethod
    @contextlib.contextmanager
    def inference_mode():
        yield

    @staticmethod
    def softmax(row, dim=-1):
        return row


class _Processor:
    def __init__(self, text="[]"):
        self.text = text

    def apply_chat_template(self, messages, **kwargs):
        return _Inputs(input_ids=_Ids())

    def decode(self, seq, skip_special_tokens=True):
        return self.text


class _BaseModel:
    """A plain transformers model: no adapter, and so no `disable_adapter` to
    call. Every AWQ measurement in the campaign generates through this shape."""

    device = "cpu"

    def __init__(self):
        self.adapter_active = False
        # Whether the adapter was live at each generate(), in call order. The
        # per-call record is the point: a flag read afterwards cannot tell a
        # correctly scoped run from one that disabled the adapter everywhere.
        self.adapter_at_generate = []

    def generate(self, **kwargs):
        self.adapter_at_generate.append(self.adapter_active)
        return _Generated()


class _AdaptedModel(_BaseModel):
    """What `PeftModel.from_pretrained` returns: the adapter is live, and
    `disable_adapter()` is a context manager that suspends it."""

    def __init__(self):
        super().__init__()
        self.adapter_active = True

    @contextlib.contextmanager
    def disable_adapter(self):
        self.adapter_active = False
        try:
            yield
        finally:
            self.adapter_active = True


def _backend(model, adapter="read-lora-v1"):
    """A VLMBackend around a double. `__init__` loads 145 GB of weights, so the
    attributes it sets are constructed here directly."""
    b = object.__new__(vlm_backend.VLMBackend)
    b.torch = _Torch
    b.processor = _Processor()
    b.model = model
    b.max_new_tokens = 40
    b.adapter = adapter
    return b


def _page():
    return Image.new("RGB", (64, 64), "white")


def test_detection_runs_on_unadapted_weights():
    """The scoping this exists for. `read-lora-v1` is trained on callout read
    crops; detection must see exactly the weights its control ran on, or the
    arm measures the adapter's effect on two stages at once and the read
    stage's contribution cannot be recovered from the total."""
    model = _AdaptedModel()
    _backend(model).detect_regions(_page())
    assert model.adapter_at_generate == [False]


def test_reads_keep_the_adapter_live():
    """The other half: scoping must not disable the adapter everywhere, which
    would serve the base model under a treatment arm's run name -- the failure
    `resolve_adapter` already refuses to allow."""
    model = _AdaptedModel()
    _backend(model).read_region(_page())
    assert model.adapter_at_generate == [True]


def test_the_adapter_is_restored_after_detection():
    """One model serves both passes and `extract()` interleaves them per
    document -- detect the page, then read every region it found. A disable
    that leaked would quietly turn the rest of the document into a base run."""
    model = _AdaptedModel()
    b = _backend(model)
    b.detect_regions(_page())
    b.read_region(_page())
    assert model.adapter_at_generate == [False, True]


def test_detection_on_the_base_model_needs_no_adapter_context():
    """With no adapter selected the model is a plain module with no
    `disable_adapter` at all. That is the production path, and asking it to
    suspend an adapter it never loaded must not raise."""
    model = _BaseModel()
    dets = _backend(model, adapter=None).detect_regions(_page())
    assert model.adapter_at_generate == [False]
    assert dets == []


def test_an_adapter_that_cannot_be_disabled_fails_loudly():
    """If PEFT renames `disable_adapter`, generating anyway would run detection
    through the read adapter and void the arm -- and n_pred would only reveal it
    ~20 GPU-hours later. Same rule as `resolve_adapter`: refuse, loudly, rather
    than produce a number that reads as a measurement."""
    b = _backend(_BaseModel(), adapter="read-lora-v1")
    with pytest.raises(RuntimeError, match="disable_adapter"):
        b.detect_regions(_page())
