"""Stage 5 -- CAP (Part C SS C.2.3, SS C.4 Stage 5) -- DELIBERATELY MINIMAL SLICE.

Built alongside Stage 4 only to make golden_syn_ecom_basic.json (a cap-
binding golden) runnable, per Satya's call logged in docs/DECISIONS.md.
Implements exactly the concave reward curve of Part A SS A.3 --

    Reward(S) = a . min(S, Sbar) + b . max(S - Sbar, 0),  Sbar = Cap / a

-- for a single reward-measure, calendar-month-window, rule-scoped cap with
one segment per rule per month. NOT the full Stage 5: spend-measure caps,
quarterly/annual windows, rule_group/card scopes, and multi-segment months
all raise rather than silently mishandling. See docs/DECISIONS.md for what
the full Stage 5 still needs.

Overflow rate resolution (`overflow: "base_rate"`) doesn't hardcode which
rule is "the base" -- it re-runs Stage 3's match on the same segment with
the capped rule excluded, and uses whichever non-stacking rule wins that
contest. This is general (works for any card whose overflow should fall
back to whatever would otherwise apply, not just a rule literally named
"base") and reuses Stage 3 rather than duplicating its resolution logic.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from engine.accrue import Accrual, AccrualResult, accrue_transaction
from engine.match import EarningRule, match_segment

SUPPORTED_MEASURE = "reward"
SUPPORTED_WINDOW = "calendar_month"
SUPPORTED_SCOPE = "rule"
VALID_OVERFLOW = frozenset({"base_rate", "zero"})


@dataclass(frozen=True)
class Cap:
    key: str
    rule_key: str
    measure: str
    amount: Decimal
    window: str
    scope: str
    overflow: str


def _validate_cap(cap: Cap) -> None:
    if cap.measure != SUPPORTED_MEASURE:
        raise ValueError(f"cap {cap.key!r}: measure {cap.measure!r} not supported yet (only {SUPPORTED_MEASURE!r})")
    if cap.window != SUPPORTED_WINDOW:
        raise ValueError(f"cap {cap.key!r}: window {cap.window!r} not supported yet (only {SUPPORTED_WINDOW!r})")
    if cap.scope != SUPPORTED_SCOPE:
        raise ValueError(f"cap {cap.key!r}: scope {cap.scope!r} not supported yet (only {SUPPORTED_SCOPE!r})")
    if cap.overflow not in VALID_OVERFLOW:
        raise ValueError(f"cap {cap.key!r}: unknown overflow mode {cap.overflow!r}")


def _flat_rate(accrual: Accrual) -> Decimal:
    """The continuous (unfloored) rupee rate `a`/`b` of A.3's curve."""
    if accrual.type == "percentage":
        return accrual.rate
    return accrual.points_per_unit / accrual.unit_amount


def apply_caps(
    accrual_results: Sequence[AccrualResult],
    caps: Sequence[Cap],
    earning_rules: Sequence[EarningRule],
    accruals: dict[str, Accrual],
) -> tuple[AccrualResult, ...]:
    for cap in caps:
        _validate_cap(cap)

    # Every decision below is computed against the ORIGINAL, untouched
    # accrual_results list -- indices must never go stale mid-loop, so
    # nothing is mutated until the single pass at the end.
    capped_reward_by_index: dict[int, Decimal] = {}
    overflow_extra: list[AccrualResult] = []

    for cap in caps:
        by_month: dict[int, list[int]] = {}
        for i, result in enumerate(accrual_results):
            if result.rule_key == cap.rule_key:
                by_month.setdefault(result.segment.month, []).append(i)

        for month, indices in by_month.items():
            total_reward = sum((accrual_results[i].reward for i in indices), Decimal("0"))
            if total_reward <= cap.amount:
                continue
            if len(indices) != 1:
                raise ValueError(
                    f"cap {cap.key!r}: month {month} has {len(indices)} segments bound to "
                    f"{cap.rule_key!r}; multi-segment cap months aren't supported yet"
                )

            idx = indices[0]
            original = accrual_results[idx]
            capped_reward_by_index[idx] = cap.amount

            if cap.overflow == "base_rate":
                rate = _flat_rate(accruals[cap.rule_key])
                spend_at_cap = cap.amount / rate
                overflow_spend = original.segment.amount - spend_at_cap
                if overflow_spend > 0:
                    fallback_rules = [r for r in earning_rules if r.key != cap.rule_key]
                    fallback_bindings = match_segment(original.segment, fallback_rules)
                    fallback_winner = next((b for b in fallback_bindings if not b.stacked), None)
                    if fallback_winner is not None:
                        fallback_accrual = accruals[fallback_winner.rule_key]
                        overflow_reward = accrue_transaction(fallback_accrual, overflow_spend)
                        overflow_segment = replace(original.segment, amount=overflow_spend)
                        overflow_extra.append(
                            AccrualResult(
                                rule_key=fallback_winner.rule_key,
                                segment=overflow_segment,
                                reward=overflow_reward,
                                flags=("cap_overflow",),
                            )
                        )
            # overflow == "zero": excess spend earns nothing further.

    results = [
        replace(r, reward=capped_reward_by_index[i]) if i in capped_reward_by_index else r
        for i, r in enumerate(accrual_results)
    ]
    results.extend(overflow_extra)
    return tuple(results)
