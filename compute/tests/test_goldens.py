"""Golden battery (compute/goldens/*.json), per goldens/README.md: run
through the full engine pipeline built so far, hand computation in each
golden's own `_hand_computation` is the arbiter of a red test.

The seed->engine adapter below (`_load_card_rules`/`_load_thresholds`/
`_load_benefits`/`_load_currencies`) translates directly from
seeds/synthetic_cards.py so goldens test the same rule data that gets
seeded into the database. It currently handles percentage/per_unit
accruals, category/channel/merchant_group selectors, Stage 5's full
reward-measure cap support (any window kind, any scope), spend-measure
incremental bands (`tier_mode` inferred from rule_group + spend-measure
caps, since the raw schema has no explicit field -- see
`_load_card_rules`), activate_rule (requires_activation carried straight
from the seed field), Stage 6-7's grant-type threshold payloads, Stage 8's
currency/route valuation, and Stage 9's countable/voucher benefits. It
will still need extending for surcharges/forex once a golden needs them
(none do yet).

Need/unit_value/utilisation/friction/primary-route assumptions have no
home in the card schema at all (they're user/registry inputs, per C.7) --
each golden's own `assumptions` block declares them explicitly, and the
test reads them from there rather than inventing defaults.
"""
import json
from decimal import Decimal
from pathlib import Path

from engine.accrue import Accrual, accrue_category_mode
from engine.assemble import assemble_nacv, value_milestone_grants
from engine.benefits import Benefit, value_countable_benefit, value_voucher_benefit
from engine.caps import Cap, Window, apply_caps, apply_incremental_bands
from engine.costs import compute_fees
from engine.eligibility import apply_eligibility
from engine.match import EarningRule, Selector, match
from engine.normalise import AssumptionsSnapshot, CategorySpend, NormalisedSpend, SpendInput, normalise
from engine.thresholds import Payload, Threshold, ThresholdBasis, Tier, evaluate_thresholds
from engine.valuation import RedemptionRoute, RewardCurrency, value_accrual_results
from seeds.synthetic_cards import CARDS, CURRENCIES

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


def _accrual_from_dict(d: dict, currency: str) -> Accrual:
    if d["type"] == "percentage":
        return Accrual(type="percentage", currency=currency, rate=Decimal(str(d["rate"])), rounding=d["rounding"])
    if d["type"] == "per_unit":
        return Accrual(
            type="per_unit", currency=currency,
            unit_amount=Decimal(str(d["unit_amount"])), points_per_unit=Decimal(str(d["points_per_unit"])),
            rounding=d["rounding"],
        )
    raise ValueError(f"golden adapter: unsupported accrual type {d['type']!r}")


def _load_card_rules(card_key: str):
    """Returns (earning_rules, accruals, caps) -- `caps` mixes reward- and
    spend-measure caps; callers filter by `.measure` for whichever
    downstream function (apply_caps vs apply_incremental_bands) they need."""
    card = next(c for c in CARDS if c["key"] == card_key)
    cap_defs = {c["key"]: c for c in card.get("caps", [])}

    # The raw seed schema has no tier_mode field at all (not even a
    # seed.py INSERT column) -- syn_slab's incremental rules are only
    # identifiable by their rule_group containing a spend-measure cap
    # SOMEWHERE in it (slab3 itself is uncapped, but shares rule_group
    # "slab" with slab1/slab2, which are). Inferred here rather than
    # trusting an explicit field that doesn't exist yet.
    incremental_groups = set()
    for er in card["earning_rules"]:
        group = er.get("rule_group")
        if group is None:
            continue
        for cap_key in er.get("caps", []):
            if cap_defs[cap_key]["measure"] == "spend":
                incremental_groups.add(group)

    earning_rules = []
    accruals: dict[str, Accrual] = {}
    for er in card["earning_rules"]:
        selector = _selector_from_dict(er.get("selector", {}))
        group = er.get("rule_group")
        earning_rules.append(EarningRule(
            key=er["key"], selector=selector,
            priority=er.get("priority", 10), stacks_with_base=er.get("stacks_with_base", False),
            rule_group=group, requires_activation=er.get("requires_activation", False),
            tier_mode="incremental" if group in incremental_groups else None,
        ))
        # seed.py itself stamps accrual["currency"] = card["currency"] before
        # inserting (the raw fixture dicts always carry currency: None) --
        # mirrored here so the golden adapter matches what actually gets seeded.
        accruals[er["key"]] = _accrual_from_dict(er["accrual"], card["currency"])

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


