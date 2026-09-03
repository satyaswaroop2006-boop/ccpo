"""Unit tests for `ingest.publish._run_scenario`'s key-validation hardening
(docs/DECISIONS.md #153) -- no database access needed, since the failure
path this covers returns BEFORE `evaluate_card` is ever called. Deliberately
separate from `tests/test_ingest_publish.py` (which needs a live DB for its
own end-to-end scenarios) so this stays fast and runs everywhere.

Before this fix, `_run_scenario` silently skipped any `expected`/
`assumptions` key it didn't recognise -- `golden_sbi_prime.json`'s own
first publish attempt would have reported PASS while actually comparing
almost nothing, caught only because a benefit-assumptions crash happened
to fire first (see DECISIONS.md #152/#153 for the full incident).
"""
from decimal import Decimal

from engine.card_bundle import CardRuleBundle
from ingest.publish import _run_scenario

_EMPTY_BUNDLE = CardRuleBundle(
    card_key="test_card", currency_key="test_currency",
    joining_fee=Decimal("0"), annual_fee=Decimal("0"), forex_markup=Decimal("0"),
    earning_rules=(), accruals={}, caps=(), thresholds=(), exclusions=(), benefits={}, surcharges=(),
)


def test_unrecognized_expected_key_fails_loudly_not_silently():
    scenario = {
        "spend_annual": {"grocery": 1000},
        # golden_sbi_prime.json's own real first-attempt mistake, verbatim:
        "expected": {"gross_reward_value_rupees": 5700.24, "nacv_steady_state_rupees": 5700.24},
    }
    result = _run_scenario(_EMPTY_BUNDLE, {}, "golden.json", "scenario", scenario)
    assert result.passed is False
    assert any("gross_reward_value_rupees" in d and "nacv_steady_state_rupees" in d for d in result.diffs)


def test_unrecognized_assumptions_key_fails_loudly_not_silently():
    scenario = {
        "spend_annual": {"grocery": 1000},
        "assumptions": {"benefit_needs": {"lounge": 4}},  # typo: needs vs need
        "expected": {"gross_reward_value": 0},
    }
    result = _run_scenario(_EMPTY_BUNDLE, {}, "golden.json", "scenario", scenario)
    assert result.passed is False
    assert any("benefit_needs" in d for d in result.diffs)


def test_leading_underscore_keys_are_informational_and_never_flagged():
    """The repo-wide `_note`/`_source` convention -- an underscore-prefixed
    key is documentation, deliberately exempt from the recognized-key check
    in both `expected` and `assumptions`."""
    scenario = {
        "spend_annual": {},
        "assumptions": {"primary_route": {}, "_note": "informational only"},
        "expected": {"gross_reward_value": 0, "_gross_points_earned": 0, "_note": "informational only"},
    }
    result = _run_scenario(_EMPTY_BUNDLE, {"test_currency": None}, "golden.json", "scenario", scenario)
    # Gets past the key-validation stage cleanly -- whatever happens next
    # (evaluate_card succeeding or not) is unrelated to this test's concern.
    assert not any("unrecognized" in d for d in result.diffs)


def test_recognized_keys_are_never_flagged():
    """Every key this repo's two real goldens (CASHBACK/PRIME) actually
    use -- confirms the hardening doesn't regress the legitimate contract."""
    scenario = {
        "spend_annual": {},
        "assumptions": {
            "primary_route": {}, "voucher_utilisation": 1, "voucher_friction": 1,
            "benefit_need": {}, "benefit_unit_value": {}, "redemptions_per_year": {},
        },
        "expected": {
            "gross_reward_value": 0, "milestone_value": 0, "milestone_value_year1": 0,
            "benefit_value": 0, "fee_paid": 0, "waiver_achieved": True,
            "nacv_steady_state": 0, "nacv_year_1": 0,
        },
    }
    result = _run_scenario(_EMPTY_BUNDLE, {"test_currency": None}, "golden.json", "scenario", scenario)
    assert not any("unrecognized" in d for d in result.diffs)
