"""Fourth real-card pipeline validation: SBI Card ELITE. Chosen to exercise
a genuinely NEW construct the first three real cards never touched: a
multi-tier CUMULATIVE grant_points milestone (Bonus Reward Points, four
tiers at Rs.3L/4L/5L/8L annual spend) -- PRIME/CASHBACK/OCTANE's own
thresholds were all single-tier waive_fee. Also re-exercises PRIME's own
two "does not exist for OCTANE" gaps (welcome-gift voucher, two-tier lounge
cap) on a SECOND card, confirming they recur when the underlying feature is
actually present, and a genuinely reduced forex rate (1.99%, not the
standard 3.5%) unique among the four real cards ingested so far.

This card's own sourcing is materially weaker than the first three,
flagged prominently rather than smoothed over:

  - The captured reward_terms PDF's filename says "2016"; two concrete
    signals (no wallet/rent exclusion, absent from the MITC's own per-card
    fee table) suggest it may be stale relative to current policy. Fees
    were sourced from the live product page instead, cross-checked against
    a bank-partnership variant's identical MITC row.
  - The live product page ALSO shows a DIRECT statement-credit route ("4
    Reward Points = Re.1", matching OCTANE's own issuer-stated mechanism)
    that appears NOWHERE in the captured reward_terms PDF -- NOT added to
    this bundle without a citable source, flagged instead (checklist item
    2). This golden prices only via the weaker, estimated-not-issuer-
    stated shop_n_smile_catalog route.

Which of PRIME's four gaps recur (this is the second real card to have
each underlying feature at all, so the first real confirmation that a gap
recurs rather than just failing to apply):

  1. [RECURS, third real card in a row] Multi-category pooled cap
     (`cap_accelerated_monthly` pools dining/departmental_stores/grocery).
  2. [RECURS, second real card WITH the feature] Fee-triggered voucher
     grant (`welcome_gift_voucher`) -- no `ThresholdBasis.measure` exists
     for "fee payment", same as PRIME's own identical benefit.
  3. [RECURS, second real card WITH the feature] Two-tier benefit cap
     (`priority_pass_lounge`, "6/year AND max 2/quarter") -- same shape as
     PRIME's own identical benefit, just different numbers.
  4. [DOES NOT RECUR] Every-route-must-be-priced constraint -- ELITE's
     currency has only ONE declared route, and it IS priced.

Plus: OCTANE's own new gap (no selector negation primitive) does NOT
recur here -- ELITE's fuel exclusion is a plain eligibility.py Exclusion,
the simpler CASHBACK/PRIME-style mechanism, since there's no competing
fuel-earning rule that needs to bypass it.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.accrue import accrue_category_mode
from engine.caps import apply_caps
from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.costs import forex_cost, international_spend_total
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.match import match
from engine.normalise import DEFAULT_CATEGORY_MCC_MAP, AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from engine.thresholds import evaluate_thresholds

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"

_RAW_BUNDLE = json.loads((INGESTION_DIR / "bundle_sbi_elite.json").read_text())
_GOLDEN = json.loads((INGESTION_DIR / "golden_sbi_elite.json").read_text())


def _bundle_and_currencies():
    bundle = bundle_from_dict(_RAW_BUNDLE)
    currencies = currencies_from_dicts(_RAW_BUNDLE["currencies"])
    return bundle, currencies


def _spend_from_annual(spend_annual: dict) -> SpendInput:
    """"category[/channel][~merchant_group][@geography]" -- same convention
    as ingest/publish.py's own _spend_input_from_scenario, kept as a local
    copy for the same reason every prior real-card test file already has one
    (test code shouldn't depend on ingest/)."""
    lines = []
    for raw_key, amount in spend_annual.items():
        rest, _, geography = raw_key.partition("@")
        rest, _, merchant_group = rest.partition("~")
        category, _, channel = rest.partition("/")
        lines.append(CategorySpend(
            category=category, channel=channel or None, annual_amount=Decimal(str(amount)),
            geography=geography or "domestic", merchant_group=merchant_group or None,
        ))
    return SpendInput(category_spend=tuple(lines))


def test_ingestion_bundle_loads_without_crashing():
    bundle, currencies = _bundle_and_currencies()
    assert bundle.card_key == "sbi_card_elite"
    assert bundle.currency_key == "sbi_elite_points"
    assert "sbi_elite_points" in currencies
    assert {r.key for r in bundle.earning_rules} == {"accelerated_10pt", "base_2pt"}
    assert {t.key for t in bundle.thresholds} == {"fee_waiver", "milestone_bonus_points"}
    assert {e.key for e in bundle.exclusions} == {"fuel_exclusion"}
    assert bundle.surcharges == ()  # confirmed absent -- no fuel-earning mechanism to waive a surcharge on


def test_only_one_route_and_it_is_priced_prime_gap_4_does_not_recur():
    _bundle, currencies = _bundle_and_currencies()
    routes = currencies["sbi_elite_points"].routes
    assert len(routes) == 1
    assert routes[0].key == "shop_n_smile_catalog"
    assert routes[0].ratio == Decimal("0.1882")


def test_fuel_excluded_via_plain_eligibility_exclusion_not_selector_enumeration():
    """Confirms ELITE uses the SIMPLER CASHBACK/PRIME-style exclusion
    mechanism, not OCTANE's own enumeration workaround -- no competing
    fuel-earning rule exists here, so a blanket exclusion is correct."""
    bundle, _currencies = _bundle_and_currencies()
    spend = SpendInput(category_spend=(CategorySpend(category="fuel", annual_amount=Decimal("12000")),))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions, category_mcc_map=DEFAULT_CATEGORY_MCC_MAP)
    assert eligible.reward == ()  # excluded before match() ever runs
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert bindings == ()


