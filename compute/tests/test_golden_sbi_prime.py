"""Second real-card pipeline validation: SBI Card PRIME. Pipeline-generalisation
test per Satya's own framing -- CASHBACK SBI (Phase 5) was a direct-rupee
cashback card; PRIME is the first REAL points card, exercising reward-currency
route valuation, milestones, and vouchers CASHBACK never touched. Confirms the
engine computes what IS sourced correctly against a hand-drafted real
ingestion bundle (`compute/ingestion/bundle_sbi_prime.json` + `golden_sbi_
prime.json`), NOT a publish -- the card_version stays conceptually `draft`
throughout (Part I SS I.4/I.8's publish gate is untouched by this file).

This card surfaces THREE genuine engine gaps (full reasoning + citations live
in the bundle's own `_review_checklist` and docs/DECISIONS.md's PRIME entry),
one of which is now resolved differently than originally expected:

  1. [RESOLVED for valuation, via a different route than first assumed] No
     source states a fixed rupee-per-point value for the `statement_credit`
     route -- confirmed genuinely absent, not just hard to find (SBI's own
     FAQ: "reserves the right to decide the Reward points required... for
     each segment of credit cards"). `statement_credit` STAYS unpriced.
     Instead, `voucher_catalog` (a second route, `route_type="voucher"`)
     prices rewards via an empirically-derived ratio from `ingest.reward_
     catalog_ratio` -- the mean of 100 catalog items actually eligible for
     `sbi-card-prime`, NOT the two initially-proposed anchor figures (Titan/
     MakeMyTrip), which turned out to be scoped to other card segments once
     traced back to their own eligibility fields. This is an ASSUMPTION-
     REGISTRY default, not a T&C-cited fact -- see the route's own `_note`.
  2. `cap_accelerated_monthly` pools FOUR distinct categories into one cap --
     `engine.caps.apply_caps` raises when more than one of them has spend in
     the same calendar month while the cap needs trimming (docs/DECISIONS.md
     #11/#32's pre-existing "multi-category pooled caps aren't supported yet"
     deferral, demonstrated concretely below for the first time against a
     real card).
  3. `welcome_gift_voucher` is gated on fee PAYMENT, not spend -- no
     `ThresholdBasis.measure` exists for that, so no threshold in the bundle
     grants it; `value_voucher_benefit` correctly reports 0/`not_granted`.
  4. [Found while wiring gap #1's fix] `engine.valuation.value_currency`
     validates EVERY route on a currency, not just the one being priced --
     `for route in currency.routes: _validate_route(route)` runs
     unconditionally, so a currency with even ONE still-unpriced route (our
     `statement_credit`) can never be valued through ANY other route either,
     including a fully-priced `voucher_catalog`. The bundle's own
     `currencies[]` keeps BOTH routes declared (that's the honest, sourced
     fact -- PRIME really does offer both), but the tests below value
     through a `_valuation_currency()` helper that constructs a
     single-route view for the parts of Stage 8 that actually run -- same
     "load real, adapt for what's safe to evaluate" pattern CASHBACK's own
     `_adapt_ingestion_bundle` used for its `exclusions[]`, not a new
     workaround invented for PRIME specifically.

None of these are silently worked around -- see the tests below.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.accrue import accrue_category_mode
from engine.benefits import value_voucher_benefit
from engine.caps import apply_caps
from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.match import match
from engine.normalise import AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from engine.thresholds import evaluate_thresholds
from engine.valuation import RewardCurrency, value_currency

_PRIMARY_ROUTES = {"sbi_prime_points": "voucher_catalog"}

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"

_RAW_BUNDLE = json.loads((INGESTION_DIR / "bundle_sbi_prime.json").read_text())
_GOLDEN = json.loads((INGESTION_DIR / "golden_sbi_prime.json").read_text())


def _bundle_and_currencies():
    bundle = bundle_from_dict(_RAW_BUNDLE)
    currencies = currencies_from_dicts(_RAW_BUNDLE["currencies"])
    return bundle, currencies


def _spend_from_annual(spend_annual: dict) -> SpendInput:
    lines = [CategorySpend(category=cat, annual_amount=Decimal(str(amt))) for cat, amt in spend_annual.items()]
    return SpendInput(category_spend=tuple(lines))


def _valuation_currencies(currencies: dict) -> dict:
    """Gap #4 (see module docstring): value_currency validates every route
    on a currency, so a still-unpriced statement_credit would block pricing
    voucher_catalog too. Single-route view, priced route only -- the bundle
    ITSELF keeps both routes declared; only this test-side valuation view
    narrows to what's actually evaluable today."""
    full = currencies["sbi_prime_points"]
    voucher_only = tuple(r for r in full.routes if r.route_type == "voucher")
    return {**currencies, "sbi_prime_points": RewardCurrency(key=full.key, routes=voucher_only)}


