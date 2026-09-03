"""Stage 11 -- ASSEMBLE (Part A SS A.12, Part C SS C.4.2, SS C.10, Stage 11).

The final arithmetic combination of everything Stages 8-10 already
computed, per A.12:

    NACV(c) = GR(c)                gross reward value          (Stage 8)
            + MilestoneValue(c)    grant_points/cashback/voucher events valued below
            + benefit_value        countable + non-countable    (Stage 9)
            - SteadyFee(c) / Year1Fee(c)                        (Stage 10)
            - ForexCost(c) - SurchargeCost(c)                   (Stage 10)

`assemble_nacv` is deliberately pure arithmetic over already-resolved
rupee amounts -- Stages 8/9/10 own the actual valuation logic; this stage
doesn't re-derive any of it. `value_milestone_grants` is the one piece of
new computation Stage 11 needs: A.5's MilestoneValue sums grant_points/
grant_cashback/grant_voucher events (NOT grant_entitlement, which feeds
Stage 9's countable-benefit value instead, and NOT waive_fee, which
already reduces the fee in Stage 10 -- counting either again here would
double-count).

grant_points/grant_cashback route through Stage 8's value_currency
directly (same points-to-rupees pipeline as ordinary rewards).
grant_voucher reuses whatever the caller already computed via Stage 9's
value_voucher_benefit for that benefit key -- Stage 11 doesn't recompute
utilisation/friction, it just totals what Stage 9 already priced.

3-year cumulative (C.4.2): V_3yr = V_year1 + V_steady_year2 + V_steady_year3.
MVP treats years 2-3 as identical steady-state runs (no spend-growth
modelling, per C.4.2's own stated simplification), so V_3yr = V_year1 +
2 . V_steady_state -- no separate year-2/3 evaluation needed.

Deferred, logged in docs/DECISIONS.md: WelcomeValue (accepted as an
optional zero-default parameter, per A.12's formula, but no card in the
seed catalog has a welcome payload type to test against). RedemptionFees
is no longer deferred -- `redemption_fees_cost` (engine/costs.py) computes
it; `assemble_nacv` takes the resulting Decimal as a plain already-priced
cost, same posture as `forex_cost`/`surcharge_cost` below.

C.3's `condition: "on_renewal"` (syn_renewal's grant_points tier) is this
stage's filter to apply, per docs/DECISIONS.md #14: an on_renewal grant
belongs in the steady-state MilestoneValue but NOT in Year-1's -- there's
no renewal to speak of in the first year. `value_milestone_grants_by_
year_mode` does exactly that split (two `value_milestone_grants` calls,
one over every event, one with on_renewal events dropped) so
`assemble_nacv`'s `milestone_value_year1` parameter can diverge from
`milestone_value` when a card needs it; it defaults to `None`, which falls
back to `milestone_value` unchanged for every card that doesn't (identical
to every existing golden/test call site).

The trace is a flat list of named rupee lines (kind/amount/label/flags),
not C.10's full per-node schema (cap_state, spend_basis, per-currency
v/phi breakdown, source_refs) -- see docs/DECISIONS.md for why that's
deferred rather than attempted at reduced fidelity.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.benefits import BenefitValuation
from engine.thresholds import ThresholdEvent
from engine.valuation import RewardCurrency, value_currency

MILESTONE_PAYLOAD_TYPES = frozenset({"grant_points", "grant_cashback", "grant_voucher"})


@dataclass(frozen=True)
class TraceLine:
    kind: str  # "reward" | "milestone" | "benefit" | "fee" | "forex" | "surcharge"
    amount: Decimal  # signed: positive = value, negative = cost
    label: str
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class NACVResult:
    steady_state: Decimal
    year_1: Decimal
    three_year: Decimal
    trace: tuple[TraceLine, ...]


def value_milestone_grants(
    events: Sequence[ThresholdEvent],
    currencies: dict[str, RewardCurrency],
    primary_routes: dict[str, str],
    voucher_valuations: dict[str, BenefitValuation],
) -> tuple[Decimal, tuple[TraceLine, ...]]:
    total = Decimal("0")
    lines: list[TraceLine] = []

    for event in events:
        payload = event.payload
        if payload.type not in MILESTONE_PAYLOAD_TYPES:
            continue  # grant_entitlement -> Stage 9; waive_fee -> Stage 10; activate_rule -> unsupported (Stage 6-7)

        label = f"{event.threshold_key} tier {event.tier_index}: {payload.type}"

        if payload.type == "grant_voucher":
            valuation = voucher_valuations[payload.benefit]
            amount = valuation.value_rupees
            flags = valuation.flags
        else:  # grant_points | grant_cashback
            currency = currencies[payload.currency]
            valuation = value_currency(currency, payload.amount, primary_routes.get(payload.currency))
            amount = valuation.v_exp_rupees
            flags = valuation.flags

        total += amount
        lines.append(TraceLine(kind="milestone", amount=amount, label=label, flags=flags))

    return total, tuple(lines)


def value_milestone_grants_by_year_mode(
    events: Sequence[ThresholdEvent],
    currencies: dict[str, RewardCurrency],
    primary_routes: dict[str, str],
    voucher_valuations: dict[str, BenefitValuation],
) -> tuple[Decimal, tuple[TraceLine, ...], Decimal, tuple[TraceLine, ...]]:
    """Splits milestone grants by C.3's `condition: "on_renewal"`: the
    steady-state total includes every firing grant (a renewal year IS a
    renewal), the Year-1 total drops any event whose payload carries
    `condition == "on_renewal"` (nothing has renewed yet). Every other
    condition value (currently none defined) is left alone -- unfiltered,
    same as `value_milestone_grants` on its own. Returns (steady_total,
    steady_lines, year1_total, year1_lines)."""
    steady_total, steady_lines = value_milestone_grants(events, currencies, primary_routes, voucher_valuations)
    year1_events = tuple(e for e in events if e.payload.condition != "on_renewal")
    year1_total, year1_lines = value_milestone_grants(year1_events, currencies, primary_routes, voucher_valuations)
    return steady_total, steady_lines, year1_total, year1_lines


def assemble_nacv(
    gross_reward: Decimal,
    milestone_value: Decimal,
    benefit_value: Decimal,
    steady_fee: Decimal,
    year1_fee: Decimal,
    forex_cost: Decimal = Decimal("0"),
    surcharge_cost: Decimal = Decimal("0"),
    redemption_fees_cost: Decimal = Decimal("0"),
    welcome_value: Decimal = Decimal("0"),
    milestone_lines: Sequence[TraceLine] = (),
    milestone_value_year1: Decimal | None = None,
) -> NACVResult:
    if milestone_value_year1 is None:
        milestone_value_year1 = milestone_value
    steady_state = (
        gross_reward + milestone_value + benefit_value
        - steady_fee - forex_cost - surcharge_cost - redemption_fees_cost
    )
    year_1 = (
        gross_reward + milestone_value_year1 + benefit_value + welcome_value
        - year1_fee - forex_cost - surcharge_cost - redemption_fees_cost
    )
    three_year = year_1 + Decimal("2") * steady_state

    trace = (
        TraceLine(kind="reward", amount=gross_reward, label="gross reward value"),
        *milestone_lines,
        TraceLine(kind="benefit", amount=benefit_value, label="benefit value"),
        TraceLine(kind="fee", amount=-steady_fee, label="annual fee (steady-state)"),
        TraceLine(kind="forex", amount=-forex_cost, label="forex cost"),
        TraceLine(kind="surcharge", amount=-surcharge_cost, label="surcharge cost"),
        TraceLine(kind="fee", amount=-redemption_fees_cost, label="redemption fees"),
    )

    return NACVResult(steady_state=steady_state, year_1=year_1, three_year=three_year, trace=trace)
