"""Stage 9 -- BENEFITS (Part A SS A.8, SS A.9, Part C SS C.2.8, Stage 9).

Card-level utilisation (A.8): converts a Benefit definition into rupees.
  - countable (lounge visits, movie tickets, free nights): BenefitValue =
    min(Need(b), Entitle(c,b)) . V(b). Entitle is either flat (entitlement
    x number of entitlement_window instances in the year, when there's no
    qualification gate) or threshold-gated (summed from Stage 6-7's
    grant_entitlement ThresholdEvents for this benefit key -- syn_lounge's
    quarterly gate: only qualifying quarters contribute their quantity).
  - voucher: BenefitValue = face_value . utilisation . friction, once per
    matching grant_voucher ThresholdEvent (a voucher granted by two
    different tiers is two vouchers).
  - flat_perk: BenefitValue = face_value . utilisation, unconditional
    (no card in the seed catalog uses this kind; implemented per A.8's
    formula and hand-tested, since it's simple and directly spec'd).

Need, V(b) (unit_value), utilisation, and friction are all registry/user
assumptions (C.7) -- this stage consumes them as parameters, it doesn't
own or default them, same posture as Stage 1's ticket_size and Stage 8's
route friction.

Portfolio-level dedup (A.9): the *value* ceiling across several cards
offering the same benefit is min(Need(b), sum of every card's Entitle(c,b))
. V(b) -- because every unit is worth the same V(b) regardless of which
card provides it, that's the value-maximising total regardless of the
specific per-card split. A.9 itself says the split ("allocated to whichever
card's quota the optimiser draws down") is an optimiser decision (Phase 4,
not built) when qualification gates make drawing down one card's quota
cost something -- this stage computes the achievable value ceiling, not a
specific allocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.caps import Window, window_instances
from engine.thresholds import ThresholdEvent

VALID_KIND = frozenset({"countable", "flat_perk", "voucher"})


@dataclass(frozen=True)
class Benefit:
    key: str
    kind: str
    unit_label: str | None = None
    entitlement: Decimal | None = None  # countable only
    entitlement_window: Window | None = None  # countable only
    qualification_threshold_key: str | None = None  # countable only, optional
    face_value: Decimal | None = None  # voucher / flat_perk


@dataclass(frozen=True)
class BenefitValuation:
    benefit_key: str
    entitlement_units: Decimal | None  # countable only
    consumed_units: Decimal | None  # countable only
    value_rupees: Decimal
    flags: tuple[str, ...] = ()


def _validate_benefit(benefit: Benefit) -> None:
    if benefit.kind not in VALID_KIND:
        raise ValueError(f"benefit {benefit.key!r}: unknown kind {benefit.kind!r}")
    if benefit.kind == "countable":
        if benefit.entitlement is None or benefit.entitlement_window is None:
            raise ValueError(f"benefit {benefit.key!r}: countable benefits require entitlement and entitlement_window")
    elif benefit.face_value is None:
        raise ValueError(f"benefit {benefit.key!r}: {benefit.kind} benefits require face_value")


def _gated_entitlement(benefit: Benefit, events: Sequence[ThresholdEvent]) -> Decimal:
    total = Decimal("0")
    for event in events:
        if event.payload.type == "grant_entitlement" and event.payload.benefit == benefit.key:
            total += Decimal(event.payload.quantity)
    return total


def _flat_entitlement(benefit: Benefit) -> Decimal:
    return benefit.entitlement * len(window_instances(benefit.entitlement_window))


def value_countable_benefit(
    benefit: Benefit,
    events: Sequence[ThresholdEvent],
    need: Decimal,
    unit_value: Decimal,
) -> BenefitValuation:
    _validate_benefit(benefit)
    if benefit.kind != "countable":
        raise ValueError(f"benefit {benefit.key!r}: not a countable benefit")

    if benefit.qualification_threshold_key is not None:
        entitle = _gated_entitlement(benefit, events)
    else:
        entitle = _flat_entitlement(benefit)

    consumed = min(need, entitle)
    return BenefitValuation(
        benefit_key=benefit.key,
        entitlement_units=entitle,
        consumed_units=consumed,
        value_rupees=consumed * unit_value,
    )


def value_voucher_benefit(
    benefit: Benefit,
    events: Sequence[ThresholdEvent],
    utilisation: Decimal,
    friction: Decimal,
) -> BenefitValuation:
    _validate_benefit(benefit)
    if benefit.kind != "voucher":
        raise ValueError(f"benefit {benefit.key!r}: not a voucher benefit")

    grants = sum(
        1 for e in events if e.payload.type == "grant_voucher" and e.payload.benefit == benefit.key
    )
    value = benefit.face_value * utilisation * friction * grants
    return BenefitValuation(
        benefit_key=benefit.key,
        entitlement_units=None,
        consumed_units=None,
        value_rupees=value,
        flags=() if grants else ("not_granted",),
    )


def value_flat_perk_benefit(benefit: Benefit, utilisation: Decimal) -> BenefitValuation:
    _validate_benefit(benefit)
    if benefit.kind != "flat_perk":
        raise ValueError(f"benefit {benefit.key!r}: not a flat_perk benefit")

    return BenefitValuation(
        benefit_key=benefit.key,
        entitlement_units=None,
        consumed_units=None,
        value_rupees=benefit.face_value * utilisation,
    )


@dataclass(frozen=True)
class CardEntitlement:
    card_key: str
    entitle: Decimal


@dataclass(frozen=True)
class PortfolioBenefitValuation:
    benefit_key: str
    total_entitlement: Decimal
    consumed_units: Decimal
    value_rupees: Decimal


def deduplicate_portfolio_benefit(
    benefit_key: str,
    need: Decimal,
    card_entitlements: Sequence[CardEntitlement],
    unit_value: Decimal,
) -> PortfolioBenefitValuation:
    total_entitlement = sum((ce.entitle for ce in card_entitlements), Decimal("0"))
    consumed = min(need, total_entitlement)
    return PortfolioBenefitValuation(
        benefit_key=benefit_key,
        total_entitlement=total_entitlement,
        consumed_units=consumed,
        value_rupees=consumed * unit_value,
    )
