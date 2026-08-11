"""BREAKPOINTS -- the compiled breakpoint list (Part C SS C.0 Safeguard 2,
Part E SS E.0 module layout).

At solve time the (future) optimiser compiles, per enumerated portfolio,
"the full breakpoint list: every (card, threshold, window) tuple with its
exact rupee value" -- every threshold tier (which already covers waiver
lines, since fee waivers are ordinary Threshold rules per C.3) and every
cap boundary, each expressed as the exact SPEND-domain rupee value that
crosses it, because C.0's repair pass operates entirely in spend terms
("exact milestone/waiver-eligible spend near beta", "top-up ... spend to
cross T(beta)").

Threshold tiers are already spend-domain (`basis.measure` is
milestone_eligible_spend or waiver_eligible_spend, `tier.threshold_amount`
IS the rupee value) -- copied through unchanged. Reward-measure caps are
NOT spend-domain by default (`cap.amount` is a reward ceiling); this
module converts via A.3's Sbar = Cap/a using the capped rule's own
continuous rate (`caps.flat_rate`, the same conversion caps.py's own
overflow-spend derivation already uses) so every breakpoint in the
compiled list is comparable on the same axis.

Every breakpoint also carries C.0's default `buffer(beta) = max(Rs5,000,
2% . T(beta))`, since the (future) repair pass needs it immediately
alongside the breakpoint itself.

Scope: this module ONLY compiles the list -- per Part E SS E.0's module
layout, the repair pass that actually walks this list and generates near-
miss/barely-made variants is a SEPARATE future module (optimiser/repair.py,
Phase 4), not built here. Spend-measure caps stay out of scope, same
posture as caps.py itself (docs/DECISIONS.md #9) -- syn_slab's fill-order
bands aren't ordinary breakpoints and need their own design pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from engine.accrue import Accrual
from engine.caps import Cap, Window, flat_rate
from engine.thresholds import Threshold

DEFAULT_BUFFER_FLOOR = Decimal("5000")
DEFAULT_BUFFER_RATE = Decimal("0.02")


def default_buffer(threshold_spend: Decimal) -> Decimal:
    return max(DEFAULT_BUFFER_FLOOR, DEFAULT_BUFFER_RATE * threshold_spend)


@dataclass(frozen=True)
class Breakpoint:
    card_key: str
    source_type: str  # "threshold" | "cap"
    source_key: str  # threshold key or cap key
    tier_index: int | None  # set for thresholds; None for caps
    threshold_spend: Decimal  # exact rupee spend value that crosses this breakpoint
    window: Window
    measure: str  # threshold's basis.measure, or the cap's measure ("reward")
    buffer: Decimal
    scope: str | None = None  # cap scope; None for thresholds


@dataclass(frozen=True)
class CardBreakpointInputs:
    card_key: str
    thresholds: tuple[Threshold, ...] = ()
    caps: tuple[Cap, ...] = ()
    accruals: dict[str, Accrual] = field(default_factory=dict)  # rule_key -> Accrual; only needed if caps is non-empty


def _threshold_breakpoints(card_key: str, threshold: Threshold) -> list[Breakpoint]:
    return [
        Breakpoint(
            card_key=card_key,
            source_type="threshold",
            source_key=threshold.key,
            tier_index=tier.tier_index,
            threshold_spend=tier.threshold_amount,
            window=threshold.basis.window,
            measure=threshold.basis.measure,
            buffer=default_buffer(tier.threshold_amount),
        )
        for tier in threshold.tiers
    ]


def _cap_breakpoint(card_key: str, cap: Cap, accruals: dict[str, Accrual]) -> Breakpoint:
    if cap.measure != "reward":
        raise ValueError(
            f"cap {cap.key!r}: measure {cap.measure!r} not supported yet (only 'reward'); "
            "spend-measure caps are syn_slab's fill-order mechanic -- see docs/DECISIONS.md"
        )
    rate = flat_rate(accruals[cap.rule_key])
    spend_at_cap = cap.amount / rate
    return Breakpoint(
        card_key=card_key,
        source_type="cap",
        source_key=cap.key,
        tier_index=None,
        threshold_spend=spend_at_cap,
        window=cap.window,
        measure=cap.measure,
        buffer=default_buffer(spend_at_cap),
        scope=cap.scope,
    )


def compile_card_breakpoints(card: CardBreakpointInputs) -> tuple[Breakpoint, ...]:
    breakpoints: list[Breakpoint] = []
    for threshold in card.thresholds:
        breakpoints.extend(_threshold_breakpoints(card.card_key, threshold))
    for cap in card.caps:
        breakpoints.append(_cap_breakpoint(card.card_key, cap, card.accruals))
    return tuple(breakpoints)


def compile_breakpoints(portfolio: Sequence[CardBreakpointInputs]) -> tuple[Breakpoint, ...]:
    breakpoints: list[Breakpoint] = []
    for card in portfolio:
        breakpoints.extend(compile_card_breakpoints(card))
    return tuple(breakpoints)
