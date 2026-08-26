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

# Detection prompt: the model LOCATES every inspection callout in the tile and
# returns JSON only. It does not need to transcribe accurately — Stage 2 re-reads
# each crop with read_region. kind is coarse; box is in pixels of THIS image.
_DETECT_PROMPT = (
    "This image is a tile cropped from a mechanical engineering drawing. Find "
    "EVERY inspection callout: linear/diameter/radius dimensions with their "
    "tolerances, GD&T feature-control frames, surface-finish symbols, numbered "
    "notes, and material/process specifications. Return ONLY a JSON array, no "
    "prose. Each element: {\"box\":[x0,y0,x1,y1],\"kind\":\"dimension|gdt|"
    "surface|note|material\"}. box is pixel coordinates within this image. If "
    "there are no callouts, return []."
)

# GD&T read prompt: the crop is the INNER content of a feature-control frame
# (border already stripped). Transcribe symbol, tolerance value and datum(s)
# on one line, e.g. "⊕ Ø0.1 A". The parser maps this to 0 / zone / 0.
_GDT_PROMPT = (
    "This image is the inner content of a GD&T feature control frame from a "
    "mechanical drawing, with the surrounding box border removed. Transcribe it "
    "on one line as: <symbol> <tolerance value> <datum letters>, e.g. "
    "'⊕ Ø0.1 A' or '⏥ 0,05'. Use a comma as the decimal separator. Preserve the "
    "geometric symbol and any Ø. Output nothing else, no explanation."
)


# Notes-block read prompt: the crop is the general-notes table from the
# drawing. Each row begins with a 3-digit number (101…); some rows contain
# inline numbered sub-bullets (1., 2., …). The model returns tab-separated
# triples so the parser can align EN and DE columns in one pass.
_NOTES_PROMPT = (
    "This image is the general-notes / mark-legend table from a mechanical "
    "engineering drawing. Each row starts with a 3-digit number (101, 102, …) "
    "and contains an English text and a German text; a single cell may span "
    "several lines. Return ONLY a JSON array, one object per row, of the form "
    '[{"pos": 101, "en": "<english>", "de": "<german>"}]. For an inline '
    'numbered sub-bullet, add "sub": <n>. If a row has no German text use an '
    "empty string. Use a comma as the decimal separator. No prose, no code "
    "fences, no trailing text."
)


# Title-block cell read prompt: the crop is ONE cell of the bottom-right title
# block (Schriftfeld). Each cell holds a small bilingual caption ("English /
# German") and a prominent value; the caption may sit above OR below the value.
# Returns a JSON object so the parser can split caption from value in one pass.
_TITLE_PROMPT = (
    "This image is a single cell cropped from the title block (Schriftfeld) of "
    "a mechanical engineering drawing. The cell contains a small printed caption "
    "(a bilingual label in the form 'English / German', e.g. 'Sheet / Blatt' or "
    "'Released / Freigabe') together with a prominent value. The caption may "
    "appear ABOVE or BELOW the value. Return ONLY a JSON object "
    "{\"label\": \"<caption as printed>\", \"value\": \"<the value>\"}. If the "
    "cell has only a value and no caption, use an empty label. Use a comma as the "
    "decimal separator. No prose, no explanation, no code fences."
)


# A read crop wider/taller than this many pixels is downscaled before it reaches
# the model. A full legend crop (~2890x1436 px at 300 dpi) otherwise OOMs the
# vision encoder — the CUDA allocator aborts mid-generate, the read wrapper
# swallows it to "", and the whole notes/marks table comes back empty. Confirmed
# by a downscale sweep: native crashes; <=2000 px reads cleanly. 1600 sits below
# the crash and below one detection tile (1280 sq) in pixel count, and stays
# above the point where the model starts splitting each text line into its own
# row. Small callout/title crops are already well under this, so unaffected.
_MAX_READ_LONG_EDGE = 1600


