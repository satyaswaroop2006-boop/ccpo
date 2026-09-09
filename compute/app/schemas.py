"""Pydantic request/response models for the FastAPI layer (Part E SS E.0).

Shape only -- mirrors `engine/normalise.py`'s `CategorySpend` and
`engine/evaluate.py`'s `EvaluateAssumptions` exactly, plus small conversion
methods to build those engine dataclasses. No rupee math happens here
(CLAUDE.md rule 1); the `to_*` methods only reshape already-typed data.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.repository import CardSummary
from engine.assemble import NACVResult, TraceLine
from engine.card_bundle import CardRuleBundle
from engine.evaluate import EvaluateAssumptions, EvaluateResult
from engine.normalise import CategorySpend, SpendInput
from optimiser.candidates import (
    DEFAULT_CHAMPION_CATEGORY_THRESHOLD,
    DEFAULT_CHAMPION_DELTA,
    DEFAULT_CHAMPION_TOP_N,
    DEFAULT_MAX_TOTAL,
    DEFAULT_STANDALONE_N,
)
from ingest.review import ReviewQueueGroup, ReviewQueueItem
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


class CardSummaryOut(BaseModel):
    """Part F §F.2.1's Catalog list row -- one per published card."""

    card_key: str
    name: str
    issuer_name: str
    network: str
    tier: str | None
    segment: str | None
    joining_fee: Decimal
    annual_fee: Decimal
    currency_key: str

    @classmethod
    def from_summary(cls, s: CardSummary) -> "CardSummaryOut":
        return cls(
            card_key=s.card_key, name=s.name, issuer_name=s.issuer_name, network=s.network,
            tier=s.tier, segment=s.segment, joining_fee=s.joining_fee, annual_fee=s.annual_fee,
            currency_key=s.currency_key,
        )


def _jsonable(value: Any) -> Any:
    """Recursively turns an engine rule dataclass (`EarningRule`, `Cap`,
    `Threshold`, `Exclusion`, `Benefit`, `Surcharge`, and everything they
    nest -- `Selector`, `Accrual`, `Window`, `Payload`, `Tier`, ...) into a
    plain JSON-safe structure, field-for-field, with no field list
    hand-duplicated here. Part F §F.2.1's own framing for the Catalog
    detail view: "a direct, formatted dump of the rule vocabulary Part C
    defines, not a new summarization layer" -- hand-mirroring each of
    these ~10 dataclasses into its own Pydantic model would BE a second
    copy of that vocabulary, with its own drift risk every time an engine
    dataclass gains a field; this has none, by construction. Decimal ->
    str (not float) to stay exact, matching CLAUDE.md rule 5's "Decimal
    for money" discipline all the way to the wire, not just up to the API
    boundary."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


class CardDetailOut(CardSummaryOut):
    """Part F §F.2.1's Catalog detail view: the summary row plus the full
    rule breakdown, straight off `CardRuleBundle` -- the same object
    `/evaluate` and `/optimise` already load a card into, not a second
    translation of it. Each `earning_rules[]` entry has its own `accrual`
    merged in even though `EarningRule` itself carries no such field
    (Stage 4 looks it up separately from `bundle.accruals[rule.key]`,
    same split `evaluate_card`'s own Stage 3-4 boundary already uses) --
    a display view naturally wants a rule and its rate together, so this
    denormalises the one hop a renderer would otherwise have to redo
    itself, discovered by this endpoint's own test."""

    earning_rules: list[dict[str, Any]]
    caps: list[dict[str, Any]]
    thresholds: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    benefits: list[dict[str, Any]]
    surcharges: list[dict[str, Any]]

    @classmethod
    def from_summary_and_bundle(cls, s: CardSummary, bundle: CardRuleBundle) -> "CardDetailOut":
        earning_rules = []
        for rule in bundle.earning_rules:
            rule_out = _jsonable(rule)
            rule_out["accrual"] = _jsonable(bundle.accruals[rule.key])  # not on EarningRule itself -- see class docstring
            earning_rules.append(rule_out)

        return cls(
            card_key=s.card_key, name=s.name, issuer_name=s.issuer_name, network=s.network,
            tier=s.tier, segment=s.segment, joining_fee=s.joining_fee, annual_fee=s.annual_fee,
            currency_key=s.currency_key,
            earning_rules=earning_rules,
            caps=[_jsonable(c) for c in bundle.caps],
            thresholds=[_jsonable(t) for t in bundle.thresholds],
            exclusions=[_jsonable(e) for e in bundle.exclusions],
            benefits=[_jsonable(b) for b in bundle.benefits.values()],
            surcharges=[_jsonable(sc) for sc in bundle.surcharges],
        )


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


class ReviewQueueItemOut(BaseModel):
    """Part F §F.2.3's Ingestion Review row (Slice 6). `entity_fields` is
    the drafted row's own columns (`ingest/review.py::_fetch_entity_
    fields`, already JSON-safe) -- deliberately an open dict, not a
    hand-typed model per entity_type, same "don't re-duplicate Part C's
    vocabulary a second time" call `CardDetailOut`/`_jsonable` already
    made (§F.2.1/#163). `source_snapshot_url` is `None` when the source
    has no `storage_path` on file (captured before Part I's own capture
    tooling existed, or never captured) -- surfaced honestly, not a
    broken link."""

    source_link_id: str
    entity_type: str
    entity_key: str
    entity_fields: dict[str, Any]
    confidence: str
    source_url: str
    source_type: str
    source_title: str | None
    source_snapshot_url: str | None

    @classmethod
    def from_item(cls, item: ReviewQueueItem, snapshot_url: str | None) -> "ReviewQueueItemOut":
        return cls(
            source_link_id=item.source_link_id, entity_type=item.entity_type, entity_key=item.entity_key,
            entity_fields=item.entity_fields, confidence=item.confidence, source_url=item.source_url,
            source_type=item.source_type, source_title=item.source_title, source_snapshot_url=snapshot_url,
        )


class ReviewQueueGroupOut(BaseModel):
    label: str  # "card:<key>" or "issuer:<key> (shared currency)" -- ingest/review.py's own grouping, rendered not printed
    card_version_id: str | None
    items: list[ReviewQueueItemOut]

    @classmethod
    def from_group(cls, group: ReviewQueueGroup, items: list[ReviewQueueItemOut]) -> "ReviewQueueGroupOut":
        return cls(label=group.label, card_version_id=group.card_version_id, items=items)


class ReviewQueueResponse(BaseModel):
    groups: list[ReviewQueueGroupOut]


class RejectSourceLinkRequest(BaseModel):
    """F.2.3: "Reject (with a required note)" -- `min_length=1` enforces
    that at the schema boundary, not just as a UI convention a caller
    could bypass by hitting the endpoint directly."""

    note: str = Field(min_length=1)


class SourceLinkActionResponse(BaseModel):
    source_link_id: str
    reviewer_status: Literal["approved", "rejected"]
