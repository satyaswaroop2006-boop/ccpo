"""Golden-style hand-computed scenarios for optimiser/allocate.py's inner
MILP (Phase 4, first slice -- docs/DECISIONS.md). Each scenario's expected
values are hand-computed here, never re-derived from the code under test,
same discipline as compute/goldens/.
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.normalise import CategorySpend, SpendInput
from optimiser.allocate import OUTSIDE_OPTION_KEY, allocate

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()


def _bundle(card_key: str):
    return REPO.get_card_bundle(card_key)


def _totals_by_card(result) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for a in result.allocations:
        totals[a.card_key] = totals.get(a.card_key, Decimal("0")) + a.amount
    return totals


def test_single_card_matches_golden_syn_ecom_basic_exactly():
    # Same spend profile as golden_syn_ecom_basic.json (ecommerce/online
    # 3,60,000 + grocery 1,20,000/yr). Both tickets (1,800 / 700) are exact
    # multiples of the underlying per-txn maths at these rates -- ê equals
    # the evaluator's exact rate here, so the planning value should equal
    # the golden's evaluator-verified gross_reward_value byte-for-byte:
    # 20,000/mo @5% capped (Rs1,000) + 10,000/mo @1% overflow (Rs100) =
    # Rs1,100/mo on ecommerce, + 10,000/mo @1% (Rs100) on grocery =
    # Rs1,200/mo total * 12 = Rs14,400.00/yr. Only one card in the subset,
    # so there's no allocation DECISION being made -- this scenario proves
    # segment/rate/cap-width compilation is correct, not the solver's
    # cross-card logic (that's scenario 2).
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("360000")),
        CategorySpend(category="grocery", annual_amount=Decimal("120000")),
    ))

    result = allocate([_bundle("syn_ecom")], CURRENCIES, spend)

    assert result.status == "Optimal"
    assert result.reward_value == Decimal("14400.00")
    assert result.surcharge_cost == Decimal("0")
    assert result.forex_cost == Decimal("0")
    assert result.pv_planned == Decimal("14400.00")


def test_two_cards_split_across_the_better_rate_after_the_cap():
    # syn_ecom (1% base / 5% ecommerce capped at Rs1,000/mo reward,
    # overflow=base_rate i.e. re-rates to syn_ecom's OWN 1% base) vs
    # syn_flat (flat 1.5%, no cap). Rs50,000/mo ecommerce/online spend,
    # eligible on both cards. B.5: a maximising LP fills the higher-rate
    # segment first automatically -- Rs20,000/mo fills syn_ecom's capped 5%
    # segment (Rs1,000/mo = Rs12,000/yr). The remaining Rs30,000/mo is a
    # genuine cross-card choice: syn_ecom's own overflow only pays 1%
    # (Rs300/mo), but syn_flat pays 1.5% (Rs450/mo) on the SAME spend --
    # the solver must prefer routing it to syn_flat instead. Both cards'
    # ticket-size maths (1,800) is exact at 1%/1.5%/5%, so the hand
    # computation is exact: Rs12,000 + Rs5,400 = Rs17,400.00/yr.
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    ))

    result = allocate([_bundle("syn_ecom"), _bundle("syn_flat")], CURRENCIES, spend)

    assert result.status == "Optimal"
    assert result.reward_value == Decimal("17400.00")
    assert result.pv_planned == Decimal("17400.00")

    totals = _totals_by_card(result)
    assert totals["syn_ecom"] == Decimal("240000.00")  # Rs20,000/mo * 12, exactly the cap-equivalent width
    assert totals["syn_flat"] == Decimal("360000.00")  # the remainder, at the better rate
    assert OUTSIDE_OPTION_KEY not in totals  # every rupee has a positive-margin home


def test_outside_option_routes_away_negative_margin_overflow():
    # syn_fuel: base 0.5% + fuel_refund 1% (stacked) = 1.5% combined,
    # capped at Rs250/mo reward on fuel_refund alone (overflow=zero, so
    # once the cap binds, only base's 0.5% keeps earning). Surcharge 1% *
    # 1.18 GST = 1.18%. Cap-equivalent spend = Rs250 / 1% = Rs25,000/mo:
    # below it, net margin = 1.5% - 1.18% = +0.32%/rupee (worth routing to
    # syn_fuel); above it, net margin = 0.5% - 1.18% = -0.68%/rupee (worth
    # LESS than doing nothing). With Rs50,000/mo fuel spend (double the
    # cap-equivalent), A.11's claim -- "the optimiser routes surcharge-
    # negative spend away from cards automatically" via the always-present
    # outside option c0 -- predicts exactly Rs25,000/mo to syn_fuel and
    # Rs25,000/mo to c0, not all Rs50,000/mo forced onto a card that would
    # make the second half a loss. Ticket size 1,500 is exact at 0.5%/1%,
    # so the hand computation is exact: reward = (125+250)/mo*12 =
    # Rs4,500.00/yr; surcharge = 1.18% * Rs25,000/mo (only the spend
    # actually routed to syn_fuel is surcharged) *12 = Rs3,540.00/yr;
    # pv_planned = Rs960.00/yr.
    spend = SpendInput(category_spend=(
        CategorySpend(category="fuel", annual_amount=Decimal("600000")),
    ))

    result = allocate([_bundle("syn_fuel")], CURRENCIES, spend)

    assert result.status == "Optimal"
    assert result.reward_value == Decimal("4500.00")
    assert result.surcharge_cost == Decimal("3540.00")
    assert result.pv_planned == Decimal("960.00")

    totals = _totals_by_card(result)
    assert totals["syn_fuel"] == Decimal("300000.00")  # Rs25,000/mo * 12, exactly the cap-equivalent width
    assert totals[OUTSIDE_OPTION_KEY] == Decimal("300000.00")  # the negative-margin remainder, routed away


def test_solver_fallback_to_cbc_produces_the_same_result():
    # Forces the CBC path explicitly (docs/DECISIONS.md's Phase 4 entry --
    # the fallback branch isn't dead code) on the same profile as the
    # single-card scenario above; same golden-verified answer either way.
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("360000")),
        CategorySpend(category="grocery", annual_amount=Decimal("120000")),
    ))

    result = allocate([_bundle("syn_ecom")], CURRENCIES, spend, solver="cbc")

    assert result.status == "Optimal"
    assert result.reward_value == Decimal("14400.00")


def test_incremental_tier_card_raises_rather_than_mismodelling():
    # syn_slab's rules are tier_mode="incremental" (Part B SS B.5's convex
    # PWL case -- needs fill-order binaries this pass doesn't build).
    # Silently treating it as a plain concave segment chain would produce
    # a WRONG optimum (the LP would fill the high-rate band first, backwards
    # from the correct low-to-high incremental fill order) -- raising is
    # the correct behaviour, matching engine/caps.py's own posture on
    # unsupported constructs.
    spend = SpendInput(category_spend=(CategorySpend(category="grocery", annual_amount=Decimal("100000")),))

    try:
        allocate([_bundle("syn_slab")], CURRENCIES, spend)
        assert False, "expected ValueError for an incremental-tier card"
    except ValueError as e:
        assert "incremental-tier" in str(e)
