"""Third real-card pipeline validation: BPCL SBI Card OCTANE. Deliberately
chosen to stress dimensions CASHBACK/PRIME didn't: a fuel-surcharge waiver
that actually contributes value (fuel earns rewards here, unlike CASHBACK's
reward-excluded fuel), accelerated-category earning as the card's core
mechanic, and a reward-points currency that re-tests PRIME's valuation
machinery. Confirms the engine computes what IS sourced correctly against a
hand-drafted real ingestion bundle (`compute/ingestion/bundle_bpcl_octane.
json` + `golden_bpcl_octane.json`), NOT a publish -- the card_version stays
conceptually `draft` throughout.

Which of PRIME's four gaps recur (full reasoning in the bundle's own
`_review_checklist` and docs/DECISIONS.md):

  1. [RECURS] Multi-category pooled cap (`cap_category_accelerator_monthly`
     pools dining/departmental_stores/grocery/movies) -- the same
     `engine.caps.apply_caps` "multi-category pooled caps aren't supported
     yet" guard (#11/#32) fires identically. Confirms this is a structural
     gap, not PRIME-specific.
  2. [DOES NOT RECUR] PRIME's fee-triggered voucher grant (no
     `ThresholdBasis.measure` for fee payment) -- OCTANE has no
     welcome-gift-with-value benefit in its sourced T&C at all.
  3. [DOES NOT RECUR] PRIME's two-tier benefit cap -- OCTANE has no
     countable benefit (lounge or otherwise) in its sourced T&C.
  4. [DOES NOT RECUR] PRIME's "every route must be priced" constraint on
     `value_currency` -- BOTH of OCTANE's routes have a real ratio (one
     issuer-stated, one estimated); there's no unpriced route to trip it.

Plus one genuinely NEW gap PRIME never surfaced: no selector negation
primitive exists, so "base rate on everything except fuel" is only
expressible via positive enumeration of every other known category (see
`base_1x`'s own bundle `_note`).
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.accrue import accrue_category_mode
from engine.caps import apply_caps
from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.costs import surcharge_cost
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.match import match
from engine.normalise import AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from engine.thresholds import evaluate_thresholds

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"

_RAW_BUNDLE = json.loads((INGESTION_DIR / "bundle_bpcl_octane.json").read_text())
_GOLDEN = json.loads((INGESTION_DIR / "golden_bpcl_octane.json").read_text())


def _bundle_and_currencies():
    bundle = bundle_from_dict(_RAW_BUNDLE)
    currencies = currencies_from_dicts(_RAW_BUNDLE["currencies"])
    return bundle, currencies


def _spend_from_annual(spend_annual: dict) -> SpendInput:
    """"category[~merchant_group]" key convention, matching this golden's
    own "fuel~bpcl" key -- a local copy of the same parsing convention
    tests/test_goldens.py and ingest/publish.py's own _spend_input_from_
    scenario already use, kept local since neither is production-importable."""
    lines = []
    for key, amount in spend_annual.items():
        category, _, merchant_group = key.partition("~")
        lines.append(CategorySpend(category=category, merchant_group=merchant_group or None, annual_amount=Decimal(str(amount))))
    return SpendInput(category_spend=tuple(lines))


def test_ingestion_bundle_loads_without_crashing():
    bundle, currencies = _bundle_and_currencies()
    assert bundle.card_key == "bpcl_octane_sbi"
    assert bundle.currency_key == "bpcl_octane_points"
    assert "bpcl_octane_points" in currencies
    assert {r.key for r in bundle.earning_rules} == {"bpcl_fuel_25x", "category_accelerator_10x", "base_1x"}
    assert len(bundle.thresholds) == 1
    assert {e.key for e in bundle.exclusions} == {"wallet_exclusion", "rent_exclusion"}
    assert len(bundle.surcharges) == 1


def test_both_routes_are_priced_prime_gap_4_does_not_recur():
    """PRIME's gap #4 (value_currency requires every route on a currency to
    have a ratio) doesn't apply here -- confirms OCTANE's currency can be
    valued via EITHER route directly, no narrowed single-route view needed
    (unlike PRIME's own test file's _valuation_currencies workaround)."""
    from engine.valuation import value_currency

    _bundle, currencies = _bundle_and_currencies()
    routes_by_key = {r.key: r for r in currencies["bpcl_octane_points"].routes}
    assert routes_by_key["bpcl_redemption"].ratio == Decimal("0.25")
    assert routes_by_key["shop_n_smile_catalog"].ratio == Decimal("0.1751")

    for route_key in ("bpcl_redemption", "shop_n_smile_catalog"):
        valuation = value_currency(currencies["bpcl_octane_points"], points=Decimal("38400"), primary_route_key=route_key)
        assert valuation.v_exp_rupees > 0  # both resolve without raising


def test_bpcl_fuel_matches_only_the_accelerator_not_base():
    """Confirms the 'except fuel' enumeration workaround (checklist item 3,
    the new gap) actually works for BPCL fuel specifically -- it must match
    ONLY bpcl_fuel_25x, never base_1x too (which would double-count)."""
    bundle, _currencies = _bundle_and_currencies()
    spend = SpendInput(category_spend=(CategorySpend(category="fuel", merchant_group="bpcl", annual_amount=Decimal("12000")),))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"bpcl_fuel_25x"}


def test_non_bpcl_fuel_earns_nothing_not_even_the_base_rate():
    """The real point of the enumeration workaround: fuel spend at a
    DIFFERENT (non-BPCL) merchant must match NEITHER bpcl_fuel_25x (wrong
    merchant_group) NOR base_1x (fuel omitted from its selector) -- earns
    exactly Rs0, matching Sec 11.4(a) bullet 4's 'except fuel'."""
    bundle, _currencies = _bundle_and_currencies()
    spend = SpendInput(category_spend=(CategorySpend(category="fuel", merchant_group="other_brand", annual_amount=Decimal("12000")),))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert bindings == ()  # no rule matches -- zero reward, not a crash and not an incorrect base-rate credit


