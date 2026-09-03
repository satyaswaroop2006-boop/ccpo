"""Stage 10 -- COSTS (Part A SS A.6, SS A.10, SS A.11, SS A.12, Part C SS C.4 Stage 10).

Four independent cost lines, each a direct transcription of its formula:

  Fees (A.6): waiver achievement comes straight from Stage 6-7's actual
  waive_fee ThresholdEvents (not re-derived here) --
    SteadyFee(c) = F_annual . (1+g) . (1-w)
    Year1Fee(c)  = (F_join + F_annual . (1-w)) . (1+g)
  GST sits inside the waiver bracket -- a waived fee is never grossed up.

  Forex (A.10): ForexCost(c) = m(c) . (1+g) . international_spend.
  `forex_cost` takes the international spend amount as a plain Decimal
  (a pure formula, like accrue_transaction); `international_spend_total`
  derives that amount from segments via SpendSegment.geography (Stage 1),
  the same pattern surcharge_cost already uses for its own selector-
  matched spend. syn_travel's forex_markup=0 (zero-forex card) makes this
  Rs0 regardless of the amount, by construction.

  Surcharges (A.11): SurchargeCost(c) = Sum over each surcharge rule's
  selector-matched spend, rate . (1+gst_on_surcharge). No conflict
  resolution between multiple surcharge rules (unlike earning rules) --
  A.11 doesn't describe one, so each independently sums whatever it
  matches. Surcharge WAIVERS come in two shapes, deliberately NOT unified
  into one mechanism (docs/DECISIONS.md #131/#132):
    - syn_fuel's shape (C.9 Example 10): an ordinary capped earning rule
      refunding the surcharge through Stages 3-5, when the surcharged
      category is otherwise fully reward-eligible. Unaffected by this
      module.
    - `Surcharge.waiver` (this module, Phase 5 Task B): computed directly
      here, against the SAME raw matched spend the surcharge itself uses
      -- required when the surcharged category is ALSO excluded from
      Stage 2's "rewards" view (CASHBACK SBI's real fuel exclusion),
      which makes the earning-rule shape permanently inert (proven
      empirically, not assumed -- a refund-shaped earning_rule can only
      ever see `eligible.reward`, and Stage 2 already stripped every
      fuel segment out of it). A waiver isn't a "reward" in C.2.5's
      sense, so it was never correctly gated by that mask in the first
      place; computing it here, alongside the surcharge it offsets,
      sidesteps the conflict instead of inventing a fourth eligibility
      mask to route around it.

  Redemption fees (A.12): RedemptionFees(c) = Sum over each priced
  currency's route flat_redemption_fee(route) . (1+g) . redemptions_per_
  year(currency), only for currencies that actually earned something to
  redeem (points<=0 -> nothing redeemed -> no fee). `flat_redemption_fee`
  is a flat, per-REQUEST charge (SBI Card PRIME's real MITC p.31 fact:
  "Rs.99 ... charged only once as per batch processed in a day
  irrespective of no. of items redeemed") -- fundamentally NOT the same
  shape as `RedemptionRoute.per_point_fee` (which scales with points and
  is already priced inside Stage 8's own per-point value). Annual
  modelling has no visibility into how many separate redemption requests
  a cardholder actually makes in a year -- `redemptions_per_year` is
  therefore a genuine usage-frequency ASSUMPTION (default 1/year per
  currency), the same registry/scenario status as Need/utilisation/
  friction elsewhere (docs/DECISIONS.md #22), never a T&C-cited fact.
  Deferred since #19/#29 (Phase 2) -- no route in the seed catalog ever
  carried one; closed once SBI Card PRIME needed it for real.

GST_RATE=0.18 is a fixed constant, not a per-card parameter: no card in
the seed catalog overrides it, and it matches golden_syn_ecom_basic.json's
own hand computation (joining fee 500 * 1.18 = 590) exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.caps import VALID_WINDOW_KINDS, Window, window_flags, window_instances
from engine.match import Selector, UNSUPPORTED_SELECTOR_FIELDS, selector_matches
from engine.normalise import SpendSegment
from engine.thresholds import ThresholdEvent
from engine.valuation import CurrencyValuation, RewardCurrency

GST_RATE = Decimal("0.18")
DEFAULT_REDEMPTIONS_PER_YEAR = Decimal("1")


@dataclass(frozen=True)
class SurchargeWaiver:
    """A capped rebate on a Surcharge's own rate (Part A SS A.11: "fuel
    surcharge waivers set sigma=0 up to the waiver's own monthly cap"),
    evaluated directly against the surcharge's own matched (raw) spend --
    see module docstring for why this lives here rather than as an
    earning_rule for cards like CASHBACK SBI.

    `rate` is the waived portion of the surcharge's own rate -- almost
    always equal to `Surcharge.rate` (a full waiver), kept as a separate
    field rather than implied so a PARTIAL waiver is representable without
    a future schema change. `cap_amount` bounds the PRE-GST waived amount
    per `cap_window` instance (an assumption, not a sourced fact -- the
    real bundle's "capped Rs100/statement" doesn't specify GST treatment
    either way; GST is then applied on top of whatever is actually waived,
    mirroring how the surcharge's own `gst_on_surcharge` is a separate
    multiplicative step, not folded into `rate`). `txn_min`/`txn_max` are
    accepted but NOT enforced in category mode -- same posture as Phase 5
    Task A's ExclusionSelector/match.Selector: no per-transaction data to
    test them against, so the waiver applies to the FULL matched spend in
    each window instance rather than silently approximating which slice
    of it would really qualify, flagged `txn_threshold_unenforced` instead."""

    rate: Decimal
    cap_amount: Decimal
    cap_window: Window
    txn_min: Decimal | None = None
    txn_max: Decimal | None = None


@dataclass(frozen=True)
class Surcharge:
    key: str
    selector: Selector
    rate: Decimal
    gst_on_surcharge: Decimal = GST_RATE
    waiver: SurchargeWaiver | None = None


@dataclass(frozen=True)
class SurchargeCostResult:
    total: Decimal
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeeResult:
    waived: bool
    steady_fee: Decimal
    year1_fee: Decimal


def compute_fees(
    joining_fee: Decimal,
    annual_fee: Decimal,
    threshold_events: Sequence[ThresholdEvent],
    fee: str = "annual",
) -> FeeResult:
    waived = any(e.payload.type == "waive_fee" and e.payload.fee == fee for e in threshold_events)
    charged_annual = Decimal("0") if waived else annual_fee
    steady_fee = charged_annual * (1 + GST_RATE)
    year1_fee = (joining_fee + charged_annual) * (1 + GST_RATE)
    return FeeResult(waived=waived, steady_fee=steady_fee, year1_fee=year1_fee)


def forex_cost(international_spend: Decimal, forex_markup: Decimal) -> Decimal:
    return forex_markup * (1 + GST_RATE) * international_spend


def international_spend_total(segments: Sequence[SpendSegment]) -> Decimal:
    return sum((s.amount for s in segments if s.geography == "international"), Decimal("0"))


def redemption_fees_cost(
    valuations: Sequence[CurrencyValuation],
    currencies: dict[str, RewardCurrency],
    redemptions_per_year: dict[str, Decimal] | None = None,
) -> Decimal:
    """A.12's RedemptionFees(c): flat_redemption_fee(route) . (1+g) .
    redemptions_per_year(currency), summed over every currency this card
    actually earned something in (points<=0 -> nothing to redeem -> no
    fee -- a currency nobody earned anything on this year isn't being
    redeemed FROM). Priced against `valuation.v_exp_route_key` -- the same
    route Stage 8 actually valued the currency's points through, not some
    other route on the same currency the card also happens to declare.

    `redemptions_per_year` is a sparse currency_key -> count override; a
    currency not named there defaults to `DEFAULT_REDEMPTIONS_PER_YEAR`
    (1/year) -- see module docstring for why this is a usage-frequency
    ASSUMPTION, never a sourced fact. A route with `flat_redemption_fee=0`
    (every route that's never declared one, i.e. every existing card)
    contributes exactly Rs0, so this is a zero-behaviour-change addition
    for every card that predates it."""
    redemptions_per_year = redemptions_per_year or {}
    total = Decimal("0")
    for valuation in valuations:
        if valuation.points <= 0:
            continue
        route = next(
            (r for r in currencies[valuation.currency_key].routes if r.key == valuation.v_exp_route_key), None,
        )
        if route is None or route.flat_redemption_fee == 0:
            continue
        n = redemptions_per_year.get(valuation.currency_key, DEFAULT_REDEMPTIONS_PER_YEAR)
        total += route.flat_redemption_fee * (1 + GST_RATE) * n
    return total


def validate_surcharge(surcharge: Surcharge) -> None:
    """Mirrors `match.validate_rule`/`eligibility.validate_exclusion` --
    a surcharge's selector is match.py's own `Selector` type (SS49), but
    unlike earning rules and exclusions, nothing ever validated it against
    `UNSUPPORTED_SELECTOR_FIELDS`. A genuinely separate gap from the
    card_bundle.py loader bug those two validators' own fix uncovered --
    surcharges never had a validator to fail silently in the first place.
    Public from the start (not module-private then promoted) -- built
    knowing `compute/ingest`'s lint tool needs to call it per-item. See
    docs/DECISIONS.md."""
    used_unsupported = [
        field for field in UNSUPPORTED_SELECTOR_FIELDS if getattr(surcharge.selector, field) is not None
    ]
    if used_unsupported:
        raise ValueError(
            f"surcharge {surcharge.key!r} selector uses field(s) {used_unsupported} that "
            "category-mode spend segments cannot be matched against"
        )
    if surcharge.waiver is not None:
        waiver = surcharge.waiver
        if waiver.cap_window.kind not in VALID_WINDOW_KINDS:
            raise ValueError(f"surcharge {surcharge.key!r} waiver: unknown window kind {waiver.cap_window.kind!r}")
        if waiver.rate > surcharge.rate:
            raise ValueError(
                f"surcharge {surcharge.key!r} waiver rate {waiver.rate} exceeds the surcharge's own rate {surcharge.rate}"
            )


def surcharge_cost(segments: Sequence[SpendSegment], surcharges: Sequence[Surcharge]) -> SurchargeCostResult:
    total = Decimal("0")
    flags: set[str] = set()

    for surcharge in surcharges:
        validate_surcharge(surcharge)
        matching_segments = [s for s in segments if selector_matches(surcharge.selector, s)]
        matching_spend = sum((s.amount for s in matching_segments), Decimal("0"))
        gross = surcharge.rate * (1 + surcharge.gst_on_surcharge) * matching_spend

        waived = Decimal("0")
        if surcharge.waiver is not None:
            waiver = surcharge.waiver
            if waiver.txn_min is not None or waiver.txn_max is not None:
                flags.add("txn_threshold_unenforced")
            flags.update(window_flags(waiver.cap_window))
            for instance_months in window_instances(waiver.cap_window):
                month_set = set(instance_months)
                instance_spend = sum((s.amount for s in matching_segments if s.month in month_set), Decimal("0"))
                instance_waived_base = min(waiver.rate * instance_spend, waiver.cap_amount)
                waived += instance_waived_base * (1 + surcharge.gst_on_surcharge)

        total += max(gross - waived, Decimal("0"))

    return SurchargeCostResult(total=total, flags=tuple(sorted(flags)))
