"""Golden battery (compute/goldens/*.json), per goldens/README.md: run
through the full engine pipeline built so far, hand computation in each
golden's own `_hand_computation` is the arbiter of a red test.

The seed->engine adapter below (`_load_card_rules`/`_load_thresholds`/
`_load_benefits`/`_load_currencies`) translates directly from
seeds/synthetic_cards.py so goldens test the same rule data that gets
seeded into the database. It currently handles percentage/per_unit
accruals, category/channel/merchant_group/geography selectors, Stage 2's
exclusions (`_load_exclusions`), Stage 5's full reward-measure cap support
(any window kind, any scope), spend-measure incremental bands (`tier_mode`
inferred from rule_group + spend-measure caps, since the raw schema has
no explicit field -- see `_load_card_rules`), activate_rule
(requires_activation carried straight from the seed field), Stage 6-7's
grant-type threshold payloads, Stage 8's currency/route valuation, Stage
9's countable/voucher benefits, and Stage 10's surcharges/forex
(`_load_surcharges`, `international_spend_total`). Every engine construct
in Part C SS C.9's catalogue now has at least one golden exercising it.

`_parse_spend_annual`'s key format is "category[/channel][~merchant_group]
[@geography]" -- e.g. "international_flights@international" or
"hotels_domestic~synth_portal" -- geography defaults to "domestic" and
merchant_group to unset (None) when omitted. The "~merchant_group" token is
a golden-adapter-only spelling (not part of C.2.1's own JSON vocabulary);
see docs/DECISIONS.md #5 for why `CategorySpend` needed the field at all.

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
from engine.costs import Surcharge, compute_fees, forex_cost, international_spend_total, surcharge_cost
from engine.eligibility import Exclusion, ExclusionSelector, apply_eligibility
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
    if d.get("geography") is not None:
        kwargs["geography"] = d["geography"]
    return Selector(**kwargs)


def _exclusion_selector_from_dict(d: dict) -> ExclusionSelector:
    kwargs = {}
    if d.get("categories") is not None:
        kwargs["categories"] = tuple(d["categories"])
    if d.get("channels") is not None:
        kwargs["channels"] = tuple(d["channels"])
    if d.get("merchant_groups") is not None:
        kwargs["merchant_groups"] = tuple(d["merchant_groups"])
    if d.get("geography") is not None:
        kwargs["geography"] = d["geography"]
    return ExclusionSelector(**kwargs)


def _load_exclusions(card_key: str) -> tuple[Exclusion, ...]:
    card = next(c for c in CARDS if c["key"] == card_key)
    return tuple(
        Exclusion(
            key=e["key"], selector=_exclusion_selector_from_dict(e["selector"]),
            excluded_from=tuple(e["excluded_from"]), note=e.get("note"),
        )
        for e in card.get("exclusions", [])
    )


def _load_surcharges(card_key: str) -> tuple[Surcharge, ...]:
    card = next(c for c in CARDS if c["key"] == card_key)
    return tuple(
        Surcharge(
            key=s["key"], selector=_selector_from_dict(s["selector"]),
            rate=Decimal(str(s["rate"])), gst_on_surcharge=Decimal(str(s["gst_on_surcharge"])),
        )
        for s in card.get("surcharges", [])
    )


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
    """Key format: "category[/channel][~merchant_group][@geography]" --
    geography defaults to "domestic" and merchant_group to unset (None)
    when omitted (e.g. "international_flights@international",
    "hotels_domestic~synth_portal"). `seasonality`, if given, maps
    category -> a 12-weight list (C.9's per-category custom seasonality);
    categories not in it default to normalise()'s uniform split."""
    lines = []
    for key, amount in spend_annual.items():
        rest, _, geography = key.partition("@")
        rest, _, merchant_group = rest.partition("~")
        category, _, channel = rest.partition("/")
        weights = None
        if seasonality and category in seasonality:
            weights = tuple(Decimal(str(w)) for w in seasonality[category])
        lines.append(CategorySpend(
            category=category, channel=channel or None, annual_amount=Decimal(str(amount)),
            seasonality=weights, geography=geography or "domestic",
            merchant_group=merchant_group or None,
        ))
    return SpendInput(category_spend=tuple(lines))


def test_golden_syn_flat_baseline():
    golden = json.loads((GOLDENS_DIR / "golden_syn_flat_baseline.json").read_text())
    assert golden["card"] == "syn_flat"
    assert golden["seasonality"] == "uniform"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, exclusions=())  # syn_flat has no exclusions

    earning_rules, accruals, caps = _load_card_rules("syn_flat")
    assert caps == ()  # this card has none at all
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    # base (selector={}) is the ONLY rule -- every segment binds to it.
    assert {b.rule_key for b in bindings} == {"base"}

    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)  # no caps -> no-op
    assert not any(r.flags for r in final)  # both tickets even -> zero floor loss

    gross_reward_value = sum((r.reward for r in final), Decimal("0"))
    assert gross_reward_value == Decimal(str(golden["expected"]["gross_reward_value"]))

    thresholds = _load_thresholds("syn_flat")
    assert thresholds == ()  # this card has none
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_flat")
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