def test_multi_category_pooled_cap_gap_recurs_same_as_prime():
    """Gap #1: demonstrates the SAME engine.caps.apply_caps guard PRIME's
    own golden already proved fires for its identically-shaped cap --
    confirms this is a recurring structural limitation, not PRIME-specific."""
    bundle, _currencies = _bundle_and_currencies()
    spend = SpendInput(category_spend=(
        CategorySpend(category="grocery", annual_amount=Decimal("600000")),   # 50,000/mo -> 5,000 pts/mo
        CategorySpend(category="dining", annual_amount=Decimal("600000")),    # 50,000/mo -> 5,000 pts/mo
    ))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")

    with pytest.raises(ValueError, match="multi-category pooled caps aren't supported yet"):
        apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)


def test_fuel_surcharge_waiver_actually_contributes_value_unlike_cashback():
    """The dimension CASHBACK never exercised (its fuel was reward-excluded,
    so this waiver was its ENTIRE fuel-category NACV contribution) -- here,
    the SAME BPCL fuel spend both earns 25X points AND has its surcharge
    waived, independently, with no exclusion-mask conflict between them."""
    bundle, _currencies = _bundle_and_currencies()
    spend = SpendInput(category_spend=(CategorySpend(category="fuel", merchant_group="bpcl", annual_amount=Decimal("96000")),))  # Rs.8,000/month
    normalised = normalise(spend, AssumptionsSnapshot())

    result = surcharge_cost(normalised.segments, bundle.surcharges)
    # gross/month = 0.01*1.18*8000 = 94.40; waived_base/month = min(0.01*8000, 100) = 80.00 (under the Rs100 cap);
    # waived/month = 80.00*1.18 = 94.40 -> net/month = 0 -> annual = Rs0. Full waiver, not a residual.
    assert result.total == Decimal("0")
    assert "txn_threshold_unenforced" in result.flags  # the Rs.4,000 txn ceiling is accepted, not enforced

    # And the SAME fuel spend also earns points -- no exclusion zeroed it out
    # (the CASHBACK-style conflict this card structurally avoids, per
    # bpcl_fuel_25x's own bundle _note).
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"bpcl_fuel_25x"}
    accrued = accrue_category_mode(bindings, bundle.accruals)
    assert sum((r.reward for r in accrued), Decimal("0")) == Decimal("24000")  # 2,000 pts/mo x 12, uncapped


