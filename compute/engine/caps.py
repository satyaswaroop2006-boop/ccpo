"""Stage 5 -- CAP (Part C SS C.2.3, SS C.2.4, SS C.4 Stage 5).

Applies reward-measure caps to Stage 4's accrual results: every window
instance (a calendar month, a quarter, or the whole modelling year) where a
cap's scope pools more reward than `amount` gets trimmed to exactly
`amount`, in chronological month order -- months before the running total
crosses the cap keep their full reward; the month that crosses it is
trimmed to whatever budget remains; every month after is fully overflow.
This is A.3's concave reward curve, generalised from "one segment" to "any
number of months pooled by scope and window."

Caps with multiple granularities on the same rule (a monthly cap AND a
yearly cap) compose via the pipeline's documented nesting order -- finer
windows are applied first, and coarser caps see the finer ones' results,
per C.4's "Apply caps in nesting order txn -> month -> quarter -> year."

`scope` pools which rules' results count toward one cap:
  - "rule": just the rule the cap is declared on
  - "rule_group:<key>": every rule sharing that rule_group tag (plus the
    declaring rule itself, even if untagged, so the cap is never emptied by
    a catalog that only tags some group members)
  - "card": every rule on the card

`overflow: "base_rate"` doesn't hardcode which rule is "base" -- for the
month that crosses the cap, it re-runs Stage 3's match on that month's
segment with every pooled rule excluded, and uses whichever non-stacking
rule wins. `overflow: "zero"` just discards the excess.

`apply_caps` (this stage's main entry point) handles measure="reward" only.
"spend"-measure caps are syn_slab's incremental-band mechanic (A.3's
convex-PWL case: several same-selector rules, increasing rates, each
owning a *slice* of one pooled spend total rather than competing for all
of it) -- handled by the separate `apply_incremental_bands` below, since
it's a genuinely different mechanic from an ordinary reward ceiling, not
a variant of one. A cap window that pools more than one distinct category/
channel combination raises rather than guessing how to attribute the
overflow across them -- no current synthetic card's ordinary cap does
this, and it needs its own design pass.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from engine.accrue import Accrual, AccrualResult, accrue_transaction
from engine.match import EarningRule, match_segment, selector_matches
from engine.normalise import SpendSegment

VALID_MEASURE = frozenset({"reward"})
VALID_WINDOW_KINDS = frozenset({"calendar_month", "quarter", "calendar_year", "anniversary_year", "statement_cycle"})
VALID_OVERFLOW = frozenset({"base_rate", "zero"})

# C.4's nesting order: finer windows resolve before coarser ones, so a
# coarser cap on the same rule sees the finer cap's already-trimmed results.
_GRANULARITY_RANK = {
    "calendar_month": 0,
    "statement_cycle": 0,
    "quarter": 1,
    "calendar_year": 2,
    "anniversary_year": 2,
}


@dataclass(frozen=True)
class Window:
    kind: str
    alignment: str | None = None  # "quarter" only: "calendar" | "anniversary"


@dataclass(frozen=True)
class Cap:
    key: str
    rule_key: str  # the rule this cap is declared on (IS the pool for scope="rule")
    measure: str
    amount: Decimal
    window: Window
    scope: str  # "rule" | "rule_group:<key>" | "card"
    overflow: str


def _validate_cap(cap: Cap) -> None:
    if cap.measure not in VALID_MEASURE:
        raise ValueError(f"cap {cap.key!r}: measure {cap.measure!r} not supported yet (only {sorted(VALID_MEASURE)})")
    if cap.window.kind not in VALID_WINDOW_KINDS:
        raise ValueError(f"cap {cap.key!r}: unknown window kind {cap.window.kind!r}")
    if cap.overflow not in VALID_OVERFLOW:
        raise ValueError(f"cap {cap.key!r}: unknown overflow mode {cap.overflow!r}")
    if cap.scope != "rule" and cap.scope != "card" and not cap.scope.startswith("rule_group:"):
        raise ValueError(f"cap {cap.key!r}: unknown scope {cap.scope!r}")


def window_instances(window: Window) -> tuple[tuple[int, ...], ...]:
    if window.kind in ("calendar_month", "statement_cycle"):
        return tuple((m,) for m in range(1, 13))
    if window.kind == "quarter":
        return ((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12))
    if window.kind in ("calendar_year", "anniversary_year"):
        return (tuple(range(1, 13)),)
    raise ValueError(f"unknown window kind {window.kind!r}")


def window_flags(window: Window) -> tuple[str, ...]:
    flags = []
    if window.kind == "statement_cycle":
        flags.append("cycle_approximated")
    if window.kind == "anniversary_year" or (window.kind == "quarter" and window.alignment == "anniversary"):
        flags.append("anniversary_approximated")
    return tuple(flags)


def _scope_rule_keys(cap: Cap, earning_rules: Sequence[EarningRule]) -> set[str]:
    if cap.scope == "rule":
        return {cap.rule_key}
    if cap.scope == "card":
        return {r.key for r in earning_rules}
    group = cap.scope.split(":", 1)[1]
    keys = {r.key for r in earning_rules if r.rule_group == group}
    keys.add(cap.rule_key)
    return keys


def flat_rate(accrual: Accrual) -> Decimal:
    """The continuous (unfloored) rupee rate `a`/`b` of A.3's curve."""
    if accrual.type == "percentage":
        return accrual.rate
    return accrual.points_per_unit / accrual.unit_amount


