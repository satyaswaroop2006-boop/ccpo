"""Unit tests for Stage 10 (engine/costs.py), Part A SS A.6 / SS A.10 / SS A.11.

Fee/surcharge fixtures for syn_ecom, syn_travel, and syn_fuel are
hand-transcribed from seeds/synthetic_cards.py, not invented. Expected
values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

from engine.costs import Surcharge, compute_fees, forex_cost, international_spend_total, surcharge_cost
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
    assert surcharge_cost(segments, [FUEL_SURCHARGE]) == Decimal("590.0000")


def test_no_matching_spend_costs_nothing():
    segments = [SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("100000"), ticket_size=Decimal("700"))]
    assert surcharge_cost(segments, [FUEL_SURCHARGE]) == Decimal("0")


def test_multiple_surcharges_sum_independently():
    government_surcharge = Surcharge(key="govt_sur", selector=Selector(categories=("government",)), rate=Decimal("0.01"), gst_on_surcharge=Decimal("0.18"))
    segments = [
        SpendSegment(category="fuel", channel=None, month=1, amount=Decimal("50000"), ticket_size=Decimal("1500")),
        SpendSegment(category="government", channel=None, month=1, amount=Decimal("20000"), ticket_size=Decimal("5000")),
    ]
    # fuel: 0.01*1.18*50000=590.00; government: 0.01*1.18*20000=236.00; sum=826.00
    assert surcharge_cost(segments, [FUEL_SURCHARGE, government_surcharge]) == Decimal("826.0000")


def test_no_surcharges_defined_costs_nothing():
    segments = [SpendSegment(category="fuel", channel=None, month=1, amount=Decimal("50000"), ticket_size=Decimal("1500"))]
    assert surcharge_cost(segments, []) == Decimal("0")