def _benefit_from_dict(d: dict) -> Benefit:
    return Benefit(
        key=d["key"], kind=d["kind"], unit_label=d.get("unit_label"),
        entitlement=Decimal(str(d["entitlement"])) if "entitlement" in d else None,
        entitlement_window=(
            Window(kind=d["entitlement_window"]["kind"], alignment=d["entitlement_window"].get("alignment"))
            if "entitlement_window" in d else None
        ),
        qualification_threshold_key=d.get("qualification_threshold_key"),
        face_value=Decimal(str(d["face_value"])) if "face_value" in d else None,
    )


def _load_benefits(card_key: str) -> dict[str, Benefit]:
    card = next(c for c in CARDS if c["key"] == card_key)
    return {b["key"]: _benefit_from_dict(b) for b in card.get("benefits", [])}


def _route_from_dict(d: dict) -> RedemptionRoute:
    return RedemptionRoute(
        key=d["key"], route_type=d["route_type"],
        ratio=Decimal(str(d["ratio"])) if "ratio" in d else None,
        friction=Decimal(str(d["friction_default"])) if "friction_default" in d else None,
        min_points=Decimal(str(d["min_points"])) if "min_points" in d else None,
        transfer_partner=d.get("transfer_partner"),
        transfer_ratio=Decimal(str(d["transfer_ratio"])) if "transfer_ratio" in d else None,
        partner_point_value=Decimal(str(d["partner_point_value"])) if "partner_point_value" in d else None,
    )


def _load_currencies() -> dict[str, RewardCurrency]:
    return {c["key"]: RewardCurrency(key=c["key"], routes=tuple(_route_from_dict(r) for r in c["routes"])) for c in CURRENCIES}


def _parse_spend_annual(spend_annual: dict, seasonality: dict | None = None) -> SpendInput:
    """`seasonality`, if given, maps category -> a 12-weight list (C.9's
    per-category custom seasonality); categories not in it default to
    normalise()'s uniform split."""
    lines = []
    for key, amount in spend_annual.items():
        category, _, channel = key.partition("/")
        weights = None
        if seasonality and category in seasonality:
            weights = tuple(Decimal(str(w)) for w in seasonality[category])
        lines.append(CategorySpend(category=category, channel=channel or None, annual_amount=Decimal(str(amount)), seasonality=weights))
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

    # Stage 11: final assembly. syn_ecom has no milestones/benefits/
    # surcharges/forex, so every one of those terms is legitimately zero.
    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))


def test_golden_syn_miles_vouchers():
    golden = json.loads((GOLDENS_DIR / "golden_syn_miles_vouchers.json").read_text())
    assert golden["card"] == "syn_miles"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, exclusions=())  # syn_miles has no exclusions

    earning_rules, accruals, caps = _load_card_rules("syn_miles")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)  # no caps on this card -> no-op
    assert not any("rounding_estimated" in r.flags for r in final)

    currencies = _load_currencies()
    primary_routes = golden["assumptions"]["primary_route"]
    reward_valuations = value_accrual_results(final, accruals, currencies, primary_routes)
    gross_reward_rupees = sum((v.v_exp_rupees for v in reward_valuations), Decimal("0"))
    assert gross_reward_rupees == Decimal(str(golden["expected"]["gross_reward_value"]))

    thresholds = _load_thresholds("syn_miles")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    benefits = _load_benefits("syn_miles")
    voucher_valuations = {
        key: value_voucher_benefit(b, threshold_events, Decimal(str(golden["assumptions"]["voucher_utilisation"])), Decimal(str(golden["assumptions"]["voucher_friction"])))
        for key, b in benefits.items() if b.kind == "voucher"
    }
    milestone_value, _milestone_lines = value_milestone_grants(threshold_events, currencies, primary_routes, voucher_valuations)
    assert milestone_value == Decimal(str(golden["expected"]["milestone_value"]))

    card = next(c for c in CARDS if c["key"] == "syn_miles")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)  # no waiver threshold -> always charged
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_rupees, milestone_value=milestone_value, benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))


