"""Regression test for `engine/card_bundle.py`'s selector loaders. Before
this fix, `_selector_from_dict`/`_exclusion_selector_from_dict` silently
dropped every Part C SS C.2.1 selector field except categories/channels/
merchant_groups/geography -- so `match._validate_rule` and `eligibility.
_validate_exclusion` (which already exist specifically to raise on an
unsupported field) never actually saw those fields and never fired.
Discovered while building `compute/ingest`'s lint tool; see docs/
DECISIONS.md. No existing golden covers this because no synthetic card
uses these fields -- these are hand-built fixtures.
"""
from decimal import Decimal

import pytest

from engine.card_bundle import _exclusion_selector_from_dict, _selector_from_dict
from engine.eligibility import apply_eligibility
from engine.match import match
from engine.normalise import NormalisedSpend, SpendSegment

# match()'s _validate_rule call is nested inside the per-segment loop
# (unlike eligibility.py's apply_eligibility, which validates upfront
# regardless of segments) -- a probe needs at least one segment to reach
# it. The segment's own fields don't matter; _validate_rule loops over
# ALL earning_rules unconditionally, not just ones the segment matches.
_PROBE_SEGMENT = SpendSegment(category="_probe", channel=None, month=1, amount=Decimal("0"), ticket_size=Decimal("1"))


def test_selector_from_dict_populates_previously_dropped_fields():
    selector = _selector_from_dict({
        "categories": ["fuel"], "mcc_include": [5172], "txn_min": "500", "txn_max": "3000",
    })
    assert selector.categories == ("fuel",)
    assert selector.mcc_include == (5172,)
    assert selector.txn_min == Decimal("500")
    assert selector.txn_max == Decimal("3000")


def test_exclusion_selector_from_dict_populates_previously_dropped_fields():
    selector = _exclusion_selector_from_dict({"categories": ["fuel"], "mcc_include": [5172, 5541]})
    assert selector.categories == ("fuel",)
    assert selector.mcc_include == (5172, 5541)


def test_earning_rule_with_unsupported_selector_field_now_raises():
    from engine.match import EarningRule, Selector

    bad_rule = EarningRule(key="bad", selector=Selector(mcc_include=(5172,)), priority=10)
    with pytest.raises(ValueError, match="mcc_include"):
        match(NormalisedSpend(segments=(_PROBE_SEGMENT,)), [bad_rule])


def test_exclusion_with_unsupported_selector_field_now_raises():
    # txn_max moved to accepted-but-unenforced in Phase 5 Task A (docs/
    # DECISIONS.md #130) -- merchants stays genuinely unsupported.
    from engine.eligibility import Exclusion, ExclusionSelector

    bad_exclusion = Exclusion(
        key="bad", selector=ExclusionSelector(merchants=("bigbasket",)), excluded_from=("rewards",),
    )
    with pytest.raises(ValueError, match="merchants"):
        apply_eligibility(NormalisedSpend(segments=()), [bad_exclusion])


def test_surcharge_with_unsupported_selector_field_now_raises():
    """Unlike the two above, this validator (costs._validate_surcharge)
    didn't exist at all before this pass -- surcharges never had one,
    a genuinely separate gap from the loader bug the other two tests
    cover. Discovered while building compute/ingest's lint tool."""
    from engine.costs import Surcharge, surcharge_cost
    from engine.match import Selector

    bad_surcharge = Surcharge(key="bad", selector=Selector(mcc_include=(5172,)), rate=Decimal("0.01"))
    with pytest.raises(ValueError, match="mcc_include"):
        surcharge_cost((), [bad_surcharge])