def _apply_one_cap(
    results: Sequence[AccrualResult],
    cap: Cap,
    earning_rules: Sequence[EarningRule],
    accruals: dict[str, Accrual],
) -> tuple[AccrualResult, ...]:
    pool_rule_keys = _scope_rule_keys(cap, earning_rules)
    flags_for_window = window_flags(cap.window)

    capped_reward_by_index: dict[int, Decimal] = {}
    overflow_extra: list[AccrualResult] = []

    for instance_months in window_instances(cap.window):
        month_set = set(instance_months)
        indices = [i for i, r in enumerate(results) if r.rule_key in pool_rule_keys and r.segment.month in month_set]
        if not indices:
            continue
        total_reward = sum((results[i].reward for i in indices), Decimal("0"))
        if total_reward <= cap.amount:
            continue

        categories_present = {(results[i].segment.category, results[i].segment.channel) for i in indices}
        if len(categories_present) > 1:
            raise ValueError(
                f"cap {cap.key!r}: window {instance_months} pools {len(categories_present)} distinct "
                "category/channel combinations while binding; multi-category pooled caps aren't supported yet"
            )

        running_total = Decimal("0")
        for i in sorted(indices, key=lambda i: results[i].segment.month):
            original = results[i]
            if running_total + original.reward <= cap.amount:
                running_total += original.reward
                continue  # fully within budget -- unchanged

            allowed = max(cap.amount - running_total, Decimal("0"))
            capped_reward_by_index[i] = allowed
            excess_reward = original.reward - allowed
            running_total = cap.amount

            if cap.overflow == "base_rate" and excess_reward > 0:
                rate = flat_rate(accruals[original.rule_key])
                excess_spend = min(excess_reward / rate, original.segment.amount)
                if excess_spend > 0:
                    fallback_rules = [r for r in earning_rules if r.key not in pool_rule_keys]
                    fallback_bindings = match_segment(original.segment, fallback_rules)
                    fallback_winner = next((b for b in fallback_bindings if not b.stacked), None)
                    if fallback_winner is not None:
                        fallback_accrual = accruals[fallback_winner.rule_key]
                        overflow_reward = accrue_transaction(fallback_accrual, excess_spend)
                        overflow_segment = replace(original.segment, amount=excess_spend)
                        overflow_extra.append(
                            AccrualResult(
                                rule_key=fallback_winner.rule_key,
                                segment=overflow_segment,
                                reward=overflow_reward,
                                flags=("cap_overflow",) + flags_for_window,
                            )
                        )
            # overflow == "zero": excess reward is simply discarded.

    new_results = [
        replace(r, reward=capped_reward_by_index[i], flags=r.flags + tuple(f for f in flags_for_window if f not in r.flags))
        if i in capped_reward_by_index else r
        for i, r in enumerate(results)
    ]
    new_results.extend(overflow_extra)
    return tuple(new_results)


