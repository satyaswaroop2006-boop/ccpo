"""Unit tests for engine/caps.py's apply_incremental_bands (Part A SS A.3's
convex-PWL case; Part C SS C.9 Example 7). syn_slab's rules/caps are
hand-transcribed from seeds/synthetic_cards.py, not invented. Expected
values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

import pytest

from engine.accrue import Accrual, accrue_category_mode
from engine.caps import Cap, Window, apply_incremental_bands
from engine.match import EarningRule, Selector, match
from engine.normalise import NormalisedSpend, SpendSegment

ANNIV = Window(kind="anniversary_year")

SLAB1 = EarningRule(key="slab1", selector=Selector(), priority=30, rule_group="slab", tier_mode="incremental")
SLAB2 = EarningRule(key="slab2", selector=Selector(), priority=20, rule_group="slab", tier_mode="incremental")
SLAB3 = EarningRule(key="slab3", selector=Selector(), priority=10, rule_group="slab", tier_mode="incremental")
SLAB_RULES = (SLAB1, SLAB2, SLAB3)

SLAB_ACCRUALS = {
    "slab1": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn", currency="cashback_inr"),
    "slab2": Accrual(type="percentage", rate=Decimal("0.02"), rounding="floor_paise_per_txn", currency="cashback_inr"),
    "slab3": Accrual(type="percentage", rate=Decimal("0.03"), rounding="floor_paise_per_txn", currency="cashback_inr"),
}
BAND1 = Cap(key="band1", rule_key="slab1", measure="spend", amount=Decimal("100000"), window=ANNIV, scope="rule", overflow="zero")
BAND2 = Cap(key="band2", rule_key="slab2", measure="spend", amount=Decimal("200000"), window=ANNIV, scope="rule", overflow="zero")
SLAB_CAPS = (BAND1, BAND2)  # slab3 deliberately uncapped -- gets the remainder


def _segments(monthly_amount, months=range(1, 13)):
    return tuple(SpendSegment(category="grocery", channel=None, month=m, amount=Decimal(monthly_amount), ticket_size=Decimal("700")) for m in months)


def _by_rule(results):
    return {r.rule_key: r.reward for r in results}


# ---------------------------------------------------------------------------
# tier_mode="incremental" is excluded from ordinary Stage 3 matching
# ---------------------------------------------------------------------------

def test_incremental_rules_never_bind_under_ordinary_matching():
    segments = _segments("40000")  # 480,000 annual, would cross all three bands if they mattered here
    bindings = match(NormalisedSpend(segments=segments), SLAB_RULES)
    assert bindings == ()  # every rule is tier_mode=incremental -> nothing ever binds


# ---------------------------------------------------------------------------
# apply_incremental_bands: fills bands in descending-priority order
# ---------------------------------------------------------------------------

def test_spend_crossing_all_three_bands_hand_computed():
    # Rs5,00,000 annual (one Rs5,00,000 spend in December, simplest to pool):
    # band1: min(500000,100000)=100,000 @ 1% = 1,000.00
    # band2: min(400000,200000)=200,000 @ 2% = 4,000.00
    # band3 (uncapped): remaining 200,000 @ 3% = 6,000.00
    segments = (SpendSegment(category="grocery", channel=None, month=12, amount=Decimal("500000"), ticket_size=Decimal("700")),)
    results = apply_incremental_bands(segments, SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)

    by_rule = _by_rule(results)
    assert by_rule["slab1"] == Decimal("1000.00")
    assert by_rule["slab2"] == Decimal("4000.00")
    assert by_rule["slab3"] == Decimal("6000.00")
    assert sum((r.reward for r in results), Decimal("0")) == Decimal("11000.00")


def test_spend_within_first_band_only():
    segments = (SpendSegment(category="grocery", channel=None, month=6, amount=Decimal("60000"), ticket_size=Decimal("700")),)
    results = apply_incremental_bands(segments, SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)

    assert len(results) == 1
    assert results[0].rule_key == "slab1"
    assert results[0].reward == Decimal("600.00")  # 60,000 * 1%


def test_spend_exactly_at_a_band_boundary():
    segments = (SpendSegment(category="grocery", channel=None, month=6, amount=Decimal("100000"), ticket_size=Decimal("700")),)
    results = apply_incremental_bands(segments, SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)

    assert len(results) == 1  # exactly fills band1, nothing left for band2/band3
    assert results[0].rule_key == "slab1"
    assert results[0].reward == Decimal("1000.00")


def test_pooling_across_months_matches_single_lump_sum():
    # Rs40,000/month spread across all 12 months should band-split
    # identically to one lump sum of the same annual total (Rs4,80,000) --
    # the annual window pools every month's spend before banding, so
    # *how* the total arrived, month by month or all at once, is irrelevant.
    spread_segments = tuple(SpendSegment(category="grocery", channel=None, month=m, amount=Decimal("40000"), ticket_size=Decimal("700")) for m in range(1, 13))
    lump_segments = (SpendSegment(category="grocery", channel=None, month=12, amount=Decimal("480000"), ticket_size=Decimal("700")),)

    spread_results = apply_incremental_bands(spread_segments, SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)
    lump_results = apply_incremental_bands(lump_segments, SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)

    spread_total = sum((r.reward for r in spread_results), Decimal("0"))
    lump_total = sum((r.reward for r in lump_results), Decimal("0"))
    assert spread_total == lump_total == Decimal("10400.00")  # 1,000 + 4,000 + (180,000*3%=5,400)


def test_zero_spend_produces_no_results():
    results = apply_incremental_bands((), SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)
    assert results == ()


def test_end_to_end_syn_slab_pipeline_matches_direct_call():
    # Confirms the intended pipeline shape: normal Stage 3 match() on the
    # full rule list correctly produces nothing (all incremental), and
    # apply_incremental_bands is the only source of this card's reward.
    segments = _segments("40000")  # 480,000 annual: band1=100k@1%, band2=200k@2%, band3=180k@3%
    normalised = NormalisedSpend(segments=segments)

    bindings = match(normalised, SLAB_RULES)
    ordinary_results = accrue_category_mode(bindings, SLAB_ACCRUALS)
    assert ordinary_results == ()

    band_results = apply_incremental_bands(segments, SLAB_RULES, SLAB_CAPS, SLAB_ACCRUALS)
    by_rule = _by_rule(band_results)
    assert by_rule["slab1"] == Decimal("1000.00")   # 100,000 * 1%
    assert by_rule["slab2"] == Decimal("4000.00")   # 200,000 * 2%
    assert by_rule["slab3"] == Decimal("5400.00")   # (480,000-300,000)=180,000 * 3%
    assert sum((r.reward for r in band_results), Decimal("0")) == Decimal("10400.00")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_mismatched_selectors_raise():
    different_selector_rule = EarningRule(key="slab1b", selector=Selector(categories=("fuel",)), priority=30, tier_mode="incremental")
    with pytest.raises(ValueError, match="identical selector"):
        apply_incremental_bands((), (different_selector_rule, SLAB2), SLAB_CAPS, SLAB_ACCRUALS)


def test_reward_measure_cap_raises_for_incremental_bands():
    bad_cap = Cap(key="band1", rule_key="slab1", measure="reward", amount=Decimal("1000"), window=ANNIV, scope="rule", overflow="zero")
    with pytest.raises(ValueError, match="measure='spend'"):
        apply_incremental_bands((), SLAB_RULES, (bad_cap,), SLAB_ACCRUALS)


def test_non_rule_scope_raises():
    bad_cap = Cap(key="band1", rule_key="slab1", measure="spend", amount=Decimal("100000"), window=ANNIV, scope="card", overflow="zero")
    with pytest.raises(ValueError, match="scope='rule'"):
        apply_incremental_bands((), SLAB_RULES, (bad_cap,), SLAB_ACCRUALS)


def test_mismatched_windows_across_capped_bands_raise():
    monthly_cap = Cap(key="band2m", rule_key="slab2", measure="spend", amount=Decimal("200000"), window=Window(kind="calendar_month"), scope="rule", overflow="zero")
    with pytest.raises(ValueError, match="one shared window"):
        apply_incremental_bands((), SLAB_RULES, (BAND1, monthly_cap), SLAB_ACCRUALS)


def test_empty_band_rules_returns_empty():
    assert apply_incremental_bands((), (), (), {}) == ()
