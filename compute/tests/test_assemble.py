"""Unit tests for Stage 11 (engine/assemble.py), Part A SS A.12 / Part C SS C.4.2.

Milestone payload fixtures for syn_renewal's grant_points tier and
syn_miles' grant_voucher are hand-transcribed from seeds/synthetic_cards.py
(C.9 Examples 12, 4). syn_renewal's tier is hand-constructed as a
ThresholdEvent directly (not run through evaluate_threshold) because its
threshold's first tier is activate_rule, which correctly raises when
crossed (Stage 6-7 scope; docs/DECISIONS.md #13) -- this test is about
Stage 11's valuation of the payload, not re-deriving the crossing.
Expected values are hand-computed constants (CLAUDE.md rule 1).
"""
from decimal import Decimal

from engine.assemble import assemble_nacv, value_milestone_grants
from engine.benefits import BenefitValuation
from engine.thresholds import Payload, ThresholdEvent
from engine.valuation import RedemptionRoute, RewardCurrency

CASHBACK_INR = RewardCurrency(key="cashback_inr", routes=(
    RedemptionRoute(key="stmt", route_type="statement_credit", ratio=Decimal("1.0")),
))
SYNTH_POINTS = RewardCurrency(key="synth_points", routes=(
    RedemptionRoute(key="stmt", route_type="statement_credit", ratio=Decimal("0.25")),
    RedemptionRoute(
        key="transfer", route_type="transfer", friction=Decimal("0.8"),
        transfer_ratio=Decimal("1.0"), partner_point_value=Decimal("1.0"), min_points=Decimal("5000"),
    ),
))


# ---------------------------------------------------------------------------
# assemble_nacv
# ---------------------------------------------------------------------------

def test_assemble_nacv_matches_golden_syn_ecom_basic():
    # golden_syn_ecom_basic.json: gross=14400, no milestones/benefits/forex/
    # surcharge, waived (steady_fee=0), year1_fee=590.
    result = assemble_nacv(
        gross_reward=Decimal("14400.00"), milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=Decimal("0"), year1_fee=Decimal("590.00"),
    )
    assert result.steady_state == Decimal("14400.00")
    assert result.year_1 == Decimal("13810.00")
    assert result.three_year == Decimal("42610.00")  # 13810 + 2*14400


def test_assemble_nacv_full_formula_hand_computed():
    result = assemble_nacv(
        gross_reward=Decimal("10000"), milestone_value=Decimal("2000"), benefit_value=Decimal("1500"),
        steady_fee=Decimal("590"), year1_fee=Decimal("1180"),
        forex_cost=Decimal("200"), surcharge_cost=Decimal("100"), welcome_value=Decimal("500"),
    )
    # steady = 10000+2000+1500-590-200-100 = 12610
    assert result.steady_state == Decimal("12610")
    # year1 = 10000+2000+1500+500-1180-200-100 = 12520
    assert result.year_1 == Decimal("12520")
    # 3yr = 12520 + 2*12610 = 37740
    assert result.three_year == Decimal("37740")


def test_assemble_nacv_trace_includes_canonical_lines_even_when_zero():
    result = assemble_nacv(
        gross_reward=Decimal("1000"), milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=Decimal("0"), year1_fee=Decimal("0"),
    )
    kinds = [line.kind for line in result.trace]
    assert kinds == ["reward", "benefit", "fee", "forex", "surcharge"]
    assert result.trace[0].amount == Decimal("1000")
    assert result.trace[2].amount == Decimal("0")  # fee line present even at Rs0


def test_assemble_nacv_milestone_lines_are_inserted_into_the_trace():
    from engine.assemble import TraceLine
    milestone_lines = (TraceLine(kind="milestone", amount=Decimal("8000"), label="grant_points"),)
    result = assemble_nacv(
        gross_reward=Decimal("1000"), milestone_value=Decimal("8000"), benefit_value=Decimal("0"),
        steady_fee=Decimal("0"), year1_fee=Decimal("0"), milestone_lines=milestone_lines,
    )
    kinds = [line.kind for line in result.trace]
    assert kinds == ["reward", "milestone", "benefit", "fee", "forex", "surcharge"]
    assert result.steady_state == Decimal("9000")


