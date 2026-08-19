"""Versioned schemas for the eval harness. Everything that is written to disk
(gold docs, prediction dumps, scores, reports) lives here and carries
`schema_version` so old artifacts are rejected loudly, never misread.

Geometry convention: ALL positions/boxes in these models are PDF points
(dpi-independent). Conversion from render pixels happens exactly once, in
`app.eval.dump.to_points`.
"""
import hashlib
import json
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, field_validator

from app.models import ExtractionResult

SCHEMA_VERSION = 1


class _Versioned(BaseModel):
    schema_version: int = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v):
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {v} (this code reads {SCHEMA_VERSION})")
        return v


class GoldCharacteristic(BaseModel):
    balloon: int                                # client's balloon number
    # None when the balloon could not be located on the page. The row is still
    # gold — it is matched on value instead of geometry (see MatchParams.mode).
    position_pt: Optional[Tuple[float, float]] = None
    char_type: str = ""
    nominal: str = ""
    upper_tol: str = ""
    lower_tol: str = ""
    raw: str = ""                               # optional free-text from Excel
    # "dimension" (measurable, ballooned) or "note" (verbal requirement, never
    # ballooned). ~14% of the delivered rows are notes; scoring them as missed
    # callouts would let note text dominate the review-cost metric.
    kind: str = "dimension"


class GoldDoc(_Versioned):
    doc_id: str
    pdf: str
    excel: str
    page_rect: Tuple[float, float, float, float]   # PDF points
    characteristics: List[GoldCharacteristic] = []
    is_variant: bool = False
    provenance: Dict = {}    # join stats, balloon-recovery stats — NOT hashed

    def gold_hash(self) -> str:
        """Content hash of everything that affects scoring (provenance excluded)."""
        payload = self.model_dump(exclude={"provenance"})
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class RunConfig(BaseModel):
    """Fingerprint of what produced a prediction dump — captured at predict
    time on the GPU box, never reconstructed later."""
    model_id: str = ""
    dpi: int = 300
    git_sha: str = "unknown"
    prompt_sha256: str = "unavailable"
    extra: Dict = {}          # tuned knobs, few-shot bank id, adapter id, ...


class PredictionDump(_Versioned):
    doc_id: str
    config: RunConfig
    scale: float                                   # render pixels per PDF point
    page_rect: Tuple[float, float, float, float]
    result: ExtractionResult


class ReviewCostWeights(BaseModel):
    """Handoff §4: w_miss >> w_escaped > w_false > w_flag. Replace defaults with
    client-sourced estimates in Task 13; reports embed the weights used."""
    miss: float = 10.0
    escaped: float = 5.0
    false: float = 2.0
    flag: float = 1.0


class MatchParams(BaseModel):
    # extra="forbid": a mistyped knob must not be silently dropped — it would
    # change scoring while still comparing "equal" in the comparability guard.
    model_config = {"extra": "forbid"}

    max_geo_frac: float = 0.10        # match gate: center distance / page diagonal
    value_bonus: float = 0.35         # cost reduction when nominals agree
    misplaced_frac: float = 0.04      # matched farther than this → tagged misplaced
    # "geometry" needs gold balloon positions. "value" is the fallback for
    # documents where they could not be recovered: pairs on value similarity
    # alone, so a misread still scores as one wrong row rather than a miss plus
    # a false detection. Part of the comparability guard — runs scored with
    # different modes refuse to compare.
    mode: str = "geometry"
    value_sim_min: float = 0.6        # value mode: minimum similarity to pair
    # Which gold rows the headline metric covers. Verbal requirements were
    # never ballooned, so charging them as missed callouts would let note text
    # dominate. Part of the comparability guard — a run scored over a different
    # scope refuses to compare.
    score_kinds: Tuple[str, ...] = ("dimension",)


class MatchedPair(BaseModel):
    gold_balloon: int
    pred_pos: int
    distance_frac: float
    fields_correct: bool
    field_errors: List[str] = []      # e.g. ["nominal: '20'!='28'"]
    flagged: bool = False
    taxonomy: str = ""                # correct|flagged_correct|flagged_error|
                                      # escaped_error (+ cause/misplaced tags in notes)
    notes: List[str] = []


class DocScore(_Versioned):
    doc_id: str
    gold_hash: str
    n_gold: int
    n_pred: int
    pairs: List[MatchedPair] = []
    missed_balloons: List[int] = []
    false_positions: List[int] = []   # pred.pos of unmatched predictions
    counts: Dict[str, int] = {}       # taxonomy histogram
    excluded_by_kind: int = 0         # gold rows outside MatchParams.score_kinds
    # Resolution this document was actually rendered at. render.py clamps dpi
    # to an 80 MP budget on large-format sheets, so this can sit below
    # config.dpi — and "did the misses cluster on the clamped drawings?" cannot
    # be answered without it. 0.0 in reports written before this field existed.
    effective_dpi: float = 0.0
    # Predictions by detector kind, and which of them went unmatched. Gold is
    # filtered to MatchParams.score_kinds; predictions are not, so a correctly
    # detected surface finish or note is charged as a false detection. These two
    # make that inflation measurable instead of assumed.
    pred_kinds: Dict[str, int] = {}
    false_kinds: Dict[str, int] = {}
    # Of the predictions that DID match in-scope gold, their detector kinds.
    # normalize._DIMENSION_WORDS keeps GD&T and surface characteristics inside
    # score_kinds on the gold side, so a non-"dimension" kind appearing here is
    # a prediction that filtering to score_kinds would turn into a miss (w=10)
    # from a false detection (w=2). pred_kinds[k] == matched_kinds[k] +
    # false_kinds[k] for every k, which is what makes this checkable.
    matched_kinds: Dict[str, int] = {}
    # Why each gold row was missed. "missed" alone cannot distinguish detection
    # finding nothing from detection finding something the matcher spent on a
    # neighbour, and those route to different Rung 1 knobs. The three partition
    # counts["missed"] exactly.
    #   contended: a prediction sits inside max_geo_frac but paired elsewhere
    #              -> merge_adjacent / dedupe collapsed sibling callouts
    #   isolated:  no prediction inside the gate -> tile size / overlap / conf
    #   unlocated: gold row has no recovered balloon position, so no detection
    #              change can ever match it -> balloon recovery, not detection
    missed_contended: int = 0
    missed_isolated: int = 0
    missed_unlocated: int = 0
    review_cost: float = 0.0
    recall: float = 0.0
    precision: float = 0.0
    escaped_rate: float = 0.0


class RunReport(_Versioned):
    run_name: str
    config: RunConfig
    weights: ReviewCostWeights
    match_params: MatchParams
    splits_hash: str = ""
    split_used: str = ""              # "dev" | "test" | "all"
    doc_scores: List[DocScore] = []
    mean_review_cost: float = 0.0
    micro_recall: float = 0.0
    micro_precision: float = 0.0
    escaped_rate: float = 0.0
    taxonomy: Dict[str, int] = {}
