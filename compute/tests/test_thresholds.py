"""Unit tests for Stage 6-7 (engine/thresholds.py), Part C SS C.3 / SS C.4.

Threshold fixtures for syn_miles, syn_waiver, and syn_lounge are
hand-transcribed from seeds/synthetic_cards.py (C.9 Examples 4, 5, 11),
not invented. Expected values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.caps import Window
from engine.match import Selector
from engine.normalise import SpendSegment
from engine.thresholds import Payload, Threshold, ThresholdBasis, Tier, evaluate_threshold

ANNIV = Window(kind="anniversary_year")
QTR = Window(kind="quarter", alignment="calendar")


def _seg(category, month, amount, channel=None):
    return SpendSegment(category=category, channel=channel, month=month, amount=Decimal(amount), ticket_size=Decimal("1000"))


def _payload_types(events):
    return [e.payload.type for e in events]


# ---------------------------------------------------------------------------
# syn_miles (C.9 Example 4): cumulative annual, 4L -> vch_a, 8L -> vch_b
# ---------------------------------------------------------------------------

SYN_MILES_THRESHOLD = Threshold(
    key="annual_miles",
    basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV),
    tier_mode="cumulative",
    tiers=(
        Tier(1, Decimal("400000"), Payload(type="grant_voucher", benefit="vch_a")),
        Tier(2, Decimal("800000"), Payload(type="grant_voucher", benefit="vch_b")),
    ),
)


def test_syn_miles_crossing_only_first_tier_fires_only_vch_a():
    milestone = [_seg("travel", 6, "500000")]
    events = evaluate_threshold(SYN_MILES_THRESHOLD, milestone, waiver_segments=[])
    assert len(events) == 1
    assert events[0].tier_index == 1
    assert events[0].payload.benefit == "vch_a"
    assert events[0].pooled_spend == Decimal("500000")


def test_syn_miles_crossing_both_tiers_fires_both_cumulative():
    milestone = [_seg("travel", 6, "900000")]
    events = evaluate_threshold(SYN_MILES_THRESHOLD, milestone, waiver_segments=[])
    assert _payload_types(events) == ["grant_voucher", "grant_voucher"]
    assert {e.payload.benefit for e in events} == {"vch_a", "vch_b"}


def test_syn_miles_below_first_tier_fires_nothing():
    milestone = [_seg("travel", 6, "300000")]
    events = evaluate_threshold(SYN_MILES_THRESHOLD, milestone, waiver_segments=[])
    assert events == ()


# ---------------------------------------------------------------------------
# syn_waiver (C.9 Example 5): waiver_eligible_spend >= 3L -> waive annual fee
# ---------------------------------------------------------------------------

SYN_WAIVER_THRESHOLD = Threshold(
    key="waiver",
    basis=ThresholdBasis(measure="waiver_eligible_spend", window=ANNIV),
    tier_mode="cumulative",
    tiers=(Tier(1, Decimal("300000"), Payload(type="waive_fee", fee="annual")),),
)


def test_syn_waiver_below_threshold_fee_not_waived():
    events = evaluate_threshold(SYN_WAIVER_THRESHOLD, milestone_segments=[], waiver_segments=[_seg("grocery", 6, "250000")])
    assert events == ()


def test_syn_waiver_at_or_above_threshold_fee_waived():
    events = evaluate_threshold(SYN_WAIVER_THRESHOLD, milestone_segments=[], waiver_segments=[_seg("grocery", 6, "300000")])
    assert len(events) == 1
    assert events[0].payload.type == "waive_fee"
    assert events[0].payload.fee == "annual"


# ---------------------------------------------------------------------------
# syn_lounge (C.9 Example 11): quarterly gate, per-quarter independence
# ---------------------------------------------------------------------------

SYN_LOUNGE_THRESHOLD = Threshold(
    key="q_spend",
    basis=ThresholdBasis(measure="milestone_eligible_spend", window=QTR),
    tier_mode="cumulative",
    tiers=(Tier(1, Decimal("75000"), Payload(type="grant_entitlement", benefit="dom_lounge", quantity=4, window=QTR)),),
)


def test_syn_lounge_only_qualified_quarters_fire():
    milestone = [
        _seg("travel", 1, "40000"), _seg("travel", 2, "40000"), _seg("travel", 3, "10000"),  # Q1 = 90,000 -> qualifies
        _seg("travel", 4, "10000"), _seg("travel", 5, "10000"), _seg("travel", 6, "10000"),  # Q2 = 30,000 -> doesn't
    ]
    events = evaluate_threshold(SYN_LOUNGE_THRESHOLD, milestone, waiver_segments=[])
    assert len(events) == 1
    assert events[0].window_months == (1, 2, 3)
    assert events[0].pooled_spend == Decimal("90000")


def test_syn_lounge_multiple_qualified_quarters_each_fire_independently():
    milestone = [
        _seg("travel", 1, "80000"),  # Q1 qualifies
        _seg("travel", 7, "80000"),  # Q3 qualifies
    ]
    events = evaluate_threshold(SYN_LOUNGE_THRESHOLD, milestone, waiver_segments=[])
    assert {e.window_months for e in events} == {(1, 2, 3), (7, 8, 9)}


# ---------------------------------------------------------------------------
# highest_only suppression (hand-built -- syn_retro's real highest_only
# threshold uses activate_rule, which is out of scope; see below)
# ---------------------------------------------------------------------------

HIGHEST_ONLY_THRESHOLD = Threshold(
    key="tiers",
    basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV),
    tier_mode="highest_only",
    tiers=(
        Tier(1, Decimal("100000"), Payload(type="grant_points", amount=Decimal("1000"), currency="x")),
        Tier(2, Decimal("300000"), Payload(type="grant_points", amount=Decimal("3000"), currency="x")),
    ),
)


def test_highest_only_suppresses_lower_tier_when_both_crossed():
    events = evaluate_threshold(HIGHEST_ONLY_THRESHOLD, [_seg("grocery", 6, "350000")], waiver_segments=[])
    assert len(events) == 1
    assert events[0].tier_index == 2
    assert events[0].payload.amount == Decimal("3000")


def test_highest_only_fires_lower_tier_when_only_it_crossed():
    events = evaluate_threshold(HIGHEST_ONLY_THRESHOLD, [_seg("grocery", 6, "150000")], waiver_segments=[])
    assert len(events) == 1
    assert events[0].tier_index == 1


def test_highest_only_fires_nothing_below_any_tier():
    events = evaluate_threshold(HIGHEST_ONLY_THRESHOLD, [_seg("grocery", 6, "50000")], waiver_segments=[])
    assert events == ()


# ---------------------------------------------------------------------------
# selector_override (C.2.1/C.3 -- no current card exercises this, hand-built)
# ---------------------------------------------------------------------------

def test_selector_override_narrows_the_pooled_basis():
    threshold = Threshold(
        key="travel_only",
        basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV, selector_override=Selector(categories=("travel",))),
        tier_mode="cumulative",
        tiers=(Tier(1, Decimal("100000"), Payload(type="grant_points", amount=Decimal("500"), currency="x")),),
    )
    milestone = [_seg("travel", 6, "60000"), _seg("dining", 6, "60000")]  # combined 120,000, but only travel counts
    events = evaluate_threshold(threshold, milestone, waiver_segments=[])
    assert events == ()  # travel alone (60,000) doesn't cross 100,000

    milestone_more_travel = [_seg("travel", 6, "110000"), _seg("dining", 6, "60000")]
    events = evaluate_threshold(threshold, milestone_more_travel, waiver_segments=[])
    assert len(events) == 1
    assert events[0].pooled_spend == Decimal("110000")  # dining excluded from the pool


# ---------------------------------------------------------------------------
# condition: "on_renewal" is carried through unfiltered (year-mode gating is
# Stage 11's job, not built yet)
# ---------------------------------------------------------------------------

def test_on_renewal_condition_carried_through_unfiltered():
    threshold = Threshold(
        key="renewal_bonus",
        basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV),
        tier_mode="cumulative",
        tiers=(Tier(1, Decimal("500000"), Payload(type="grant_points", amount=Decimal("10000"), currency="synth_points", condition="on_renewal")),),
    )
    events = evaluate_threshold(threshold, [_seg("grocery", 6, "600000")], waiver_segments=[])
    assert len(events) == 1
    assert events[0].payload.condition == "on_renewal"


# ---------------------------------------------------------------------------
# activate_rule -- out of scope, raises when actually crossed
# ---------------------------------------------------------------------------

SYN_RENEWAL_THRESHOLD = Threshold(
    key="anniv",
    basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV),
    tier_mode="cumulative",
    tiers=(
        Tier(1, Decimal("100000"), Payload(type="activate_rule", rule="dining_2x", application="prospective")),
        Tier(2, Decimal("500000"), Payload(type="grant_points", amount=Decimal("10000"), currency="synth_points", condition="on_renewal")),
    ),
)


def test_syn_renewal_activate_rule_tier_raises_when_crossed():
    # cumulative mode: crossing tier 2 (5L) necessarily also crosses tier 1
    # (1L, activate_rule) -- correctly surfaces the current limitation
    # rather than silently skipping the unsupported payload.
    with pytest.raises(ValueError, match="activate_rule"):
        evaluate_threshold(SYN_RENEWAL_THRESHOLD, [_seg("grocery", 6, "600000")], waiver_segments=[])


def test_syn_renewal_uncrossed_activate_rule_tier_does_not_block_evaluation():
    # Below even tier 1 -- nothing fires, nothing raises.
    events = evaluate_threshold(SYN_RENEWAL_THRESHOLD, [_seg("grocery", 6, "50000")], waiver_segments=[])
    assert events == ()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_measure_raises():
    threshold = Threshold(
        key="bad", basis=ThresholdBasis(measure="bonus_eligible_spend", window=ANNIV),
        tier_mode="cumulative", tiers=(Tier(1, Decimal("1000"), Payload(type="grant_points")),),
    )
    with pytest.raises(ValueError, match="basis.measure"):
        evaluate_threshold(threshold, [_seg("grocery", 1, "5000")], waiver_segments=[])


def test_unknown_tier_mode_raises():
    threshold = Threshold(
        key="bad", basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV),
        tier_mode="top_three", tiers=(Tier(1, Decimal("1000"), Payload(type="grant_points")),),
    )
    with pytest.raises(ValueError, match="tier_mode"):
        evaluate_threshold(threshold, [_seg("grocery", 1, "5000")], waiver_segments=[])