# ---------------------------------------------------------------------------
# value_milestone_grants
# ---------------------------------------------------------------------------

def test_grant_points_valued_via_currency_route():
    # syn_renewal's real 2nd tier payload: grant_points 10,000 synth_points.
    event = ThresholdEvent(
        threshold_key="anniv", tier_index=2, window_months=tuple(range(1, 13)), pooled_spend=Decimal("600000"),
        payload=Payload(type="grant_points", amount=Decimal("10000"), currency="synth_points", condition="on_renewal"),
    )
    total, lines = value_milestone_grants(
        [event], currencies={"synth_points": SYNTH_POINTS}, primary_routes={"synth_points": "transfer"}, voucher_valuations={},
    )
    assert total == Decimal("8000")  # 10,000 * 0.8 (transfer rate)
    assert len(lines) == 1
    assert lines[0].kind == "milestone"


def test_grant_cashback_valued_via_currency_route():
    event = ThresholdEvent(
        threshold_key="cashback_bonus", tier_index=1, window_months=tuple(range(1, 13)), pooled_spend=Decimal("200000"),
        payload=Payload(type="grant_cashback", amount=Decimal("500"), currency="cashback_inr"),
    )
    total, lines = value_milestone_grants([event], currencies={"cashback_inr": CASHBACK_INR}, primary_routes={}, voucher_valuations={})
    assert total == Decimal("500")


def test_grant_voucher_reuses_stage_9s_already_computed_valuation():
    # syn_miles' vch_a.
    event = ThresholdEvent(
        threshold_key="annual_miles", tier_index=1, window_months=tuple(range(1, 13)), pooled_spend=Decimal("500000"),
        payload=Payload(type="grant_voucher", benefit="vch_a"),
    )
    voucher_valuations = {"vch_a": BenefitValuation(benefit_key="vch_a", entitlement_units=None, consumed_units=None, value_rupees=Decimal("7650.000"))}
    total, lines = value_milestone_grants([event], currencies={}, primary_routes={}, voucher_valuations=voucher_valuations)
    assert total == Decimal("7650.000")


def test_grant_entitlement_and_waive_fee_excluded_from_milestone_value():
    entitlement_event = ThresholdEvent(
        threshold_key="q_spend", tier_index=1, window_months=(1, 2, 3), pooled_spend=Decimal("90000"),
        payload=Payload(type="grant_entitlement", benefit="dom_lounge", quantity=4),
    )
    waiver_event = ThresholdEvent(
        threshold_key="waiver", tier_index=1, window_months=tuple(range(1, 13)), pooled_spend=Decimal("480000"),
        payload=Payload(type="waive_fee", fee="annual"),
    )
    total, lines = value_milestone_grants([entitlement_event, waiver_event], currencies={}, primary_routes={}, voucher_valuations={})
    assert total == Decimal("0")
    assert lines == ()


def test_multiple_grants_sum_together():
    points_event = ThresholdEvent(
        threshold_key="t1", tier_index=1, window_months=tuple(range(1, 13)), pooled_spend=Decimal("100000"),
        payload=Payload(type="grant_points", amount=Decimal("10000"), currency="synth_points"),
    )
    voucher_event = ThresholdEvent(
        threshold_key="t2", tier_index=1, window_months=tuple(range(1, 13)), pooled_spend=Decimal("400000"),
        payload=Payload(type="grant_voucher", benefit="vch_a"),
    )
    voucher_valuations = {"vch_a": BenefitValuation(benefit_key="vch_a", entitlement_units=None, consumed_units=None, value_rupees=Decimal("7650"))}
    total, lines = value_milestone_grants(
        [points_event, voucher_event], currencies={"synth_points": SYNTH_POINTS},
        primary_routes={"synth_points": "transfer"}, voucher_valuations=voucher_valuations,
    )
    assert total == Decimal("15650")  # 8000 + 7650
    assert len(lines) == 2
