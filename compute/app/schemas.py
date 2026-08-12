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
