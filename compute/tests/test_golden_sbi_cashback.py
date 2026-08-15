"""First REAL-card golden: CASHBACK SBI Card. Pipeline validation only, per
Satya's own framing -- confirms the engine computes CASHBACK correctly
against a hand-drafted real ingestion bundle (`compute/ingestion/
bundle_sbi_cashback.json` + `golden_sbi_cashback.json`), NOT a publish.
Nothing here writes to Postgres; the card_version stays conceptually
`draft` throughout -- see `docs/Part_I_Ingestion_Workflow.md` SS I.4/I.8
for the actual publish gate (>=1 passing golden is a *precondition* for
publish, not proof of it -- reviewer approval of every source_link is a
separate, human-only step this run doesn't attempt).

The ingestion bundle's field names were subsequently renamed to match
`engine.card_bundle.bundle_from_dict`'s expected shape directly (per
Satya's own request, docs/DECISIONS.md #113) -- `card_key`/`key`,
`card_name`/`name`, `fees`/`version`, `threshold_rules`/`thresholds`, a
tier's `threshold`/`threshold_amount`, a surcharge's `surcharge_rate`/
`rate`, and `currency` restructured from a card-embedded object into a
bare string plus a separate top-level `currencies` list (mirrors
`seeds/synthetic_cards.py`'s own standalone `CURRENCIES`). The bundle now
loads via `bundle_from_dict` with only ONE deliberate override left --
see `_adapt_ingestion_bundle` below.

**Two things were NOT renamed, because they aren't naming mismatches**
(full detail: docs/DECISIONS.md #110-#111, and the bundle's own
`_engine_compatibility_note` / the fuel surcharge's `_note`):

- `exclusions[]` (`cashback_mcc_exclusions` using `mcc_include`,
  `min_txn_100` using `txn_max`) uses selector fields Part C itself
  defines -- correctly named, just not yet evaluable by `engine/
  eligibility.py` (Stage 2 only supports categories/channels/
  merchant_group/geography). Worse than merely unsupported:
  `_exclusion_selector_from_dict` silently drops these fields, producing
  an all-`None` `ExclusionSelector` that `_selector_matches` treats as
  MATCHES EVERYTHING -- loading `exclusions[]` as-is would zero out ALL
  cashback (`excluded_from: ["rewards"]`), not just the intended
  fuel/rent/sub-Rs100 spend. `_adapt_ingestion_bundle` still drops this
  array explicitly; confirmed inert for both scenarios below regardless
  (neither touches an MCC-excluded category).
- `surcharges[0].waiver` (the 1% fuel-surcharge refund) has no field on
  `engine.costs.Surcharge` at all -- per the `syn_fuel` precedent
  (docs/DECISIONS.md #26) it needs to become a separate capped
  earning_rule, not a sub-object. Left in the bundle file (informational,
  flagged, not yet modelled); harmless to load since `bundle_from_dict`
  simply ignores the extra `waiver` key the same way it ignores `_note`/
  `_source`. Inert for both scenarios (neither touches fuel spend).

Scenario A (SBI's own PDF-published worked example, expected Rs1,350)
depends on excluding EMI-converted spend, which has no representation
anywhere in Part C's Selector vocabulary or category-mode `CategorySpend`/
`SpendSegment` -- the only `is_emi` field in the whole schema is
`user_transactions.is_emi`, transaction-mode-only, not wired into
`evaluate_card`. Confirmed with Satya: genuine schema gap, deferred
(Part C SS C.1 principle 1), not worked around. `@pytest.mark.skip`ped
below with the full reasoning as its skip message -- a permanent record
in the suite, not just in chat.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.accrue import accrue_category_mode
from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.caps import apply_caps
from engine.eligibility import apply_eligibility
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.match import match
from engine.normalise import AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from engine.thresholds import evaluate_thresholds

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"

_RAW_BUNDLE = json.loads((INGESTION_DIR / "bundle_sbi_cashback.json").read_text())
_GOLDEN = json.loads((INGESTION_DIR / "golden_sbi_cashback.json").read_text())


def _adapt_ingestion_bundle(raw: dict) -> dict:
    """The bundle now matches `bundle_from_dict`'s expected shape directly
    -- the only override left is dropping `exclusions[]`, which remains
    genuinely unsafe to load as-is (see module docstring)."""
    return {**raw, "exclusions": []}


def _bundle_and_currencies():
    bundle = bundle_from_dict(_adapt_ingestion_bundle(_RAW_BUNDLE))
    currencies = currencies_from_dicts(_RAW_BUNDLE["currencies"])
    return bundle, currencies


def _spend_from_annual(spend_annual: dict) -> SpendInput:
    """Same "category[/channel]" key convention as tests/test_goldens.py's
    own `_parse_spend_annual` (a local copy since this file doesn't need
    that helper's merchant_group/geography handling)."""
    lines = []
    for key, amount in spend_annual.items():
        category, _, channel = key.partition("/")
        lines.append(CategorySpend(category=category, channel=channel or None, annual_amount=Decimal(str(amount))))
    return SpendInput(category_spend=tuple(lines))


def test_ingestion_bundle_loads_without_crashing():
    """Confirms the (now nearly-direct) load is complete and correct."""
    bundle, currencies = _bundle_and_currencies()
    assert bundle.card_key == "cashback_sbi"
    assert bundle.currency_key == "cashback_inr"
    assert "cashback_inr" in currencies
    assert {r.key for r in bundle.earning_rules} == {"online_5pct", "offline_1pct"}
    assert len(bundle.thresholds) == 1
    assert bundle.exclusions == ()  # dropped this run -- see module docstring


@pytest.mark.skip(reason=(
    "Scenario A (SBI's own PDF worked example, expected Rs1,350) depends on "
    "excluding EMI-converted spend before applying the 5% online rate. There "
    "is no EMI selector dimension anywhere in Part C's Selector vocabulary or "
    "in category-mode CategorySpend/SpendSegment -- the only is_emi field in "
    "the whole schema is user_transactions.is_emi, a transaction-mode-only "
    "column not wired into evaluate_card. Confirmed with Satya: genuine "
    "schema gap (Part C SS C.1 principle 1: 'a versioned engine extension, "
    "never an ad-hoc special case'), deferred as its own task rather than "
    "worked around by pre-filtering this test's input. Un-skip once an "
    "EMI/transaction-flag selector dimension exists."
))
def test_scenario_a_pdf_worked_example():
    pass


def test_scenario_b_steady_state_annual():
    scenario = _GOLDEN["scenario_B_steady_state_annual"]
    expected = scenario["expected"]
    spend = _spend_from_annual(scenario["spend_annual"])

    # Stage-by-stage, same pattern tests/test_goldens.py uses for every
    # synthetic-card golden -- gives the granular online/offline/cap
    # breakdown Satya asked to eyeball against the PDF, not just the final
    # NACV number.
    bundle, currencies = _bundle_and_currencies()
    normalised = normalise(spend, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, bundle.exclusions)  # () this run -- see module docstring
    bindings = match(NormalisedSpend(segments=eligible.reward), bundle.earning_rules)
    assert {b.rule_key for b in bindings} == {"online_5pct", "offline_1pct"}

    uncapped = accrue_category_mode(bindings, bundle.accruals)
    reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    final = apply_caps(uncapped, reward_caps, bundle.earning_rules, bundle.accruals)
    assert not any("rounding_estimated" in r.flags for r in final)  # ecommerce/offline_retail ticket sizes -> zero floor loss at 5%/1%

    online_total = sum((r.reward for r in final if r.rule_key == "online_5pct"), Decimal("0"))
    offline_total = sum((r.reward for r in final if r.rule_key == "offline_1pct"), Decimal("0"))
    # Online: Rs50,000/mo x 5% = Rs2,500/mo, but cap_online_monthly = Rs2,000
    # (statement_cycle, overflow=zero) trims every month to exactly Rs2,000.
    assert online_total == Decimal("24000.00")  # 2,000/mo x 12, cap BOUND every month
    # Offline: Rs20,000/mo x 1% = Rs200/mo, well under its own Rs2,000 cap
    # and the Rs4,000 aggregate cap -- neither ever binds.
    assert offline_total == Decimal("2400.00")  # 200/mo x 12, uncapped
    gross_reward_value = online_total + offline_total
    assert gross_reward_value == Decimal(str(expected["gross_reward_value"]))

    threshold_events = evaluate_thresholds(bundle.thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)
    assert len(threshold_events) == 1 and threshold_events[0].payload.type == "waive_fee"

    # Cross-check: the full evaluate_card() orchestrator (Phase 3's
    # consolidated Stage 1-11 pipeline) must agree exactly with the
    # stage-by-stage numbers above and with the golden's own hand
    # computation -- two independent code paths, one answer.
    result = evaluate_card(bundle, currencies, spend, EvaluateAssumptions())
    assert result.gross_reward_value == gross_reward_value
    assert result.waiver_achieved == expected["waiver_achieved"]
    assert result.fee_steady == Decimal(str(expected["fee_paid"]))
    assert result.nacv.steady_state == Decimal(str(expected["nacv_steady_state"]))
    assert result.nacv.year_1 == Decimal(str(expected["nacv_year_1"]))
