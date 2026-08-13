"""Portfolio generation -- subset enumeration (Part E SS E.3).

For a candidate card list and cardinality bounds, generates every subset
within those bounds and runs `optimiser.allocate.allocate` then
`optimiser.repair.repair` on each -- pure orchestration over the two
already-built per-subset primitives, no new financial logic of its own
(CLAUDE.md rule 1).

**Scope for this pass** (docs/DECISIONS.md's Phase 4 slice 3 entry):
plain full-sweep enumeration only. Each of the following is a real
follow-up module's job, not an oversight:
- Wallet mode's mandatory current-portfolio inclusion -- wallet mode
  itself doesn't exist yet (docs/DECISIONS.md #10/#61).
- Structural infeasibility filtering (fee budget, network requirements,
  must-keep/refuse-use) -- Part B SS B.4(8)'s user-rule constraints
  aren't modelled anywhere yet; every subset is tried.
- Bound pruning (SS E.3's admissible-upper-bound skip rule) -- needs a
  MABC-style per-card ceiling (SS E.2's job, not built); the spec frames
  this as a scale optimisation irrelevant at today's <=12-card catalog,
  and full-sweep (no pruning) is already a spec-sanctioned mode on its own.
- Caching (`subset_key + spend hash + rule versions + assumptions
  version`) -- needs `portfolio_subset_results` persistence, which needs
  DB-backed writes nothing currently does.
- Parallelism -- a performance concern, not correctness.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Sequence

from engine.card_bundle import CardRuleBundle
from engine.evaluate import EvaluateAssumptions, EvaluateResult
from engine.normalise import SpendInput
from engine.valuation import RewardCurrency
from optimiser.allocate import AllocationResult, allocate
from optimiser.repair import repair

VALID_CARDINALITY_MODES = frozenset({"exactly", "up_to", "optimiser_decides"})


@dataclass(frozen=True)
class SubsetResult:
    subset_key: str  # sorted card keys, "+"-joined -- Part D's portfolio_subset_results.subset_key convention
    card_keys: tuple[str, ...]
    size: int
    pv_planned: Decimal
    pv_exact: Decimal
    repair_applied: bool
    gap: Decimal
    allocation: AllocationResult  # the winning (possibly repaired) allocation
    card_results: dict[str, EvaluateResult]  # per-card exact evaluation, reused by frontier.py's T2 fee-cover test


def enumerate_subsets(
    bundles: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions | None = None,
    cardinality_mode: str = "up_to",
    max_cards: int | None = None,
) -> tuple[SubsetResult, ...]:
    if cardinality_mode not in VALID_CARDINALITY_MODES:
        raise ValueError(
            f"optimiser/enumerate.py: unknown cardinality_mode {cardinality_mode!r}; "
            f"valid modes are {sorted(VALID_CARDINALITY_MODES)}"
        )
    assumptions = assumptions or EvaluateAssumptions()

    sorted_bundles = tuple(sorted(bundles, key=lambda b: b.card_key))
    if max_cards is None:
        max_cards = len(sorted_bundles)

    sizes = [max_cards] if cardinality_mode == "exactly" else list(range(1, max_cards + 1))

    results: list[SubsetResult] = []
    for size in sizes:
        for subset in combinations(sorted_bundles, size):
            card_keys = tuple(b.card_key for b in subset)
            subset_key = "+".join(card_keys)

            allocation = allocate(list(subset), currencies, spend, assumptions)
            repaired = repair(list(subset), currencies, allocation, assumptions)

            results.append(SubsetResult(
                subset_key=subset_key, card_keys=card_keys, size=size,
                pv_planned=allocation.pv_planned, pv_exact=repaired.valuation.pv_exact,
                repair_applied=repaired.repair_applied, gap=repaired.gap,
                allocation=repaired.allocation, card_results=repaired.valuation.card_results,
            ))

    return tuple(results)
