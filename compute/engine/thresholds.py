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

SCOPE: "grant" payload types (grant_points, grant_cashback, grant_voucher,
waive_fee, grant_entitlement) are evaluated directly. `activate_rule`
(accelerated-rate unlocks -- syn_retro, syn_renewal) is now also
supported, via `apply_rule_activations` below, which is the one piece of
this stage that reaches back into Stage 3/4 -- match.py's `EarningRule`
gained a `requires_activation` flag for exactly this. A payload only needs
to be *reachable* (crossed) to require support -- an activate_rule tier
that's never crossed in a given scenario is inert and doesn't block
evaluation of the tiers that do fire.

Prospective vs retroactive (C.3's `application` field), once a tier fires:
  - prospective: the rule activates for months AFTER the crossing month
    only -- the crossing month itself keeps whatever rate already applied
    (chosen because the engine has no intra-month transaction timing; the
    crossing month's spend is already fully accrued by the time the
    threshold is known to be crossed, so backdating into it would be
    arbitrary -- see docs/DECISIONS.md for the full reasoning, since C.4's
    "apply from crossing month onward" doesn't itself say inclusive or
    exclusive).
  - retroactive: the rule activates for every month in the window,
    including ones before the crossing month -- the whole window re-rates.

`evaluate_threshold` now also finds, for every firing tier, the first
month (walking the window's months in chronological order, tracking a
running total) at which its own threshold was met -- `crossing_month` on
ThresholdEvent. This is computed for every payload type, not just
activate_rule, since it's cheap and generally useful for trace/
explainability (e.g. "this voucher was earned in July").

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

from engine.accrue import Accrual, AccrualResult, accrue_category_mode
from engine.caps import Window, window_flags, window_instances
from engine.match import EarningRule, RuleBinding, Selector, match_segment, selector_matches
from engine.normalise import SpendSegment

VALID_MEASURE = frozenset({"milestone_eligible_spend", "waiver_eligible_spend"})
VALID_TIER_MODE = frozenset({"cumulative", "highest_only"})
VALID_APPLICATION = frozenset({"prospective", "retroactive"})
SUPPORTED_PAYLOAD_TYPES = frozenset(
    {"grant_points", "grant_cashback", "grant_voucher", "waive_fee", "grant_entitlement", "activate_rule"}
)



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
    crossing_month: int | None = None  # first month (chronological, within window_months) the tier was met


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
    payload = tier.payload
    if payload.type not in SUPPORTED_PAYLOAD_TYPES:
        raise ValueError(
            f"threshold {threshold.key!r} tier {tier.tier_index}: payload type {payload.type!r} "
            f"not supported (only {sorted(SUPPORTED_PAYLOAD_TYPES)})"
        )
    if payload.type == "activate_rule":
        if not payload.rule:
            raise ValueError(f"threshold {threshold.key!r} tier {tier.tier_index}: activate_rule payload needs 'rule'")
        if payload.application not in VALID_APPLICATION:
            raise ValueError(
                f"threshold {threshold.key!r} tier {tier.tier_index}: activate_rule payload has "
                f"unknown application {payload.application!r}; valid values are {sorted(VALID_APPLICATION)}"
            )


def evaluate_threshold(
    threshold: Threshold,
    milestone_segments: Sequence[SpendSegment],
    waiver_segments: Sequence[SpendSegment],
) -> tuple[ThresholdEvent, ...]:
    _validate_threshold_structure(threshold)

    segments = milestone_segments if threshold.basis.measure == "milestone_eligible_spend" else waiver_segments
    if threshold.basis.selector_override is not None:
        segments = [s for s in segments if selector_matches(threshold.basis.selector_override, s)]

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

        # Chronological running-total walk to find each firing tier's
        # first-crossed month within this window instance.
        crossing_month_by_tier: dict[int, int] = {}
        remaining = {t.tier_index: t for t in firing}
        running = Decimal("0")
        for month in sorted(instance_months):
            running += sum((s.amount for s in segments if s.month == month), Decimal("0"))
            for tier_index, tier in list(remaining.items()):
                if running >= tier.threshold_amount:
                    crossing_month_by_tier[tier_index] = month
                    del remaining[tier_index]
            if not remaining:
                break

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
                    crossing_month=crossing_month_by_tier.get(tier.tier_index),
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


def _active_months(event: ThresholdEvent) -> set[int]:
    if event.payload.application == "retroactive":
        return set(event.window_months)
    # prospective: strictly after the crossing month (see module docstring
    # for why the crossing month itself keeps its original rate).
    return {m for m in event.window_months if event.crossing_month is not None and m > event.crossing_month}


def apply_rule_activations(
    accrual_results: Sequence[AccrualResult],
    threshold_events: Sequence[ThresholdEvent],
    reward_segments: Sequence[SpendSegment],
    earning_rules: Sequence[EarningRule],
    accruals: dict[str, Accrual],
) -> tuple[AccrualResult, ...]:
    """For every activate_rule event, re-derives the correct binding for
    each affected segment (Stage 3's match_segment, now with the named rule
    eligible) and re-accrues it (Stage 4), replacing whatever originally
    bound that exact segment. Segment identity (`is`), not equality, is
    what ties an AccrualResult back to its segment -- correct as long as
    the segment references flow through unchanged from Stage 1/2/3, which
    holds for every uncapped segment; caps.py's synthetic overflow segments
    (a different object) aren't reconciled here -- no in-scope card
    combines a cap with an activation-gated rule, see docs/DECISIONS.md."""
    rules_by_key = {r.key: r for r in earning_rules}
    results = list(accrual_results)

    for event in threshold_events:
        if event.payload.type != "activate_rule":
            continue

        activated_rule = rules_by_key.get(event.payload.rule)
        if activated_rule is None:
            raise ValueError(f"activate_rule payload names unknown rule {event.payload.rule!r}")

        active_months = _active_months(event)
        if not active_months:
            continue

        affected_segments = [
            s for s in reward_segments if s.month in active_months and selector_matches(activated_rule.selector, s)
        ]
        for segment in affected_segments:
            results = [r for r in results if r.segment is not segment]
            new_bindings: tuple[RuleBinding, ...] = match_segment(
                segment, earning_rules, active_rule_keys=frozenset({activated_rule.key})
            )
            results.extend(accrue_category_mode(new_bindings, accruals))

    return tuple(results)
