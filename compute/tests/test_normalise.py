"""Unit tests for Stage 1 (engine/normalise.py), Part C SS C.4 Stage 1 / C.4.1.

Expected values below are hand-computed constants (CLAUDE.md rule 1: tests
assert against hand-computed values, they never re-derive via the code
under test).
"""
from decimal import Decimal

import pytest

from engine.normalise import (
    AssumptionsSnapshot,
    CategorySpend,
    SpendInput,
    UpiAggregateSpend,
    normalise,
)


_UNFILTERED = object()


def _amounts_by_month(segments, category=None, channel=_UNFILTERED):
    sel = [
        s
        for s in segments
        if (category is None or s.category == category) and (channel is _UNFILTERED or s.channel == channel)
    ]
    return {s.month: s.amount for s in sel}


# ---------------------------------------------------------------------------
# Uniform seasonality
# ---------------------------------------------------------------------------

def test_uniform_seasonality_evenly_divisible():
    # Rs 84,000/yr grocery, no seasonality supplied -> uniform, divides evenly.
    spend = SpendInput(category_spend=(
        CategorySpend(category="grocery", annual_amount=Decimal("84000")),
    ))
    result = normalise(spend, AssumptionsSnapshot())

    assert len(result.segments) == 12
    amounts = _amounts_by_month(result.segments, category="grocery")
    for month in range(1, 13):
        assert amounts[month] == Decimal("7000.00")
    assert sum(amounts.values()) == Decimal("84000.00")
    assert all(s.channel is None for s in result.segments)
    assert all(s.ticket_size == Decimal("700") for s in result.segments)
    assert all(s.flags == () for s in result.segments)


def test_uniform_seasonality_with_paisa_remainder():
    # Rs 1,00,000/yr does not divide 12 evenly -> hand-computed remainder split:
    # 1,00,00,000 paise / 12 = 8,33,333 paise remainder 4 -> first 4 months
    # get an extra paisa (Rs 8,333.34), the other 8 get Rs 8,333.33.
    spend = SpendInput(category_spend=(
        CategorySpend(category="dining", annual_amount=Decimal("100000")),
    ))
    result = normalise(spend, AssumptionsSnapshot())

    amounts = _amounts_by_month(result.segments, category="dining")
    for month in (1, 2, 3, 4):
        assert amounts[month] == Decimal("8333.34")
    for month in (5, 6, 7, 8, 9, 10, 11, 12):
        assert amounts[month] == Decimal("8333.33")
    assert sum(amounts.values()) == Decimal("100000.00")


# ---------------------------------------------------------------------------
# Non-uniform seasonality
# ---------------------------------------------------------------------------

def test_non_uniform_seasonality_diwali_heavy_ecommerce():
    # Rs 2,40,000/yr ecommerce, Oct/Nov/Dec-heavy (Diwali + year-end sales).
    weights = tuple(
        [Decimal("0.05")] * 9 + [Decimal("0.25"), Decimal("0.20"), Decimal("0.10")]
    )
    assert sum(weights) == Decimal("1.00")
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", annual_amount=Decimal("240000"), seasonality=weights),
    ))
    result = normalise(spend, AssumptionsSnapshot())

    amounts = _amounts_by_month(result.segments, category="ecommerce")
    for month in range(1, 10):
        assert amounts[month] == Decimal("12000.00")
    assert amounts[10] == Decimal("60000.00")
    assert amounts[11] == Decimal("48000.00")
    assert amounts[12] == Decimal("24000.00")
    assert sum(amounts.values()) == Decimal("240000.00")
    assert all(s.ticket_size == Decimal("1800") for s in result.segments)


def test_non_uniform_seasonality_rounding_reconciliation():
    # Rs 1,00,000.01/yr entertainment across 3 months (0.3, 0.3, 0.4, rest 0).
    # Hand computation: 100000.01*0.3 = 30000.003 -> rounds to 30000.00 (x2);
    # 100000.01*0.4 = 40000.004 -> rounds to 40000.00; rounded sum = 100000.00,
    # residual Rs 0.01 lands on the last nonzero-weight month (March).
    weights = tuple([Decimal("0.3"), Decimal("0.3"), Decimal("0.4")] + [Decimal("0")] * 9)
    spend = SpendInput(category_spend=(
        CategorySpend(category="entertainment", annual_amount=Decimal("100000.01"), seasonality=weights),
    ))
    result = normalise(spend, AssumptionsSnapshot())

    amounts = _amounts_by_month(result.segments, category="entertainment")
    assert amounts[1] == Decimal("30000.00")
    assert amounts[2] == Decimal("30000.00")
    assert amounts[3] == Decimal("40000.01")
    for month in range(4, 13):
        assert amounts[month] == Decimal("0.00")
    assert sum(amounts.values()) == Decimal("100000.01")


