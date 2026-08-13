"""Low/Expected/High spend scenarios and robustness (Part E SS E.11).

    Low/Expected/High = spend vector x {0.8, 1.0, 1.2}

Each is "one more sweep over the *same* candidate set" (SS E.11) --
`run_scenarios` takes the exact `bundles` list the caller already ran
its expected-spend enumeration over (candidates.py's job, not this
module's) and re-enumerates it twice more, at 0.8x and 1.2x spend. No new
financial logic here (CLAUDE.md rule 1): every rupee number is still
produced by `optimiser.enumerate.enumerate_subsets`, which is itself
`optimiser.allocate.allocate` + `optimiser.repair.repair` per subset.

Reported per portfolio (`robustness_for`):

    Robustness = V_low / V_expected        ("keeps 87% of its value if
                                             your spending drops 20%")
    rank stability = does this subset stay in the top-N (default 3) of
                      ALL enumerated subsets, in every one of the three
                      scenarios' own rankings

`low_spend_pv_by_subset_key` packages the Low sweep as the
`dict[subset_key, pv_exact]` map `optimiser.frontier.build_frontier`'s
optional `low_spend_pv_by_subset_key` parameter expects -- SS E.11's own
"feeds T3" line, wired directly.

**Scope for this pass** (docs/DECISIONS.md, Phase 4 scenarios entry):
- **Uniform scalar scaling only.** SS E.11 explicitly defers "per-category
  scenario editing" to later ("later per-category scenario editing") --
  `scale_spend` multiplies every `CategorySpend.annual_amount` (and
  `UpiAggregateSpend.monthly_amount`, if present) by the same factor, not
  a per-category vector. Exactly what the spec asks for at this stage,
  not a narrowing of it.
- **"Warm cache" is read as "same candidate set," not literal caching.**
  `optimiser/enumerate.py`'s own scope note (docs/DECISIONS.md #73/#74)
  already defers subset-level caching to DB persistence that doesn't
  exist yet. This module gets the "don't redo candidate selection three
  times" half of SS E.11's economy for free (the caller passes one
  `bundles` list, reused verbatim across all three sweeps) but each sweep
  is still three independent full solves, not cache hits.
  `expected_results` lets a caller that already ran the expected-spend
  sweep (as every caller of this module will have, since frontier.py
  needs it too) pass those results straight through instead of solving a
  third time.
- **Robustness is `None`, not a divide-by-zero or a misleading ratio,
  when `V_expected <= 0`.** "Keeps X% of its value" presupposes there was
  positive value to keep; a portfolio the optimiser would never actually
  recommend (net-negative NACV) doesn't get a fabricated percentage.
- **Rank stability ranks across the WHOLE enumerated set, not per size.**
  SS E.11 says "stay top-3 across scenarios" without qualifying by size --
  read literally: among every subset the sweep considered, does this
  one's rank stay <= `top_n` in Low, Expected, and High alike.
- **`low_factor`/`high_factor`/rank-stability `top_n` are new
  assumption-registry defaults** (0.8/1.2/3, all three stated directly in
  SS E.11's own text) -- flagged for Satya's sign-off, same posture as
  frontier.py's T1-T3 constants.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from engine.card_bundle import CardRuleBundle
from engine.evaluate import EvaluateAssumptions
from engine.normalise import SpendInput, UpiAggregateSpend
from engine.valuation import RewardCurrency
from optimiser.enumerate import SubsetResult, enumerate_subsets

DEFAULT_LOW_FACTOR = Decimal("0.8")
DEFAULT_HIGH_FACTOR = Decimal("1.2")
DEFAULT_RANK_STABILITY_TOP_N = 3


def scale_spend(spend: SpendInput, factor: Decimal) -> SpendInput:
    category_spend = tuple(
        replace(line, annual_amount=line.annual_amount * factor) for line in spend.category_spend
    )
    upi_aggregate = (
        UpiAggregateSpend(monthly_amount=spend.upi_aggregate.monthly_amount * factor)
        if spend.upi_aggregate is not None else None
    )
    return SpendInput(category_spend=category_spend, upi_aggregate=upi_aggregate)


@dataclass(frozen=True)
class ScenarioSweep:
    low: tuple[SubsetResult, ...]
    expected: tuple[SubsetResult, ...]
    high: tuple[SubsetResult, ...]
    low_factor: Decimal
    high_factor: Decimal


def run_scenarios(
    bundles: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions | None = None,
    cardinality_mode: str = "up_to",
    max_cards: int | None = None,
    low_factor: Decimal = DEFAULT_LOW_FACTOR,
    high_factor: Decimal = DEFAULT_HIGH_FACTOR,
    expected_results: tuple[SubsetResult, ...] | None = None,
) -> ScenarioSweep:
    assumptions = assumptions or EvaluateAssumptions()

    expected = (
        expected_results if expected_results is not None
        else enumerate_subsets(bundles, currencies, spend, assumptions, cardinality_mode, max_cards)
    )
    low = enumerate_subsets(
        bundles, currencies, scale_spend(spend, low_factor), assumptions, cardinality_mode, max_cards,
    )
    high = enumerate_subsets(
        bundles, currencies, scale_spend(spend, high_factor), assumptions, cardinality_mode, max_cards,
    )

    return ScenarioSweep(low=low, expected=expected, high=high, low_factor=low_factor, high_factor=high_factor)


@dataclass(frozen=True)
class PortfolioRobustness:
    subset_key: str
    v_expected: Decimal
    v_low: Decimal
    v_high: Decimal
    robustness: Decimal | None  # V_low / V_expected; None when V_expected <= 0 (undefined, not misleading)
    rank_expected: int
    rank_low: int
    rank_high: int
    rank_stable: bool  # rank <= top_n in every one of the three scenarios


def _by_key(results: Sequence[SubsetResult]) -> dict[str, SubsetResult]:
    return {r.subset_key: r for r in results}


def _rank(subset_key: str, results: Sequence[SubsetResult]) -> int:
    ranked = sorted(results, key=lambda r: r.pv_exact, reverse=True)
    for i, r in enumerate(ranked, start=1):
        if r.subset_key == subset_key:
            return i
    raise ValueError(f"optimiser/scenarios.py: subset {subset_key!r} not found among this scenario's results")


def robustness_for(
    subset_key: str,
    sweep: ScenarioSweep,
    top_n: int = DEFAULT_RANK_STABILITY_TOP_N,
) -> PortfolioRobustness:
    v_expected = _by_key(sweep.expected)[subset_key].pv_exact
    v_low = _by_key(sweep.low)[subset_key].pv_exact
    v_high = _by_key(sweep.high)[subset_key].pv_exact

    robustness = (v_low / v_expected) if v_expected > 0 else None

    rank_expected = _rank(subset_key, sweep.expected)
    rank_low = _rank(subset_key, sweep.low)
    rank_high = _rank(subset_key, sweep.high)
    rank_stable = max(rank_expected, rank_low, rank_high) <= top_n

    return PortfolioRobustness(
        subset_key=subset_key, v_expected=v_expected, v_low=v_low, v_high=v_high, robustness=robustness,
        rank_expected=rank_expected, rank_low=rank_low, rank_high=rank_high, rank_stable=rank_stable,
    )


def low_spend_pv_by_subset_key(sweep: ScenarioSweep) -> dict[str, Decimal]:
    """Feeds `optimiser.frontier.build_frontier`'s T3 (scenario floor)
    directly -- SS E.11's own "feeds T3 above" line."""
    return {r.subset_key: r.pv_exact for r in sweep.low}
