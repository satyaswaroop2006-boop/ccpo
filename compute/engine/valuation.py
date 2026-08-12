"""Stage 8 -- VALUE (Part A SS A.7, Part C SS C.2.9, SS C.4 Stage 8).

Converts a currency's total accrued points/cashback (summed across Stage
4-7's results) into rupees via A.7's five-stage pipeline: points ->
redemption route -> route ratio -> friction discount -> rupees --

    v(m, route) = ratio(m, route) . friction(m, route) - per_point_fee(m, route)

`ratio` is the route's own stored ratio, EXCEPT for `transfer`-type routes,
where A.7 states it explicitly as `transfer_ratio . partner_point_value`
instead -- computed, not read from a stored `ratio` field, per that
parenthetical. Missing friction defaults to 1.0 for every route type; the
registry itself is expected to store the A.7-suggested non-1.0 defaults
explicitly where they apply (portal 0.9, transfer 0.8 in the seed data) --
this stage does not hardcode route-type-specific defaults.

Three values per currency (A.7 SS8):
  - v_cons: max over "cash-like" routes (statement_credit, voucher)
  - v_opt:  max over every route, including travel_portal and transfer
  - v_exp:  the user's declared primary route for that currency -- what
    the engine actually prices rewards at, everywhere downstream. v_cons
    and v_opt are a display range only; NEVER used as the priced value.

`min_points` gates route eligibility: a route needing a minimum balance to
redeem at all is excluded from v_cons/v_opt's max() when the points total
falls short, and if it's the user's own v_exp choice, this stage values it
at Rs0 rather than silently pricing an unreachable route -- flagged
`min_points_not_met` so the reason is visible, not swallowed.

A currency with exactly one route (cashback_inr, always) needs no primary
route declaration -- there's no real choice to make. A currency with more
than one route requires one, and raises if it's missing or unknown.

Flat per-currency redemption fees (A.12's `RedemptionFees(c)`, distinct
from the per-point fee above) aren't modelled here -- no route in the
seed catalog carries one; see docs/DECISIONS.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.accrue import Accrual, AccrualResult

CASH_LIKE_ROUTE_TYPES = frozenset({"statement_credit", "voucher"})
VALID_ROUTE_TYPES = frozenset({"statement_credit", "voucher", "travel_portal", "transfer"})
DEFAULT_FRICTION = Decimal("1.0")


@dataclass(frozen=True)
class RedemptionRoute:
    key: str
    route_type: str
    ratio: Decimal | None = None  # rupees per point; unused for route_type="transfer"
    friction: Decimal | None = None  # None -> DEFAULT_FRICTION
    min_points: Decimal | None = None
    per_point_fee: Decimal = Decimal("0")
    transfer_partner: str | None = None
    transfer_ratio: Decimal | None = None
    partner_point_value: Decimal | None = None


@dataclass(frozen=True)
class RewardCurrency:
    key: str
    routes: tuple[RedemptionRoute, ...]


@dataclass(frozen=True)
class CurrencyValuation:
    currency_key: str
    points: Decimal
    v_exp_route_key: str
    v_exp_rupees: Decimal
    v_cons_rupees: Decimal | None
    v_opt_rupees: Decimal | None
    flags: tuple[str, ...] = ()


def _validate_route(route: RedemptionRoute) -> None:
    if route.route_type not in VALID_ROUTE_TYPES:
        raise ValueError(f"route {route.key!r}: unknown route_type {route.route_type!r}")
    if route.route_type == "transfer":
        if route.transfer_ratio is None or route.partner_point_value is None:
            raise ValueError(f"route {route.key!r}: transfer routes require transfer_ratio and partner_point_value")
    elif route.ratio is None:
        raise ValueError(f"route {route.key!r}: non-transfer routes require ratio")


def _effective_ratio(route: RedemptionRoute) -> Decimal:
    if route.route_type == "transfer":
        return route.transfer_ratio * route.partner_point_value
    return route.ratio


def _route_value_per_point(route: RedemptionRoute) -> Decimal:
    friction = route.friction if route.friction is not None else DEFAULT_FRICTION
    return _effective_ratio(route) * friction - route.per_point_fee


def _route_eligible(route: RedemptionRoute, points: Decimal) -> bool:
    return route.min_points is None or points >= route.min_points


def _resolve_primary_route(currency: RewardCurrency, primary_route_key: str | None) -> RedemptionRoute:
    if primary_route_key is not None:
        for route in currency.routes:
            if route.key == primary_route_key:
                return route
        raise ValueError(f"currency {currency.key!r}: unknown primary route {primary_route_key!r}")
    if len(currency.routes) == 1:
        return currency.routes[0]
    raise ValueError(
        f"currency {currency.key!r} has {len(currency.routes)} routes "
        f"({[r.key for r in currency.routes]}); a primary route must be declared"
    )


def primary_route_value_per_point(currency: RewardCurrency, primary_route_key: str | None = None) -> Decimal:
    """Rupees per point on the currency's declared primary route,
    unconditionally -- deliberately ignores `min_points` eligibility
    (that gate is Stage 8's concern for a specific *realized* point total,
    e.g. `value_currency`'s `min_points_not_met` flag; the optimiser's
    planning rate needs the route's per-point value on its own, not zeroed
    out just because a probe amount happens to fall under a transfer
    route's minimum). Used by `optimiser/allocate.py` to convert `engine.
    accrue.effective_rate`'s points/₹ into a rupee segment rate."""
    route = _resolve_primary_route(currency, primary_route_key)
    return _route_value_per_point(route)


def value_currency(currency: RewardCurrency, points: Decimal, primary_route_key: str | None = None) -> CurrencyValuation:
    for route in currency.routes:
        _validate_route(route)

    primary_route = _resolve_primary_route(currency, primary_route_key)

    flags: list[str] = []
    if _route_eligible(primary_route, points):
        v_exp_rupees = points * _route_value_per_point(primary_route)
    else:
        v_exp_rupees = Decimal("0")
        flags.append("min_points_not_met")

    eligible_routes = [r for r in currency.routes if _route_eligible(r, points)]
    cons_routes = [r for r in eligible_routes if r.route_type in CASH_LIKE_ROUTE_TYPES]
    best_cons = max(cons_routes, key=_route_value_per_point, default=None)
    best_opt = max(eligible_routes, key=_route_value_per_point, default=None)

    return CurrencyValuation(
        currency_key=currency.key,
        points=points,
        v_exp_route_key=primary_route.key,
        v_exp_rupees=v_exp_rupees,
        v_cons_rupees=points * _route_value_per_point(best_cons) if best_cons is not None else None,
        v_opt_rupees=points * _route_value_per_point(best_opt) if best_opt is not None else None,
        flags=tuple(flags),
    )


def value_accrual_results(
    accrual_results: Sequence[AccrualResult],
    accruals: dict[str, Accrual],
    currencies: dict[str, RewardCurrency],
    primary_routes: dict[str, str] | None = None,
) -> tuple[CurrencyValuation, ...]:
    """Groups Stage 4-7's accrual results by currency (via each rule's
    Accrual.currency) and values each currency's total once."""
    primary_routes = primary_routes or {}
    totals_by_currency: dict[str, Decimal] = {}
    for result in accrual_results:
        currency_key = accruals[result.rule_key].currency
        totals_by_currency[currency_key] = totals_by_currency.get(currency_key, Decimal("0")) + result.reward

    return tuple(
        value_currency(currencies[currency_key], points, primary_routes.get(currency_key))
        for currency_key, points in totals_by_currency.items()
    )
