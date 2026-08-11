"""Stage 10 -- COSTS (Part A SS A.6, SS A.10, SS A.11, Part C SS C.4 Stage 10).

Three independent cost lines, each a direct transcription of its formula:

  Fees (A.6): waiver achievement comes straight from Stage 6-7's actual
  waive_fee ThresholdEvents (not re-derived here) --
    SteadyFee(c) = F_annual . (1+g) . (1-w)
    Year1Fee(c)  = (F_join + F_annual . (1-w)) . (1+g)
  GST sits inside the waiver bracket -- a waived fee is never grossed up.

  Forex (A.10): ForexCost(c) = m(c) . (1+g) . international_spend. Stage
  1's category-mode grid has no geography dimension yet (the same gap
  noted for Stage 3's syn_travel "intl" rule -- docs/DECISIONS.md #4), so
  international_spend is a direct caller-supplied parameter here, not
  derived from segments. syn_travel's forex_markup=0 (zero-forex card)
  makes this Rs0 regardless of the amount, by construction.

  Surcharges (A.11): SurchargeCost(c) = Sum over each surcharge rule's
  selector-matched spend, rate . (1+gst_on_surcharge). No conflict
  resolution between multiple surcharge rules (unlike earning rules) --
  A.11 doesn't describe one, so each independently sums whatever it
  matches. Surcharge WAIVERS (syn_fuel) are modelled entirely outside this
  stage, as an ordinary capped earning rule refunding the surcharge
  through Stages 3-5 (C.9 Example 10's own framing: "surcharge waivers are
  just capped negative-cost rules") -- costs.py charges the flat,
  unconditional rate and never needs to know a waiver exists.

GST_RATE=0.18 is a fixed constant, not a per-card parameter: no card in
the seed catalog overrides it, and it matches golden_syn_ecom_basic.json's
own hand computation (joining fee 500 * 1.18 = 590) exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.match import Selector
from engine.normalise import SpendSegment
from engine.thresholds import ThresholdEvent

GST_RATE = Decimal("0.18")


def _selector_matches(selector: Selector, segment: SpendSegment) -> bool:
    if selector.categories is not None and segment.category not in selector.categories:
        return False
    if selector.channels is not None and segment.channel not in selector.channels:
        return False
    if selector.merchant_groups is not None and segment.merchant_group not in selector.merchant_groups:
        return False
    return True


@dataclass(frozen=True)
class Surcharge:
    key: str
    selector: Selector
    rate: Decimal
    gst_on_surcharge: Decimal = GST_RATE


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


def surcharge_cost(segments: Sequence[SpendSegment], surcharges: Sequence[Surcharge]) -> Decimal:
    total = Decimal("0")
    for surcharge in surcharges:
        matching_spend = sum(
            (s.amount for s in segments if _selector_matches(surcharge.selector, s)), Decimal("0")
        )
        total += surcharge.rate * (1 + surcharge.gst_on_surcharge) * matching_spend
    return total
