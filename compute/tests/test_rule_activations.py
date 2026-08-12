"""Unit tests for engine/thresholds.py's apply_rule_activations (Part C
SS C.3's activate_rule payload). syn_renewal (prospective) and syn_retro
(retroactive) fixtures are hand-transcribed from seeds/synthetic_cards.py
(C.9 Examples 12, 6), not invented. Expected values are hand-computed
constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

from engine.accrue import Accrual, accrue_category_mode
from engine.caps import Window
from engine.match import EarningRule, Selector, match
from engine.normalise import NormalisedSpend, SpendSegment
from engine.thresholds import Payload, Threshold, ThresholdBasis, Tier, apply_rule_activations, evaluate_thresholds

ANNIV = Window(kind="anniversary_year")


def _dining_segment(amount, month):
    return SpendSegment(category="dining", channel=None, month=month, amount=Decimal(amount), ticket_size=Decimal("600"))


def _grocery_segment(amount, month):
    return SpendSegment(category="grocery", channel=None, month=month, amount=Decimal(amount), ticket_size=Decimal("700"))


def _by_month_rule(results):
    return {(r.segment.month, r.rule_key): r.reward for r in results}


# ---------------------------------------------------------------------------
# syn_renewal (C.9 Example 12): prospective activation of dining_2x
# ---------------------------------------------------------------------------

SYN_RENEWAL_RULES = (
    EarningRule(key="base", selector=Selector(), priority=10),
    EarningRule(key="dining_2x", selector=Selector(categories=("dining",)), priority=100, requires_activation=True),
)
SYN_RENEWAL_ACCRUALS = {
    "base": Accrual(type="per_unit", unit_amount=Decimal("100"), points_per_unit=Decimal("1"), rounding="floor_per_txn", currency="synth_points"),
    "dining_2x": Accrual(type="per_unit", unit_amount=Decimal("100"), points_per_unit=Decimal("2"), rounding="floor_per_txn", currency="synth_points"),
}
SYN_RENEWAL_THRESHOLD = Threshold(
    key="anniv", basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV), tier_mode="cumulative",
    tiers=(
        Tier(1, Decimal("100000"), Payload(type="activate_rule", rule="dining_2x", application="prospective")),
        Tier(2, Decimal("500000"), Payload(type="grant_points", amount=Decimal("10000"), currency="synth_points", condition="on_renewal")),
    ),
)


def test_syn_renewal_prospective_activation_end_to_end():
    # Flat Rs30,000/month dining, ticket 600 (exact multiple of the
    # 100-unit rule -> zero floor loss both before and after activation).
    # Running total crosses 100,000 in month 4 (120,000 >= 100,000; month 3
    # was only 90,000) -- total annual = 360,000, so tier 2 (500,000) never
    # fires, keeping this test focused on activate_rule alone.
    segments = tuple(_dining_segment("30000", m) for m in range(1, 13))
    normalised = NormalisedSpend(segments=segments)

    # Stage 3 (initial, pre-activation): dining_2x is requires_activation
    # and no active_rule_keys given -> every month binds to "base" only.
    bindings = match(normalised, SYN_RENEWAL_RULES)
    original = accrue_category_mode(bindings, SYN_RENEWAL_ACCRUALS)
    assert {r.rule_key for r in original} == {"base"}

    threshold_events = evaluate_thresholds([SYN_RENEWAL_THRESHOLD], milestone_segments=segments, waiver_segments=())
    activate_event = next(e for e in threshold_events if e.payload.type == "activate_rule")
    assert activate_event.crossing_month == 4

    final = apply_rule_activations(original, threshold_events, segments, SYN_RENEWAL_RULES, SYN_RENEWAL_ACCRUALS)

    by_month_rule = _by_month_rule(final)
    # Months 1-4 (crossing month INCLUDED) keep the original base rate:
    # floor(600/100)=6, 6*1=6pts/ticket -> ea=6/600=0.01 exact. 30,000*0.01=300pts.
    for month in (1, 2, 3, 4):
        assert by_month_rule[(month, "base")] == Decimal("300")
        assert (month, "dining_2x") not in by_month_rule
    # Months 5-12 (strictly after the crossing month) re-rate to dining_2x:
    # floor(600/100)=6, 6*2=12pts/ticket -> ea=12/600=0.02 exact. 30,000*0.02=600pts.
    for month in range(5, 13):
        assert by_month_rule[(month, "dining_2x")] == Decimal("600")
        assert (month, "base") not in by_month_rule

    total = sum((r.reward for r in final), Decimal("0"))
    assert total == Decimal("300") * 4 + Decimal("600") * 8  # 1,200 + 4,800 = 6,000


def test_prospective_activation_crossing_in_final_month_activates_nothing():
    # Crossing happens in month 12 -> no months strictly after it within
    # the annual window -> the rule never actually applies this year.
    segments = tuple(_dining_segment("8500", m) for m in range(1, 13))  # 8,500*12=102,000, crosses only in month 12
    normalised = NormalisedSpend(segments=segments)
    bindings = match(normalised, SYN_RENEWAL_RULES)
    original = accrue_category_mode(bindings, SYN_RENEWAL_ACCRUALS)

    threshold_events = evaluate_thresholds([SYN_RENEWAL_THRESHOLD], milestone_segments=segments, waiver_segments=())
    activate_event = next(e for e in threshold_events if e.payload.type == "activate_rule")
    assert activate_event.crossing_month == 12

    final = apply_rule_activations(original, threshold_events, segments, SYN_RENEWAL_RULES, SYN_RENEWAL_ACCRUALS)
    assert final == original  # no change at all -- nothing was active


# ---------------------------------------------------------------------------
# syn_retro (C.9 Example 6): retroactive, highest_only suppression
# ---------------------------------------------------------------------------

SYN_RETRO_RULES = (
    EarningRule(key="base", selector=Selector(), priority=10),
    EarningRule(key="rate_2", selector=Selector(), priority=50, requires_activation=True),
    EarningRule(key="rate_3", selector=Selector(), priority=60, requires_activation=True),
)
SYN_RETRO_ACCRUALS = {
    "base": Accrual(type="percentage", rate=Decimal("0.01"), rounding="floor_paise_per_txn", currency="cashback_inr"),
    "rate_2": Accrual(type="percentage", rate=Decimal("0.02"), rounding="floor_paise_per_txn", currency="cashback_inr"),
    "rate_3": Accrual(type="percentage", rate=Decimal("0.03"), rounding="floor_paise_per_txn", currency="cashback_inr"),
}
SYN_RETRO_THRESHOLD = Threshold(
    key="tiers", basis=ThresholdBasis(measure="milestone_eligible_spend", window=ANNIV), tier_mode="highest_only",
    tiers=(
        Tier(1, Decimal("100000"), Payload(type="activate_rule", rule="rate_2", application="retroactive")),
        Tier(2, Decimal("300000"), Payload(type="activate_rule", rule="rate_3", application="retroactive")),
    ),
)


def test_syn_retro_retroactive_activation_whole_year_reretes_at_highest_tier():
    # Flat Rs35,000/month, ticket 700 (700*0.03=21.00 exact, no paisa loss).
    # Annual = 420,000 -> crosses both tiers; highest_only suppresses tier 1
    # (rate_2), only rate_3 (3%) fires -- retroactively re-rating ALL 12 months.
    segments = tuple(_grocery_segment("35000", m) for m in range(1, 13))
    normalised = NormalisedSpend(segments=segments)

    bindings = match(normalised, SYN_RETRO_RULES)
    original = accrue_category_mode(bindings, SYN_RETRO_ACCRUALS)
    assert {r.rule_key for r in original} == {"base"}
    assert sum((r.reward for r in original), Decimal("0")) == Decimal("4200.00")  # 35,000*0.01*12

    threshold_events = evaluate_thresholds([SYN_RETRO_THRESHOLD], milestone_segments=segments, waiver_segments=())
    assert len(threshold_events) == 1  # highest_only: only rate_3's tier fires
    assert threshold_events[0].payload.rule == "rate_3"

    final = apply_rule_activations(original, threshold_events, segments, SYN_RETRO_RULES, SYN_RETRO_ACCRUALS)

    assert {r.rule_key for r in final} == {"rate_3"}  # base entirely replaced, every month
    assert len(final) == 12
    for r in final:
        assert r.reward == Decimal("1050.00")  # 35,000 * 0.03
    assert sum((r.reward for r in final), Decimal("0")) == Decimal("12600.00")


def test_syn_retro_below_lower_tier_stays_on_base():
    segments = tuple(_grocery_segment("5000", m) for m in range(1, 13))  # 60,000 annual, below even 100,000
    normalised = NormalisedSpend(segments=segments)
    bindings = match(normalised, SYN_RETRO_RULES)
    original = accrue_category_mode(bindings, SYN_RETRO_ACCRUALS)

    threshold_events = evaluate_thresholds([SYN_RETRO_THRESHOLD], milestone_segments=segments, waiver_segments=())
    assert threshold_events == ()

    final = apply_rule_activations(original, threshold_events, segments, SYN_RETRO_RULES, SYN_RETRO_ACCRUALS)
    assert final == original
