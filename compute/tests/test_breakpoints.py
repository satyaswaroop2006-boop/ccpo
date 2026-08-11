"""Unit tests for breakpoints.py, Part C SS C.0 / Part E SS E.0.

syn_miles and syn_ecom fixtures are hand-transcribed from
seeds/synthetic_cards.py (C.9 Examples 4, 2), not invented. Expected
values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.accrue import Accrual
from engine.breakpoints import CardBreakpointInputs, compile_breakpoints, default_buffer
from engine.caps import Cap, Window
from engine.thresholds import Payload, Threshold, ThresholdBasis, Tier

ANNIV = Window(kind="anniversary_year")
MONTH = Window(kind="calendar_month")

SYN_MILES_THRESHOLD = Threshold(
    key="annual_miles",
    basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV),
    tier_mode="cumulative",
    tiers=(
        Tier(1, Decimal("400000"), Payload(type="grant_voucher", benefit="vch_a")),
        Tier(2, Decimal("800000"), Payload(type="grant_voucher", benefit="vch_b")),
    ),
)

SYN_ECOM_WAIVER_THRESHOLD = Threshold(
    key="waiver",
    basis=ThresholdBasis(measure="waiver_eligible_spend", window=ANNIV),
    tier_mode="cumulative",
    tiers=(Tier(1, Decimal("100000"), Payload(type="waive_fee", fee="annual")),),
)
SYN_ECOM_CAP = Cap(key="cap_ecom", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=MONTH, scope="rule", overflow="base_rate")
SYN_ECOM_ACCRUALS = {"ecom": Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn")}


# ---------------------------------------------------------------------------
# syn_miles: both milestone tiers appear, spend-domain values unchanged
# ---------------------------------------------------------------------------

def test_syn_miles_4l_and_8l_milestone_lines_appear():
    card = CardBreakpointInputs(card_key="syn_miles", thresholds=(SYN_MILES_THRESHOLD,))
    breakpoints = compile_breakpoints([card])

    spends = {bp.threshold_spend for bp in breakpoints}
    assert Decimal("400000") in spends
    assert Decimal("800000") in spends
    assert len(breakpoints) == 2

    tier1 = next(bp for bp in breakpoints if bp.threshold_spend == Decimal("400000"))
    assert tier1.card_key == "syn_miles"
    assert tier1.source_type == "threshold"
    assert tier1.source_key == "annual_miles"
    assert tier1.tier_index == 1
    assert tier1.window == ANNIV
    assert tier1.measure == "milestone_eligible_spend"


def test_syn_miles_buffers_hand_computed():
    # buffer(400000) = max(5000, 2%*400000=8000) = 8000
    # buffer(800000) = max(5000, 2%*800000=16000) = 16000
    card = CardBreakpointInputs(card_key="syn_miles", thresholds=(SYN_MILES_THRESHOLD,))
    breakpoints = {bp.threshold_spend: bp for bp in compile_breakpoints([card])}
    assert breakpoints[Decimal("400000")].buffer == Decimal("8000")
    assert breakpoints[Decimal("800000")].buffer == Decimal("16000.00")


# ---------------------------------------------------------------------------
# syn_ecom: monthly cap boundary, converted to spend via A.3's Sbar=Cap/a
# ---------------------------------------------------------------------------

def test_syn_ecom_monthly_cap_boundary_appears():
    card = CardBreakpointInputs(card_key="syn_ecom", caps=(SYN_ECOM_CAP,), accruals=SYN_ECOM_ACCRUALS)
    breakpoints = compile_breakpoints([card])

    assert len(breakpoints) == 1
    cap_bp = breakpoints[0]
    assert cap_bp.card_key == "syn_ecom"
    assert cap_bp.source_type == "cap"
    assert cap_bp.source_key == "cap_ecom"
    assert cap_bp.tier_index is None
    assert cap_bp.threshold_spend == Decimal("20000")  # 1000 / 0.05
    assert cap_bp.window == MONTH
    assert cap_bp.scope == "rule"


def test_syn_ecom_cap_buffer_hits_the_floor():
    # buffer(20000) = max(5000, 2%*20000=400) = 5000 (floor wins)
    card = CardBreakpointInputs(card_key="syn_ecom", caps=(SYN_ECOM_CAP,), accruals=SYN_ECOM_ACCRUALS)
    breakpoints = compile_breakpoints([card])
    assert breakpoints[0].buffer == Decimal("5000")


def test_default_buffer_formula():
    assert default_buffer(Decimal("100000")) == Decimal("5000")   # 2% = 2000, floor wins
    assert default_buffer(Decimal("1000000")) == Decimal("20000")  # 2% = 20000, rate wins


# ---------------------------------------------------------------------------
# Two-card portfolio (syn_miles + syn_ecom, both thresholds and caps)
# ---------------------------------------------------------------------------

def test_two_card_portfolio_compiles_every_breakpoint_tagged_by_card():
    miles = CardBreakpointInputs(card_key="syn_miles", thresholds=(SYN_MILES_THRESHOLD,))
    ecom = CardBreakpointInputs(
        card_key="syn_ecom", thresholds=(SYN_ECOM_WAIVER_THRESHOLD,), caps=(SYN_ECOM_CAP,), accruals=SYN_ECOM_ACCRUALS,
    )
    breakpoints = compile_breakpoints([miles, ecom])

    assert len(breakpoints) == 4  # 2 miles tiers + 1 ecom waiver tier + 1 ecom cap
    by_card = {}
    for bp in breakpoints:
        by_card.setdefault(bp.card_key, []).append(bp)
    assert len(by_card["syn_miles"]) == 2
    assert len(by_card["syn_ecom"]) == 2
    assert {bp.source_type for bp in by_card["syn_ecom"]} == {"threshold", "cap"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_spend_measure_cap_raises():
    bad_cap = Cap(key="band1", rule_key="slab1", measure="spend", amount=Decimal("100000"), window=ANNIV, scope="rule", overflow="zero")
    card = CardBreakpointInputs(card_key="syn_slab", caps=(bad_cap,), accruals={"slab1": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn")})
    with pytest.raises(ValueError, match="measure"):
        compile_breakpoints([card])
