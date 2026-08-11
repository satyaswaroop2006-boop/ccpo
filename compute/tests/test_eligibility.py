"""Unit tests for Stage 2 (engine/eligibility.py), Part C SS C.2.5 / SS C.4 Stage 2.

Fixtures for syn_waiver and syn_upi are hand-transcribed from the actual
seed definitions in seeds/synthetic_cards.py (C.9 Examples 5 and 9), not
invented, so these tests exercise the real synthetic-catalog exclusions.

Expected values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.eligibility import Exclusion, ExclusionSelector, apply_eligibility
from engine.normalise import NormalisedSpend, SpendSegment


def _segment(category, channel, month, amount, ticket_size=Decimal("1000")):
    return SpendSegment(category=category, channel=channel, month=month, amount=Decimal(amount), ticket_size=ticket_size)


def _flat_year(category, channel, monthly_amount, ticket_size=Decimal("1000")):
    return [_segment(category, channel, m, monthly_amount, ticket_size) for m in range(1, 13)]


def _categories_present(segments):
    return {(s.category, s.channel) for s in segments}


def _total(segments):
    return sum((s.amount for s in segments), Decimal("0"))


# ---------------------------------------------------------------------------
# syn_waiver (C.9 Example 5) -- rent excluded from fee_waiver only,
# fuel excluded from rewards only
# ---------------------------------------------------------------------------

SYN_WAIVER_EXCLUSIONS = (
    Exclusion(
        key="rent_no_waiver",
        selector=ExclusionSelector(categories=("rent",)),
        excluded_from=("fee_waiver",),
        note="Rent earns rewards here but does NOT count toward the waiver",
    ),
    Exclusion(
        key="fuel_no_rewards",
        selector=ExclusionSelector(categories=("fuel",)),
        excluded_from=("rewards",),
        note="Fuel earns nothing but DOES count toward the waiver",
    ),
)


def _syn_waiver_spend():
    # Rs 3,000/mo grocery (untouched by any exclusion), Rs 5,000/mo rent,
    # Rs 2,000/mo fuel -- all general spend (channel=None).
    segments = (
        _flat_year("grocery", None, "3000", Decimal("700"))
        + _flat_year("rent", None, "5000", Decimal("30000"))
        + _flat_year("fuel", None, "2000", Decimal("1500"))
    )
    return NormalisedSpend(segments=tuple(segments))


def test_syn_waiver_rent_in_reward_not_waiver():
    result = apply_eligibility(_syn_waiver_spend(), SYN_WAIVER_EXCLUSIONS)
    assert ("rent", None) in _categories_present(result.reward)
    assert ("rent", None) not in _categories_present(result.waiver)


def test_syn_waiver_fuel_in_waiver_not_reward():
    result = apply_eligibility(_syn_waiver_spend(), SYN_WAIVER_EXCLUSIONS)
    assert ("fuel", None) in _categories_present(result.waiver)
    assert ("fuel", None) not in _categories_present(result.reward)


def test_syn_waiver_milestone_view_untouched_by_either_exclusion():
    result = apply_eligibility(_syn_waiver_spend(), SYN_WAIVER_EXCLUSIONS)
    present = _categories_present(result.milestone)
    assert ("rent", None) in present
    assert ("fuel", None) in present
    assert ("grocery", None) in present


def test_syn_waiver_untouched_category_appears_in_all_three_views():
    result = apply_eligibility(_syn_waiver_spend(), SYN_WAIVER_EXCLUSIONS)
    for view in (result.reward, result.milestone, result.waiver):
        assert ("grocery", None) in _categories_present(view)


def test_syn_waiver_view_totals_hand_computed():
    # grocery 3000*12=36000, rent 5000*12=60000, fuel 2000*12=24000
    result = apply_eligibility(_syn_waiver_spend(), SYN_WAIVER_EXCLUSIONS)
    assert _total(result.reward) == Decimal("96000")     # grocery(36000) + rent(60000), no fuel
    assert _total(result.milestone) == Decimal("120000")  # grocery + rent + fuel, nothing excluded
    assert _total(result.waiver) == Decimal("60000")      # grocery(36000) + fuel(24000), no rent


# ---------------------------------------------------------------------------
# syn_upi (C.9 Example 9) -- {channels: [upi], categories: [fuel, rent]}
# excluded from rewards only
# ---------------------------------------------------------------------------

SYN_UPI_EXCLUSIONS = (
    Exclusion(
        key="upi_fuel_rent",
        selector=ExclusionSelector(channels=("upi",), categories=("fuel", "rent")),
        excluded_from=("rewards",),
        note="UPI fuel/rent earns nothing",
    ),
)


def _syn_upi_spend():
    segments = (
        _flat_year("fuel", "upi", "500", Decimal("1500"))       # excluded from rewards
        + _flat_year("rent", "upi", "1000", Decimal("30000"))   # excluded from rewards
        + _flat_year("fuel", None, "500", Decimal("1500"))      # NOT upi channel -> unaffected
        + _flat_year("rent", None, "1000", Decimal("30000"))    # NOT upi channel -> unaffected
        + _flat_year("grocery", "upi", "2000", Decimal("700"))  # upi but not fuel/rent -> unaffected
    )
    return NormalisedSpend(segments=tuple(segments))


def test_syn_upi_channel_and_category_exclusion_earns_nothing_in_rewards():
    result = apply_eligibility(_syn_upi_spend(), SYN_UPI_EXCLUSIONS)
    present = _categories_present(result.reward)
    assert ("fuel", "upi") not in present
    assert ("rent", "upi") not in present


def test_syn_upi_non_upi_fuel_and_rent_unaffected():
    # Same categories, but general (non-UPI) channel -- the selector requires
    # BOTH channel=upi AND category in {fuel,rent} (AND across selector fields).
    result = apply_eligibility(_syn_upi_spend(), SYN_UPI_EXCLUSIONS)
    present = _categories_present(result.reward)
    assert ("fuel", None) in present
    assert ("rent", None) in present


def test_syn_upi_grocery_over_upi_unaffected():
    result = apply_eligibility(_syn_upi_spend(), SYN_UPI_EXCLUSIONS)
    assert ("grocery", "upi") in _categories_present(result.reward)


def test_syn_upi_milestone_and_waiver_views_untouched():
    # exclusion only lists "rewards" -- milestone/waiver views get everything.
    result = apply_eligibility(_syn_upi_spend(), SYN_UPI_EXCLUSIONS)
    input_categories = _categories_present(_syn_upi_spend().segments)
    assert _categories_present(result.milestone) == input_categories
    assert _categories_present(result.waiver) == input_categories


def test_syn_upi_view_totals_hand_computed():
    # upi fuel 500*12=6000, upi rent 1000*12=12000, general fuel 6000,
    # general rent 12000, upi grocery 2000*12=24000. Total = 60000.
    result = apply_eligibility(_syn_upi_spend(), SYN_UPI_EXCLUSIONS)
    assert _total(result.reward) == Decimal("42000")      # 60000 - upi_fuel(6000) - upi_rent(12000)
    assert _total(result.milestone) == Decimal("60000")
    assert _total(result.waiver) == Decimal("60000")


# ---------------------------------------------------------------------------
# General behaviour
# ---------------------------------------------------------------------------

def test_no_exclusions_all_three_views_equal_full_spend():
    spend = NormalisedSpend(segments=tuple(_flat_year("grocery", None, "1000")))
    result = apply_eligibility(spend, exclusions=())
    assert result.reward == spend.segments
    assert result.milestone == spend.segments
    assert result.waiver == spend.segments


def test_unknown_excluded_from_scope_raises():
    bad = Exclusion(key="bad", selector=ExclusionSelector(categories=("fuel",)), excluded_from=("bonus_points",))
    spend = NormalisedSpend(segments=tuple(_flat_year("fuel", None, "1000")))
    with pytest.raises(ValueError, match="unknown excluded_from scope"):
        apply_eligibility(spend, (bad,))


def test_unsupported_selector_field_raises():
    bad = Exclusion(
        key="bad_mcc",
        selector=ExclusionSelector(mcc_exclude=(6540,)),
        excluded_from=("rewards",),
    )
    spend = NormalisedSpend(segments=tuple(_flat_year("fuel", None, "1000")))
    with pytest.raises(ValueError, match="cannot be matched against"):
        apply_eligibility(spend, (bad,))