def test_golden_syn_upi_channel():
    golden = json.loads((GOLDENS_DIR / "golden_syn_upi_channel.json").read_text())
    assert golden["card"] == "syn_upi"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())

    exclusions = _load_exclusions("syn_upi")
    assert len(exclusions) == 1  # upi_fuel_rent
    eligible = apply_eligibility(normalised, exclusions)

    # UPI fuel/rent excluded from the reward view entirely at Stage 2.
    reward_categories = {(s.category, s.channel) for s in eligible.reward}
    assert ("fuel", "upi") not in reward_categories
    assert ("rent", "upi") not in reward_categories
    assert ("grocery", "upi") in reward_categories
    assert ("grocery", None) in reward_categories  # general spend untouched by the exclusion

    earning_rules, accruals, caps = _load_card_rules("syn_upi")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    # General (non-UPI) grocery matches no rule at all -- this card has only
    # the one channel=upi earning rule, no base/catch-all rule.
    bound_categories = {b.segment.category for b in bindings}
    assert bound_categories == {"grocery"}
    assert all(b.segment.channel == "upi" for b in bindings)

    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)
    assert not any(r.flags for r in final)  # no rounding_estimated, no cap_overflow (overflow=zero)

    gross_reward_points = sum((r.reward for r in final), Decimal("0"))
    assert gross_reward_points == Decimal("6000")  # 500pts/mo capped * 12

    currencies = _load_currencies()
    primary_routes = golden["assumptions"]["primary_route"]
    reward_valuations = value_accrual_results(final, accruals, currencies, primary_routes)
    gross_reward_rupees = sum((v.v_exp_rupees for v in reward_valuations), Decimal("0"))
    assert gross_reward_rupees == Decimal(str(golden["expected"]["gross_reward_value"]))

    thresholds = _load_thresholds("syn_upi")
    assert thresholds == ()  # this card has none
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_upi")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_rupees, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))