# Prompt variants, selected per run by environment variable. Registries rather
# than edited constants for three reasons, each one paid for:
#   * two arms whose only difference is a source constant cannot run
#     concurrently — run_experiment_gpu.sh keeps one checkout on the GPU host,
#     so a code-level prompt edit serialises the campaign at ~9 h per arm.
#   * `prompt_sha256` proves that A prompt changed, never WHICH. The variant
#     name reaches RunConfig.extra, so the digest says so out loud.
#   * an unknown name raises instead of falling back, so a typo loses the arm
#     rather than producing a control run wearing a treatment arm's run name.
# "base" must stay byte-identical to the constants above: it is what every
# report from the Rung-0 baseline onward was produced with, and
# tests/test_vlm_prompt.py pins the resulting hash at aa7659f1929184ea.
# Arm `readcenter`. Hypothesis from the Phase A cross-tab: 64 of 144 misread
# rows sit on MISPLACED pairs, and 70 of the 80 misplaced pairs are wrong (87.5%)
# — the signature of transcribing a neighbouring callout correctly and being
# scored as a perception failure. The base prompt says "ONLY the dimension" for a
# crop that may hold parts of several, and never says which one to read. Targets
# error_cause_crosstab.misread.misplaced.
_PROMPT_CENTER = (
    "This image is a crop from a mechanical engineering drawing containing one "
    "dimension callout at its centre, and possibly parts of neighbouring "
    "callouts near the edges. Transcribe ONLY the callout closest to the "
    "centre of the image, as plain text on one line, e.g. '1,2 +0,1 -0,1' or "
    "'Ø7 +0,1 -0,1' or 'R0,5 MAX'. Ignore any other callout, even a complete "
    "one, and never merge two callouts into one line. Use a comma as the "
    "decimal separator. Preserve the symbols Ø, R and ±. Ignore leader lines, "
    "dimension lines and arrowheads. If there is no dimension text at the "
    "centre, output nothing. No explanation."
)

# Arm `detectbox`. Hypothesis from the Phase A signatures: 49 rows (25% of the
# 196 wrong rows) have ALL FOUR fields wrong and 42% have the whole value wrong.
# A box that clips the tolerance off a callout, or swallows a neighbour, produces
# exactly that — every field wrong at once — because the read crop is derived
# from it. The base prompt says to find callouts but never says what the box must
# enclose. Targets fields:char_type+nominal+upper_tol+lower_tol.
#
# Riskiest arm in the campaign: it changes the crop for every downstream read,
# which is how finetiles did its damage (-0.0662 field_acc). The kind vocabulary
# is unchanged on purpose — parse_detections validates against detect._KINDS, so
# renaming a kind here would silently discard detections rather than improve
# them.
_DETECT_PROMPT_BOX = (
    "This image is a tile cropped from a mechanical engineering drawing. Find "
    "EVERY inspection callout: linear/diameter/radius dimensions with their "
    "tolerances, GD&T feature-control frames, surface-finish symbols, numbered "
    "notes, and material/process specifications. Each box must enclose the "
    "COMPLETE callout — the nominal value together with its tolerance text, "
    "prefix symbol (Ø, R, ±) and any MAX/MIN qualifier — and nothing else: do "
    "not include a neighbouring callout, and do not cut a tolerance off. Give "
    "one box per callout, not one per line of text. Return ONLY a JSON array, "
    "no prose. Each element: {\"box\":[x0,y0,x1,y1],\"kind\":\"dimension|gdt|"
    "surface|note|material\"}. box is pixel coordinates within this image. If "
    "there are no callouts, return []."
)

_READ_VARIANTS = {"base": _PROMPT, "center": _PROMPT_CENTER}
_DETECT_VARIANTS = {"base": _DETECT_PROMPT, "box": _DETECT_PROMPT_BOX}


def _select(variants, env_key, env):
    name = (os.environ if env is None else env).get(env_key, "base")
    if name not in variants:
        raise ValueError(
            f"{env_key}={name!r} is not a known prompt variant "
            f"(have: {sorted(variants)}). Refusing to fall back to 'base': a "
            f"silent fallback turns a treatment arm into a control arm wearing "
            f"a treatment arm's run name.")
    return name


def read_prompt(env=None) -> str:
    """The per-callout read prompt in effect for this run."""
    return _READ_VARIANTS[_select(_READ_VARIANTS, "SINDRI_READ_PROMPT", env)]


