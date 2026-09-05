"""Batch ingestion validation: PRIME-family co-brand variants (Task per Satya,
2026-09-05). Five cards -- Bank of Maharashtra, PSB, UCO Bank, City Union Bank,
and Karnataka Bank SBI Card PRIME -- chosen deliberately as the EASIEST batch
to prove the batch-ingestion process on: all five are co-brand variants of the
already-ingested, already-verified base SBI Card PRIME (bundle_sbi_prime.json,
docs/DECISIONS.md #150-153), same logic as validating single-card ingestion on
CASHBACK first before harder cards.

Per-card verification (NOT assumed from the family framing) confirmed, via
direct text comparison of each card's own independently-captured booklet and a
visual (not OCR-garbled) read of the MITC's own fee table, that ALL FIVE are:
  - Reward-mechanics-identical to base PRIME: base 2pt/Rs.100, accelerated
    10pt/Rs.100 on dining/departmental_stores/grocery/movies (same MCCs),
    7,500pt/calendar-month pooled cap, fuel exclusion (same MCCs), welcome
    gift Rs.3,000 (fee-gated), Priority Pass 4/year international (2/quarter
    sub-cap). Bank of Maharashtra and PSB share the exact same 29pp e-kit
    booklet as base PRIME (byte-identical Section 11). UCO/City Union
    Bank/Karnataka Bank each have their OWN distinct 51pp "banking-ekit"
    booklet -- verified independently, not assumed from the shared 51pp page
    count across the three -- and are ALSO reward-mechanics-identical, with
    one real (but immaterial) documentation-structure difference: their own
    booklets state the e-wallet exclusion by cross-reference to the generic
    Shop-and-Smile Rewards Program T&Cs page rather than in-document (base
    PRIME/BOM/PSB state it directly) -- confirmed present on that page too
    (fetched and read), not fabricated from the family assumption.
  - Fee/waiver/forex-identical to base PRIME: Rs.2,999/Rs.2,999, waived at
    Rs.3,00,000 annual spend, forex 3.5% (none of the five appear on the
    MITC's own forex exception list, which names only 'Prime NRI secured').

CONCLUSION (Task F's own required statement): the "PRIME family = same
structure, different fees" hypothesis this batch set out to test did NOT
fully hold -- it undershot reality. Every one of these five co-brand cards is
identical to base PRIME not just in reward STRUCTURE but in the specific FEE
NUMBERS too. No structural divergence was found in any of the five. Because
of this, every card's own proposed golden (mirroring base PRIME's own
golden_sbi_prime.json scenario exactly) produces a BYTE-IDENTICAL NACV to base
PRIME's own (Rs.5,700.24 steady-state / Rs.2,161.42 year-1) -- verified per
card below, not assumed from the family framing.

Every engine gap this family surfaces is INHERITED from base PRIME (docs/
DECISIONS.md #150), not new: the multi-category pooled-cap gap (#11/#32),
welcome_gift_voucher's fee-payment-gating gap, priority_pass_lounge's
2-per-quarter sub-cap approximation, and value_currency's "every route on a
currency must be priced" validation (worked around via the same
`_valuation_currencies()` narrowing helper PRIME's own test file established).
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

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"

# (card_key, bundle_filename, golden_filename) -- appended to incrementally,
# one card at a time, per Task C's token-discipline instruction (draft one
# card, commit, release its booklet from context, move to the next).
CARDS = [
    ("prime_bom", "bundle_sbi_prime_bom.json", "golden_sbi_prime_bom.json"),
    ("prime_psb", "bundle_sbi_prime_psb.json", "golden_sbi_prime_psb.json"),
    ("prime_uco", "bundle_sbi_prime_uco.json", "golden_sbi_prime_uco.json"),
    ("prime_cub", "bundle_sbi_prime_cub.json", "golden_sbi_prime_cub.json"),
    ("prime_ktb", "bundle_sbi_prime_ktb.json", "golden_sbi_prime_ktb.json"),
]


def _load(bundle_file: str, golden_file: str):
    raw_bundle = json.loads((INGESTION_DIR / bundle_file).read_text())
    golden = json.loads((INGESTION_DIR / golden_file).read_text())
    bundle = bundle_from_dict(raw_bundle)
    currencies = currencies_from_dicts(raw_bundle["currencies"])
    return raw_bundle, bundle, currencies, golden


def _spend_from_annual(spend_annual: dict) -> SpendInput:
    lines = [CategorySpend(category=cat, annual_amount=Decimal(str(amt))) for cat, amt in spend_annual.items()]
    return SpendInput(category_spend=tuple(lines))


def _valuation_currencies(currencies: dict) -> dict:
    """Same gap #4 workaround as PRIME's own test file: value_currency
    validates EVERY route on a currency, so the still-unpriced
    statement_credit route would block pricing voucher_catalog too. This
    narrows to the priced route only, for the parts of Stage 8 that are
    actually evaluable today -- the bundle itself keeps both routes declared."""
    full = currencies["sbi_prime_points"]
    voucher_only = tuple(r for r in full.routes if r.route_type == "voucher")
    return {**currencies, "sbi_prime_points": RewardCurrency(key=full.key, routes=voucher_only)}


@pytest.mark.parametrize("card_key,bundle_file,golden_file", CARDS)
def test_bundle_loads_and_mirrors_prime_structure(card_key, bundle_file, golden_file):
    _raw, bundle, currencies, _golden = _load(bundle_file, golden_file)
    assert bundle.card_key == card_key
    assert bundle.currency_key == "sbi_prime_points"
    assert "sbi_prime_points" in currencies
    assert {r.key for r in bundle.earning_rules} == {"base_2pt", "accelerated_10pt"}
    assert len(bundle.thresholds) == 1
    assert len(bundle.exclusions) == 2
    assert bundle.surcharges == ()
    assert bundle.joining_fee == Decimal("2999")
    assert bundle.annual_fee == Decimal("2999")
    assert bundle.forex_markup == Decimal("0.035")


@pytest.mark.parametrize("card_key,bundle_file,golden_file", CARDS)
def test_voucher_catalog_ratio_reused_from_base_prime(card_key, bundle_file, golden_file):
    _raw, _bundle, currencies, _golden = _load(bundle_file, golden_file)
    voucher_catalog = next(r for r in currencies["sbi_prime_points"].routes if r.route_type == "voucher")
    assert voucher_catalog.ratio == Decimal("0.1827")  # same ratio as base PRIME, reused not re-derived


@pytest.mark.parametrize("card_key,bundle_file,golden_file", CARDS)
def test_multi_category_pooled_cap_gap_recurs(card_key, bundle_file, golden_file):
    """Same inherited engine gap as base PRIME (docs/DECISIONS.md #150 gap #2)
    -- demonstrated per card, not assumed to recur just because the family
    shares the same cap structure."""
    _raw, bundle, _currencies, _golden = _load(bundle_file, golden_file)
    spend = SpendInput(category_spend=(
        CategorySpend(category="grocery", annual_amount=Decimal("360000")),
        CategorySpend(category="dining", annual_amount=Decimal("600000")),
    ))
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")

    with pytest.raises(ValueError, match="multi-category pooled caps aren't supported yet"):
        apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)


@pytest.mark.parametrize("card_key,bundle_file,golden_file", CARDS)
def test_welcome_gift_voucher_not_granted_by_any_threshold(card_key, bundle_file, golden_file):
    _raw, bundle, _currencies, golden = _load(bundle_file, golden_file)
    benefit = bundle.benefits["welcome_gift_voucher"]
    scenario = golden["scenario_A_steady_state_points_and_fee"]

    spend = _spend_from_annual(scenario["spend_annual"])
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    assert not any(e.payload.type == "grant_voucher" and e.payload.benefit == "welcome_gift_voucher" for e in threshold_events)

    valuation = value_voucher_benefit(benefit, threshold_events, utilisation=Decimal("1.0"), friction=Decimal("1.0"))
    assert valuation.value_rupees == Decimal("0")
    assert "not_granted" in valuation.flags


@pytest.mark.parametrize("card_key,bundle_file,golden_file", CARDS)
def test_scenario_a_matches_base_prime_nacv_exactly(card_key, bundle_file, golden_file):
    """The proposed golden, stage-by-stage AND via the full evaluate_card
    orchestrator -- verified to equal base PRIME's own golden numbers exactly,
    per this card's own bundle/golden _hand_computation reasoning for why
    that's expected (identical mechanics+fees, not coincidence)."""
    _raw, bundle, currencies, golden = _load(bundle_file, golden_file)
    scenario = golden["scenario_A_steady_state_points_and_fee"]
    expected = scenario["expected"]
    spend = _spend_from_annual(scenario["spend_annual"])

    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"base_2pt", "accelerated_10pt"}

    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    final = apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)
    assert not any("rounding_estimated" in r.flags for r in final)
    assert not any("cap_overflow" in r.flags for r in final)

    grocery_points = sum((r.reward for r in final if r.rule_key == "accelerated_10pt"), Decimal("0"))
    ecommerce_points = sum((r.reward for r in final if r.rule_key == "base_2pt"), Decimal("0"))
    assert grocery_points == Decimal("24000")
    assert ecommerce_points == Decimal("7200")
    gross_points_earned = grocery_points + ecommerce_points
    assert gross_points_earned == Decimal(str(expected["_gross_points_earned"]))

    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    assert len(threshold_events) == 1 and threshold_events[0].payload.type == "waive_fee"

    from engine.costs import compute_fees
    fees = compute_fees(bundle.joining_fee, bundle.annual_fee, threshold_events)
    assert fees.waived == expected["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(expected["fee_paid"]))
    assert fees.year1_fee == Decimal(str(expected["_fee_year1"]))

    valuation_currencies = _valuation_currencies(currencies)
    valuation = value_currency(valuation_currencies["sbi_prime_points"], points=gross_points_earned, primary_route_key="voucher_catalog")
    assert valuation.v_exp_rupees == Decimal(str(expected["gross_reward_value"]))

    golden_assumptions = scenario["assumptions"]
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
    # The batch's own headline finding: byte-identical to base PRIME's golden.
    assert result.nacv.steady_state == Decimal("5700.24")
    assert result.nacv.year_1 == Decimal("2161.42")
