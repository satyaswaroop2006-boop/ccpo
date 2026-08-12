"""Golden-style hand-computed scenarios for optimiser/candidates.py
(Phase 4 slice 4). Universe = {syn_ecom, syn_flat, syn_miles}, spend =
ecommerce/online Rs6,00,000/yr + utilities Rs30,000/yr -- both categories
clear the Rs25,000 champion threshold, both zero-floor-loss for every
card's relevant rates. synth_points priced via the simplest "stmt" route
throughout (ratio 0.25, no min_points gate to worry about). Every
expected value is hand-computed here, never re-derived from the code
under test.
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.evaluate import EvaluateAssumptions
from engine.normalise import CategorySpend, SpendInput
from optimiser.candidates import select_candidates

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()
ASSUMPTIONS = EvaluateAssumptions(primary_routes={"synth_points": "stmt"})

SPEND = SpendInput(category_spend=(
    CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    CategorySpend(category="utilities", annual_amount=Decimal("30000")),
))


def _universe():
    return [REPO.get_card_bundle("syn_ecom"), REPO.get_card_bundle("syn_flat"), REPO.get_card_bundle("syn_miles")]


def test_standalone_ranking_matches_hand_computation():
    # syn_ecom: ecommerce alone (no syn_flat alternative in a single-card
    # subset) = Rs15,600.00 (slice 1's own number, Rs20,000/mo capped @5%
    # + Rs30,000/mo overflow @1%) + utilities base 1% (Rs300.00) =
    # Rs15,900.00. syn_flat: flat 1.5% on the combined Rs6,30,000 =
    # Rs9,450.00, no fee. syn_miles: reward via "stmt" (ratio 0.25) on
    # (6,00,000+30,000)/200*4 = 12,600 pts * 0.25 = Rs3,150.00; milestone
    # crosses the 4,00,000 tier only (vch_a, face value 10,000 * 1.0 util
    # * 1.0 friction = Rs10,000.00); no waiver threshold on this card, fee
    # always charged in full: (10,000 joining not relevant to steady state
    # + 10,000 annual)*1.18 = Rs11,800.00 steady fee. NACV = 3,150+10,000-
    # 11,800 = Rs1,350.00.
    result = select_candidates(_universe(), CURRENCIES, SPEND, ASSUMPTIONS, standalone_n=3)

    by_key = dict(result.standalone_ranked)
    assert by_key["syn_ecom"] == Decimal("15900.00")
    assert by_key["syn_flat"] == Decimal("9450.00")
    assert by_key["syn_miles"] == Decimal("1350.00")
    # sorted descending
    assert [k for k, _v in result.standalone_ranked] == ["syn_ecom", "syn_flat", "syn_miles"]


def test_category_champions_correctly_ranked():
    # Marginal rate over a Rs10,000 delta, isolated per category -- the
    # fixed annual fee cancels in the subtraction on every card, leaving
    # a pure reward-rate signal. syn_flat: flat 1.5% regardless of
    # category or baseline. syn_ecom: at a Rs30,000 utilities baseline,
    # 1% base (no utilities-specific acceleration on this card); at the
    # Rs6,00,000 ecommerce baseline, the marginal Rs10,000 lands entirely
    # in syn_ecom's OWN overflow zone (already Rs20,000/mo past its
    # Rs20,000/mo cap-equivalent), so it earns only the 1% base overflow
    # rate too -- not the 5% accelerated rate. syn_miles: per_unit(200,4)
    # -> ea=0.02 pts/rupee exact, delta reward = 10,000*0.02 = 200 pts,
    # valued at the "stmt" route's 0.25/pt = Rs50.00 -> rate = 50/10,000 =
    # 0.5% (milestone thresholds are nowhere near either baseline or
    # bumped amount in isolation, so MilestoneValue cancels to 0 on both
    # sides of the subtraction) -- well below both syn_flat and syn_ecom.
    result = select_candidates(_universe(), CURRENCIES, SPEND, ASSUMPTIONS, standalone_n=3)

    by_category = {}
    for champion in result.champions:
        by_category.setdefault(champion.category, []).append(champion)

    for category in ("ecommerce", "utilities"):
        ranked = by_category[category]
        assert len(ranked) == 2  # champion_top_n default
        assert [c.card_key for c in ranked] == ["syn_flat", "syn_ecom"]
        assert ranked[0].marginal_rate == Decimal("0.015")
        assert ranked[1].marginal_rate == Decimal("0.01")
        # syn_miles never appears -- 0.5% loses to both on every category
        assert "syn_miles" not in [c.card_key for c in ranked]


def test_union_rescues_a_card_a_tight_standalone_n_would_drop():
    # standalone_n=1 -> standalone-only top pick is {syn_ecom}. Union
    # with either category's champions ({syn_flat, syn_ecom}) still adds
    # syn_flat to the final candidate set even though it's outside the
    # (deliberately tiny) standalone cut -- the coverage-guarantee
    # mechanism SS B.7 exists for, doing its job with real hand-verified
    # numbers. syn_miles is rescued by neither mechanism here and is
    # correctly absent.
    result = select_candidates(_universe(), CURRENCIES, SPEND, ASSUMPTIONS, standalone_n=1)
    assert set(result.candidates) == {"syn_ecom", "syn_flat"}


def test_trim_protects_champions_cuts_standalone_tail_only():
    # standalone_n=3 -> all three cards qualify on standalone rank alone;
    # max_total=2 forces a trim. syn_miles is the only standalone-only
    # (non-champion) entry and the lowest-ranked of the three -- it's the
    # one dropped, leaving exactly the two champions/top-standalone cards.
    result = select_candidates(_universe(), CURRENCIES, SPEND, ASSUMPTIONS, standalone_n=3, max_total=2)
    assert set(result.candidates) == {"syn_ecom", "syn_flat"}
    assert "syn_miles" not in result.candidates
    # nothing protected was ever at risk -- the full standalone ranking
    # and champion list are still reported for explainability regardless
    # of the trim.
    assert len(result.standalone_ranked) == 3
    assert len(result.champions) == 4  # 2 categories * top_n=2
