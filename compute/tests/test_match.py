"""Unit tests for Stage 3 (engine/match.py), Part C SS C.2.1 / SS C.2.6 / SS C.4 Stage 3.

Rule fixtures for syn_points, syn_ecom, and syn_fuel are hand-transcribed
from seeds/synthetic_cards.py (C.9 Examples 3, 2, 10), not invented.
"""
from decimal import Decimal

import pytest

from engine.match import EarningRule, Selector, match_segment
from engine.normalise import SpendSegment


def _segment(category, channel=None, merchant_group=None, amount="1000"):
    return SpendSegment(
        category=category, channel=channel, month=1, amount=Decimal(amount),
        ticket_size=Decimal("1000"), merchant_group=merchant_group,
    )


def _keys(bindings):
    return {b.rule_key for b in bindings}


# ---------------------------------------------------------------------------
# syn_points (C.9 Example 3): base 5pts/Rs150 (priority 10) +
# portal_bonus 20pts/Rs150 on merchant_group=synth_portal (priority 100,
# stacks_with_base=True, rule_group=portal_accel)
# ---------------------------------------------------------------------------

SYN_POINTS_RULES = (
    EarningRule(key="base", selector=Selector(), priority=10, stacks_with_base=False),
    EarningRule(
        key="portal_bonus",
        selector=Selector(merchant_groups=("synth_portal",)),
        priority=100,
        stacks_with_base=True,
    ),
)


def test_syn_points_portal_spend_binds_base_and_portal_bonus():
    segment = _segment("travel", merchant_group="synth_portal")
    bindings = match_segment(segment, SYN_POINTS_RULES)

    assert _keys(bindings) == {"base", "portal_bonus"}
    base_binding = next(b for b in bindings if b.rule_key == "base")
    bonus_binding = next(b for b in bindings if b.rule_key == "portal_bonus")
    assert base_binding.stacked is False
    assert bonus_binding.stacked is True


def test_syn_points_ordinary_spend_binds_only_base():
    segment = _segment("dining")  # no merchant_group -> portal_bonus selector doesn't match
    bindings = match_segment(segment, SYN_POINTS_RULES)

    assert _keys(bindings) == {"base"}
    assert bindings[0].stacked is False


# ---------------------------------------------------------------------------
# syn_ecom (C.9 Example 2): base 1% (priority 10) vs ecom 5% on
# categories=[ecommerce], channels=[online] (priority 100, replaces base --
# stacks_with_base defaults to False)
# ---------------------------------------------------------------------------

SYN_ECOM_RULES = (
    EarningRule(key="base", selector=Selector(), priority=10, stacks_with_base=False),
    EarningRule(
        key="ecom",
        selector=Selector(categories=("ecommerce",), channels=("online",)),
        priority=100,
        stacks_with_base=False,
    ),
)


def test_syn_ecom_online_ecommerce_binds_only_ecom():
    segment = _segment("ecommerce", channel="online")
    bindings = match_segment(segment, SYN_ECOM_RULES)

    assert _keys(bindings) == {"ecom"}
    assert bindings[0].stacked is False


def test_syn_ecom_grocery_binds_only_base():
    segment = _segment("grocery", channel="online")
    bindings = match_segment(segment, SYN_ECOM_RULES)

    assert _keys(bindings) == {"base"}


def test_syn_ecom_ecommerce_without_online_channel_falls_back_to_base():
    # AND semantics (C.2.1): categories AND channels must both match --
    # ecommerce spend not tagged "online" doesn't trigger the ecom rule.
    segment = _segment("ecommerce", channel=None)
    bindings = match_segment(segment, SYN_ECOM_RULES)

    assert _keys(bindings) == {"base"}


# ---------------------------------------------------------------------------
# syn_fuel (C.9 Example 10): base 0.5% (priority 10) +
# fuel_refund 1% on categories=[fuel] (priority 100, stacks_with_base=True)
# ---------------------------------------------------------------------------

SYN_FUEL_RULES = (
    EarningRule(key="base", selector=Selector(), priority=10, stacks_with_base=False),
    EarningRule(
        key="fuel_refund",
        selector=Selector(categories=("fuel",)),
        priority=100,
        stacks_with_base=True,
    ),
)


