"""Golden battery (compute/goldens/*.json), per goldens/README.md: run
through the full engine pipeline built so far, hand computation in each
golden's own `_hand_computation` is the arbiter of a red test.

The seed->engine adapter below (`_load_card_rules`/`_load_thresholds`)
translates directly from seeds/synthetic_cards.py so goldens test the same
rule data that gets seeded into the database. It currently handles
percentage/per_unit accruals, category/channel/merchant_group selectors,
Stage 5's full reward-measure cap support (any window kind, any scope),
and Stage 6-7's grant-type threshold payloads. It will still need
extending for benefits/surcharges/forex as more goldens come online (this
golden's card has none of those), and for spend-measure caps /
activate_rule once syn_slab's fill-order mechanic and Stage 3 activation
support are built (see docs/DECISIONS.md).
"""
import json
from decimal import Decimal
from pathlib import Path

from engine.accrue import Accrual, accrue_category_mode
from engine.caps import Cap, Window, apply_caps
from engine.costs import compute_fees
from engine.eligibility import apply_eligibility
from engine.match import EarningRule, Selector, match
from engine.normalise import AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from engine.thresholds import Payload, Threshold, ThresholdBasis, Tier, evaluate_thresholds
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
            rule_group=er.get("rule_group"),
        ))
        accruals[er["key"]] = _accrual_from_dict(er["accrual"])

    cap_defs = {c["key"]: c for c in card.get("caps", [])}
    caps = []
    for er in card["earning_rules"]:
        for cap_key in er.get("caps", []):
            cd = cap_defs[cap_key]
            window = Window(kind=cd["window_def"]["kind"], alignment=cd["window_def"].get("alignment"))
            caps.append(Cap(
                key=cd["key"], rule_key=er["key"], measure=cd["measure"],
                amount=Decimal(str(cd["amount"])), window=window,
                scope=cd["scope"], overflow=cd["overflow"],
            ))

    return tuple(earning_rules), accruals, tuple(caps)


def _payload_from_dict(d: dict) -> Payload:
    return Payload(
        type=d["type"],
        amount=Decimal(str(d["amount"])) if "amount" in d else None,
        currency=d.get("currency"),
        benefit=d.get("benefit"),
        fee=d.get("fee"),
        quantity=d.get("quantity"),
        window=Window(kind=d["window"]["kind"], alignment=d["window"].get("alignment")) if "window" in d else None,
        condition=d.get("condition"),
        rule=d.get("rule"),
        application=d.get("application"),
    )


def _threshold_from_dict(d: dict) -> Threshold:
    basis_dict = d["basis"]
    basis = ThresholdBasis(
        measure=basis_dict["measure"],
        window=Window(kind=basis_dict["window"]["kind"], alignment=basis_dict["window"].get("alignment")),
        selector_override=_selector_from_dict(basis_dict["selector_override"]) if basis_dict.get("selector_override") else None,
    )
    tiers = tuple(
        Tier(tier_index=t["tier_index"], threshold_amount=Decimal(str(t["threshold_amount"])), payload=_payload_from_dict(t["payload"]))
        for t in d["tiers"]
    )
    return Threshold(key=d["key"], basis=basis, tier_mode=d["tier_mode"], tiers=tiers)


def _load_thresholds(card_key: str) -> tuple[Threshold, ...]:
    card = next(c for c in CARDS if c["key"] == card_key)
    return tuple(_threshold_from_dict(t) for t in card.get("thresholds", []))


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

    # Stages 6-7 (thresholds) + 10 (costs): waiver crossing and fees.
    thresholds = _load_thresholds("syn_ecom")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_ecom")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)

    assert fees.waived == golden["expected"]["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    # syn_ecom has no benefits/surcharges/forex, so NACV = gross reward -
    # fees exactly (A.12's other terms are all zero for this card).
    nacv_steady_state = gross_reward_value - fees.steady_fee
    nacv_year_1 = gross_reward_value - fees.year1_fee
    assert nacv_steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv_year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
