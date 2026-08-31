"""Stage 3 -- MATCH (Part C SS C.2.1, SS C.2.6, SS C.4 Stage 3).

Binds a card's earning rules to Stage 1's normalised spend segments via
selector matching, and resolves conflicts among rules that match the same
segment: higher priority wins, then more specific selector (count of
non-null selector dimensions), then publication order (a rule's position
in the card's earning_rules list) -- with a warning flag when priority and
specificity are both tied, since that means the rule set itself is
ambiguous (C.11's publish-time linting is the other half of this check;
Stage 3 flags it defensively at evaluation time too).

`stacks_with_base` rules (e.g. "5X = base 1X + bonus 4X") do not enter that
replace-or-lose contest at all: whichever non-stacking rule would otherwise
win for a segment is bound as usual, and every matching stacking rule binds
*in addition* to it -- never instead of it.

`requires_activation` rules (accelerated-rate unlocks, C.3's `activate_rule`
payload type) never match unless the caller explicitly names them active
via `active_rule_keys` -- the default (empty) excludes them, so passing a
card's full rule list (activation-gated rules included) to `match`/
`match_segment` for an ordinary Stage 3 pass is always safe; a dormant
rule can't accidentally win a segment. Stage 6-7 (thresholds.py) is the
only caller that ever passes a non-empty `active_rule_keys`, once a
threshold crossing says a specific rule is active for specific months.

Selector matching covers category, channel, merchant_group, and geography
(domestic/international/all -- syn_travel's "intl" rule), plus txn_min/
txn_max (Phase 5 Task A, docs/DECISIONS.md #130): accepted but IGNORED for
matching purposes -- category mode has no per-transaction data to test
them against -- so a rule using them binds on its OTHER fields as if the
txn bound weren't there, flagged `txn_threshold_unenforced` on its
bindings instead of silently approximating. Deliberately NOT the same
resolution as eligibility.py's ExclusionSelector, which treats an
unverifiable txn bound as "never matches": here, ignoring the field means
a rule *over-applies* on the txn dimension (extra reward credit on
transactions outside its true band) -- bounded by whatever cap the rule
already carries in practice, not the "zero every reward" blast radius an
over-matching EXCLUSION has. Making an earning-rule selector "never match"
whenever it names a txn bound would make the field useless to ever set
(no rule could use it for anything), so the two modules genuinely need
different defaults, not a shared one. mcc_include/mcc_exclude support
landed on eligibility.py's ExclusionSelector this same task but was
deliberately NOT extended here yet -- no earning rule in any bundle needs
it, and threading the category->MCC registry through this module's other
callers (caps.py/thresholds.py/costs.py, several different selector
contexts) is a separate, larger increment than "cheap in the same pass"
covers. Other C.2.1 fields (mcc, networks, date range) are still rejected
rather than silently ignored.

`selector_matches` is the canonical implementation -- caps.py, thresholds.py,
and costs.py all import it directly rather than keeping their own copies
(they all operate on this module's `Selector` type). eligibility.py is the
one exception: it has its own `ExclusionSelector` type from before this
consolidation, so it keeps a parallel implementation.

`tier_mode="incremental"` rules (syn_slab's fill-order bands, A.3's convex-
PWL case) are excluded from ordinary matching UNCONDITIONALLY -- there is
no `active_rule_keys`-style opt-in for them, because winner-takes-all
resolution is the wrong mechanic for a rule whose whole point is that
several same-selector rules each own a *slice* of the same spend pool
rather than competing for all of it. caps.py's `apply_incremental_bands`
is the only thing that ever binds them, operating on the pooled spend
directly rather than through match_segment.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from engine.normalise import NormalisedSpend, SpendSegment

ALL_SELECTOR_FIELDS = (
    "categories", "channels", "merchant_groups", "merchants", "mcc_include",
    "mcc_exclude", "networks", "geography", "txn_min", "txn_max", "date_from", "date_to",
)
SUPPORTED_SELECTOR_FIELDS = frozenset({"categories", "channels", "merchant_groups", "geography", "txn_min", "txn_max"})
# Public (was module-private) -- costs.py's surcharge validator needs the
# same list, and duplicating it risked the two ever drifting apart.
UNSUPPORTED_SELECTOR_FIELDS = tuple(f for f in ALL_SELECTOR_FIELDS if f not in SUPPORTED_SELECTOR_FIELDS)


@dataclass(frozen=True)
class Selector:
    categories: tuple[str, ...] | None = None
    channels: tuple[str, ...] | None = None
    merchant_groups: tuple[str, ...] | None = None
    merchants: tuple[str, ...] | None = None
    mcc_include: tuple[int, ...] | None = None
    mcc_exclude: tuple[int, ...] | None = None
    networks: tuple[str, ...] | None = None
    geography: str | None = None
    txn_min: Decimal | None = None
    txn_max: Decimal | None = None
    date_from: str | None = None
    date_to: str | None = None


@dataclass(frozen=True)
class EarningRule:
    """The slice of C.2.6's EarningRule that Stage 3 needs. `accrual` and
    `caps` are Stage 4/5's concern -- callers re-look-up the full rule by
    `key` from the card's rule set once a binding names it.

    `rule_group` doesn't affect matching at all -- Stage 5 (caps.py) reads
    it to resolve "rule_group:<key>"-scoped caps."""

    key: str
    selector: Selector
    priority: int = 10
    stacks_with_base: bool = False
    rule_group: str | None = None
    requires_activation: bool = False
    tier_mode: str | None = None  # "incremental" -> excluded from ordinary matching; see apply_incremental_bands


@dataclass(frozen=True)
class RuleBinding:
    rule_key: str
    segment: SpendSegment
    stacked: bool  # True: bound via stacks_with_base, alongside the base winner
    flags: tuple[str, ...] = ()


def _specificity(selector: Selector) -> int:
    return sum(1 for field in ALL_SELECTOR_FIELDS if getattr(selector, field) is not None)


def validate_rule(rule: EarningRule) -> None:
    """Public (was module-private) -- `compute/ingest`'s lint tool calls
    this per-rule directly so it can collect every issue in one bundle
    instead of stopping at whichever rule `match()`'s own loop hits
    first. `match()` still calls it internally, unchanged."""
    used_unsupported = [
        field for field in UNSUPPORTED_SELECTOR_FIELDS if getattr(rule.selector, field) is not None
    ]
    if used_unsupported:
        raise ValueError(
            f"earning rule {rule.key!r} selector uses field(s) {used_unsupported} that "
            "category-mode spend segments cannot be matched against"
        )


def selector_matches(selector: Selector, segment: SpendSegment) -> bool:
    if selector.categories is not None and segment.category not in selector.categories:
        return False
    if selector.channels is not None and segment.channel not in selector.channels:
        return False
    if selector.merchant_groups is not None and segment.merchant_group not in selector.merchant_groups:
        return False
    if selector.geography is not None and selector.geography != "all" and segment.geography != selector.geography:
        return False
    # txn_min/txn_max: transaction-level, no data to test in category mode --
    # deliberately not evaluated here (see match_segment's binding flags).
    return True


def _selector_flags(selector: Selector) -> tuple[str, ...]:
    if selector.txn_min is not None or selector.txn_max is not None:
        return ("txn_threshold_unenforced",)
    return ()


def _resolve_conflict(candidates: list[EarningRule]) -> tuple[EarningRule, bool]:
    """`candidates` must be non-empty and in publication order (their
    position in the card's earning_rules list). Returns (winner, tie_warning)."""
    best_priority = max(r.priority for r in candidates)
    top_priority = [r for r in candidates if r.priority == best_priority]
    if len(top_priority) == 1:
        return top_priority[0], False

    best_specificity = max(_specificity(r.selector) for r in top_priority)
    top_specificity = [r for r in top_priority if _specificity(r.selector) == best_specificity]
    if len(top_specificity) == 1:
        return top_specificity[0], False

    # Tied on both priority and specificity: earliest-published rule wins,
    # but this means the rule set itself is ambiguous -- flag it.
    return top_specificity[0], True


def match_segment(
    segment: SpendSegment,
    earning_rules: Sequence[EarningRule],
    active_rule_keys: frozenset[str] = frozenset(),
) -> tuple[RuleBinding, ...]:
    for rule in earning_rules:
        validate_rule(rule)

    eligible = [
        r for r in earning_rules
        if r.tier_mode != "incremental" and (not r.requires_activation or r.key in active_rule_keys)
    ]
    matching = [r for r in eligible if selector_matches(r.selector, segment)]
    non_stacking = [r for r in matching if not r.stacks_with_base]
    stacking = [r for r in matching if r.stacks_with_base]

    bindings: list[RuleBinding] = []
    if non_stacking:
        winner, tie_warning = _resolve_conflict(non_stacking)
        flags = _selector_flags(winner.selector)
        if tie_warning:
            flags = flags + ("priority_specificity_tie",)
        bindings.append(RuleBinding(rule_key=winner.key, segment=segment, stacked=False, flags=flags))
    for rule in stacking:
        bindings.append(RuleBinding(rule_key=rule.key, segment=segment, stacked=True, flags=_selector_flags(rule.selector)))
    return tuple(bindings)


def match(
    normalised: NormalisedSpend,
    earning_rules: Sequence[EarningRule],
    active_rule_keys: frozenset[str] = frozenset(),
) -> tuple[RuleBinding, ...]:
    bindings: list[RuleBinding] = []
    for segment in normalised.segments:
        bindings.extend(match_segment(segment, earning_rules, active_rule_keys))
    return tuple(bindings)
