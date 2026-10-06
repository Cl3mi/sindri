import os
import re
import uuid
from pathlib import Path
from typing import Tuple
from PIL import Image
from app.models import Characteristic, ExtractionResult, NoteBlock
from app.pipeline.render import render_page
from app.pipeline.detect import detect_characteristics
from app.pipeline import boxes as bx
from app.pipeline.place import number_characteristics, place_balloons
from app.pipeline.parser import parse_value
from app.pipeline.ocr import get_backend
from app.pipeline.review import review_flags
from app.pipeline import policy_rules as pr
from app.pipeline import notes_block as nb
from app.pipeline import marks_block as mb
from app.pipeline import title_block as tb

# detector kind -> parser hint
_HINTS = {"material": "material", "note": "note", "gdt": "gdt",
          "theoretical": "theoretical"}

# A bare 100-series integer in a box is a note-reference, not a dimension.
_NOTE_REF_RE = re.compile(r"^\s*(10[0-9]|1[1-9][0-9])\s*$")

# how close the two rotation candidates must score to count as ambiguous
ROTATION_EPS = 0.15


def _safe_read(reader, crop) -> Tuple[str, float]:
    try:
        result = reader(crop)
        return result.text, result.confidence
    except Exception:
        return "", 0.0


def _score(text: str, conf: float) -> float:
    c = parse_value(text)
    return (1.0 if c.nominal else 0.0) + (0.5 if c.upper_tol else 0.0) + conf


def _best_read(backend, crop: Image.Image, vertical: bool) -> Tuple[str, float, bool]:
    candidates = [crop]
    if vertical:
        candidates = [crop.rotate(-90, expand=True), crop.rotate(90, expand=True)]
    scored = []
    for im in candidates:
        text, conf = _safe_read(backend.read_region, im)
        scored.append((_score(text, conf), text, conf))
    scored.sort(key=lambda t: -t[0])
    best_score, best_text, best_conf = scored[0]
    # An empty winning read is not "rotation-ambiguous" — both orientations
    # simply read nothing. Only flag ambiguity when a real read was produced and
    # the runner-up scores within EPS of it.
    ambiguous = (len(scored) >= 2 and bool((best_text or "").strip())
                 and (best_score - scored[1][0]) < ROTATION_EPS)
    return best_text, best_conf, ambiguous


def _clamp(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))


# Read-crop normalization: give the reader a little context around the (tight)
# box and upscale small crops so faint sub-mm text and stacked tolerances read
# consistently instead of "sometimes". Frame-stripped CV inner boxes are read
# with pad=0 (padding would re-introduce the border the crop deliberately removed).
_CROP_PAD = 24          # px of context added around a VLM read box
_MIN_CROP_H = 40        # upscale crops shorter than this…
_MAX_UPSCALE = 3.0      # …but never by more than this factor


# The three constants above are the DEFAULTS, and they are what the frozen
# baseline, r3-awqcontrol, every arm in the campaign and read-lora-v1's training
# crops were produced with. They became selectable because r3-hybrid measured
# the crop, not the reader, as the dominant term in read accuracy: holding the
# reader and changing the boxes moved field_acc -0.206, against +0.013 for
# holding the boxes and changing the reader.
#
# Env-selected rather than edited in source, for the three reasons the prompt
# registry gives: two arms differing only in a constant cannot run concurrently
# on the single checkout the GPU host keeps; the value has to reach
# RunConfig.extra or _reusable_dump skips every document as "already predicted"
# across the change being measured; and a bad value must lose the arm rather
# than quietly produce a control wearing the arm's run name.
# What every dump predicted BEFORE 2026-09-16 was produced with, frozen. The
# recording below compares against THIS, not against the defaults above, and the
# distinction is load-bearing: comparing against the current default would make
# a run at pad 24 record nothing and therefore look identical in RunConfig to
# every pad-6 dump on disk -- so _reusable_dump would reuse them straight across
# the change being measured. Pinning SINDRI_CROP_PAD=6 still reproduces those
# dumps byte-for-byte, config included, which is what makes an old arm
# re-runnable.
_BASELINE_CROP_KNOBS = (6, 40, 3.0)

