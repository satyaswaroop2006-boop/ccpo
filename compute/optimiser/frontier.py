"""Efficient frontier + the transparent size-recommendation rule (Part E
SS E.9, the C.8 promise).

Two pieces, both pure post-processing over `optimiser.enumerate.
enumerate_subsets`'s output -- no new financial logic (CLAUDE.md rule 1):

1. `build_frontier` -- "SELECT size, max(pv_exact) ... GROUP BY size":
   the best subset found at each enumerated size, exact values only.
2. The recommendation checklist -- walks the frontier size by size and
   applies SS E.9's three tests to every step n -> n+1:

     T1 MATERIALITY   ΔV >= max(abs_floor, rel_pct . V(n))
     T2 FEE-AT-RISK   ΔGrossBenefit / ΔF >= 1.5  OR  ΔF <= Rs1,000
     T3 SCENARIO FLOOR  ΔV_low_spend >= 0 (only when scenario data is supplied)

   The largest n <= n_tol for which every step 1->2->...->n passes is
   `recommended_size`. A step that fails stops the walk outright (SS E.9:
   "never silently expand" past a failure); a step that would otherwise
   pass but sits beyond n_tol stops the walk too, with `capped_by_tolerance`
   set so the caller can render SS E.9's "a 4th card would add Rs6,400/yr,
   but you asked for <=3" message instead of silently truncating.

**Scope for this pass** (docs/DECISIONS.md, Phase 4 frontier/classify
entry):
- **T3 is optional, not stubbed out.** `optimiser/scenarios.py` (SS E.11,
  the Low/Expected/High spend sweep) doesn't exist yet -- it's later in
  the build order than this module. `build_frontier` takes an optional
  `low_spend_pv_by_subset_key` map; when omitted, every step's `t3_pass`
  is `None` ("not evaluated") and the overall pass/fail decision is made
  from T1+T2 alone. This is a real, documented scope reduction, not a
  silent default -- `RecommendationStep.t3_pass is None` is how a caller
  tells "not evaluated" apart from "evaluated and passed."
- **ΔF is a portfolio-total delta, not literally "the new card's fee."**
  SS E.9's own worked example ("3rd card: ... fees +Rs1,500 ...") reads as
  if step n->n+1 always adds exactly one card, but the frontier's winning
  subset at size n+1 need not be a superset of the winner at size n (a
  totally different subset can simply score higher) -- full-sweep
  enumeration gives no nesting guarantee. `ΔF = TotalGrossFee(winner(n+1))
  - TotalGrossFee(winner(n))` is well-defined regardless of nesting and
  collapses to the single-card reading whenever the frontier IS nested
  (the common case), so nothing is lost when it is.
- **"Gross" fee = annual_fee . (1+GST), pre-waiver** -- SS E.9 says
  "gross (pre-waiver)" but doesn't say whether GST is folded in. Read as
  "the amount you're actually on the hook for absent a waiver" (matches
  `engine.costs.compute_fees`'s own `steady_fee` formula with the waiver
  term forced off), not the bare pre-tax sticker price.
- **ΔGrossBenefit** sums each card's `gross_reward_value + milestone_value
  + benefit_value` (the three terms `engine.assemble.assemble_nacv` adds
  before subtracting fees/forex/surcharge) -- i.e. NACV with fees added
  back, not NACV itself. This needs `SubsetResult.card_results`, so this
  slice also added that field to `optimiser/enumerate.py`'s `SubsetResult`
  (populated from `repair.RepairResult.valuation.card_results`, which
  `enumerate_subsets` was already computing and discarding).
- **`abs_floor`/`rel_pct`/`fee_cover_ratio`/`fee_de_minimis` are new
  assumption-registry defaults** (SS E.9: "all three parameters live in
  the assumptions registry (C.7), never hidden") -- flagged in
  docs/DECISIONS.md for Satya's sign-off, same posture as every other new
  default introduced so far (e.g. `upi_category_mix`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.card_bundle import CardRuleBundle
from engine.costs import GST_RATE
from optimiser.enumerate import SubsetResult

DEFAULT_ABS_FLOOR = Decimal("2000")
DEFAULT_REL_PCT = Decimal("0.03")
DEFAULT_FEE_COVER_RATIO = Decimal("1.5")
DEFAULT_FEE_DE_MINIMIS = Decimal("1000")


@dataclass(frozen=True)
class FrontierPoint:
    size: int
    subset_key: str
    card_keys: tuple[str, ...]
    pv_exact: Decimal


@dataclass(frozen=True)
class RecommendationStep:
    size: int  # the "n+1" side of this n -> n+1 step
    delta_v: Decimal
    t1_pass: bool
    t1_threshold: Decimal
    delta_fee: Decimal
    delta_gross_benefit: Decimal
    fee_cover_ratio: Decimal | None  # None when delta_fee <= 0 (T2 trivially passes)
    t2_pass: bool
    low_spend_delta_v: Decimal | None  # None when no scenario data was supplied
    t3_pass: bool | None  # None = "not evaluated", distinct from evaluated-and-failed
    passes: bool


@dataclass(frozen=True)
class FrontierResult:
    points: tuple[FrontierPoint, ...]  # one winner per enumerated size, size-ordered
    steps: tuple[RecommendationStep, ...]  # one per consecutive size pair actually walked
    recommended_size: int
    capped_by_tolerance: bool  # True iff the walk stopped only because size > n_tol, not a failed test


def _gross_annual_fee(bundle: CardRuleBundle) -> Decimal:
    return bundle.annual_fee * (1 + GST_RATE)


def _total_gross_fee(card_keys: Sequence[str], bundles_by_key: dict[str, CardRuleBundle]) -> Decimal:
    return sum((_gross_annual_fee(bundles_by_key[k]) for k in card_keys), Decimal("0"))


def _gross_benefit(subset: SubsetResult) -> Decimal:
    return sum(
        (r.gross_reward_value + r.milestone_value + r.benefit_value for r in subset.card_results.values()),
        Decimal("0"),
    )


def build_frontier(
    results: Sequence[SubsetResult],
    bundles: Sequence[CardRuleBundle],
    n_tol: int | None = None,
    abs_floor: Decimal = DEFAULT_ABS_FLOOR,
    rel_pct: Decimal = DEFAULT_REL_PCT,
    fee_cover_ratio: Decimal = DEFAULT_FEE_COVER_RATIO,
    fee_de_minimis: Decimal = DEFAULT_FEE_DE_MINIMIS,
    low_spend_pv_by_subset_key: dict[str, Decimal] | None = None,
) -> FrontierResult:
    if not results:
        raise ValueError("optimiser/frontier.py: build_frontier needs at least one enumerated subset")

    bundles_by_key = {b.card_key: b for b in bundles}

    best_by_size: dict[int, SubsetResult] = {}
    for r in results:
        current = best_by_size.get(r.size)
        if current is None or r.pv_exact > current.pv_exact:
            best_by_size[r.size] = r

    sizes = sorted(best_by_size)
    points = tuple(
        FrontierPoint(size=s, subset_key=best_by_size[s].subset_key, card_keys=best_by_size[s].card_keys, pv_exact=best_by_size[s].pv_exact)
        for s in sizes
    )

    steps: list[RecommendationStep] = []
    recommended_size = sizes[0]
    capped_by_tolerance = False

    for i in range(1, len(sizes)):
        prev_size, size = sizes[i - 1], sizes[i]
        if size != prev_size + 1:
            break  # gap in the frontier -- can't evaluate a step without both endpoints

        prev, cur = best_by_size[prev_size], best_by_size[size]

        delta_v = cur.pv_exact - prev.pv_exact
        t1_threshold = max(abs_floor, rel_pct * prev.pv_exact)
        t1_pass = delta_v >= t1_threshold

        delta_fee = _total_gross_fee(cur.card_keys, bundles_by_key) - _total_gross_fee(prev.card_keys, bundles_by_key)
        delta_gross_benefit = _gross_benefit(cur) - _gross_benefit(prev)
        if delta_fee <= 0:
            fee_ratio, t2_pass = None, True
        else:
            fee_ratio = delta_gross_benefit / delta_fee
            t2_pass = fee_ratio >= fee_cover_ratio or delta_fee <= fee_de_minimis

        low_delta, t3_pass = None, None
        if low_spend_pv_by_subset_key is not None:
            prev_low = low_spend_pv_by_subset_key.get(prev.subset_key)
            cur_low = low_spend_pv_by_subset_key.get(cur.subset_key)
            if prev_low is not None and cur_low is not None:
                low_delta = cur_low - prev_low
                t3_pass = low_delta >= 0

        step_passes = t1_pass and t2_pass and (t3_pass is not False)
        steps.append(RecommendationStep(
            size=size, delta_v=delta_v, t1_pass=t1_pass, t1_threshold=t1_threshold,
            delta_fee=delta_fee, delta_gross_benefit=delta_gross_benefit, fee_cover_ratio=fee_ratio,
            t2_pass=t2_pass, low_spend_delta_v=low_delta, t3_pass=t3_pass, passes=step_passes,
        ))

        if not step_passes:
            break
        if n_tol is not None and size > n_tol:
            capped_by_tolerance = True
            break
        recommended_size = size

    return FrontierResult(points=points, steps=tuple(steps), recommended_size=recommended_size, capped_by_tolerance=capped_by_tolerance)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _signed_rupees(amount: Decimal) -> str:
    sign = "-" if amount < 0 else "+"
    return f"{sign}Rs{abs(amount):,.0f}"


def format_step(step: RecommendationStep) -> str:
    """SS E.9's own worked example, plain-rupee, ASCII-only (no unicode
    checkmarks -- this is printed straight to a Windows console in tests
    and, per CLAUDE.md, shown to Satya directly)."""
    def mark(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    parts = [f"{_ordinal(step.size)} card: {_signed_rupees(step.delta_v)}/yr [{mark(step.t1_pass)} material]"]
    if step.delta_fee > 0:
        cover = f"{step.fee_cover_ratio:.1f}x" if step.fee_cover_ratio is not None else "n/a"
        parts.append(f"fees +Rs{step.delta_fee:,.0f}, benefit cover {cover} [{mark(step.t2_pass)}]")
    else:
        parts.append(f"fees Rs{step.delta_fee:,.0f} [{mark(step.t2_pass)}]")
    if step.t3_pass is not None:
        parts.append(f"low-spend delta {_signed_rupees(step.low_spend_delta_v)} [{mark(step.t3_pass)}]")
    return " * ".join(parts)
