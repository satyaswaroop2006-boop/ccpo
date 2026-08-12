"""Stage 4 -- ACCRUE (Part C SS C.2.2, SS C.6, Part A/B SS A.2).

Computes gross reward per (rule, segment) binding from Stage 3.

Two modes, per A.2:
  - Transaction mode: exact per-transaction floor/round -- `points(A) =
    floor(A / U) * r`. Ground truth, no approximation, never flagged.
  - Category mode: only category totals are known, so individual
    transaction sizes are approximated by the segment's ticket-size
    assumption (Stage 1). The effective rate ea(c,k) = floor(ticket/U)*r/ticket
    is applied to the segment's full amount. Per C.6, this gets a
    `rounding_estimated` flag when the category-mode result deviates from
    the continuous (unrounded) rate by more than 1%, aggregated per rule
    (not per segment) -- a rule bound to many segments gets one verdict.

Two of the four documented rounding modes need no approximation at all in
category mode and are computed exactly regardless: `none` (continuous, no
flooring) and `floor_on_aggregate` (the segment IS the aggregate window in
category mode, so flooring its own total needs no ticket-size guess).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Sequence

from engine.match import RuleBinding
from engine.normalise import SpendSegment

MONEY_QUANT = Decimal("0.01")
MATERIALITY_THRESHOLD = Decimal("0.01")

# "floor_paise_per_txn" is the seed catalog's spelling for percentage-type
# rules (paisa is the finest unit percentage accrual can floor to); treated
# as a synonym of floor_per_txn throughout.
_FLOOR_PER_TXN_MODES = frozenset({"floor_per_txn", "floor_paise_per_txn"})
_FLOOR_ON_AGGREGATE = "floor_on_aggregate"
_ROUND_PER_TXN = "round_per_txn"
_NONE = "none"
_VALID_ROUNDING = _FLOOR_PER_TXN_MODES | {_FLOOR_ON_AGGREGATE, _ROUND_PER_TXN, _NONE}

# Rounding modes where category mode must approximate via ticket size,
# because the true per-transaction floor/round can't be recovered from a
# monthly total alone.
_NEEDS_TICKET_APPROXIMATION = _FLOOR_PER_TXN_MODES | {_ROUND_PER_TXN}


@dataclass(frozen=True)
class Accrual:
    type: str  # "per_unit" | "percentage"
    currency: str | None = None
    unit_amount: Decimal | None = None      # per_unit only (U)
    points_per_unit: Decimal | None = None  # per_unit only (r)
    rate: Decimal | None = None             # percentage only
    rounding: str = "none"


@dataclass(frozen=True)
class AccrualResult:
    rule_key: str
    segment: SpendSegment
    reward: Decimal  # gross, in the rule's own currency units (points, or Rs for cashback)
    flags: tuple[str, ...] = ()


def _floor_to_integer(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_FLOOR)


def _round_to_integer(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_HALF_UP)


def _validate_accrual(accrual: Accrual) -> None:
    if accrual.rounding not in _VALID_ROUNDING:
        raise ValueError(f"unknown rounding mode {accrual.rounding!r}; valid modes are {sorted(_VALID_ROUNDING)}")
    if accrual.type == "per_unit":
        if accrual.unit_amount is None or accrual.points_per_unit is None:
            raise ValueError("per_unit accrual requires unit_amount and points_per_unit")
    elif accrual.type == "percentage":
        if accrual.rate is None:
            raise ValueError("percentage accrual requires rate")
    else:
        raise ValueError(f"unknown accrual type {accrual.type!r}")


def accrue_transaction(accrual: Accrual, amount: Decimal) -> Decimal:
    """Exact accrual for ONE transaction of `amount` (A.2's exact form),
    applying `accrual.rounding` at the finest unit the accrual type allows.
    Also used, at ticket_size instead of a real transaction amount, as the
    building block of category mode's ticket-size approximation below."""
    _validate_accrual(accrual)

    if accrual.type == "per_unit":
        raw_units = amount / accrual.unit_amount
        if accrual.rounding in _FLOOR_PER_TXN_MODES or accrual.rounding == _FLOOR_ON_AGGREGATE:
            units = _floor_to_integer(raw_units)
        elif accrual.rounding == _ROUND_PER_TXN:
            units = _round_to_integer(raw_units)
        else:  # none
            units = raw_units
        return units * accrual.points_per_unit

    raw = amount * accrual.rate
    if accrual.rounding in _FLOOR_PER_TXN_MODES or accrual.rounding == _FLOOR_ON_AGGREGATE:
        return raw.quantize(MONEY_QUANT, rounding=ROUND_FLOOR)
    if accrual.rounding == _ROUND_PER_TXN:
        return raw.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    return raw  # none


def accrue_transactions(accrual: Accrual, amounts: Sequence[Decimal]) -> Decimal:
    """Transaction mode over a list of real transaction amounts. Only
    `floor_on_aggregate` needs special handling here -- it floors the SUM
    once rather than each transaction, which is exactly the difference
    between the two modes when real transactions are known."""
    if accrual.rounding == _FLOOR_ON_AGGREGATE:
        total = sum(amounts, Decimal("0"))
        return accrue_transaction(accrual, total)
    return sum((accrue_transaction(accrual, a) for a in amounts), Decimal("0"))


def effective_rate(accrual: Accrual, ticket_size: Decimal) -> Decimal:
    """Part A SS A.2's ê(c,k): the ticket-size-approximated planning rate
    (reward units per rupee), evaluated once at `ticket_size` rather than
    per real transaction. This is `_ticket_approx_reward`'s per-rupee rate,
    promoted to a standalone function (docs/DECISIONS.md's #15 precedent --
    promote a private stage helper when a later stage needs it) for
    `optimiser/allocate.py`'s segment compilation, which needs the rate on
    its own, not multiplied through a segment's amount. Distinct from
    `caps.py::flat_rate`, which is the evaluator-exact continuous rate used
    for spend-domain breakpoint conversion -- a different purpose (exact
    threshold crossings) that must NOT be conflated with the optimiser's
    approximated planning rate."""
    per_txn_at_ticket = accrue_transaction(accrual, ticket_size)
    return per_txn_at_ticket / ticket_size


def _ticket_approx_reward(accrual: Accrual, segment: SpendSegment) -> Decimal:
    return effective_rate(accrual, segment.ticket_size) * segment.amount


def _unrounded_reward(accrual: Accrual, segment: SpendSegment) -> Decimal:
    return accrue_transaction(replace(accrual, rounding=_NONE), segment.amount)


def _exact_category_reward(accrual: Accrual, segment: SpendSegment) -> Decimal:
    """Valid for `none` and `floor_on_aggregate`: both are computable
    directly from the segment's own total, no ticket-size guess needed."""
    return accrue_transaction(accrual, segment.amount)


def accrue_category_mode(bindings: Sequence[RuleBinding], accruals: dict[str, Accrual]) -> tuple[AccrualResult, ...]:
    by_rule: dict[str, list[RuleBinding]] = {}
    for binding in bindings:
        by_rule.setdefault(binding.rule_key, []).append(binding)

    results: list[AccrualResult] = []
    for rule_key, rule_bindings in by_rule.items():
        accrual = accruals[rule_key]

        if accrual.rounding in _NEEDS_TICKET_APPROXIMATION:
            approx_rewards = [_ticket_approx_reward(accrual, b.segment) for b in rule_bindings]
            approx_total = sum(approx_rewards, Decimal("0"))
            unrounded_total = sum((_unrounded_reward(accrual, b.segment) for b in rule_bindings), Decimal("0"))
            flagged = approx_total != 0 and abs(approx_total - unrounded_total) / approx_total > MATERIALITY_THRESHOLD
            flags = ("rounding_estimated",) if flagged else ()
            for binding, reward in zip(rule_bindings, approx_rewards):
                results.append(AccrualResult(rule_key=rule_key, segment=binding.segment, reward=reward, flags=flags))
        else:
            for binding in rule_bindings:
                reward = _exact_category_reward(accrual, binding.segment)
                results.append(AccrualResult(rule_key=rule_key, segment=binding.segment, reward=reward))

    return tuple(results)