_CROP_KNOBS = (
    ("crop_pad", "SINDRI_CROP_PAD", _CROP_PAD, int, 0, None),
    ("crop_min_h", "SINDRI_CROP_MIN_H", _MIN_CROP_H, int, 0, None),
    # Below 1.0 this would DOWNscale, inverting the knob's meaning while still
    # looking like a number someone chose.
    ("crop_max_upscale", "SINDRI_CROP_MAX_UPSCALE", _MAX_UPSCALE, float, 1.0, None),
)


# The height-dependent pad, added 2026-09-16. The pad response is not spread
# across tall boxes, it is ONE band: across pads 6/24/48 the 120-200 px bucket
# went 0.391 -> 0.523 -> 0.578, monotone and still climbing, while 40-80 was flat
# (+0.011), >=200 flat to three decimals, and 80-120 gained nothing and gave back
# what little it had at 48. A single global pad has to split that difference,
# which is why pad 48 read better where it mattered and still cost more overall.
#
# Defaults are "no height dependence": the tall pad falls back to the base pad,
# so an unconfigured run is byte-identical to every dump ever taken, and the
# recording stays silent for it.
_CROP_TALL_H = 120          # px; where the responding band starts
_TALL_KNOBS = (
    ("crop_pad_tall", "SINDRI_CROP_PAD_TALL", None, int, 0, None),
    ("crop_tall_h", "SINDRI_CROP_TALL_H", _CROP_TALL_H, int, 1, None),
)


def _crop_knob(env, name, key, default, cast, lo, hi):
    raw = (os.environ if env is None else env).get(key)
    if raw is None or raw == "":
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"{key}={raw!r} is not a {cast.__name__}. Refusing to fall back to "
            f"{default!r}: that serves the control configuration under a "
            f"treatment arm's run name, and the result reads as 'the crop made "
            f"no difference'.")
    if value < lo or (hi is not None and value > hi):
        raise ValueError(
            f"{key}={raw!r} is out of range (>= {lo}"
            + (f", <= {hi}" if hi is not None else "") + "). Same refusal: a "
            f"silently corrected knob measures the default and reports the arm.")
    return value


def resolve_crop_knobs(env=None):
    """(pad, min_h, max_upscale) in effect for this run."""
    return tuple(_crop_knob(env, name, key, default, cast, lo, hi)
                 for name, key, default, cast, lo, hi in _CROP_KNOBS)


def resolve_tall_pad(env=None):
    """(tall_pad, threshold_px), with tall_pad None when height dependence is off."""
    return tuple(_crop_knob(env, *k) for k in _TALL_KNOBS)


def crop_pad_for(height_px: float, env=None) -> int:
    """The pad for a read box of this height.

    Threshold is INCLUSIVE, so `crop_tall_h` names the first height that gets
    the tall pad -- which is how the measured band is described (`120-200`), and
    an exclusive reading would silently exclude its own boundary."""
    base = resolve_crop_knobs(env)[0]
    tall, threshold = resolve_tall_pad(env)
    if tall is None:
        return base
    return tall if height_px >= threshold else base


def active_crop_knobs(env=None) -> dict:
    """The crop knobs for RunConfig.extra -- empty when all three are default.

    All three or none: recording only the changed key would make a dump's
    meaning depend on what the defaults were on the day it ran, and the defaults
    are exactly what an arm moves. Empty at default, so every dump ever taken
    keeps the config it has and stays reusable."""
    values = resolve_crop_knobs(env)
    tall, threshold = resolve_tall_pad(env)
    out = {} if values == _BASELINE_CROP_KNOBS else {
        k[0]: v for k, v in zip(_CROP_KNOBS, values)}
    if tall is not None:
        # All or none, for the reason the base three follow: a dump recording
        # the tall pad without its threshold would not say WHICH boxes got it.
        # And the base knobs travel with it even at the baseline, because a
        # height-dependent run is not a baseline run whatever its base pad is.
        out = {k[0]: v for k, v in zip(_CROP_KNOBS, values)}
        out["crop_pad_tall"] = tall
        out["crop_tall_h"] = threshold
    return out


