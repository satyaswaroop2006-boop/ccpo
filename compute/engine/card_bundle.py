"""Card-dict -> engine-dataclass loader (Part E SS E.0's `/evaluate` input
boundary).

A "card dict" is the raw JSON-shaped structure every card definition
already takes -- `seeds/synthetic_cards.py`'s `CARDS` entries today, and
(once `DATABASE_URL` is confirmed reachable) a Postgres-assembled dict of
the same shape tomorrow, per `supabase/migrations/0001_init.sql` /
`seeds/seed.py`'s insert order. `bundle_from_dict` is the ONE translation
from that shape into the engine's own dataclasses (`EarningRule`, `Cap`,
`Threshold`, `Exclusion`, `Benefit`, `Surcharge`) -- both data sources
funnel through this single function so they can never silently diverge in
interpretation the way two independent copies could.

This module was extracted verbatim from `tests/test_goldens.py`'s five
private `_load_*` adapter functions (built up incrementally across every
golden in Part C SS C.9) -- no behaviour changed, only promoted out of the
test file so `engine/evaluate.py` and the API layer can reuse it too. See
docs/DECISIONS.md's Phase 3 entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.accrue import Accrual
from engine.benefits import Benefit
from engine.caps import Cap, Window
from engine.costs import Surcharge, SurchargeWaiver
from engine.eligibility import Exclusion, ExclusionSelector
from engine.match import EarningRule, Selector
from engine.thresholds import Payload, Threshold, ThresholdBasis, Tier
from engine.valuation import RedemptionRoute, RewardCurrency


@dataclass(frozen=True)
class CardRuleBundle:
    """Everything Stages 2-10 need for one card_version, already translated
    into engine dataclasses. `caps` mixes reward- and spend-measure caps --
    callers filter by `.measure` for whichever downstream function
    (`apply_caps` vs `apply_incremental_bands`) they need, same as the
    golden adapter always did."""

    card_key: str
    currency_key: str
    joining_fee: Decimal
    annual_fee: Decimal
    forex_markup: Decimal
    earning_rules: tuple[EarningRule, ...]
    accruals: dict[str, Accrual]
    caps: tuple[Cap, ...]
    thresholds: tuple[Threshold, ...]
    exclusions: tuple[Exclusion, ...]
    benefits: dict[str, Benefit]
    surcharges: tuple[Surcharge, ...]


_SELECTOR_TUPLE_FIELDS = ("categories", "channels", "merchant_groups", "merchants", "networks")
_SELECTOR_INT_TUPLE_FIELDS = ("mcc_include", "mcc_exclude")
_SELECTOR_SCALAR_FIELDS = ("geography", "date_from", "date_to")
_SELECTOR_DECIMAL_FIELDS = ("txn_min", "txn_max")


def _selector_kwargs_from_dict(d: dict) -> dict:
    """Populates EVERY Part C SS C.2.1 selector field present in the raw
    dict, not just the four (categories/channels/merchant_groups/
    geography) the engine's matching logic (`match.selector_matches`,
    `eligibility._selector_matches`) actually reads. This is deliberate:
    `match._validate_rule` and `eligibility._validate_exclusion` already
    exist specifically to raise when a selector uses an unsupported field
    (mcc_include, txn_max, etc.) -- but only if that field actually reaches
    the dataclass. Before this fix, both loader functions silently dropped
    those fields during dict->dataclass translation, so the validators
    always saw an all-supported (or all-None) selector and never fired --
    a real bug, not by design: an ingestion bundle using mcc_include on an
    exclusion would silently produce an all-None ExclusionSelector, which
    `eligibility._selector_matches` treats as MATCHES EVERYTHING, rather
    than the loud raise the validators were built to produce. Discovered
    while building `compute/ingest`'s lint tool; see docs/DECISIONS.md."""
    kwargs = {}
    for field in _SELECTOR_TUPLE_FIELDS:
        if d.get(field) is not None:
            kwargs[field] = tuple(d[field])
    for field in _SELECTOR_INT_TUPLE_FIELDS:
        if d.get(field) is not None:
            kwargs[field] = tuple(d[field])
    for field in _SELECTOR_SCALAR_FIELDS:
        if d.get(field) is not None:
            kwargs[field] = d[field]
    for field in _SELECTOR_DECIMAL_FIELDS:
        if d.get(field) is not None:
            kwargs[field] = Decimal(str(d[field]))
    return kwargs


