"""Golden-style hand-computed scenarios for optimiser/frontier.py (Phase 4
frontier/classify slice). Two kinds of test:

- End-to-end through the real engine (syn_ecom + syn_flat, Rs12,00,000/yr
  ecommerce, chosen so month amounts are whole rupees) for the frontier
  points and T1/T2's "trivial" (fee doesn't increase) branch.
- Direct `SubsetResult` construction (same pattern Stage 3's own tests use
  to bypass `normalise()`, docs/DECISIONS.md #5) for T2's actual fee-cover
  ratio arithmetic and T3's optional scenario-floor branch -- these test
  frontier.py's own checklist logic, not a second copy of the engine.
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.assemble import NACVResult
from engine.evaluate import EvaluateResult
from engine.normalise import CategorySpend, SpendInput
from optimiser.allocate import AllocationResult
from optimiser.enumerate import SubsetResult, enumerate_subsets
from optimiser.frontier import build_frontier, format_step

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()

SPEND = SpendInput(category_spend=(
    CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("1200000")),
))


def _bundles():
    return [REPO.get_card_bundle("syn_ecom"), REPO.get_card_bundle("syn_flat")]


def _real_results(n_tol=None, **kwargs):
    results = enumerate_subsets(_bundles(), CURRENCIES, SPEND, cardinality_mode="up_to", max_cards=2)
    return build_frontier(results, _bundles(), n_tol=n_tol, **kwargs), results


def test_frontier_points_pick_max_pv_per_size():
    # syn_ecom alone: Rs20,000/mo @5% (Rs1,000) + Rs80,000/mo @1% overflow
    # (Rs800) = Rs1,800/mo * 12 = Rs21,600/yr; Rs12,00,000 total clears the
    # Rs1,00,000 waiver threshold outright, fee waived. syn_flat alone:
    # flat 1.5% * Rs12,00,000 = Rs18,000/yr. syn_ecom wins size 1.
    frontier, _results = _real_results()
    by_size = {p.size: p for p in frontier.points}
    assert by_size[1].pv_exact == Decimal("21600.00")
    assert by_size[1].subset_key == "syn_ecom"
    # both cards: same Rs20,000/mo @5% (Rs1,000) on syn_ecom, remaining
    # Rs80,000/mo now diverts to syn_flat's better 1.5% (Rs1,200) instead
    # of syn_ecom's 1% overflow = Rs2,200/mo * 12 = Rs26,400/yr.
    assert by_size[2].pv_exact == Decimal("26400.00")
    assert by_size[2].subset_key == "syn_ecom+syn_flat"


def test_step_passes_materiality_and_trivial_fee_gate_extends_recommendation():
    # DeltaV = 26,400 - 21,600 = 4,800 >= max(2000, 3%*21600=648) -> T1 pass.
    # DeltaFee = gross(syn_ecom)+gross(syn_flat) - gross(syn_ecom) = 0 (syn_flat
    # has annual_fee=0) -> T2 trivially passes (DeltaF <= 0).
    frontier, _results = _real_results()
    assert len(frontier.steps) == 1
    step = frontier.steps[0]
    assert step.delta_v == Decimal("4800.00")
    assert step.t1_pass is True
    assert step.delta_fee == Decimal("0.00")
    assert step.fee_cover_ratio is None
    assert step.t2_pass is True
    assert step.passes is True
    assert frontier.recommended_size == 2
    assert frontier.capped_by_tolerance is False


def test_n_tol_caps_recommendation_without_dropping_the_step_record():
    frontier, _results = _real_results(n_tol=1)
    assert frontier.recommended_size == 1
    assert frontier.capped_by_tolerance is True
    # the step is still recorded and still shows as passing -- "say so",
    # per SS E.9, not silently dropped.
    assert frontier.steps[0].passes is True


def test_materiality_failure_blocks_recommendation():
    # Same two cards, smaller spend (Rs6,00,000/yr, test_enumerate.py's own
    # scenario): DeltaV = 17,400 - 15,600 = 1,800 < max(2000, 3%*15600=468)
    # -> T1 fails, recommendation stays at the 1-card portfolio.
    small_spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    ))
    results = enumerate_subsets(_bundles(), CURRENCIES, small_spend, cardinality_mode="up_to", max_cards=2)
    frontier = build_frontier(results, _bundles())
    assert frontier.steps[0].t1_pass is False
    assert frontier.steps[0].passes is False
    assert frontier.recommended_size == 1
    assert frontier.capped_by_tolerance is False


def test_frontier_stops_at_a_gap_in_enumerated_sizes():
    results = enumerate_subsets(_bundles(), CURRENCIES, SPEND, cardinality_mode="up_to", max_cards=2)
    size_1_only = tuple(r for r in results if r.size == 1)
    fabricated_size_3 = SubsetResult(
        subset_key="zzz_fake", card_keys=("syn_ecom", "syn_flat", "zzz"), size=3,
        pv_planned=Decimal("0"), pv_exact=Decimal("999999"), repair_applied=False, gap=Decimal("0"),
        allocation=size_1_only[0].allocation, card_results={},
    )
    frontier = build_frontier(size_1_only + (fabricated_size_3,), _bundles())
    assert frontier.steps == ()  # size 1 -> size 3 isn't a valid consecutive step
    assert frontier.recommended_size == 1


def _dummy_allocation() -> AllocationResult:
    return AllocationResult(
        status="Optimal", pv_planned=Decimal("0"), reward_value=Decimal("0"),
        surcharge_cost=Decimal("0"), forex_cost=Decimal("0"), allocations=(),
    )


def _eval_result(card_key: str, gross: Decimal, milestone: Decimal, benefit: Decimal) -> EvaluateResult:
    return EvaluateResult(
        card_key=card_key, gross_reward_value=gross, milestone_value=milestone, milestone_value_year1=milestone,
        benefit_value=benefit, waiver_achieved=True, fee_steady=Decimal("0"), fee_year1=Decimal("0"),
        nacv=NACVResult(steady_state=Decimal("0"), year_1=Decimal("0"), three_year=Decimal("0"), trace=()),
        benefit_valuations={}, flags=(),
    )


def _fabricated_subset(card_keys, pv_exact, card_results):
    return SubsetResult(
        subset_key="+".join(sorted(card_keys)), card_keys=tuple(card_keys), size=len(card_keys),
        pv_planned=pv_exact, pv_exact=pv_exact, repair_applied=False, gap=Decimal("0"),
        allocation=_dummy_allocation(), card_results=card_results,
    )


def test_fee_cover_ratio_passes_when_gross_benefit_clears_the_bar():
    # syn_flat (annual_fee=0) alone -> Rs10,000 gross reward, no milestone/
    # benefit. Adding syn_miles (annual_fee=10,000 -> gross fee Rs11,800 at
    # 18% GST) contributes Rs20,000 of its own gross reward+milestone
    # (5,000 + 15,000 + 0) -- DeltaGrossBenefit = 20,000, DeltaFee = 11,800,
    # ratio = 20,000/11,800 = ~1.695 >= 1.5 -> T2 passes on its own merit
    # (not the <=Rs1,000 de-minimis clause).
    size_1 = _fabricated_subset(
        ("syn_flat",), Decimal("10000"),
        {"syn_flat": _eval_result("syn_flat", Decimal("10000"), Decimal("0"), Decimal("0"))},
    )
    size_2 = _fabricated_subset(
        ("syn_flat", "syn_miles"), Decimal("16000"),
        {
            "syn_flat": _eval_result("syn_flat", Decimal("10000"), Decimal("0"), Decimal("0")),
            "syn_miles": _eval_result("syn_miles", Decimal("5000"), Decimal("15000"), Decimal("0")),
        },
    )
    frontier = build_frontier((size_1, size_2), [REPO.get_card_bundle("syn_flat"), REPO.get_card_bundle("syn_miles")])
    step = frontier.steps[0]
    assert step.delta_fee == Decimal("11800.00")
    assert step.fee_cover_ratio == (Decimal("20000") / Decimal("11800.00"))
    assert step.t2_pass is True
    assert step.passes is True
    assert frontier.recommended_size == 2


def test_fee_cover_ratio_fails_below_bar_and_above_de_minimis():
    # Same shape, but syn_miles now only contributes Rs8,000 of gross
    # benefit: ratio = 8,000/11,800 = ~0.678 < 1.5, and DeltaFee (11,800) is
    # well above the Rs1,000 de-minimis escape hatch -> T2 fails, blocking
    # the step even though T1 (materiality) still passes on its own.
    size_1 = _fabricated_subset(
        ("syn_flat",), Decimal("10000"),
        {"syn_flat": _eval_result("syn_flat", Decimal("10000"), Decimal("0"), Decimal("0"))},
    )
    size_2 = _fabricated_subset(
        ("syn_flat", "syn_miles"), Decimal("16000"),
        {
            "syn_flat": _eval_result("syn_flat", Decimal("10000"), Decimal("0"), Decimal("0")),
            "syn_miles": _eval_result("syn_miles", Decimal("3000"), Decimal("5000"), Decimal("0")),
        },
    )
    frontier = build_frontier((size_1, size_2), [REPO.get_card_bundle("syn_flat"), REPO.get_card_bundle("syn_miles")])
    step = frontier.steps[0]
    assert step.t1_pass is True  # DeltaV = 6,000 >= max(2000, 300)
    assert step.t2_pass is False
    assert step.passes is False
    assert frontier.recommended_size == 1


def test_scenario_floor_vetoes_an_otherwise_passing_step():
    frontier_pass, results = _real_results(low_spend_pv_by_subset_key={"syn_ecom": Decimal("15000"), "syn_ecom+syn_flat": Decimal("18000")})
    assert frontier_pass.steps[0].t3_pass is True
    assert frontier_pass.recommended_size == 2

    frontier_fail, _results = _real_results(low_spend_pv_by_subset_key={"syn_ecom": Decimal("15000"), "syn_ecom+syn_flat": Decimal("14000")})
    step = frontier_fail.steps[0]
    assert step.t1_pass is True and step.t2_pass is True  # both still pass on their own
    assert step.t3_pass is False
    assert step.passes is False
    assert frontier_fail.recommended_size == 1


def test_scenario_floor_stays_not_evaluated_when_subset_key_missing():
    # the map is supplied but doesn't cover this step's subset keys --
    # t3_pass stays None ("not evaluated"), distinct from "evaluated and
    # failed"; the step's pass/fail is decided by T1+T2 alone.
    frontier, _results = _real_results(low_spend_pv_by_subset_key={"some_other_key": Decimal("1")})
    assert frontier.steps[0].t3_pass is None
    assert frontier.steps[0].passes is True
    assert frontier.recommended_size == 2


def test_format_step_is_plain_ascii_and_states_the_deltas():
    frontier, _results = _real_results()
    line = format_step(frontier.steps[0])
    assert line.isascii()
    assert "2nd card" in line
    assert "+Rs4,800/yr" in line
    assert "PASS material" in line