def apply_caps(
    accrual_results: Sequence[AccrualResult],
    caps: Sequence[Cap],
    earning_rules: Sequence[EarningRule],
    accruals: dict[str, Accrual],
) -> tuple[AccrualResult, ...]:
    for cap in caps:
        _validate_cap(cap)

    results: tuple[AccrualResult, ...] = tuple(accrual_results)
    for cap in sorted(caps, key=lambda c: _GRANULARITY_RANK[c.window.kind]):
        results = _apply_one_cap(results, cap, earning_rules, accruals)
    return results


# ---------------------------------------------------------------------------
# Incremental bands (A.3's convex-PWL case; C.9 Example 7, syn_slab)
# ---------------------------------------------------------------------------

def _validate_incremental_cap(cap: Cap) -> None:
    if cap.measure != "spend":
        raise ValueError(f"cap {cap.key!r}: incremental bands need measure='spend', got {cap.measure!r}")
    if cap.window.kind not in VALID_WINDOW_KINDS:
        raise ValueError(f"cap {cap.key!r}: unknown window kind {cap.window.kind!r}")
    if cap.scope != "rule":
        raise ValueError(f"cap {cap.key!r}: incremental band caps only support scope='rule', got {cap.scope!r}")


def apply_incremental_bands(
    reward_segments: Sequence[SpendSegment],
    band_rules: Sequence[EarningRule],
    caps: Sequence[Cap],
    accruals: dict[str, Accrual],
) -> tuple[AccrualResult, ...]:
    """Fills each band in descending-priority order (syn_slab: 1% up to
    Rs1L, then 2% for the next Rs2L, then 3% uncapped) from one pooled
    spend total, per A.3's concave/convex band curve. A band without a
    matching cap in `caps` is treated as uncapped -- gets whatever spend
    remains after every higher-priority band has taken its share; this
    should only ever be the LOWEST-priority band (syn_slab's slab3), but
    nothing here enforces that ordering assumption beyond the fill loop
    itself naturally leaving nothing for any band ordered after an
    uncapped one.

    Every band rule must share an identical selector (they're slices of
    ONE spend pool, not independently-targeted rules) and every capped
    band must share one window -- both raise otherwise.

    Reward per band is computed by treating the whole band's spend as one
    aggregate amount through accrue_transaction -- mathematically identical
    to `floor_on_aggregate` regardless of the rule's own rounding string,
    since flooring a single aggregate amount to the paisa is the same
    operation either way.
    """
    if not band_rules:
        return ()

    shared_selector = band_rules[0].selector
    if any(r.selector != shared_selector for r in band_rules):
        raise ValueError("incremental band rules must share an identical selector")

    for cap in caps:
        _validate_incremental_cap(cap)
    caps_by_rule = {c.rule_key: c for c in caps}

    capped_windows = {caps_by_rule[r.key].window for r in band_rules if r.key in caps_by_rule}
    if len(capped_windows) != 1:
        raise ValueError("incremental band group needs exactly one shared window across its capped bands")
    window = next(iter(capped_windows))

    ordered_rules = sorted(band_rules, key=lambda r: r.priority, reverse=True)
    matching_segments = [s for s in reward_segments if selector_matches(shared_selector, s)]

    results: list[AccrualResult] = []
    for instance_months in window_instances(window):
        month_set = set(instance_months)
        pooled_spend = sum((s.amount for s in matching_segments if s.month in month_set), Decimal("0"))
        if pooled_spend <= 0:
            continue

        remaining = pooled_spend
        last_month = max(instance_months)
        for rule in ordered_rules:
            if remaining <= 0:
                break
            cap = caps_by_rule.get(rule.key)
            band_spend = min(remaining, cap.amount) if cap is not None else remaining
            if band_spend <= 0:
                continue
            remaining -= band_spend

            reward = accrue_transaction(accruals[rule.key], band_spend)
            synthetic_segment = SpendSegment(
                category="incremental_band", channel=None, month=last_month,
                amount=band_spend, ticket_size=band_spend,
            )
            results.append(AccrualResult(rule_key=rule.key, segment=synthetic_segment, reward=reward))

    return tuple(results)