def test_golden_syn_lounge_quarterly_gate():
    golden = json.loads((GOLDENS_DIR / "golden_syn_lounge_quarterly_gate.json").read_text())
    assert golden["card"] == "syn_lounge"

    spend_input = _parse_spend_annual(golden["spend_annual"], golden["seasonality"])
    normalised = normalise(spend_input, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, exclusions=())  # syn_lounge has no exclusions

    earning_rules, accruals, caps = _load_card_rules("syn_lounge")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)  # no caps on this card -> no-op
    assert not any("rounding_estimated" in r.flags for r in final)

    currencies = _load_currencies()
    primary_routes = golden["assumptions"]["primary_route"]
    reward_valuations = value_accrual_results(final, accruals, currencies, primary_routes)
    gross_reward_rupees = sum((v.v_exp_rupees for v in reward_valuations), Decimal("0"))
    assert gross_reward_rupees == Decimal(str(golden["expected"]["gross_reward_value"]))

    thresholds = _load_thresholds("syn_lounge")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    # no grant_points/grant_cashback/grant_voucher payloads on this card --
    # only grant_entitlement, which value_milestone_grants correctly excludes.
    milestone_value, _milestone_lines = value_milestone_grants(threshold_events, currencies, primary_routes, voucher_valuations={})
    assert milestone_value == Decimal(str(golden["expected"]["milestone_value"]))

    benefits = _load_benefits("syn_lounge")
    need = Decimal(str(golden["assumptions"]["benefit_need"]["dom_lounge"]))
    unit_value = Decimal(str(golden["assumptions"]["benefit_unit_value"]["dom_lounge"]))
    benefit_valuation = value_countable_benefit(benefits["dom_lounge"], threshold_events, need, unit_value)
    assert benefit_valuation.entitlement_units == Decimal(str(golden["expected"]["benefit_entitlement_units"]))
    assert benefit_valuation.value_rupees == Decimal(str(golden["expected"]["benefit_value"]))

    card = next(c for c in CARDS if c["key"] == "syn_lounge")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)  # no waiver threshold -> always charged
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_rupees, milestone_value=milestone_value, benefit_value=benefit_valuation.value_rupees,
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))


def test_golden_syn_slab_bands():
    golden = json.loads((GOLDENS_DIR / "golden_syn_slab_bands.json").read_text())
    assert golden["card"] == "syn_slab"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, exclusions=())  # syn_slab has no exclusions

    earning_rules, accruals, all_caps = _load_card_rules("syn_slab")
    assert {r.tier_mode for r in earning_rules} == {"incremental"}  # all three slabs inferred correctly

    # Ordinary Stage 3 matching produces nothing -- every rule is
    # tier_mode="incremental", so this card's entire reward comes from
    # apply_incremental_bands below.
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    assert bindings == ()

    spend_caps = tuple(c for c in all_caps if c.measure == "spend")
    band_results = apply_incremental_bands(eligible.reward, earning_rules, spend_caps, accruals)
    gross_reward_value = sum((r.reward for r in band_results), Decimal("0"))
    assert gross_reward_value == Decimal(str(golden["expected"]["gross_reward_value"]))
    assert not any(r.flags for r in band_results)  # never estimated -- see the golden's own note

    thresholds = _load_thresholds("syn_slab")
    assert thresholds == ()  # this card has none
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_slab")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))
