"""Candidate selection -- pre-filtering the card universe (Part E SS E.2,
Part B SS B.7).

Enumeration (`optimiser/enumerate.py`) can only afford ~12-15 candidates.
SS B.7 warns naive pre-filtering by standalone value alone is biased --
"it drops specialist cards (zero-forex, UPI, fuel) whose standalone value
is low but whose incremental value inside a portfolio is high." The fix
SS B.7 specifies is a union of three things: top-N standalone value,
per-category champions, and user-constraint-required cards. This module
builds the first two -- the actual coverage-guarantee mechanism SS B.7
names -- and dedupes/trims per SS E.2 step 6.

**Scope for this pass** (docs/DECISIONS.md's Phase 4 slice 4 entry): no
MABC (SS E.2 step 5 -- a refinement SS B.7 itself doesn't mention, needing
a "force eligible spend to exactly this tier" evaluation construct that
doesn't exist anywhere), no hard include/exclude from wallet or user-
constraint state (neither exists yet), no `optimisation_runs.
constraints_snapshot` persistence (needs DB writes nothing currently
does) -- the "why was card X considered" explainability this module
already produces (`CandidateSelection.standalone_ranked`/`.champions`) is
simply returned in-memory rather than written to a row.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.card_bundle import CardRuleBundle
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.normalise import CategorySpend, SpendInput
from engine.valuation import RewardCurrency
from optimiser.allocate import allocate
from optimiser.repair import repair

DEFAULT_STANDALONE_N = 8
DEFAULT_CHAMPION_CATEGORY_THRESHOLD = Decimal("25000")
DEFAULT_CHAMPION_TOP_N = 2
DEFAULT_CHAMPION_DELTA = Decimal("10000")
DEFAULT_MAX_TOTAL = 18


@dataclass(frozen=True)
class CategoryChampion:
    category: str
    annual_amount: Decimal
    card_key: str
    marginal_rate: Decimal


@dataclass(frozen=True)
class CandidateSelection:
    candidates: tuple[str, ...]  # final set, trimmed
    standalone_ranked: tuple[tuple[str, Decimal], ...]  # (card_key, pv_exact), full ranking
    champions: tuple[CategoryChampion, ...]  # every champion pick with its reason


def _standalone_value(
    bundle: CardRuleBundle, currencies: dict[str, RewardCurrency],
    spend: SpendInput, assumptions: EvaluateAssumptions,
) -> Decimal:
    """SS B.7 step 1's "cheap single-card LP" -- deliberately allocate()
    + repair(), not a raw evaluate_card() with all spend forced onto the
    card: tests/test_allocate.py's own syn_fuel scenario already proved a
    single card's true standalone value can route part of the user's
    spend to c0 when that card's margin on some category is negative, and
    that only happens through the LP + outside option."""
    allocation = allocate([bundle], currencies, spend, assumptions)
    return repair([bundle], currencies, allocation, assumptions).valuation.pv_exact


def _category_lines(spend: SpendInput, category: str) -> tuple[CategorySpend, ...]:
    return tuple(line for line in spend.category_spend if line.category == category)


def _marginal_rate(
    bundle: CardRuleBundle, currencies: dict[str, RewardCurrency], category: str,
    baseline_lines: tuple[CategorySpend, ...], delta: Decimal, assumptions: EvaluateAssumptions,
) -> Decimal:
    """Part A SS A.15's MV(c,k,delta) = Evaluator(baseline+delta) -
    Evaluator(baseline), isolated to just this category's own spend lines
    (SS B.7: "net marginal rate on that category alone") -- no allocate()/
    repair() needed, since evaluating one category on one card in
    isolation is not a cross-card allocation decision; a negative rate is
    itself a valid, correctly-informative signal. The card's fixed annual
    fee cancels exactly in this subtraction regardless of whether it's
    waived at either amount, which is exactly why a marginal delta (not
    an average inclusive of fixed costs) is the right formula here."""
    baseline = evaluate_card(bundle, currencies, SpendInput(category_spend=baseline_lines), assumptions)
    bumped_lines = baseline_lines + (CategorySpend(category=category, annual_amount=delta),)
    bumped = evaluate_card(bundle, currencies, SpendInput(category_spend=bumped_lines), assumptions)
    return (bumped.nacv.steady_state - baseline.nacv.steady_state) / delta


def select_candidates(
    universe: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions | None = None,
    standalone_n: int = DEFAULT_STANDALONE_N,
    champion_category_threshold: Decimal = DEFAULT_CHAMPION_CATEGORY_THRESHOLD,
    champion_top_n: int = DEFAULT_CHAMPION_TOP_N,
    champion_delta: Decimal = DEFAULT_CHAMPION_DELTA,
    max_total: int = DEFAULT_MAX_TOTAL,
) -> CandidateSelection:
    assumptions = assumptions or EvaluateAssumptions()

    standalone_scores = [
        (bundle.card_key, _standalone_value(bundle, currencies, spend, assumptions))
        for bundle in universe
    ]
    standalone_scores.sort(key=lambda item: item[1], reverse=True)
    standalone_top = {key for key, _value in standalone_scores[:standalone_n]}

    category_totals: dict[str, Decimal] = {}
    for line in spend.category_spend:
        category_totals[line.category] = category_totals.get(line.category, Decimal("0")) + line.annual_amount

    champions: list[CategoryChampion] = []
    champion_keys: set[str] = set()
    for category, total in category_totals.items():
        if total <= champion_category_threshold:
            continue
        baseline_lines = _category_lines(spend, category)
        rates = [
            (bundle.card_key, _marginal_rate(bundle, currencies, category, baseline_lines, champion_delta, assumptions))
            for bundle in universe
        ]
        rates.sort(key=lambda item: item[1], reverse=True)
        for card_key, rate in rates[:champion_top_n]:
            champions.append(CategoryChampion(category=category, annual_amount=total, card_key=card_key, marginal_rate=rate))
            champion_keys.add(card_key)

    union = standalone_top | champion_keys

    if len(union) > max_total:
        # trim from the standalone-only tail (worst-ranked first);
        # champions are never dropped (SS E.2 step 6: "coverage guarantee
        # outranks list size").
        worst_first_standalone_only = [
            key for key, _value in reversed(standalone_scores)
            if key in union and key not in champion_keys
        ]
        to_drop = set(worst_first_standalone_only[: len(union) - max_total])
        union = union - to_drop

    ordered = [key for key, _value in standalone_scores if key in union]
    for key in champion_keys:
        if key not in ordered:
            ordered.append(key)

    return CandidateSelection(
        candidates=tuple(ordered),
        standalone_ranked=tuple(standalone_scores),
        champions=tuple(champions),
    )
