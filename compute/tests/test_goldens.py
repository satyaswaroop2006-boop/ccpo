"""Golden battery (compute/goldens/*.json), per goldens/README.md: run
through the full engine pipeline built so far, hand computation in each
golden's own `_hand_computation` is the arbiter of a red test.

The seed->engine adapter below (`_load_card_rules`) is intentionally
narrow: it handles exactly what golden_syn_ecom_basic.json's card
(percentage accruals, category/channel selectors, a single reward-measure/
calendar-month/rule-scope cap) needs, translating directly from
seeds/synthetic_cards.py so the golden tests the same rule data that gets
seeded into the database. It will need extending (per_unit accruals,
thresholds, benefits, ...) as more goldens come online.
"""
import json
from decimal import Decimal
from pathlib import Path

from engine.accrue import Accrual, accrue_category_mode
from engine.caps import Cap, apply_caps
from engine.eligibility import apply_eligibility
from engine.match import EarningRule, Selector, match
from engine.normalise import AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from seeds.synthetic_cards import CARDS

GOLDENS_DIR = Path(__file__).resolve().parent.parent / "goldens"


def _selector_from_dict(d: dict) -> Selector:
    kwargs = {}
    if d.get("categories") is not None:
        kwargs["categories"] = tuple(d["categories"])
    if d.get("channels") is not None:
        kwargs["channels"] = tuple(d["channels"])
    if d.get("merchant_groups") is not None:
        kwargs["merchant_groups"] = tuple(d["merchant_groups"])
    return Selector(**kwargs)


def _accrual_from_dict(d: dict) -> Accrual:
    if d["type"] == "percentage":
        return Accrual(type="percentage", currency=d.get("currency"), rate=Decimal(str(d["rate"])), rounding=d["rounding"])
    if d["type"] == "per_unit":
        return Accrual(
            type="per_unit", currency=d.get("currency"),
            unit_amount=Decimal(str(d["unit_amount"])), points_per_unit=Decimal(str(d["points_per_unit"])),
            rounding=d["rounding"],
        )
    raise ValueError(f"golden adapter: unsupported accrual type {d['type']!r}")


def _load_card_rules(card_key: str):
    card = next(c for c in CARDS if c["key"] == card_key)

    earning_rules = []
    accruals: dict[str, Accrual] = {}
    for er in card["earning_rules"]:
        selector = _selector_from_dict(er.get("selector", {}))
        earning_rules.append(EarningRule(
            key=er["key"], selector=selector,
            priority=er.get("priority", 10), stacks_with_base=er.get("stacks_with_base", False),
        ))
        accruals[er["key"]] = _accrual_from_dict(er["accrual"])

    cap_defs = {c["key"]: c for c in card.get("caps", [])}
    caps = []
    for er in card["earning_rules"]:
        for cap_key in er.get("caps", []):
            cd = cap_defs[cap_key]
            caps.append(Cap(
                key=cd["key"], rule_key=er["key"], measure=cd["measure"],
                amount=Decimal(str(cd["amount"])), window=cd["window_def"]["kind"],
                scope=cd["scope"], overflow=cd["overflow"],
            ))

    return tuple(earning_rules), accruals, tuple(caps)


def _parse_spend_annual(spend_annual: dict) -> SpendInput:
    lines = []
    for key, amount in spend_annual.items():
        category, _, channel = key.partition("/")
        lines.append(CategorySpend(category=category, channel=channel or None, annual_amount=Decimal(str(amount))))
    return SpendInput(category_spend=tuple(lines))


def test_golden_syn_ecom_basic():
    golden = json.loads((GOLDENS_DIR / "golden_syn_ecom_basic.json").read_text())
    assert golden["card"] == "syn_ecom"
    assert golden["seasonality"] == "uniform"  # normalise()'s default when no seasonality is given

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())

    # syn_ecom has no exclusions -> the reward view is the full spend grid.
    eligible = apply_eligibility(normalised, exclusions=())

    earning_rules, accruals, caps = _load_card_rules("syn_ecom")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)

    gross_reward_value = sum((r.reward for r in final), Decimal("0"))

    assert gross_reward_value == Decimal(str(golden["expected"]["gross_reward_value"]))
    # golden's own note: "ticket-size rounding immaterial at these rates --
    # no estimation flag expected". cap_overflow bookkeeping flags (internal
    # to caps.py's trace) are a separate concern from this check.
    assert not any("rounding_estimated" in r.flags for r in final)
