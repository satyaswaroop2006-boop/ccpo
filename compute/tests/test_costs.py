"""Unit tests for Stage 10 (engine/costs.py), Part A SS A.6 / SS A.10 / SS A.11.

Fee/surcharge fixtures for syn_ecom, syn_travel, and syn_fuel are
hand-transcribed from seeds/synthetic_cards.py, not invented. Expected
values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.caps import Window
from engine.costs import Surcharge, SurchargeWaiver, compute_fees, forex_cost, international_spend_total, surcharge_cost
from engine.match import Selector
from engine.normalise import SpendSegment
from engine.thresholds import Payload, ThresholdEvent

ANNIV_MONTHS = tuple(range(1, 13))


def _waive_fee_event(fee="annual"):
    return ThresholdEvent(
        threshold_key="waiver", tier_index=1, window_months=ANNIV_MONTHS, pooled_spend=Decimal("480000"),
        payload=Payload(type="waive_fee", fee=fee),
    )


# ---------------------------------------------------------------------------
# Fees (A.6) -- syn_ecom: joining=500, annual=500 (matches
# golden_syn_ecom_basic.json's own hand computation exactly)
# ---------------------------------------------------------------------------

def test_syn_ecom_waived_fee_matches_golden_hand_computation():
    result = compute_fees(Decimal("500"), Decimal("500"), [_waive_fee_event()])
    assert result.waived is True
    assert result.steady_fee == Decimal("0")           # "GST none charged"
    assert result.year1_fee == Decimal("590.00")        # "joining fee 500 * 1.18 = 590"


def test_syn_ecom_unwaived_fee_charges_annual_plus_gst_both_years():
    result = compute_fees(Decimal("500"), Decimal("500"), threshold_events=[])
    assert result.waived is False
    assert result.steady_fee == Decimal("590.00")       # 500 * 1.18
    assert result.year1_fee == Decimal("1180.00")       # (500+500) * 1.18


def test_fee_waiver_only_matches_the_named_fee():
    # a waive_fee event for a DIFFERENT fee name doesn't waive "annual"
    # (no card actually has a second fee type today; defensive coverage
    # for the `fee` parameter's matching semantics)
    result = compute_fees(Decimal("500"), Decimal("500"), [_waive_fee_event(fee="supplementary_card")])
    assert result.waived is False
    assert result.steady_fee == Decimal("590.00")


def test_zero_joining_fee_year1_equals_steady_fee_plus_nothing():
    # syn_fuel: joining_fee defaults to 0 when unwaived
    result = compute_fees(Decimal("0"), Decimal("500"), threshold_events=[])
    assert result.steady_fee == Decimal("590.00")
    assert result.year1_fee == Decimal("590.00")


# ---------------------------------------------------------------------------
# Forex (A.10) -- syn_travel is the zero-forex card (forex_markup=0.0)
# ---------------------------------------------------------------------------

def test_zero_forex_card_costs_nothing_regardless_of_intl_spend():
    assert forex_cost(Decimal("500000"), forex_markup=Decimal("0.0")) == Decimal("0.0")


def test_nonzero_forex_markup_hand_computed():
    # hand-built: 3.5% markup (the seed default for non-zero-forex cards),
    # Rs 1,00,000 international spend -> 0.035 * 1.18 * 100000 = 4,130.00
    assert forex_cost(Decimal("100000"), forex_markup=Decimal("0.035")) == Decimal("4130.000")


def test_international_spend_total_sums_only_international_segments():
    segments = [
        SpendSegment(category="hotels_domestic", channel=None, month=1, amount=Decimal("50000"), ticket_size=Decimal("9000")),  # domestic (default)
        SpendSegment(category="international_flights", channel=None, month=2, amount=Decimal("30000"), ticket_size=Decimal("35000"), geography="international"),
        SpendSegment(category="international_flights", channel=None, month=3, amount=Decimal("20000"), ticket_size=Decimal("35000"), geography="international"),
    ]
    assert international_spend_total(segments) == Decimal("50000")


def test_international_spend_total_zero_when_all_domestic():
    segments = [SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("10000"), ticket_size=Decimal("700"))]
    assert international_spend_total(segments) == Decimal("0")


# ---------------------------------------------------------------------------
# Surcharges (A.11) -- syn_fuel's fuel_sur: 1% on fuel, 18% GST on the surcharge
# ---------------------------------------------------------------------------

FUEL_SURCHARGE = Surcharge(key="fuel_sur", selector=Selector(categories=("fuel",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"))


def test_surcharge_applies_only_to_matching_category():
    segments = [
        SpendSegment(category="fuel", channel=None, month=1, amount=Decimal("50000"), ticket_size=Decimal("1500")),
        SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("100000"), ticket_size=Decimal("700")),
    ]
    # only fuel counts: 0.01 * 1.18 * 50,000 = 590.00
    assert surcharge_cost(segments, [FUEL_SURCHARGE]).total == Decimal("590.0000")


def test_no_matching_spend_costs_nothing():
    segments = [SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("100000"), ticket_size=Decimal("700"))]
    assert surcharge_cost(segments, [FUEL_SURCHARGE]).total == Decimal("0")


def test_multiple_surcharges_sum_independently():
    government_surcharge = Surcharge(key="govt_sur", selector=Selector(categories=("government",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"))
    segments = [
        SpendSegment(category="fuel", channel=None, month=1, amount=Decimal("50000"), ticket_size=Decimal("1500")),
        SpendSegment(category="government", channel=None, month=1, amount=Decimal("20000"), ticket_size=Decimal("5000")),
    ]
    # fuel: 0.01*1.18*50000=590.00; government: 0.01*1.18*20000=236.00; sum=826.00
    assert surcharge_cost(segments, [FUEL_SURCHARGE, government_surcharge]).total == Decimal("826.0000")


def test_no_surcharges_defined_costs_nothing():
    segments = [SpendSegment(category="fuel", channel=None, month=1, amount=Decimal("50000"), ticket_size=Decimal("1500"))]
    assert surcharge_cost(segments, []).total == Decimal("0")


# ---------------------------------------------------------------------------
# Surcharge.waiver (Phase 5 Task B, docs/DECISIONS.md #132) -- a capped
# rebate on the surcharge's own rate, computed against the SAME raw
# matched spend as the surcharge itself (never gated by Stage 2's
# rewards-eligibility mask -- see costs.py module docstring for why).
# ---------------------------------------------------------------------------

def _fuel_segment(month, amount):
    return SpendSegment(category="fuel", channel=None, month=month, amount=Decimal(amount), ticket_size=Decimal("1500"))


def test_full_waiver_under_the_cap_zeroes_the_surcharge():
    # CASHBACK SBI's real shape: 1% surcharge, 1% waiver (full), capped
    # Rs100/statement-cycle (~= Rs10,000 of fuel spend/month at 1%).
    surcharge = Surcharge(
        key="fuel_sur", selector=Selector(categories=("fuel",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"),
        waiver=SurchargeWaiver(rate=Decimal("0.01"), cap_amount=Decimal("100"), cap_window=Window(kind="statement_cycle")),
    )
    segments = [_fuel_segment(1, "8000")]  # well under the Rs10,000/month waiver-cap equivalent
    result = surcharge_cost(segments, [surcharge])
    # gross = 0.01*1.18*8000=94.40; waived_base=min(0.01*8000,100)=80; waived=80*1.18=94.40 -> net 0
    assert result.total == Decimal("0")


def test_waiver_caps_at_the_stated_amount_leaving_a_residual_surcharge():
    surcharge = Surcharge(
        key="fuel_sur", selector=Selector(categories=("fuel",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"),
        waiver=SurchargeWaiver(rate=Decimal("0.01"), cap_amount=Decimal("100"), cap_window=Window(kind="statement_cycle")),
    )
    segments = [_fuel_segment(1, "20000")]  # 1% waiver base = Rs200, capped at Rs100
    result = surcharge_cost(segments, [surcharge])
    # gross = 0.01*1.18*20000=236.00; waived=100*1.18=118.00; net=236.00-118.00=118.00
    assert result.total == Decimal("118.00")


def test_waiver_cap_resets_every_window_instance_not_pooled_annually():
    surcharge = Surcharge(
        key="fuel_sur", selector=Selector(categories=("fuel",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"),
        waiver=SurchargeWaiver(rate=Decimal("0.01"), cap_amount=Decimal("100"), cap_window=Window(kind="statement_cycle")),
    )
    # Rs20,000 fuel spend EVERY month for a year -- if the cap were pooled
    # annually (Rs100 total) almost none of this would be waived; per-
    # instance resetting waives Rs100/month, every month.
    segments = [_fuel_segment(m, "20000") for m in range(1, 13)]
    result = surcharge_cost(segments, [surcharge])
    # per month: gross=236.00, waived=118.00, net=118.00 -> annual = 118.00*12
    assert result.total == Decimal("1416.00")


def test_txn_bound_on_waiver_is_accepted_but_unenforced_and_flagged():
    """txn_min/txn_max can't be tested against a category-mode aggregate --
    the waiver applies to the FULL matched spend (not silently trimmed to
    an estimated in-band share), flagged instead (Phase 5 Task A's
    already-established posture)."""
    surcharge = Surcharge(
        key="fuel_sur", selector=Selector(categories=("fuel",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"),
        waiver=SurchargeWaiver(
            rate=Decimal("0.01"), cap_amount=Decimal("100"), cap_window=Window(kind="statement_cycle"),
            txn_min=Decimal("500"), txn_max=Decimal("3000"),
        ),
    )
    segments = [_fuel_segment(1, "8000")]
    result = surcharge_cost(segments, [surcharge])
    assert result.total == Decimal("0")  # same as the no-txn-bound case above -- not filtered
    # cycle_approximated also fires (statement_cycle window, same as any
    # other statement-cycle cap/window) -- both are genuinely present.
    assert result.flags == ("cycle_approximated", "txn_threshold_unenforced")


def test_waiver_rate_exceeding_surcharge_rate_raises():
    bad = Surcharge(
        key="fuel_sur", selector=Selector(categories=("fuel",)), rate=Decimal("0.01"),
        waiver=SurchargeWaiver(rate=Decimal("0.02"), cap_amount=Decimal("100"), cap_window=Window(kind="statement_cycle")),
    )
    with pytest.raises(ValueError, match="exceeds the surcharge's own rate"):
        surcharge_cost([_fuel_segment(1, "1000")], [bad])
