"""Exact evaluation + near-miss repair (Part E SS E.1 steps 5-6, SS E.7;
Part A SS A.16; Part B SS B.10.4).

Two responsibilities:

1. `evaluate_allocation` -- E.1 step 5 ("EVALUATE exact engine run per
   surviving subset"): translates `optimiser.allocate`'s `x(c,k,t)`
   solution into per-card spend and runs Phase 3's `engine.evaluate.
   evaluate_card` on each, summing steady-state NACV to `pv_exact`. This
   is the ONLY place this module computes a rupee value -- CLAUDE.md
   rule 1, same as `optimiser/allocate.py`.
2. `repair` -- E.7's near-miss variant search: for every threshold
   breakpoint `engine.breakpoints` already compiles from a card's own
   rules, checks whether the allocation's current pooled spend for that
   threshold falls just short (within `buffer(beta)`) and, if so, tries
   topping it up from the outside option `c0`'s allocation to see if
   crossing it beats the original.

**Scope for this pass** (docs/DECISIONS.md's Phase 4 slice 2 entry): Part
A SS A.16 / Part B SS B.10.4's repair rule ("fix milestone/waiver binaries
to their evaluator-verified states, re-allocate once") assumes the MILP
already has those binaries -- `allocate.py`'s slice-1 LP doesn't model
milestones, waivers, or fees at all, so there's nothing to "fix" in that
sense. This module instead builds E.7's two-part description directly:
exact evaluation (always) and near-miss-only threshold repair (the LP is
*structurally blind* to thresholds -- no milestone/waiver terms in its
objective at all -- so this is currently the only mechanism that can
capture threshold value, not a refinement of an otherwise-complete model).

Two further narrowings, each because the alternative needs a materially
bigger design this pass doesn't need yet:
- **Near-miss only, not "barely-made."** Barely-made (pulling back spend
  that's just cleared a *cap* boundary, in case it earns more elsewhere)
  is already handled optimally by the LP's own segment structure (B.5: a
  maximising LP fills the higher-rate segment first) -- it's specifically
  *threshold* breakpoints the LP can't see, and thresholds only ever add
  value once crossed, never make crossing them worse, so near-miss (push
  under-threshold spend over) is the case with real LP-invisible upside.
  Cap breakpoints aren't even compiled here (`CardBreakpointInputs.caps`
  is left empty) for this reason.
- **Top-up sourced from `c0` only, never from another real card's
  allocation.** Moving spend FROM `c0` is always safe (c0 earns nothing,
  so there's no opportunity cost to weigh) -- matches C.0's own "top-up...
  spend to cross T(beta)" phrasing exactly. Pulling from a real card
  instead needs a cost model for what's given up there; deferred.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from engine.breakpoints import Breakpoint, CardBreakpointInputs, compile_card_breakpoints
from engine.card_bundle import CardRuleBundle
from engine.caps import window_instances
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, EvaluateResult, evaluate_card
from engine.normalise import CategorySpend, NormalisedSpend, SpendInput, SpendSegment
from engine.valuation import RewardCurrency
from optimiser.allocate import OUTSIDE_OPTION_KEY, AllocationResult, SpendAllocation, SpendKey

_PLACEHOLDER_TICKET_SIZE = Decimal("1")  # eligibility/exclusion matching never reads ticket_size


@dataclass(frozen=True)
class PortfolioValuation:
    pv_exact: Decimal
    card_results: dict[str, EvaluateResult]


@dataclass(frozen=True)
class RepairResult:
    allocation: AllocationResult
    valuation: PortfolioValuation
    repair_applied: bool
    gap: Decimal  # |pv_planned - pv_exact| / pv_exact of the ORIGINAL allocation; a health metric, not a gate
    variants_tried: int


def _spend_input_for_card(allocation: AllocationResult, card_key: str) -> SpendInput:
    by_key: dict[SpendKey, dict[int, Decimal]] = {}
    for a in allocation.allocations:
        if a.card_key != card_key:
            continue
        months = by_key.setdefault(a.key, {})
        months[a.month] = months.get(a.month, Decimal("0")) + a.amount

    lines = []
    for key, month_amounts in by_key.items():
        annual = sum(month_amounts.values(), Decimal("0"))
        if annual <= 0:
            continue
        weights = tuple(month_amounts.get(m, Decimal("0")) / annual for m in range(1, 13))
        lines.append(CategorySpend(
            category=key.category, channel=key.channel, annual_amount=annual,
            seasonality=weights, geography=key.geography, merchant_group=key.merchant_group,
        ))
    return SpendInput(category_spend=tuple(lines))


def evaluate_allocation(
    bundles: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    allocation: AllocationResult,
    assumptions: EvaluateAssumptions | None = None,
) -> PortfolioValuation:
    assumptions = assumptions or EvaluateAssumptions()
    bundles_by_key = {b.card_key: b for b in bundles}

    card_results: dict[str, EvaluateResult] = {}
    total = Decimal("0")
    for card_key in {a.card_key for a in allocation.allocations if a.card_key != OUTSIDE_OPTION_KEY}:
        spend_input = _spend_input_for_card(allocation, card_key)
        if not spend_input.category_spend:
            continue
        result = evaluate_card(bundles_by_key[card_key], currencies, spend_input, assumptions)
        card_results[card_key] = result
        total += result.nacv.steady_state

    return PortfolioValuation(pv_exact=total, card_results=card_results)


def _card_segments(allocation: AllocationResult, card_key: str) -> tuple[SpendSegment, ...]:
    return tuple(
        SpendSegment(
            category=a.key.category, channel=a.key.channel, month=a.month, amount=a.amount,
            ticket_size=_PLACEHOLDER_TICKET_SIZE, merchant_group=a.key.merchant_group, geography=a.key.geography,
        )
        for a in allocation.allocations if a.card_key == card_key
    )


def pooled_spend_per_instance(
    allocation: AllocationResult, card_key: str, exclusions, bp: Breakpoint,
) -> list[tuple[tuple[int, ...], Decimal]]:
    eligible = apply_eligibility(NormalisedSpend(segments=_card_segments(allocation, card_key)), exclusions)
    view = eligible.milestone if bp.measure == "milestone_eligible_spend" else eligible.waiver
    results = []
    for instance_months in window_instances(bp.window):
        month_set = set(instance_months)
        pooled = sum((s.amount for s in view if s.month in month_set), Decimal("0"))
        results.append((instance_months, pooled))
    return results


def _topup_from_outside(
    allocation: AllocationResult, card_key: str, exclusions, measure: str,
    instance_months: tuple[int, ...], gap: Decimal,
) -> AllocationResult | None:
    """Tries to cover `gap` rupees entirely from c0's allocation within
    `instance_months`, restricted to keys that would actually count
    toward `measure` on `card_key` (i.e. not excluded there). A partial
    cover is useless (thresholds are step functions -- no partial credit
    for almost crossing one) and is never returned."""
    month_set = set(instance_months)
    candidates = [
        a for a in allocation.allocations
        if a.card_key == OUTSIDE_OPTION_KEY and a.month in month_set and a.amount > 0
    ]

    eligible_candidates = []
    for a in candidates:
        fake_segment = SpendSegment(
            category=a.key.category, channel=a.key.channel, month=a.month, amount=a.amount,
            ticket_size=_PLACEHOLDER_TICKET_SIZE, merchant_group=a.key.merchant_group, geography=a.key.geography,
        )
        eligible = apply_eligibility(NormalisedSpend(segments=(fake_segment,)), exclusions)
        view = eligible.milestone if measure == "milestone_eligible_spend" else eligible.waiver
        if view:
            eligible_candidates.append(a)
    eligible_candidates.sort(key=lambda a: a.amount, reverse=True)

    remaining = gap
    moves: list[tuple[SpendAllocation, Decimal]] = []
    for a in eligible_candidates:
        if remaining <= 0:
            break
        move = min(a.amount, remaining)
        moves.append((a, move))
        remaining -= move

    if remaining > 0:
        return None  # c0 couldn't fully cover the gap -- a partial top-up wouldn't cross the threshold anyway

    new_allocations = list(allocation.allocations)
    for source, move in moves:
        idx = new_allocations.index(source)
        remainder = source.amount - move
        new_allocations[idx] = replace(source, amount=remainder) if remainder > 0 else None
        new_allocations.append(SpendAllocation(card_key=card_key, key=source.key, month=source.month, amount=move))
    new_allocations = [a for a in new_allocations if a is not None]

    return replace(allocation, allocations=tuple(new_allocations))


def repair(
    bundles: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    allocation: AllocationResult,
    assumptions: EvaluateAssumptions | None = None,
) -> RepairResult:
    assumptions = assumptions or EvaluateAssumptions()

    baseline_valuation = evaluate_allocation(bundles, currencies, allocation, assumptions)
    gap = (
        abs(allocation.pv_planned - baseline_valuation.pv_exact) / baseline_valuation.pv_exact
        if baseline_valuation.pv_exact != 0 else Decimal("0")
    )

    best_allocation, best_valuation = allocation, baseline_valuation
    repair_applied = False
    variants_tried = 0

    for bundle in bundles:
        breakpoints = compile_card_breakpoints(CardBreakpointInputs(card_key=bundle.card_key, thresholds=bundle.thresholds))
        for bp in breakpoints:
            for instance_months, pooled in pooled_spend_per_instance(allocation, bundle.card_key, bundle.exclusions, bp):
                gap_to_cross = bp.threshold_spend - pooled
                if not (Decimal("0") < gap_to_cross <= bp.buffer):
                    continue

                variant_allocation = _topup_from_outside(
                    allocation, bundle.card_key, bundle.exclusions, bp.measure, instance_months, gap_to_cross,
                )
                if variant_allocation is None:
                    continue

                variants_tried += 1
                variant_valuation = evaluate_allocation(bundles, currencies, variant_allocation, assumptions)
                if variant_valuation.pv_exact > best_valuation.pv_exact:
                    best_allocation, best_valuation = variant_allocation, variant_valuation
                    repair_applied = True

    return RepairResult(
        allocation=best_allocation, valuation=best_valuation,
        repair_applied=repair_applied, gap=gap, variants_tried=variants_tried,
    )