def test_welcome_gift_voucher_is_not_granted_by_any_threshold():
    """Gap #2 recurring: same as PRIME's own identical benefit."""
    from engine.benefits import value_voucher_benefit

    bundle, _currencies = _bundle_and_currencies()
    benefit = bundle.benefits["welcome_gift_voucher"]
    spend = _spend_from_annual({"grocery": 300000, "ecommerce": 750000})
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions, category_mcc_map=DEFAULT_CATEGORY_MCC_MAP)
    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    assert not any(e.payload.type == "grant_voucher" and e.payload.benefit == "welcome_gift_voucher" for e in threshold_events)
    valuation = value_voucher_benefit(benefit, threshold_events, utilisation=Decimal("1.0"), friction=Decimal("1.0"))
    assert valuation.value_rupees == Decimal("0")
    assert "not_granted" in valuation.flags


def test_multi_category_pooled_cap_gap_recurs_a_third_time():
    """Gap #1: third real card in a row to hit the identical
    engine.caps.apply_caps guard."""
    bundle, _currencies = _bundle_and_currencies()
    # 60,000/mo each -> 6,000 pts/mo each -> pooled 12,000/mo, genuinely
    # ABOVE the 10,000 cap (not merely equal to it, which needs no trim
    # and so wouldn't exercise the multi-category guard at all).
    spend = SpendInput(category_spend=(
        CategorySpend(category="grocery", annual_amount=Decimal("720000")),
        CategorySpend(category="dining", annual_amount=Decimal("720000")),
    ))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions, category_mcc_map=DEFAULT_CATEGORY_MCC_MAP)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")

    with pytest.raises(ValueError, match="multi-category pooled caps aren't supported yet"):
        apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)


def test_all_four_milestone_tiers_fire_cumulatively_at_high_spend():
    """The genuinely new construct this card exercises: a real multi-tier
    cumulative grant_points milestone, first time in this repo's own
    ingestion history (PRIME/CASHBACK/OCTANE's thresholds were all
    single-tier waive_fee)."""
    bundle, _currencies = _bundle_and_currencies()
    spend = _spend_from_annual({"grocery": 300000, "ecommerce": 750000, "ecommerce@international": 60000})
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions, category_mcc_map=DEFAULT_CATEGORY_MCC_MAP)
    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    milestone_events = [e for e in threshold_events if e.threshold_key == "milestone_bonus_points"]
    assert len(milestone_events) == 4
    assert sorted(e.payload.amount for e in milestone_events) == [
        Decimal("10000"), Decimal("10000"), Decimal("15000"), Decimal("15000"),
    ]
    fee_events = [e for e in threshold_events if e.threshold_key == "fee_waiver"]
    assert len(fee_events) == 1 and fee_events[0].payload.type == "waive_fee"


