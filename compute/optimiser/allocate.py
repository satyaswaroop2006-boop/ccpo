"""Inner MILP -- fixed-subset spend allocation (Part B SS B.2-B.4, Part E
SS E.4).

Given a set of candidate card bundles (plus the always-available outside
option `c0`, A.11) and the user's spend, decide how to split every rupee
to maximise planning-rate reward value net of surcharge and forex cost.

**Scope for this pass** (docs/DECISIONS.md's Phase 4 entry): continuous
variables only (`x`, `s` per B.2) -- no card-selection binary `y` (the
subset the caller passes IS the input, per B.6/E.4's "y removed" framing
for the inner problem), no milestone/waiver binaries `z`/`w`, no benefit
dedup `l`, no fees. Concave capped/accelerated curves (B.5) are the one
nonlinearity a plain LP already handles correctly by construction (a
maximising LP fills the higher-rate segment first automatically, no
binaries needed). Explicitly out of scope this pass, and raised rather
than silently mismodelled: incremental (convex) `tier_mode` rules (B.5
says these need fill-order binaries), reward-measure caps scoped beyond
`"rule"` (card/rule_group pooling -- not needed by any scenario this pass
targets), and caps on non-monthly windows (quarterly/annual reward-measure
caps need a pooled-across-months segment variable this pass doesn't
build; every reward-measure cap in the current 12-card synthetic catalog
happens to be monthly, so this restriction excludes nothing that exists
today). `activate_rule`-gated rules are simply excluded from matching
(same as `engine.match.match_segment`'s own default
`active_rule_keys=frozenset()` behaviour) -- E.4's own note that
"prospective unlocks are approximated... exact chronology restored by the
evaluator" already anticipates this.

`k` (Part A/B's "spend category") is generalised to the full (category,
channel, geography, merchant_group) tuple the user's declared spend uses,
matching `engine.normalise.SpendSegment`'s own identity -- Part C's rules
already differentiate on all four dimensions (`syn_upi`'s channel rule,
`syn_points`'s merchant_group rule), so collapsing to category alone would
under-model rules the engine already supports (see docs/DECISIONS.md).

Segment compilation deliberately reuses Stage 1-3 wholesale
(`normalise`/`apply_eligibility`/`match`) rather than re-deriving
selector/priority/stacking resolution: which rules bind to a given
category/channel/geography/merchant_group combination doesn't depend on
how much is allocated there, so running the real engine pipeline on each
candidate card (as if its full declared demand were routed there) is both
correct and the only way to guarantee this module can never silently
diverge from the evaluator's own interpretation of a card's rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

import pulp

from engine.accrue import effective_rate
from engine.card_bundle import CardRuleBundle
from engine.costs import GST_RATE
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, assumptions_snapshot_from
from engine.match import match, match_segment, selector_matches
from engine.normalise import NormalisedSpend, SpendInput, normalise
from engine.valuation import RewardCurrency, primary_route_value_per_point

OUTSIDE_OPTION_KEY = "__outside__"
MONTHLY_WINDOW_KINDS = frozenset({"calendar_month", "statement_cycle"})


@dataclass(frozen=True)
class SpendKey:
    category: str
    channel: str | None
    geography: str
    merchant_group: str | None


@dataclass(frozen=True)
class SpendAllocation:
    card_key: str  # OUTSIDE_OPTION_KEY for c0
    key: SpendKey
    month: int
    amount: Decimal


@dataclass(frozen=True)
class AllocationResult:
    status: str  # pulp.LpStatus value: "Optimal" | "Infeasible" | ...
    pv_planned: Decimal
    reward_value: Decimal
    surcharge_cost: Decimal
    forex_cost: Decimal
    allocations: tuple[SpendAllocation, ...]  # nonzero x(c,k,t) only


@dataclass(frozen=True)
class _Segment:
    rate: Decimal  # rupees per rupee spent, planning-rate priced
    cap_amount: Decimal | None  # spend-domain width for this month; None = uncapped


def _spend_key(segment) -> SpendKey:
    return SpendKey(
        category=segment.category, channel=segment.channel,
        geography=segment.geography, merchant_group=segment.merchant_group,
    )


def _rupee_rate(bundle: CardRuleBundle, currencies: dict[str, RewardCurrency], primary_routes: dict[str, str],
                 rule_key: str, ticket_size: Decimal, _cache: dict[str, Decimal]) -> Decimal:
    if rule_key not in _cache:
        points_rate = effective_rate(bundle.accruals[rule_key], ticket_size)
        per_point = primary_route_value_per_point(currencies[bundle.currency_key], primary_routes.get(bundle.currency_key))
        _cache[rule_key] = points_rate * per_point
    return _cache[rule_key]


def _compile_card_pools(
    bundle: CardRuleBundle,
    normalised_segments: Sequence,
    currencies: dict[str, RewardCurrency],
    primary_routes: dict[str, str],
) -> dict[tuple[str, SpendKey], list[list[_Segment]]]:
    """Returns, per (rule_key, k), a list of month-indexed segment chains
    (index 0 = month 1, etc.) -- one chain per RuleBinding pool, since a
    stacking rule and the non-stacking winner are economically independent
    parallel channels on the same underlying spend (each must separately
    sum to x(c,k,t), not jointly)."""
    if any(r.tier_mode == "incremental" for r in bundle.earning_rules):
        raise ValueError(
            f"optimiser/allocate.py: card {bundle.card_key!r} has incremental-tier rules "
            "(Part B SS B.5's convex PWL case, needs fill-order binaries) -- not supported this pass"
        )

    eligible = apply_eligibility(NormalisedSpend(segments=tuple(normalised_segments)), bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)

    reward_caps_by_rule = {}
    for cap in bundle.caps:
        if cap.measure != "reward":
            continue
        if cap.scope != "rule":
            raise ValueError(
                f"optimiser/allocate.py: cap {cap.key!r} on card {bundle.card_key!r} has scope "
                f"{cap.scope!r} -- only scope='rule' reward caps are supported this pass"
            )
        if cap.window.kind not in MONTHLY_WINDOW_KINDS:
            raise ValueError(
                f"optimiser/allocate.py: cap {cap.key!r} on card {bundle.card_key!r} has window kind "
                f"{cap.window.kind!r} -- only monthly-window reward caps are supported this pass"
            )
        if cap.rule_key in reward_caps_by_rule:
            raise ValueError(f"optimiser/allocate.py: multiple reward caps on rule {cap.rule_key!r} not supported")
        reward_caps_by_rule[cap.rule_key] = cap

    rate_cache: dict[str, Decimal] = {}
    pools: dict[tuple[str, SpendKey], dict[int, list[_Segment]]] = {}

    for binding in bindings:
        seg = binding.segment
        k = _spend_key(seg)
        pool_key = (binding.rule_key, k)
        month_chains = pools.setdefault(pool_key, {})
        chain: list[_Segment] = []

        cap = reward_caps_by_rule.get(binding.rule_key)
        if cap is None:
            rate = _rupee_rate(bundle, currencies, primary_routes, binding.rule_key, seg.ticket_size, rate_cache)
            chain.append(_Segment(rate=rate, cap_amount=None))
        else:
            capped_points_rate = effective_rate(bundle.accruals[binding.rule_key], seg.ticket_size)
            capped_rupee_rate = _rupee_rate(bundle, currencies, primary_routes, binding.rule_key, seg.ticket_size, rate_cache)
            cap_width = (cap.amount / capped_points_rate) if capped_points_rate > 0 else Decimal("0")
            chain.append(_Segment(rate=capped_rupee_rate, cap_amount=cap_width))

            overflow_rate = Decimal("0")
            if cap.overflow == "base_rate":
                fallback_rules = [r for r in bundle.earning_rules if r.key != binding.rule_key]
                fallback_bindings = match_segment(seg, fallback_rules)
                fallback_winner = next((b for b in fallback_bindings if not b.stacked), None)
                if fallback_winner is not None:
                    overflow_rate = _rupee_rate(
                        bundle, currencies, primary_routes, fallback_winner.rule_key, seg.ticket_size, rate_cache
                    )
            chain.append(_Segment(rate=overflow_rate, cap_amount=None))  # uncapped completion (rate may be 0)

        month_chains[seg.month] = chain

    return {pool_key: [chains.get(m, []) for m in range(1, 13)] for pool_key, chains in pools.items()}


def allocate(
    bundles: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions | None = None,
    solver: str = "highs",
) -> AllocationResult:
    assumptions = assumptions or EvaluateAssumptions()
    normalised = normalise(spend, assumptions_snapshot_from(assumptions)).segments

    all_keys = sorted(
        {_spend_key(s) for s in normalised},
        key=lambda k: (k.category, k.channel or "", k.geography, k.merchant_group or ""),
    )
    key_index = {k: i for i, k in enumerate(all_keys)}
    demand: dict[tuple[SpendKey, int], Decimal] = {}
    for seg in normalised:
        dk = (_spend_key(seg), seg.month)
        demand[dk] = demand.get(dk, Decimal("0")) + seg.amount

    prob = pulp.LpProblem("allocate", pulp.LpMaximize)

    # x(c,k,t): total spend routed to card c for key k in month t (c0 included).
    card_keys = [b.card_key for b in bundles] + [OUTSIDE_OPTION_KEY]
    x = {
        (c, k, t): prob.add_variable(f"x_{c}_{key_index[k]}_{t}", lowBound=0)
        for c in card_keys for k in all_keys for t in range(1, 13)
    }

    objective_terms: list = []
    surcharge_terms: list = []
    forex_terms: list = []

    for bundle in bundles:
        pools = _compile_card_pools(bundle, normalised, currencies, assumptions.primary_routes)

        # Segment-sum constraints: each rule-pool's segment spend sums
        # exactly to x(c,k,t) -- a stacking rule and the non-stacking
        # winner are independent pools referencing the same x (B.4's
        # "per rule-group" framing).
        pools_by_key: dict[SpendKey, list[tuple[str, list[list[_Segment]]]]] = {}
        for (rule_key, k), month_chains in pools.items():
            pools_by_key.setdefault(k, []).append((rule_key, month_chains))

        for k, rule_pools in pools_by_key.items():
            for rule_key, month_chains in rule_pools:
                for t in range(1, 13):
                    chain = month_chains[t - 1]
                    if not chain:
                        continue
                    s_vars = [
                        prob.add_variable(
                            f"s_{bundle.card_key}_{rule_key}_{key_index[k]}_{t}_{i}", lowBound=0,
                            upBound=float(seg.cap_amount) if seg.cap_amount is not None else None,
                        )
                        for i, seg in enumerate(chain)
                    ]
                    prob += pulp.lpSum(s_vars) == x[(bundle.card_key, k, t)]
                    objective_terms.extend(float(seg.rate) * var for seg, var in zip(chain, s_vars))

        # Any k with no rule-pool at all on this card earns nothing --
        # x(c,k,t) is still a valid (zero-reward) routing destination,
        # just never one the LP will use unless demand forces it to.

        for k in all_keys:
            rep_segment = next((s for s in normalised if _spend_key(s) == k), None)
            if rep_segment is None:
                continue
            surcharge_rate = sum(
                (sur.rate * (1 + sur.gst_on_surcharge) for sur in bundle.surcharges if selector_matches(sur.selector, rep_segment)),
                Decimal("0"),
            )
            if surcharge_rate:
                for t in range(1, 13):
                    surcharge_terms.append(float(surcharge_rate) * x[(bundle.card_key, k, t)])
            if k.geography == "international" and bundle.forex_markup:
                forex_rate = bundle.forex_markup * (1 + GST_RATE)
                for t in range(1, 13):
                    forex_terms.append(float(forex_rate) * x[(bundle.card_key, k, t)])

    prob += pulp.lpSum(objective_terms) - pulp.lpSum(surcharge_terms) - pulp.lpSum(forex_terms)

    # Demand: every rupee of declared spend is routed somewhere (a real
    # card in the subset, or c0).
    for k in all_keys:
        for t in range(1, 13):
            prob += pulp.lpSum(x[(c, k, t)] for c in card_keys) == float(demand.get((k, t), Decimal("0")))

    status = _solve(prob, solver)

    def _resolved(terms: list) -> Decimal:
        return Decimal(str(round(pulp.value(pulp.lpSum(terms)) or 0.0, 2))) if terms else Decimal("0")

    reward_value = _resolved(objective_terms)
    surcharge_total = _resolved(surcharge_terms)
    forex_total = _resolved(forex_terms)
    pv_planned = reward_value - surcharge_total - forex_total

    allocations = []
    for (c, k, t), var in x.items():
        amount = var.value()
        if amount and amount > 1e-9:
            allocations.append(SpendAllocation(card_key=c, key=k, month=t, amount=Decimal(str(round(amount, 2)))))

    return AllocationResult(
        status=status, pv_planned=pv_planned, reward_value=reward_value,
        surcharge_cost=surcharge_total, forex_cost=forex_total,
        allocations=tuple(allocations),
    )


# PULP_CBC_CMD, not its suggested replacement COIN_CMD: verified directly
# in this environment that COIN_CMD can't locate cbc.exe (PulpSolverError)
# while PULP_CBC_CMD works (confirmed by test_solver_fallback_to_cbc_
# produces_the_same_result) -- a deprecation warning is an acceptable
# trade for a fallback path that's actually verified to work.
def _solve(prob: pulp.LpProblem, solver: str = "highs") -> str:
    if solver == "cbc":
        prob.solve(pulp.PULP_CBC_CMD(msg=False))
        return pulp.LpStatus[prob.status]
    try:
        prob.solve(pulp.HiGHS(msg=False))
    except Exception:
        prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return pulp.LpStatus[prob.status]
