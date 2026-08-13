"""Explainability surfaces (Part E SS E.12, SS37-39/SS26).

SS E.12 names five surfaces. One of them -- "marginal bands / Next-Best-
Spend" -- is already built: Phase 3's `POST /next-best-spend`
(`app/main.py`) is exactly SS E.12's own description ("evaluator-only
endpoint... for each held card and each of Delta in {1k,10k,50k}, exact
delta-value"), so it isn't rebuilt here. This module covers the other
four, each a thin layer over machinery Phase 2-4 already built (CLAUDE.md
rule 1 -- every rupee number below comes from `evaluate_card`, `allocate`,
or `repair`, never recomputed):

1. **`build_card_ledger`** -- "why this card": groups `EvaluateResult.
   nacv.trace` (already computed by `evaluate_card`) into SS37's
   reward/milestones/benefits/costs buckets. Inherits docs/DECISIONS.md
   #27's known fidelity gap: the trace has one lump "reward" line, not a
   per-rule base/bonus split (Stage 8 doesn't expose per-rule totals
   anywhere) -- documented in the docstring below, not silently smoothed
   over.
2. **`threshold_funding_report`** -- SS38's "which thresholds were funded
   vs left short and by how much." Reuses `breakpoints.compile_card_
   breakpoints` + `repair.pooled_spend_per_instance` (promoted from
   private to public this pass) directly -- the exact same pooling logic
   `repair()` already runs to find near-miss candidates, just reported
   for EVERY breakpoint instead of only the near-miss ones. Deliberately
   does NOT cover SS38's "which caps were hit (binding segment)" half --
   that needs Stage 5's per-window cap_state, which isn't returned
   anywhere in the engine today (the same gap #27 already names); caps
   aren't even compiled into breakpoints for THIS report (thresholds
   only), matching `repair.py`'s own established boundary (#71: "cap
   breakpoints are already optimal by construction in allocate.py's LP").
3. **`scan_driver` / `find_smallest_flip`** -- SS38's crossover scan and
   SS E.12's "what could change this?" 1-D sensitivity scan share one
   piece of machinery: vary one spend line across a grid, re-solve BOTH
   candidate portfolios (`allocate()` + `repair()`) at each point, find
   where the sign of their value difference flips. SS E.12 says "evaluator
   only -- no MILP" for the crossover scan; read literally that only
   applies to a single-card portfolio (nothing to allocate). For a
   multi-card portfolio, re-optimising the routing is the only way to get
   a value that's actually correct as spend shifts (freezing an old
   allocation's split ratios and just re-pricing it would silently
   understate value once one card's segments saturate differently) -- so
   this module always re-solves via `allocate`+`repair`, which happens to
   collapse to a pure evaluator call for the single-card case SS E.12's
   own example describes. Flagged as a judgment call, not a literal
   reading of "no MILP."
4. **`marginal_value_curve`** -- SS39's spend sweep on ONE card,
   evaluator-only (matches the spec literally: single-card, `evaluate_card`
   calls, no allocate/repair needed). Kinks are annotated by cross-
   referencing `breakpoints.compile_card_breakpoints`'s own compiled list
   -- see the module-level note on `_annualised_kinks` below for why a
   monthly-window cap's `threshold_spend` (already spend-domain per
   docs/DECISIONS.md #30, but expressed per WINDOW INSTANCE, e.g. "Rs20,000
   per calendar month") needs multiplying by `len(window_instances(...))`
   before it's comparable to an ANNUAL spend grid.

`SpendKey` (from `optimiser.allocate`) is reused throughout to name "which
spend line" a scan varies (category/channel/geography/merchant_group) --
the same identity `allocate.py`'s own LP variables are keyed on, not a
new type.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from engine.breakpoints import CardBreakpointInputs, compile_card_breakpoints
from engine.caps import window_instances
from engine.card_bundle import CardRuleBundle
from engine.evaluate import EvaluateAssumptions, EvaluateResult, evaluate_card
from engine.normalise import CategorySpend, SpendInput
from engine.valuation import RewardCurrency
from optimiser.allocate import SpendKey, allocate
from optimiser.repair import pooled_spend_per_instance, repair

# ---------------------------------------------------------------------------
# 1. Why this card -- SS37 ledger
# ---------------------------------------------------------------------------

LEDGER_BUCKETS = ("reward", "milestones", "benefits", "costs")
_TRACE_KIND_TO_BUCKET = {
    "reward": "reward", "milestone": "milestones", "benefit": "benefits",
    "fee": "costs", "forex": "costs", "surcharge": "costs",
}


@dataclass(frozen=True)
class LedgerBucket:
    label: str
    total: Decimal
    lines: tuple  # tuple[TraceLine, ...]


@dataclass(frozen=True)
class CardLedger:
    card_key: str
    buckets: tuple[LedgerBucket, ...]  # one per LEDGER_BUCKETS entry, in that order
    total: Decimal  # sum of all bucket totals -- reconciles exactly to nacv.steady_state


def build_card_ledger(card_key: str, result: EvaluateResult) -> CardLedger:
    lines_by_bucket: dict[str, list] = {b: [] for b in LEDGER_BUCKETS}
    for line in result.nacv.trace:
        lines_by_bucket[_TRACE_KIND_TO_BUCKET[line.kind]].append(line)

    buckets = tuple(
        LedgerBucket(
            label=label, total=sum((l.amount for l in lines_by_bucket[label]), Decimal("0")),
            lines=tuple(lines_by_bucket[label]),
        )
        for label in LEDGER_BUCKETS
    )
    return CardLedger(card_key=card_key, buckets=buckets, total=sum((b.total for b in buckets), Decimal("0")))


# ---------------------------------------------------------------------------
# 2. Threshold funding analysis -- SS38 (thresholds only, not caps; see
#    module docstring point 2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThresholdStatus:
    card_key: str
    threshold_key: str
    tier_index: int | None
    instance_months: tuple[int, ...]
    threshold_spend: Decimal
    pooled_spend: Decimal
    gap: Decimal  # threshold_spend - pooled_spend: > 0 = short by that much; <= 0 = funded (headroom = -gap)
    funded: bool
    near_miss: bool  # unfunded AND within the breakpoint's own repair buffer


def threshold_funding_report(bundle: CardRuleBundle, allocation) -> tuple[ThresholdStatus, ...]:
    breakpoints = compile_card_breakpoints(CardBreakpointInputs(card_key=bundle.card_key, thresholds=bundle.thresholds))
    statuses = []
    for bp in breakpoints:
        for instance_months, pooled in pooled_spend_per_instance(allocation, bundle.card_key, bundle.exclusions, bp):
            gap = bp.threshold_spend - pooled
            funded = gap <= 0
            near_miss = (not funded) and gap <= bp.buffer
            statuses.append(ThresholdStatus(
                card_key=bundle.card_key, threshold_key=bp.source_key, tier_index=bp.tier_index,
                instance_months=instance_months, threshold_spend=bp.threshold_spend, pooled_spend=pooled,
                gap=gap, funded=funded, near_miss=near_miss,
            ))
    return tuple(statuses)


# ---------------------------------------------------------------------------
# 3. Crossover scans -- SS38 ("Card A wins below X; Card B above") and
#    SS E.12's "what could change this?"
# ---------------------------------------------------------------------------

def _find_category_line(spend: SpendInput, driver: SpendKey) -> CategorySpend:
    for line in spend.category_spend:
        if (line.category, line.channel, line.geography, line.merchant_group) == (
            driver.category, driver.channel, driver.geography, driver.merchant_group,
        ):
            return line
    raise ValueError(f"optimiser/explain.py: no spend line matches driver {driver!r} in the supplied baseline spend")


def _spend_with_driver(spend: SpendInput, driver: SpendKey, value: Decimal) -> SpendInput:
    target = _find_category_line(spend, driver)
    lines = tuple(replace(target, annual_amount=value) if line is target else line for line in spend.category_spend)
    return SpendInput(category_spend=lines, upi_aggregate=spend.upi_aggregate)


def _pv_exact(bundles: Sequence[CardRuleBundle], currencies, spend, assumptions) -> Decimal:
    allocation = allocate(list(bundles), currencies, spend, assumptions)
    return repair(list(bundles), currencies, allocation, assumptions).valuation.pv_exact


@dataclass(frozen=True)
class ScanPoint:
    driver_value: Decimal
    pv_a: Decimal
    pv_b: Decimal


@dataclass(frozen=True)
class DriverScan:
    driver: SpendKey
    label_a: str
    label_b: str
    points: tuple[ScanPoint, ...]
    crossover: Decimal | None  # driver value where pv_a == pv_b (interpolated); None if no sign change in range
    winner_at_low: str
    winner_at_high: str


def _interpolate_crossover(points: Sequence[ScanPoint]) -> Decimal | None:
    for p in points:
        if p.pv_a == p.pv_b:
            return p.driver_value
    for prev, cur in zip(points, points[1:]):
        diff_prev, diff_cur = prev.pv_a - prev.pv_b, cur.pv_a - cur.pv_b
        if (diff_prev > 0) != (diff_cur > 0):
            frac = diff_prev / (diff_prev - diff_cur)
            return prev.driver_value + (cur.driver_value - prev.driver_value) * frac
    return None


def scan_driver(
    portfolio_a: Sequence[CardRuleBundle],
    portfolio_b: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    baseline_spend: SpendInput,
    driver: SpendKey,
    grid: Sequence[Decimal],
    assumptions: EvaluateAssumptions | None = None,
    label_a: str | None = None,
    label_b: str | None = None,
) -> DriverScan:
    assumptions = assumptions or EvaluateAssumptions()
    label_a = label_a or "+".join(sorted(b.card_key for b in portfolio_a))
    label_b = label_b or "+".join(sorted(b.card_key for b in portfolio_b))

    points = []
    for value in grid:
        spend = _spend_with_driver(baseline_spend, driver, value)
        pv_a = _pv_exact(portfolio_a, currencies, spend, assumptions)
        pv_b = _pv_exact(portfolio_b, currencies, spend, assumptions)
        points.append(ScanPoint(driver_value=value, pv_a=pv_a, pv_b=pv_b))

    crossover = _interpolate_crossover(points)
    winner_at_low = label_a if points[0].pv_a >= points[0].pv_b else label_b
    winner_at_high = label_a if points[-1].pv_a >= points[-1].pv_b else label_b

    return DriverScan(
        driver=driver, label_a=label_a, label_b=label_b, points=tuple(points),
        crossover=crossover, winner_at_low=winner_at_low, winner_at_high=winner_at_high,
    )


@dataclass(frozen=True)
class SensitivityResult:
    driver: SpendKey
    baseline_value: Decimal
    crossover: Decimal | None
    change_needed: Decimal | None  # abs(crossover - baseline_value); None if the scanned grid found no flip


def find_smallest_flip(
    portfolio_a: Sequence[CardRuleBundle],
    portfolio_b: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    baseline_spend: SpendInput,
    driver_grids: Sequence[tuple[SpendKey, Sequence[Decimal]]],
    assumptions: EvaluateAssumptions | None = None,
    label_a: str | None = None,
    label_b: str | None = None,
) -> tuple[SensitivityResult, ...]:
    results = []
    for driver, grid in driver_grids:
        baseline_value = _find_category_line(baseline_spend, driver).annual_amount
        scan = scan_driver(portfolio_a, portfolio_b, currencies, baseline_spend, driver, grid, assumptions, label_a, label_b)
        change_needed = abs(scan.crossover - baseline_value) if scan.crossover is not None else None
        results.append(SensitivityResult(
            driver=driver, baseline_value=baseline_value, crossover=scan.crossover, change_needed=change_needed,
        ))
    return tuple(sorted(results, key=lambda r: (r.change_needed is None, r.change_needed)))


# ---------------------------------------------------------------------------
# 4. Marginal value curve -- SS39
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CurvePoint:
    driver_value: Decimal
    nacv_steady_state: Decimal


@dataclass(frozen=True)
class MarginalValueCurve:
    card_key: str
    driver: SpendKey
    points: tuple[CurvePoint, ...]
    kinks: tuple[Decimal, ...]  # annualised breakpoint spend values that fall within the swept grid, sorted


def _annualised_kinks(bundle: CardRuleBundle, driver: CategorySpend, grid: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Every `Breakpoint.threshold_spend` is spend-domain (docs/DECISIONS.md
    #30) but scoped to its OWN window instance -- a calendar_month cap's
    Rs20,000 means "per month," not "per year." Comparing it against an
    ANNUAL spend grid needs multiplying by how many instances that window
    has per year (`len(window_instances(...))`: 12 for calendar_month, 1
    for anniversary_year, etc.) -- valid under uniform-seasonality
    splitting (each instance gets exactly total/N), which is what
    `_spend_with_driver` produces (it only ever replaces `annual_amount`,
    never touches `seasonality`). If the swept line carries a custom
    seasonality, that assumption doesn't hold and kinks are skipped
    entirely (no annotation) rather than mislabelled -- the curve's own
    points are unaffected either way, since those come from a real
    `evaluate_card` call, not this approximation."""
    if driver.seasonality is not None:
        return ()
    only_reward_caps = tuple(c for c in bundle.caps if c.measure == "reward")
    breakpoints = compile_card_breakpoints(CardBreakpointInputs(
        card_key=bundle.card_key, thresholds=bundle.thresholds, caps=only_reward_caps, accruals=bundle.accruals,
    ))
    grid_min, grid_max = min(grid), max(grid)
    annualised = {bp.threshold_spend * len(window_instances(bp.window)) for bp in breakpoints}
    return tuple(sorted(v for v in annualised if grid_min <= v <= grid_max))


def marginal_value_curve(
    bundle: CardRuleBundle,
    currencies: dict[str, RewardCurrency],
    baseline_spend: SpendInput,
    driver: SpendKey,
    grid: Sequence[Decimal],
    assumptions: EvaluateAssumptions | None = None,
) -> MarginalValueCurve:
    assumptions = assumptions or EvaluateAssumptions()
    driver_line = _find_category_line(baseline_spend, driver)

    points = []
    for value in grid:
        spend = _spend_with_driver(baseline_spend, driver, value)
        result = evaluate_card(bundle, currencies, spend, assumptions)
        points.append(CurvePoint(driver_value=value, nacv_steady_state=result.nacv.steady_state))

    kinks = _annualised_kinks(bundle, driver_line, grid)
    return MarginalValueCurve(card_key=bundle.card_key, driver=driver, points=tuple(points), kinks=kinks)