def test_fuel_surcharge_waiver_caps_at_stated_amount_leaving_a_residual():
    """Confirms the cap DOES bind (and leaves a residual, unwaived cost) at
    a high enough fuel spend -- proves the waiver isn't unconditionally
    Rs0, just fully-covering at the proposed golden's own Rs.8,000/month."""
    bundle, _currencies = _bundle_and_currencies()
    spend = SpendInput(category_spend=(CategorySpend(category="fuel", merchant_group="bpcl", annual_amount=Decimal("240000")),))  # Rs.20,000/month
    normalised = normalise(spend, AssumptionsSnapshot())
    result = surcharge_cost(normalised.segments, bundle.surcharges)
    # gross/month = 0.01*1.18*20000 = 236.00; waived_base/month = min(0.01*20000, 100) = 100.00 (cap BINDS);
    # waived/month = 100.00*1.18 = 118.00 -> net/month = 236.00-118.00 = 118.00 -> annual = 118.00*12 = Rs.1,416.00.
    assert result.total == Decimal("1416.00")


def test_scenario_a_bpcl_fuel_grocery_ecommerce():
    """The proposed golden, stage-by-stage AND via the full evaluate_card
    orchestrator, verified against golden_bpcl_octane.json's own
    _hand_computation. PROPOSED, not yet independently confirmed by Satya --
    see that file's own opening _hand_computation line."""
    scenario = _GOLDEN["scenario_A_bpcl_fuel_grocery_ecommerce"]
    expected = scenario["expected"]
    spend = _spend_from_annual(scenario["spend_annual"])

    bundle, currencies = _bundle_and_currencies()
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"bpcl_fuel_25x", "category_accelerator_10x", "base_1x"}

    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    final = apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)
    assert not any("rounding_estimated" in r.flags for r in final)  # all three ticket sizes -> zero floor loss
    assert not any("cap_overflow" in r.flags for r in final)  # both caps well under their own limits

    fuel_points = sum((r.reward for r in final if r.rule_key == "bpcl_fuel_25x"), Decimal("0"))
    grocery_points = sum((r.reward for r in final if r.rule_key == "category_accelerator_10x"), Decimal("0"))
    ecommerce_points = sum((r.reward for r in final if r.rule_key == "base_1x"), Decimal("0"))
    assert fuel_points == Decimal("24000")       # 2,000/mo x 12
    assert grocery_points == Decimal("12000")    # 1,000/mo x 12
    assert ecommerce_points == Decimal("2400")   # 200/mo x 12
    gross_points_earned = fuel_points + grocery_points + ecommerce_points
    assert gross_points_earned == Decimal(str(expected["_gross_points_earned"]))

    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    assert len(threshold_events) == 1 and threshold_events[0].payload.type == "waive_fee"

    from engine.costs import compute_fees
    fees = compute_fees(bundle.joining_fee, bundle.annual_fee, threshold_events)
    assert fees.waived == expected["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(expected["fee_paid"]))
    assert fees.year1_fee == Decimal(str(expected["_fee_year1"]))

    surcharge_result = surcharge_cost(normalised.segments, bundle.surcharges)
    assert surcharge_result.total == Decimal("0")  # fully waived at this spend level

    # Cross-check: the full evaluate_card() orchestrator must agree exactly
    # with the stage-by-stage numbers above and with the golden's own hand
    # computation -- same discipline as CASHBACK/PRIME's own golden tests.
    golden_assumptions = scenario["assumptions"]
    assumptions = EvaluateAssumptions(primary_routes=golden_assumptions["primary_route"])
    result = evaluate_card(bundle, currencies, spend, assumptions)
    assert result.gross_reward_value == Decimal(str(expected["gross_reward_value"]))
    assert result.waiver_achieved == expected["waiver_achieved"]
    assert result.fee_steady == Decimal(str(expected["fee_paid"]))
    assert result.nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert result.nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
