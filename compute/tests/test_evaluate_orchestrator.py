"""Regression proof for engine/evaluate.py's `evaluate_card` (Phase 3):
runs the SAME 12 synthetic cards `tests/test_goldens.py` already hand-
verified stage-by-stage, this time through the single consolidated
orchestrator call, and asserts identical numbers against each golden's own
`expected` block. A pass here proves the orchestrator reproduces exactly
what 12 independently-verified pipelines already proved correct -- it is
not re-deriving correctness, it is checking the consolidation didn't drift.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.evaluate import EvaluateAssumptions, evaluate_card
from seeds.synthetic_cards import CARDS, CURRENCIES
from tests.test_goldens import _parse_spend_annual

GOLDENS_DIR = Path(__file__).resolve().parent.parent / "goldens"
CURRENCIES_BY_KEY = currencies_from_dicts(CURRENCIES)

GOLDEN_FILES = sorted(GOLDENS_DIR.glob("golden_syn_*.json"))


def _card_dict(card_key: str) -> dict:
    return next(c for c in CARDS if c["key"] == card_key)


def _assumptions_from_golden(golden: dict) -> EvaluateAssumptions:
    a = golden.get("assumptions", {})
    kwargs = {}
    if "primary_route" in a:
        kwargs["primary_routes"] = a["primary_route"]
    if "voucher_utilisation" in a:
        kwargs["voucher_utilisation"] = Decimal(str(a["voucher_utilisation"]))
    if "voucher_friction" in a:
        kwargs["voucher_friction"] = Decimal(str(a["voucher_friction"]))
    if "benefit_need" in a:
        kwargs["benefit_need"] = {k: Decimal(str(v)) for k, v in a["benefit_need"].items()}
    if "benefit_unit_value" in a:
        kwargs["benefit_unit_value"] = {k: Decimal(str(v)) for k, v in a["benefit_unit_value"].items()}
    return EvaluateAssumptions(**kwargs)


@pytest.mark.parametrize("golden_path", GOLDEN_FILES, ids=lambda p: p.stem)
def test_evaluate_card_matches_golden(golden_path):
    golden = json.loads(golden_path.read_text())
    card_key = golden["card"]

    seasonality = golden.get("seasonality")
    seasonality_arg = seasonality if isinstance(seasonality, dict) else None
    spend = _parse_spend_annual(golden["spend_annual"], seasonality_arg)

    bundle = bundle_from_dict(_card_dict(card_key))
    assumptions = _assumptions_from_golden(golden)

    result = evaluate_card(bundle, CURRENCIES_BY_KEY, spend, assumptions)

    expected = golden["expected"]
    assert result.gross_reward_value == Decimal(str(expected["gross_reward_value"]))
    if "milestone_value" in expected:
        assert result.milestone_value == Decimal(str(expected["milestone_value"]))
    if "milestone_value_year1" in expected:
        assert result.milestone_value_year1 == Decimal(str(expected["milestone_value_year1"]))
    if "benefit_value" in expected:
        assert result.benefit_value == Decimal(str(expected["benefit_value"]))
    if "waiver_achieved" in expected:
        assert result.waiver_achieved == expected["waiver_achieved"]
    assert result.fee_steady == Decimal(str(expected["fee_paid"]))
    assert result.nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert result.nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
    if "nacv_3yr" in expected:
        assert result.nacv.three_year == Decimal(str(expected["nacv_3yr"]))
    if "benefit_entitlement_units" in expected:
        (benefit_key,) = bundle.benefits.keys()
        assert result.benefit_valuations[benefit_key].entitlement_units == Decimal(str(expected["benefit_entitlement_units"]))


def test_all_twelve_synthetic_cards_covered():
    covered_cards = {json.loads(p.read_text())["card"] for p in GOLDEN_FILES}
    all_cards = {c["key"] for c in CARDS}
    assert covered_cards == all_cards
