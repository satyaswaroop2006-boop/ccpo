"""Engine-level (non-HTTP) proof that the annual marginal-delta MVP
(docs/DECISIONS.md's Phase 3 entry) surfaces a real diminishing-returns
kink, using syn_ecom's own cap -- `cap_ecom`, Rs1,000/month reward cap on
the 5% ecommerce/online rule, `overflow: base_rate` (C.9 Example 2). This
is the same kink `breakpoints.py` compiles as a spend-domain breakpoint
(Sbar = Cap/rate = Rs20,000/month, Rs2,40,000/yr uncapped-equivalent) --
this test doesn't call breakpoints.py, it just demonstrates the delta
value genuinely drops once a Δ crosses that boundary, hand-computed
against the same mechanic `golden_syn_ecom_basic.json` already verifies
(20,000/mo @5% + overflow re-rated @1% base, not discarded).

Baseline spend (Rs2,00,000 dining) is chosen specifically to already clear
syn_ecom's Rs1,00,000 waiver threshold on its own, so the fee-waiver state
never changes between baseline and either delta scenario -- isolating the
comparison to the reward-rate kink alone, not a confounded fee crossing.
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from app.schemas import AssumptionsIn, SpendItemIn, spend_input_from_items
from engine.evaluate import evaluate_card
from tests.test_api_evaluate import _spend_items_from_golden  # reuse the "category/channel" splitter

REPO = SyntheticCatalogRepository()


def _ecom_delta_nacv(delta_annual: str) -> Decimal:
    bundle = REPO.get_card_bundle("syn_ecom")
    currencies = REPO.get_currencies()
    assumptions = AssumptionsIn().to_evaluate_assumptions()

    baseline_items = _spend_items_from_golden({"dining": 200000})
    delta_items = baseline_items + _spend_items_from_golden({"ecommerce/online": delta_annual})

    baseline_spend = spend_input_from_items([SpendItemIn(**item) for item in baseline_items])
    delta_spend = spend_input_from_items([SpendItemIn(**item) for item in delta_items])

    baseline_result = evaluate_card(bundle, currencies, baseline_spend, assumptions)
    delta_result = evaluate_card(bundle, currencies, delta_spend, assumptions)

    # baseline alone already clears the Rs1,00,000 waiver threshold, so the
    # fee is identically waived in both runs -- confirmed, not assumed.
    assert baseline_result.waiver_achieved
    assert delta_result.waiver_achieved
    assert baseline_result.fee_steady == delta_result.fee_steady == Decimal("0")

    return delta_result.nacv.steady_state - baseline_result.nacv.steady_state


def test_small_delta_stays_under_the_cap_flat_five_percent():
    delta_nacv = _ecom_delta_nacv("10000")
    assert delta_nacv == Decimal("500.00")  # 10,000 * 5%, well under the Rs20,000/mo cap-equivalent
    assert (delta_nacv / Decimal("10000")) == Decimal("0.05")


def test_large_delta_crosses_the_cap_and_the_net_rate_drops():
    delta_nacv = _ecom_delta_nacv("300000")
    # 12 months * (20,000 capped @5% = 1,000 + 5,000 overflow re-rated @1% base = 50) = 12,600.00
    assert delta_nacv == Decimal("12600.00")
    rate = delta_nacv / Decimal("300000")
    assert rate == Decimal("0.042")
    assert rate < Decimal("0.05")  # the diminishing-returns kink: below the flat-5% rate small deltas get


def test_next_best_spend_would_rank_the_small_delta_higher():
    small_rate = _ecom_delta_nacv("10000") / Decimal("10000")
    large_rate = _ecom_delta_nacv("300000") / Decimal("300000")
    assert small_rate > large_rate
