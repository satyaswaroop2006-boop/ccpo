"""Unit tests for Stage 5 (engine/caps.py), Part C SS C.2.3 / SS C.2.4 / SS C.4 Stage 5.

Expected values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.accrue import Accrual, AccrualResult
from engine.caps import Cap, Window, apply_caps
from engine.match import EarningRule, Selector
from engine.normalise import SpendSegment

BASE_RULE = EarningRule(key="base", selector=Selector(), priority=10, stacks_with_base=False)
ECOM_RULE = EarningRule(key="ecom", selector=Selector(categories=("ecommerce",), channels=("online",)), priority=100, stacks_with_base=False)
ACCRUALS = {
    "base": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn"),
    "ecom": Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn"),
}
CAP_ECOM_MONTHLY = Cap(key="cap_ecom", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="calendar_month"), scope="rule", overflow="base_rate")


def _ecom_segment(amount, month):
    return SpendSegment(category="ecommerce", channel="online", month=month, amount=Decimal(amount), ticket_size=Decimal("1800"))


def _ecom_result(amount, month, reward):
    return AccrualResult(rule_key="ecom", segment=_ecom_segment(amount, month), reward=Decimal(reward))


# ---------------------------------------------------------------------------
# Single-month binding (syn_ecom's actual shape) -- same math as the golden
# ---------------------------------------------------------------------------

def test_monthly_cap_binds_with_base_rate_overflow_matches_golden_hand_computation():
    uncapped = [_ecom_result("30000", 1, "1500.00")]
    results = apply_caps(uncapped, [CAP_ECOM_MONTHLY], [BASE_RULE, ECOM_RULE], ACCRUALS)

    ecom_results = [r for r in results if r.rule_key == "ecom"]
    overflow_results = [r for r in results if r.rule_key == "base" and "cap_overflow" in r.flags]
    assert ecom_results[0].reward == Decimal("1000")
    assert overflow_results[0].reward == Decimal("100.00")
    assert overflow_results[0].segment.amount == Decimal("10000")
    assert sum((r.reward for r in results), Decimal("0")) == Decimal("1100.00")


def test_monthly_cap_does_not_bind_when_under_threshold():
    uncapped = [_ecom_result("15000", 1, "750.00")]
    results = apply_caps(uncapped, [CAP_ECOM_MONTHLY], [BASE_RULE, ECOM_RULE], ACCRUALS)
    assert results[0].reward == Decimal("750.00")


def test_zero_overflow_discards_excess():
    cap = Cap(key="cap_zero", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="calendar_month"), scope="rule", overflow="zero")
    uncapped = [_ecom_result("30000", 1, "1500.00")]
    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)
    assert len(results) == 1
    assert results[0].reward == Decimal("1000")


def test_uncapped_rule_passes_through_unaffected():
    segment = SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("10000"), ticket_size=Decimal("700"))
    uncapped = [AccrualResult(rule_key="base", segment=segment, reward=Decimal("100.00"))]
    results = apply_caps(uncapped, [CAP_ECOM_MONTHLY], [BASE_RULE, ECOM_RULE], ACCRUALS)
    assert results == tuple(uncapped)


# ---------------------------------------------------------------------------
# rule_group scope (syn_points' cap_portal shape)
# ---------------------------------------------------------------------------

def test_rule_group_scope_pools_every_rule_tagged_with_that_group():
    base = EarningRule(key="base", selector=Selector(), priority=10)
    bonus_a = EarningRule(key="bonus_a", selector=Selector(categories=("travel",)), priority=100, rule_group="accel")
    bonus_b = EarningRule(key="bonus_b", selector=Selector(categories=("travel",)), priority=90, rule_group="accel")
    accruals = {
        "base": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn"),
        "bonus_a": Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn"),
        "bonus_b": Accrual(type="percentage", rate=Decimal("0.03"), rounding="floor_paise_per_txn"),
    }
    cap = Cap(key="cap_group", rule_key="bonus_a", measure="reward", amount=Decimal("1000"), window=Window(kind="calendar_month"), scope="rule_group:accel", overflow="zero")
    # bonus_a and bonus_b both post to the group's pooled cap even though the
    # cap is declared (rule_key=) on bonus_a only.
    seg = SpendSegment(category="travel", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("1000"))
    uncapped = [
        AccrualResult(rule_key="bonus_a", segment=seg, reward=Decimal("700.00")),
        AccrualResult(rule_key="bonus_b", segment=seg, reward=Decimal("600.00")),
    ]
    results = apply_caps(uncapped, [cap], [base, bonus_a, bonus_b], accruals)
    assert sum((r.reward for r in results), Decimal("0")) == Decimal("1000.00")


def test_zero_overflow_at_group_scope_matches_syn_points_cap_portal_shape():
    # Mirrors syn_points: base (uncapped, unrelated rule_group) + portal_bonus
    # (rule_group="portal_accel", cap 15,000/mo, overflow zero).
    base = EarningRule(key="base", selector=Selector(), priority=10)
    portal_bonus = EarningRule(key="portal_bonus", selector=Selector(merchant_groups=("synth_portal",)), priority=100, stacks_with_base=True, rule_group="portal_accel")
    accruals = {
        "base": Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("5"), rounding="floor_per_txn"),
        "portal_bonus": Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("20"), rounding="floor_per_txn"),
    }
    cap = Cap(key="cap_portal", rule_key="portal_bonus", measure="reward", amount=Decimal("15000"), window=Window(kind="calendar_month"), scope="rule_group:portal_accel", overflow="zero")
    seg = SpendSegment(category="travel", channel=None, month=1, amount=Decimal("150000"), ticket_size=Decimal("150"), merchant_group="synth_portal")
    # 150,000 spend * 20pts/150 = 20,000 pts uncapped -> exceeds 15,000 cap.
    uncapped = [AccrualResult(rule_key="portal_bonus", segment=seg, reward=Decimal("20000"))]

    results = apply_caps(uncapped, [cap], [base, portal_bonus], accruals)
    assert len(results) == 1
    assert results[0].reward == Decimal("15000")


# ---------------------------------------------------------------------------
# card scope
# ---------------------------------------------------------------------------

def test_card_scope_pools_every_rule_on_the_card():
    r1 = EarningRule(key="r1", selector=Selector(categories=("a",)), priority=10)
    r2 = EarningRule(key="r2", selector=Selector(categories=("b",)), priority=10)
    accruals = {
        "r1": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn"),
        "r2": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn"),
    }
    cap = Cap(key="cap_card", rule_key="r1", measure="reward", amount=Decimal("100"), window=Window(kind="calendar_month"), scope="card", overflow="zero")
    seg_a = SpendSegment(category="a", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("100"))
    seg_b = SpendSegment(category="a", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("100"))
    uncapped = [
        AccrualResult(rule_key="r1", segment=seg_a, reward=Decimal("60.00")),
        AccrualResult(rule_key="r2", segment=seg_b, reward=Decimal("60.00")),
    ]
    results = apply_caps(uncapped, [cap], [r1, r2], accruals)
    assert sum((r.reward for r in results), Decimal("0")) == Decimal("100.00")


# ---------------------------------------------------------------------------
# Multi-month pooling: quarterly/annual windows, chronological crossing point
# ---------------------------------------------------------------------------

def test_quarterly_window_pools_three_months_running_total_crosses_mid_quarter():
    # Jan=400, Feb=400, Mar=400 (same rule/category) pooled into Q1, cap=900.
    # Running total: Jan keeps 400 (total 400), Feb keeps 400 (total 800),
    # Mar crosses at 800+400=1200>900 -> Mar allowed = 900-800=100, excess=300.
    cap = Cap(key="cap_q", rule_key="ecom", measure="reward", amount=Decimal("900"), window=Window(kind="quarter", alignment="calendar"), scope="rule", overflow="base_rate")
    uncapped = [_ecom_result("8000", 1, "400.00"), _ecom_result("8000", 2, "400.00"), _ecom_result("8000", 3, "400.00")]
    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)

    by_month = {r.segment.month: r for r in results if r.rule_key == "ecom"}
    assert by_month[1].reward == Decimal("400.00")  # untouched, before the crossing point
    assert by_month[2].reward == Decimal("400.00")  # untouched, before the crossing point
    assert by_month[3].reward == Decimal("100")      # trimmed at the crossing point

    overflow = [r for r in results if r.rule_key == "base" and "cap_overflow" in r.flags]
    assert len(overflow) == 1
    # excess_reward=300 at ecom's 5% rate -> excess_spend=6000, re-rated at base's 1% = 60.00
    assert overflow[0].segment.amount == Decimal("6000")
    assert overflow[0].reward == Decimal("60.00")

    # April (month 4) is a different quarter instance -- untouched.
    assert 4 not in by_month  # not in this fixture, but confirms no cross-quarter leakage
    assert sum((r.reward for r in results), Decimal("0")) == Decimal("400.00") + Decimal("400.00") + Decimal("100") + Decimal("60.00")


def test_month_after_crossing_point_is_fully_overflow():
    # Jan=1200 alone already exceeds cap=1000 -> Feb (same quarter) is
    # entirely past the cap, fully overflow, zero kept.
    cap = Cap(key="cap_q", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="quarter", alignment="calendar"), scope="rule", overflow="zero")
    uncapped = [_ecom_result("24000", 1, "1200.00"), _ecom_result("8000", 2, "400.00")]
    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)

    by_month = {r.segment.month: r for r in results if r.rule_key == "ecom"}
    assert by_month[1].reward == Decimal("1000")  # trimmed at the crossing point (Jan itself)
    assert by_month[2].reward == Decimal("0")      # fully past the cap


def test_annual_window_pools_all_twelve_months():
    cap = Cap(key="cap_year", rule_key="ecom", measure="reward", amount=Decimal("5000"), window=Window(kind="calendar_year"), scope="rule", overflow="zero")
    uncapped = [_ecom_result("12000", m, "600.00") for m in range(1, 13)]  # 12 * 600 = 7200 > 5000
    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)
    assert sum((r.reward for r in results if r.rule_key == "ecom"), Decimal("0")) == Decimal("5000")


def test_anniversary_year_window_flags_anniversary_approximated():
    cap = Cap(key="cap_anniv", rule_key="ecom", measure="reward", amount=Decimal("5000"), window=Window(kind="anniversary_year"), scope="rule", overflow="zero")
    uncapped = [_ecom_result("12000", m, "600.00") for m in range(1, 13)]
    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)
    capped = [r for r in results if r.rule_key == "ecom" and r.reward < Decimal("600.00")]
    assert capped and all("anniversary_approximated" in r.flags for r in capped)


def test_statement_cycle_window_flags_cycle_approximated():
    cap = Cap(key="cap_stmt", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="statement_cycle"), scope="rule", overflow="base_rate")
    uncapped = [_ecom_result("30000", 1, "1500.00")]
    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)
    ecom_result = next(r for r in results if r.rule_key == "ecom")
    assert "cycle_approximated" in ecom_result.flags


# ---------------------------------------------------------------------------
# Nesting: a monthly cap and a yearly cap on the same rule compose
# ---------------------------------------------------------------------------

def test_nested_monthly_and_annual_caps_apply_finer_window_first():
    # Monthly cap 1,000 trims every month's 1,500 down to 1,000+overflow.
    # A further annual cap of 10,000 on the (already monthly-capped) ecom
    # totals (12*1,000=12,000) trims the annual total down to 10,000.
    monthly = Cap(key="cap_month", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="calendar_month"), scope="rule", overflow="zero")
    annual = Cap(key="cap_year", rule_key="ecom", measure="reward", amount=Decimal("10000"), window=Window(kind="calendar_year"), scope="rule", overflow="zero")
    uncapped = [_ecom_result("30000", m, "1500.00") for m in range(1, 13)]

    results = apply_caps(uncapped, [monthly, annual], [BASE_RULE, ECOM_RULE], ACCRUALS)
    ecom_total = sum((r.reward for r in results if r.rule_key == "ecom"), Decimal("0"))
    assert ecom_total == Decimal("10000")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "override,error_snippet",
    [
        ({"measure": "spend"}, "measure"),
        ({"scope": "bogus"}, "scope"),
        ({"overflow": "half_rate"}, "overflow mode"),
    ],
)
def test_unsupported_cap_configuration_raises(override, error_snippet):
    fields = dict(key="cap_ecom", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="calendar_month"), scope="rule", overflow="base_rate")
    fields.update(override)
    cap = Cap(**fields)
    uncapped = [_ecom_result("30000", 1, "1500.00")]
    with pytest.raises(ValueError, match=error_snippet):
        apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)


def test_unknown_window_kind_raises():
    cap = Cap(key="cap_bad", rule_key="ecom", measure="reward", amount=Decimal("1000"), window=Window(kind="fortnight"), scope="rule", overflow="zero")
    uncapped = [_ecom_result("30000", 1, "1500.00")]
    with pytest.raises(ValueError, match="window kind"):
        apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)


def test_multi_category_pooled_window_raises():
    cap = Cap(key="cap_group", rule_key="ecom", measure="reward", amount=Decimal("100"), window=Window(kind="calendar_year"), scope="card", overflow="zero")
    seg_a = SpendSegment(category="ecommerce", channel="online", month=1, amount=Decimal("1000"), ticket_size=Decimal("1800"))
    seg_b = SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("700"))
    uncapped = [
        AccrualResult(rule_key="ecom", segment=seg_a, reward=Decimal("60.00")),
        AccrualResult(rule_key="base", segment=seg_b, reward=Decimal("60.00")),
    ]
    with pytest.raises(ValueError, match="distinct"):
        apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)
