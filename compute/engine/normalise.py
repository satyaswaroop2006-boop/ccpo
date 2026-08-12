"""Stage 1 — NORMALISE (Part C §C.4 Stage 1, §C.4.1).

Expands user-declared category spend into the (category x channel x month)
grid, applies seasonality, decomposes an aggregate UPI figure across
categories, and attaches each segment's ticket-size assumption (needed by
Stage 4's category-mode rounding maths, Part A §A.2).

Money is Decimal throughout and every allocation reconciles exactly to its
input total (paisa-level residual pushed onto one slot) -- no rupee is ever
created or lost by rounding (CLAUDE.md determinism rule).

Geography (C.2.1's `domestic | international | all` selector dimension) is
a definite fact about a segment, unlike channel -- every real transaction
genuinely happened one way or the other, there's no "unspecified" state to
carry forward. `CategorySpend.geography` defaults to "domestic" (the
overwhelming majority of a typical user's spend) and is threaded straight
onto each `SpendSegment` it produces; UPI-decomposed segments are always
domestic (UPI has no international rails). Matching against a rule's
`geography` selector is Stage 3/2/6-7/5's concern (`match.py::
selector_matches`), not this stage's.

`merchant_group`, unlike geography, genuinely can be unspecified (`None`)
-- most user-declared spend has no merchant-group breakdown at all, the
same "resolved later or never" status as `channel`. `CategorySpend.
merchant_group` threads straight onto each `SpendSegment` it produces
(UPI-decomposed segments never carry one); see docs/DECISIONS.md #5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANT = Decimal("0.01")
MONTHS: tuple[int, ...] = tuple(range(1, 13))
VALID_GEOGRAPHY = frozenset({"domestic", "international"})

# C.7 default average tickets, Rs (registry defaults -- overridable via the
# assumptions snapshot). Transcribed verbatim from the Part C table.
DEFAULT_TICKET_SIZES: dict[str, Decimal] = {
    "grocery": Decimal("700"),
    "quick_commerce": Decimal("700"),
    "dining": Decimal("600"),
    "food_delivery": Decimal("600"),
    "ecommerce": Decimal("1800"),
    "offline_retail": Decimal("1500"),
    "utilities": Decimal("1200"),
    "entertainment": Decimal("800"),
    "domestic_flights": Decimal("6500"),
    "international_flights": Decimal("35000"),
    "hotels_domestic": Decimal("9000"),
    "fuel": Decimal("1500"),
    "insurance": Decimal("20000"),
    "rent": Decimal("30000"),
    "education": Decimal("40000"),
}

# NOT specified in Part C -- a new assumption-registry default introduced
# here to operationalise C.4.1 ("grocery-heavy; editable"). Needs Satya's
# sign-off per CLAUDE.md working style. See docs/DECISIONS.md.
DEFAULT_UPI_CATEGORY_MIX: dict[str, Decimal] = {
    "grocery": Decimal("0.38"),
    "ecommerce": Decimal("0.15"),
    "dining": Decimal("0.12"),
    "utilities": Decimal("0.10"),
    "offline_retail": Decimal("0.10"),
    "fuel": Decimal("0.08"),
    "entertainment": Decimal("0.07"),
}


@dataclass(frozen=True)
class AssumptionsSnapshot:
    """The slice of the C.7 registry Stage 1 reads. Stage 0 freezes the full
    registry for the run; this is this stage's projection of it."""

    ticket_sizes: dict[str, Decimal] = field(default_factory=lambda: dict(DEFAULT_TICKET_SIZES))
    upi_category_mix: dict[str, Decimal] = field(default_factory=lambda: dict(DEFAULT_UPI_CATEGORY_MIX))


@dataclass(frozen=True)
class CategorySpend:
    """One line of user-declared annual spend (category mode, Part A §A.0 D(k))."""

    category: str
    annual_amount: Decimal
    channel: str | None = None  # None = unspecified/general; resolved at Stage 3 (match)
    seasonality: tuple[Decimal, ...] | None = None  # 12 weights summing to 1; None = uniform
    geography: str = "domestic"  # "domestic" | "international"
    merchant_group: str | None = None  # None = unspecified; threaded straight onto each SpendSegment


@dataclass(frozen=True)
class UpiAggregateSpend:
    """C.4.1: user enters one aggregate "Monthly UPI spend" figure with no
    category breakdown. Stage 1 decomposes it via upi_category_mix."""

    monthly_amount: Decimal


