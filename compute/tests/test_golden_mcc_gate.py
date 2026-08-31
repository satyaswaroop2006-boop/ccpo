"""Phase 5 Task A regression gate (docs/DECISIONS.md #130): proves
mcc_include exclusions work end-to-end through the real pipeline, and
specifically that the fixed #111/#114 "matches everything" failure stays
fixed. `goldens/golden_mcc_gate_standalone.json` carries the full hand
computation; this file is the arbiter-by-arithmetic per CLAUDE.md rule 2.

The card is standalone -- NOT added to seeds/synthetic_cards.py's CARDS
catalog (that list is "12 of 12 synthetic cards wired, full C.9
coverage"; adding a 13th would ripple into every hardcoded card-count
assumption in the optimiser/seed tests, well beyond this task's scope).
Same pattern as tests/test_golden_sbi_cashback.py's own standalone real-
card fixture: bundle_from_dict is called directly on the golden's
embedded card dict, no CARDS lookup.
"""
import json
from decimal import Decimal
from pathlib import Path

from engine.accrue import accrue_category_mode
from engine.assemble import assemble_nacv
from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.caps import apply_caps
from engine.costs import compute_fees
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.match import match
from engine.normalise import (
    DEFAULT_CATEGORY_MCC_MAP,
    AssumptionsSnapshot,
    CategorySpend,
    NormalisedSpend,
    SpendInput,
    normalise,
)
from engine.thresholds import evaluate_thresholds

GOLDENS_DIR = Path(__file__).resolve().parent.parent / "goldens"
_GOLDEN = json.loads((GOLDENS_DIR / "golden_mcc_gate_standalone.json").read_text())


def _spend_input() -> SpendInput:
    lines = [
        CategorySpend(category=cat, annual_amount=Decimal(str(amount)))
        for cat, amount in _GOLDEN["spend_annual"].items()
    ]
    return SpendInput(category_spend=tuple(lines))


def _bundle_and_currencies():
    bundle = bundle_from_dict(_GOLDEN["card"])
    currencies = currencies_from_dicts(_GOLDEN["currencies"])
    return bundle, currencies


def test_fuel_mccs_are_in_the_map_the_golden_assumes():
    """Sanity-checks the golden's own premise before trusting anything it
    concludes: 5541/5542 (used by the exclusion selector) really are a
    subset, not the whole, of fuel's mapped MCCs -- proving the match is
    genuine set-intersection, not the selector happening to equal the map."""
    fuel_mccs = set(DEFAULT_CATEGORY_MCC_MAP["fuel"])
    assert {5541, 5542} < fuel_mccs  # strict subset
    assert "grocery" not in DEFAULT_CATEGORY_MCC_MAP  # grocery has no known MCCs -> can't accidentally match


def test_mcc_include_excludes_the_whole_matched_category_stage_by_stage():
    expected = _GOLDEN["expected"]
    bundle, currencies = _bundle_and_currencies()
    assert len(bundle.exclusions) == 1
    assert bundle.exclusions[0].selector.mcc_include == (5541, 5542)

    normalised = normalise(_spend_input(), AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions, DEFAULT_CATEGORY_MCC_MAP)

    # The regression proof itself: fuel is gone from the reward view,
    # grocery is untouched -- NOT "everything zeroed" (the pre-fix bug).
    reward_categories = {s.category for s in eligible.reward}
    assert reward_categories == {"grocery"}
    assert sum((s.amount for s in eligible.reward), Decimal("0")) == Decimal("120000")

    # excluded_from=["rewards"] only -- fuel still counts toward milestone/waiver.
    assert {s.category for s in eligible.milestone} == {"grocery", "fuel"}
    assert {s.category for s in eligible.waiver} == {"grocery", "fuel"}
    assert sum((s.amount for s in eligible.waiver), Decimal("0")) == Decimal("216000")

    assert eligible.flags == ("mcc_category_estimated",)

    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"base"}  # only grocery reaches match at all

    uncapped = accrue_category_mode(bindings, bundle.accruals)
    final = apply_caps(uncapped, (), bundle.earning_rules, bundle.accruals)  # no caps on this card
    assert not any("rounding_estimated" in r.flags for r in final)  # ticket 700 * 2% = exact

    gross_reward_value = sum((r.reward for r in final), Decimal("0"))
    assert gross_reward_value == Decimal(str(expected["gross_reward_value"]))

    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    assert threshold_events == ()  # no thresholds on this card

    fees = compute_fees(bundle.joining_fee, bundle.annual_fee, threshold_events)
    assert fees.steady_fee == Decimal(str(expected["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(expected["nacv_3yr"]))


def test_evaluate_card_orchestrator_agrees():
    """Cross-check: the consolidated Stage 1-11 pipeline (engine/
    evaluate.py, what /evaluate and /optimise actually call) must agree
    with the stage-by-stage numbers above -- two independent code paths,
    one answer, same discipline as every other golden in this suite."""
    expected = _GOLDEN["expected"]
    bundle, currencies = _bundle_and_currencies()
    result = evaluate_card(bundle, currencies, _spend_input(), EvaluateAssumptions())

    assert result.gross_reward_value == Decimal(str(expected["gross_reward_value"]))
    assert result.fee_steady == Decimal(str(expected["fee_paid"]))
    assert result.nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert result.nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
    assert result.nacv.three_year == Decimal(str(expected["nacv_3yr"]))
    assert list(result.flags) == expected["flags"]
