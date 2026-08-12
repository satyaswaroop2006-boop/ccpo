"""Golden-style hand-computed scenarios for optimiser/enumerate.py (Phase
4 slice 3). Deliberately small candidate sets so every subset's outcome
is hand-computable or cross-checked against an existing slice-1 test
number, never re-derived from the code under test.
"""
from decimal import Decimal

import pytest

from app.repository import SyntheticCatalogRepository
from engine.normalise import CategorySpend, SpendInput
from optimiser.enumerate import enumerate_subsets

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()

# Same spend as tests/test_allocate.py's scenario 2: Rs6,00,000/yr
# ecommerce/online, syn_ecom (1% base / 5% capped at Rs20,000/mo
# spend-equivalent, overflow=base_rate) vs syn_flat (flat 1.5%, no fee,
# no threshold).
SPEND = SpendInput(category_spend=(
    CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
))


def _bundles():
    return [REPO.get_card_bundle("syn_ecom"), REPO.get_card_bundle("syn_flat")]


def test_combinatorial_correctness_up_to_two():
    results = enumerate_subsets(_bundles(), CURRENCIES, SPEND, cardinality_mode="up_to", max_cards=2)
    assert len(results) == 3  # C(2,1) + C(2,2) = 2 + 1

    by_key = {r.subset_key: r for r in results}
    assert set(by_key) == {"syn_ecom", "syn_flat", "syn_ecom+syn_flat"}
    assert by_key["syn_ecom"].size == 1
    assert by_key["syn_flat"].size == 1
    assert by_key["syn_ecom+syn_flat"].size == 2
    assert by_key["syn_ecom+syn_flat"].card_keys == ("syn_ecom", "syn_flat")  # sorted order


def test_numbers_match_independent_computation():
    results = enumerate_subsets(_bundles(), CURRENCIES, SPEND, cardinality_mode="up_to", max_cards=2)
    by_key = {r.subset_key: r for r in results}

    # syn_ecom alone: no syn_flat alternative, so overflow past the
    # Rs20,000/mo cap re-rates to syn_ecom's OWN base (1%), not discarded
    # (overflow=base_rate) and not diverted (no better card in this
    # subset). Rs20,000/mo @5% (Rs1,000) + Rs30,000/mo @1% (Rs300) =
    # Rs1,300/mo * 12 = Rs15,600.00/yr. Total spend (Rs6,00,000) clears
    # the Rs1,00,000 waiver threshold outright -- fee waived, no repair
    # needed (nothing near-miss).
    ecom_only = by_key["syn_ecom"]
    assert ecom_only.pv_planned == Decimal("15600.00")
    assert ecom_only.pv_exact == Decimal("15600.00")
    assert ecom_only.repair_applied is False

    # syn_flat alone: flat 1.5% on the whole Rs6,00,000, no fee, no threshold.
    flat_only = by_key["syn_flat"]
    assert flat_only.pv_planned == Decimal("9000.00")
    assert flat_only.pv_exact == Decimal("9000.00")

    # both cards: matches tests/test_allocate.py's own scenario 2 exactly
    # (Rs20,000/mo fills syn_ecom's capped 5% segment = Rs12,000/yr; the
    # remaining Rs30,000/mo goes to syn_flat's better 1.5% = Rs5,400/yr).
    both = by_key["syn_ecom+syn_flat"]
    assert both.pv_planned == Decimal("17400.00")
    assert both.pv_exact == Decimal("17400.00")

    # the 2-card subset is the genuine best of the three -- sets up
    # cleanly for frontier.py later without building it now.
    assert both.pv_exact > ecom_only.pv_exact > flat_only.pv_exact


def test_exactly_mode_filters_to_single_size():
    results = enumerate_subsets(_bundles(), CURRENCIES, SPEND, cardinality_mode="exactly", max_cards=1)
    assert len(results) == 2  # only the two size-1 subsets
    assert {r.subset_key for r in results} == {"syn_ecom", "syn_flat"}
    assert all(r.size == 1 for r in results)


def test_unknown_cardinality_mode_raises():
    with pytest.raises(ValueError, match="unknown cardinality_mode"):
        enumerate_subsets(_bundles(), CURRENCIES, SPEND, cardinality_mode="bogus", max_cards=1)
