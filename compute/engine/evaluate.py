"""Pipeline orchestrator (Part C SS C.4's Stage 1-11 order, Part E SS E.1
step 5 "EVALUATE exact engine run per surviving subset" — applied here to
one card, the primitive the future optimiser loops over per subset).

`evaluate_card` is the ONE function that runs a full evaluation: normalise
-> eligibility -> match -> accrue -> caps/incremental-bands -> thresholds ->
apply_rule_activations -> valuation -> benefits -> costs -> year-mode split
-> assemble_nacv. It replaces nothing engine-internal -- it is exactly the
sequence every `goldens/golden_syn_*.json` test in `tests/test_goldens.py`
already ran by hand, consolidated into one reusable call so `/evaluate` and
`/next-best-spend` (and, later, the optimiser's per-subset evaluation step)
share it instead of re-deriving the composition. CLAUDE.md rule 1 ("no
financial math in API handlers") is exactly why this lives in `engine/`,
not `app/`.

Caveat inherited unchanged from `apply_rule_activations` itself: it
doesn't reconcile `caps.py`'s synthetic overflow/incremental-band segments
(different object identity than the real spend segments it matches
against) -- no in-scope card combines a cap with an activation-gated rule,
so this never actually triggers today, same posture as the source
function's own docstring.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from engine.accrue import accrue_category_mode
from engine.assemble import NACVResult, assemble_nacv, value_milestone_grants_by_year_mode
from engine.benefits import BenefitValuation, value_countable_benefit, value_flat_perk_benefit, value_voucher_benefit
from engine.card_bundle import CardRuleBundle
from engine.caps import apply_caps, apply_incremental_bands
from engine.costs import compute_fees, forex_cost, international_spend_total, redemption_fees_cost, surcharge_cost
from engine.eligibility import apply_eligibility
from engine.match import match
from engine.normalise import (
    DEFAULT_CATEGORY_MCC_MAP,
    DEFAULT_TICKET_SIZES,
    DEFAULT_UPI_CATEGORY_MIX,
    AssumptionsSnapshot,
    NormalisedSpend,
    SpendInput,
    normalise,
)
from engine.thresholds import apply_rule_activations, evaluate_thresholds
from engine.valuation import RewardCurrency, value_accrual_results

DEFAULT_UTILISATION = Decimal("1.0")
DEFAULT_FRICTION = Decimal("1.0")


@dataclass(frozen=True)
class EvaluateAssumptions:
    """The C.7 registry slice a single evaluation needs, mirroring exactly
    the shape every golden's own `assumptions` JSON block already declares
    ad hoc -- formalised here, not expanded. `ticket_sizes`/`upi_category_mix`
    are sparse overrides merged on top of the engine's own defaults (a user
    overriding one category's ticket size shouldn't have to restate all
    fifteen). `voucher_utilisation`/`voucher_friction` are single scalars
    applied to every voucher benefit on the card -- no golden has ever
    needed per-voucher differentiation, so this doesn't invent it."""

    primary_routes: dict[str, str] = field(default_factory=dict)  # currency_key -> route_key
    ticket_sizes: dict[str, Decimal] = field(default_factory=dict)  # sparse override
    upi_category_mix: dict[str, Decimal] = field(default_factory=dict)  # sparse override
    category_mcc_map: dict[str, tuple[int, ...]] = field(default_factory=dict)  # sparse override
    voucher_utilisation: Decimal = DEFAULT_UTILISATION
    voucher_friction: Decimal = DEFAULT_FRICTION
    flat_perk_utilisation: Decimal = DEFAULT_UTILISATION
    benefit_need: dict[str, Decimal] = field(default_factory=dict)  # benefit_key -> need
    benefit_unit_value: dict[str, Decimal] = field(default_factory=dict)  # benefit_key -> unit value
    redemptions_per_year: dict[str, Decimal] = field(default_factory=dict)  # currency_key -> count (A.12); sparse, default 1


@dataclass(frozen=True)
class EvaluateResult:
    card_key: str
    gross_reward_value: Decimal
    milestone_value: Decimal  # steady-state
    milestone_value_year1: Decimal
    benefit_value: Decimal
    waiver_achieved: bool
    fee_steady: Decimal
    fee_year1: Decimal
    nacv: NACVResult
    benefit_valuations: dict[str, BenefitValuation]
    flags: tuple[str, ...]


def assumptions_snapshot_from(assumptions: EvaluateAssumptions) -> AssumptionsSnapshot:
    ticket_sizes = dict(DEFAULT_TICKET_SIZES)
    ticket_sizes.update(assumptions.ticket_sizes)
    upi_category_mix = dict(DEFAULT_UPI_CATEGORY_MIX)
    upi_category_mix.update(assumptions.upi_category_mix)
    category_mcc_map = dict(DEFAULT_CATEGORY_MCC_MAP)
    category_mcc_map.update(assumptions.category_mcc_map)
    return AssumptionsSnapshot(ticket_sizes=ticket_sizes, upi_category_mix=upi_category_mix, category_mcc_map=category_mcc_map)