def _selector_from_dict(d: dict) -> Selector:
    return Selector(**_selector_kwargs_from_dict(d))


def _exclusion_selector_from_dict(d: dict) -> ExclusionSelector:
    return ExclusionSelector(**_selector_kwargs_from_dict(d))


def _accrual_from_dict(d: dict, currency: str) -> Accrual:
    if d["type"] == "percentage":
        return Accrual(type="percentage", currency=currency, rate=Decimal(str(d["rate"])), rounding=d["rounding"])
    if d["type"] == "per_unit":
        return Accrual(
            type="per_unit", currency=currency,
            unit_amount=Decimal(str(d["unit_amount"])), points_per_unit=Decimal(str(d["points_per_unit"])),
            rounding=d["rounding"],
        )
    raise ValueError(f"card_bundle: unsupported accrual type {d['type']!r}")


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


def _surcharge_waiver_from_dict(d: dict) -> SurchargeWaiver:
    """Phase 5 Task B (docs/DECISIONS.md #132) -- CASHBACK SBI's own
    ingestion bundle already used exactly these field names
    (rate/txn_min/txn_max/cap_amount/cap_window) before this loader
    existed, drafted directly against Part A SS A.11's prose; no renaming
    needed to match `engine.costs.SurchargeWaiver`."""
    return SurchargeWaiver(
        rate=Decimal(str(d["rate"])),
        cap_amount=Decimal(str(d["cap_amount"])),
        cap_window=Window(kind=d["cap_window"]["kind"], alignment=d["cap_window"].get("alignment")),
        txn_min=Decimal(str(d["txn_min"])) if d.get("txn_min") is not None else None,
        txn_max=Decimal(str(d["txn_max"])) if d.get("txn_max") is not None else None,
    )


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


def currencies_from_dicts(currency_dicts: list[dict]) -> dict[str, RewardCurrency]:
    return {
        c["key"]: RewardCurrency(key=c["key"], routes=tuple(_route_from_dict(r) for r in c["routes"]))
        for c in currency_dicts
    }


def bundle_from_dict(card: dict) -> CardRuleBundle:
    """Translates one raw card dict (`seeds/synthetic_cards.py` shape, or a
    Postgres row assembly of the same shape) into a `CardRuleBundle`."""
    cap_defs = {c["key"]: c for c in card.get("caps", [])}

    # The raw schema has no tier_mode field at all (not even a seed.py
    # INSERT column) -- incremental rules are only identifiable by their
    # rule_group containing a spend-measure cap SOMEWHERE in it. Inferred
    # here rather than trusting an explicit field that doesn't exist yet.
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
        # seed.py stamps accrual["currency"] = card["currency"] before
        # inserting (the raw fixture dicts always carry currency: None) --
        # mirrored here so this matches what actually gets seeded.
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

    thresholds = tuple(_threshold_from_dict(t) for t in card.get("thresholds", []))

    exclusions = tuple(
        Exclusion(
            key=e["key"], selector=_exclusion_selector_from_dict(e["selector"]),
            excluded_from=tuple(e["excluded_from"]), note=e.get("note"),
        )
        for e in card.get("exclusions", [])
    )

    benefits = {b["key"]: _benefit_from_dict(b) for b in card.get("benefits", [])}

    surcharges = tuple(
        Surcharge(
            key=s["key"], selector=_selector_from_dict(s["selector"]),
            rate=Decimal(str(s["rate"])), gst_on_surcharge=Decimal(str(s["gst_on_surcharge"])),
            waiver=_surcharge_waiver_from_dict(s["waiver"]) if s.get("waiver") is not None else None,
        )
        for s in card.get("surcharges", [])
    )

    v = card.get("version", {})
    return CardRuleBundle(
        card_key=card["key"],
        currency_key=card["currency"],
        joining_fee=Decimal(str(v.get("joining_fee", 0))),
        annual_fee=Decimal(str(v.get("annual_fee", 0))),
        forex_markup=Decimal(str(v.get("forex_markup", "0.035"))),
        earning_rules=tuple(earning_rules),
        accruals=accruals,
        caps=tuple(caps),
        thresholds=thresholds,
        exclusions=exclusions,
        benefits=benefits,
        surcharges=surcharges,
    )