def test_syn_fuel_fuel_spend_binds_base_and_refund():
    segment = _segment("fuel")
    bindings = match_segment(segment, SYN_FUEL_RULES)

    assert _keys(bindings) == {"base", "fuel_refund"}
    base_binding = next(b for b in bindings if b.rule_key == "base")
    refund_binding = next(b for b in bindings if b.rule_key == "fuel_refund")
    assert base_binding.stacked is False
    assert refund_binding.stacked is True


def test_syn_fuel_non_fuel_spend_binds_only_base():
    segment = _segment("grocery")
    bindings = match_segment(segment, SYN_FUEL_RULES)
    assert _keys(bindings) == {"base"}


# ---------------------------------------------------------------------------
# Conflict resolution mechanics: priority -> specificity -> publication order
# ---------------------------------------------------------------------------

def test_higher_priority_wins_over_more_specific_lower_priority_rule():
    # A more specific but lower-priority rule must still lose to a less
    # specific, higher-priority one -- priority is checked first.
    rules = (
        EarningRule(key="specific_low", selector=Selector(categories=("dining",), channels=("pos",)), priority=10),
        EarningRule(key="generic_high", selector=Selector(categories=("dining",)), priority=50),
    )
    bindings = match_segment(_segment("dining", channel="pos"), rules)
    assert _keys(bindings) == {"generic_high"}


def test_equal_priority_more_specific_selector_wins():
    rules = (
        EarningRule(key="generic", selector=Selector(categories=("dining",)), priority=50),
        EarningRule(key="specific", selector=Selector(categories=("dining",), channels=("pos",)), priority=50),
    )
    bindings = match_segment(_segment("dining", channel="pos"), rules)
    assert _keys(bindings) == {"specific"}


def test_tie_on_priority_and_specificity_uses_publication_order_and_warns():
    # Two same-priority, same-specificity rules that both match the segment.
    tied_rules = (
        EarningRule(key="rule_a", selector=Selector(categories=("dining", "fuel")), priority=50),
        EarningRule(key="rule_b", selector=Selector(categories=("dining", "grocery")), priority=50),
    )
    bindings = match_segment(_segment("dining"), tied_rules)
    assert len(bindings) == 1
    assert bindings[0].rule_key == "rule_a"  # published first -> wins
    assert bindings[0].flags == ("priority_specificity_tie",)


def test_unsupported_selector_field_raises():
    bad_rule = EarningRule(key="bad", selector=Selector(networks=("rupay",)), priority=10)
    with pytest.raises(ValueError, match="cannot be matched against"):
        match_segment(_segment("travel"), (bad_rule,))


def test_txn_threshold_selector_is_accepted_but_unenforced_and_flagged():
    """Phase 5 Task A (docs/DECISIONS.md #130): txn_min/txn_max no longer
    raise on an earning-rule selector, but category mode still has no
    per-transaction data to test them against -- the rule must bind every
    matching segment regardless (not silently filter), flagged instead.
    Task B's fuel-surcharge-waiver-as-capped-earning-rule is the reason
    this needed to land on match.py's Selector, not just eligibility.py's
    ExclusionSelector."""
    rule = EarningRule(key="fuel_waiver_refund", selector=Selector(categories=("fuel",), txn_min=Decimal("500"), txn_max=Decimal("3000")), priority=10)
    bindings = match_segment(_segment("fuel"), (rule,))
    assert len(bindings) == 1
    assert bindings[0].rule_key == "fuel_waiver_refund"  # not filtered out
    assert bindings[0].flags == ("txn_threshold_unenforced",)


def test_geography_selector_matches_segments_own_geography():
    domestic_only = EarningRule(key="dom", selector=Selector(geography="domestic"), priority=10)
    intl_only = EarningRule(key="intl", selector=Selector(geography="international"), priority=10)

    domestic_segment = _segment("travel")  # SpendSegment default geography="domestic"
    bindings = match_segment(domestic_segment, (domestic_only, intl_only))
    assert {b.rule_key for b in bindings} == {"dom"}


def test_geography_all_matches_every_segment():
    from engine.normalise import SpendSegment
    international_segment = SpendSegment(category="travel", channel=None, month=1, amount=Decimal("1000"), ticket_size=Decimal("1000"), geography="international")
    any_geo = EarningRule(key="any", selector=Selector(geography="all"), priority=10)
    bindings = match_segment(international_segment, (any_geo,))
    assert {b.rule_key for b in bindings} == {"any"}
