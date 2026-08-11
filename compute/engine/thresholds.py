"""Stage 6-7 -- THRESHOLDS (Part C SS C.3, SS C.4 Stages 6-7).

Evaluates a card's Threshold objects against the milestone/waiver eligible
spend views from Stage 2, and emits a ThresholdEvent for every tier that
fires. Window resolution (calendar_month/quarter/calendar_year/
anniversary_year/statement_cycle, with the anniversary/statement-cycle
approximation flags) is the exact same C.2.4 machinery Stage 5 uses,
imported from caps.py rather than re-implemented -- windows are quarter/
year, not calendar-month, so quarterly gates (syn_lounge) and annual
milestones (syn_miles) resolve identically to how a quarterly or annual
cap would.

Per-window-instance independence: a quarterly threshold's four quarters
are each evaluated against their own pooled spend, never cumulatively
across the year (C.9 Example 11: "entitlement only exists in qualified
quarters").

Tier resolution (C.3): `cumulative` fires every tier whose threshold the
window's pooled spend meets; `highest_only` fires just the highest such
tier, suppressing the rest.

SCOPE: only "grant" payload types (grant_points, grant_cashback,
grant_voucher, waive_fee, grant_entitlement) are evaluated -- confirmed
with Satya. `activate_rule` payloads raise: correctly firing them needs
Stage 3 to gain a requires_activation concept it doesn't have today (see
docs/DECISIONS.md), so syn_retro and syn_renewal's rate-unlock tiers are a
separate future task. A payload only needs to be *reachable* (crossed) to
raise -- an activate_rule tier that's never crossed in a given scenario is
inert and doesn't block evaluation of the tiers that do fire.

Value (rupees), utilisation, and friction (A.5's u/phi) are Stage 8/9's
concern, not this stage's -- a ThresholdEvent carries the raw payload only.
Similarly, `condition: "on_renewal"` is carried through unfiltered; deciding
whether a given evaluation run is a renewal year is Stage 11 (year-mode),
not built yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.caps import Window, window_flags, window_instances
from engine.match import Selector
from engine.normalise import SpendSegment

VALID_MEASURE = frozenset({"milestone_eligible_spend", "waiver_eligible_spend"})
VALID_TIER_MODE = frozenset({"cumulative", "highest_only"})
SUPPORTED_PAYLOAD_TYPES = frozenset({"grant_points", "grant_cashback", "grant_voucher", "waive_fee", "grant_entitlement"})


def _selector_matches(selector: Selector, segment: SpendSegment) -> bool:
    if selector.categories is not None and segment.category not in selector.categories:
        return False
    if selector.channels is not None and segment.channel not in selector.channels:
        return False
    if selector.merchant_groups is not None and segment.merchant_group not in selector.merchant_groups:
        return False
    return True


@dataclass(frozen=True)
class ThresholdBasis:
    measure: str  # "milestone_eligible_spend" | "waiver_eligible_spend"
    window: Window
    selector_override: Selector | None = None


@dataclass(frozen=True)
class Payload:
    type: str
    amount: Decimal | None = None
    currency: str | None = None
    benefit: str | None = None
    fee: str | None = None
    quantity: int | None = None
    window: Window | None = None
    condition: str | None = None
    rule: str | None = None
    application: str | None = None


@dataclass(frozen=True)
class Tier:
    tier_index: int
    threshold_amount: Decimal
    payload: Payload


@dataclass(frozen=True)
class Threshold:
    key: str
    basis: ThresholdBasis
    tier_mode: str
    tiers: tuple[Tier, ...]


@dataclass(frozen=True)
class ThresholdEvent:
    threshold_key: str
    tier_index: int
    window_months: tuple[int, ...]
    pooled_spend: Decimal
    payload: Payload
    flags: tuple[str, ...] = ()


def _validate_threshold_structure(threshold: Threshold) -> None:
    if threshold.basis.measure not in VALID_MEASURE:
        raise ValueError(
            f"threshold {threshold.key!r}: unknown basis.measure {threshold.basis.measure!r}; "
            f"valid measures are {sorted(VALID_MEASURE)}"
        )
    if threshold.tier_mode not in VALID_TIER_MODE:
        raise ValueError(
            f"threshold {threshold.key!r}: unknown tier_mode {threshold.tier_mode!r}; "
            f"valid modes are {sorted(VALID_TIER_MODE)}"
        )


def _require_supported_payload(threshold: Threshold, tier: Tier) -> None:
    if tier.payload.type not in SUPPORTED_PAYLOAD_TYPES:
        raise ValueError(
            f"threshold {threshold.key!r} tier {tier.tier_index}: payload type {tier.payload.type!r} "
            f"not supported yet (only {sorted(SUPPORTED_PAYLOAD_TYPES)}); "
            "activate_rule needs Stage 3 activation support -- see docs/DECISIONS.md"
        )


def evaluate_threshold(
    threshold: Threshold,
    milestone_segments: Sequence[SpendSegment],
    waiver_segments: Sequence[SpendSegment],
) -> tuple[ThresholdEvent, ...]:
    _validate_threshold_structure(threshold)

    segments = milestone_segments if threshold.basis.measure == "milestone_eligible_spend" else waiver_segments
    if threshold.basis.selector_override is not None:
        segments = [s for s in segments if _selector_matches(threshold.basis.selector_override, s)]

    flags = window_flags(threshold.basis.window)
    events: list[ThresholdEvent] = []

    for instance_months in window_instances(threshold.basis.window):
        month_set = set(instance_months)
        pooled = sum((s.amount for s in segments if s.month in month_set), Decimal("0"))

        crossed = [t for t in threshold.tiers if pooled >= t.threshold_amount]
        if not crossed:
            continue

        if threshold.tier_mode == "cumulative":
            firing = crossed
        else:  # highest_only
            firing = [max(crossed, key=lambda t: t.threshold_amount)]

        for tier in firing:
            _require_supported_payload(threshold, tier)
            events.append(
                ThresholdEvent(
                    threshold_key=threshold.key,
                    tier_index=tier.tier_index,
                    window_months=instance_months,
                    pooled_spend=pooled,
                    payload=tier.payload,
                    flags=flags,
                )
            )

    return tuple(events)


def evaluate_thresholds(
    thresholds: Sequence[Threshold],
    milestone_segments: Sequence[SpendSegment],
    waiver_segments: Sequence[SpendSegment],
) -> tuple[ThresholdEvent, ...]:
    events: list[ThresholdEvent] = []
    for threshold in thresholds:
        events.extend(evaluate_threshold(threshold, milestone_segments, waiver_segments))
    return tuple(events)
