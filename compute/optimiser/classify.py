"""ICV and KEEP/OPTIONAL/CLOSE/HOLD/ADD/DOWNGRADE classification (Part E
SS E.8).

For a chosen portfolio P (a set of card keys) and, optionally, a list of
not-yet-owned candidate cards:

    ICV(c|P)   = pv_exact(P) - pv_exact(P \\ {c})      -- owned cards
    ICV(c+|P)  = pv_exact(P u {c+}) - pv_exact(P)       -- ADD candidates
    Overlap(c|P) = pv_exact({c}) - ICV(c|P)             -- owned cards

Every `pv_exact(...)` above is a lookup, not a recomputation: full-sweep
enumeration (`optimiser/enumerate.py`) already solved every subset SS E.8
needs, *provided* the caller ran an `up_to`/`optimiser_decides` sweep big
enough to cover P and its one-card variants. SS E.8 itself allows for the
lookup to miss ("if enumerated; else one extra solve") -- `_pv_of` falls
back to a fresh `allocate()` + `repair()` call on exactly the missing
subset when it isn't already in `results`, so this module never silently
guesses. This is the only place classify.py computes a rupee value, and it
does so by calling the same two optimiser primitives every other module in
this package already uses (CLAUDE.md rule 1).

**Scope for this pass** (docs/DECISIONS.md, Phase 4 frontier/classify
entry):
- **Wallet mode doesn't exist yet** (docs/DECISIONS.md #10/#61), so there
  is no real "which cards does the user currently hold" input anywhere in
  the system. This module classifies whatever `portfolio_card_keys` the
  caller passes -- in practice, today, the frontier's own recommended
  subset (greenfield: "if I build this portfolio, which of its cards are
  pulling their weight") rather than a wallet's actual holdings. The
  KEEP/OPTIONAL/CLOSE/HOLD labels are exactly as meaningful either way
  (SS E.8's formulas don't care where P came from); only the product
  framing differs, and that's a Part F concern.
- **No eligibility filter on ADD candidates.** SS E.8's ADD rule is "ICV >
  icv_meaningful AND eligibility != unlikely" -- eligibility scoring
  (SS33) isn't modelled anywhere in the optimiser yet. Every candidate
  above the ICV bar is labelled ADD; below it, NOT_MATERIAL (a label SS
  E.8 doesn't name, since it only describes the ADD case for candidates --
  introduced here so a below-bar candidate is still reported with its ICV
  for explainability, not silently dropped).
- **DOWNGRADE needs `cards.family_key` (Part D SS D.3), which doesn't
  exist in the schema yet** -- grep confirms no card, seed, or migration
  defines one anywhere in this repo today. Implemented against SS E.8's
  formula exactly (`pv_exact(P \\ {c} u {c'}) > pv_exact(P)`) but gated on
  an optional caller-supplied `family_keys: dict[card_key, family_id]`
  map; with the default `None`, DOWNGRADE is simply never emitted. Same
  "spec-complete, no real fixture yet" posture as `WelcomeValue` (#29) and
  `flat_perk` (#23).
- **HOLD's strategic-feature flag is caller-supplied, not derived.**
  SS E.8 ties HOLD to "a user-flagged strategic feature (zero-forex / UPI
  / status / acceptance)" -- there is no user-constraint model to derive
  this from yet, so `strategic_feature_cards: frozenset[card_key]` is a
  plain input (the caller/UI already knows which feature flags the user
  set); this module only applies the ICV<=0-but-flagged rule, it doesn't
  invent the flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.card_bundle import CardRuleBundle
from engine.evaluate import EvaluateAssumptions
from engine.normalise import SpendInput
from engine.valuation import RewardCurrency
from optimiser.allocate import allocate
from optimiser.enumerate import SubsetResult
from optimiser.repair import repair

DEFAULT_ICV_MEANINGFUL = Decimal("1000")

KEEP = "KEEP"
OPTIONAL = "OPTIONAL"
CLOSE = "CLOSE"
HOLD = "HOLD"
ADD = "ADD"
NOT_MATERIAL = "NOT_MATERIAL"
DOWNGRADE = "DOWNGRADE"


@dataclass(frozen=True)
class CardClassification:
    card_key: str
    label: str
    icv: Decimal
    overlap: Decimal | None  # only computed for owned cards; None for ADD/NOT_MATERIAL candidates
    note: str | None = None
    downgrade_to: str | None = None


@dataclass(frozen=True)
class ClassificationResult:
    portfolio_subset_key: str
    pv_exact: Decimal
    owned: tuple[CardClassification, ...]
    candidates: tuple[CardClassification, ...]


def _subset_key(card_keys) -> str:
    return "+".join(sorted(card_keys))


def _pv_of(
    card_keys: frozenset[str],
    results_by_key: dict[str, SubsetResult],
    bundles_by_key: dict[str, CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions,
) -> Decimal:
    if not card_keys:
        return Decimal("0")
    existing = results_by_key.get(_subset_key(card_keys))
    if existing is not None:
        return existing.pv_exact
    subset_bundles = [bundles_by_key[k] for k in sorted(card_keys)]
    allocation = allocate(subset_bundles, currencies, spend, assumptions)
    return repair(subset_bundles, currencies, allocation, assumptions).valuation.pv_exact


def classify_portfolio(
    results: Sequence[SubsetResult],
    bundles: Sequence[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    portfolio_card_keys: Sequence[str],
    assumptions: EvaluateAssumptions | None = None,
    candidate_card_keys: Sequence[str] = (),
    icv_meaningful: Decimal = DEFAULT_ICV_MEANINGFUL,
    strategic_feature_cards: frozenset[str] = frozenset(),
    family_keys: dict[str, str] | None = None,
) -> ClassificationResult:
    assumptions = assumptions or EvaluateAssumptions()
    family_keys = family_keys or {}
    bundles_by_key = {b.card_key: b for b in bundles}
    results_by_key = {r.subset_key: r for r in results}

    portfolio = frozenset(portfolio_card_keys)

    def pv(card_keys: frozenset[str]) -> Decimal:
        return _pv_of(card_keys, results_by_key, bundles_by_key, currencies, spend, assumptions)

    pv_p = pv(portfolio)

    owned: list[CardClassification] = []
    for c in sorted(portfolio):
        rest = portfolio - {c}
        icv = pv_p - pv(rest)
        overlap = pv(frozenset({c})) - icv

        downgrade_to: str | None = None
        family = family_keys.get(c)
        if family is not None:
            for sibling, sibling_family in sorted(family_keys.items()):
                if sibling_family != family or sibling == c or sibling in portfolio:
                    continue
                swapped_pv = pv((portfolio - {c}) | {sibling})
                if swapped_pv > pv_p:
                    downgrade_to = sibling
                    break

        if downgrade_to is not None:
            label = DOWNGRADE
            note = f"replacing with family sibling {downgrade_to!r} would net more value"
        elif icv > icv_meaningful:
            label, note = KEEP, None
        elif icv > 0:
            label, note = OPTIONAL, None
        elif c in strategic_feature_cards:
            label = HOLD
            note = f"keeping this costs Rs{-icv:,.2f}/yr, held only for a flagged strategic feature"
        else:
            label, note = CLOSE, None

        owned.append(CardClassification(card_key=c, label=label, icv=icv, overlap=overlap, note=note, downgrade_to=downgrade_to))

    candidates: list[CardClassification] = []
    for c in candidate_card_keys:
        if c in portfolio:
            continue
        icv_plus = pv(portfolio | {c}) - pv_p
        label = ADD if icv_plus > icv_meaningful else NOT_MATERIAL
        candidates.append(CardClassification(card_key=c, label=label, icv=icv_plus, overlap=None))

    return ClassificationResult(
        portfolio_subset_key=_subset_key(portfolio), pv_exact=pv_p,
        owned=tuple(owned), candidates=tuple(candidates),
    )
