"""Unit tests for Stage 8 (engine/valuation.py), Part A SS A.7 / Part C SS C.2.9.

Currency/route fixtures are hand-transcribed from seeds/synthetic_cards.py's
CURRENCIES list (cashback_inr, synth_points), not invented. Expected values
are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.accrue import Accrual, AccrualResult
from engine.normalise import SpendSegment
from engine.valuation import RedemptionRoute, RewardCurrency, value_accrual_results, value_currency

CASHBACK_INR = RewardCurrency(key="cashback_inr", routes=(
    RedemptionRoute(key="stmt", route_type="statement_credit", ratio=Decimal("1.0")),
))

SYNTH_POINTS = RewardCurrency(key="synth_points", routes=(
    RedemptionRoute(key="stmt", route_type="statement_credit", ratio=Decimal("0.25")),
    RedemptionRoute(key="voucher", route_type="voucher", ratio=Decimal("0.35")),
    RedemptionRoute(key="portal", route_type="travel_portal", ratio=Decimal("0.5"), friction=Decimal("0.9")),
    RedemptionRoute(
        key="transfer", route_type="transfer", ratio=Decimal("1.0"), friction=Decimal("0.8"),
        transfer_partner="synth_air", transfer_ratio=Decimal("1.0"), partner_point_value=Decimal("1.0"),
        min_points=Decimal("5000"),
    ),
))


# ---------------------------------------------------------------------------
# cashback_inr: v === 1 (C.2.2), single route, no primary declaration needed
# ---------------------------------------------------------------------------

def test_cashback_currency_values_at_exactly_one_to_one():
    result = value_currency(CASHBACK_INR, Decimal("14400"))
    assert result.v_exp_rupees == Decimal("14400")
    assert result.v_exp_route_key == "stmt"
    assert result.v_cons_rupees == Decimal("14400")
    assert result.v_opt_rupees == Decimal("14400")
    assert result.flags == ()


# ---------------------------------------------------------------------------
# synth_points: all four routes, hand-computed per-point rates
#   stmt:    0.25 * 1.0 (default friction)      = 0.25/pt
#   voucher: 0.35 * 1.0 (default friction)      = 0.35/pt
#   portal:  0.50 * 0.9                          = 0.45/pt
#   transfer:(1.0 transfer_ratio * 1.0 partner)  * 0.8 = 0.80/pt (min 5,000 pts)
# ---------------------------------------------------------------------------

def test_synth_points_each_route_hand_computed_at_10000_points():
    points = Decimal("10000")
    assert value_currency(SYNTH_POINTS, points, "stmt").v_exp_rupees == Decimal("2500.0")
    assert value_currency(SYNTH_POINTS, points, "voucher").v_exp_rupees == Decimal("3500.0")
    assert value_currency(SYNTH_POINTS, points, "portal").v_exp_rupees == Decimal("4500.00")
    assert value_currency(SYNTH_POINTS, points, "transfer").v_exp_rupees == Decimal("8000.0")


def test_synth_points_v_cons_and_v_opt_at_10000_points():
    # v_cons = max(stmt 2500, voucher 3500) = 3500 (voucher)
    # v_opt  = max(all four, transfer eligible at 10,000>=5,000) = 8000 (transfer)
    result = value_currency(SYNTH_POINTS, Decimal("10000"), "stmt")
    assert result.v_cons_rupees == Decimal("3500.0")
    assert result.v_opt_rupees == Decimal("8000.0")
    # v_exp still prices at the DECLARED route (stmt), never silently at v_opt
    assert result.v_exp_rupees == Decimal("2500.0")


def test_synth_points_transfer_below_min_points_excluded_from_range_and_flagged_if_primary():
    points = Decimal("3000")  # below transfer's min_points=5,000
    # transfer excluded -> v_cons = max(750, 1050) = 1050 (voucher);
    # v_opt = max(750, 1050, 1350) = 1350 (portal, transfer no longer eligible)
    result = value_currency(SYNTH_POINTS, points, "voucher")
    assert result.v_cons_rupees == Decimal("1050.0")
    assert result.v_opt_rupees == Decimal("1350.00")

    # if the user's OWN declared route is the one that's unreachable, price
    # at Rs0 rather than silently using the rate for points they can't move.
    transfer_primary = value_currency(SYNTH_POINTS, points, "transfer")
    assert transfer_primary.v_exp_rupees == Decimal("0")
    assert transfer_primary.flags == ("min_points_not_met",)


def test_multi_route_currency_without_primary_declared_raises():
    with pytest.raises(ValueError, match="primary route must be declared"):
        value_currency(SYNTH_POINTS, Decimal("10000"))


def test_unknown_primary_route_raises():
    with pytest.raises(ValueError, match="unknown primary route"):
        value_currency(SYNTH_POINTS, Decimal("10000"), "crypto")


# ---------------------------------------------------------------------------
# per_point_fee (no seed route sets this -- hand-built)
# ---------------------------------------------------------------------------

def test_per_point_fee_subtracted_from_the_rate():
    currency = RewardCurrency(key="feeful", routes=(
        RedemptionRoute(key="r", route_type="voucher", ratio=Decimal("0.5"), per_point_fee=Decimal("0.05")),
    ))
    # (0.5 * 1.0 default friction) - 0.05 = 0.45/pt * 1000 = 450.00
    result = value_currency(currency, Decimal("1000"), "r")
    assert result.v_exp_rupees == Decimal("450.00")


# ---------------------------------------------------------------------------
# value_accrual_results: groups by currency via each rule's Accrual.currency
# ---------------------------------------------------------------------------

def test_value_accrual_results_groups_and_sums_by_currency():
    accruals = {
        "base": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn", currency="cashback_inr"),
        "ecom": Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn", currency="cashback_inr"),
    }
    seg = SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("700"))
    results = [
        AccrualResult(rule_key="base", segment=seg, reward=Decimal("100.00")),
        AccrualResult(rule_key="ecom", segment=seg, reward=Decimal("50.00")),
    ]
    valuations = value_accrual_results(results, accruals, {"cashback_inr": CASHBACK_INR})
    assert len(valuations) == 1
    assert valuations[0].currency_key == "cashback_inr"
    assert valuations[0].points == Decimal("150.00")
    assert valuations[0].v_exp_rupees == Decimal("150.00")


def test_value_accrual_results_handles_two_currencies_and_explicit_primary_routes():
    accruals = {
        "base": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn", currency="cashback_inr"),
        "portal_bonus": Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("20"), rounding="floor_per_txn", currency="synth_points"),
    }
    seg = SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("700"))
    results = [
        AccrualResult(rule_key="base", segment=seg, reward=Decimal("100.00")),
        AccrualResult(rule_key="portal_bonus", segment=seg, reward=Decimal("10000")),
    ]
    valuations = value_accrual_results(
        results, accruals,
        {"cashback_inr": CASHBACK_INR, "synth_points": SYNTH_POINTS},
        primary_routes={"synth_points": "transfer"},
    )
    by_currency = {v.currency_key: v for v in valuations}
    assert by_currency["cashback_inr"].v_exp_rupees == Decimal("100.00")
    assert by_currency["synth_points"].v_exp_rupees == Decimal("8000")  # 10,000 * 0.8


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_route_type_raises():
    currency = RewardCurrency(key="bad", routes=(RedemptionRoute(key="r", route_type="crypto", ratio=Decimal("1")),))
    with pytest.raises(ValueError, match="unknown route_type"):
        value_currency(currency, Decimal("100"), "r")


def test_transfer_route_missing_transfer_fields_raises():
    currency = RewardCurrency(key="bad", routes=(RedemptionRoute(key="r", route_type="transfer"),))
    with pytest.raises(ValueError, match="transfer_ratio and partner_point_value"):
        value_currency(currency, Decimal("100"), "r")


def test_non_transfer_route_missing_ratio_raises():
    currency = RewardCurrency(key="bad", routes=(RedemptionRoute(key="r", route_type="voucher"),))
    with pytest.raises(ValueError, match="require ratio"):
        value_currency(currency, Decimal("100"), "r")