def test_ingestion_bundle_loads_without_crashing():
    bundle, currencies = _bundle_and_currencies()
    assert bundle.card_key == "prime_sbi"
    assert bundle.currency_key == "sbi_prime_points"
    assert "sbi_prime_points" in currencies
    assert {r.key for r in bundle.earning_rules} == {"base_2pt", "accelerated_10pt"}
    assert len(bundle.thresholds) == 1
    assert len(bundle.exclusions) == 2
    assert bundle.surcharges == ()  # confirmed absent for PRIME, not an oversight -- see bundle notes


def test_statement_credit_route_ratio_is_genuinely_unset_not_fabricated():
    """Confirms gap #1's statement_credit half is still real at the dataclass
    level: the route loaded exactly as declared (no ratio), and pricing it
    raises rather than silently defaulting to some invented number."""
    _bundle, currencies = _bundle_and_currencies()
    routes_by_type = {r.route_type: r for r in currencies["sbi_prime_points"].routes}
    assert set(routes_by_type) == {"statement_credit", "voucher"}

    statement_credit = routes_by_type["statement_credit"]
    assert statement_credit.ratio is None  # NOT 0, NOT a guessed value -- genuinely absent
    with pytest.raises(ValueError, match="require ratio"):
        value_currency(currencies["sbi_prime_points"], points=Decimal("31200"), primary_route_key="statement_credit")


def test_voucher_catalog_route_prices_at_the_empirically_derived_ratio():
    """The other half of gap #1's resolution: voucher_catalog DOES have a
    ratio (an assumption-registry default, not a T&C fact -- see the route's
    own bundle _note), and pricing through it works."""
    _bundle, currencies = _bundle_and_currencies()
    voucher_catalog = next(r for r in currencies["sbi_prime_points"].routes if r.route_type == "voucher")
    assert voucher_catalog.ratio == Decimal("0.1827")

    valuation_currencies = _valuation_currencies(currencies)
    valuation = value_currency(valuation_currencies["sbi_prime_points"], points=Decimal("31200"), primary_route_key="voucher_catalog")
    assert valuation.v_exp_rupees == Decimal("31200") * Decimal("0.1827")
    assert valuation.v_exp_rupees == Decimal("5700.2400")


def test_multi_category_pooled_cap_gap_is_real():
    """Demonstrates gap #2 concretely: a spend profile touching TWO of the
    four accelerated categories in the SAME calendar month, high enough to
    need trimming, makes engine.caps.apply_caps raise -- proving this is a
    live limitation for a real card, not a hypothetical one. (The proposed
    golden below deliberately avoids this by only spending in one accelerated
    category, so IT remains computable.)"""
    bundle, _currencies = _bundle_and_currencies()

    # Both grocery and dining active every month, well past the combined
    # Rs.7,500 RP/month cap (grocery alone: 20,000*10% = 2,000/mo; dining
    # 40,000*10% = 4,000/mo -- combined 6,000/mo, still under cap on its own,
    # so push higher to force trimming).
    spend = SpendInput(category_spend=(
        CategorySpend(category="grocery", annual_amount=Decimal("360000")),   # 30,000/mo -> 3,000 pts/mo
        CategorySpend(category="dining", annual_amount=Decimal("600000")),    # 50,000/mo -> 5,000 pts/mo
    ))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")

    with pytest.raises(ValueError, match="multi-category pooled caps aren't supported yet"):
        apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)


