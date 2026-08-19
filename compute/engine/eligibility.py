"""Stage 2 -- ELIGIBILITY (Part C SS C.2.5, SS C.4 Stage 2).

Applies a card's exclusions, each scoped independently to rewards /
milestones / fee_waiver (Part A Decision 4: three independent eligibility
masks eR/eM/eW, never inferred from one another), to Stage 1's normalised
spend grid, producing the three eligible-spend views the rest of the
pipeline (Stage 3 onward) reads from.

Selector matching implements C.2.1's semantics (match if every non-null
field matches; OR within a list) for the fields Stage 1's category-mode
grid actually carries: category, channel, merchant_group, and geography.
Any exclusion selector naming a field the grid doesn't carry (mcc,
networks, transaction amount, date) is rejected rather than silently
ignored -- category-mode eligibility has no data to evaluate those fields
against.

`ExclusionSelector` stays a separate type from match.py's `Selector`
(predates that module), so its matching logic is a parallel implementation
rather than a shared import -- match.py's `selector_matches` is what
caps.py/thresholds.py/costs.py all reuse instead of keeping their own
copies, since those three already operate on match.py's `Selector` type.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.normalise import NormalisedSpend, SpendSegment

VALID_EXCLUDED_FROM = frozenset({"rewards", "milestones", "fee_waiver"})
_SCOPE_TO_VIEW = {"rewards": "reward", "milestones": "milestone", "fee_waiver": "waiver"}

# Selector fields the category-mode grid cannot evaluate -- present in
# C.2.1 but not yet meaningful at Stage 2.
_UNSUPPORTED_SELECTOR_FIELDS = (
    "merchants", "mcc_include", "mcc_exclude",
    "networks", "txn_min", "txn_max", "date_from", "date_to",
)


@dataclass(frozen=True)
class ExclusionSelector:
    categories: tuple[str, ...] | None = None
    channels: tuple[str, ...] | None = None
    merchants: tuple[str, ...] | None = None
    merchant_groups: tuple[str, ...] | None = None
    mcc_include: tuple[int, ...] | None = None
    mcc_exclude: tuple[int, ...] | None = None
    networks: tuple[str, ...] | None = None
    geography: str | None = None
    txn_min: Decimal | None = None
    txn_max: Decimal | None = None
    date_from: str | None = None
    date_to: str | None = None


@dataclass(frozen=True)
class Exclusion:
    key: str
    selector: ExclusionSelector
    excluded_from: tuple[str, ...]  # subset of VALID_EXCLUDED_FROM
    note: str | None = None


@dataclass(frozen=True)
class EligibleSpend:
    """The three views of Part A SS A.1: RewardSpend / MileSpend / WaiverSpendYr,
    materialised as segment lists rather than summed (summing is Stage 4+'s job)."""

    reward: tuple[SpendSegment, ...]
    milestone: tuple[SpendSegment, ...]
    waiver: tuple[SpendSegment, ...]


def validate_exclusion(exclusion: Exclusion) -> None:
    """Public (was module-private) -- same reason as `match.validate_
    rule`: `compute/ingest`'s lint tool calls this per-exclusion so it
    can collect every issue instead of stopping at whichever exclusion
    `apply_eligibility`'s own loop hits first. `apply_eligibility` still
    calls it internally, unchanged."""
    unknown = set(exclusion.excluded_from) - VALID_EXCLUDED_FROM
    if unknown:
        raise ValueError(
            f"exclusion {exclusion.key!r} has unknown excluded_from scope(s) {sorted(unknown)}; "
            f"valid scopes are {sorted(VALID_EXCLUDED_FROM)}"
        )
    used_unsupported = [
        field for field in _UNSUPPORTED_SELECTOR_FIELDS
        if getattr(exclusion.selector, field) is not None
    ]
    if used_unsupported:
        raise ValueError(
            f"exclusion {exclusion.key!r} selector uses field(s) {used_unsupported} that "
            "category-mode spend segments (category/channel/month only) cannot be matched against"
        )


def _selector_matches(selector: ExclusionSelector, segment: SpendSegment) -> bool:
    if selector.categories is not None and segment.category not in selector.categories:
        return False
    if selector.channels is not None and segment.channel not in selector.channels:
        return False
    if selector.merchant_groups is not None and segment.merchant_group not in selector.merchant_groups:
        return False
    if selector.geography is not None and selector.geography != "all" and segment.geography != selector.geography:
        return False
    return True


def apply_eligibility(normalised: NormalisedSpend, exclusions: Sequence[Exclusion]) -> EligibleSpend:
    for exclusion in exclusions:
        validate_exclusion(exclusion)

    reward: list[SpendSegment] = []
    milestone: list[SpendSegment] = []
    waiver: list[SpendSegment] = []

    for segment in normalised.segments:
        excluded_views: set[str] = set()
        for exclusion in exclusions:
            if _selector_matches(exclusion.selector, segment):
                excluded_views.update(_SCOPE_TO_VIEW[scope] for scope in exclusion.excluded_from)
        if "reward" not in excluded_views:
            reward.append(segment)
        if "milestone" not in excluded_views:
            milestone.append(segment)
        if "waiver" not in excluded_views:
            waiver.append(segment)

    return EligibleSpend(reward=tuple(reward), milestone=tuple(milestone), waiver=tuple(waiver))
