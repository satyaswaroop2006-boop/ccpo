"""Golden-style hand-computed scenarios for optimiser/scenarios.py (Phase 4
scenarios slice). Real-engine scenarios reuse test_enumerate.py's and
test_frontier.py's own catalog/spend combinations so the Expected-spend
numbers are already independently verified elsewhere; only the Low/High
(0.8x/1.2x) numbers are freshly hand-computed here. Rank-stability's
"falls out of the top-3" branch needs more than 3 candidate subsets to be
meaningful, so that one test constructs `SubsetResult`s directly (same
"bypass the upstream stage, test this module's own logic" pattern used in
test_frontier.py/test_classify.py, docs/DECISIONS.md #5).
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.normalise import CategorySpend, SpendInput, UpiAggregateSpend
from optimiser.enumerate import SubsetResult, enumerate_subsets
from optimiser.frontier import build_frontier
from optimiser.scenarios import (
    DEFAULT_HIGH_FACTOR,
    DEFAULT_LOW_FACTOR,
    low_spend_pv_by_subset_key,
    robustness_for,
    run_scenarios,
    scale_spend,
)

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()


def _bundles():
    return [REPO.get_card_bundle("syn_ecom"), REPO.get_card_bundle("syn_flat")]


def test_scale_spend_scales_category_lines_and_leaves_other_fields_alone():
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    ))
    scaled = scale_spend(spend, Decimal("0.8"))
    assert scaled.category_spend[0].annual_amount == Decimal("480000.0")
    assert scaled.category_spend[0].category == "ecommerce"
    assert scaled.category_spend[0].channel == "online"
    assert scaled.upi_aggregate is None


def test_scale_spend_also_scales_upi_aggregate():
    spend = SpendInput(upi_aggregate=UpiAggregateSpend(monthly_amount=Decimal("10000")))
    scaled = scale_spend(spend, Decimal("1.2"))
    assert scaled.upi_aggregate.monthly_amount == Decimal("12000.0")


def test_low_high_sweeps_match_hand_computation():
    # Same shape as test_enumerate.py's own scenario, at 3 spend levels.
    # syn_ecom alone: Rs20,000/mo @5% (Rs1,000) + overflow @1% (no better
    # card in a size-1 subset), waiver always cleared (every level clears
    # Rs1,00,000 comfortably). syn_flat alone: flat 1.5%. Both: the same
    # Rs20,000/mo @5% (Rs1,000, waiver-eligible spend Rs2,40,000/yr always
    # clears the threshold) + all overflow to syn_flat's better 1.5%.
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    ))
    sweep = run_scenarios(_bundles(), CURRENCIES, spend, cardinality_mode="up_to", max_cards=2)
    assert sweep.low_factor == DEFAULT_LOW_FACTOR
    assert sweep.high_factor == DEFAULT_HIGH_FACTOR

    low_by_key = {r.subset_key: r.pv_exact for r in sweep.low}
    expected_by_key = {r.subset_key: r.pv_exact for r in sweep.expected}
    high_by_key = {r.subset_key: r.pv_exact for r in sweep.high}

    # Low = Rs4,80,000/yr -> Rs40,000/mo: Rs20,000@5%=1,000 + Rs20,000@1%=200 = 1,200/mo*12
    assert low_by_key["syn_ecom"] == Decimal("14400.00")
    assert low_by_key["syn_flat"] == Decimal("7200.00")  # 4,80,000 * 1.5%
    assert low_by_key["syn_ecom+syn_flat"] == Decimal("15600.00")  # 12,000 + 20,000*1.5%*12=3,600

    # Expected = Rs6,00,000/yr -- matches test_enumerate.py's own numbers exactly.
    assert expected_by_key["syn_ecom"] == Decimal("15600.00")
    assert expected_by_key["syn_flat"] == Decimal("9000.00")
    assert expected_by_key["syn_ecom+syn_flat"] == Decimal("17400.00")

    # High = Rs7,20,000/yr -> Rs60,000/mo: Rs20,000@5%=1,000 + Rs40,000@1%=400 = 1,400/mo*12
    assert high_by_key["syn_ecom"] == Decimal("16800.00")
    assert high_by_key["syn_flat"] == Decimal("10800.00")  # 7,20,000 * 1.5%
    assert high_by_key["syn_ecom+syn_flat"] == Decimal("19200.00")  # 12,000 + 40,000*1.5%*12=7,200


def test_expected_results_reused_instead_of_resolved():
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    ))
    precomputed = enumerate_subsets(_bundles(), CURRENCIES, spend, cardinality_mode="up_to", max_cards=2)
    sweep = run_scenarios(_bundles(), CURRENCIES, spend, cardinality_mode="up_to", max_cards=2, expected_results=precomputed)
    assert sweep.expected is precomputed  # passed straight through, not re-solved


def test_robustness_ratio_and_low_spend_map_feed_frontier_t3():
    # Rs12,00,000/yr scenario -- same numbers test_frontier.py's own T1-pass
    # test uses (syn_ecom 21,600 / syn_flat 18,000 / both 26,400 at
    # Expected). Low (Rs9,60,000/yr, Rs80,000/mo): Rs20,000@5%=1,000 +
    # Rs60,000@1%=600 = 1,600/mo*12=19,200 (syn_ecom alone); both:
    # 12,000 + 60,000*1.5%*12=10,800 = 22,800.
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("1200000")),
    ))
    sweep = run_scenarios(_bundles(), CURRENCIES, spend, cardinality_mode="up_to", max_cards=2)

    robustness = robustness_for("syn_ecom+syn_flat", sweep)
    assert robustness.v_expected == Decimal("26400.00")
    assert robustness.v_low == Decimal("22800.00")
    assert robustness.robustness == Decimal("22800.00") / Decimal("26400.00")
    assert robustness.rank_expected == 1  # best subset in every scenario (only 3 subsets total)
    assert robustness.rank_low == 1
    assert robustness.rank_stable is True

    # Wire straight into frontier's T3 -- both scenarios' numbers pass: T1
    # (DeltaV=4,800 >= 2,000), T2 (DeltaFee=0, trivial), T3 (low DeltaV =
    # 22,800-19,200=3,600 >= 0).
    frontier = build_frontier(sweep.expected, _bundles(), low_spend_pv_by_subset_key=low_spend_pv_by_subset_key(sweep))
    step = frontier.steps[0]
    assert step.low_spend_delta_v == Decimal("3600.00")
    assert step.t3_pass is True
    assert step.passes is True
    assert frontier.recommended_size == 2


def test_robustness_is_none_when_expected_value_is_not_positive():
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    ))
    sweep = run_scenarios(_bundles(), CURRENCIES, spend, cardinality_mode="up_to", max_cards=2)
    fake_expected = tuple(
        r if r.subset_key != "syn_flat" else SubsetResult(
            subset_key="syn_flat", card_keys=("syn_flat",), size=1,
            pv_planned=Decimal("0"), pv_exact=Decimal("0"), repair_applied=False, gap=Decimal("0"),
            allocation=r.allocation, card_results=r.card_results,
        )
        for r in sweep.expected
    )
    zeroed_sweep = run_scenarios(
        _bundles(), CURRENCIES, spend, cardinality_mode="up_to", max_cards=2, expected_results=fake_expected,
    )
    robustness = robustness_for("syn_flat", zeroed_sweep)
    assert robustness.v_expected == Decimal("0")
    assert robustness.robustness is None


def _fabricated(card_keys, pv_exact):
    return SubsetResult(
        subset_key="+".join(sorted(card_keys)), card_keys=tuple(card_keys), size=len(card_keys),
        pv_planned=pv_exact, pv_exact=pv_exact, repair_applied=False, gap=Decimal("0"),
        allocation=None, card_results={},
    )


def test_rank_stability_fails_when_a_portfolio_drops_out_of_the_top_n():
    # 5 subsets, only 3 fit in the top-3. "target" ranks 3rd in Expected/
    # High but drops to 4th in Low -- the exact case SS E.11's rank-
    # stability check exists to catch (a portfolio that looks fine at
    # Expected spend but isn't dependable if spending contracts).
    from optimiser.scenarios import ScenarioSweep

    expected = (
        _fabricated(("a",), Decimal("100")), _fabricated(("b",), Decimal("90")),
        _fabricated(("target",), Decimal("80")), _fabricated(("c",), Decimal("70")), _fabricated(("d",), Decimal("60")),
    )
    high = (
        _fabricated(("a",), Decimal("100")), _fabricated(("b",), Decimal("90")),
        _fabricated(("target",), Decimal("80")), _fabricated(("c",), Decimal("70")), _fabricated(("d",), Decimal("60")),
    )
    low = (
        _fabricated(("a",), Decimal("100")), _fabricated(("b",), Decimal("90")),
        _fabricated(("c",), Decimal("85")), _fabricated(("target",), Decimal("50")), _fabricated(("d",), Decimal("40")),
    )
    sweep = ScenarioSweep(low=low, expected=expected, high=high, low_factor=Decimal("0.8"), high_factor=Decimal("1.2"))

    robustness = robustness_for("target", sweep)
    assert robustness.rank_expected == 3
    assert robustness.rank_high == 3
    assert robustness.rank_low == 4
    assert robustness.rank_stable is False

    stable = robustness_for("a", sweep)
    assert stable.rank_stable is True