def test_welcome_gift_voucher_is_not_granted_by_any_threshold():
    """Confirms gap #3: the benefit is declared (real, sourced, face_value
    3000) but genuinely un-grantable through the current Threshold vocabulary
    -- reports 0/not_granted honestly rather than crashing or fabricating."""
    bundle, _currencies = _bundle_and_currencies()
    benefit = bundle.benefits["welcome_gift_voucher"]

    spend = _spend_from_annual({"grocery": 240000, "ecommerce": 360000})
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    assert not any(e.payload.type == "grant_voucher" and e.payload.benefit == "welcome_gift_voucher" for e in threshold_events)

    valuation = value_voucher_benefit(benefit, threshold_events, utilisation=Decimal("1.0"), friction=Decimal("1.0"))
    assert valuation.value_rupees == Decimal("0")
    assert "not_granted" in valuation.flags


def test_scenario_a_steady_state_points_and_fee_waiver():
    """The proposed golden, stage-by-stage: points earned, fee waiver, fees,
    AND (now that voucher_catalog resolves gap #1) the full rupee valuation
    and NACV, each verified against golden_sbi_prime.json's own
    `_hand_computation` independently of engine internals."""
    scenario = _GOLDEN["scenario_A_steady_state_points_and_fee"]
    expected = scenario["expected"]
    spend = _spend_from_annual(scenario["spend_annual"])

    bundle, currencies = _bundle_and_currencies()
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"base_2pt", "accelerated_10pt"}

    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    final = apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)
    assert not any("rounding_estimated" in r.flags for r in final)  # grocery/ecommerce ticket sizes -> zero floor loss
    assert not any("cap_overflow" in r.flags for r in final)  # 2,000/mo well under the 7,500 cap -- never binds

    grocery_points = sum((r.reward for r in final if r.rule_key == "accelerated_10pt"), Decimal("0"))
    ecommerce_points = sum((r.reward for r in final if r.rule_key == "base_2pt"), Decimal("0"))
    assert grocery_points == Decimal("24000")   # 2,000/mo x 12
    assert ecommerce_points == Decimal("7200")  # 600/mo x 12
    gross_points_earned = grocery_points + ecommerce_points
    assert gross_points_earned == Decimal(str(expected["gross_points_earned"]))

    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    assert len(threshold_events) == 1 and threshold_events[0].payload.type == "waive_fee"

    from engine.costs import compute_fees
    fees = compute_fees(bundle.joining_fee, bundle.annual_fee, threshold_events)
    assert fees.waived == expected["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(expected["fee_paid"]))
    assert fees.year1_fee == Decimal(str(expected["fee_year1"]))

    # Valuation (Stage 8), via voucher_catalog -- the piece that used to be blocked.
    valuation_currencies = _valuation_currencies(currencies)
    valuation = value_currency(valuation_currencies["sbi_prime_points"], points=gross_points_earned, primary_route_key="voucher_catalog")
    assert valuation.v_exp_rupees == Decimal(str(expected["gross_reward_value"]))

    # Cross-check: the full evaluate_card() orchestrator must agree exactly
    # with the stage-by-stage numbers above, the golden's own hand
    # computation, AND (crucially) `ingest publish`'s own scenario runner --
    # same discipline as CASHBACK's own golden test, now mirroring the
    # golden's real `assumptions` block exactly (rather than an alternate
    # path that merely produced the same number) after `ingest publish`
    # itself caught a real mismatch: this file used to drop
    # priority_pass_lounge from the bundle entirely, but publish.py has no
    # such mechanism and calls evaluate_card on the FULL DB-loaded bundle --
    # benefit_need=0/benefit_unit_value=0 (a scenario choice, not a claim
    # about the real 4/year entitlement) is what actually makes this
    # publishable, so the test now exercises that same path. Uses the
    # narrowed valuation_currencies (gap #4) since evaluate_card's own Stage
    # 8 call would otherwise hit the same still-unpriced statement_credit route.
    golden_assumptions = _GOLDEN["scenario_A_steady_state_points_and_fee"]["assumptions"]
    assumptions = EvaluateAssumptions(
        primary_routes=golden_assumptions["primary_route"],
        benefit_need={k: Decimal(str(v)) for k, v in golden_assumptions["benefit_need"].items()},
        benefit_unit_value={k: Decimal(str(v)) for k, v in golden_assumptions["benefit_unit_value"].items()},
    )
    result = evaluate_card(bundle, valuation_currencies, spend, assumptions)
    assert result.gross_reward_value == Decimal(str(expected["gross_reward_value"]))
    assert result.waiver_achieved == expected["waiver_achieved"]
    assert result.fee_steady == Decimal(str(expected["fee_paid"]))
    assert result.nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert result.nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