def _prep_crop(image, box, w, h, pad: int, min_h=None, max_upscale=None):
    """Crop, pad, and upscale a small crop toward a legible size.

    min_h/max_upscale resolve from the environment at call time so an arm does
    not have to thread them through every call site -- the same reason
    `read_prompt()` is called inside the backend rather than passed in."""
    if min_h is None or max_upscale is None:
        _, env_min_h, env_max_upscale = resolve_crop_knobs()
        min_h = env_min_h if min_h is None else min_h
        max_upscale = env_max_upscale if max_upscale is None else max_upscale
    x0, y0, x1, y1 = box
    if pad:
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    crop = image.crop((x0, y0, x1, y1))
    ch = crop.height
    if 0 < ch < min_h:
        scale = min(max_upscale, min_h / ch)
        crop = crop.resize((max(1, int(crop.width * scale)),
                            max(1, int(ch * scale))), Image.LANCZOS)
    return crop


def _is_vertical(box) -> bool:
    return (box[3] - box[1]) > (box[2] - box[0]) * 1.3


def _regions_overlap(a, b, min_frac: float = 0.5) -> bool:
    """True if boxes a and b overlap by at least `min_frac` of the smaller box's
    area — used to detect when the notes and marks locators found the same
    physical legend (a small corner touch does not count)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return smaller > 0 and inter / smaller >= min_frac


# Plain language, like FLAG_REASONS: this is what the reviewer reads.
SUGGESTION_REASON = "low confidence — not ballooned; confirm to add"


def _policy_with_suggestions(results, drop_stages=None):
    """(kept, suggestions): the active policy, with rows dropped by stage 2 or
    later KEPT ASIDE for the reviewer instead of deleted
    (docs/plans/2026-10-07-suggestion-tray-design.md). Stage 1 removes
    duplicates of a balloon, which are not suggestions. Flags first, then the
    stages, exactly as _apply_active_policy has always run them."""
    for c in results:
        extra = pr.apply_flag_rules(c, pr.ACTIVE_FLAG_RULES)
        if extra:
            c.needs_review = True
            c.review_reasons = [*c.review_reasons, *extra]
    stages = pr.ACTIVE_DROP_STAGES if drop_stages is None else drop_stages
    kept, dropped = pr.split_drop_stages(results, stages)
    suggestions = [c for stage in dropped[1:] for c in stage]
    for c in suggestions:
        c.suggested = True
        c.pos = 0
        c.review_reasons = [*c.review_reasons, SUGGESTION_REASON]
    return kept, suggestions


def _apply_active_policy(results, drop_stages=None):
    """The kept flag and drop rules, through the SAME functions the offline
    pricing used -- so the price and the shipped behaviour cannot drift apart.

    Flags FIRST, then the drop stages in order. The order is load-bearing since
    2026-10-06: the phantom drops fire only on rows that would ship UNFLAGGED,
    so they read the needs_review the flags just set. `drop_stages` defaults to
    the active ones; app/eval/drop_check.py passes the registered BASE so its
    control stays the policy the phantom drops were priced against."""
    return _policy_with_suggestions(results, drop_stages)[0]


def extract(pdf_path, work_dir, dpi: int = 300, backend=None,
            detect_only: bool = False, progress=None) -> ExtractionResult:
    work_dir = Path(work_dir)
    backend = backend or get_backend()

    # `progress(step, detail, current, total)` lets callers stream pipeline
    # status to the UI; it is a no-op when no callback is supplied.
    def emit(step, detail="", current=None, total=None):
        if progress is not None:
            progress(step, detail, current, total)

    if not hasattr(backend, "detect_regions"):
        from app.pipeline.ocr import get_vlm_fallback_reason
        reason = get_vlm_fallback_reason()
        msg = "auto-ballooning requires the VLM backend"
        if reason:
            msg += f" — {reason}"
        raise RuntimeError(msg)

    emit("render", "Rendering page")
    render = render_page(pdf_path, dpi=dpi, out_dir=work_dir)
    image = Image.open(render.png_path).convert("RGB")

    # The top-right "Mark/Note . Description" legend is a NOTES table. Two
    # locators can find notes tables: the deterministic CV grid locator, which
    # reliably finds the ruled top-right legend, and the VLM cluster locator,
    # which finds any SEPARATE notes table elsewhere. Both feed `notes`. When the
    # VLM region coincides with the CV legend it is the same physical table, so
    # the CV one owns it and the VLM region is dropped (read/masked once).
    emit("notes", "Reading notes block")
    legend = mb.locate_marks_block(image)            # CV: top-right legend
    region = nb.locate_notes_block(image, backend)   # VLM: any separate table
    if (region is not None and legend is not None
            and _regions_overlap(region.outer_box, legend.outer_box)):
        region = None

    # Read every located notes table, parse into rows, and concatenate. Each
    # source keeps its own column count for review flagging. Any failure leaves
    # notes empty and the rest of the pipeline runs unchanged.
    note_sources = []   # (raw_text, outer_box, two_columns)
    if legend is not None:
        note_sources.append((mb.read_marks_block(image, legend, backend),
                             legend.outer_box, len(legend.lang_columns) == 2))
    if region is not None:
        note_sources.append((nb.read_notes_block(image, region, backend),
                             region.outer_box, len(region.lang_columns) == 2))

    notes_obj = None
    parsed = []   # (Note, two_columns)
    for raw, box, two_cols in note_sources:
        for n in nb.parse_notes_block(raw, box).notes:
            parsed.append((n, two_cols))
    if parsed:
        all_notes = [n for n, _ in parsed]
        known_parents = {n.pos for n in all_notes if n.parent_pos is None}
        for n, two_cols in parsed:
            n.needs_review, n.review_reasons = nb.review_flags_note(
                n, two_columns=two_cols, known_parents=known_parents)
        notes_obj = NoteBlock(region=note_sources[0][1], notes=all_notes)

    # The Mark/Note legend now feeds notes (above); no separate marks table is
    # extracted from these drawings.
    marks_obj = None

    image_for_detect = image
    if legend is not None:
        image_for_detect = mb.mask_region(image_for_detect, legend)
    if region is not None:
        image_for_detect = nb.mask_region(image_for_detect, region)

    # Title-block path: locate the bottom-right Schriftfeld, read its cells as
    # label/value fields, and mask it so its text is not misread as dimensions.
    # Locate runs on the raw image; the bottom-right quadrant restriction makes
    # overlap with a (typically elsewhere) notes block very unlikely.
    emit("title", "Reading title block")
    tb_region = tb.locate_title_block(image)
    title_fields = []
    if tb_region is not None:
        title_fields = tb.read_title_block(image, tb_region, backend)
        image_for_detect = tb.mask_region(image_for_detect, tb_region)

    emit("detect", "Detecting characteristics")
    detections = detect_characteristics(image_for_detect, backend)

    # detect_only exists to price the train-split crop pass. Reads are ~41-80
    # generates per document against detection's ~12, so skipping them MAY be
    # most of the cost -- but a detect generate runs to 1024 tokens against a
    # read's 40, so it may not be. This path is what makes that measurable.
    #
    # It returns boxes with NO transcription: char_type, nominal and the
    # tolerances stay empty, and nothing here invents them. Every consumer must
    # treat such a dump as unscoreable, which is why _cmd_predict records
    # detect_only in RunConfig.extra.
    if detect_only:
        emit("ocr", "Skipping reads (detect_only)", 0, len(detections))
        results = []
        for d in detections:
            outer = _clamp(d.box, render.width, render.height)
            if d.inner_box is None:
                outer = _clamp(bx.tighten_to_ink(image, outer),
                               render.width, render.height)
            c = Characteristic(pos=0, kind=d.kind, subtype=d.subtype or "",
                               source="auto", target_region=outer)
            c.id = uuid.uuid4().hex
            results.append(c)
        number_characteristics(results)
        # render.scale, NOT dpi/72 and NOT omitted. render.py clamps dpi to a
        # pixel budget on large-format sheets, so this is the only scale that
        # interprets the boxes above correctly -- predict_one reads it straight
        # into PredictionDump.scale. Omitting it left the field None and failed
        # every document of the timing arm with a ValidationError, which is the
        # good outcome: had it defaulted to a plausible number, every box in
        # every detect-only dump would have been silently wrong and every
        # training crop cut from the wrong place.
        return ExtractionResult(characteristics=results,
                                render_scale=render.scale)

    known_positions = ({n.pos for n in notes_obj.notes if n.parent_pos is None}
                       if notes_obj is not None else None)
    # Resolved ONCE per document rather than per callout: a bad value must fail
    # before any read, not part-way through, and a knob that changed
    # mid-document would produce a dump whose crops came from two settings.
    resolve_crop_knobs()
    resolve_tall_pad()
    total = len(detections)
    emit("ocr", f"Reading {total} region{'' if total == 1 else 's'}", 0, total)
    results = []
    for i, d in enumerate(detections):
        outer = _clamp(d.box, render.width, render.height)
        # Tighten generous VLM boxes to their ink so the balloon anchors on the
        # real glyph corner and the read crop isn't diluted. CV boxes already
        # carry an exact frame-stripped inner_box, so leave those untouched.
        if d.inner_box is None:
            outer = _clamp(bx.tighten_to_ink(image, outer),
                           render.width, render.height)
        read_box = _clamp(d.inner_box, render.width, render.height) if d.inner_box else outer
        # Height-dependent: the crop pad is resolved from THIS box's height,
        # because the measured response lives in one band rather than across
        # tall boxes generally. Falls back to the flat pad when the tall knob is
        # unset, which is the default.
        crop = _prep_crop(image, read_box, render.width, render.height,
                          pad=0 if d.inner_box
                          else crop_pad_for(read_box[3] - read_box[1]))
        if d.subtype == "gdt" and hasattr(backend, "read_region_gdt"):
            text, confidence = _safe_read(backend.read_region_gdt, crop)
            rotation_ambiguous = False
        else:
            text, confidence, rotation_ambiguous = _best_read(
                backend, crop, _is_vertical(read_box))

        hint = _HINTS.get(d.kind, "")
        subtype = d.subtype or ""
        kind = d.kind
        if subtype == "theoretical" and _NOTE_REF_RE.match(text or ""):
            hint, subtype, kind = "note", "note_ref", "note"

        c = parse_value(text, hint=hint)
        c.id = uuid.uuid4().hex
        c.kind = kind
        c.subtype = subtype
        c.source = "auto"
        c.target_region = outer
        c.confidence = confidence
        if subtype == "note_ref":
            try:
                c.note_ref_pos = int((text or "").strip())
            except ValueError:
                c.note_ref_pos = None
        c.needs_review, c.review_reasons = review_flags(
            c, rotation_ambiguous, known_note_positions=known_positions)
        results.append(c)
        emit("ocr", "Reading regions", i + 1, total)

    # The loose_text exclusion below must still cover DROPPED boxes, or their
    # text resurfaces as title fields -- a side effect the offline price
    # cannot see. So keep the pre-drop regions for it.
    read_regions = [c.target_region for c in results
                    if c.target_region is not None]
    results, suggestions = _policy_with_suggestions(results)

    emit("place", "Placing balloons")
    number_characteristics(results)
    # render.dpi, not the requested dpi: the gap is specified in PDF points and
    # converted to pixels, so a clamped render must use the resolution it was
    # actually drawn at or every balloon drifts away from its callout.
    place_balloons(results, dpi=render.dpi)
    # Suggestions are placed AFTER the real balloons, in their own pass, so
    # they can never push a real balloon away from its callout.
    place_balloons(suggestions, dpi=render.dpi)
    # Free text outside the structured blocks (e.g. margin notes). Exclude the
    # notes/marks/title regions AND every region the main detector already
    # captured, so loose_text only adds text nothing else picked up
    # (no double-extraction, no redundant reads).
    exclude = [b for b in (tb_region.outer_box if tb_region else None,
                           legend.outer_box if legend is not None else None,
                           region.outer_box if region is not None else None)
               if b is not None]
    exclude += read_regions
    title_fields += tb.loose_text(image, backend, exclude)
    return ExtractionResult(characteristics=results, suggestions=suggestions,
                            notes=notes_obj,
                            title_block=title_fields, marks=marks_obj,
                            render_scale=render.scale)
