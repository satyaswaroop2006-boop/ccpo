"""Pydantic request/response models for the FastAPI layer (Part E SS E.0).

Shape only -- mirrors `engine/normalise.py`'s `CategorySpend` and
`engine/evaluate.py`'s `EvaluateAssumptions` exactly, plus small conversion
methods to build those engine dataclasses. No rupee math happens here
(CLAUDE.md rule 1); the `to_*` methods only reshape already-typed data.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from engine.assemble import NACVResult, TraceLine
from engine.evaluate import EvaluateAssumptions, EvaluateResult
from engine.normalise import CategorySpend, SpendInput
from optimiser.candidates import (
    DEFAULT_CHAMPION_CATEGORY_THRESHOLD,
    DEFAULT_CHAMPION_DELTA,
    DEFAULT_CHAMPION_TOP_N,
    DEFAULT_MAX_TOTAL,
    DEFAULT_STANDALONE_N,
)
from optimiser.classify import CardClassification, DEFAULT_ICV_MEANINGFUL
from optimiser.frontier import FrontierPoint, RecommendationStep, format_step
from optimiser.scenarios import PortfolioRobustness

Geography = Literal["domestic", "international"]


class SpendItemIn(BaseModel):
    category: str
    annual_amount: Decimal = Field(ge=0)
    channel: str | None = None
    geography: Geography = "domestic"
    merchant_group: str | None = None
    seasonality: list[Decimal] | None = None  # 12 fractions summing to 1; None = uniform

    def to_category_spend(self) -> CategorySpend:
        return CategorySpend(
            category=self.category, annual_amount=self.annual_amount, channel=self.channel,
            seasonality=tuple(self.seasonality) if self.seasonality is not None else None,
            geography=self.geography, merchant_group=self.merchant_group,
        )


def spend_input_from_items(items: list[SpendItemIn]) -> SpendInput:
    return SpendInput(category_spend=tuple(item.to_category_spend() for item in items))


class AssumptionsIn(BaseModel):
    primary_routes: dict[str, str] = Field(default_factory=dict)
    ticket_sizes: dict[str, Decimal] = Field(default_factory=dict)
    upi_category_mix: dict[str, Decimal] = Field(default_factory=dict)
    voucher_utilisation: Decimal = Decimal("1.0")
    voucher_friction: Decimal = Decimal("1.0")
    flat_perk_utilisation: Decimal = Decimal("1.0")
    benefit_need: dict[str, Decimal] = Field(default_factory=dict)
    benefit_unit_value: dict[str, Decimal] = Field(default_factory=dict)

    def to_evaluate_assumptions(self) -> EvaluateAssumptions:
        return EvaluateAssumptions(
            primary_routes=self.primary_routes, ticket_sizes=self.ticket_sizes,
            upi_category_mix=self.upi_category_mix, voucher_utilisation=self.voucher_utilisation,
            voucher_friction=self.voucher_friction, flat_perk_utilisation=self.flat_perk_utilisation,
            benefit_need=self.benefit_need, benefit_unit_value=self.benefit_unit_value,
        )


class EvaluateRequest(BaseModel):
    card_key: str
    spend: list[SpendItemIn]
    assumptions: AssumptionsIn = Field(default_factory=AssumptionsIn)


class TraceLineOut(BaseModel):
    kind: str
    amount: Decimal
    label: str
    flags: tuple[str, ...] = ()

    @classmethod
    def from_trace_line(cls, line: TraceLine) -> "TraceLineOut":
        return cls(kind=line.kind, amount=line.amount, label=line.label, flags=line.flags)


class NACVOut(BaseModel):
    steady_state: Decimal
    year_1: Decimal
    three_year: Decimal
    trace: list[TraceLineOut]

    @classmethod
    def from_nacv_result(cls, nacv: NACVResult) -> "NACVOut":
        return cls(
            steady_state=nacv.steady_state, year_1=nacv.year_1, three_year=nacv.three_year,
            trace=[TraceLineOut.from_trace_line(line) for line in nacv.trace],
        )


class EvaluateResponse(BaseModel):
    card_key: str
    gross_reward_value: Decimal
    milestone_value: Decimal
    milestone_value_year1: Decimal
    benefit_value: Decimal
    waiver_achieved: bool
    fee_steady: Decimal
    fee_year1: Decimal
    nacv: NACVOut
    flags: tuple[str, ...]

    @classmethod
    def from_result(cls, result: EvaluateResult) -> "EvaluateResponse":
        return cls(
            card_key=result.card_key, gross_reward_value=result.gross_reward_value,
            milestone_value=result.milestone_value, milestone_value_year1=result.milestone_value_year1,
            benefit_value=result.benefit_value, waiver_achieved=result.waiver_achieved,
            fee_steady=result.fee_steady, fee_year1=result.fee_year1,
            nacv=NACVOut.from_nacv_result(result.nacv), flags=result.flags,
        )


DEFAULT_DELTAS = [Decimal("1000"), Decimal("10000"), Decimal("50000")]


class NextBestSpendCandidateIn(BaseModel):
    """One (card, category) route to test -- E.12's marginal-band idea,
    annual full-profile MVP (docs/DECISIONS.md's Phase 3 entry): no wallet
    mid-year state yet, so this compares two full-year evaluations, not a
    remaining-months-of-the-year delta."""

    card_key: str
    category: str
    channel: str | None = None
    geography: Geography = "domestic"
    merchant_group: str | None = None
    assumptions: AssumptionsIn = Field(default_factory=AssumptionsIn)


class NextBestSpendRequest(BaseModel):
    baseline_spend: list[SpendItemIn]
    candidates: list[NextBestSpendCandidateIn]
    deltas: list[Decimal] = Field(default_factory=lambda: list(DEFAULT_DELTAS))


class NextBestSpendResultOut(BaseModel):
    card_key: str
    category: str
    delta: Decimal
    baseline_nacv_steady_state: Decimal
    delta_nacv_steady_state: Decimal
    delta_nacv_rate: Decimal  # delta_nacv_steady_state / delta -- net rate on the marginal rupee


class NextBestSpendResponse(BaseModel):
    results: list[NextBestSpendResultOut]  # best (highest delta_nacv_rate) first


CardinalityMode = Literal["exactly", "up_to", "optimiser_decides"]


class OptimiseRequest(BaseModel):
    """Part E SS E.1's end-to-end flow (candidates -> enumerate -> allocate
    -> evaluate -> repair -> frontier/classify), minus the two pieces still
    genuinely deferred elsewhere: SNAPSHOT (no rule-version/assumptions
    freeze table exists yet, same gap as evaluation_runs persistence,
    docs/DECISIONS.md's Phase 3 status) and wallet mode (#10/#61,
    unbuilt). `candidate_universe=None` pulls the full live catalog via
    `CardRepository.get_all_card_bundles`; set it to pin a specific set of
    keys instead (also how tests keep this endpoint fast and
    deterministic). Frontier's T1/T2 constants and scenarios.py's
    Low/High factors stay at their module defaults (docs/DECISIONS.md
    #82/#90) -- not yet exposed as per-request overrides, since neither
    has Satya's sign-off as an assumptions-registry value a caller should
    be able to move."""

    spend: list[SpendItemIn]
    assumptions: AssumptionsIn = Field(default_factory=AssumptionsIn)
    candidate_universe: list[str] | None = None
    cardinality_mode: CardinalityMode = "up_to"
    max_cards: int | None = None
    n_tol: int | None = None
    run_scenarios: bool = True
    icv_meaningful: Decimal = DEFAULT_ICV_MEANINGFUL
    strategic_feature_cards: list[str] = Field(default_factory=list)
    standalone_n: int = DEFAULT_STANDALONE_N
    champion_category_threshold: Decimal = DEFAULT_CHAMPION_CATEGORY_THRESHOLD
    champion_top_n: int = DEFAULT_CHAMPION_TOP_N
    champion_delta: Decimal = DEFAULT_CHAMPION_DELTA
    max_total_candidates: int = DEFAULT_MAX_TOTAL


class ExcludedCardOut(BaseModel):
    """A universe card `optimiser.allocate.allocate` (+ `repair`) couldn't
    process at all, so it never reached candidate selection -- SS E.2's
    own "why was card X even considered / not considered" transparency
    principle, applied one level earlier than SS E.2 itself describes
    (before ranking, not after)."""

    card_key: str
    reason: str


class FrontierPointOut(BaseModel):
    size: int
    subset_key: str
    card_keys: tuple[str, ...]
    pv_exact: Decimal

    @classmethod
    def from_point(cls, point: FrontierPoint) -> "FrontierPointOut":
        return cls(size=point.size, subset_key=point.subset_key, card_keys=point.card_keys, pv_exact=point.pv_exact)


class RecommendationStepOut(BaseModel):
    size: int
    delta_v: Decimal
    t1_pass: bool
    t1_threshold: Decimal
    delta_fee: Decimal
    delta_gross_benefit: Decimal
    fee_cover_ratio: Decimal | None
    t2_pass: bool
    low_spend_delta_v: Decimal | None
    t3_pass: bool | None
    passes: bool
    explanation: str  # SS E.9's own worked-example phrasing, plain rupees

    @classmethod
    def from_step(cls, step: RecommendationStep) -> "RecommendationStepOut":
        return cls(
            size=step.size, delta_v=step.delta_v, t1_pass=step.t1_pass, t1_threshold=step.t1_threshold,
            delta_fee=step.delta_fee, delta_gross_benefit=step.delta_gross_benefit,
            fee_cover_ratio=step.fee_cover_ratio, t2_pass=step.t2_pass, low_spend_delta_v=step.low_spend_delta_v,
            t3_pass=step.t3_pass, passes=step.passes, explanation=format_step(step),
        )


class CardClassificationOut(BaseModel):
    card_key: str
    label: str
    icv: Decimal
    overlap: Decimal | None
    note: str | None
    downgrade_to: str | None

    @classmethod
    def from_classification(cls, c: CardClassification) -> "CardClassificationOut":
        return cls(card_key=c.card_key, label=c.label, icv=c.icv, overlap=c.overlap, note=c.note, downgrade_to=c.downgrade_to)


class RobustnessOut(BaseModel):
    v_expected: Decimal
    v_low: Decimal
    v_high: Decimal
    robustness: Decimal | None
    rank_stable: bool

    @classmethod
    def from_robustness(cls, r: PortfolioRobustness) -> "RobustnessOut":
        return cls(v_expected=r.v_expected, v_low=r.v_low, v_high=r.v_high, robustness=r.robustness, rank_stable=r.rank_stable)


class OptimiseResponse(BaseModel):
    candidates: list[str]  # SS E.2's pre-filtered set, after excluded_cards is removed
    excluded_cards: list[ExcludedCardOut]
    frontier: list[FrontierPointOut]  # one winner per enumerated size (SS E.9)
    recommendation_steps: list[RecommendationStepOut]
    recommended_size: int
    capped_by_tolerance: bool
    recommended_subset_key: str
    recommended_card_keys: tuple[str, ...]
    recommended_pv_exact: Decimal
    classification_owned: list[CardClassificationOut]  # the recommended portfolio's own cards (SS E.8)
    classification_candidates: list[CardClassificationOut]  # candidates not in the recommended portfolio
    robustness: RobustnessOut | None  # None when run_scenarios=False