def test_reduced_forex_rate_is_1_99_percent_not_the_standard_3_5():
    """The distinguishing fact this card's own MITC clause states
    explicitly -- confirms it's wired through, not silently defaulted."""
    bundle, _currencies = _bundle_and_currencies()
    assert bundle.forex_markup == Decimal("0.0199")

    spend = SpendInput(category_spend=(CategorySpend(category="ecommerce", geography="international", annual_amount=Decimal("60000")),))
    normalised = normalise(spend, AssumptionsSnapshot())
    intl_total = international_spend_total(normalised.segments)
    cost = forex_cost(intl_total, bundle.forex_markup)
    assert cost == Decimal("1408.9200")  # 0.0199 * 1.18 * 60,000


def test_scenario_a_grocery_ecommerce_milestones():
    """The proposed golden, stage-by-stage AND via the full evaluate_card
    orchestrator, verified against golden_sbi_elite.json's own
    _hand_computation. PROPOSED, not yet independently confirmed by
    Satya -- see that file's own opening _hand_computation lines."""
    scenario = _GOLDEN["scenario_A_grocery_ecommerce_milestones"]
    expected = scenario["expected"]
    spend = _spend_from_annual(scenario["spend_annual"])

    bundle, currencies = _bundle_and_currencies()
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions, category_mcc_map=DEFAULT_CATEGORY_MCC_MAP)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"accelerated_10pt", "base_2pt"}

    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    final = apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)
    assert not any("rounding_estimated" in r.flags for r in final)
    assert not any("cap_overflow" in r.flags for r in final)  # 2,500/mo well under the 10,000 cap

    grocery_points = sum((r.reward for r in final if r.rule_key == "accelerated_10pt"), Decimal("0"))
    ecommerce_points = sum((r.reward for r in final if r.rule_key == "base_2pt"), Decimal("0"))
    assert grocery_points == Decimal("30000")     # 2,500/mo x 12
    assert ecommerce_points == Decimal("16200")   # (1,250 + 100)/mo x 12 -- domestic + international ecommerce
    gross_points_earned = grocery_points + ecommerce_points
    assert gross_points_earned == Decimal(str(expected["_gross_points_earned"]))

    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    milestone_points = sum(
        (e.payload.amount for e in threshold_events if e.threshold_key == "milestone_bonus_points"), Decimal("0"),
    )
    assert milestone_points == Decimal(str(expected["_milestone_points_earned"]))
    assert any(e.payload.type == "waive_fee" for e in threshold_events)

    from engine.costs import compute_fees
    fees = compute_fees(bundle.joining_fee, bundle.annual_fee, threshold_events)
    assert fees.waived == expected["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(expected["fee_paid"]))
    assert fees.year1_fee == Decimal(str(expected["_fee_year1"]))

    # Cross-check: the full evaluate_card() orchestrator must agree exactly
    # with the stage-by-stage numbers above and with the golden's own hand
    # computation -- same discipline as every prior real-card golden test.
    golden_assumptions = scenario["assumptions"]
    assumptions = EvaluateAssumptions(
        primary_routes=golden_assumptions["primary_route"],
        benefit_need={k: Decimal(str(v)) for k, v in golden_assumptions["benefit_need"].items()},
        benefit_unit_value={k: Decimal(str(v)) for k, v in golden_assumptions["benefit_unit_value"].items()},
    )
    result = evaluate_card(bundle, currencies, spend, assumptions)
    assert result.gross_reward_value == Decimal(str(expected["gross_reward_value"]))
    assert result.milestone_value == Decimal(str(expected["milestone_value"]))
    assert result.waiver_achieved == expected["waiver_achieved"]
    assert result.fee_steady == Decimal(str(expected["fee_paid"]))
    assert result.nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert result.nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
