"""Unit tests for the minimal Stage 5 slice (engine/caps.py). See its module
docstring and docs/DECISIONS.md for scope. Expected values are hand-computed
constants (CLAUDE.md rule 1) -- this is the same arithmetic as
golden_syn_ecom_basic.json's hand computation, isolated to caps.py alone.
"""
from decimal import Decimal

import pytest

from engine.accrue import Accrual, AccrualResult
from engine.caps import Cap, apply_caps
from engine.match import EarningRule, Selector
from engine.normalise import SpendSegment

BASE_RULE = EarningRule(key="base", selector=Selector(), priority=10, stacks_with_base=False)
ECOM_RULE = EarningRule(key="ecom", selector=Selector(categories=("ecommerce",), channels=("online",)), priority=100, stacks_with_base=False)
ACCRUALS = {
    "base": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn"),
    "ecom": Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn"),
}
CAP_ECOM = Cap(key="cap_ecom", rule_key="ecom", measure="reward", amount=Decimal("1000"), window="calendar_month", scope="rule", overflow="base_rate")


def _ecom_segment(amount, month=1):
    return SpendSegment(category="ecommerce", channel="online", month=month, amount=Decimal(amount), ticket_size=Decimal("1800"))


def test_cap_binds_with_base_rate_overflow_matches_golden_hand_computation():
    # 30,000 * 5% = 1,500 uncapped, cap=1,000 -> Sbar=1000/0.05=20,000,
    # overflow spend = 10,000 * 1% (base) = 100. Capped total = 1,000+100=1,100.
    segment = _ecom_segment("30000")
    uncapped = [AccrualResult(rule_key="ecom", segment=segment, reward=Decimal("1500.00"))]

    results = apply_caps(uncapped, [CAP_ECOM], [BASE_RULE, ECOM_RULE], ACCRUALS)

    ecom_results = [r for r in results if r.rule_key == "ecom"]
    overflow_results = [r for r in results if r.rule_key == "base" and "cap_overflow" in r.flags]
    assert len(ecom_results) == 1
    assert ecom_results[0].reward == Decimal("1000")
    assert len(overflow_results) == 1
    assert overflow_results[0].reward == Decimal("100.00")
    assert overflow_results[0].segment.amount == Decimal("10000")
    assert sum((r.reward for r in results), Decimal("0")) == Decimal("1100.00")


def test_cap_does_not_bind_when_under_threshold():
    segment = _ecom_segment("15000")  # 15000*5%=750, under the 1000 cap
    uncapped = [AccrualResult(rule_key="ecom", segment=segment, reward=Decimal("750.00"))]

    results = apply_caps(uncapped, [CAP_ECOM], [BASE_RULE, ECOM_RULE], ACCRUALS)

    assert len(results) == 1
    assert results[0].reward == Decimal("750.00")


def test_cap_with_zero_overflow_discards_excess():
    cap = Cap(key="cap_zero", rule_key="ecom", measure="reward", amount=Decimal("1000"), window="calendar_month", scope="rule", overflow="zero")
    segment = _ecom_segment("30000")
    uncapped = [AccrualResult(rule_key="ecom", segment=segment, reward=Decimal("1500.00"))]

    results = apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)

    assert len(results) == 1
    assert results[0].reward == Decimal("1000")


def test_uncapped_rule_passes_through_unaffected():
    segment = SpendSegment(category="grocery", channel=None, month=1, amount=Decimal("10000"), ticket_size=Decimal("700"))
    uncapped = [AccrualResult(rule_key="base", segment=segment, reward=Decimal("100.00"))]

    results = apply_caps(uncapped, [CAP_ECOM], [BASE_RULE, ECOM_RULE], ACCRUALS)

    assert results == tuple(uncapped)


@pytest.mark.parametrize(
    "override,error_snippet",
    [
        ({"measure": "spend"}, "measure"),
        ({"window": "quarter"}, "window"),
        ({"scope": "rule_group:x"}, "scope"),
        ({"overflow": "half_rate"}, "overflow mode"),
    ],
)
def test_unsupported_cap_configuration_raises(override, error_snippet):
    fields = dict(key="cap_ecom", rule_key="ecom", measure="reward", amount=Decimal("1000"), window="calendar_month", scope="rule", overflow="base_rate")
    fields.update(override)
    cap = Cap(**fields)
    segment = _ecom_segment("30000")
    uncapped = [AccrualResult(rule_key="ecom", segment=segment, reward=Decimal("1500.00"))]

    with pytest.raises(ValueError, match=error_snippet):
        apply_caps(uncapped, [cap], [BASE_RULE, ECOM_RULE], ACCRUALS)


def test_multi_segment_month_for_same_capped_rule_raises():
    seg_a = _ecom_segment("20000")
    seg_b = _ecom_segment("10000")
    uncapped = [
        AccrualResult(rule_key="ecom", segment=seg_a, reward=Decimal("1000.00")),
        AccrualResult(rule_key="ecom", segment=seg_b, reward=Decimal("500.00")),
    ]
    with pytest.raises(ValueError, match="multi-segment cap months"):
        apply_caps(uncapped, [CAP_ECOM], [BASE_RULE, ECOM_RULE], ACCRUALS)