def evaluate_card(
    bundle: CardRuleBundle,
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions | None = None,
) -> EvaluateResult:
    assumptions = assumptions or EvaluateAssumptions()

    # Stage 1
    snapshot = assumptions_snapshot_from(assumptions)
    normalised = normalise(spend, snapshot)
    # Stage 2
    eligible = apply_eligibility(normalised, bundle.exclusions, snapshot.category_mcc_map)

    # Stage 3-4
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    uncapped = accrue_category_mode(bindings, bundle.accruals)

    # Stage 5 -- reward-measure caps and spend-measure incremental bands are
    # disjoint mechanisms over disjoint rule sets (match() already excludes
    # tier_mode="incremental" rules from ordinary binding), so both can run
    # unconditionally and concatenate; each is a no-op when the card has none.
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    spend_caps = tuple(c for c in bundle.caps if c.measure == "spend")
    incremental_rules = tuple(r for r in bundle.earning_rules if r.tier_mode == "incremental")
    capped = apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)
    band_results = apply_incremental_bands(eligible.reward, incremental_rules, spend_caps, bundle.accruals)
    pre_activation = capped + band_results

    # Stage 6-7
    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    final = apply_rule_activations(pre_activation, threshold_events, eligible.reward, bundle.earning_rules, bundle.accruals)

    # Stage 8
    reward_valuations = value_accrual_results(final, bundle.accruals, currencies, assumptions.primary_routes)
    gross_reward_value = sum((v.v_exp_rupees for v in reward_valuations), Decimal("0"))

    # Stage 9
    benefit_valuations: dict[str, BenefitValuation] = {}
    voucher_valuations: dict[str, BenefitValuation] = {}
    benefit_value = Decimal("0")
    for key, benefit in bundle.benefits.items():
        if benefit.kind == "voucher":
            valuation = value_voucher_benefit(benefit, threshold_events, assumptions.voucher_utilisation, assumptions.voucher_friction)
            voucher_valuations[key] = valuation
            benefit_valuations[key] = valuation  # priced into MilestoneValue below, not summed here (avoids double-count)
        elif benefit.kind == "countable":
            need = assumptions.benefit_need.get(key)
            unit_value = assumptions.benefit_unit_value.get(key)
            if need is None or unit_value is None:
                raise ValueError(f"evaluate_card: countable benefit {key!r} needs benefit_need/benefit_unit_value assumptions")
            valuation = value_countable_benefit(benefit, threshold_events, need, unit_value)
            benefit_valuations[key] = valuation
            benefit_value += valuation.value_rupees
        elif benefit.kind == "flat_perk":
            valuation = value_flat_perk_benefit(benefit, assumptions.flat_perk_utilisation)
            benefit_valuations[key] = valuation
            benefit_value += valuation.value_rupees

    # Stage 11's year-mode split (docs/DECISIONS.md #14): an on_renewal
    # grant prices into the steady-state MilestoneValue but not Year-1's.
    steady_milestone, steady_lines, year1_milestone, _year1_lines = value_milestone_grants_by_year_mode(
        threshold_events, currencies, assumptions.primary_routes, voucher_valuations,
    )

    # Stage 10 -- surcharges/forex apply to the RAW spend grid
    # (normalised.segments), not eligible.reward. Part A SS A.10/A.11's own
    # formulas (x(c,k,t) = total category spend) tie these costs to actual
    # spend, never to reward eligibility -- a card can exclude fuel from
    # cashback (Stage 2's "rewards" mask) while still charging its flat
    # fuel surcharge on that exact same spend; the two are independent
    # economics that happen to share a category selector, not one gating
    # the other. Using eligible.reward here was a latent bug (discovered
    # while building Phase 5 Task B's fuel-surcharge-waiver rule for
    # CASHBACK SBI, docs/DECISIONS.md #131) that never fired against any
    # of the 12 synthetic cards because none combines a surcharge/intl
    # selector with a reward exclusion on the same category -- syn_fuel
    # has no exclusions at all, so eligible.reward == normalised.segments
    # for it regardless, and every golden stayed byte-identical after
    # this fix (verified).
    surcharge_result = surcharge_cost(normalised.segments, bundle.surcharges)
    intl_total = international_spend_total(normalised.segments)
    cost_of_forex = forex_cost(intl_total, bundle.forex_markup)
    fees = compute_fees(bundle.joining_fee, bundle.annual_fee, threshold_events)
    cost_of_redemption_fees = redemption_fees_cost(reward_valuations, currencies, assumptions.redemptions_per_year)

    # Stage 11
    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=steady_milestone, benefit_value=benefit_value,
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
        forex_cost=cost_of_forex, surcharge_cost=surcharge_result.total,
        redemption_fees_cost=cost_of_redemption_fees,
        milestone_value_year1=year1_milestone, milestone_lines=steady_lines,
    )

    flags = set()
    flags.update(eligible.flags)
    flags.update(surcharge_result.flags)
    for r in final:
        flags.update(r.flags)
    for v in reward_valuations:
        flags.update(v.flags)
    for v in benefit_valuations.values():
        flags.update(v.flags)

    return EvaluateResult(
        card_key=bundle.card_key,
        gross_reward_value=gross_reward_value,
        milestone_value=steady_milestone,
        milestone_value_year1=year1_milestone,
        benefit_value=benefit_value,
        waiver_achieved=fees.waived,
        fee_steady=fees.steady_fee,
        fee_year1=fees.year1_fee,
        nacv=nacv,
        benefit_valuations=benefit_valuations,
        flags=tuple(sorted(flags)),
    )
