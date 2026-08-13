"""Golden-style hand-computed scenarios for optimiser/explain.py (Phase 4
explain slice). `threshold_funding_report` reuses test_repair.py's own
three already-hand-verified `AllocationResult` fixtures directly (never
re-derived). `scan_driver`/`find_smallest_flip` scenarios are built around
syn_ecom vs syn_flat's own well-understood rate structure (5% capped at
Rs20,000/mo, 1% overflow, vs syn_flat's flat 1.5%) with spend amounts
chosen so every monthly split is paisa-clean and the algebra is exact
(no floating tolerance anywhere -- every assertion is an exact Decimal
equality against a hand-derived crossover).
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.evaluate import evaluate_card
from engine.normalise import CategorySpend, SpendInput
from optimiser.allocate import OUTSIDE_OPTION_KEY, AllocationResult, SpendAllocation, SpendKey, allocate
from optimiser.explain import (
    build_card_ledger,
    find_smallest_flip,
    marginal_value_curve,
    scan_driver,
    threshold_funding_report,
)

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()

GROCERY = SpendKey(category="grocery", channel=None, geography="domestic", merchant_group=None)
DINING = SpendKey(category="dining", channel=None, geography="domestic", merchant_group=None)
ECOM_ONLINE = SpendKey(category="ecommerce", channel="online", geography="domestic", merchant_group=None)
UTILITIES = SpendKey(category="utilities", channel=None, geography="domestic", merchant_group=None)


def _bundle(card_key: str):
    return REPO.get_card_bundle(card_key)


# ---------------------------------------------------------------------------
# 1. build_card_ledger
# ---------------------------------------------------------------------------

def test_card_ledger_groups_the_trace_into_ss37_buckets():
    # syn_ecom, Rs96,000/yr grocery (base 1%, zero floor loss): reward =
    # 960.00, no milestone, no benefit, fee = 500*1.18 = 590.00 (waiver
    # missed, 96,000 < Rs1,00,000) -- same hand computation as
    # test_repair.py's own near-miss scenario baseline.
    bundle = _bundle("syn_ecom")
    spend = SpendInput(category_spend=(CategorySpend(category="grocery", annual_amount=Decimal("96000")),))
    result = evaluate_card(bundle, CURRENCIES, spend)
    assert result.nacv.steady_state == Decimal("370.00")

    ledger = build_card_ledger("syn_ecom", result)
    by_label = {b.label: b.total for b in ledger.buckets}
    assert by_label["reward"] == Decimal("960.00")
    assert by_label["milestones"] == Decimal("0")
    assert by_label["benefits"] == Decimal("0")
    assert by_label["costs"] == Decimal("-590.00")
    assert ledger.total == Decimal("370.00") == result.nacv.steady_state


# ---------------------------------------------------------------------------
# 2. threshold_funding_report -- reuses test_repair.py's own fixtures
# ---------------------------------------------------------------------------

def _flat_annual_allocation(card_key: str, key: SpendKey, monthly_amount: Decimal) -> list[SpendAllocation]:
    return [SpendAllocation(card_key=card_key, key=key, month=m, amount=monthly_amount) for m in range(1, 13)]


def test_threshold_funding_report_flags_a_near_miss():
    # test_repair.py's test_near_miss_waiver_crossing_pays_off baseline:
    # Rs96,000/yr pooled on syn_ecom -- Rs4,000 short of the Rs1,00,000
    # waiver threshold, within the Rs5,000 buffer.
    bundle = _bundle("syn_ecom")
    allocation = AllocationResult(
        status="Optimal", pv_planned=Decimal("960.00"), reward_value=Decimal("960.00"),
        surcharge_cost=Decimal("0"), forex_cost=Decimal("0"),
        allocations=tuple(_flat_annual_allocation("syn_ecom", GROCERY, Decimal("8000.00"))),
    )
    statuses = threshold_funding_report(bundle, allocation)
    assert len(statuses) == 1
    status = statuses[0]
    assert status.threshold_key == "waiver"
    assert status.threshold_spend == Decimal("100000")
    assert status.pooled_spend == Decimal("96000.00")
    assert status.gap == Decimal("4000.00")
    assert status.funded is False
    assert status.near_miss is True


def test_threshold_funding_report_flags_a_genuine_shortfall_not_a_near_miss():
    # test_repair.py's test_no_repair_when_gap_exceeds_the_buffer:
    # Rs72,000/yr pooled -- Rs28,000 short, well outside the Rs5,000 buffer.
    bundle = _bundle("syn_ecom")
    allocation = AllocationResult(
        status="Optimal", pv_planned=Decimal("720.00"), reward_value=Decimal("720.00"),
        surcharge_cost=Decimal("0"), forex_cost=Decimal("0"),
        allocations=tuple(_flat_annual_allocation("syn_ecom", GROCERY, Decimal("6000.00"))),
    )
    status = threshold_funding_report(bundle, allocation)[0]
    assert status.gap == Decimal("28000.00")
    assert status.funded is False
    assert status.near_miss is False


def test_threshold_funding_report_flags_comfortably_funded():
    # test_repair.py's test_gap_reporting_round_trips_through_a_real_
    # allocate_solve: Rs4,80,000 total (ecommerce 3,60,000 + grocery
    # 1,20,000), all routed to syn_ecom (single-card subset) -- Rs3,80,000
    # of headroom past the Rs1,00,000 waiver threshold.
    bundle = _bundle("syn_ecom")
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("360000")),
        CategorySpend(category="grocery", annual_amount=Decimal("120000")),
    ))
    allocation = allocate([bundle], CURRENCIES, spend)
    status = threshold_funding_report(bundle, allocation)[0]
    assert status.pooled_spend == Decimal("480000.00")
    assert status.gap == Decimal("-380000.00")
    assert status.funded is True
    assert status.near_miss is False


# ---------------------------------------------------------------------------
# 3. scan_driver / find_smallest_flip
# ---------------------------------------------------------------------------

def _ecommerce_spend(annual_amount) -> SpendInput:
    return SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal(annual_amount)),
    ))


def test_scan_driver_crossover_lands_exactly_on_a_grid_point():
    # syn_ecom (single-card): 12,000 (capped 5% segment, Rs20,000/mo * 12)
    # + 1%*(S-2,40,000) overflow, waiver always cleared (S >> Rs1,00,000
    # throughout this grid). syn_flat: flat 1.5%*S. Crossover solves
    # 12,000 + 0.01*(S-240,000) = 0.015*S -> S = Rs19,20,000 exactly, which
    # this grid includes directly (step Rs2,40,000, a clean multiple of
    # the Rs20,000/mo cap width).
    grid = [Decimal(v) for v in range(1_200_000, 2_400_001, 240_000)]
    scan = scan_driver([_bundle("syn_ecom")], [_bundle("syn_flat")], CURRENCIES, _ecommerce_spend(1_200_000), ECOM_ONLINE, grid)

    by_value = {p.driver_value: (p.pv_a, p.pv_b) for p in scan.points}
    assert by_value[Decimal("1200000")] == (Decimal("21600.00"), Decimal("18000.00"))
    assert by_value[Decimal("1920000")] == (Decimal("28800.00"), Decimal("28800.00"))
    assert by_value[Decimal("2400000")] == (Decimal("33600.00"), Decimal("36000.00"))

    assert scan.crossover == Decimal("1920000")
    assert scan.winner_at_low == "syn_ecom"
    assert scan.winner_at_high == "syn_flat"


def test_scan_driver_interpolates_when_the_crossover_falls_between_grid_points():
    # Same underlying (exactly linear) functions, but the grid skips
    # Rs19,20,000 itself -- interpolation between the two bracketing
    # points must land on the exact same true crossover.
    grid = [Decimal("1680000"), Decimal("2160000")]
    scan = scan_driver([_bundle("syn_ecom")], [_bundle("syn_flat")], CURRENCIES, _ecommerce_spend(1_680_000), ECOM_ONLINE, grid)
    assert scan.crossover == Decimal("1920000.00")


def test_scan_driver_reports_no_crossover_when_one_side_always_wins():
    # Entirely within syn_ecom's uncapped 5% zone (S/12 <= Rs20,000 for
    # every grid point) -- 5% > 1.5% always, no sign change possible.
    grid = [Decimal(v) for v in range(120_000, 240_001, 24_000)]
    scan = scan_driver([_bundle("syn_ecom")], [_bundle("syn_flat")], CURRENCIES, _ecommerce_spend(120_000), ECOM_ONLINE, grid)
    assert scan.crossover is None
    assert scan.winner_at_low == scan.winner_at_high == "syn_ecom"


def test_find_smallest_flip_sorts_finite_change_before_none():
    # Both driver lines start at a zero placeholder baseline so neither
    # interferes with the other's scan (a zero-amount line contributes
    # nothing to either card's reward or to syn_ecom's waiver-eligible
    # total). Ecommerce flips at Rs19,20,000 (same algebra as above);
    # utilities never flips -- syn_flat's flat 1.5% beats syn_ecom's base
    # 1% at every utilities level tried, even after accounting for
    # syn_ecom's Rs590 unwaived fee (utilities alone never reaches the
    # Rs1,00,000 waiver threshold in this grid).
    baseline = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("0")),
        CategorySpend(category="utilities", annual_amount=Decimal("0")),
    ))
    driver_grids = [
        (ECOM_ONLINE, [Decimal("1680000"), Decimal("2160000")]),
        (UTILITIES, [Decimal("40000"), Decimal("60000"), Decimal("80000")]),
    ]
    results = find_smallest_flip([_bundle("syn_ecom")], [_bundle("syn_flat")], CURRENCIES, baseline, driver_grids)

    assert len(results) == 2
    assert results[0].driver == ECOM_ONLINE
    assert results[0].baseline_value == Decimal("0")
    assert results[0].crossover == Decimal("1920000.00")
    assert results[0].change_needed == Decimal("1920000.00")

    assert results[1].driver == UTILITIES
    assert results[1].crossover is None
    assert results[1].change_needed is None


# ---------------------------------------------------------------------------
# 4. marginal_value_curve
# ---------------------------------------------------------------------------

def test_marginal_value_curve_hand_computed_points_and_kinks():
    # syn_ecom's ecommerce rule (5%, no base-rate stacking) alone this
    # time -- single category, no waiver-clearing crosstalk to track
    # beyond the ecommerce total itself. Rs96,000 (< Rs1,00,000 waiver,
    # fee charged): 96,000*0.05 - 590.00 = 4,800.00-590.00 = 4,210.00.
    # Rs1,20,000 (waiver just cleared): 1,20,000*0.05 = 6,000.00.
    # Rs2,16,000 (still under the Rs20,000/mo=Rs2,40,000/yr cap
    # equivalent): 2,16,000*0.05 = 10,800.00. Rs2,40,000 (exactly at the
    # cap boundary, still fully in the 5% segment): 12,000.00. Rs2,64,000
    # (Rs2,000/mo over the cap): 20,000*0.05+2,000*0.01=1,020/mo*12=12,240.00.
    bundle = _bundle("syn_ecom")
    grid = [Decimal(v) for v in (96_000, 120_000, 216_000, 240_000, 264_000)]
    curve = marginal_value_curve(bundle, CURRENCIES, _ecommerce_spend(96_000), ECOM_ONLINE, grid)

    by_value = {p.driver_value: p.nacv_steady_state for p in curve.points}
    assert by_value[Decimal("96000")] == Decimal("4210.00")
    assert by_value[Decimal("120000")] == Decimal("6000.00")
    assert by_value[Decimal("216000")] == Decimal("10800.00")
    assert by_value[Decimal("240000")] == Decimal("12000.00")
    assert by_value[Decimal("264000")] == Decimal("12240.00")

    # kinks: the Rs1,00,000 waiver threshold (window=anniversary_year, N=1
    # -> annualised unchanged) and the cap's Rs20,000/mo width annualised
    # via its window's 12 instances/year -> Rs2,40,000. Both fall inside
    # [96,000, 2,64,000].
    assert curve.kinks == (Decimal("100000"), Decimal("240000"))


def test_marginal_value_curve_skips_kinks_for_a_custom_seasonality_line():
    bundle = _bundle("syn_ecom")
    custom = SpendInput(category_spend=(
        CategorySpend(
            category="ecommerce", channel="online", annual_amount=Decimal("96000"),
            seasonality=tuple(Decimal("1") / 12 for _ in range(12)),
        ),
    ))
    grid = [Decimal(v) for v in (96_000, 120_000, 264_000)]
    curve = marginal_value_curve(bundle, CURRENCIES, custom, ECOM_ONLINE, grid)
    assert curve.kinks == ()
    assert len(curve.points) == 3  # the curve itself is still computed