@dataclass(frozen=True)
class SpendInput:
    category_spend: tuple[CategorySpend, ...] = ()
    upi_aggregate: UpiAggregateSpend | None = None


@dataclass(frozen=True)
class SpendSegment:
    category: str
    channel: str | None
    month: int  # 1..12
    amount: Decimal  # Rs, exact
    ticket_size: Decimal  # Rs, from the assumptions snapshot
    merchant_group: str | None = None  # set when the user can distinguish spend by merchant group
    geography: str = "domestic"  # "domestic" | "international"
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalisedSpend:
    segments: tuple[SpendSegment, ...]


def _uniform_monthly_split(total: Decimal) -> list[Decimal]:
    """Split `total` into 12 equal paisa-exact monthly amounts. Pure integer
    arithmetic -- no 1/12 Decimal division, so no dependence on decimal
    context precision. Extra paise (total mod 12) land on the first months."""
    total_paise = int((total * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    base, remainder = divmod(total_paise, 12)
    parts_paise = [base] * 12
    for i in range(remainder):
        parts_paise[i] += 1
    return [Decimal(p) / Decimal(100) for p in parts_paise]


def _allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` across `weights` with paisa-exact reconciliation: each
    share rounds to the nearest paisa, then the residual (total - sum of
    rounded shares) is pushed onto the last nonzero-weight slot so the parts
    sum to `total` exactly."""
    total = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    shares = [(total * w).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP) for w in weights]
    residual = total - sum(shares)
    if residual != 0:
        idx = max((i for i, w in enumerate(weights) if w > 0), default=len(weights) - 1)
        shares[idx] += residual
    return shares


def _validate_seasonality(weights: tuple[Decimal, ...], category: str) -> None:
    if len(weights) != 12:
        raise ValueError(f"seasonality for {category!r} must have 12 entries, got {len(weights)}")
    if any(w < 0 for w in weights):
        raise ValueError(f"seasonality for {category!r} has a negative weight")
    total = sum(weights)
    if abs(total - Decimal(1)) > Decimal("0.0001"):
        raise ValueError(f"seasonality for {category!r} must sum to 1, got {total}")


def _validate_upi_mix(mix: dict[str, Decimal]) -> None:
    total = sum(mix.values())
    if abs(total - Decimal(1)) > Decimal("0.0001"):
        raise ValueError(f"upi_category_mix must sum to 1, got {total}")


def _ticket_size(category: str, assumptions: AssumptionsSnapshot) -> Decimal:
    try:
        return assumptions.ticket_sizes[category]
    except KeyError:
        raise ValueError(
            f"no ticket-size assumption for category {category!r}; add it to the assumptions snapshot"
        ) from None


def normalise(spend_input: SpendInput, assumptions: AssumptionsSnapshot) -> NormalisedSpend:
    segments: list[SpendSegment] = []

    for line in spend_input.category_spend:
        if line.geography not in VALID_GEOGRAPHY:
            raise ValueError(f"category {line.category!r}: unknown geography {line.geography!r}; valid values are {sorted(VALID_GEOGRAPHY)}")
        ticket_size = _ticket_size(line.category, assumptions)
        if line.seasonality is None:
            monthly_amounts = _uniform_monthly_split(line.annual_amount)
        else:
            _validate_seasonality(line.seasonality, line.category)
            monthly_amounts = _allocate(line.annual_amount, list(line.seasonality))
        for month, amount in zip(MONTHS, monthly_amounts):
            segments.append(
                SpendSegment(
                    category=line.category,
                    channel=line.channel,
                    month=month,
                    amount=amount,
                    ticket_size=ticket_size,
                    geography=line.geography,
                    merchant_group=line.merchant_group,
                )
            )

    if spend_input.upi_aggregate is not None:
        mix = assumptions.upi_category_mix
        _validate_upi_mix(mix)
        categories = list(mix.keys())
        weights = [mix[c] for c in categories]
        category_shares = _allocate(spend_input.upi_aggregate.monthly_amount, weights)
        for category, share in zip(categories, category_shares):
            ticket_size = _ticket_size(category, assumptions)
            for month in MONTHS:
                segments.append(
                    SpendSegment(
                        category=category,
                        channel="upi",
                        month=month,
                        amount=share,
                        ticket_size=ticket_size,
                        flags=("decomposition_assumed",),
                    )
                )

    return NormalisedSpend(segments=tuple(segments))