def test_golden_syn_points_portal_stacking():
    golden = json.loads((GOLDENS_DIR / "golden_syn_points_portal_stacking.json").read_text())
    assert golden["card"] == "syn_points"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())

    # hotels_domestic carries merchant_group=synth_portal (the "~" token in
    # its spend_annual key); dining carries none.
    portal_segments = [s for s in normalised.segments if s.merchant_group == "synth_portal"]
    assert all(s.category == "hotels_domestic" for s in portal_segments)
    assert sum((s.amount for s in portal_segments), Decimal("0")) == Decimal("1800000.00")

    eligible = apply_eligibility(normalised, exclusions=())  # syn_points has no exclusions

    earning_rules, accruals, caps = _load_card_rules("syn_points")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)

    # hotels_domestic (portal merchant group) binds BOTH base (non-stacking
    # winner) and portal_bonus (stacked alongside it); dining binds only base.
    hotels_rule_keys = {b.rule_key for b in bindings if b.segment.category == "hotels_domestic"}
    dining_rule_keys = {b.rule_key for b in bindings if b.segment.category == "dining"}
    assert hotels_rule_keys == {"base", "portal_bonus"}
    assert dining_rule_keys == {"base"}

    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)
    assert not any(r.flags for r in final)  # zero floor loss anywhere; cap_portal's zero overflow needs no re-rate

    # cap_portal (scope=rule_group:portal_accel) sums only portal_bonus's
    # reward -- base's reward on the same hotels_domestic spend is a
    # separate pool entirely and stays uncapped at 5,000/month * 12.
    base_total = sum((r.reward for r in final if r.rule_key == "base"), Decimal("0"))
    portal_bonus_total = sum((r.reward for r in final if r.rule_key == "portal_bonus"), Decimal("0"))
    assert base_total == Decimal("72000.00")  # (5,000 hotels + 1,000 dining) * 12
    assert portal_bonus_total == Decimal("180000.00")  # 15,000/month capped * 12

    gross_reward_points = sum((r.reward for r in final), Decimal("0"))
    assert gross_reward_points == Decimal("252000.00")

    currencies = _load_currencies()
    primary_routes = golden["assumptions"]["primary_route"]
    reward_valuations = value_accrual_results(final, accruals, currencies, primary_routes)
    gross_reward_rupees = sum((v.v_exp_rupees for v in reward_valuations), Decimal("0"))
    assert gross_reward_rupees == Decimal(str(golden["expected"]["gross_reward_value"]))

    thresholds = _load_thresholds("syn_points")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_points")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)
    assert fees.waived == golden["expected"]["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_rupees, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))


def test_golden_syn_waiver_divergence():
    golden = json.loads((GOLDENS_DIR / "golden_syn_waiver_divergence.json").read_text())
    assert golden["card"] == "syn_waiver"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())

    exclusions = _load_exclusions("syn_waiver")
    assert len(exclusions) == 2  # rent_no_waiver, fuel_no_rewards
    eligible = apply_eligibility(normalised, exclusions)

    # rent is excluded from the WAIVER view only -- still present in reward.
    reward_categories = {s.category for s in eligible.reward}
    waiver_categories = {s.category for s in eligible.waiver}
    assert reward_categories == {"grocery", "rent"}  # fuel excluded from reward
    assert waiver_categories == {"grocery", "fuel"}  # rent excluded from waiver

    earning_rules, accruals, caps = _load_card_rules("syn_waiver")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)
    # fuel never reaches Stage 3 at all -- excluded from the reward view at Stage 2.
    assert {b.segment.category for b in bindings} == {"grocery", "rent"}

    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)  # no caps on this card -> no-op
    assert not any(r.flags for r in final)  # all tickets integer rupees -> zero floor loss

    gross_reward_value = sum((r.reward for r in final), Decimal("0"))
    assert gross_reward_value == Decimal(str(golden["expected"]["gross_reward_value"]))

    thresholds = _load_thresholds("syn_waiver")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_waiver")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)
    # waiver-view spend (grocery+fuel = Rs2,16,000) stays below the
    # Rs3,00,000 tier even though reward-view spend (grocery+rent =
    # Rs4,20,000) clears it comfortably -- the divergence this golden exists
    # to exercise.
    assert fees.waived == golden["expected"]["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))


def test_golden_syn_fuel_surcharge():
    golden = json.loads((GOLDENS_DIR / "golden_syn_fuel_surcharge.json").read_text())
    assert golden["card"] == "syn_fuel"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, exclusions=())  # syn_fuel has no exclusions

    earning_rules, accruals, caps = _load_card_rules("syn_fuel")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)

    # fuel binds BOTH base (non-stacking winner) and fuel_refund (stacked
    # alongside it); grocery binds only base.
    fuel_rule_keys = {b.rule_key for b in bindings if b.segment.category == "fuel"}
    grocery_rule_keys = {b.rule_key for b in bindings if b.segment.category == "grocery"}
    assert fuel_rule_keys == {"base", "fuel_refund"}
    assert grocery_rule_keys == {"base"}

    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)
    assert not any(r.flags for r in final)  # zero floor loss anywhere, overflow=zero -> no cap_overflow entries

    gross_reward_value = sum((r.reward for r in final), Decimal("0"))
    assert gross_reward_value == Decimal(str(golden["expected"]["gross_reward_value"]))  # cashback_inr, v=1

    # Stage 10: surcharge (fuel only) and the waiver threshold.
    surcharges = _load_surcharges("syn_fuel")
    assert len(surcharges) == 1
    cost_of_surcharge = surcharge_cost(eligible.reward, surcharges)
    assert cost_of_surcharge == Decimal(str(golden["expected"]["surcharge_cost"]))

    thresholds = _load_thresholds("syn_fuel")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    card = next(c for c in CARDS if c["key"] == "syn_fuel")
    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)
    assert fees.waived == golden["expected"]["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee, surcharge_cost=cost_of_surcharge,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))


def test_golden_syn_travel_forex():
    golden = json.loads((GOLDENS_DIR / "golden_syn_travel_forex.json").read_text())
    assert golden["card"] == "syn_travel"

    spend_input = _parse_spend_annual(golden["spend_annual"])
    normalised = normalise(spend_input, AssumptionsSnapshot())
    eligible = apply_eligibility(normalised, exclusions=())  # syn_travel has no exclusions

    international_segments = [s for s in eligible.reward if s.geography == "international"]
    assert all(s.category == "international_flights" for s in international_segments)
    assert sum((s.amount for s in international_segments), Decimal("0")) == Decimal("120000.00")

    earning_rules, accruals, caps = _load_card_rules("syn_travel")
    bindings = match(NormalisedSpend(segments=eligible.reward), earning_rules)

    # intl REPLACES base on international spend (higher priority, not
    # stacking); domestic grocery only ever matches base.
    intl_rule_keys = {b.rule_key for b in bindings if b.segment.geography == "international"}
    domestic_rule_keys = {b.rule_key for b in bindings if b.segment.geography == "domestic"}
    assert intl_rule_keys == {"intl"}
    assert domestic_rule_keys == {"base"}

    uncapped = accrue_category_mode(bindings, accruals)
    final = apply_caps(uncapped, caps, earning_rules, accruals)  # this card has no caps -> no-op
    assert not any(r.flags for r in final)  # zero floor loss anywhere

    currencies = _load_currencies()
    primary_routes = golden["assumptions"]["primary_route"]
    reward_valuations = value_accrual_results(final, accruals, currencies, primary_routes)
    gross_reward_value = sum((v.v_exp_rupees for v in reward_valuations), Decimal("0"))
    assert gross_reward_value == Decimal(str(golden["expected"]["gross_reward_value"]))

    # Stage 10 (forex): this card's own zero markup, plus the contrast the
    # golden's hand computation calls out -- the SAME spend at the seed's
    # default 3.5% markup, purely illustrative, not part of syn_travel's NACV.
    card = next(c for c in CARDS if c["key"] == "syn_travel")
    forex_markup = Decimal(str(card["version"]["forex_markup"]))
    intl_total = international_spend_total(eligible.reward)
    cost_of_forex = forex_cost(intl_total, forex_markup)
    assert cost_of_forex == Decimal(str(golden["expected"]["forex_cost"]))
    assert forex_cost(intl_total, Decimal("0.035")) == Decimal("4956.000")  # the contrast

    thresholds = _load_thresholds("syn_travel")
    threshold_events = evaluate_thresholds(thresholds, milestone_segments=eligible.milestone, waiver_segments=eligible.waiver)

    joining_fee = Decimal(str(card["version"].get("joining_fee", 0)))
    annual_fee = Decimal(str(card["version"].get("annual_fee", 0)))
    fees = compute_fees(joining_fee, annual_fee, threshold_events)
    assert fees.waived == golden["expected"]["waiver_achieved"]
    assert fees.steady_fee == Decimal(str(golden["expected"]["fee_paid"]))

    nacv = assemble_nacv(
        gross_reward=gross_reward_value, milestone_value=Decimal("0"), benefit_value=Decimal("0"),
        steady_fee=fees.steady_fee, year1_fee=fees.year1_fee, forex_cost=cost_of_forex,
    )
    assert nacv.steady_state == Decimal(str(golden["expected"]["nacv_steady_state"]))
    assert nacv.year_1 == Decimal(str(golden["expected"]["nacv_year_1"]))
    assert nacv.three_year == Decimal(str(golden["expected"]["nacv_3yr"]))
