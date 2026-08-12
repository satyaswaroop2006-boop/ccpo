"""Golden-style hand-computed scenarios for optimiser/repair.py (Phase 4
slice 2). Scenarios 1-3 construct an `AllocationResult` directly rather
than relying on `allocate()`'s solver output -- decouples repair.py's
correctness from allocate.py's emergent behaviour, same reasoning
docs/DECISIONS.md's Phase 4 slice 2 entry records. Every expected value is
hand-computed here, never re-derived from the code under test.
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.normalise import CategorySpend, SpendInput
from optimiser.allocate import OUTSIDE_OPTION_KEY, AllocationResult, SpendAllocation, SpendKey, allocate
from optimiser.repair import evaluate_allocation, repair

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()

GROCERY = SpendKey(category="grocery", channel=None, geography="domestic", merchant_group=None)
DINING = SpendKey(category="dining", channel=None, geography="domestic", merchant_group=None)


def _bundle(card_key: str):
    return REPO.get_card_bundle(card_key)


def _flat_annual_allocation(card_key: str, key: SpendKey, monthly_amount: Decimal) -> list[SpendAllocation]:
    return [SpendAllocation(card_key=card_key, key=key, month=m, amount=monthly_amount) for m in range(1, 13)]


def test_near_miss_waiver_crossing_pays_off():
    # syn_ecom: waiver threshold Rs1,00,000 (anniversary_year, single
    # window instance), annual_fee Rs500, no exclusions -- so waiver-
    # eligible spend = all spend. Rs96,000/yr grocery on syn_ecom (zero
    # floor loss at 1%: 700*0.01=7.00 exact) -- Rs4,000 short of the
    # threshold, well within the default buffer (max(5000, 2%*1,00,000) =
    # Rs5,000). Rs4,000/mo dining parked on c0 (Rs48,000/yr available,
    # only Rs4,000 needed, taken from month 1).
    #
    # Baseline: reward = 96,000*0.01 = Rs960.00. Waiver missed (96,000 <
    # 1,00,000) -> fee = 500*1.18 = Rs590.00. NACV = 960.00-590.00 = Rs370.00.
    # Repaired: move Rs4,000 dining from c0 to syn_ecom (dining also earns
    # base's 1%: 600*0.01=6.00 exact, zero floor loss) -> reward =
    # 96,000*0.01 + 4,000*0.01 = 960+40 = Rs1,000.00. Waiver-eligible now
    # exactly Rs1,00,000 -> waived -> fee = Rs0.00. NACV = Rs1,000.00.
    bundle = _bundle("syn_ecom")
    allocations = _flat_annual_allocation("syn_ecom", GROCERY, Decimal("8000.00"))
    allocations += _flat_annual_allocation(OUTSIDE_OPTION_KEY, DINING, Decimal("4000.00"))
    allocation = AllocationResult(
        status="Optimal", pv_planned=Decimal("960.00"), reward_value=Decimal("960.00"),
        surcharge_cost=Decimal("0"), forex_cost=Decimal("0"), allocations=tuple(allocations),
    )

    baseline = evaluate_allocation([bundle], CURRENCIES, allocation)
    assert baseline.pv_exact == Decimal("370.00")

    result = repair([bundle], CURRENCIES, allocation)
    assert result.repair_applied is True
    assert result.variants_tried == 1
    assert result.valuation.pv_exact == Decimal("1000.00")
    assert result.gap == Decimal("590.00") / Decimal("370.00")  # |960-370|/370, the ORIGINAL allocation's gap

    # the repaired allocation genuinely crosses the threshold, not just a
    # coincidentally-better number:
    ecom_result = result.valuation.card_results["syn_ecom"]
    assert ecom_result.waiver_achieved is True
    assert ecom_result.gross_reward_value == Decimal("1000.00")


def test_no_repair_when_outside_option_has_nothing_to_give():
    # Same near-miss gap as above, but no c0 spend exists anywhere --
    # there is nothing to top up FROM (this pass deliberately never pulls
    # from another real card), so no variant is even constructed.
    bundle = _bundle("syn_ecom")
    allocation = AllocationResult(
        status="Optimal", pv_planned=Decimal("960.00"), reward_value=Decimal("960.00"),
        surcharge_cost=Decimal("0"), forex_cost=Decimal("0"),
        allocations=tuple(_flat_annual_allocation("syn_ecom", GROCERY, Decimal("8000.00"))),
    )

    result = repair([bundle], CURRENCIES, allocation)
    assert result.repair_applied is False
    assert result.variants_tried == 0
    assert result.valuation.pv_exact == Decimal("370.00")
    assert result.allocation is allocation  # unchanged, same object


def test_no_repair_when_gap_exceeds_the_buffer():
    # Rs72,000/yr on syn_ecom -- Rs28,000 short of the Rs1,00,000 waiver
    # threshold, far outside the Rs5,000 buffer -- not a "near miss" at
    # all, even though c0 has ample spend available to pull from.
    bundle = _bundle("syn_ecom")
    allocations = _flat_annual_allocation("syn_ecom", GROCERY, Decimal("6000.00"))
    allocations += _flat_annual_allocation(OUTSIDE_OPTION_KEY, DINING, Decimal("10000.00"))
    allocation = AllocationResult(
        status="Optimal", pv_planned=Decimal("720.00"), reward_value=Decimal("720.00"),
        surcharge_cost=Decimal("0"), forex_cost=Decimal("0"), allocations=tuple(allocations),
    )

    result = repair([bundle], CURRENCIES, allocation)
    assert result.repair_applied is False
    assert result.variants_tried == 0


def test_gap_reporting_round_trips_through_a_real_allocate_solve():
    # Reuses Phase 4 slice 1's own scenario 1 (tests/test_allocate.py):
    # syn_ecom, the same spend as golden_syn_ecom_basic.json. Proves the
    # allocation-to-evaluator translation (_spend_input_for_card's
    # seasonality reconstruction) round-trips exactly, not just that
    # allocate()'s own internal planning-value accounting matches.
    bundle = _bundle("syn_ecom")
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("360000")),
        CategorySpend(category="grocery", annual_amount=Decimal("120000")),
    ))
    allocation = allocate([bundle], CURRENCIES, spend)

    valuation = evaluate_allocation([bundle], CURRENCIES, allocation)
    assert valuation.pv_exact == Decimal("14400.00")
    assert valuation.card_results["syn_ecom"].nacv.steady_state == Decimal("14400.00")

    # no waiver threshold gap to repair here (this scenario doesn't cross
    # or near-miss syn_ecom's own Rs1,00,000 waiver threshold -- 480,000
    # total spend clears it outright, waived already in the baseline).
    result = repair([bundle], CURRENCIES, allocation)
    assert result.repair_applied is False
    assert result.valuation.pv_exact == Decimal("14400.00")
