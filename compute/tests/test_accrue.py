"""Unit tests for Stage 4 (engine/accrue.py), Part C SS C.2.2 / SS C.6, Part A SS A.2.

Expected values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.accrue import Accrual, accrue_category_mode, accrue_transaction, accrue_transactions
from engine.match import RuleBinding
from engine.normalise import SpendSegment

PER_UNIT_150_5 = Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("5"), rounding="floor_per_txn")
PCT_5 = Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn")


def _segment(category, amount, ticket_size, month=1, channel=None):
    return SpendSegment(category=category, channel=channel, month=month, amount=Decimal(amount), ticket_size=Decimal(ticket_size))


# ---------------------------------------------------------------------------
# Transaction mode -- exact per-transaction floor (A.2 exact form)
# ---------------------------------------------------------------------------

def test_transaction_mode_per_unit_floor_matches_a2_worked_example():
    # A.2's own example: 5pts/Rs150, Rs800 transaction -> floor(800/150)*5 = 25.
    assert accrue_transaction(PER_UNIT_150_5, Decimal("800")) == Decimal("25")


def test_transaction_mode_per_unit_exact_multiple_no_floor_loss():
    # Rs 450 = exactly 3 units of Rs150 -> floor(450/150)*5 = 3*5 = 15, no loss.
    assert accrue_transaction(PER_UNIT_150_5, Decimal("450")) == Decimal("15")


def test_transaction_mode_percentage_floors_to_paisa():
    # Rs 833.33 * 5% = 41.6665 -> floors to 41.66.
    accrual = Accrual(type="percentage", rate=Decimal("0.05"), rounding="floor_paise_per_txn")
    assert accrue_transaction(accrual, Decimal("833.33")) == Decimal("41.66")


def test_accrue_transactions_sums_per_transaction_floor_losses():
    # Three Rs800 transactions, each independently floored: 25+25+25 = 75.
    total = accrue_transactions(PER_UNIT_150_5, [Decimal("800"), Decimal("800"), Decimal("800")])
    assert total == Decimal("75")


def test_accrue_transactions_floor_on_aggregate_floors_once_not_per_txn():
    # Same three Rs800 transactions (Rs2,400 total), but floor_on_aggregate
    # floors the SUM once: floor(2400/150)*5 = 16*5 = 80, not 75.
    accrual = Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("5"), rounding="floor_on_aggregate")
    total = accrue_transactions(accrual, [Decimal("800"), Decimal("800"), Decimal("800")])
    assert total == Decimal("80")


def test_transaction_mode_none_rounding_is_continuous():
    accrual = Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("5"), rounding="none")
    # 800/150 * 5 = 26.666...
    result = accrue_transaction(accrual, Decimal("800"))
    assert result == Decimal("800") / Decimal("150") * Decimal("5")
    assert result > Decimal("26.66")


# ---------------------------------------------------------------------------
# Category mode -- ticket-size approximation + C.6 materiality flag
# ---------------------------------------------------------------------------

def test_syn_points_base_rule_800_ticket_produces_estimation_flag():
    # syn_points' base rule: per_unit(150, 5), floor_per_txn. At a Rs800
    # ticket (A.2's own worked example): ea = floor(800/150)*5/800 =
    # floor(5.333)*5/800 = 25/800 = 0.03125 exactly, vs the unrounded/naive
    # rate 5/150 = 0.03333... -- a ~6.67% gap, well over the 1% bar.
    binding = RuleBinding(rule_key="base", segment=_segment("dining", "4000", "800"), stacked=False)
    results = accrue_category_mode([binding], {"base": PER_UNIT_150_5})

    assert len(results) == 1
    # reported reward uses the ticket-approximated rate: 0.03125 * 4000 = 125.00
    assert results[0].reward == Decimal("125.00")
    assert results[0].flags == ("rounding_estimated",)


def test_percentage_rule_at_large_clean_ticket_no_estimation_flag():
    # 5% on a Rs1,800 ticket: floor_paisa(1800*0.05)=90.00 exactly, ea=0.05
    # exactly == the unrounded rate -- zero gap, no flag (matches the
    # golden_syn_ecom_basic.json note: "ticket-size rounding immaterial").
    binding = RuleBinding(rule_key="ecom", segment=_segment("ecommerce", "30000", "1800"), stacked=False)
    results = accrue_category_mode([binding], {"ecom": PCT_5})

    assert results[0].reward == Decimal("1500.00")
    assert results[0].flags == ()


def test_materiality_check_aggregates_across_all_of_a_rules_segments():
    # Same rule bound to two segments with different ticket sizes in the
    # SAME month -- the flag decision is per-rule (summed), not per-segment.
    # Rs150 ticket -> ea = floor(150/150)*5/150 = 5/150 = 0.03333... (no
    # floor loss at all, ticket is an exact multiple of the unit).
    # Rs800 ticket -> ea = 0.03125 (the lossy one, computed above).
    clean_binding = RuleBinding(rule_key="base", segment=_segment("fuel", "1500", "150"), stacked=False)
    lossy_binding = RuleBinding(rule_key="base", segment=_segment("dining", "4000", "800"), stacked=False)
    results = accrue_category_mode([clean_binding, lossy_binding], {"base": PER_UNIT_150_5})

    # aggregate gap is still dominated by the lossy segment -> still flagged,
    # and the SAME flag applies to both of the rule's results.
    assert all(r.flags == ("rounding_estimated",) for r in results)


def test_category_mode_floor_on_aggregate_needs_no_ticket_size_and_never_flags():
    accrual = Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("5"), rounding="floor_on_aggregate")
    # floor(2400/150)*5 = 80 exactly, regardless of ticket_size (unused).
    binding = RuleBinding(rule_key="base", segment=_segment("grocery", "2400", "1"), stacked=False)
    results = accrue_category_mode([binding], {"base": accrual})
    assert results[0].reward == Decimal("80")
    assert results[0].flags == ()


def test_category_mode_none_rounding_never_flags():
    accrual = Accrual(type="per_unit", unit_amount=Decimal("150"), points_per_unit=Decimal("5"), rounding="none")
    binding = RuleBinding(rule_key="base", segment=_segment("grocery", "4000", "800"), stacked=False)
    results = accrue_category_mode([binding], {"base": accrual})
    assert results[0].flags == ()


def test_unknown_rounding_mode_raises():
    bad = Accrual(type="percentage", rate=Decimal("0.01"), rounding="bogus")
    with pytest.raises(ValueError, match="unknown rounding mode"):
        accrue_transaction(bad, Decimal("100"))


def test_unknown_accrual_type_raises():
    bad = Accrual(type="cashback_special", rate=Decimal("0.01"))
    with pytest.raises(ValueError, match="unknown accrual type"):
        accrue_transaction(bad, Decimal("100"))
