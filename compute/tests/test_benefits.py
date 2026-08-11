"""Unit tests for Stage 9 (engine/benefits.py), Part A SS A.8 / SS A.9, Part C SS C.2.8.

syn_lounge and syn_miles benefit definitions are hand-transcribed from
seeds/synthetic_cards.py (C.9 Examples 11, 4), not invented. Expected
values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.benefits import (
    Benefit,
    CardEntitlement,
    deduplicate_portfolio_benefit,
    value_countable_benefit,
    value_flat_perk_benefit,
    value_voucher_benefit,
)
from engine.caps import Window
from engine.thresholds import Payload, ThresholdEvent

QTR = Window(kind="quarter", alignment="calendar")


def _grant_entitlement_event(months, benefit_key, quantity):
    return ThresholdEvent(
        threshold_key="q_spend", tier_index=1, window_months=months, pooled_spend=Decimal("90000"),
        payload=Payload(type="grant_entitlement", benefit=benefit_key, quantity=quantity, window=QTR),
    )


def _grant_voucher_event(benefit_key):
    return ThresholdEvent(
        threshold_key="annual_miles", tier_index=1, window_months=tuple(range(1, 13)), pooled_spend=Decimal("500000"),
        payload=Payload(type="grant_voucher", benefit=benefit_key),
    )


# ---------------------------------------------------------------------------
# syn_lounge's dom_lounge (C.9 Example 11): gated countable, 4/quarter
# ---------------------------------------------------------------------------

DOM_LOUNGE = Benefit(
    key="dom_lounge", kind="countable", unit_label="domestic lounge visit",
    entitlement=Decimal("4"), entitlement_window=QTR, qualification_threshold_key="q_spend",
)


def test_gated_countable_entitlement_sums_only_qualifying_quarters():
    events = [_grant_entitlement_event((1, 2, 3), "dom_lounge", 4), _grant_entitlement_event((7, 8, 9), "dom_lounge", 4)]
    result = value_countable_benefit(DOM_LOUNGE, events, need=Decimal("6"), unit_value=Decimal("800"))
    assert result.entitlement_units == Decimal("8")  # 2 qualifying quarters * 4
    assert result.consumed_units == Decimal("6")      # Need is the binding constraint
    assert result.value_rupees == Decimal("4800")


def test_gated_countable_entitlement_binds_when_need_exceeds_it():
    events = [_grant_entitlement_event((1, 2, 3), "dom_lounge", 4), _grant_entitlement_event((7, 8, 9), "dom_lounge", 4)]
    result = value_countable_benefit(DOM_LOUNGE, events, need=Decimal("10"), unit_value=Decimal("800"))
    assert result.entitlement_units == Decimal("8")
    assert result.consumed_units == Decimal("8")  # Entitle is the binding constraint now
    assert result.value_rupees == Decimal("6400")


def test_gated_countable_no_qualifying_quarters_yields_zero():
    result = value_countable_benefit(DOM_LOUNGE, events=[], need=Decimal("6"), unit_value=Decimal("800"))
    assert result.entitlement_units == Decimal("0")
    assert result.value_rupees == Decimal("0")


# ---------------------------------------------------------------------------
# Ungated countable (hand-built -- no current card has an ungated countable
# benefit; syn_lounge's is the only countable benefit and it's gated)
# ---------------------------------------------------------------------------

def test_ungated_countable_entitlement_is_flat_rate_times_window_instances():
    monthly_movies = Benefit(
        key="movie_tickets", kind="countable", unit_label="movie ticket",
        entitlement=Decimal("2"), entitlement_window=Window(kind="calendar_month"),
    )
    # no qualification gate -> flat 2/month * 12 months = 24/year
    result = value_countable_benefit(monthly_movies, events=[], need=Decimal("10"), unit_value=Decimal("300"))
    assert result.entitlement_units == Decimal("24")
    assert result.consumed_units == Decimal("10")
    assert result.value_rupees == Decimal("3000")


# ---------------------------------------------------------------------------
# syn_miles' vch_a/vch_b (C.9 Example 4): voucher benefits
# ---------------------------------------------------------------------------

VCH_A = Benefit(key="vch_a", kind="voucher", face_value=Decimal("10000"))


def test_voucher_granted_values_at_face_times_utilisation_times_friction():
    events = [_grant_voucher_event("vch_a")]
    result = value_voucher_benefit(VCH_A, events, utilisation=Decimal("0.9"), friction=Decimal("0.85"))
    assert result.value_rupees == Decimal("7650.000")  # 10000 * 0.9 * 0.85
    assert result.flags == ()


def test_voucher_not_granted_values_at_zero_and_flags():
    result = value_voucher_benefit(VCH_A, events=[], utilisation=Decimal("0.9"), friction=Decimal("0.85"))
    assert result.value_rupees == Decimal("0")
    assert result.flags == ("not_granted",)


def test_voucher_granted_twice_by_two_tiers_values_twice():
    # Hand-built: two tiers granting the SAME benefit key -- each grant is a
    # real voucher, so the value doubles. No current card does this.
    events = [_grant_voucher_event("vch_a"), _grant_voucher_event("vch_a")]
    result = value_voucher_benefit(VCH_A, events, utilisation=Decimal("1"), friction=Decimal("1"))
    assert result.value_rupees == Decimal("20000")


def test_voucher_ignores_events_for_a_different_benefit_key():
    events = [_grant_voucher_event("vch_b")]  # different voucher
    result = value_voucher_benefit(VCH_A, events, utilisation=Decimal("1"), friction=Decimal("1"))
    assert result.value_rupees == Decimal("0")


# ---------------------------------------------------------------------------
# flat_perk (hand-built -- no current card uses this kind)
# ---------------------------------------------------------------------------

def test_flat_perk_values_at_face_times_utilisation():
    perk = Benefit(key="meet_greet", kind="flat_perk", face_value=Decimal("2000"))
    result = value_flat_perk_benefit(perk, utilisation=Decimal("0.5"))
    assert result.value_rupees == Decimal("1000.0")


# ---------------------------------------------------------------------------
# Portfolio-level dedup (A.9): value ceiling = min(Need, sum of entitlements)
# ---------------------------------------------------------------------------

def test_portfolio_dedup_need_is_the_binding_constraint():
    entitlements = [CardEntitlement("syn_lounge", Decimal("8")), CardEntitlement("syn_lounge_v2", Decimal("8"))]
    result = deduplicate_portfolio_benefit("dom_lounge", need=Decimal("6"), card_entitlements=entitlements, unit_value=Decimal("800"))
    assert result.total_entitlement == Decimal("16")
    assert result.consumed_units == Decimal("6")
    assert result.value_rupees == Decimal("4800")


def test_portfolio_dedup_entitlement_is_the_binding_constraint():
    entitlements = [CardEntitlement("syn_lounge", Decimal("8")), CardEntitlement("syn_lounge_v2", Decimal("8"))]
    result = deduplicate_portfolio_benefit("dom_lounge", need=Decimal("20"), card_entitlements=entitlements, unit_value=Decimal("800"))
    assert result.total_entitlement == Decimal("16")
    assert result.consumed_units == Decimal("16")
    assert result.value_rupees == Decimal("12800")


def test_portfolio_dedup_single_card_matches_card_level_result():
    entitlements = [CardEntitlement("syn_lounge", Decimal("8"))]
    result = deduplicate_portfolio_benefit("dom_lounge", need=Decimal("6"), card_entitlements=entitlements, unit_value=Decimal("800"))
    assert result.value_rupees == Decimal("4800")  # same as the card-level test above


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_kind_raises():
    bad = Benefit(key="bad", kind="mystery")
    with pytest.raises(ValueError, match="unknown kind"):
        value_flat_perk_benefit(bad, utilisation=Decimal("1"))


def test_countable_missing_entitlement_fields_raises():
    bad = Benefit(key="bad", kind="countable")
    with pytest.raises(ValueError, match="require entitlement"):
        value_countable_benefit(bad, events=[], need=Decimal("1"), unit_value=Decimal("1"))


def test_voucher_missing_face_value_raises():
    bad = Benefit(key="bad", kind="voucher")
    with pytest.raises(ValueError, match="require face_value"):
        value_voucher_benefit(bad, events=[], utilisation=Decimal("1"), friction=Decimal("1"))


def test_calling_wrong_valuation_function_for_kind_raises():
    with pytest.raises(ValueError, match="not a voucher benefit"):
        value_voucher_benefit(DOM_LOUNGE, events=[], utilisation=Decimal("1"), friction=Decimal("1"))
    with pytest.raises(ValueError, match="not a countable benefit"):
        value_countable_benefit(VCH_A, events=[], need=Decimal("1"), unit_value=Decimal("1"))
