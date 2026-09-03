"""Reward-catalog point-to-rupee ratio estimator.

NOT one of Part I SS I.4's six pipeline stages (CAPTURE/DRAFT/LINT/LINK/
REVIEW/PUBLISH) -- this is a DRAFT-time helper, used only when a card's own
T&C never states a fixed rupee-per-point ratio anywhere (SBI Card PRIME's own
situation: reward_terms, the MITC, the Shop-and-Smile T&C page, and every
official FAQ all defer to "SBICPSL reserves the right to decide the Reward
Points required... for each segment of credit cards" -- there is no single
published number to cite, by SBI's own admission, not because nobody looked
hard enough).

**What this computes, precisely**: `sbicard.com/en/personal/rewards.page`
embeds its entire live catalog as one JSON blob (`window.rewards = {...}`) --
every product row carries `card` (a comma-joined list of the card keys
eligible to redeem it at the stated points cost), an `itemName` that, for
gift-card/voucher products, states its face value in the name itself (e.g.
"Titan e Voucher INR 1000"), and `item[0].point` (the pure-points cost).
`segment_stats` filters to items eligible for ONE named card key, parses the
face value out of each matching item's name, computes rupee-per-point for
each, and reports the median/mean/min/max per 100 points across that card's
own slice of the catalog.

**Why median/mean of a DISTRIBUTION, not one fixed number**: per-item pricing
genuinely varies within a single card's own catalog slice (SBI Card PRIME's
101 priced items range from Rs.3.03 to Rs.50.00 per 100 points) -- there is
no single "true" ratio to recover, only a typical one. This tool's output is
therefore an ASSUMPTION-REGISTRY default (same status as `engine.normalise.
DEFAULT_TICKET_SIZES`), not a citable T&C fact -- it needs Satya's sign-off
before use in any bundle, and should be labelled as such wherever it's
written into one (see `bundle_sbi_prime.json`'s own `voucher_catalog` route
for the pattern).

**This MUST be re-run, not reused, whenever a card's terms change**: SBI can
(and per the catalog's own `specialOffer`/date-stamped cadence, does) revise
catalog pricing independently of any T&C document. `refresh_reference` always
re-fetches the LIVE page -- there is no caching, no staleness check, and no
"has anything changed" detection here on purpose: every call is a fresh
snapshot, dated by `captured_at`. A reference file this tool writes is a
point-in-time record, not a durable fact -- see docs/DECISIONS.md's PRIME
entry for the standing instruction to re-run this before trusting an old
snapshot for a new ingestion or a republish.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import statistics
from dataclasses import dataclass
from typing import Callable, Sequence

import httpx

CATALOG_URL = "https://www.sbicard.com/en/personal/rewards.page"
_JS_VAR_MARKER = "window.rewards = "
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_VALUE_IN_NAME_RE = re.compile(r"(?:INR|Rs)\.?\s*([\d,]+)", re.IGNORECASE)


class CatalogFetchError(Exception):
    """Fetch succeeded but the expected `window.rewards = {...}` JS variable
    assignment wasn't found, or it didn't parse as JSON -- the page's own
    structure changed. Refuses loudly rather than silently returning an
    empty/wrong catalog."""


Fetcher = Callable[[], str]


def fetch_catalog_html(client: httpx.Client | None = None) -> str:
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": _DEFAULT_UA})
    try:
        resp = client.get(CATALOG_URL)
        resp.raise_for_status()
        return resp.text
    finally:
        if owns_client:
            client.close()


def extract_catalog_json(html: str) -> dict:
    start = html.find(_JS_VAR_MARKER)
    if start == -1:
        raise CatalogFetchError(f"'{_JS_VAR_MARKER}' not found in the fetched page -- catalog page structure may have changed")
    start += len(_JS_VAR_MARKER)

    end_candidates = [i for i in (html.find(";\n", start), html.find(";\r\n", start), html.find("</SCRIPT", start, start + 3_000_000)) if i != -1]
    if not end_candidates:
        raise CatalogFetchError("could not find the end of the window.rewards assignment")
    end = min(end_candidates)

    blob = html[start:end].rstrip().rstrip(";")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as e:
        raise CatalogFetchError(f"window.rewards blob didn't parse as JSON: {e}") from e
    if "reward" not in data:
        raise CatalogFetchError("parsed JSON has no top-level 'reward' key -- catalog schema may have changed")
    return data


@dataclass(frozen=True)
class CatalogItem:
    name: str
    brand: str
    card_keys: tuple[str, ...]
    points: float | None
    cash_point: float | None
    cash_amount: float | None
    rupee_value: float | None  # parsed from the item's own name; None if not a fixed-value gift card/voucher

    @property
    def pure_ratio_per_100_points(self) -> float | None:
        if self.rupee_value is None or not self.points:
            return None
        return (self.rupee_value / self.points) * 100


def _rupee_value_from_name(name: str) -> float | None:
    m = _VALUE_IN_NAME_RE.search(name)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _as_float(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value != 0 else None


def parse_catalog_items(data: dict) -> tuple[CatalogItem, ...]:
    items = []
    for row in data["reward"]:
        card_field = row.get("card", "")
        card_keys = tuple(k for k in card_field.split(",") if k)
        item_rows = row.get("item") or [{}]
        first = item_rows[0]
        name = row.get("itemName", "")
        items.append(CatalogItem(
            name=name,
            brand=row.get("brand", ""),
            card_keys=card_keys,
            points=_as_float(first.get("point")),
            cash_point=_as_float(first.get("cashPoint")),
            cash_amount=_as_float(first.get("cashAmount")),
            rupee_value=_rupee_value_from_name(name),
        ))
    return tuple(items)


@dataclass(frozen=True)
class SegmentStats:
    card_key: str
    n: int
    median_per_100_points: float
    mean_per_100_points: float
    min_per_100_points: float
    max_per_100_points: float


def segment_stats(items: Sequence[CatalogItem], card_key: str) -> SegmentStats | None:
    """None if `card_key` doesn't appear as an eligible card on any
    fixed-value catalog item -- e.g. a typo, or a segment with no priced
    gift-card/voucher products in the current catalog snapshot."""
    ratios = [
        item.pure_ratio_per_100_points
        for item in items
        if card_key in item.card_keys and item.pure_ratio_per_100_points is not None
    ]
    if not ratios:
        return None
    return SegmentStats(
        card_key=card_key, n=len(ratios),
        median_per_100_points=statistics.median(ratios),
        mean_per_100_points=statistics.mean(ratios),
        min_per_100_points=min(ratios), max_per_100_points=max(ratios),
    )


def refresh_reference(
    card_keys: Sequence[str],
    fetcher: Fetcher = fetch_catalog_html,
    today: str | None = None,
) -> dict:
    """Always fetches LIVE -- see module docstring for why this never caches.
    Returns a JSON-serialisable dict; callers decide whether/where to persist
    it (the CLI writes it to `ingestion/reference_reward_point_values.json`)."""
    html = fetcher()
    data = extract_catalog_json(html)
    items = parse_catalog_items(data)

    segments = {}
    for card_key in card_keys:
        stats = segment_stats(items, card_key)
        segments[card_key] = (
            {
                "n": stats.n,
                "median_per_100_points": round(stats.median_per_100_points, 4),
                "mean_per_100_points": round(stats.mean_per_100_points, 4),
                "min_per_100_points": round(stats.min_per_100_points, 4),
                "max_per_100_points": round(stats.max_per_100_points, 4),
            }
            if stats is not None else "not_found -- card_key not eligible on any fixed-value catalog item in this snapshot"
        )

    return {
        "captured_at": today or _dt.date.today().isoformat(),
        "source_url": CATALOG_URL,
        "total_catalog_items_parsed": len(items),
        "methodology": (
            "For each card_key, filters catalog items whose 'card' eligibility field includes it AND whose "
            "itemName states an explicit INR/Rs face value (gift cards/vouchers only, not physical products "
            "without a stated value). Computes (face_value / points) * 100 per item, then median/mean/min/max "
            "across that card's own slice. This is a DISTRIBUTION summary, not one fixed fact -- per-item "
            "pricing genuinely varies within one card's own catalog (SBI's own stated discretion). An "
            "assumption-registry default requiring sign-off before use, not a citable T&C fact."
        ),
        "refresh_policy": (
            "MUST be re-run (not reused from a prior snapshot) whenever: (a) the card's own reward T&C changes, "
            "(b) a new card is being ingested and needs its own segment's numbers, or (c) enough time has passed "
            "that catalog pricing may have drifted. There is no automatic staleness detection -- re-running this "
            "tool IS the refresh."
        ),
        "segments": segments,
    }