@pytest.mark.parametrize(
    "weights,error_snippet",
    [
        (tuple([Decimal("1")] * 11), "must have 12 entries"),
        (tuple([Decimal("0.1")] * 12), "must sum to 1"),
        (tuple([Decimal("-0.1")] + [Decimal("0.1")] * 10 + [Decimal("1")]), "negative weight"),
    ],
)
def test_seasonality_validation_errors(weights, error_snippet):
    spend = SpendInput(category_spend=(
        CategorySpend(category="grocery", annual_amount=Decimal("12000"), seasonality=weights),
    ))
    with pytest.raises(ValueError, match=error_snippet):
        normalise(spend, AssumptionsSnapshot())


def test_missing_ticket_size_assumption_raises():
    spend = SpendInput(category_spend=(
        CategorySpend(category="crypto", annual_amount=Decimal("12000")),
    ))
    with pytest.raises(ValueError, match="no ticket-size assumption"):
        normalise(spend, AssumptionsSnapshot())


# ---------------------------------------------------------------------------
# UPI aggregate decomposition (C.4.1)
# ---------------------------------------------------------------------------

def test_upi_aggregate_decomposes_across_default_mix():
    # Rs 10,000/month UPI aggregate, default mix (all multiplications clean):
    # grocery .38->3800, ecommerce .15->1500, dining .12->1200, utilities .10->1000,
    # offline_retail .10->1000, fuel .08->800, entertainment .07->700. Sum=10000.
    spend = SpendInput(upi_aggregate=UpiAggregateSpend(monthly_amount=Decimal("10000")))
    result = normalise(spend, AssumptionsSnapshot())

    assert len(result.segments) == 7 * 12
    assert all(s.channel == "upi" for s in result.segments)
    assert all(s.flags == ("decomposition_assumed",) for s in result.segments)

    expected_monthly = {
        "grocery": Decimal("3800.00"),
        "ecommerce": Decimal("1500.00"),
        "dining": Decimal("1200.00"),
        "utilities": Decimal("1000.00"),
        "offline_retail": Decimal("1000.00"),
        "fuel": Decimal("800.00"),
        "entertainment": Decimal("700.00"),
    }
    for category, expected in expected_monthly.items():
        amounts = _amounts_by_month(result.segments, category=category, channel="upi")
        assert len(amounts) == 12
        assert all(a == expected for a in amounts.values())

    # every month's category shares reconcile exactly to the aggregate
    for month in range(1, 13):
        month_total = sum(s.amount for s in result.segments if s.month == month)
        assert month_total == Decimal("10000.00")

    annual_total = sum(s.amount for s in result.segments)
    assert annual_total == Decimal("120000.00")  # Rs 10,000 x 12


def test_upi_aggregate_rounding_reconciliation():
    # Rs 1,000.03/month does not split cleanly across the 7-way mix; each
    # month's category shares must still reconcile exactly to Rs 1,000.03.
    spend = SpendInput(upi_aggregate=UpiAggregateSpend(monthly_amount=Decimal("1000.03")))
    result = normalise(spend, AssumptionsSnapshot())

    for month in range(1, 13):
        month_total = sum(s.amount for s in result.segments if s.month == month)
        assert month_total == Decimal("1000.03")


def test_upi_aggregate_combines_with_category_spend_as_distinct_segments():
    # A user with both a general grocery line AND a UPI aggregate: the UPI
    # decomposition's grocery share must not merge into the plain grocery
    # line -- they're distinct channel dimensions (C.1 principle 4).
    spend = SpendInput(
        category_spend=(CategorySpend(category="grocery", annual_amount=Decimal("24000")),),
        upi_aggregate=UpiAggregateSpend(monthly_amount=Decimal("5000")),
    )
    result = normalise(spend, AssumptionsSnapshot())

    general_grocery = _amounts_by_month(result.segments, category="grocery", channel=None)
    upi_grocery = _amounts_by_month(result.segments, category="grocery", channel="upi")
    assert sum(general_grocery.values()) == Decimal("24000.00")
    assert sum(upi_grocery.values()) == Decimal("22800.00")  # 5000 * 0.38 * 12