def detect_prompt(env=None) -> str:
    """The tile detection prompt in effect for this run."""
    return _DETECT_VARIANTS[_select(_DETECT_VARIANTS, "SINDRI_DETECT_PROMPT",
                                    env)]


def active_prompts(env=None) -> dict:
    """Variant names in effect, for RunConfig.extra — the same role
    detect.active_knobs plays for the detection knobs."""
    return {"read_prompt": _select(_READ_VARIANTS, "SINDRI_READ_PROMPT", env),
            "detect_prompt": _select(_DETECT_VARIANTS, "SINDRI_DETECT_PROMPT",
                                     env)}


def effective_prompts(env=None) -> list:
    """The five prompts this run will actually send, in the order
    runner._prompt_sha256 hashes them. With no variant selected this is exactly
    [_PROMPT, _DETECT_PROMPT, _GDT_PROMPT, _NOTES_PROMPT, _TITLE_PROMPT], so the
    default hash is unchanged."""
    return [read_prompt(env), detect_prompt(env), _GDT_PROMPT, _NOTES_PROMPT,
            _TITLE_PROMPT]


def _cap_long_edge(image: Image.Image, max_long_edge: int = _MAX_READ_LONG_EDGE) -> Image.Image:
    """Downscale `image` so its longest side is at most `max_long_edge`, keeping
    aspect ratio. Returns the image unchanged when already within bounds."""
    w, h = image.size
    longest = max(w, h)
    if longest <= max_long_edge:
        return image
    s = max_long_edge / longest
    return image.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)


def _mean_token_confidence(step_probs) -> float:
    """Mean of per-token max-softmax probabilities; 0.0 for an empty sequence."""
    probs = list(step_probs)
    return float(sum(probs) / len(probs)) if probs else 0.0


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
        # AWQ's Triton dequant kernel only supports float16: mixing its int32
        # unpacked weights with bfloat16 scales fails to compile. Qwen2.5-VL's
        # config defaults to bfloat16, so torch_dtype="auto" picks the
        # unsupported dtype for AWQ checkpoints; force float16 instead.
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_id, torch_dtype=torch.float16, device_map="auto"
        )
        self.model.eval()

    def _generate_text(self, prompt: str, image: Image.Image,
                       max_new_tokens: int):
        """Run one constrained generation and return (text, confidence). The
        confidence is the mean per-token top-softmax probability of the greedy
        decode, so an uncertain read scores low and can be flagged for review."""
        messages = [{"role": "user", "content": [
            {"type": "image", "image": _cap_long_edge(image.convert("RGB"))},
            {"type": "text", "text": prompt},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=False,
                output_scores=True, return_dict_in_generate=True,
            )
        seq = out.sequences[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(seq, skip_special_tokens=True).strip()
        step_probs = [float(self.torch.softmax(s[0], dim=-1).max())
                      for s in out.scores]
        conf = _mean_token_confidence(step_probs) if text else 0.0
        return text, conf

    def read_region(self, image: Image.Image) -> OcrResult:
        text, conf = self._generate_text(read_prompt(), image,
                                         self.max_new_tokens)
        return OcrResult(text=text, confidence=conf)

    def read_region_gdt(self, image: Image.Image) -> OcrResult:
        text, conf = self._generate_text(_GDT_PROMPT, image, self.max_new_tokens)
        return OcrResult(text=text, confidence=conf)

    def read_notes_block(self, image: Image.Image) -> OcrResult:
        text, conf = self._generate_text(_NOTES_PROMPT, image, 512)
        return OcrResult(text=text, confidence=conf)

    def read_title_cell(self, image: Image.Image) -> OcrResult:
        text, conf = self._generate_text(_TITLE_PROMPT, image, 128)
        return OcrResult(text=text, confidence=conf)

    def detect_regions(self, image: Image.Image):
        from app.pipeline.detect import parse_detections
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image.convert("RGB")},
            {"type": "text", "text": detect_prompt()},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=1024, do_sample=False,
            )
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        text = self.processor.decode(trimmed, skip_special_tokens=True).strip()
        return parse_detections(text)
